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
 def test_ollama_request_uses_the_context_selected_tool_schemas(self):
  lines=[json.dumps({'message':{'content':'ready'},'done':True}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[]
  selected=[{'type':'function','function':{'name':'read_file','description':'read','parameters':{'type':'object','properties':{}}}}]
  state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[{'type':'function','function':{'name':'write_file'}}]})(),
         'temperature':0.2,'context':4096,'max_tokens':512,'runtime_options':{},'request_tools':selected,
         'deferred_tool_names':['write_file']}
  with patch('desktop_engine.open_http_stream',return_value=response) as open_url:
   runtime.stream(state,[],threading.Event(),events.append)
  payload=json.loads(open_url.call_args.args[0].data)
  self.assertEqual(payload['tools'],selected)
  settings=next(event for event in events if event['type']=='inference_settings')
  self.assertEqual(settings['tool_names'],['read_file'])
  self.assertEqual(settings['deferred_tool_names'],['write_file'])

 def test_reasoning_and_user_facing_generation_are_distinct_phases(self):
  lines=[json.dumps({'message':{'thinking':'reasoning only'}}).encode()+b'\n',json.dumps({'message':{'content':'visible answer'},'done':True,'eval_count':9}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[];state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':16384,'max_tokens':500,'runtime_options':{}}
  with patch('desktop_engine.open_http_stream',return_value=response):runtime.stream(state,[],threading.Event(),events.append)
  self.assertEqual([event['phase'] for event in events if event['type']=='phase'],['reasoning','generating'])

 def test_incomplete_stream_preserves_partial_reasoning_and_reports_request_error(self):
  lines=[json.dumps({'message':{'thinking':'partial plan before disconnect'}}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[]
  state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),
         'temperature':0.2,'context':16384,'max_tokens':500,'runtime_options':{}}
  with patch('desktop_engine.open_http_stream',return_value=response):
   with self.assertRaisesRegex(RuntimeError,'without its final done=true record'):
    runtime.stream(state,[],threading.Event(),events.append)
  self.assertEqual(events[-1]['type'],'request_error')
  self.assertEqual(events[-1]['phase'],'streaming_response')
  self.assertTrue(any(event.get('type')=='thinking' and event.get('text')=='partial plan before disconnect' for event in events))
  self.assertFalse(any(event['type']=='request_complete' for event in events))

 def test_requested_reasoning_is_sent_exactly_and_streamed_separately(self):
  lines=[json.dumps({'message':{'thinking':'plan ','content':'answer'},'done':True,'eval_count':4,'eval_duration':2000000000}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[];state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':16384,'max_tokens':500,'runtime_options':{},'reasoning':'high'}
  with patch('desktop_engine.open_http_stream',return_value=response) as open_url:
   result=runtime.stream(state,[],threading.Event(),events.append)
  payload=json.loads(open_url.call_args.args[0].data)
  self.assertEqual(payload['think'],'high')
  settings=next(event for event in events if event['type']=='inference_settings')
  self.assertEqual(settings['requested_context'],16384)
  self.assertEqual(settings['max_output_tokens'],500)
  self.assertEqual(settings['reasoning_override'],'high')
  self.assertTrue(settings['reasoning_field_sent'])
  self.assertEqual(settings['additional_fields'],{'think':'high'})
  self.assertEqual(settings['provider'],'ollama')
  self.assertEqual(settings['request_api'],'/api/chat')
  self.assertEqual(settings['request_options']['num_predict'],500)
  self.assertEqual(result['message']['thinking'],'plan ')
  self.assertEqual(next(event['text'] for event in events if event['type']=='thinking'),'plan ')
  self.assertEqual(next(event['text'] for event in events if event['type']=='text'),'answer')

 def test_ollama_ndjson_stream_and_tool_call_assembly(self):
  lines=[json.dumps({'message':{'content':'hello'}}).encode()+b'\n',json.dumps({'message':{'tool_calls':[{'function':{'name':'list_files','arguments':'{"path":"."}'}}]},'done':True,'prompt_eval_count':18,'prompt_eval_cached_count':7,'eval_count':1}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[];state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'temperature':0.2,'context':16384,'max_tokens':500,'runtime_options':{},'request_index':3,'keep_alive':'2h'}
  with patch('desktop_engine.open_http_stream',return_value=response) as open_url:
   got=runtime.stream(state,[],threading.Event(),events.append)
  self.assertEqual(got['message']['content'],'hello');self.assertEqual(got['message']['tool_calls'][0]['function']['name'],'list_files');self.assertEqual(got['prompt_eval_count'],18);self.assertEqual(got['prompt_eval_cached_count'],7);self.assertEqual(next(e['text'] for e in events if e['type']=='text'),'hello');self.assertEqual(next(e['phase'] for e in events if e['type']=='phase'),'generating')
  progress=next(e for e in events if e['type']=='tool_call_progress');self.assertEqual(progress['tool_name'],'list_files');self.assertGreater(progress['argument_characters'],0);self.assertNotIn('arguments',progress);self.assertNotIn('arguments_preview',progress)
  payload=json.loads(open_url.call_args.args[0].data);self.assertEqual(payload['options']['num_ctx'],16384);self.assertNotIn('think',payload);self.assertNotIn('reasoning_effort',payload);self.assertNotIn('timeout',open_url.call_args.kwargs)
  settings=next(event for event in events if event['type']=='inference_settings')
  self.assertGreater(settings['estimated_input_tokens'],0)
  self.assertIn('estimate',settings['input_estimate_method'])
  self.assertEqual(settings['provider'],'ollama')
  self.assertEqual(settings['request_index'],3)
  self.assertEqual(settings['request_api'],'/api/chat')
  self.assertEqual(settings['api_model'],'mock')
  self.assertTrue(settings['stream'])
  self.assertEqual(settings['keep_alive'],'2h')
  self.assertFalse(settings['reasoning_field_sent'])
  self.assertEqual(settings['additional_fields'],{})
  self.assertNotIn('think',payload)

 def test_selected_context_output_and_temperature_override_conflicting_runtime_options(self):
  lines=[json.dumps({'message':{'content':'ok'},'done':True}).encode()+b'\n']
  response=FakeResponse(lines);runtime=OllamaRuntime();events=[]
  state={'model':'mock','messages':[],'registry':type('R',(),{'schemas':[]})(),'temperature':0.2,
         'context':65536,'max_tokens':8192,
         'runtime_options':{'num_ctx':8192,'num_predict':500,'temperature':1.0,'num_thread':6}}
  with patch('desktop_engine.open_http_stream',return_value=response) as open_url:
   runtime.stream(state,[],threading.Event(),events.append)
  payload=json.loads(open_url.call_args.args[0].data)
  self.assertEqual(payload['options'],{'temperature':0.2,'num_ctx':65536,'num_predict':8192,'num_thread':6})
  settings=next(event for event in events if event['type']=='inference_settings')
  self.assertEqual(settings['requested_context'],65536)
  self.assertEqual(settings['max_output_tokens'],8192)
  self.assertEqual(settings['additional_fields']['ignored_runtime_option_overrides'],
                   {'num_ctx':8192,'num_predict':500,'temperature':1.0})
if __name__=='__main__':unittest.main()
