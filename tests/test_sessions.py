import unittest,tempfile
from pathlib import Path
from sessions import SessionStore
from desktop_engine import AgentEngine, MODEL, CONTEXT_MAX
class SessionTests(unittest.TestCase):
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
