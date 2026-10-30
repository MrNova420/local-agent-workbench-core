from __future__ import annotations
import json, os, threading, time, tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path
from desktop_engine import AgentEngine, OllamaRuntime, MODEL, CONTEXT_MAX
from sessions import SessionStore
from telemetry import Telemetry
from ui_events import EventBus
from project_knowledge import ProjectKnowledge
from mcp_support import MCPManager

APP=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'LocalAgentDesk'
SESSIONS=SessionStore(APP/'sessions')
BG='#0B1020'; PANEL='#111A2E'; PANEL2='#17233B'; LINE='#263552'; TEXT='#EAF2FF'; MUTED='#93A7C3'; CYAN='#55D6E8'; BLUE='#7688FF'; GREEN='#6FE0B4'; AMBER='#F3C879'; RED='#FF7789'

class LocalAgentApp(tk.Tk):
 def __init__(self):
  super().__init__();self.title('Local Agent Desk');self.geometry('1440x920');self.minsize(1120,720);self.configure(bg=BG)
  self.option_add('*Font','{Segoe UI} 10');self.events=EventBus();self.runtime=OllamaRuntime();self.telemetry=Telemetry();self.engine=None;self.state=None;self.session_state=None;self.busy=False;self.started=None;self.pending_approval=None;self.approval_condition=threading.Condition();self.workspace=Path.home();self.sessions=SESSIONS.list();self.mcp_status='Not connected';self.mcp=MCPManager(APP/'mcp.json');self.mcp.load();self.mcp_tools=[];self.displayed_context=CONTEXT_MAX
  self._style();self._layout();self.after(100,self._poll);self.after(1200,self._refresh_runtime);self.after(900,self._telemetry)
 def _style(self):
  s=ttk.Style(self);s.theme_use('clam');s.configure('TCombobox',fieldbackground=PANEL2,background=PANEL2,foreground=TEXT,arrowcolor=CYAN,bordercolor=LINE,lightcolor=LINE,darkcolor=LINE);s.configure('Vertical.TScrollbar',background=PANEL2,troughcolor=BG,arrowcolor=MUTED)
 def _layout(self):
  self.columnconfigure(1,weight=1);self.rowconfigure(1,weight=1)
  header=tk.Frame(self,bg=BG,height=74);header.grid(row=0,column=0,columnspan=2,sticky='ew',padx=24,pady=(16,12));header.columnconfigure(1,weight=1)
  logo=tk.Canvas(header,width=40,height=40,bg=BG,highlightthickness=0);logo.grid(row=0,column=0,rowspan=2,sticky='w');logo.create_oval(3,3,37,37,fill='#162740',outline=CYAN,width=2);logo.create_arc(9,9,31,31,start=25,extent=260,style='arc',outline=BLUE,width=3);logo.create_oval(17,17,23,23,fill=CYAN,outline='')
  tk.Label(header,text='LOCAL AGENT DESK',bg=BG,fg=TEXT,font=('Segoe UI',15)).grid(row=0,column=1,sticky='w',padx=12,pady=(2,0));tk.Label(header,text='Private project development · Ollama runtime',bg=BG,fg=MUTED,font=('Segoe UI',9)).grid(row=1,column=1,sticky='w',padx=12)
  self.runtime_badge=tk.Label(header,text='●  Ollama checking',bg='#142236',fg=AMBER,padx=14,pady=8,font=('Segoe UI',9));self.runtime_badge.grid(row=0,column=2,rowspan=2,sticky='e')
  side=tk.Frame(self,bg=PANEL,highlightbackground=LINE,highlightthickness=1,width=310);side.grid(row=1,column=0,sticky='nsew',padx=(24,12),pady=(0,16));side.grid_propagate(False);side.columnconfigure(0,weight=1)
  self._label(side,'WORKSPACE',0);self.path_label=tk.Label(side,text='No project selected',bg=PANEL,fg=MUTED,wraplength=260,justify='left');self.path_label.grid(row=1,column=0,sticky='ew',padx=18,pady=(8,8));self._button(side,'Choose project folder',self._choose_workspace,2)
  self._label(side,'MODEL',3);self.model_var=tk.StringVar(value=MODEL);self.model_combo=ttk.Combobox(side,textvariable=self.model_var,state='readonly');self.model_combo.grid(row=4,column=0,sticky='ew',padx=18,pady=(8,6));self.model_combo.bind('<<ComboboxSelected>>',lambda e:self._model_changed())
  self.model_note=tk.Label(side,text='Reasoning: model default · Context: 262,144',bg=PANEL,fg=MUTED,wraplength=260,justify='left');self.model_note.grid(row=5,column=0,sticky='w',padx=18,pady=(0,8))
  self._label(side,'RUN PROFILE',6);row=tk.Frame(side,bg=PANEL);row.grid(row=7,column=0,sticky='ew',padx=18,pady=(8,4));row.columnconfigure(1,weight=1);tk.Label(row,text='Context',bg=PANEL,fg=MUTED).grid(row=0,column=0,sticky='w');self.context_var=tk.StringVar(value='262144');self.context_entry=tk.Entry(row,textvariable=self.context_var,bg=PANEL2,fg=TEXT,insertbackground=TEXT,relief='flat',highlightthickness=1,highlightbackground=LINE,width=11);self.context_entry.grid(row=0,column=1,sticky='e',ipady=6)
  row2=tk.Frame(side,bg=PANEL);row2.grid(row=8,column=0,sticky='ew',padx=18,pady=4);row2.columnconfigure(1,weight=1);tk.Label(row2,text='Output tokens',bg=PANEL,fg=MUTED).grid(row=0,column=0,sticky='w');self.tokens_var=tk.StringVar(value='4096');tk.Entry(row2,textvariable=self.tokens_var,bg=PANEL2,fg=TEXT,insertbackground=TEXT,relief='flat',highlightthickness=1,highlightbackground=LINE,width=11).grid(row=0,column=1,sticky='e',ipady=6)
  row3=tk.Frame(side,bg=PANEL);row3.grid(row=9,column=0,sticky='ew',padx=18,pady=4);tk.Label(row3,text='Access',bg=PANEL,fg=MUTED).pack(side='left');self.access_var=tk.StringVar(value='ask');ttk.Combobox(row3,textvariable=self.access_var,values=['ask','edit','plan'],state='readonly',width=10).pack(side='right')
  row4=tk.Frame(side,bg=PANEL);row4.grid(row=10,column=0,sticky='ew',padx=18,pady=4);tk.Label(row4,text='Approval',bg=PANEL,fg=MUTED).pack(side='left');self.approval_var=tk.StringVar(value='per-action');ttk.Combobox(row4,textvariable=self.approval_var,values=['per-action','per-task'],state='readonly',width=12).pack(side='right')
  self._label(side,'LIVE RESOURCES',11);self.resource_label=tk.Label(side,text='Idle · model will load only when you send a task',bg=PANEL,fg=MUTED,justify='left',wraplength=270);self.resource_label.grid(row=12,column=0,sticky='ew',padx=18,pady=(8,8))
  self._label(side,'SESSIONS',13);self.session_var=tk.StringVar();self.session_combo=ttk.Combobox(side,textvariable=self.session_var,state='readonly');self.session_combo.grid(row=14,column=0,sticky='ew',padx=18,pady=(8,5));self._button(side,'Resume selected session',self._resume_session,15);self._button(side,'New session',self._new_session,16)
  self._label(side,'LOCAL MCP',17);self.mcp_label=tk.Label(side,text='Local stdio servers · not connected',bg=PANEL,fg=MUTED);self.mcp_label.grid(row=18,column=0,sticky='w',padx=18,pady=(8,4));self._button(side,'Configure MCP servers',self._configure_mcp,19)
  main=tk.Frame(self,bg=BG);main.grid(row=1,column=1,sticky='nsew',padx=(0,24),pady=(0,16));main.rowconfigure(1,weight=1);main.columnconfigure(0,weight=1)
  toolbar=tk.Frame(main,bg=PANEL,highlightbackground=LINE,highlightthickness=1);toolbar.grid(row=0,column=0,sticky='ew',pady=(0,12));toolbar.columnconfigure(2,weight=1)
  tk.Label(toolbar,text='  SESSION',bg=PANEL,fg=MUTED,font=('Segoe UI',9)).grid(row=0,column=0,sticky='w',padx=10,pady=12);self.session_title=tk.Label(toolbar,text='Ready for a project',bg=PANEL,fg=TEXT,font=('Segoe UI',10));self.session_title.grid(row=0,column=1,sticky='w',padx=6);self.status=tk.Label(toolbar,text='● READY',bg=PANEL,fg=GREEN,font=('Segoe UI',9));self.status.grid(row=0,column=3,sticky='e',padx=18)
  feed=tk.Frame(main,bg=PANEL,highlightbackground=LINE,highlightthickness=1);feed.grid(row=1,column=0,sticky='nsew');feed.rowconfigure(0,weight=1);feed.columnconfigure(0,weight=1)
  self.chat=tk.Text(feed,bg='#0E1728',fg=TEXT,insertbackground=TEXT,relief='flat',wrap='word',padx=24,pady=22,spacing1=5,spacing3=8,state='disabled',font=('Segoe UI',10));self.chat.grid(row=0,column=0,sticky='nsew',padx=1,pady=1);scroll=ttk.Scrollbar(feed,command=self.chat.yview);scroll.grid(row=0,column=1,sticky='ns');self.chat.configure(yscrollcommand=scroll.set)
  self.chat.tag_configure('user',foreground=CYAN,font=('Segoe UI',10));self.chat.tag_configure('assistant',foreground=BLUE,font=('Segoe UI',10));self.chat.tag_configure('tool',foreground=AMBER,font=('Segoe UI',9));self.chat.tag_configure('error',foreground=RED);self.chat.tag_configure('body',foreground=TEXT);self.chat.tag_configure('muted',foreground=MUTED)
  composer=tk.Frame(main,bg=PANEL,highlightbackground=LINE,highlightthickness=1);composer.grid(row=2,column=0,sticky='ew',pady=(12,0));composer.columnconfigure(0,weight=1);self.prompt=tk.Text(composer,height=4,bg=PANEL2,fg=TEXT,insertbackground=TEXT,relief='flat',wrap='word',padx=14,pady=12,font=('Segoe UI',10));self.prompt.grid(row=0,column=0,sticky='ew',padx=12,pady=12);self.prompt.insert('1.0','Describe the project task. Add a project folder to begin.')
  actions=tk.Frame(composer,bg=PANEL);actions.grid(row=1,column=0,sticky='ew',padx=12,pady=(0,12));tk.Label(actions,text='Enter to send · Shift+Enter for a new line',bg=PANEL,fg=MUTED,font=('Segoe UI',9)).pack(side='left');self.cancel_btn=tk.Button(actions,text='Cancel',command=self._cancel,bg='#34202F',fg=RED,relief='flat',padx=16,pady=8,state='disabled');self.cancel_btn.pack(side='right',padx=(8,0));self.send_btn=tk.Button(actions,text='Start task  ↗',command=self._send,bg='#1B5970',fg='#EAFBFF',relief='flat',padx=18,pady=8,font=('Segoe UI',9));self.send_btn.pack(side='right');self.prompt.bind('<Return>',self._enter)
  self._append('assistant','Local Agent Desk','Choose a project folder and describe what you want built. It will use the exact selected Ollama model, keep its reasoning behavior at the model default, and ask before each edit or command. Models are not loaded when the app opens.')
 def _label(self,parent,text,row):tk.Label(parent,text=text,bg=PANEL,fg=CYAN,font=('Segoe UI',8)).grid(row=row,column=0,sticky='w',padx=18,pady=(16,0))
 def _button(self,parent,text,command,row):
  b=tk.Button(parent,text=text,command=command,bg=PANEL2,fg=TEXT,activebackground='#203351',activeforeground=TEXT,relief='flat',anchor='w',padx=12,pady=8);b.grid(row=row,column=0,sticky='ew',padx=18,pady=3);return b
 def _append(self,role,title,body):
  self.chat.configure(state='normal');self.chat.insert('end',title+'\n',role);self.chat.insert('end',body+'\n\n','body');self.chat.see('end');self.chat.configure(state='disabled')
 def _choose_workspace(self):
  selected=filedialog.askdirectory(title='Select project folder',initialdir=str(self.workspace))
  if selected:self.workspace=Path(selected);self.path_label.configure(text=str(self.workspace),fg=TEXT);self.session_title.configure(text=self.workspace.name);self._new_session()
 def _refresh_runtime(self):
  self.runtime_badge.configure(text='●  Checking Ollama',fg=AMBER)
  def work():
   try:models=self.runtime.models();self.events.emit({'type':'models','models':models})
   except Exception as exc:self.events.emit({'type':'runtime_error','error':str(exc)})
  threading.Thread(target=work,daemon=True).start()
 def _handle_models(self,models):
  self.model_combo.configure(values=models);self.runtime_badge.configure(text='●  Ollama connected',fg=GREEN)
  if self.model_var.get() in models:return
  if MODEL in models:self.model_var.set(MODEL)
  elif models:self.model_var.set(models[0])
  else:self.model_var.set('')
  self._model_changed()
 def _model_changed(self):
  model=self.model_var.get()
  if not model:return
  def work():
   try:info=self.runtime.model_info(model);details=info.get('model_info',{});ctx=max((v for k,v in details.items() if k.endswith('.context_length') and isinstance(v,int)),default=CONTEXT_MAX if model==MODEL else 8192);self.events.emit({'type':'model_info','model':model,'context':ctx})
   except Exception as exc:self.events.emit({'type':'model_info_error','error':str(exc)})
  threading.Thread(target=work,daemon=True).start()
 def _new_session(self):
  self.session_state=None;self.state=None;self.chat.configure(state='normal');self.chat.delete('1.0','end');self.chat.configure(state='disabled');self.session_title.configure(text=self.workspace.name if self.workspace else 'New session');self._append('assistant','New session','Project selected. Set the model and context, then describe the task.')
 def _refresh_sessions(self):
  rows=SESSIONS.list();self.session_rows=rows;self.session_combo.configure(values=[x['session_id'] for x in rows]);self.session_var.set(self.session_state.get('session_id','') if self.session_state else (rows[0]['session_id'] if rows else ''))
 def _resume_session(self):
  sid=self.session_var.get()
  if not sid:return
  try:
   state=SESSIONS.load(sid);self.session_state=state;self.workspace=Path(state['workspace']);self.path_label.configure(text=str(self.workspace),fg=TEXT);self.model_var.set(state['model']);self.context_var.set(str(state['context']));self.access_var.set(state.get('access','ask'));self.approval_var.set(state.get('approval_mode','per-action'));self.session_title.configure(text=sid);self._append('assistant','Session resumed',f"{sid} · {len(state.get('messages',[]))} saved messages")
   for msg in state.get('messages',[]):
    if msg.get('role') in {'user','assistant'} and msg.get('content'):self._append(msg['role'],'You' if msg['role']=='user' else 'Agent',msg['content'])
  except Exception as exc:messagebox.showerror('Could not resume session',str(exc))
 def _save(self):
  if self.session_state:SESSIONS.save(self.session_state)
 def _send(self):
  if self.busy:return
  if not self.workspace.is_dir():messagebox.showinfo('Choose a project','Select the project folder first.');return
  prompt=self.prompt.get('1.0','end').strip()
  if not prompt or prompt.startswith('Describe the project task'):return
  try:context=int(self.context_var.get());tokens=int(self.tokens_var.get());
  except ValueError:messagebox.showerror('Invalid settings','Context and output tokens must be whole numbers.');return
  if context<1024 or tokens<1:messagebox.showerror('Invalid settings','Context must be at least 1,024 and output tokens must be positive.');return
  model=self.model_var.get().strip()
  if not model:messagebox.showerror('No model selected','No installed Ollama models were found. Install your model with Ollama, then refresh/reopen the app.');return
  if not self.session_state:self.session_state=SESSIONS.create(str(self.workspace),model,context)
  state=self.session_state;state.update(workspace=str(self.workspace),model=model,context=context,access=self.access_var.get(),approval_mode=self.approval_var.get(),max_tokens=tokens,temperature=0.2)
  self._save();self._memory_warning_shown=False;self._append('user','You',prompt);self.prompt.delete('1.0','end');self._set_busy(True);self.started=time.monotonic();self.events.emit({'type':'phase','phase':'connecting'})
  def approve(name,args):
   condition=threading.Condition();result={'value':False,'done':False};self.events.emit({'type':'approval_request','name':name,'arguments':args,'condition':condition,'result':result})
   with condition:
    while not result['done'] and not self.stop_event.is_set():condition.wait(.25)
   return result['value']
  def run():
   try:
    if self.mcp.servers and not self.mcp_tools:
     self.mcp_tools=self.mcp.run(self.mcp.discover())
     self.events.emit({'type':'mcp_status','text':f'{len(self.mcp_tools)} local tools connected'})
   except Exception as exc:self.events.emit({'type':'mcp_status','text':f'MCP unavailable: {exc}'})
   knowledge=ProjectKnowledge(self.workspace)
   engine=AgentEngine(self.workspace,model,context,self.access_var.get(),self.approval_var.get(),self.events.emit,approve,runtime_options=state.get('runtime_options',{}),max_tokens=tokens,temperature=float(state.get('temperature',0.2)),project_instructions=knowledge.instructions(),mcp_tools=self.mcp_tools,mcp_call=lambda name,args:self.mcp.run(self.mcp.call(name,args)))
   engine.state['messages']=state['messages'];self.engine=engine;new_messages=engine.run(prompt,state['messages']);state['messages']=new_messages;state.setdefault('turns',[]).append({'prompt':prompt,'at':time.time()});SESSIONS.save(state);self.events.emit({'type':'session_saved'});self.events.emit({'type':'turn_result','messages':new_messages})
  self.stop_event=threading.Event();threading.Thread(target=run,name='agent-task',daemon=True).start()
 def _set_busy(self,value):self.busy=value;self.send_btn.configure(state='disabled' if value else 'normal');self.cancel_btn.configure(state='normal' if value else 'disabled');self.status.configure(text='● RUNNING' if value else '● READY',fg=AMBER if value else GREEN)
 def _cancel(self):
  if self.engine:self.engine.cancel()
  if hasattr(self,'stop_event'):self.stop_event.set()
  if self.pending_approval:
   cond,result=self.pending_approval
   with cond:result.update(value=False,done=True);cond.notify_all()
  self._append('tool','Cancel requested','Interrupting this request only. Ollama and the loaded model are left alone.')
 def _enter(self,event):
  if event.state & 0x0001:return None
  self._send();return 'break'
 def _poll(self):
  for e in self.events.drain():
   kind=e.get('type')
   if kind=='models':self._handle_models(e['models'])
   elif kind=='runtime_error':self.runtime_badge.configure(text='●  Ollama unavailable',fg=RED)
   elif kind=='model_info':self.displayed_context=e['context'];self.model_note.configure(text=f"Reasoning: model default · advertised context: {e['context']:,} tokens · allocation shown live after send");self.context_var.set(str(e['context']))
   elif kind=='model_info_error':self.model_note.configure(text='Model metadata unavailable; context remains user-controlled.')
   elif kind=='text':
    self.chat.configure(state='normal');self.chat.insert('end',e['text'],'body');self.chat.see('end');self.chat.configure(state='disabled')
   elif kind=='phase':self.status.configure(text=('● '+e['phase'].upper()+f" · {int(e.get('elapsed',0))}s · ctx {e.get('context',self.context_var.get())}") if e['phase']=='waiting' else '● '+e['phase'].upper(),fg=AMBER)
   elif kind=='tool_start':self._append('tool','Tool: '+e['name'],json.dumps(e.get('arguments'),ensure_ascii=False))
   elif kind=='tool_done':self._append('tool','Result',str(e.get('result',''))[:3000])
   elif kind=='approval_request':self._approval_dialog(e)
   elif kind=='complete':self._append('assistant','Turn complete',f"Elapsed {e['elapsed']:.1f}s");self._set_busy(False);self._save();self._refresh_sessions()
   elif kind=='cancelled':self._append('tool','Cancelled',f"Request interrupted after {e['elapsed']:.1f}s. Ollama was left running.");self._set_busy(False);self._refresh_sessions()
   elif kind=='error':self._append('error','Error',e['error']);self._set_busy(False)
   elif kind=='session_saved':self._refresh_sessions()
   elif kind=='turn_result':
    msgs=e['messages'];last=next((m.get('content') for m in reversed(msgs) if m.get('role')=='assistant' and m.get('content')),None)
    if last and not self.chat.get('end-2l','end-1l').strip().endswith(str(last)[-80:]):self._append('assistant','Agent',str(last))
   elif kind=='telemetry':self._show_telemetry(e['data'])
   elif kind=='context_limit':self._append('error','Context budget',e['error']);self._set_busy(False)
   elif kind=='mcp_status':self.mcp_label.configure(text=e['text'])
   elif kind=='load_warn':self._append('tool','Context / memory notice',e['text'])
   elif kind=='plan':self._append('tool','Task plan',json.dumps(e['items'],indent=2,ensure_ascii=False))
  self.after(100,self._poll)
 def _approval_dialog(self,e):
  name,args=e['name'],e.get('arguments',{});condition=e['condition'];result=e['result'];self.pending_approval=(condition,result)
  if name=='run_command':detail='Command\n'+json.dumps(args.get('argv',[]),ensure_ascii=False)+'\n\nCommands execute with your Windows account permissions.'
  elif name=='apply_patch':detail='Patch preview\n'+str(args.get('patch',''))[:5000]
  else:detail=json.dumps(args,indent=2,ensure_ascii=False)[:3000]
  ok=messagebox.askyesno('Approve '+name,detail+'\n\nApprove this action?',icon='warning')
  with condition:result.update(value=ok,done=True);condition.notify_all()
  self.pending_approval=None
 def _telemetry(self):
  def work():
   try:
    loaded=self.runtime.loaded();snap=self.telemetry.snapshot(loaded);self.events.emit({'type':'telemetry','data':snap})
   except Exception:self.events.emit({'type':'telemetry','data':self.telemetry.snapshot()})
  threading.Thread(target=work,daemon=True).start()
  self.after(1800,self._telemetry)
 def _show_telemetry(self,d):
  parts=[]
  if d.get('cpu_percent') is not None:parts.append(f"CPU {d['cpu_percent']:.0f}%")
  total=d.get('ram_total_bytes');avail=d.get('ram_available_bytes')
  if total and avail:parts.append(f"RAM {(total-avail)/1024**3:.1f}/{total/1024**3:.1f} GB")
  if d.get('gpu'):parts.append('GPU '+d['gpu'].splitlines()[0])
  if d.get('loaded'):
   m=d['loaded'][0];parts.append(f"Ollama allocated context {m.get('context_length','?'):,}" if isinstance(m.get('context_length'),int) else 'Model loaded')
   requested=self.session_state.get('context',self.displayed_context) if getattr(self,'session_state',None) else int(self.context_var.get() or self.displayed_context)
   actual=m.get('context_length')
   if isinstance(actual,int) and actual<requested:parts.append(f"WARNING: Ollama allocated {actual:,}, request was {requested:,}. Check runtime logs/memory; app did not lower setting.")
  if self.busy and self.started:parts.append(f"Elapsed {int(time.monotonic()-self.started)}s · {self.status.cget('text').split(' · ')[0]}")
  self.resource_label.configure(text='\n'.join(parts) or 'Resource telemetry unavailable')
  if total and avail and avail/total<.10 and not getattr(self,'_memory_warning_shown',False):self._memory_warning_shown=True;self._append('tool','Memory pressure',f"Only {avail/1024**3:.1f} GiB RAM remains available. Ollama decides whether the requested context can be allocated; the app will not silently lower it.")
 def _configure_mcp(self):
  path=APP/'mcp.json';path.parent.mkdir(parents=True,exist_ok=True)
  if not path.exists():path.write_text(json.dumps({'mcpServers':{}},indent=2),encoding='utf8')
  try:os.startfile(path)
  except Exception:messagebox.showinfo('MCP configuration',f'Edit local stdio server configuration at:\n{path}\n\nInstall optional dependencies with pip install -r requirements-mcp.txt, then restart the app.')
 def on_close(self):
  if self.engine and self.busy:self.engine.cancel()
  self._save()
  try:self.mcp.run(self.mcp.close())
  except Exception:pass
  self.destroy()

if __name__=='__main__':
 app=LocalAgentApp();app.protocol('WM_DELETE_WINDOW',app.on_close);app.mainloop()


