"""Desktop-only UI messages and helpers tested without creating a display."""
from __future__ import annotations
import queue
from typing import Any
class EventBus:
 def __init__(self):self._q=queue.Queue()
 def emit(self,event:dict[str,Any]):self._q.put(event)
 def drain(self,limit:int=500):
  out=[]
  for _ in range(limit):
   try:out.append(self._q.get_nowait())
   except queue.Empty:break
  return out

class AgentEventRelay:
 """Keep terminal engine events behind session persistence."""
 def __init__(self,emit):self._emit=emit;self._terminal=None
 def __call__(self,event:dict[str,Any]):
  kind=event.get('type')
  if kind in {'error','cancelled','stopped_after_operation'}:
   self._terminal=dict(event)
   return
  if kind=='complete':event={**event,'type':'generation_complete'}
  self._emit(event)
 @property
 def has_terminal(self):return self._terminal is not None
 @property
 def terminal_event(self):return dict(self._terminal) if self._terminal is not None else None
 def emit_terminal_after_persist(self):
  event,self._terminal=self._terminal,None
  if event is not None:self._emit(event)
 def discard_terminal(self):self._terminal=None
