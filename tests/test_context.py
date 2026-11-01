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
    def test_retrieval_returns_line_numbered_match_even_when_it_is_deep_in_a_file(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as tmp:
            root=Path(tmp);lines=[f"unrelated line {i}" for i in range(800)];lines[700]="def target_feature(): return 42"
            (root/'src.py').write_text("\n".join(lines),encoding='utf8')
            got=ContextManager().retrieve(root,'target_feature')
            self.assertIn('src.py',got)
            self.assertIn('701:',got)
            self.assertIn('def target_feature(): return 42',got)
            self.assertNotIn('unrelated line 0',got)
    def test_compaction_keeps_system_and_recent_messages(self):
        manager=ContextManager(context=4096,reserve=1024);messages=[{'role':'system','content':'SYSTEM'}]
        messages += [{'role':'user','content':f'turn {i} '+'x'*400} for i in range(40)]
        events=[];compacted=manager.compact(messages,on_compact=events.append);self.assertEqual(compacted[0],messages[0]);notice=next(m for m in compacted if 'earlier complete conversation turn(s) were omitted' in str(m.get('content')));self.assertEqual(notice['role'],'user');self.assertEqual(compacted[-1],messages[-1]);self.assertGreater(events[0]['omittedTurns'],0)
        self.assertEqual(len(messages),41,'request compaction must not mutate the canonical session history')
    def test_compaction_injects_checkpoint_and_discloses_its_inclusion(self):
        manager=ContextManager(context=4096,reserve=1024)
        messages=[{'role':'system','content':'SYSTEM'}]
        messages += [{'role':'user','content':f'turn {i} '+'x'*400} for i in range(40)]
        checkpoint={'current':{'task_id':'task-1','goal':'Finish the website','status':'active',
                               'workspace':'C:/fixture','settings':{'context':4096,'max_tokens':512},
                               'plan':[{'title':'Implement page','status':'pending'}],
                               'operations':[{'operation_id':'op-1','status':'completed','result_ref':'op-1'}]}}
        events=[]
        compacted=manager.compact(messages,output_tokens=512,on_compact=events.append,checkpoint=checkpoint)
        checkpoint_message=next(message for message in compacted if 'Active task continuity checkpoint' in str(message.get('content','')))
        self.assertIn('Finish the website',checkpoint_message['content'])
        self.assertIn('Implement page',checkpoint_message['content'])
        self.assertGreater(events[0]['omittedTurns'],0)
        self.assertTrue(events[0]['checkpointIncluded'])
        self.assertGreater(events[0]['checkpointEstimate'],0)
    def test_compaction_preserves_tool_call_and_result_pairs(self):
        manager=ContextManager(context=4096,reserve=1024)
        messages=[{'role':'system','content':'SYSTEM'}]
        messages += [{'role':'user','content':f'old turn {i} '+'x'*300} for i in range(40)]
        messages += [{'role':'assistant','tool_calls':[{'id':'c1','function':{'name':'read_file','arguments':{'path':'src/a.py'}}}]},{'role':'tool','tool_name':'read_file','content':'result '+('z'*400)}]
        messages += [{'role':'user','content':'current '+('y'*400)}]
        compacted=manager.compact(messages)
        assistant=next(m for m in compacted if m.get('tool_calls'))
        result=next(m for m in compacted if m.get('role')=='tool')
        self.assertEqual(assistant['tool_calls'][0]['function']['name'],result['tool_name'])
    def test_request_skills_fit_available_budget_and_full_catalog_stays_callable(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from desktop_engine import AgentEngine
        with TemporaryDirectory() as directory:
            engine=AgentEngine(Path(directory),context=8192,max_tokens=512)
            engine.state['registry'].skill_rows=[{'name':f'skill-{i}','description':('Workflow guidance '*40)} for i in range(100)]
            base=[{'role':'system','content':'system instructions'},{'role':'user','content':'task about skill-42'}]
            tools=engine._tools_for_request(base)
            description=next(t['function']['description'] for t in tools if t['function']['name']=='skill')
            size=engine.context.estimate(__import__('json').dumps({'messages':base,'tools':tools},ensure_ascii=False,separators=(',',':')))
            self.assertLessEqual(size,engine.context.budget(512))
            if 'Additional skills are available' in description:
                self.assertIn('action=list',description)
    def test_budget_refuses_to_silently_lower_context(self):
        manager=ContextManager(context=4096,reserve=1024);huge=[{'role':'system','content':'s'},{'role':'user','content':'x'*50000}]
        with self.assertRaisesRegex(ValueError,'context was not reduced'):manager.compact(huge)
    def test_8k_context_accepts_builtin_project_tools_and_512_output(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from desktop_engine import ToolRegistry, Workspace
        with TemporaryDirectory() as directory:
            tools = ToolRegistry(Workspace(Path(directory)), lambda *_: True).schemas
            messages = [{'role':'system','content':'system'}, {'role':'user','content':'Say hello.'}]
            self.assertEqual(ContextManager(context=8192).compact(messages, tools, 512), messages)
    def test_retrieval_never_reads_private_session_mirror(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root=Path(directory); private=root/'.agent-workbench'/'sessions';private.mkdir(parents=True)
            (private/'history.json').write_text('hello private session transcript',encoding='utf8')
            (root/'src.py').write_text('hello public source',encoding='utf8')
            got=ContextManager().retrieve(root,'hello private transcript')
            self.assertIn('src.py',got)
            self.assertNotIn('history.json',got)
            self.assertNotIn('private session transcript',got)
if __name__=='__main__':unittest.main()
