import unittest,tempfile
from pathlib import Path
from desktop_engine import Workspace,ToolRegistry
class ToolTests(unittest.TestCase):
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
   tools=ToolRegistry(Workspace(t,'edit'),lambda *_:False)
   self.assertEqual(tools.run('write_file',{'path':'x','content':'no'}),'Denied by user.')
   self.assertFalse((Path(t)/'x').exists())
 def test_approved_patch_prompts_once(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'a.txt';p.write_text('old\n');calls=[]
   tools=ToolRegistry(Workspace(t,'edit'),lambda name,args:calls.append(name) or True)
   result=tools.run('apply_patch',{'patch':'--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n'})
   self.assertIn('a.txt',result);self.assertEqual(calls,['apply_patch']);self.assertEqual(p.read_text(),'new\n')
if __name__=='__main__':unittest.main()
