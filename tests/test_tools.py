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
 def test_approved_patch_prompts_once(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'a.txt';p.write_text('old\n');calls=[]
   tools=ToolRegistry(Workspace(t,'ask'),lambda name,args:calls.append(name) or True)
   result=tools.run('apply_patch',{'patch':'--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n'})
   self.assertIn('a.txt',result);self.assertEqual(calls,['apply_patch']);self.assertEqual(p.read_text(),'new\n')
if __name__=='__main__':unittest.main()
