"""Durable local session storage for the project agent."""
from __future__ import annotations
import json, math, os, re, tempfile, threading, time, uuid
from pathlib import Path
from typing import Any

class SessionStore:
    def __init__(self,root:Path|str): self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self._lock=threading.RLock()
    def create(self,workspace:str,model:str,context:int,options:dict|None=None)->dict[str,Any]:
        sid=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        provider='llama.cpp' if str(model).startswith('gguf::') else 'ollama'
        return {'schema_version':1,'session_id':sid,'created_at':time.time(),'updated_at':time.time(),'workspace':str(Path(workspace).resolve()),'model':model,'model_id':model,'provider_id':provider,'context':context,'runtime_options':options or {},'access':'ask','approval_mode':'per-action','messages':[{'role':'system','content':'You are a capable local software development agent. Inspect the project, plan substantial changes, use the available project tools, and ask before writes or commands. Follow project instructions and skills as relevant. The model controls its own reasoning behavior.'}],'turns':[],'todos':[]}
    def save(self,state:dict)->Path:
        state['updated_at']=time.time();path=self.root/(self._safe(state['session_id'])+'.json');tmp=None
        with self._lock:
            try:
                with tempfile.NamedTemporaryFile('w',encoding='utf8',delete=False,dir=self.root,prefix=path.stem+'-',suffix='.tmp') as handle:
                    tmp=Path(handle.name);json.dump(state,handle,indent=2,ensure_ascii=False);handle.flush();os.fsync(handle.fileno())
                _replace_with_retry(tmp,path);return path
            finally:
                if tmp is not None:
                    try:tmp.unlink(missing_ok=True)
                    except OSError:pass
    def load(self,sid:str)->dict:
        state=json.loads((self.root/(self._safe(sid)+'.json')).read_text(encoding='utf8'))
        if not isinstance(state,dict) or not {'session_id','workspace','model','context','messages'}<=state.keys():raise ValueError('Session file is incomplete')
        if state['session_id']!=sid:raise ValueError('Session identifier does not match its file name')
        if state.get('schema_version',1)!=1:raise ValueError(f"Unsupported session schema version: {state.get('schema_version')}")
        if not isinstance(state['workspace'],str) or not state['workspace']:raise ValueError('Session workspace is invalid')
        if not isinstance(state['model'],str) or not state['model']:raise ValueError('Session model is invalid')
        inferred_provider='llama.cpp' if state['model'].startswith('gguf::') else 'ollama'
        state.setdefault('provider_id',inferred_provider)
        state.setdefault('model_id',state['model'])
        if state['provider_id'] not in {'ollama','llama.cpp'} or not isinstance(state['model_id'],str) or not state['model_id']:raise ValueError('Session provider/model identity is invalid')
        if state['provider_id']!=inferred_provider or state['model_id']!=state['model']:raise ValueError('Session provider/model identity does not match its model reference')
        if isinstance(state['context'],bool) or not isinstance(state['context'],int) or state['context']<1:raise ValueError('Session context is invalid')
        if 'max_tokens' in state and (isinstance(state['max_tokens'],bool) or not isinstance(state['max_tokens'],int) or state['max_tokens']<1):raise ValueError('Session maximum output token setting is invalid')
        if 'temperature' in state and (isinstance(state['temperature'],bool) or not isinstance(state['temperature'],(int,float)) or not 0<=state['temperature']<=2 or not math.isfinite(state['temperature'])):raise ValueError('Session temperature setting is invalid')
        if not isinstance(state.get('access','ask'),str) or state.get('access','ask') not in {'plan','copy','ask','edit'}:raise ValueError('Session access mode is invalid')
        if not isinstance(state.get('approval_mode','per-action'),str) or state.get('approval_mode','per-action') not in {'per-action','per-task','per-session','auto'}:raise ValueError('Session approval mode is invalid')
        if 'session_approval_granted' in state and not isinstance(state['session_approval_granted'],bool):raise ValueError('Saved session approval grant is invalid')
        state.setdefault('session_approval_granted',False)
        if 'reasoning' in state and state['reasoning'] is not None and (not isinstance(state['reasoning'],(str,bool))):raise ValueError('Session reasoning setting is invalid')
        if 'working_workspace' in state and (not isinstance(state['working_workspace'],str) or not state['working_workspace']):raise ValueError('Session working workspace is invalid')
        manifest=state.get('copy_manifest')
        if manifest is not None:
            if not isinstance(manifest,dict) or len(manifest)>100000:raise ValueError('Session copy baseline is invalid')
            for relative,digest in manifest.items():
                if not isinstance(relative,str) or not relative:raise ValueError('Session copy baseline entry is invalid')
                path=Path(relative)
                if path.is_absolute() or '..' in path.parts or not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('Session copy baseline entry is invalid')
        messages=state['messages']
        if not isinstance(messages,list) or any(not isinstance(m,dict) or m.get('role') not in {'system','user','assistant','tool'} or not isinstance(m.get('content',''),str) for m in messages):raise ValueError('Session message history is invalid')
        if not isinstance(state.get('turns',[]),list):raise ValueError('Session turn history is invalid')
        return state
    def list(self)->list[dict]:
        rows=[]
        for p in self.root.glob('*.json'):
            try:
                s=json.loads(p.read_text(encoding='utf8'))
                if isinstance(s,dict):rows.append({k:s.get(k) for k in ('session_id','workspace','model','provider_id','model_id','updated_at')})
            except (OSError,json.JSONDecodeError):continue
        return sorted(rows,key=lambda x:x.get('updated_at') or 0,reverse=True)
    @staticmethod
    def _safe(sid:str)->str:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',sid):raise ValueError('Invalid session id')
        return sid


def _replace_with_retry(source:Path,destination:Path)->None:
    """Wait for transient Windows sharing locks to release instead of dropping a session save."""
    delay=0.05
    while True:
        try:
            os.replace(source,destination)
            return
        except PermissionError:
            time.sleep(delay)
            delay=min(delay*2,0.75)
