import unittest
from desktop_engine import ContextManager, CONTEXT_MAX, MODEL, tool_result_for_context

class ContextTests(unittest.TestCase):
    def test_oversized_tool_results_keep_full_saved_result_retrievable_with_utf8_safe_cursor(self):
        import re
        result = ("start 雪\n" + "middle-" * 5000 + "\nfinish 雪")

        excerpt = tool_result_for_context(result, 512, "operation-42")

        self.assertLessEqual(len(excerpt.encode("utf-8")), 512)
        self.assertTrue(excerpt.startswith("start 雪"))
        self.assertTrue(excerpt.endswith("finish 雪"))
        self.assertIn("read_operation_result", excerpt)
        self.assertIn("operation-42", excerpt)
        match = re.search(r"offset=(\d+) length=(\d+)", excerpt)
        self.assertIsNotNone(match)
        offset, length = map(int, match.groups())
        head = excerpt.split("\n\n[Tool result excerpt:", 1)[0]
        tail = excerpt.rsplit("]\n\n", 1)[1]
        self.assertTrue(result.startswith(head))
        self.assertTrue(result.endswith(tail))
        self.assertEqual(offset, len(head))
        self.assertEqual(length, len(result) - len(head) - len(tail))

    def test_tool_results_under_budget_are_unchanged(self):
        self.assertEqual(tool_result_for_context("complete result 雪", 512, "op"), "complete result 雪")

    def test_target_model_and_full_context_are_defaults(self):
        self.assertEqual(MODEL, 'hf.co/mradermacher/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M')
        self.assertEqual(CONTEXT_MAX, 262144)
        self.assertEqual(ContextManager().context, 262144)
        self.assertIsNone(ContextManager().scan_file_limit)
        self.assertIsNone(ContextManager().scan_bytes_limit)
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
    def test_compaction_can_shrink_a_single_long_task_at_complete_tool_exchange_boundaries(self):
        manager=ContextManager(context=4096,reserve=1024)
        messages=[{'role':'system','content':'SYSTEM'},{'role':'user','content':'Build the requested project '+('goal '*50)}]
        for index in range(9):
            messages.append({'role':'assistant','tool_calls':[{'id':f'call-{index}','function':{'name':'read_file','arguments':{'path':f'src/{index}.py'}}}]})
            messages.append({'role':'tool','tool_name':'read_file','content':f'complete saved result {index} '+('detail '*220)})
        checkpoint={'current':{'task_id':'task-long','goal':'Build the requested project','status':'active',
                               'operations':[{'operation_id':f'op-{index}','name':'read_file','status':'completed','result_ref':f'op-{index}'} for index in range(9)]}}
        events=[]
        compacted=manager.compact(messages,output_tokens=512,on_compact=events.append,checkpoint=checkpoint)
        compact_calls=[message for message in compacted if message.get('tool_calls')]
        compact_results=[message for message in compacted if message.get('role')=='tool']
        self.assertGreater(events[0]['omittedOperationGroups'],0)
        self.assertLess(len(compact_calls),9)
        self.assertEqual(len(compact_calls),len(compact_results))
        self.assertIn('complete saved result 8',compact_results[-1]['content'])
        self.assertTrue(any('read_operation_result' in str(message.get('content','')) for message in compacted))
        self.assertEqual(len(messages),20,'compaction must leave canonical task history untouched')
    def test_compaction_budget_summarizes_oversized_checkpoint_without_changing_context(self):
        manager=ContextManager(context=4096,reserve=1024)
        checkpoint={'current':{'task_id':'active','goal':'Keep building the full project','status':'active',
                    'plan':[{'title':'Inspect','status':'done'},{'title':'Finish implementation','status':'in_progress'}],
                    'operations':[{'operation_id':f'op-{i}','name':'read_file','status':'completed','result_ref':f'op-{i}'} for i in range(200)],
                    'user_updates':[{'turn_id':f't{i}','instruction':f'Follow-up {i} '+('detail '*20)} for i in range(100)]}}
        messages=[{'role':'system','content':'system'},{'role':'user','content':'Continue this project '+('task '*30)}]
        events=[]
        compacted=manager.compact(messages,output_tokens=512,on_compact=events.append,checkpoint=checkpoint)
        self.assertTrue(events[0]['checkpointCompacted'])
        self.assertGreater(events[0]['checkpointOmissions']['operations'],0)
        self.assertTrue(any('Continue this project' in str(message.get('content','')) for message in compacted))
        self.assertEqual(manager.context,4096)
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
