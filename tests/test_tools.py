import unittest,tempfile
from unittest.mock import patch
from pathlib import Path
from desktop_engine import Workspace,ToolRegistry
class ToolTests(unittest.TestCase):
 def test_advertised_tools_include_git_diff(self):
  with tempfile.TemporaryDirectory() as t:
   tools=ToolRegistry(Workspace(t),lambda *_:True)
   names={schema['function']['name'] for schema in tools.schemas}
   self.assertIn('git_diff',names)
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
    self.assertEqual(engine.state['registry'].read_output_byte_budget,112640)

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
   content='x'*600000;tools=ToolRegistry(Workspace(t,'edit'),lambda *_:True)
   self.assertEqual(tools.run('write_file',{'path':'large.txt','content':content}),'Wrote large.txt')
   self.assertEqual((Path(t)/'large.txt').read_text(),content)
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
