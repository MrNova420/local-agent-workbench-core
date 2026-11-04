"""Curated file preview helpers; never walk outside the chosen project."""
import os
import stat
import threading
from pathlib import Path
from desktop_engine import IGNORED,TEXT_EXT
from project_knowledge import ProjectKnowledge
from project_walk import iter_project_files

def _directory_listing_state(path:Path):
 try:
  is_junction=getattr(os.path,"isjunction",lambda value:False)
  if path.is_symlink() or is_junction(path):return None
  directory=path.lstat()
  if not stat.S_ISDIR(directory.st_mode):return None
  ignore=path/'.gitignore'
  if ignore.is_symlink() or is_junction(ignore):ignore_state=('linked',)
  elif ignore.is_file():
   ignored=ignore.stat()
   ignore_state=(ignored.st_dev,ignored.st_ino,ignored.st_mtime_ns,ignored.st_ctime_ns,ignored.st_size)
  else:ignore_state=None
  return (directory.st_dev,directory.st_ino,directory.st_mtime_ns,directory.st_ctime_ns,directory.st_size,ignore_state)
 except (OSError,RuntimeError):return None


class ProjectFilePageCursor:
 """Lazily retain the selected project's walk between adjacent UI pages."""
 def __init__(self,root:Path|str):
  self.root=Path(root).resolve(strict=True)
  self._lock=threading.RLock()
  self._reset()
 def _reset(self):
  self.directory_states={}
  self.position=0
  self.pending=None
  self.exhausted=False
  self.paths=iter_project_files(self.root,directory_observer=self._observe_directory)
 def _observe_directory(self,path:Path):
  self.directory_states[path]=_directory_listing_state(path)
 def _is_current(self):
  return all(before is not None and _directory_listing_state(path)==before
             for path,before in self.directory_states.items())
 def _next_path(self):
  if self.pending is not None:
   path=self.pending
   self.pending=None
   return path
  if self.exhausted:return None
  path=next(self.paths,None)
  if path is None:self.exhausted=True
  return path
 def page(self,offset:int=0,limit:int=200):
  if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:raise ValueError("File page offset must be a non-negative integer.")
  if isinstance(limit,bool) or not isinstance(limit,int) or limit<1:raise ValueError("File page size must be a positive integer.")
  with self._lock:
   if offset<self.position or not self._is_current():self._reset()
   while self.position<offset:
    if self._next_path() is None:break
    self.position+=1
   rows=[];end=offset+limit
   while self.position<end:
    path=self._next_path()
    if path is None:break
    self.position+=1
    try:size=path.stat().st_size
    except OSError:continue
    rows.append({'path':path.relative_to(self.root).as_posix(),'size':size,'text':path.suffix.casefold() in TEXT_EXT})
   next_offset=None
   if self.position>=end and not self.exhausted:
    following=self._next_path()
    if following is not None:
     self.pending=following
     next_offset=end
   return {'files':rows,'offset':offset,'next_offset':next_offset}


def preview_file_page(root:Path|str,offset:int=0,limit:int=200,cursor:ProjectFilePageCursor|None=None):
 base=Path(root).resolve(strict=True)
 pager=cursor if cursor is not None and cursor.root==base else ProjectFilePageCursor(base)
 return pager.page(offset,limit)

def preview_files(root:Path|str,limit:int=1000):
 return preview_file_page(root,limit=limit)['files']
