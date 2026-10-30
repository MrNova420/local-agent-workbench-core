import unittest, json, threading
from unittest.mock import patch
from desktop_engine import OllamaRuntime
class FakeResponse:
 def __init__(self,lines):self.lines=iter(lines);self.closed=False
 def __enter__(self):return self
 def __exit__(self,*args):self.closed=True
 def readline(self):return next(self.lines,b'')
 def close(self):self.closed=True
class StreamTests(unittest.TestCase):
 def test_ollama_ndjson_stream_and_tool_call_assembly(self):
  lines=[json.dumps({'message':{'content':'hello'}}).encode()+b'\n',json.dumps({'message':{'tool_calls':[{'function':{'name':'list_files','arguments':'{"path":"."}'}}]},'done':True,'eval_count':1}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[];state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':16384,'max_tokens':500,'runtime_options':{}}
  with patch('urllib.request.urlopen',return_value=response) as open_url:
   got=runtime.stream(state,[],threading.Event(),events.append)
  self.assertEqual(got['message']['content'],'hello');self.assertEqual(got['message']['tool_calls'][0]['function']['name'],'list_files');self.assertEqual(next(e['text'] for e in events if e['type']=='text'),'hello');self.assertEqual(next(e['phase'] for e in events if e['type']=='phase'),'generating')
  payload=json.loads(open_url.call_args.args[0].data);self.assertEqual(payload['options']['num_ctx'],16384);self.assertNotIn('think',payload);self.assertNotIn('reasoning_effort',payload);self.assertIsNone(open_url.call_args.kwargs['timeout'])
if __name__=='__main__':unittest.main()
