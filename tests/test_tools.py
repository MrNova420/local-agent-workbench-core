import unittest,tempfile,threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path
from desktop_engine import Workspace,ToolRegistry,RequestCancelled
class ToolTests(unittest.TestCase):
 def test_advertised_tools_include_git_diff(self):
  with tempfile.TemporaryDirectory() as t:
   tools=ToolRegistry(Workspace(t),lambda *_:True)
   names={schema['function']['name'] for schema in tools.schemas}
   self.assertIn('git_diff',names)
   git_log=next(schema for schema in tools.schemas if schema['function']['name']=='git_log')['function']['parameters']['properties']
   self.assertNotIn('maximum',git_log['limit'])
   self.assertEqual(git_log['offset']['minimum'],0)
 def test_file_listing_pages_at_requested_page_size_without_a_hard_maximum(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for i in range(1201):(root/f'{i:04}.txt').write_text('x')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   result=tools.run('list_files',{'path':'.','pattern':'*.txt'})
   self.assertIn('[This page reached the requested 1200-file page size.]',result)
   self.assertIsNone(tools.search_file_limit)
   self.assertIsNone(tools.search_byte_limit)
 def test_large_file_listing_emits_live_scan_progress(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for i in range(300):(root/f'{i:04}.txt').write_text('x')
   events=[];tools=ToolRegistry(Workspace(root),lambda *_:True,event=events.append)
   with patch('desktop_engine.time.monotonic',side_effect=[0.0]+[i/256 for i in range(1,1000)]):
    result=tools.run('list_files',{'path':'.','pattern':'*.none','limit':500})
   progress=[event for event in events if event.get('type')=='tool_progress']
   self.assertTrue(progress)
   self.assertEqual(progress[-1]['progress']['filesScanned'],256)
   self.assertIn('Scanning files',progress[-1]['progress']['message'])
   self.assertIn('No project files found',result)
 def test_large_project_search_emits_live_progress_without_exposing_query_or_paths(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for i in range(300):(root/f'{i:04}.py').write_text('needle in source\n')
   events=[];tools=ToolRegistry(Workspace(root),lambda *_:True,event=events.append)
   with patch('desktop_engine.time.monotonic',side_effect=[0.0]+[i/256 for i in range(1,1000)]):
    result=tools.run('search_files',{'query':'needle','limit':500})
   progress=[event for event in events if event.get('type')=='tool_progress']
   self.assertTrue(progress)
   self.assertEqual(progress[-1]['progress']['filesScanned'],256)
   self.assertGreater(progress[-1]['progress']['bytesRead'],0)
   self.assertNotIn('needle',progress[-1]['progress']['message'])
   self.assertNotIn('0000.py',progress[-1]['progress']['message'])
   self.assertIn('needle in source',result)
 def test_file_listing_pages_through_matches_without_restarting_at_first_page(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for name in ('a.py','b.py','c.py','d.py'):(root/name).write_text(name)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   first=tools.run('list_files',{'path':'.','pattern':'*.py','offset':0,'limit':2})
   second=tools.run('list_files',{'path':'.','pattern':'*.py','offset':2,'limit':2})
   self.assertIn('a.py',first);self.assertIn('b.py',first);self.assertIn('next offset 2',first.casefold())
   self.assertIn('c.py',second);self.assertIn('d.py',second);self.assertNotIn('a.py',second)
 def test_sequential_file_listing_pages_reuse_the_live_traversal(self):
  import desktop_engine
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for name in ('a.py','b.py','c.py','d.py','e.py','f.py'):(root/name).write_text(name)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   traversed=[];real_walk=desktop_engine.iter_project_files
   def recording_walk(*args,**kwargs):
    for path in real_walk(*args,**kwargs):
     traversed.append(path.name);yield path
   with patch('desktop_engine.iter_project_files',side_effect=recording_walk):
    first=tools.run('list_files',{'path':'.','pattern':'*.py','offset':0,'limit':2})
    tools.run('read_file',{'path':'a.py'})
    second=tools.run('list_files',{'path':'.','pattern':'*.py','offset':2,'limit':2})
   self.assertIn('next offset 2',first.casefold())
   self.assertIn('c.py',second);self.assertIn('d.py',second)
   self.assertEqual(len(traversed),5)
   self.assertEqual(len(set(traversed)),5)
 def test_sequential_file_listing_cursor_restarts_when_directory_changes(self):
  import os,time
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for name in ('a.py','b.py','c.py','d.py','e.py'):(root/name).write_text(name)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   first=tools.run('list_files',{'path':'.','pattern':'*.py','offset':0,'limit':2})
   self.assertIn('next offset 2',first.casefold())
   (root/'z.py').write_text('new')
   now=time.time_ns()+2_000_000_000
   os.utime(root,ns=(now,now))
   second=tools.run('list_files',{'path':'.','pattern':'*.py','offset':2,'limit':2})
   third=tools.run('list_files',{'path':'.','pattern':'*.py','offset':4,'limit':2})
   self.assertIn('c.py',second);self.assertIn('d.py',second)
   self.assertIn('z.py',third)
 def test_sequential_file_listing_cursor_restarts_when_gitignore_changes(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'.gitignore').write_text('',encoding='utf-8')
   for name in ('a.py','b.py','c.py'):(root/name).write_text(name)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   first=tools.run('list_files',{'path':'.','pattern':'*.py','offset':0,'limit':1})
   self.assertIn('a.py',first)
   (root/'.gitignore').write_text('b.py\n',encoding='utf-8')
   second=tools.run('list_files',{'path':'.','pattern':'*.py','offset':1,'limit':1})
   self.assertIn('c.py',second);self.assertNotIn('b.py',second)
 def test_sequential_file_listing_cursor_restarts_when_nested_directory_changes(self):
  import os,time
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);nested=root/'src';nested.mkdir()
   for name in ('a.py','b.py','c.py','d.py'):(nested/name).write_text(name)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   first=tools.run('list_files',{'path':'.','pattern':'*.py','offset':0,'limit':2})
   self.assertIn('next offset 2',first.casefold())
   (nested/'z.py').write_text('new')
   now=time.time_ns()+2_000_000_000
   os.utime(nested,ns=(now,now))
   second=tools.run('list_files',{'path':'.','pattern':'*.py','offset':2,'limit':3})
   self.assertIn('c.py',second);self.assertIn('d.py',second);self.assertIn('z.py',second)
 def test_file_listing_cursor_is_discarded_on_cancellation(self):
  import threading,desktop_engine
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for name in ('a.py','b.py','c.py'):(root/name).write_text(name)
   cancelled=threading.Event();tools=ToolRegistry(Workspace(root),lambda *_:True,cancel_event=cancelled)
   real_walk=desktop_engine.iter_project_files
   def cancel_after_first(*args,**kwargs):
    for path in real_walk(*args,**kwargs):
     yield path
     if path.name=='a.py':cancelled.set()
   with patch('desktop_engine.iter_project_files',side_effect=cancel_after_first):
    partial=tools.run('list_files',{'path':'.','pattern':'*.py','offset':0,'limit':2})
   self.assertIn('cancelled',partial.casefold());self.assertIn('a.py',partial)
   cancelled.clear()
   recovered=tools.run('list_files',{'path':'.','pattern':'*.py','offset':1,'limit':1})
   self.assertIn('b.py',recovered)
 def test_file_listing_cursor_preserves_budget_lookahead_for_the_next_page(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for name in ('a.py','b.py','c.py'):(root/name).write_text(name)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   first=tools.run('list_files',{'path':'.','pattern':'*.py','offset':0,'limit':3},output_byte_budget=4)
   second=tools.run('list_files',{'path':'.','pattern':'*.py','offset':1,'limit':2},output_byte_budget=64)
   self.assertIn('a.py',first);self.assertIn('next offset 1',first.casefold())
   self.assertIn('b.py',second);self.assertIn('c.py',second)
 def test_file_listing_cursor_falls_back_for_a_backward_offset(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for name in ('a.py','b.py','c.py','d.py'):(root/name).write_text(name)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   later=tools.run('list_files',{'path':'.','pattern':'*.py','offset':2,'limit':1})
   earlier=tools.run('list_files',{'path':'.','pattern':'*.py','offset':0,'limit':1})
   self.assertIn('c.py',later);self.assertIn('a.py',earlier)
 def test_file_listing_past_the_last_page_does_not_claim_the_project_is_empty(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'a.py').write_text('a')
   result=ToolRegistry(Workspace(root),lambda *_:True).run('list_files',{'path':'.','pattern':'*.py','offset':2,'limit':2})
   self.assertIn('No more project files',result)
   self.assertNotIn('directory is empty',result)
 def test_file_listing_cursor_is_isolated_by_resolved_directory_and_pattern(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'src').mkdir();(root/'docs').mkdir()
   for directory in ('src','docs'):
    for name in ('a.py','b.py','c.py'):(root/directory/name).write_text(directory+name)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   first=tools.run('list_files',{'path':'src','pattern':'*.py','offset':0,'limit':1})
   other=tools.run('list_files',{'path':'docs','pattern':'*.py','offset':1,'limit':1})
   self.assertIn('a.py',first);self.assertIn('b.py',other);self.assertNotIn('src',other)
 def test_empty_directory_listing_returns_an_explicit_result(self):
  with tempfile.TemporaryDirectory() as t:
   result=ToolRegistry(Workspace(t),lambda *_:True).run('list_files',{'path':'.'})
   self.assertIn('no project files found',result.casefold())
 def test_search_pages_through_matches_with_next_offset(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'matches.py').write_text('needle one\nneedle two\nneedle three\nneedle four\n',encoding='utf-8')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   page=tools.run('search_files',{'query':'needle','offset':2,'limit':1})
   self.assertIn('matches.py:3: needle three',page)
   self.assertNotIn('matches.py:1:',page)
   self.assertIn('next offset 3',page.casefold())
 def test_sequential_search_pages_do_not_rescan_prior_files(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);first=root/'a.py';second=root/'b.py'
   first.write_text('needle first\n',encoding='utf-8')
   second.write_text('needle second\nneedle third\n',encoding='utf-8')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   opened=[];real_open=Path.open
   def record_open(path,*args,**kwargs):
    opened.append(path.resolve())
    return real_open(path,*args,**kwargs)
   with patch.object(Path,'open',record_open):
    page_one=tools.run('search_files',{'query':'needle','offset':0,'limit':1})
    first_page_opens=opened.count(first.resolve())
    page_two=tools.run('search_files',{'query':'needle','offset':1,'limit':1})
    page_three=tools.run('search_files',{'query':'needle','offset':2,'limit':1})
   self.assertIn('a.py:1: needle first',page_one)
   self.assertIn('b.py:1: needle second',page_two)
   self.assertIn('b.py:2: needle third',page_three)
   self.assertEqual(opened.count(first.resolve()),first_page_opens)
 def test_sequential_search_page_restarts_when_a_scanned_file_changes(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);first=root/'a.py';second=root/'b.py'
   first.write_text('needle one\n',encoding='utf-8')
   second.write_text('needle two\n',encoding='utf-8')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   page_one=tools.run('search_files',{'query':'needle','offset':0,'limit':1})
   first.write_text('needle changed\nneedle added\n',encoding='utf-8')
   page_two=tools.run('search_files',{'query':'needle','offset':1,'limit':1})
   self.assertIn('needle one',page_one)
   self.assertIn('a.py:2: needle added',page_two)
 def test_search_output_budget_retains_the_unreturned_match_for_the_next_page(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'a.py').write_text('needle one\nneedle two\n',encoding='utf-8')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   budget=len(f'{tools.workspace.display_path(root / "a.py")}:1: needle one'.encode('utf-8'))
   first=tools.run('search_files',{'query':'needle','offset':0,'limit':10},output_byte_budget=budget)
   second=tools.run('search_files',{'query':'needle','offset':1,'limit':10})
   self.assertIn('needle one',first)
   self.assertIn('next offset 1',first.casefold())
   self.assertIn('a.py:2: needle two',second)
 def test_search_skips_the_rest_of_a_file_after_invalid_utf8(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'a.py').write_bytes(b'needle before\n\xff\nneedle after\n')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   result=tools.run('search_files',{'query':'needle','limit':10})
   self.assertIn('needle before',result)
   self.assertNotIn('needle after',result)
 def test_search_files_advertises_regex_and_case_options(self):
  with tempfile.TemporaryDirectory() as t:
   tools=ToolRegistry(Workspace(t),lambda *_:True)
   schema=next(row['function'] for row in tools.schemas if row['function']['name']=='search_files')
   self.assertEqual(schema['parameters']['properties']['regex']['type'],'boolean')
   self.assertEqual(schema['parameters']['properties']['case_sensitive']['type'],'boolean')
   self.assertEqual(schema['parameters']['properties']['match_timeout_seconds']['type'],'number')
 def test_regex_search_is_case_insensitive_by_default_and_case_sensitive_on_request(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'names.py').write_text('Alpha\nalpha\nBeta\n',encoding='utf-8')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   insensitive=tools.run('search_files',{'query':'^alpha$','regex':True,'path':'.','limit':10})
   sensitive=tools.run('search_files',{'query':'^alpha$','regex':True,'case_sensitive':True,'path':'.','limit':10})
   self.assertIn('names.py:1: Alpha',insensitive)
   self.assertIn('names.py:2: alpha',insensitive)
   self.assertNotIn('names.py:1:',sensitive)
   self.assertIn('names.py:2: alpha',sensitive)
 def test_invalid_regex_returns_a_correctable_search_error(self):
  with tempfile.TemporaryDirectory() as t:
   result=ToolRegistry(Workspace(t),lambda *_:True).run('search_files',{'query':'[','regex':True})
   self.assertIn('Invalid regular expression',result)
 def test_pathological_regex_times_out_and_marks_any_returned_matches_incomplete(self):
  from desktop_engine import _tool_activity_status
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'stress.py').write_text(('a'*4000)+'!\n',encoding='utf-8')
   result=ToolRegistry(Workspace(root),lambda *_:True).run(
    'search_files',{'query':'(a+)+$','regex':True,'path':'.','match_timeout_seconds':0.001})
   self.assertIn('regular expression timed out',result.casefold())
   self.assertIn('results may be incomplete',result.casefold())
   self.assertEqual(_tool_activity_status('search_files',result),'partial')
 def test_glob_patterns_cover_nested_files_and_search_can_scope_by_directory(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'src/nested').mkdir(parents=True);(root/'docs').mkdir()
   (root/'src/nested/page.tsx').write_text('const target = true',encoding='utf8')
   (root/'docs/page.tsx').write_text('unrelated',encoding='utf8')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   listed=tools.run('list_files',{'pattern':'src/**/*.tsx'})
   found=tools.run('search_files',{'query':'target','path':'src','include':'**/*.tsx'})
   self.assertIn('src/nested/page.tsx',listed)
   self.assertIn('src/nested/page.tsx:1:',found)
   self.assertNotIn('docs/page.tsx',listed)
 def test_workspace_rejects_path_escape(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'project';root.mkdir();ws=Workspace(root)
   with self.assertRaises(ValueError):ws.resolve('../outside.txt')
 def test_workspace_allows_external_file_only_under_explicitly_granted_directory(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);project=base/'project';external=base/'shared';project.mkdir();external.mkdir()
   (external/'shared.py').write_text('shared',encoding='utf-8')
   workspace=Workspace(project,external_directories=[external])
   self.assertEqual(workspace.resolve(str(external/'shared.py')).read_text(encoding='utf-8'),'shared')
   with self.assertRaises(ValueError):workspace.resolve(str(base/'ungranted.txt'))
 def test_external_folder_supports_list_search_and_read_with_absolute_results(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);project=base/'project';external=base/'shared';project.mkdir();(external/'nested').mkdir(parents=True)
   target=external/'nested'/'shared.py';target.write_text('external needle\n',encoding='utf-8')
   rules=[{'tool':'external_directory','decision':'allow','path_glob':str(external).replace('\\','/')+'/**','workspace':str(project)}]
   tools=ToolRegistry(Workspace(project,external_directories=[external]),lambda *_:True,permission_rules=rules)
   listing=tools.run('list_files',{'path':str(external),'pattern':'*.py'})
   search=tools.run('search_files',{'path':str(external),'query':'needle','include':'*.py'})
   self.assertEqual(listing,str(target))
   self.assertIn(f'{target}:1: external needle',search)
   self.assertEqual(tools.run('read_file',{'path':str(target)}),'external needle\n')
 def test_external_folder_file_edits_follow_the_selected_access_mode(self):
  from desktop_engine import AgentEngine
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);project=base/'project';external=base/'shared';project.mkdir();external.mkdir()
   target=external/'notes.txt'
   rules=[{'tool':'external_directory','decision':'allow','path_glob':str(external).replace('\\','/')+'/**','workspace':str(project)}]
   engine=AgentEngine(project,access='edit',permission_rules=rules)
   registry=engine.state['registry']
   self.assertEqual(registry.run('write_file',{'path':str(target),'content':'start'}),f'Wrote {target}')
   self.assertEqual(registry.run('edit_file',{'path':str(target),'old_text':'start','new_text':'changed'}),f'Edited {target}')
   self.assertEqual(registry.run('append_file',{'path':str(target),'content':'!'}),f'Appended {target}')
   self.assertEqual(target.read_text(encoding='utf-8'),'changed!')
 def test_plan_mode_blocks_edits_even_after_approval(self):
  with tempfile.TemporaryDirectory() as t:
   tools=ToolRegistry(Workspace(t,'plan'),lambda *_:True)
   self.assertIn('read-only',tools.run('apply_patch',{'patch':'--- a/x\n+++ b/x\n@@\n+hi'}))
 def test_append_file_preserves_existing_file_identity_for_incremental_large_writes(self):
  import os
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);target=root/'generated.txt';target.write_text('first chunk\n',encoding='utf-8')
   original_identity=os.stat(target).st_ino
   tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True)
   self.assertEqual(tools.run('append_file',{'path':'generated.txt','content':'second chunk\n'}),'Appended generated.txt')
   self.assertEqual(os.stat(target).st_ino,original_identity,
                    'appending a chunk must extend the existing file instead of copying/replacing all prior content')
   self.assertEqual(target.read_text(encoding='utf-8'),'first chunk\nsecond chunk\n')
 def test_appends_to_independent_files_can_write_concurrently(self):
  import os
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);first=root/'first.txt';second=root/'second.txt'
   first.write_text('first prefix\n',encoding='utf-8');second.write_text('second prefix\n',encoding='utf-8')
   tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True)
   both_writing=threading.Barrier(2);real_write=os.write
   def wait_for_other_file(fd,data):
    both_writing.wait(timeout=0.5)
    return real_write(fd,data)
   with patch('desktop_engine.os.write',side_effect=wait_for_other_file):
    with ThreadPoolExecutor(max_workers=2) as executor:
     results=list(executor.map(lambda row:tools.run('append_file',{'path':row[0],'content':row[1]}),
                               [('first.txt',' first suffix'),('second.txt',' second suffix')]))
   self.assertEqual(results,['Appended first.txt','Appended second.txt'])
   self.assertEqual(first.read_text(encoding='utf-8'),'first prefix\n first suffix')
   self.assertEqual(second.read_text(encoding='utf-8'),'second prefix\n second suffix')
 def test_append_file_rolls_back_a_partially_written_chunk_on_io_failure(self):
  import os
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);target=root/'generated.txt';target.write_bytes(b'committed prefix\n')
   tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True)
   real_write=os.write;calls=0
   def partial_then_fail(fd,data):
    nonlocal calls
    calls+=1
    if calls==1:return real_write(fd,data[:5])
    raise OSError('simulated disk write failure')
   with patch('desktop_engine.os.write',side_effect=partial_then_fail):
    with self.assertRaises(OSError):tools.run('append_file',{'path':'generated.txt','content':'chunk that must roll back'})
   self.assertEqual(target.read_bytes(),b'committed prefix\n')
 def test_read_file_is_project_relative(self):
  with tempfile.TemporaryDirectory() as t:
   (Path(t)/'a.py').write_text('ok');tools=ToolRegistry(Workspace(t),lambda *_:True)
   self.assertEqual(tools.run('read_file',{'path':'a.py'}),'ok')

 def test_git_inspection_refuses_parent_repository_outside_selected_root(self):
  import subprocess
  from pathlib import Path
  import tempfile
  with tempfile.TemporaryDirectory() as directory:
   parent=Path(directory);subprocess.run(['git','init','-q',str(parent)],check=True)
   child=parent/'child';child.mkdir()
   tools=ToolRegistry(Workspace(child),lambda *_:True)
   self.assertEqual(tools.run('git_status',{}),'No Git repository rooted at the selected project.')
   self.assertEqual(tools.run('git_diff',{}),'No Git repository rooted at the selected project.')

 def test_git_status_and_log_are_available_only_at_project_root(self):
  import subprocess
  from pathlib import Path
  import tempfile
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);subprocess.run(['git','init','-q',str(root)],check=True)
   subprocess.run(['git','-C',str(root),'-c','user.name=Agent','-c','user.email=agent@example.invalid','commit','--allow-empty','-m','initial'],check=True,capture_output=True)
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   self.assertIn('## ',tools.run('git_status',{}))
   self.assertIn('initial',tools.run('git_log',{'limit':10}))

 def test_git_log_supports_large_requested_pages_and_offsets(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory).resolve();tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True);calls=[]
   def capture(argv):
    calls.append(argv)
    return (0,str(root),'') if '--show-toplevel' in argv else (0,'commit page\n','')
   tools._git_capture=capture
   self.assertEqual(tools.run('git_log',{'limit':2500,'offset':320}), 'commit page\n')
   self.assertEqual(calls[1],['git','log','--max-count=2501','--skip=320','--oneline','--decorate'])

 def test_git_log_page_reports_next_offset_only_when_more_history_exists(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory).resolve();tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True);calls=[]
   def capture(argv):
    calls.append(argv)
    if '--show-toplevel' in argv:return (0,str(root),'')
    return (0,'a first\nb second\nc third\n','')
   tools._git_capture=capture
   page=tools.run('git_log',{'limit':2,'offset':10})
   self.assertEqual(page,'a first\nb second\n\nMore commit history is available. Continue with offset=12, limit=2.')
   self.assertEqual(calls[1][2],'--max-count=3')

 def test_git_capture_keeps_waiting_after_initial_poll_until_process_finishes(self):
  import subprocess
  from unittest.mock import patch
  class DelayedProcess:
   pid=1234
   returncode=None
   def __init__(self):self.polls=0
   def poll(self):return self.returncode
   def communicate(self,timeout=None):
    self.polls+=1
    if self.polls==1:raise subprocess.TimeoutExpired(['git','status'],timeout)
    self.returncode=0
    return b'complete output',b''
   def kill(self):self.returncode=-9
   def terminate(self):self.returncode=-15
  with tempfile.TemporaryDirectory() as root:
   tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True);process=DelayedProcess()
   with patch('subprocess.Popen',return_value=process), \
        patch('desktop_engine.WindowsProcessJob.attach',return_value=None), \
        patch('desktop_engine.WindowsProcessJob.resume_primary_thread',return_value=True):
    code,stdout,stderr=tools._git_capture(['git','status'])
   self.assertEqual(code,0)
   self.assertEqual(stdout,'complete output')
   self.assertEqual(stderr,'')
   self.assertEqual(process.polls,2)

 def test_git_capture_returns_partial_output_when_cancelled(self):
  from unittest.mock import patch
  class CancelledProcess:
   pid=987654
   returncode=None
   def communicate(self,timeout=None):return b'partial status',b''
   def poll(self):return self.returncode
   def terminate(self):self.returncode=-15
   def kill(self):self.returncode=-9
  with tempfile.TemporaryDirectory() as root:
   tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True);process=CancelledProcess()
   tools.cancel_event.set()
   with patch('subprocess.Popen',return_value=process), \
        patch('desktop_engine.WindowsProcessJob.attach',return_value=None), \
        patch('desktop_engine.WindowsProcessJob.resume_primary_thread',return_value=True):
    code,stdout,stderr=tools._git_capture(['git','status'])
   self.assertEqual(code,-15)
   self.assertEqual(stdout,'partial status')
   self.assertEqual(stderr,'')
 def test_read_file_supports_line_ranges_and_files_over_old_cap(self):
  with tempfile.TemporaryDirectory() as t:
   content=''.join(f'{i:06}\n' for i in range(60000));(Path(t)/'large.txt').write_text(content)
   tools=ToolRegistry(Workspace(t),lambda *_:True)
   self.assertEqual(tools.run('read_file',{'path':'large.txt','start_line':4,'end_line':6}),'000003\n000004\n000005\n')
   self.assertEqual(tools.run('read_file',{'path':'large.txt'}),content)

 def test_context_budgeted_read_file_returns_resumable_unicode_column(self):
   import re
   with tempfile.TemporaryDirectory() as t:
    root=Path(t); content='a'*900+'雪'*100+'\nsecond line\n'; (root/'large.txt').write_text(content,encoding='utf8')
    tools=ToolRegistry(Workspace(root),lambda *_:True,read_output_token_budget=512)
    first=tools.run('read_file',{'path':'large.txt'})
    match=re.search(r'continue with read_file at line (\d+), column (\d+)',first,re.IGNORECASE)
    self.assertIsNotNone(match)
    line,column=map(int,match.groups())
    self.assertEqual(line,1)
    remainder=tools.run('read_file',{'path':'large.txt','start_line':line,'start_column':column})
    self.assertIn('second line',remainder)
    self.assertIn('雪'*10,remainder)

 def test_agent_read_budget_uses_selected_context_and_output_without_rewriting_them(self):
   from desktop_engine import AgentEngine
   with tempfile.TemporaryDirectory() as t:
    engine=AgentEngine(Path(t),context=65536,max_tokens=8192)
    self.assertEqual(engine.state['context'],65536)
    self.assertEqual(engine.state['max_tokens'],8192)
   self.assertEqual(engine.state['registry'].read_output_byte_budget,114688)

 def test_file_listing_pages_within_context_budget(self):
   import re
   with tempfile.TemporaryDirectory() as t:
    root=Path(t)
    for index in range(120):(root/f'project-file-{index:03}.txt').write_text('x')
    tools=ToolRegistry(Workspace(root),lambda *_:True,read_output_token_budget=512)
    first=tools.run('list_files',{'limit':1200})
    self.assertIn('More results are available',first)
    self.assertLess(len(first.encode('utf8')),1300)
    offset=int(re.search(r'Next offset (\d+)',first).group(1))
    second=tools.run('list_files',{'offset':offset,'limit':1200})
    self.assertNotEqual(first.splitlines()[0],second.splitlines()[0])

 def test_search_reports_large_matching_line_and_points_to_complete_file_read(self):
   with tempfile.TemporaryDirectory() as t:
    root=Path(t); (root/'large.py').write_text('needle '+('雪'*1000)+'\n',encoding='utf8')
    tools=ToolRegistry(Workspace(root),lambda *_:True,read_output_token_budget=512)
    result=tools.run('search_files',{'query':'needle','include':'*.py'})
    self.assertIn('large.py:1',result)
    self.assertIn('matching line exceeds the result budget',result)
    self.assertIn('read_file at line 1',result)
    self.assertLess(len(result.encode('utf8')),1300)

 def test_file_list_scan_cap_is_reported_only_when_more_files_exist(self):
   import desktop_engine
   with tempfile.TemporaryDirectory() as t:
    root=Path(t)
    for name in ('a.py','b.py'):(root/name).write_text('x')
    tools=ToolRegistry(Workspace(root),lambda *_:True)
    with patch.object(desktop_engine,'PROJECT_LIST_SCAN_LIMIT',2):
     complete=tools.run('list_files',{})
     self.assertNotIn('scan capped',complete.casefold())
     (root/'c.py').write_text('x')
     incomplete=tools.run('list_files',{})
     self.assertIn('scan capped',incomplete.casefold())
 def test_write_file_accepts_files_over_old_cap(self):
  with tempfile.TemporaryDirectory() as t:
   content='雪🙂'*300000;tools=ToolRegistry(Workspace(t,'edit'),lambda *_:True)
   self.assertEqual(tools.run('write_file',{'path':'large.txt','content':content}),'Wrote large.txt')
   self.assertEqual((Path(t)/'large.txt').read_text(),content)
 def test_cancelled_chunked_file_replacement_preserves_old_file_and_removes_temporary_file(self):
  class CancelAfterFirstChunk:
   def __init__(self):self.checks=0
   def is_set(self):
    self.checks+=1
    return self.checks>=2
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);target=root/'large.txt';target.write_text('original',encoding='utf8')
   tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True,cancel_event=CancelAfterFirstChunk())
   with self.assertRaises(RequestCancelled):
    tools.run('write_file',{'path':'large.txt','content':'x'*(2*1024*1024+1)})
   self.assertEqual(target.read_text(encoding='utf8'),'original')
   self.assertEqual([path.name for path in root.iterdir()],['large.txt'])
 def test_cancelled_chunked_append_rolls_back_partial_chunk_output(self):
  class CancelAfterFirstChunk:
   def __init__(self):self.checks=0
   def is_set(self):
    self.checks+=1
    return self.checks>=2
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);target=root/'large.txt';target.write_text('original',encoding='utf8')
   tools=ToolRegistry(Workspace(root,'edit'),lambda *_:True,cancel_event=CancelAfterFirstChunk())
   with self.assertRaises(RequestCancelled):
    tools.run('append_file',{'path':'large.txt','content':'x'*(2*1024*1024+1)})
   self.assertEqual(target.read_text(encoding='utf8'),'original')
 def test_direct_write_requires_approval(self):
  with tempfile.TemporaryDirectory() as t:
   tools=ToolRegistry(Workspace(t,'ask'),lambda *_:False)
   self.assertEqual(tools.run('write_file',{'path':'x','content':'no'}),'Denied by user.')
   self.assertFalse((Path(t)/'x').exists())
 def test_cancelled_file_search_and_read_return_explicit_cancelled_results(self):
   import threading
   with tempfile.TemporaryDirectory() as t:
    root=Path(t);(root/'a.py').write_text('needle\n',encoding='utf8')
    cancelled=threading.Event();cancelled.set()
    tools=ToolRegistry(Workspace(root),lambda *_:True,cancel_event=cancelled)
    self.assertTrue(tools.run('list_files',{}).startswith('Cancelled:'))
    self.assertTrue(tools.run('search_files',{'query':'needle'}).startswith('Cancelled:'))
    self.assertTrue(tools.run('read_file',{'path':'a.py'}).startswith('Cancelled:'))
 def test_copy_mode_writes_only_to_its_workspace_without_per_write_approval(self):
  with tempfile.TemporaryDirectory() as t:
   approvals=[];tools=ToolRegistry(Workspace(t,'copy'),lambda *args:approvals.append(args) or False)
   self.assertEqual(tools.run('write_file',{'path':'x','content':'draft'}),'Wrote x')
   self.assertEqual((Path(t)/'x').read_text(),'draft')
   self.assertEqual(approvals,[])
 def test_copy_mode_still_requires_approval_for_commands(self):
  with tempfile.TemporaryDirectory() as t:
   approvals=[];tools=ToolRegistry(Workspace(t,'copy'),lambda *args:approvals.append(args) or False)
   self.assertEqual(tools.run('run_command',{'argv':['python','-c','print(1)']}),'Denied by user.')
   self.assertEqual([entry[0] for entry in approvals],['run_command'])
 def test_pwd_returns_the_selected_project_root_on_windows(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t).resolve();tools=ToolRegistry(Workspace(root),lambda *_:True)
   self.assertEqual(tools.run('run_command',{'argv':['pwd']}),str(root))
 def test_copy_mode_applies_patch_inside_workspace_without_source_approval(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'a.txt';path.write_text('old\n');approvals=[]
   tools=ToolRegistry(Workspace(t,'copy'),lambda *args:approvals.append(args) or False)
   result=tools.run('apply_patch',{'patch':'--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n'})
   self.assertIn('a.txt',result);self.assertEqual(path.read_text(),'new\n');self.assertEqual(approvals,[])
 def test_edit_mode_allows_file_changes_but_commands_still_use_approval(self):
  with tempfile.TemporaryDirectory() as t:
   approvals=[];tools=ToolRegistry(Workspace(t,'edit'),lambda *args:approvals.append(args) or False)
   self.assertEqual(tools.run('write_file',{'path':'x','content':'draft'}),'Wrote x')
   self.assertEqual(approvals,[])
 def test_persistent_deny_blocks_writes_even_in_edit_mode_without_prompting(self):
  with tempfile.TemporaryDirectory() as t:
   prompts=[]
   tools=ToolRegistry(Workspace(t,'edit'),lambda *args:prompts.append(args) or True,
                      permission_rules=[{'tool':'write_file','decision':'deny'}])
   self.assertIn('persistent deny',tools.run('write_file',{'path':'x','content':'no'}).casefold())
   self.assertFalse((Path(t)/'x').exists())
   self.assertEqual(prompts,[])
 def test_persistent_ask_forces_a_prompt_even_when_file_access_is_edit(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'x';prompts=[]
   tools=ToolRegistry(Workspace(t,'edit'),lambda *args:prompts.append(args) or False,
                      permission_rules=[{'tool':'write_file','decision':'ask'}])
   self.assertIn('denied',tools.run('write_file',{'path':'x','content':'no'}).casefold())
   self.assertFalse(path.exists())
   self.assertEqual([row[0] for row in prompts],['write_file'])
 def test_scoped_allow_bypasses_normal_ask_mode_only_for_matching_path(self):
  from desktop_engine import AgentEngine
  with tempfile.TemporaryDirectory() as t:
   prompts=[]
   engine=AgentEngine(Path(t),access='ask',approve=lambda *args:prompts.append(args) or False,
                      permission_rules=[{'tool':'write_file','decision':'allow','path_glob':'generated/**'}])
   tools=engine.state['registry']
   self.assertEqual(tools.run('write_file',{'path':'generated/output.txt','content':'yes'}),'Wrote generated/output.txt')
   self.assertEqual(tools.run('write_file',{'path':'src/output.txt','content':'no'}),'Denied by user.')
   self.assertEqual([row[0] for row in prompts],['write_file'])
 def test_edit_file_replaces_one_exact_match_without_rewriting_unrelated_text(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'app.py';path.write_text('alpha = 1\nbeta = 2\n',encoding='utf-8')
   tools=ToolRegistry(Workspace(t,'edit'),lambda *_:True)
   result=tools.run('edit_file',{'path':'app.py','old_text':'beta = 2','new_text':'beta = 3'})
   self.assertEqual(result,'Edited app.py')
   self.assertEqual(path.read_text(encoding='utf-8'),'alpha = 1\nbeta = 3\n')
 def test_edit_file_rejects_missing_or_ambiguous_match_without_changing_file(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'app.py';path.write_text('value = 1\nvalue = 1\n',encoding='utf-8')
   tools=ToolRegistry(Workspace(t,'edit'),lambda *_:True)
   missing=tools.run('edit_file',{'path':'app.py','old_text':'absent','new_text':'changed'})
   ambiguous=tools.run('edit_file',{'path':'app.py','old_text':'value = 1','new_text':'value = 2'})
   self.assertIn('not found',missing)
   self.assertIn('2 matches',ambiguous)
   self.assertEqual(path.read_text(encoding='utf-8'),'value = 1\nvalue = 1\n')
 def test_edit_file_requires_approval_in_ask_mode(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'app.py';path.write_text('value = 1\n',encoding='utf-8');calls=[]
   tools=ToolRegistry(Workspace(t,'ask'),lambda name,args:calls.append(name) and False)
   self.assertEqual(tools.run('edit_file',{'path':'app.py','old_text':'value = 1','new_text':'value = 2'}),'Denied by user.')
   self.assertEqual(calls,['edit_file'])
   self.assertEqual(path.read_text(encoding='utf-8'),'value = 1\n')
 def test_approved_patch_prompts_once(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'a.txt';p.write_text('old\n');calls=[]
   tools=ToolRegistry(Workspace(t,'ask'),lambda name,args:calls.append(name) or True)
   result=tools.run('apply_patch',{'patch':'--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n'})
   self.assertIn('a.txt',result);self.assertEqual(calls,['apply_patch']);self.assertEqual(p.read_text(),'new\n')
if __name__=='__main__':unittest.main()
