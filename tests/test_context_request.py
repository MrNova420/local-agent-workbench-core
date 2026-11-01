"""Mocked check that a failed context does not substitute a smaller request."""
import json,threading,unittest
from unittest.mock import patch
from urllib.error import HTTPError
from desktop_engine import OllamaRuntime
class ContextRequestTests(unittest.TestCase):
 def test_runtime_sends_requested_context_verbatim(self):
  captured={}
  def fail(req,cancel,register):
   captured['payload']=json.loads(req.data)
   raise HTTPError(req.full_url,500,'context allocation failed',None,None)
  state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'context':262144,'temperature':0.2,'max_tokens':4096}
  with patch('desktop_engine.open_http_stream',side_effect=fail):
   with self.assertRaises(HTTPError):OllamaRuntime().stream(state,[],threading.Event(),lambda e:None)
  self.assertEqual(captured['payload']['options']['num_ctx'],262144)
if __name__=='__main__':unittest.main()
