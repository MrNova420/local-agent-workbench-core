import unittest,tempfile
from pathlib import Path
from desktop_engine import Workspace,ToolRegistry
class ToolTests(unittest.TestCase):
 def test_advertised_tools_include_git_diff(self):
  with tempfile.TemporaryDirectory() as t:
   tools=ToolRegistry(Workspace(t),lambda *_:True)
   names={schema['function']['name'] for schema in tools.schemas}
   self.assertIn('git_diff',names)
 def test_file_listing_discloses_result_cap(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for i in range(1201):(root/f'{i:04}.txt').write_text('x')
   tools=ToolRegistry(Workspace(root),lambda *_:True)
   result=tools.run('list_files',{'path':'.','pattern':'*.txt'})
   self.assertIn('[File listing capped at 1,200 entries; results may be incomplete.]',result)
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
 def test_workspace_rejects_path_escape(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'project';root.mkdir();ws=Workspace(root)
   with self.assertRaises(ValueError):ws.resolve('../outside.txt')
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
