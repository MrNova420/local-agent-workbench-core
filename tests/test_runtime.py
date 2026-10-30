import unittest
from desktop_engine import AgentEngine, OllamaRuntime, RequestCancelled

class RuntimeTests(unittest.TestCase):
    def test_cancel_closes_current_response_without_stop_api(self):
        class Fake:
            closed=False
            def close(self): self.closed=True
        runtime=OllamaRuntime(); fake=Fake(); runtime.response=fake; runtime.cancel()
        self.assertTrue(fake.closed)
        self.assertFalse(hasattr(runtime,'stop_model'))
    def test_approval_mode_defaults_to_each_action(self):
        import inspect
        self.assertIn('per-action', inspect.signature(AgentEngine).parameters['approval_mode'].default)
    def test_exact_tag_default_does_not_set_reasoning_parameters(self):
        from desktop_engine import MODEL
        self.assertEqual(MODEL,'hf.co/mradermacher/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M')
        import inspect
        source=inspect.getsource(OllamaRuntime.stream)
        self.assertNotIn('think',source)
        self.assertNotIn('reasoning_effort',source)
if __name__=='__main__':unittest.main()
