"""Read project guidance and skills on demand, with symlink/traversal protections."""
from pathlib import Path
from typing import Any
from project_walk import DEFAULT_IGNORED,iter_project_files
IGNORED=DEFAULT_IGNORED

class ProjectKnowledge:
 def __init__(self,workspace:Path|str):self.root=Path(workspace).resolve(strict=True)
 def instructions(self,max_chars:int=50000)->str:
  names={'AGENTS.md','CLAUDE.md','GEMINI.md','.github/copilot-instructions.md'}; found=[]
  for p in iter_project_files(self.root,max_files=10_000):
   rel=p.relative_to(self.root).as_posix()
   if p.name in {'AGENTS.md','CLAUDE.md','GEMINI.md'} or rel=='.github/copilot-instructions.md' or (rel.startswith('.github/instructions/') and p.name.endswith('.instructions.md')):
    try:found.append((len(p.relative_to(self.root).parts),rel,p.read_text(encoding='utf8')))
    except (OSError,UnicodeError):pass
  out=[];used=0
  for _,rel,text in sorted(found):
   block=f'--- {rel} ---\n{text}';out.append(block);used+=len(block)
   if used>=max_chars:break
  return '\n\n'.join(out) or '(none found)'
 def skills(self,roots:list[Path]|None=None)->list[dict[str,str]]:
  candidates=roots or [self.root/'.agents/skills',self.root/'.claude/skills',self.root/'.opencode/skills',Path.home()/'.agents/skills',Path.home()/'.claude/skills',Path.home()/'.codex/skills',Path.home()/'.config/opencode/skills']
  found=[]
  for base in candidates:
   if not base.is_dir() or base.is_symlink():continue
   for folder in base.iterdir():
    f=folder/'SKILL.md'
    if folder.is_dir() and not folder.is_symlink() and f.is_file() and not f.is_symlink():found.append({'name':folder.name,'path':str(f)})
  return found
 def load_skill(self,name:str,skills:list[dict[str,str]])->str:
  match=next((s for s in skills if s['name']==name),None)
  if not match:raise ValueError('Unknown skill')
  path=Path(match['path']).resolve(strict=True)
  try:text=path.read_text(encoding='utf8')
  except (OSError,UnicodeError) as exc:raise ValueError(f'Could not read skill: {exc}')
  return text[:40000]
