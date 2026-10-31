import unittest,threading,tempfile
from pathlib import Path
from unittest.mock import patch
from desktop_engine import AgentEngine, OllamaRuntime, RequestCancelled
class CancelTests(unittest.TestCase):
 def test_cancel_between_stream_chunks_ends_only_request(self):
  class Response:
   def __init__(self):self.lines=iter([b'{"message":{"content":"first"}}\n',b''])
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def readline(self):return next(self.lines,b'')
   def close(self):pass
  runtime=OllamaRuntime();state={'model':'mock','registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':4096,'max_tokens':12};cancel=threading.Event()
  original=Response.readline
  def read(response):
   line=original(response)
   if line:cancel.set()
   return line
  with patch('urllib.request.urlopen',return_value=Response()),patch.object(Response,'readline',read):
   with self.assertRaises(RequestCancelled) as raised:runtime.stream(state,[],cancel,lambda e:None)
  self.assertEqual(raised.exception.partial_message,{'role':'assistant','content':'first'})
 def test_agent_engine_keeps_partial_assistant_message_when_cancelled(self):
  class PartialRuntime:
   def stream(self,*_args):raise RequestCancelled('Cancelled',partial_message={'role':'assistant','content':'partial reply'})
  with tempfile.TemporaryDirectory() as root:
   engine=AgentEngine(Path(root),access='plan')
   engine.runtime=PartialRuntime();events=[];engine.event=events.append
   messages=engine.run('do a task',[])
  self.assertEqual(messages[-1],{'role':'assistant','content':'partial reply'})
  self.assertTrue(any(event.get('type')=='cancelled' for event in events))
 def test_stream_close_during_read_preserves_partial_reply(self):
  entered_read=threading.Event();closed=threading.Event();cancel=threading.Event();errors=[]
  class Response:
   def __init__(self):self.lines=iter([b'{"message":{"content":"first"}}\n'])
   def readline(self):
    line=next(self.lines,None)
    if line:return line
    entered_read.set();closed.wait(1);raise ConnectionResetError('stream closed')
   def close(self):closed.set()
  runtime=OllamaRuntime();state={'model':'mock','registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':4096,'max_tokens':12}
  def run():
   try:runtime.stream(state,[],cancel,lambda _event:None)
   except Exception as exc:errors.append(exc)
  with patch('urllib.request.urlopen',return_value=Response()):
   thread=threading.Thread(target=run);thread.start();self.assertTrue(entered_read.wait(1));cancel.set();runtime.cancel();thread.join(1)
  self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],RequestCancelled)
  self.assertEqual(errors[0].partial_message,{'role':'assistant','content':'first'})
if __name__=='__main__':unittest.main()
