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
