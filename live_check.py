"""Run one explicitly approved live check in an isolated disposable copy."""
from __future__ import annotations
import argparse, json, shutil, tempfile, time, urllib.request
from pathlib import Path
from desktop_engine import AgentEngine, MODEL, CONTEXT_MAX

def get(url):
 with urllib.request.urlopen(url,timeout=5) as r:return json.loads(r.read().decode())
def main():
 p=argparse.ArgumentParser();p.add_argument('--model',default=MODEL);p.add_argument('--context',type=int,default=32768);p.add_argument('--output',default='live-check.json');a=p.parse_args()
 if a.model!=MODEL:raise SystemExit('This approved live check is scoped to the exact HF Ornith 1.5 9B Q4_K_M tag.')
 root=Path(tempfile.mkdtemp(prefix='local-agent-live-check-'));fixture=root/'fixture';fixture.mkdir()
 (fixture/'task.py').write_text('def greet(name):\n    return "TODO"\n',encoding='utf8')
 (fixture/'test_task.py').write_text('import unittest\nfrom task import greet\nclass TestGreeting(unittest.TestCase):\n def test_greet(self): self.assertEqual(greet("Ada"),"Hello, Ada!")\nif __name__=="__main__":unittest.main()\n',encoding='utf8')
 before=get('http://127.0.0.1:11434/api/ps').get('models',[]);before_target=next((m for m in before if m.get('model')==a.model or m.get('name')==a.model),None)
 info=get('http://127.0.0.1:11434/api/show') if False else None
 payload={'model':a.model};req=urllib.request.Request('http://127.0.0.1:11434/api/show',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
 with urllib.request.urlopen(req,timeout=5) as r:metadata=json.loads(r.read().decode())
 advertised=max((v for k,v in metadata.get('model_info',{}).items() if k.endswith('.context_length') and isinstance(v,int)),default=None)
 initial_context=a.context
 events=[];engine=AgentEngine(fixture,model=a.model,context=initial_context,access='ask',event=events.append,approve=lambda *_:True,max_tokens=512)
 prompt='Edit task.py so greet(name) returns exactly Hello, <name>! Then run the provided pytest test and report the result.'
 start=time.monotonic();messages=engine.run(prompt);elapsed=time.monotonic()-start
 after=get('http://127.0.0.1:11434/api/ps').get('models',[]);after_target=next((m for m in after if m.get('model')==a.model or m.get('name')==a.model),None)
 text=''.join(e.get('text','') for e in events);first=next((e.get('at') for e in events if e.get('type')=='text'),None)
 first_delay=round(first-start,3) if first is not None else None
 completed=next((e for e in events if e.get('type')=='complete'),{})
 usage=completed.get('usage',{});eval_count=usage.get('eval_count');eval_ns=usage.get('eval_duration')
 speed=round(eval_count/(eval_ns/1e9),3) if isinstance(eval_count,int) and isinstance(eval_ns,int) and eval_ns else None
 file=(fixture/'task.py').read_text(encoding='utf8');import subprocess
 test=subprocess.run([__import__('sys').executable,'-m','unittest','discover','-s','.','-v'],cwd=fixture,capture_output=True,text=True,timeout=120,check=False)
 result={'model':a.model,'workspace':str(fixture),'started_loaded_before':bool(before_target),'loaded_before':before_target,'loaded_after':after_target,'advertised_context':advertised,'requested_context':initial_context,'effective_context':after_target.get('context_length') if after_target else None,'elapsed_seconds':round(elapsed,3),'first_token_delay_seconds':first_delay,'generation_tokens_per_second':speed,'ollama_eval_count':eval_count,'ollama_eval_duration_ns':eval_ns,'assistant_text':text,'file_after':file,'pytest_returncode':test.returncode,'pytest_output':test.stdout+test.stderr,'event_types':[e.get('type') for e in events]}
 # Read only the test-created model's actual runtime state. Leave it warm as approved; if it wasn't loaded before, only unload that test-created runner.
 if not before_target and after_target:
  unload={'model':a.model,'keep_alive':0};u=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=json.dumps({**unload,'prompt':'','stream':False}).encode(),headers={'Content-Type':'application/json'},method='POST')
  with urllib.request.urlopen(u,timeout=120) as r:r.read()
 result['cleanup_unloaded_own_runner']=bool(not before_target and after_target)
 out=Path(a.output).resolve();out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8');print(json.dumps({'output':str(out),'effective_context':result['effective_context'],'elapsed':result['elapsed_seconds'],'test_exit':test.returncode,'loaded_before':bool(before_target)},indent=2))
if __name__=='__main__':main()
