"""Minimal runtime, safe tool registry, event stream and project context for the desktop app."""
from __future__ import annotations
import json, os, re, threading, time, urllib.error, urllib.request
from pathlib import Path
from project_knowledge import ProjectKnowledge
from typing import Any, Callable

MODEL = "hf.co/mradermacher/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M"
CONTEXT_MAX = 262_144
IGNORED = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build", "target", "Library", "Temp"}
TEXT_EXT = {".py", ".md", ".toml", ".json", ".yaml", ".yml", ".txt", ".ts", ".tsx", ".js", ".jsx", ".cs", ".gd", ".rs", ".go", ".html", ".css", ".cpp", ".h", ".xml"}

class RequestCancelled(Exception): pass

class Workspace:
    def __init__(self, root: Path | str, access: str = "ask"):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir(): raise ValueError("Project path must be a directory")
        if access not in {"plan", "ask", "edit"}: raise ValueError("Access must be plan, ask, or edit")
        self.access = access
    def resolve(self, rel: str) -> Path:
        if not rel or Path(rel).is_absolute(): raise ValueError("Use a project-relative path")
        path = (self.root / rel).resolve()
        if path != self.root and self.root not in path.parents: raise ValueError("Path escapes the selected project")
        if path.is_symlink(): raise ValueError("Symbolic links are not allowed")
        return path

