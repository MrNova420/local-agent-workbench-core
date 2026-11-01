import unittest,threading,tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
  with patch('desktop_engine.open_http_stream',return_value=Response()),patch.object(Response,'readline',read):
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
  with patch('desktop_engine.open_http_stream',return_value=Response()):
   thread=threading.Thread(target=run);thread.start();self.assertTrue(entered_read.wait(1));cancel.set();runtime.cancel();thread.join(1)
  self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],RequestCancelled)
  self.assertEqual(errors[0].partial_message,{'role':'assistant','content':'first'})
 def test_cancel_interrupts_request_waiting_for_response_headers(self):
  received=threading.Event();release=threading.Event();errors=[]
  class DelayedHeaders(BaseHTTPRequestHandler):
   protocol_version='HTTP/1.1'
   def do_POST(self):
    received.set();release.wait(5)
    try:
     self.send_response(200);self.send_header('Content-Length','0');self.end_headers()
    except OSError:pass
   def log_message(self,*_args):pass
  server=ThreadingHTTPServer(('127.0.0.1',0),DelayedHeaders)
  server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
  runtime=OllamaRuntime(f'http://127.0.0.1:{server.server_port}')
  state={'model':'mock','registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':4096,'max_tokens':12};cancel=threading.Event();events=[];dispatched=threading.Event()
  def emit(event):
   events.append(event)
   if event.get('type')=='request_dispatched':dispatched.set()
  def run():
   try:runtime.stream(state,[],cancel,emit)
   except Exception as exc:errors.append(exc)
  request_thread=threading.Thread(target=run);request_thread.start()
  request_received=received.wait(2);request_dispatched=dispatched.wait(2)
  cancel.set();runtime.cancel();request_thread.join(2);release.set()
  server.shutdown();server.server_close();server_thread.join(2)
  self.assertTrue(request_received,'fake provider did not receive the request')
  self.assertTrue(request_dispatched,'request dispatch was not recorded')
  self.assertFalse(request_thread.is_alive(),'cancel left the request blocked awaiting headers')
  self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],RequestCancelled)
  self.assertTrue(any(event.get('type')=='request_dispatched' for event in events))
  failure=next(event for event in events if event.get('type')=='request_error')
  self.assertEqual(failure['phase'],'waiting_response_headers');self.assertTrue(failure['cancelled'])
if __name__=='__main__':unittest.main()
