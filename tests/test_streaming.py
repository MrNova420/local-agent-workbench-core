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
 def test_reasoning_and_user_facing_generation_are_distinct_phases(self):
  lines=[json.dumps({'message':{'thinking':'reasoning only'}}).encode()+b'\n',json.dumps({'message':{'content':'visible answer'},'done':True,'eval_count':9}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[];state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':16384,'max_tokens':500,'runtime_options':{}}
  with patch('urllib.request.urlopen',return_value=response):runtime.stream(state,[],threading.Event(),events.append)
  self.assertEqual([event['phase'] for event in events if event['type']=='phase'],['reasoning','generating'])

 def test_requested_reasoning_is_sent_exactly_and_streamed_separately(self):
  lines=[json.dumps({'message':{'thinking':'plan ','content':'answer'},'done':True,'eval_count':4,'eval_duration':2000000000}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[];state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':16384,'max_tokens':500,'runtime_options':{},'reasoning':'high'}
  with patch('urllib.request.urlopen',return_value=response) as open_url:
   result=runtime.stream(state,[],threading.Event(),events.append)
  payload=json.loads(open_url.call_args.args[0].data)
  self.assertEqual(payload['think'],'high')
  settings=next(event for event in events if event['type']=='inference_settings')
  self.assertEqual(settings['requested_context'],16384)
  self.assertEqual(settings['max_output_tokens'],500)
  self.assertEqual(settings['reasoning_override'],'high')
  self.assertEqual(settings['request_options']['num_predict'],500)
  self.assertEqual(result['message']['thinking'],'plan ')
  self.assertEqual(next(event['text'] for event in events if event['type']=='thinking'),'plan ')
  self.assertEqual(next(event['text'] for event in events if event['type']=='text'),'answer')

 def test_ollama_ndjson_stream_and_tool_call_assembly(self):
  lines=[json.dumps({'message':{'content':'hello'}}).encode()+b'\n',json.dumps({'message':{'tool_calls':[{'function':{'name':'list_files','arguments':'{"path":"."}'}}]},'done':True,'prompt_eval_count':18,'prompt_eval_cached_count':7,'eval_count':1}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[];state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':16384,'max_tokens':500,'runtime_options':{}}
  with patch('urllib.request.urlopen',return_value=response) as open_url:
   got=runtime.stream(state,[],threading.Event(),events.append)
  self.assertEqual(got['message']['content'],'hello');self.assertEqual(got['message']['tool_calls'][0]['function']['name'],'list_files');self.assertEqual(got['prompt_eval_count'],18);self.assertEqual(got['prompt_eval_cached_count'],7);self.assertEqual(next(e['text'] for e in events if e['type']=='text'),'hello');self.assertEqual(next(e['phase'] for e in events if e['type']=='phase'),'generating')
  payload=json.loads(open_url.call_args.args[0].data);self.assertEqual(payload['options']['num_ctx'],16384);self.assertNotIn('think',payload);self.assertNotIn('reasoning_effort',payload);self.assertIsNone(open_url.call_args.kwargs['timeout'])
if __name__=='__main__':unittest.main()
