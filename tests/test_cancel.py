import unittest,threading
from unittest.mock import patch
from desktop_engine import OllamaRuntime, RequestCancelled
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
   with self.assertRaises(RequestCancelled):runtime.stream(state,[],cancel,lambda e:None)
if __name__=='__main__':unittest.main()
