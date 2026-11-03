import unittest, tempfile
from pathlib import Path
from project_view import preview_file_page, preview_files
class ViewTests(unittest.TestCase):
 def test_file_preview_excludes_symlinks_and_caches(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'a.py').write_text('x');(root/'.git').mkdir();(root/'.git/config').write_text('secret')
   rows=preview_files(root);self.assertEqual([r['path'] for r in rows],['a.py'])
 def test_project_file_pages_cover_all_files_and_allow_large_requested_page(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for index in range(2005): (root/f'{index:04}.txt').write_text(str(index))
   first=preview_file_page(root,offset=0,limit=500)
   second=preview_file_page(root,offset=first['next_offset'],limit=500)
   final=preview_file_page(root,offset=second['next_offset'],limit=2500)
   paths=[row['path'] for page in (first,second,final) for row in page['files']]
   self.assertEqual(len(paths),2005)
   self.assertEqual(len(set(paths)),2005)
   self.assertEqual(paths[-1],'2004.txt')
   self.assertIsNone(final['next_offset'])
if __name__=='__main__':unittest.main()
