from __future__ import annotations
import json, os, threading, time, tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from desktop_engine import AgentEngine, OllamaRuntime, MODEL, CONTEXT_MAX
from sessions import SessionStore
from telemetry import Telemetry
from ui_events import EventBus
from project_knowledge import ProjectKnowledge
from mcp_support import MCPManager
from turn_context import TurnContext, parse_run_settings, should_apply_advertised_context, validate_selected_workspace
from project_view import preview_files
from disposable_workspace import apply_reviewed_changes, capture_manifest, create_disposable_workspace, managed_copy_path, review_changes

APP=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'LocalAgentDesk'
SESSIONS=SessionStore(APP/'sessions')
BG='#111316'; PANEL='#191C20'; PANEL2='#22272C'; LINE='#343A40'; TEXT='#F1EEE8'; MUTED='#9A9F9F'; CYAN='#B8D98A'; BLUE='#C5B1F2'; GREEN='#B8D98A'; AMBER='#E5BA72'; RED='#E87D78'

class LocalAgentApp(tk.Tk):
 def __init__(self):
  super().__init__();self.title('Local Agent Desk');self.geometry('1580x960');self.minsize(1120,720);self.configure(bg=BG)
  self.option_add('*Font','{Segoe UI} 10');self.events=EventBus();self.runtime=OllamaRuntime();self.telemetry=Telemetry();self.engine=None;self.state=None;self.session_state=None;self.busy=False;self.copy_preparing=False;self.started=None;self.pending_approval=None;self.approval_condition=threading.Condition();self.source_workspace=Path.home();self.workspace=self.source_workspace;self.project_selected=False;self.sessions=SESSIONS.list();self.mcp_status='Not connected';self.mcp=MCPManager(APP/'mcp.json');self.mcp.load();self.mcp_tools=[];self.displayed_context=CONTEXT_MAX
  self.context_var=tk.StringVar(value=str(CONTEXT_MAX));self.context_customized=False;self.tokens_var=tk.StringVar(value='4096');self.temperature_var=tk.StringVar(value='0.2');self.access_var=tk.StringVar(value='ask');self.approval_var=tk.StringVar(value='per-action');self.context_entry=self.tokens_entry=self.temperature_entry=self.access_combo=self.approval_combo=None
  self._style();self._layout();self._refresh_sessions();self.after(100,self._poll);self.after(1200,self._refresh_runtime);self.after(900,self._telemetry)
 def _style(self):
  s=ttk.Style(self);s.theme_use('clam');s.configure('TCombobox',fieldbackground=PANEL2,background=PANEL2,foreground=TEXT,arrowcolor=CYAN,bordercolor=LINE,lightcolor=LINE,darkcolor=LINE);s.configure('Vertical.TScrollbar',background=PANEL2,troughcolor=BG,arrowcolor=MUTED);s.configure('TNotebook',background=PANEL,tabmargins=(4,5,4,0),borderwidth=0);s.configure('TNotebook.Tab',background=PANEL2,foreground=MUTED,padding=(12,8),borderwidth=0);s.map('TNotebook.Tab',background=[('selected',PANEL)],foreground=[('selected',TEXT)])
 def _layout(self):
  self.columnconfigure(1,weight=1);self.rowconfigure(1,weight=1);self.columnconfigure(0,minsize=250,weight=0);self.columnconfigure(2,minsize=285,weight=0)
  header=tk.Frame(self,bg=BG,height=70);header.grid(row=0,column=0,columnspan=3,sticky='ew',padx=22,pady=(14,10));header.columnconfigure(1,weight=1)
  mark=tk.Canvas(header,width=38,height=38,bg=BG,highlightthickness=0);mark.grid(row=0,column=0,sticky='w');mark.create_rectangle(3,3,35,35,fill='#263028',outline=CYAN,width=1);mark.create_line(11,25,18,11,25,25,fill=CYAN,width=2);mark.create_line(13,21,23,21,fill=BLUE,width=2)
  tk.Label(header,text='LOCAL AGENT',bg=BG,fg=TEXT,font=('Segoe UI Semibold',13)).grid(row=0,column=1,sticky='w',padx=12);tk.Label(header,text='PRIVATE DEVELOPMENT WORKSPACE',bg=BG,fg=MUTED,font=('Segoe UI',8)).grid(row=0,column=1,sticky='w',padx=12,pady=(31,0))
  self.runtime_badge=tk.Label(header,text='●  Checking Ollama',bg=PANEL2,fg=AMBER,padx=13,pady=8,font=('Segoe UI',9));self.runtime_badge.grid(row=0,column=2,sticky='e',padx=(8,12))
  self.settings_btn=tk.Button(header,text='Preferences',command=self._settings_dialog,bg=PANEL,fg=TEXT,activebackground=PANEL2,relief='flat',padx=14,pady=8);self.settings_btn.grid(row=0,column=3,sticky='e')
  side=tk.Frame(self,bg=PANEL,width=270);side.grid(row=1,column=0,sticky='nsew',padx=(22,10),pady=(0,18));side.grid_propagate(False);side.columnconfigure(0,weight=1);side.rowconfigure(4,weight=1)
  tk.Label(side,text='PROJECT',bg=PANEL,fg=MUTED,font=('Segoe UI Semibold',8)).grid(row=0,column=0,sticky='w',padx=16,pady=(17,7))
  self.workspace_btn=tk.Button(side,text='＋  Open project folder',command=self._choose_workspace,bg='#28312A',fg=CYAN,activebackground='#313D33',relief='flat',anchor='w',padx=12,pady=10);self.workspace_btn.grid(row=1,column=0,sticky='ew',padx=12)
  self.path_label=tk.Label(side,text='No project open',bg=PANEL,fg=MUTED,wraplength=225,justify='left',anchor='w',font=('Segoe UI',9));self.path_label.grid(row=2,column=0,sticky='ew',padx=16,pady=(8,14))
  tk.Label(side,text='PROJECT FILES',bg=PANEL,fg=MUTED,font=('Segoe UI Semibold',8)).grid(row=3,column=0,sticky='w',padx=16,pady=(0,5))
  files_box=tk.Frame(side,bg=PANEL2);files_box.grid(row=4,column=0,sticky='nsew',padx=10);files_box.rowconfigure(0,weight=1);files_box.columnconfigure(0,weight=1)
  self.file_list=tk.Listbox(files_box,bg=PANEL2,fg=TEXT,selectbackground='#394538',selectforeground=TEXT,relief='flat',highlightthickness=0,font=('Cascadia Code',9),activestyle='none');self.file_list.grid(row=0,column=0,sticky='nsew');files_scroll=ttk.Scrollbar(files_box,command=self.file_list.yview);files_scroll.grid(row=0,column=1,sticky='ns');self.file_list.configure(yscrollcommand=files_scroll.set)
  tk.Label(side,text='RECENT SESSIONS',bg=PANEL,fg=MUTED,font=('Segoe UI Semibold',8)).grid(row=6,column=0,sticky='w',padx=16,pady=(18,6))
  self.session_var=tk.StringVar();self.session_combo=ttk.Combobox(side,textvariable=self.session_var,state='readonly');self.session_combo.grid(row=7,column=0,sticky='ew',padx=12,pady=(0,6));self.resume_btn=tk.Button(side,text='Resume selected',command=self._resume_session,bg=PANEL2,fg=TEXT,activebackground='#30363A',relief='flat',padx=10,pady=8);self.resume_btn.grid(row=8,column=0,sticky='ew',padx=12,pady=3);self.new_session_btn=tk.Button(side,text='＋  New conversation',command=self._new_session,bg=PANEL,fg=CYAN,activebackground=PANEL2,relief='flat',anchor='w',padx=10,pady=8);self.new_session_btn.grid(row=9,column=0,sticky='ew',padx=12,pady=(3,12))
  main=tk.Frame(self,bg=BG);main.grid(row=1,column=1,sticky='nsew',padx=10,pady=(0,18));main.rowconfigure(1,weight=1);main.columnconfigure(0,weight=1)
  toolbar=tk.Frame(main,bg=PANEL);toolbar.grid(row=0,column=0,sticky='ew',pady=(0,9));toolbar.columnconfigure(1,weight=1)
  tk.Label(toolbar,text='CONVERSATION',bg=PANEL,fg=MUTED,font=('Segoe UI Semibold',8)).grid(row=0,column=0,sticky='w',padx=(14,8),pady=(9,2));self.session_title=tk.Label(toolbar,text='Ready for a project',bg=PANEL,fg=TEXT,font=('Segoe UI Semibold',11));self.session_title.grid(row=1,column=0,sticky='w',padx=14,pady=(0,10))
  self.model_var=tk.StringVar(value=MODEL);self.model_combo=ttk.Combobox(toolbar,textvariable=self.model_var,state='readonly',width=42);self.model_combo.grid(row=0,column=1,rowspan=2,sticky='e',padx=8,pady=9);self.model_combo.bind('<<ComboboxSelected>>',lambda e:self._model_changed())
  self.status=tk.Label(toolbar,text='● READY',bg=PANEL2,fg=GREEN,font=('Segoe UI Semibold',8),padx=10,pady=7);self.status.grid(row=0,column=2,rowspan=2,sticky='e',padx=(7,14))
  feed=tk.Frame(main,bg=PANEL);feed.grid(row=1,column=0,sticky='nsew');feed.rowconfigure(0,weight=1);feed.columnconfigure(0,weight=1)
  self.chat=tk.Text(feed,bg='#15181A',fg=TEXT,insertbackground=TEXT,relief='flat',wrap='word',padx=30,pady=26,spacing1=7,spacing3=10,state='disabled',font=('Segoe UI',10));self.chat.grid(row=0,column=0,sticky='nsew',padx=1,pady=1);scroll=ttk.Scrollbar(feed,command=self.chat.yview);scroll.grid(row=0,column=1,sticky='ns');self.chat.configure(yscrollcommand=scroll.set)
  self.chat.tag_configure('user',foreground=CYAN,font=('Segoe UI Semibold',10));self.chat.tag_configure('assistant',foreground=BLUE,font=('Segoe UI Semibold',10));self.chat.tag_configure('tool',foreground=AMBER,font=('Segoe UI',9));self.chat.tag_configure('error',foreground=RED);self.chat.tag_configure('body',foreground=TEXT);self.chat.tag_configure('muted',foreground=MUTED)
  composer=tk.Frame(main,bg=PANEL);composer.grid(row=2,column=0,sticky='ew',pady=(9,0));composer.columnconfigure(0,weight=1);self.prompt=tk.Text(composer,height=4,bg=PANEL2,fg=TEXT,insertbackground=CYAN,relief='flat',wrap='word',padx=16,pady=14,font=('Segoe UI',10),highlightthickness=1,highlightbackground=LINE,highlightcolor=CYAN);self.prompt.grid(row=0,column=0,sticky='ew',padx=10,pady=(10,5));self.prompt.insert('1.0','Describe the project task…')
  actions=tk.Frame(composer,bg=PANEL);actions.grid(row=1,column=0,sticky='ew',padx=12,pady=(1,10));tk.Label(actions,text='Enter to send  ·  Shift + Enter for a new line',bg=PANEL,fg=MUTED,font=('Segoe UI',8)).pack(side='left');self.cancel_btn=tk.Button(actions,text='Cancel run',command=self._cancel,bg='#392B2A',fg=RED,relief='flat',padx=13,pady=8,state='disabled');self.cancel_btn.pack(side='right',padx=(8,0));self.send_btn=tk.Button(actions,text='Run task  ↗',command=self._send,bg='#B8D98A',fg='#182018',activebackground='#C7E69A',relief='flat',padx=17,pady=8,font=('Segoe UI Semibold',9));self.send_btn.pack(side='right');self.prompt.bind('<Return>',self._enter)
  inspector=tk.Frame(self,bg=PANEL,width=300);inspector.grid(row=1,column=2,sticky='nsew',padx=(0,22),pady=(0,18));inspector.grid_propagate(False);inspector.rowconfigure(1,weight=1);inspector.columnconfigure(0,weight=1)
  tk.Label(inspector,text='TASK INSPECTOR',bg=PANEL,fg=MUTED,font=('Segoe UI Semibold',8)).grid(row=0,column=0,sticky='w',padx=14,pady=(14,6));self.inspector_tabs=ttk.Notebook(inspector);self.inspector_tabs.grid(row=1,column=0,sticky='nsew',padx=8,pady=(0,8))
  self.plan_view=self._inspector_text(self.inspector_tabs,'Plan');self.activity_view=self._inspector_text(self.inspector_tabs,'Activity');self.diff_view=self._inspector_text(self.inspector_tabs,'Changes')
  diff_btn=tk.Button(inspector,text='Refresh Git changes',command=self._refresh_diff,bg=PANEL2,fg=TEXT,activebackground='#30363A',relief='flat',padx=11,pady=8);diff_btn.grid(row=2,column=0,sticky='ew',padx=10,pady=(0,5));self.diff_btn=diff_btn
  self.resource_label=tk.Label(inspector,text='Idle · model loads only when a task starts',bg=PANEL,fg=MUTED,justify='left',wraplength=255,font=('Segoe UI',8));self.resource_label.grid(row=3,column=0,sticky='ew',padx=13,pady=(3,6))
  self.mcp_label=tk.Label(inspector,text='MCP  ·  not connected',bg=PANEL,fg=MUTED,anchor='w',font=('Segoe UI',8));self.mcp_label.grid(row=4,column=0,sticky='ew',padx=13,pady=(0,4));tk.Button(inspector,text='Configure local MCP',command=self._configure_mcp,bg=PANEL,fg=CYAN,activebackground=PANEL2,relief='flat',anchor='w',padx=12,pady=7).grid(row=5,column=0,sticky='ew',padx=8,pady=(0,8));self.copy_review_btn=tk.Button(inspector,text='Review copy changes',command=self._review_copy_changes,bg='#28312A',fg=CYAN,activebackground='#313D33',relief='flat',padx=11,pady=8,state='disabled');self.copy_review_btn.grid(row=6,column=0,sticky='ew',padx=10,pady=(0,10))
  self.model_note=tk.Label(toolbar,text='Reasoning: model default · context metadata pending',bg=PANEL,fg=MUTED,font=('Segoe UI',8));self.model_note.grid(row=2,column=0,columnspan=3,sticky='w',padx=14,pady=(0,8))
  self._append('assistant','Local Agent Desk','A private coding workspace for your local model. Open a project, choose a task, and review every proposed change before it runs.')
 def _inspector_text(self,parent,title):
  frame=tk.Frame(parent,bg=PANEL);parent.add(frame,text=title);frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1);view=tk.Text(frame,bg=PANEL,fg=TEXT,insertbackground=TEXT,relief='flat',wrap='word',padx=12,pady=12,font=('Segoe UI',9),state='disabled');view.grid(row=0,column=0,sticky='nsew');scroll=ttk.Scrollbar(frame,command=view.yview);scroll.grid(row=0,column=1,sticky='ns');view.configure(yscrollcommand=scroll.set);return view
 def _button(self,parent,text,command,row):
  b=tk.Button(parent,text=text,command=command,bg=PANEL2,fg=TEXT,activebackground='#30363A',activeforeground=TEXT,relief='flat',anchor='w',padx=12,pady=8);b.grid(row=row,column=0,sticky='ew',padx=18,pady=3);return b
 def _append(self,role,title,body):
  self.chat.configure(state='normal');self.chat.insert('end',title+'\n',role);self.chat.insert('end',body+'\n\n','body');self.chat.see('end');self.chat.configure(state='disabled')
 def _view_append(self,view,text,replace=False):
  view.configure(state='normal')
  if replace:view.delete('1.0','end')
  view.insert('end',text+'\n');view.see('end');view.configure(state='disabled')
 def _settings_dialog(self):
  if getattr(self,'settings_window',None) and self.settings_window.winfo_exists():self.settings_window.lift();return
  win=tk.Toplevel(self);self.settings_window=win;win.title('Run settings');win.geometry('440x620');win.configure(bg=BG);win.transient(self);win.grab_set();win.columnconfigure(0,weight=1)
  tk.Label(win,text='RUN SETTINGS',bg=BG,fg=CYAN,font=('Segoe UI Semibold',9)).grid(row=0,column=0,sticky='w',padx=24,pady=(22,3));tk.Label(win,text='Tune the next task',bg=BG,fg=TEXT,font=('Segoe UI Semibold',17)).grid(row=1,column=0,sticky='w',padx=24,pady=(0,18))
  box=tk.Frame(win,bg=PANEL);box.grid(row=2,column=0,sticky='ew',padx=18);box.columnconfigure(0,weight=1)
  tk.Label(box,text='Context tokens',bg=PANEL,fg=MUTED).grid(row=0,column=0,sticky='w',padx=16,pady=(15,5));self.context_entry=tk.Entry(box,textvariable=self.context_var,bg=PANEL2,fg=TEXT,insertbackground=CYAN,relief='flat',highlightthickness=1,highlightbackground=LINE,width=22);self.context_entry.grid(row=1,column=0,sticky='ew',padx=16,ipady=8);self.context_entry.bind('<KeyRelease>',lambda _event:setattr(self,'context_customized',True))
  tk.Label(box,text='Maximum output tokens',bg=PANEL,fg=MUTED).grid(row=2,column=0,sticky='w',padx=16,pady=(13,5));self.tokens_entry=tk.Entry(box,textvariable=self.tokens_var,bg=PANEL2,fg=TEXT,insertbackground=CYAN,relief='flat',highlightthickness=1,highlightbackground=LINE,width=22);self.tokens_entry.grid(row=3,column=0,sticky='ew',padx=16,ipady=8)
  tk.Label(box,text='Temperature (0–2)',bg=PANEL,fg=MUTED).grid(row=4,column=0,sticky='w',padx=16,pady=(13,5));self.temperature_entry=tk.Entry(box,textvariable=self.temperature_var,bg=PANEL2,fg=TEXT,insertbackground=CYAN,relief='flat',highlightthickness=1,highlightbackground=LINE,width=22);self.temperature_entry.grid(row=5,column=0,sticky='ew',padx=16,ipady=8)
  tk.Label(box,text='File access',bg=PANEL,fg=MUTED).grid(row=6,column=0,sticky='w',padx=16,pady=(13,5));self.access_combo=ttk.Combobox(box,textvariable=self.access_var,values=['copy','plan','ask','edit'],state='readonly');self.access_combo.grid(row=7,column=0,sticky='ew',padx=16)
  tk.Label(box,text='Approval',bg=PANEL,fg=MUTED).grid(row=8,column=0,sticky='w',padx=16,pady=(13,5));self.approval_combo=ttk.Combobox(box,textvariable=self.approval_var,values=['per-action','per-task'],state='readonly');self.approval_combo.grid(row=9,column=0,sticky='ew',padx=16,pady=(0,16))
  tk.Label(win,text='Copy mode edits a disposable project copy; source changes require an explicit review and apply. Reasoning stays at the model default.',bg=BG,fg=MUTED,wraplength=380,justify='left').grid(row=3,column=0,sticky='w',padx=24,pady=14)
  actions=tk.Frame(win,bg=BG);actions.grid(row=4,column=0,sticky='ew',padx=18,pady=(8,18));tk.Button(actions,text='Configure MCP',command=self._configure_mcp,bg=PANEL2,fg=TEXT,relief='flat',padx=12,pady=9).pack(side='left');tk.Button(actions,text='Done',command=win.destroy,bg=CYAN,fg='#182018',relief='flat',padx=24,pady=9).pack(side='right')
  win.bind('<Destroy>',lambda e:self._clear_settings_widgets() if e.widget is win else None)
 def _clear_settings_widgets(self):
  self.context_entry=self.tokens_entry=self.temperature_entry=self.access_combo=self.approval_combo=None
 def _refresh_project_files(self):
  root=self.workspace
  def work():
   try:rows=preview_files(root,limit=500);self.events.emit({'type':'project_files','root':str(root),'rows':rows})
   except Exception as exc:self.events.emit({'type':'project_files','root':str(root),'error':str(exc)})
  threading.Thread(target=work,name='project-index',daemon=True).start()
 def _refresh_diff(self):
  if not self.workspace.is_dir():return
  root=self.workspace;source=self.source_workspace;session=self.session_state
  def work():
   try:
    if session and session.get('access')=='copy' and session.get('copy_manifest') is not None:
     changes=review_changes(source,root,session['copy_manifest']);rows=[]
     for item in changes:rows.append(f"{'CONFLICT · ' if item['conflict'] else ''}{item['action'].upper()} {item['path']}"+(f" · {item['reason']}" if item['reason'] else ''))
     content='\n'.join(rows) if rows else 'No reviewable text changes in the disposable copy.'
     self.events.emit({'type':'project_diff','root':str(root),'content':content});return
    import subprocess
    result=subprocess.run(['git','diff','--no-ext-diff','--'],cwd=root,capture_output=True,text=True,timeout=15,shell=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));content=(result.stdout+result.stderr).strip() or 'No unstaged Git changes.'
   except Exception as exc:content=f'Git diff unavailable: {exc}'
   self.events.emit({'type':'project_diff','root':str(root),'content':content})
  threading.Thread(target=work,name='git-diff-preview',daemon=True).start()
 def _review_copy_changes(self):
  state=self.session_state
  if self.busy or not state or state.get('access')!='copy' or not state.get('copy_manifest'):return
  source=self.source_workspace;working=self.workspace;manifest=dict(state['copy_manifest'])
  def work():
   try:changes=review_changes(source,working,manifest);self.events.emit({'type':'copy_review','root':str(working),'changes':changes})
   except Exception as exc:self.events.emit({'type':'copy_review_error','error':str(exc)})
  threading.Thread(target=work,name='copy-review',daemon=True).start()
 def _show_copy_review(self,changes):
  if not changes:
   messagebox.showinfo('Copy review','There are no reviewable text changes to apply.');return
  import difflib
  dialog=tk.Toplevel(self);dialog.title('Review disposable-copy changes');dialog.geometry('900x680');dialog.minsize(620,420);dialog.configure(bg=BG);dialog.transient(self);dialog.grab_set();dialog.columnconfigure(0,weight=1);dialog.rowconfigure(2,weight=1)
  conflicts=[item for item in changes if item['conflict']]
  summary=f'{len(changes)} change(s) · {len(conflicts)} source conflict(s). '+('Resolve conflicts first; nothing will be applied.' if conflicts else 'Only after you click Apply will the source project be changed.')
  tk.Label(dialog,text='SOURCE CHANGE REVIEW',bg=BG,fg=AMBER,font=('Segoe UI Semibold',9)).grid(row=0,column=0,sticky='w',padx=18,pady=(16,4));tk.Label(dialog,text=summary,bg=BG,fg=TEXT,wraplength=840,justify='left').grid(row=1,column=0,sticky='ew',padx=18,pady=(0,10))
  frame=tk.Frame(dialog,bg=PANEL);frame.grid(row=2,column=0,sticky='nsew',padx=16);frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1);preview=tk.Text(frame,bg=PANEL2,fg=TEXT,relief='flat',wrap='none',font=('Cascadia Code',9),padx=12,pady=10);preview.grid(row=0,column=0,sticky='nsew');ys=ttk.Scrollbar(frame,command=preview.yview);ys.grid(row=0,column=1,sticky='ns');xs=ttk.Scrollbar(frame,command=preview.xview,orient='horizontal');xs.grid(row=1,column=0,sticky='ew');preview.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
  chunks=[]
  for item in changes:
   chunks.append(f"{'CONFLICT · ' if item['conflict'] else ''}{item['action'].upper()} {item['path']}\n{item['reason']}")
   old=(item.get('source_content') or '').splitlines(keepends=True)
   new=(item['content'] or '').splitlines(keepends=True) if item['action']=='write' else []
   chunks.extend(difflib.unified_diff(old,new,fromfile='source/'+item['path'],tofile='copy/'+item['path']))
   chunks.append('')
  preview.insert('1.0','\n'.join(chunks));preview.configure(state='disabled')
  actions=tk.Frame(dialog,bg=BG);actions.grid(row=3,column=0,sticky='ew',padx=16,pady=14);actions.columnconfigure(0,weight=1)
  tk.Button(actions,text='Cancel',command=dialog.destroy,bg=PANEL2,fg=TEXT,relief='flat',padx=16,pady=9).grid(row=0,column=1,padx=(0,8));apply_btn=tk.Button(actions,text='Apply reviewed changes',command=lambda:self._apply_copy_changes(changes,dialog),bg=CYAN,fg='#182018',relief='flat',padx=18,pady=9,state='disabled' if conflicts else 'normal');apply_btn.grid(row=0,column=2)
 def _apply_copy_changes(self,changes,dialog):
  dialog.destroy();self._set_busy(True);self.cancel_btn.configure(state='disabled');self.status.configure(text='● APPLYING REVIEWED COPY',fg=AMBER)
  source=self.source_workspace;working=self.workspace
  def work():
   try:applied=apply_reviewed_changes(source,changes);self.events.emit({'type':'copy_applied','paths':applied,'root':str(working)})
   except Exception as exc:self.events.emit({'type':'copy_apply_error','error':str(exc)})
  threading.Thread(target=work,name='copy-apply',daemon=True).start()
 def _choose_workspace(self):
  if self.busy:return
  selected=filedialog.askdirectory(title='Select project folder',initialdir=str(self.workspace))
  if selected:self.source_workspace=Path(selected).resolve();self.workspace=self.source_workspace;self.project_selected=True;self.path_label.configure(text=str(self.workspace),fg=TEXT);self.session_title.configure(text=self.workspace.name);self.copy_review_btn.configure(state='disabled');self._new_session();self._refresh_project_files()
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
  if self.busy:return
  self.session_state=None;self.state=None;self.workspace=self.source_workspace;self.path_label.configure(text=str(self.source_workspace),fg=TEXT);self.copy_review_btn.configure(state='disabled');self.session_var.set('');self.chat.configure(state='normal');self.chat.delete('1.0','end');self.chat.configure(state='disabled');self.session_title.configure(text=self.workspace.name if self.workspace else 'New session');self._append('assistant','New session','Project selected. Set the model and context, then describe the task.')
 def _refresh_sessions(self):
  rows=SESSIONS.list();self.session_rows=rows;self.session_choices={}
  for row in rows:
   sid=row['session_id'];project=Path(row.get('workspace') or '').name or 'Missing project';label=f"{sid}  ·  {project}";self.session_choices[label]=sid
  self.session_combo.configure(values=list(self.session_choices));active=self.session_state.get('session_id') if self.session_state else None;self.session_var.set(next((label for label,sid in self.session_choices.items() if sid==active),'') if active else '')
 def _resume_session(self):
  if self.busy:return
  sid=self.session_choices.get(self.session_var.get(),self.session_var.get())
  if not sid:return
  try:
   state=SESSIONS.load(sid);source=Path(state['workspace']).resolve(strict=True)
   if not source.is_dir():raise ValueError('The saved project folder is no longer available.')
   working=source
   if state.get('access')=='copy':
    expected=managed_copy_path(APP/'workspaces',sid,require_exists=True);saved=Path(state.get('working_workspace','')).resolve(strict=True)
    if saved!=expected or not saved.is_dir():raise ValueError('This disposable session copy is missing or outside the managed workspace. Start a new session to create a fresh copy.')
    if not isinstance(state.get('copy_manifest'),dict):raise ValueError('This disposable session has no valid source snapshot; refusing to resume it.')
    working=saved
   if self.project_selected and self.source_workspace.is_dir() and self.source_workspace.resolve()!=source and not messagebox.askyesno('Switch project?',f"This session belongs to another project:\n\n{source}\n\nSwitch the active project and resume it?"):
    return
   self.session_state=state;self.source_workspace=source;self.workspace=working;self.project_selected=True;self.path_label.configure(text=self._workspace_label(),fg=TEXT);self.model_var.set(state['model']);self.context_var.set(str(state['context']));self.context_customized=True;self.tokens_var.set(str(state.get('max_tokens',4096)));self.temperature_var.set(str(state.get('temperature',0.2)));self.access_var.set(state.get('access','ask'));self.approval_var.set(state.get('approval_mode','per-action'));self.copy_review_btn.configure(state='normal' if state.get('access')=='copy' else 'disabled');self.session_title.configure(text=sid);self.chat.configure(state='normal');self.chat.delete('1.0','end');self.chat.configure(state='disabled');self._append('assistant','Session resumed',f"{sid} · {len(state.get('messages',[]))} saved messages");self._refresh_project_files()
   for msg in state.get('messages',[]):
    if msg.get('role') in {'user','assistant'} and msg.get('content'):self._append(msg['role'],'You' if msg['role']=='user' else 'Agent',msg['content'])
  except Exception as exc:messagebox.showerror('Could not resume session',str(exc))
 def _save(self):
  if self.session_state:SESSIONS.save(self.session_state)
 def _send(self):
  if self.busy:return
  try:self.source_workspace=validate_selected_workspace(self.project_selected,self.source_workspace)
  except ValueError as exc:messagebox.showinfo('Choose a project',str(exc));return
  prompt=self.prompt.get('1.0','end').strip()
  if not prompt or prompt.startswith('Describe the project task'):return
  try:context,tokens,temperature=parse_run_settings(self.context_var.get(),self.tokens_var.get(),self.temperature_var.get())
  except ValueError as exc:messagebox.showerror('Invalid settings',str(exc));return
  model=self.model_var.get().strip()
  if not model:messagebox.showerror('No model selected','No installed Ollama models were found. Install your model with Ollama, then refresh/reopen the app.');return
  if not self.session_state:self.session_state=SESSIONS.create(str(self.source_workspace),model,context)
  state=self.session_state;access=self.access_var.get();state.update(workspace=str(self.source_workspace),model=model,context=context,access=access,approval_mode=self.approval_var.get(),max_tokens=tokens,temperature=temperature)
  if access=='copy':
   try:
    expected=managed_copy_path(APP/'workspaces',state['session_id']);saved=state.get('working_workspace')
    if saved and Path(saved).resolve()==self.source_workspace.resolve():state.pop('working_workspace',None);state.pop('copy_manifest',None);saved=None
    if saved and Path(saved).resolve()!=expected:raise ValueError('The saved copy is outside this session’s managed workspace. Start a new session before using copy mode.')
   except (OSError,ValueError) as exc:messagebox.showerror('Invalid disposable workspace',str(exc));return
   if not saved or not expected.is_dir():
    state.pop('working_workspace',None);state.pop('copy_manifest',None)
    self._prepare_copy(state);return
   if not isinstance(state.get('copy_manifest'),dict):messagebox.showerror('Missing copy baseline','This disposable copy has no source snapshot for safe review. Start a new session to create a fresh copy.');return
   self.workspace=expected
  else:
   self.workspace=self.source_workspace;state['working_workspace']=str(self.source_workspace);state.pop('copy_manifest',None)
  self.path_label.configure(text=self._workspace_label(),fg=TEXT);self.copy_review_btn.configure(state='normal' if access=='copy' and state.get('copy_manifest') else 'disabled')
  try:
   turn=TurnContext.capture(workspace=self.workspace,model=model,context=context,access=self.access_var.get(),approval_mode=self.approval_var.get(),max_tokens=tokens,temperature=temperature,runtime_options=state.get('runtime_options',{}),prompt=prompt,session=state,mcp_tools=self.mcp_tools)
  except Exception as exc:messagebox.showerror('Could not start task',str(exc));return
  self.session_state=turn.session;state=turn.session;self.state=state
  try:self._save()
  except Exception as exc:messagebox.showerror('Could not save session',str(exc));return
  self._memory_warning_shown=False;self._append('user','You',prompt);self.prompt.delete('1.0','end');self._set_busy(True);self.started=time.monotonic();self.stop_event=threading.Event();self.events.emit({'type':'phase','phase':'connecting'})
  def approve(name,args):
   condition=threading.Condition();result={'value':False,'done':False};self.events.emit({'type':'approval_request','name':name,'arguments':args,'condition':condition,'result':result})
   with condition:
    while not result['done'] and not turn_cancel.is_set():condition.wait(.25)
   return result['value']
  turn_cancel=self.stop_event
  def run():
   discovered=list(turn.mcp_tools)
   try:
    if turn_cancel.is_set():self.events.emit({'type':'cancelled','elapsed':time.monotonic()-self.started});return
    if self.mcp.servers and not self.mcp.discovered:
     discovered=self.mcp.run(self.mcp.discover(),cancel_event=turn_cancel)
     self.mcp_tools=discovered
     failed=', '.join(self.mcp.errors) if self.mcp.errors else ''
     self.events.emit({'type':'mcp_status','text':f'{len(discovered)} local tools connected'+(f' · failed: {failed}' if failed else '')})
    else:discovered=list(turn.mcp_tools)
    if turn_cancel.is_set():self.events.emit({'type':'cancelled','elapsed':time.monotonic()-self.started});return
   except Exception as exc:self.events.emit({'type':'mcp_status','text':f'MCP unavailable: {exc}'})
   try:
    knowledge=ProjectKnowledge(turn.workspace)
    terminal={'kind':None}
    def engine_event(event):
     if event.get('type') in {'error','cancelled'}:terminal['kind']=event['type']
     if event.get('type')=='complete':event={**event,'type':'generation_complete'}
     self.events.emit(event)
    engine=AgentEngine(turn.workspace,turn.model,turn.context,turn.access,turn.approval_mode,engine_event,approve,runtime_options=turn.runtime_options,max_tokens=turn.max_tokens,temperature=turn.temperature,project_instructions=knowledge.instructions(),mcp_tools=discovered,mcp_call=lambda name,args:self.mcp.run(self.mcp.call(name,args),cancel_event=turn_cancel))
    self.engine=engine
    if turn_cancel.is_set():engine.cancel()
    new_messages=engine.run(turn.prompt,turn.session['messages']);turn.session['messages']=new_messages
    if terminal['kind'] is None:
     turn.session.setdefault('turns',[]).append({'prompt':turn.prompt,'at':time.time()});SESSIONS.save(turn.session);self.events.emit({'type':'session_saved'});self.events.emit({'type':'turn_result','messages':new_messages});self.events.emit({'type':'complete','elapsed':time.monotonic()-self.started})
    else:SESSIONS.save(turn.session);self.events.emit({'type':'session_saved'})
   except Exception as exc:self.events.emit({'type':'error','error':f'{type(exc).__name__}: {exc}'})
  threading.Thread(target=run,name='agent-task',daemon=True).start()
 def _workspace_label(self):
  if self.session_state and self.session_state.get('access')=='copy' and self.workspace.resolve()!=self.source_workspace.resolve():return f"Source: {self.source_workspace}\nWorking copy: {self.workspace}"
  return str(self.workspace)
 def _prepare_copy(self,state):
  source=self.source_workspace;destination=managed_copy_path(APP/'workspaces',state['session_id'])
  try:SESSIONS.save(state)
  except Exception as exc:messagebox.showerror('Could not save session',str(exc));return
  self._set_busy(True);self.copy_preparing=True;self.started=time.monotonic();self.stop_event=threading.Event();self.events.emit({'type':'phase','phase':'preparing disposable copy'})
  def work():
   try:
    manifest=create_disposable_workspace(source,destination);self.events.emit({'type':'copy_ready','destination':str(destination),'manifest':manifest})
   except Exception as exc:self.events.emit({'type':'copy_error','error':str(exc)})
  threading.Thread(target=work,name='project-copy',daemon=True).start()
 def _set_busy(self,value):
  self.busy=value;self.send_btn.configure(state='disabled' if value else 'normal');self.cancel_btn.configure(state='normal' if value else 'disabled');self.status.configure(text='● RUNNING' if value else '● READY',fg=AMBER if value else GREEN)
  for widget in (self.workspace_btn,self.model_combo,self.context_entry,self.tokens_entry,self.temperature_entry,self.access_combo,self.approval_combo,self.session_combo,self.resume_btn,self.new_session_btn,self.settings_btn,self.diff_btn,self.copy_review_btn):
   if widget is not None:widget.configure(state=('disabled' if value else ('readonly' if isinstance(widget,ttk.Combobox) else 'normal')))
  if not value:self.copy_review_btn.configure(state='normal' if self.session_state and self.session_state.get('access')=='copy' and self.session_state.get('copy_manifest') else 'disabled')
 def _cancel(self):
  if self.copy_preparing:
   if hasattr(self,'stop_event'):self.stop_event.set()
   self._append('tool','Cancel requested','Finishing disposable-copy preparation; no model request will be sent.')
   return
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
   elif kind=='model_info':
    if e.get('model')==self.model_var.get():
     self.displayed_context=e['context'];self.model_note.configure(text=f"Reasoning: model default · advertised {e['context']:,} tokens · live allocation shown below")
     if should_apply_advertised_context(bool(self.session_state),self.context_customized):self.context_var.set(str(e['context']))
   elif kind=='model_info_error':self.model_note.configure(text='Model metadata unavailable; context remains user-controlled.')
   elif kind=='text':
    self.chat.configure(state='normal');self.chat.insert('end',e['text'],'body');self.chat.see('end');self.chat.configure(state='disabled')
   elif kind=='phase':
    if e['phase']=='waiting':label=f"● WAITING · {int(e.get('elapsed',0))}s · ctx {e.get('context',self.context_var.get())}"
    elif e['phase']=='generating':label=f"● GENERATING · first output at {e.get('elapsed',0):.1f}s"
    else:label='● '+e['phase'].upper()
    self.status.configure(text=label,fg=AMBER)
   elif kind=='tool_start':
    detail='Tool: '+e['name']+'\n'+json.dumps(e.get('arguments'),ensure_ascii=False);self._append('tool','Tool activity',detail);self._view_append(self.activity_view,detail)
   elif kind=='tool_done':
    detail='Result\n'+str(e.get('result',''))[:3000];self._append('tool','Tool result',detail);self._view_append(self.activity_view,detail)
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
   elif kind=='plan':
    plan=json.dumps(e['items'],indent=2,ensure_ascii=False);self._append('tool','Task plan',plan);self._view_append(self.plan_view,plan,replace=True);self.inspector_tabs.select(0)
   elif kind=='project_files':
    if e['root']==str(self.workspace):
     self.file_list.delete(0,'end')
     if e.get('error'):self.file_list.insert('end','Index error · '+e['error'])
     else:
      for row in e['rows']:self.file_list.insert('end',('▧  ' if row['text'] else '▣  ')+row['path'])
   elif kind=='project_diff':
    if e['root']==str(self.workspace):self._view_append(self.diff_view,e['content'],replace=True);self.inspector_tabs.select(2)
   elif kind=='copy_ready':
    self.copy_preparing=False
    state=self.session_state;state['working_workspace']=e['destination'];state['copy_manifest']=e['manifest']
    try:SESSIONS.save(state)
    except Exception as exc:self._append('error','Could not save copy session',str(exc));self._set_busy(False);continue
    self._refresh_sessions()
    self.workspace=Path(e['destination']);self.path_label.configure(text=self._workspace_label(),fg=TEXT);self.copy_review_btn.configure(state='normal');self._refresh_project_files()
    if self.stop_event.is_set():self._append('tool','Copy ready','Cancelled before sending a model request. The source project is untouched; the disposable copy is saved for this session.');self._set_busy(False);continue
    self._set_busy(False);self._send()
   elif kind=='copy_error':self.copy_preparing=False;self._append('error','Could not prepare disposable copy',e['error']);self._set_busy(False);self._refresh_sessions()
   elif kind=='copy_review':
    if e['root']==str(self.workspace):self._show_copy_review(e['changes'])
   elif kind=='copy_review_error':messagebox.showerror('Copy review failed',e['error'])
   elif kind=='copy_applied':
    if e['root']==str(self.workspace):
     try:self.session_state['copy_manifest']=capture_manifest(self.source_workspace);self._save()
     except Exception as exc:self._append('error','Could not refresh copy baseline',str(exc))
     self._append('assistant','Source updated',f"Applied {len(e['paths'])} reviewed change(s):\n"+'\n'.join(e['paths']));self._refresh_project_files();self._refresh_diff()
    self._set_busy(False)
   elif kind=='copy_apply_error':messagebox.showerror('Could not apply copy changes',e['error']);self._set_busy(False)
  self.after(100,self._poll)
 def _approval_dialog(self,e):
  name,args=e['name'],e.get('arguments',{});condition=e['condition'];result=e['result'];self.pending_approval=(condition,result)
  dialog=tk.Toplevel(self);dialog.title('Review requested action');dialog.geometry('860x650');dialog.minsize(620,430);dialog.configure(bg=BG);dialog.transient(self);dialog.grab_set();dialog.columnconfigure(0,weight=1);dialog.rowconfigure(3,weight=1)
  tk.Label(dialog,text='ACTION REVIEW',bg=BG,fg=AMBER,font=('Segoe UI Semibold',9)).grid(row=0,column=0,sticky='w',padx=20,pady=(16,3));tk.Label(dialog,text=name.replace('_',' ').title(),bg=BG,fg=TEXT,font=('Segoe UI Semibold',16)).grid(row=1,column=0,sticky='w',padx=20,pady=(0,10))
  if name=='run_command':
   detail='Command argv:\n'+json.dumps(args.get('argv',[]),ensure_ascii=False,indent=2)+f"\n\nTimeout: {args.get('timeout_seconds',120)} seconds\n\nThis process runs with your Windows account permissions."
  elif name=='apply_patch':detail=str(args.get('patch',''))
  else:detail=json.dumps(args,indent=2,ensure_ascii=False)
  if name.startswith('mcp__'):warning='Local MCP server actions may access resources outside the selected project.'
  elif name=='run_command':warning='Commands are not sandboxed and can access files beyond the selected project.'
  elif self.approval_var.get()=='per-task':warning='Per-task approval authorizes later model-requested tool actions for this task.'
  else:warning='Only this action will be approved.'
  tk.Label(dialog,text=warning,bg='#332B20',fg=AMBER,wraplength=800,justify='left',padx=12,pady=9).grid(row=2,column=0,sticky='new',padx=16,pady=(0,8))
  preview=tk.Frame(dialog,bg=PANEL);preview.grid(row=3,column=0,sticky='nsew',padx=16,pady=(0,8));preview.rowconfigure(0,weight=1);preview.columnconfigure(0,weight=1)
  text=tk.Text(preview,bg=PANEL2,fg=TEXT,insertbackground=TEXT,relief='flat',wrap='none',font=('Cascadia Code',9),padx=12,pady=10);text.grid(row=0,column=0,sticky='nsew');ys=ttk.Scrollbar(preview,command=text.yview);ys.grid(row=0,column=1,sticky='ns');xs=ttk.Scrollbar(preview,command=text.xview,orient='horizontal');xs.grid(row=1,column=0,sticky='ew');text.configure(yscrollcommand=ys.set,xscrollcommand=xs.set);text.insert('1.0',detail);text.configure(state='disabled')
  actions=tk.Frame(dialog,bg=BG);actions.grid(row=4,column=0,sticky='ew',padx=16,pady=(2,16));actions.columnconfigure(0,weight=1);approved={'value':False}
  def decide(value):approved['value']=value;dialog.destroy()
  tk.Button(actions,text='Deny',command=lambda:decide(False),bg=PANEL2,fg=TEXT,relief='flat',padx=18,pady=9).grid(row=0,column=1,padx=(0,8));tk.Button(actions,text='Approve task' if self.approval_var.get()=='per-task' else 'Approve action',command=lambda:decide(True),bg=CYAN,fg='#182018',relief='flat',padx=18,pady=9).grid(row=0,column=2)
  dialog.protocol('WM_DELETE_WINDOW',lambda:decide(False));self.wait_window(dialog);ok=approved['value']
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
  if d.get('gpu'):parts.append('NVIDIA system GPU '+d['gpu'].splitlines()[0])
  if d.get('loaded'):
   m=d['loaded'][0];parts.append(f"Ollama allocated context {m.get('context_length','?'):,}" if isinstance(m.get('context_length'),int) else 'Model loaded')
   try:requested=self.session_state.get('context',self.displayed_context) if getattr(self,'session_state',None) else int(self.context_var.get() or self.displayed_context)
   except (TypeError,ValueError):requested=self.displayed_context
   actual=m.get('context_length')
   if isinstance(actual,int) and actual<requested:parts.append(f"WARNING: Ollama allocated {actual:,}, request was {requested:,}. Check runtime logs/memory; app did not lower setting.")
  if self.busy and self.started:parts.append(f"Elapsed {int(time.monotonic()-self.started)}s · {self.status.cget('text').split(' · ')[0]}")
  self.resource_label.configure(text='\n'.join(parts) or 'System telemetry unavailable')
  if total and avail and avail/total<.10 and not getattr(self,'_memory_warning_shown',False):self._memory_warning_shown=True;self._append('tool','Memory pressure',f"Only {avail/1024**3:.1f} GiB RAM remains available. Ollama decides whether the requested context can be allocated; the app will not silently lower it.")
 def _configure_mcp(self):
  path=APP/'mcp.json';path.parent.mkdir(parents=True,exist_ok=True)
  if not path.exists():path.write_text(json.dumps({'mcpServers':{}},indent=2),encoding='utf8')
  try:os.startfile(path)
  except Exception:messagebox.showinfo('MCP configuration',f'Edit local stdio server configuration at:\n{path}\n\nInstall optional dependencies with pip install -r requirements-mcp.txt, then restart the app.')
 def on_close(self):
  if self.copy_preparing and hasattr(self,'stop_event'):self.stop_event.set()
  if self.engine and self.busy:self.engine.cancel()
  try:self._save()
  except Exception:pass
  threading.Thread(target=self.mcp.shutdown,name='mcp-shutdown',daemon=True).start()
  self.destroy()

if __name__=='__main__':
 app=LocalAgentApp();app.protocol('WM_DELETE_WINDOW',app.on_close);app.mainloop()


