import unittest
from pathlib import Path
from desktop_engine import AgentEngine, MODEL
class UIEngineTests(unittest.TestCase):
 def test_engine_keeps_model_selectable(self):
  import inspect
  sig=inspect.signature(AgentEngine)
  self.assertIn('model',sig.parameters)
  self.assertEqual(sig.parameters['model'].default,MODEL)
 def test_default_context_does_not_reduce_advertised_model_capacity(self):
  import inspect
  sig=inspect.signature(AgentEngine)
  self.assertEqual(sig.parameters['context'].default,262144)
if __name__=='__main__':unittest.main()
