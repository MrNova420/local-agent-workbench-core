import unittest, tempfile
from unittest.mock import patch
from pathlib import Path
import project_view
from project_view import ProjectFilePageCursor, preview_file_page, preview_files
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
 def test_sequential_project_browser_pages_reuse_the_lazy_walk(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for name in ('a.txt','b.txt','c.txt','d.txt','e.txt','f.txt'):(root/name).write_text(name)
   traversed=[];real_walk=project_view.iter_project_files
   def recording_walk(*args,**kwargs):
    for path in real_walk(*args,**kwargs):
     traversed.append(path.name);yield path
   with patch('project_view.iter_project_files',side_effect=recording_walk):
    pager=ProjectFilePageCursor(root)
    first=pager.page(0,2);second=pager.page(2,2)
   self.assertEqual([row['path'] for row in first['files']],['a.txt','b.txt'])
   self.assertEqual([row['path'] for row in second['files']],['c.txt','d.txt'])
   self.assertEqual(len(traversed),5)
   self.assertEqual(len(set(traversed)),5)
 def test_project_browser_cursor_restarts_after_gitignore_or_nested_directory_changes(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);nested=root/'src';nested.mkdir();(root/'.gitignore').write_text('',encoding='utf-8')
   for name in ('a.txt','b.txt','c.txt','d.txt'):(nested/name).write_text(name)
   pager=ProjectFilePageCursor(root)
   first=pager.page(0,2)
   self.assertEqual(first['next_offset'],2)
   (root/'.gitignore').write_text('src/b.txt\n',encoding='utf-8')
   second=pager.page(2,2)
   self.assertEqual([row['path'] for row in second['files']],['src/c.txt','src/d.txt'])
   self.assertIsNone(second['next_offset'])
   (nested/'e.txt').write_text('e')
   third=pager.page(4,2)
   self.assertEqual([row['path'] for row in third['files']],['src/e.txt'])
   self.assertIsNone(third['next_offset'])
 def test_project_browser_cursor_falls_back_for_backward_offsets_and_keeps_roots_isolated(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);other=root/'other';other.mkdir()
   for name in ('a.txt','b.txt','c.txt'):(root/name).write_text(name)
   (other/'x.txt').write_text('x')
   pager=ProjectFilePageCursor(root)
   pager.page(0,2)
   self.assertEqual(pager.page(0,1)['files'][0]['path'],'a.txt')
   other_pager=ProjectFilePageCursor(other)
   self.assertEqual(other_pager.page(0,1)['files'][0]['path'],'x.txt')
if __name__=='__main__':unittest.main()