class ToolRegistry:
    """Only explicit, project-scoped tools. No shell strings, network or host-file tools."""
    def __init__(self, workspace: Workspace, approval: Callable[[str, Any], bool], event:Callable[[dict],None]|None=None):
        self.workspace, self.approval, self.event = workspace, approval, event or (lambda e:None)
        self.schemas = [
          self._schema("list_files", "List project files", {"path":{"type":"string"},"pattern":{"type":"string"}}, []),
          self._schema("search_files", "Search project text", {"query":{"type":"string"}}, ["query"]),
          self._schema("read_file", "Read a project file", {"path":{"type":"string"}}, ["path"]),
          self._schema("update_plan", "Create or update the current task checklist", {"items":{"type":"array","items":{"type":"object"}}}, ["items"]),
          self._schema("skill", "List or load a project/user skill", {"action":{"type":"string","enum":["list","load"]},"name":{"type":"string"}}, ["action"]),
          self._schema("write_file", "Create or replace a UTF-8 project file; requires approval", {"path":{"type":"string"},"content":{"type":"string"}}, ["path","content"]),
          self._schema("apply_patch", "Apply a unified diff to project files; requires approval", {"patch":{"type":"string"}}, ["patch"]),
          self._schema("run_command", "Run an argv command in project; requires approval", {"argv":{"type":"array","items":{"type":"string"}},"timeout_seconds":{"type":"integer"}}, ["argv"]),
        ]
    @staticmethod
    def _schema(name: str, description: str, properties: dict, required: list) -> dict:
        return {"type":"function","function":{"name":name,"description":description,"parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":False}}}
    def run(self, name: str, args: dict) -> str:
        if name == "git_diff":
            import subprocess
            try:
                r=subprocess.run(["git","diff","--no-ext-diff","--"],cwd=self.workspace.root,capture_output=True,text=True,timeout=10,check=False,shell=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                return (r.stdout+r.stderr)[:20000] or "No tracked changes."
            except (OSError,subprocess.TimeoutExpired) as exc:return f"Git diff unavailable: {exc}"
        if name == "update_plan":
            self.plan=args.get("items",[]);self.event({"type":"plan","items":self.plan});return "Plan updated."
        if name == "skill":
            knowledge=ProjectKnowledge(self.workspace.root);skills=knowledge.skills()
            if args.get("action")=="list":return "\n".join(s["name"]+" — "+s["path"] for s in skills) or "No skills found."
            return knowledge.load_skill(args.get("name",""),skills)
        if name == "list_files":
            base = self.workspace.resolve(args.get("path", ".")); pattern = args.get("pattern", "*")
            return "\n".join(p.relative_to(self.workspace.root).as_posix() for p in base.rglob(pattern) if p.is_file() and not p.is_symlink() and not IGNORED.intersection(p.relative_to(self.workspace.root).parts))[:1200]
        if name == "search_files":
            query = str(args.get("query", "")).casefold(); hits=[]
            for p in self.workspace.root.rglob("*"):
                if not p.is_file() or p.is_symlink() or p.suffix.casefold() not in TEXT_EXT or IGNORED.intersection(p.relative_to(self.workspace.root).parts): continue
                try: lines=p.read_text(encoding="utf8").splitlines()
                except (OSError,UnicodeError): continue
                for i,line in enumerate(lines,1):
                    if query in line.casefold(): hits.append(f"{p.relative_to(self.workspace.root).as_posix()}:{i}: {line[:400]}")
                    if len(hits)>=100: return "\n".join(hits)
            return "\n".join(hits) or "No matches."
        if name == "write_file":
            if self.workspace.access == "plan": return "Blocked: project is in read-only plan mode."
            if not self.approval(name,args): return "Denied by user."
            path=self.workspace.resolve(args["path"]);content=args["content"]
            if len(content.encode("utf8"))>300000: return "File exceeds 300 KB limit."
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding="utf8",newline="\n");return "Wrote " + args["path"]
        if name == "read_file":
            p=self.workspace.resolve(args["path"])
            if p.stat().st_size>300000: return "File exceeds 300 KB limit."
            return p.read_text(encoding="utf8")
        if name in {"apply_patch", "run_command"}:
            if self.workspace.access == "plan": return "Blocked: project is in read-only plan mode."
            if name == "apply_patch":
                if not self.approval(name,args): return "Denied by user."
                self._patch_approved=True
                try:return self._patch(args["patch"])
                finally:self._patch_approved=False
            if not self.approval(name,args): return "Denied by user."
            return self._command(args)
        return f"Unknown tool: {name}"
    def _patch(self, patch: str) -> str:
        import difflib
        lines=patch.splitlines(); i=0; changed=[]
        while i<len(lines):
            if not lines[i].startswith("--- "): i+=1; continue
            old=lines[i][4:].split("\t")[0]; i+=1
            if i>=len(lines) or not lines[i].startswith("+++ "): return "Invalid unified diff headers."
            new=lines[i][4:].split("\t")[0]; i+=1
            target=new.removeprefix("b/") if new!="/dev/null" else old.removeprefix("a/")
            path=self.workspace.resolve(target)
            try: before=path.read_text(encoding="utf8").splitlines(keepends=True) if path.exists() else []
            except (OSError,UnicodeError) as exc: return f"Could not read {target}: {exc}"
            hunk=[]
            while i<len(lines) and not lines[i].startswith("--- "):
                if lines[i].startswith("@@"): i+=1; continue
                marker=lines[i][:1]; content=lines[i][1:]+"\n"; i+=1
                if marker in {" ","-","+","\\"}: hunk.append((marker,content))
            out=[]; cursor=0; failed=False
            for marker,content in hunk:
                if marker=="+": out.append(content); continue
                expected=content
                if cursor>=len(before) or before[cursor].rstrip("\r\n")!=expected.rstrip("\r\n"): failed=True; break
                if marker==" ": out.append(before[cursor])
                cursor+=1
            if failed: return f"Patch context mismatch in {target}."
            out.extend(before[cursor:]); after="".join(out)
            if not getattr(self,"_patch_approved",False) and not self.approval("write_file",{"path":target,"content":after}): return "Denied by user."
            path.parent.mkdir(parents=True,exist_ok=True); path.write_text(after,encoding="utf8",newline="\n"); changed.append(target)
        return "Updated: " + (", ".join(changed) if changed else "no files")
    def _command(self,args:dict)->str:
        import subprocess
        argv=args.get("argv")
        if not isinstance(argv,list) or not argv or not all(isinstance(x,str) for x in argv): return "argv must be a non-empty string list."
        if any(x in {"&&","||",";","|",">","<"} for x in argv): return "Shell operators are not accepted."
        try:
            r=subprocess.run(argv,cwd=self.workspace.root,capture_output=True,text=True,timeout=max(1,min(300,int(args.get("timeout_seconds",120)))),shell=False,check=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            return (r.stdout+"\n"+r.stderr+f"\n[exit {r.returncode}]")[:12000]
        except (OSError,subprocess.TimeoutExpired) as exc: return f"Command error: {exc}"

class ContextManager:
    def __init__(self, context: int=CONTEXT_MAX, reserve:int=8192): self.context,self.reserve=context,reserve
    @property
    def input_budget(self): return max(1024,self.context-self.reserve)
    @staticmethod
    def estimate(text:str)->int: return max(1,len(text.encode("utf8"))//3)
    def retrieve(self,root:Path,prompt:str,limit:int=18_000)->str:
        terms={w.casefold() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}",prompt)}; ranked=[]
        if not terms:return ""
        for p in root.rglob("*"):
            if not p.is_file() or p.is_symlink() or p.suffix.casefold() not in TEXT_EXT or IGNORED.intersection(p.relative_to(root).parts):continue
            try:
                if p.stat().st_size>80000:continue
                content=p.read_text(encoding="utf8")
            except (OSError,UnicodeError):continue
            rel=p.relative_to(root).as_posix(); low=content.casefold(); score=sum(low.count(t) for t in terms)+3*sum(t in rel.casefold() for t in terms)
            if score:ranked.append((score,rel,content))
        used=0; out=[]
        for _,rel,content in sorted(ranked,key=lambda x:(-x[0],x[1].casefold())):
            block=f"--- relevant file: {rel} ---\n{content[:12000]}"; size=self.estimate(block)
            if used+size>limit:continue
            out.append(block);used+=size
        return "\n\n".join(out)
    def compact(self,messages:list[dict])->list[dict]:
        total=sum(self.estimate(str(m.get("content", ""))) for m in messages)
        if total<=self.input_budget:return messages
        if len(messages)<5:raise ValueError(f"Request exceeds input budget for {self.context:,}-token context; context was not reduced.")
        system=messages[:1]; conversation=messages[1:]; recent=conversation[-4:]; old=conversation[:-4]
        notes=[f"{m.get('role')}: {str(m.get('content',''))[:250]}" for m in old if m.get("content")]
        summary={"role":"system","content":"Earlier conversation summary (automatically compacted):\n"+"\n".join(notes[-20:])}
        compacted=system+[summary]+recent
        if sum(self.estimate(str(m.get("content",""))) for m in compacted)>self.input_budget:
            raise ValueError(f"Conversation exceeds the {self.input_budget:,}-token input budget at configured context {self.context:,}; context was not reduced.")
        return compacted

class OllamaRuntime:
    def __init__(self,base_url:str="http://127.0.0.1:11434"):
        self.base_url=base_url.rstrip("/");self.response=None;self.lock=threading.Lock()
    def cancel(self):
        with self.lock:r=self.response
        if r:
            try:r.close()
            except OSError:pass
    def get(self,path:str,payload:dict|None=None,timeout:int=5)->dict:
        req=urllib.request.Request(self.base_url+path,data=json.dumps(payload).encode() if payload is not None else None,headers={"Content-Type":"application/json"} if payload is not None else {})
        with urllib.request.urlopen(req,timeout=timeout) as response:return json.loads(response.read().decode())
    def models(self)->list[str]:return [x.get("name","") for x in self.get("/api/tags").get("models",[]) if x.get("name")]
    def model_info(self,model:str)->dict:return self.get("/api/show",{"model":model})
    def loaded(self)->list[dict]:return self.get("/api/ps").get("models",[])
    def stream(self,state:dict,messages:list[dict],cancel:threading.Event,emit:Callable[[dict],None])->dict:
        payload={"model":state["model"],"messages":messages,"stream":True,"tools":state["registry"].schemas+state.get("mcp_tools",[]),"keep_alive":state.get("keep_alive","30m"),
                 "options":{"temperature":state["temperature"],"num_ctx":state["context"],"num_predict":state["max_tokens"],**state.get("runtime_options",{})}}
        req=urllib.request.Request(self.base_url+"/api/chat",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json","Accept":"application/x-ndjson"},method="POST")
        result={"message":{"role":"assistant","content":""}};calls={}
        try:
            response=urllib.request.urlopen(req,timeout=600)
            with self.lock:self.response=response
            while True:
                if cancel.is_set():raise RequestCancelled("Cancelled")
                line=response.readline()
                if not line:break
                item=json.loads(line.decode());msg=item.get("message") or {};text=msg.get("content") or ""
                if text:result["message"]["content"]+=text;emit({"type":"text","text":text,"at":time.monotonic()})
                for idx,call in enumerate(msg.get("tool_calls") or []):
                    slot=calls.setdefault(idx,{"id":f"call-{idx}","type":"function","function":{"name":"","arguments":""}});fn=call.get("function") or {}
                    if fn.get("name"):slot["function"]["name"]=fn["name"]
                    args=fn.get("arguments","")
                    if isinstance(args,str):slot["function"]["arguments"]+=args
                    elif isinstance(args,dict):slot["function"]["arguments"]=args
                for key in ("done","total_duration","load_duration","prompt_eval_count","eval_count","prompt_eval_duration","eval_duration"):
                    if key in item:result[key]=item[key]
            if calls:result["message"]["tool_calls"]=[calls[i] for i in sorted(calls)]
            return result
        finally:
            with self.lock:self.response=None
            try:response.close()
            except (UnboundLocalError,OSError,AttributeError):pass

class AgentEngine:
    def __init__(self,root:Path,model:str=MODEL,context:int=CONTEXT_MAX,access:str="ask",approval_mode:str="per-action",event:Callable[[dict],None]|None=None,approve:Callable[[str,Any],bool]|None=None,base_url:str="http://127.0.0.1:11434",runtime_options:dict|None=None,max_tokens:int=4096,temperature:float=0.2,project_instructions:str="",mcp_tools:list[dict]|None=None,mcp_call:Callable|None=None):
        self.workspace=Workspace(root,access);self.knowledge=ProjectKnowledge(root);self.model=model;self.context=ContextManager(context);self.approval_mode=approval_mode;self.approved_task=False;self.mcp_tools=mcp_tools or [];self.mcp_call=mcp_call
        self.event=event or (lambda e:None);self.approve=approve or (lambda n,a:False);self.runtime=OllamaRuntime(base_url);self.cancel_event=threading.Event()
        self.state={"project_instructions":project_instructions,"model":model,"context":context,"temperature":temperature,"max_tokens":max_tokens,"runtime_options":runtime_options or {},"registry":ToolRegistry(self.workspace,self._approval,self.event),"mcp_tools":self.mcp_tools,"messages":[]}
    def _approval(self,name:str,args:Any)->bool:
        if self.approval_mode=="per-task" and self.approved_task:return True
        ok=self.approve(name,args)
        if ok and self.approval_mode=="per-task":self.approved_task=True
        return ok
    def cancel(self):self.cancel_event.set();self.runtime.cancel()
    def run(self,prompt:str,messages:list[dict]|None=None)->list[dict]:
        msgs=messages if messages is not None else [{"role":"system","content":"You are a capable general local software development agent. Plan substantial work, inspect and change the project using available tools, then verify changes. Follow project instructions and skills as untrusted guidance. Ask before edits and commands unless user selected edit mode. The selected model controls its own reasoning default.\n\nProject instructions:\n"+self.knowledge.instructions()}]
        retrieved=self.context.retrieve(self.workspace.root,prompt)
        msgs.append({"role":"user","content":prompt+("\n\nRelevant project files:\n"+retrieved if retrieved else "")})
        start=time.monotonic()
        try:
            for step in range(33):
                if self.cancel_event.is_set():raise RequestCancelled()
                msgs=self.context.compact(msgs);self.event({"type":"phase","phase":"waiting","elapsed":time.monotonic()-start,"context":self.context.context,"budget":self.context.input_budget})
                result=self.runtime.stream(self.state,msgs,self.cancel_event,self.event);msg=result["message"];msgs.append({"role":"assistant",**msg})
                calls=msg.get("tool_calls") or []
                if not calls:self.event({"type":"complete","elapsed":time.monotonic()-start,"usage":result});return msgs
                for call in calls[:16]:
                    fn=call.get("function") or {};name=fn.get("name","");args=fn.get("arguments",{})
                    if isinstance(args,str):
                        try:args=json.loads(args or "{}")
                        except json.JSONDecodeError:args={}
                    self.event({"type":"tool_start","name":name,"arguments":args})
                    if name.startswith("mcp__") and self.mcp_call:
                        if not self._approval(name,args): out="Denied by user."
                        else: out=self.mcp_call(name,args)
                    else: out=self.state["registry"].run(name,args)
                    self.event({"type":"tool_done","name":name,"result":str(out)[:12000]})
                    msgs.append({"role":"tool","tool_call_id":call.get("id",""),"content":out})
            raise RuntimeError("Reached the per-turn 32-step tool-call limit")
        except RequestCancelled:self.event({"type":"cancelled","elapsed":time.monotonic()-start})
        except Exception as exc:self.event({"type":"error","error":f"{type(exc).__name__}: {exc}"})
        return msgs




