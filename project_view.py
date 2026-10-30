"""Curated file preview helpers; never walk outside the chosen project."""
from pathlib import Path
from desktop_engine import IGNORED,TEXT_EXT
from project_knowledge import ProjectKnowledge
from project_walk import iter_project_files

def preview_files(root:Path|str,limit:int=1000):
 base=Path(root).resolve(strict=True);rows=[]
 for p in iter_project_files(base):
  try:size=p.stat().st_size
  except OSError:continue
  rows.append({'path':p.relative_to(base).as_posix(),'size':size,'text':p.suffix.casefold() in TEXT_EXT})
  if len(rows)>=limit:break
 return rows
