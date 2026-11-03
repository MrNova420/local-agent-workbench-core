"""Curated file preview helpers; never walk outside the chosen project."""
from pathlib import Path
from desktop_engine import IGNORED,TEXT_EXT
from project_knowledge import ProjectKnowledge
from project_walk import iter_project_files

def preview_file_page(root:Path|str,offset:int=0,limit:int=200):
 if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:raise ValueError("File page offset must be a non-negative integer.")
 if isinstance(limit,bool) or not isinstance(limit,int) or limit<1:raise ValueError("File page size must be a positive integer.")
 base=Path(root).resolve(strict=True);rows=[];next_offset=None
 end=offset+limit
 for index,p in enumerate(iter_project_files(base,max_files=end+1)):
  if index<offset:continue
  if index>=end:
   next_offset=end
   break
  try:size=p.stat().st_size
  except OSError:continue
  rows.append({'path':p.relative_to(base).as_posix(),'size':size,'text':p.suffix.casefold() in TEXT_EXT})
 return {'files':rows,'offset':offset,'next_offset':next_offset}

def preview_files(root:Path|str,limit:int=1000):
 return preview_file_page(root,limit=limit)['files']
