import json,os,unittest,tempfile
from pathlib import Path
from sessions import SessionStore
from desktop_engine import AgentEngine, MODEL, CONTEXT_MAX
class SessionTests(unittest.TestCase):
 def test_save_round_trips_unpaired_surrogate_as_valid_utf8_json(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'sessions';store=SessionStore(root);state=store.create(t,MODEL,CONTEXT_MAX)
   state['messages'].append({'role':'user','content':'before\\udc9dafter'})
   store.save(state)
   raw=(root/(state['session_id']+'.json')).read_bytes()
   loaded=store.load(state['session_id'])
   self.assertIn(b'\\udc9d',raw)
   self.assertEqual(loaded['messages'][-1]['content'],'before\\udc9dafter')

 def test_save_retries_a_windows_sharing_lock_longer_than_old_burst(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'sessions';store=SessionStore(root);state=store.create(t,MODEL,CONTEXT_MAX);store.save(state)
   target=root/(state['session_id']+'.json');real_replace=os.replace;attempts=[];clock=[0.0]
   def replace_after_extended_lock(source,destination):
    attempts.append(1)
    if len(attempts)<=9:raise PermissionError('temporary Windows sharing violation')
    return real_replace(source,destination)
   def advance_clock(delay):clock[0]+=delay
   state['marker']='saved after lock'
   with patch('sessions.os.replace',side_effect=replace_after_extended_lock), \
        patch('sessions.time.monotonic',side_effect=lambda:clock[0]), \
        patch('sessions.time.sleep',side_effect=advance_clock):
    store.save(state)
   self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['marker'],'saved after lock')
   self.assertEqual(len(attempts),10);self.assertGreaterEqual(clock[0],2.0)
   self.assertEqual(list(root.glob('*.tmp')),[])
 def test_save_keeps_waiting_past_legacy_deadline_until_lock_releases(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'sessions';store=SessionStore(root);state=store.create(t,MODEL,CONTEXT_MAX);store.save(state)
   target=root/(state['session_id']+'.json');real_replace=os.replace;clock=[0.0]
   def replace_after_long_lock(source,destination):
    if clock[0]<35.0:raise PermissionError('session snapshot remains open')
    return real_replace(source,destination)
   def advance_clock(delay):clock[0]+=delay
   state['marker']='saved after long lock'
   with patch('sessions.os.replace',side_effect=replace_after_long_lock), \
        patch('sessions.time.monotonic',side_effect=lambda:clock[0]), \
        patch('sessions.time.sleep',side_effect=advance_clock):
    store.save(state)
   self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['marker'],'saved after long lock')
   self.assertGreaterEqual(clock[0],35.0)
   self.assertEqual(list(root.glob('*.tmp')),[])
 def test_create_save_recover_and_list(self):
  with tempfile.TemporaryDirectory() as t:
   store=SessionStore(Path(t)/'sessions');state=store.create(t,MODEL,CONTEXT_MAX);store.save(state);loaded=store.load(state['session_id'])
   self.assertEqual(loaded['model'],MODEL);self.assertEqual(loaded['context'],CONTEXT_MAX);self.assertEqual(store.list()[0]['session_id'],state['session_id'])
 def test_default_is_per_action_and_ask(self):
  with tempfile.TemporaryDirectory() as t:
   from sessions import SessionStore
   state=SessionStore(Path(t)/'s').create(t,MODEL,CONTEXT_MAX)
   self.assertEqual(state['approval_mode'],'per-action');self.assertEqual(state['access'],'ask')
if __name__=='__main__':unittest.main()
