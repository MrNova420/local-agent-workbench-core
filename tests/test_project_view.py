import unittest, tempfile
from pathlib import Path
from project_view import preview_files
class ViewTests(unittest.TestCase):
 def test_file_preview_excludes_symlinks_and_caches(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'a.py').write_text('x');(root/'.git').mkdir();(root/'.git/config').write_text('secret')
   rows=preview_files(root);self.assertEqual([r['path'] for r in rows],['a.py'])
if __name__=='__main__':unittest.main()
