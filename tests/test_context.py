import unittest
from desktop_engine import ContextManager, CONTEXT_MAX, MODEL

class ContextTests(unittest.TestCase):
    def test_target_model_and_full_context_are_defaults(self):
        self.assertEqual(MODEL, 'hf.co/mradermacher/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M')
        self.assertEqual(CONTEXT_MAX, 262144)
        self.assertEqual(ContextManager().context, 262144)
    def test_retrieval_stays_inside_workspace_and_ranks_relevant_file(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'src.py').write_text('def stream ollama reasoning setting',encoding='utf8');(root/'other.md').write_text('unrelated meadow notes',encoding='utf8')
            got=ContextManager().retrieve(root,'ollama stream');self.assertIn('src.py',got);self.assertNotIn('other.md',got)
    def test_compaction_keeps_system_and_recent_messages(self):
        manager=ContextManager(context=4096,reserve=1024);messages=[{'role':'system','content':'SYSTEM'}]
        messages += [{'role':'user','content':f'turn {i} '+'x'*400} for i in range(40)]
        compacted=manager.compact(messages);self.assertEqual(compacted[0],messages[0]);summary=next(m for m in compacted if 'automatically compacted' in str(m.get('content')));self.assertEqual(summary['role'],'user');self.assertEqual(compacted[-1],messages[-1])
    def test_budget_refuses_to_silently_lower_context(self):
        manager=ContextManager(context=4096,reserve=1024);huge=[{'role':'system','content':'s'},{'role':'user','content':'x'*50000}]
        with self.assertRaisesRegex(ValueError,'context was not reduced'):manager.compact(huge)
if __name__=='__main__':unittest.main()
