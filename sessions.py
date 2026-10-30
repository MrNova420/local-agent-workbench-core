"""Durable local session storage for the project agent."""
from __future__ import annotations
import json, os, re, time, uuid
from pathlib import Path
from typing import Any

class SessionStore:
    def __init__(self,root:Path|str): self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
    def create(self,workspace:str,model:str,context:int,options:dict|None=None)->dict[str,Any]:
        sid=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        return {'schema_version':1,'session_id':sid,'created_at':time.time(),'updated_at':time.time(),'workspace':str(Path(workspace).resolve()),'model':model,'context':context,'runtime_options':options or {},'access':'ask','approval_mode':'per-action','messages':[{'role':'system','content':'You are a capable local software development agent. Inspect the project, plan substantial changes, use the available project tools, and ask before writes or commands. Follow project instructions and skills as relevant. The model controls its own reasoning behavior.'}],'turns':[],'todos':[]}
    def save(self,state:dict)->Path:
        state['updated_at']=time.time();path=self.root/(self._safe(state['session_id'])+'.json');tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state,indent=2,ensure_ascii=False),encoding='utf8');os.replace(tmp,path);return path
    def load(self,sid:str)->dict:
        state=json.loads((self.root/(self._safe(sid)+'.json')).read_text(encoding='utf8'))
        if not {'session_id','workspace','model','context','messages'}<=state.keys():raise ValueError('Session file is incomplete')
        return state
    def list(self)->list[dict]:
        rows=[]
        for p in self.root.glob('*.json'):
            try:s=json.loads(p.read_text(encoding='utf8'));rows.append({k:s.get(k) for k in ('session_id','workspace','model','updated_at')})
            except (OSError,json.JSONDecodeError):continue
        return sorted(rows,key=lambda x:x.get('updated_at') or 0,reverse=True)
    @staticmethod
    def _safe(sid:str)->str:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',sid):raise ValueError('Invalid session id')
        return sid
