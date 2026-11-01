import sys
import tempfile
import unittest
from pathlib import Path

from desktop_engine import AgentEngine, CONTEXT_MAX, ToolRegistry, Workspace


class OperationStreamTests(unittest.TestCase):
    def _run_one_tool(self, tool_name, arguments, tool_runner=None):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            if tool_runner is not None:
                engine.state["registry"].run = tool_runner
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-1", "function": {"name": tool_name, "arguments": arguments}}
                ]}},
                {"message": {"role": "assistant", "content": "Finished."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)
            engine.run("Run one tool")
            return next(event for event in events if event.get("type") == "tool_done")

    def test_invalid_patch_is_recorded_as_failed_activity(self):
        event = self._run_one_tool("apply_patch", {"patch": "no diff here"})

        self.assertEqual(event["status"], "failed")
        self.assertIn("Invalid unified diff", event["result"])

    def test_tool_exception_is_recorded_as_failed_activity(self):
        def explode(*_args):
            raise OSError("disk unavailable")

        event = self._run_one_tool("write_file", {"path": "x.txt", "content": "x"}, explode)

        self.assertEqual(event["status"], "failed")
        self.assertIn("Tool error: OSError", event["result"])

    def test_error_like_file_contents_remain_completed(self):
        event = self._run_one_tool("read_file", {"path": "x.txt"}, lambda *_: "Invalid unified diff is an example")

        self.assertEqual(event["status"], "completed")

    def test_command_streams_unicode_output_and_returns_all_output(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True, events.append)
            script = "import sys; sys.stdout.buffer.write(('雪' * 20000 + '\\n').encode('utf-8')); print('stderr marker', file=sys.stderr)"

            result = registry._command({"argv": [sys.executable, "-c", script], "timeout_seconds": 10})

            stdout_events = [event["text"] for event in events if event.get("type") == "operation_output" and event.get("stream") == "stdout"]
            stderr_events = [event["text"] for event in events if event.get("type") == "operation_output" and event.get("stream") == "stderr"]
            self.assertEqual("".join(stdout_events).replace("\r\n", "\n"), "雪" * 20000 + "\n")
            self.assertIn("stderr marker", "".join(stderr_events))
            self.assertIn("雪" * 20000, result)
            self.assertGreater(len(result), 12000)

    def test_tool_activity_has_one_identity_and_keeps_complete_result(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            result_text = "λ" * 20000
            engine.state["registry"].run = lambda _name, _args: result_text
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-1", "function": {"name": "read_file", "arguments": {"path": "x.txt"}}}
                ]}},
                {"message": {"role": "assistant", "content": "Finished."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            messages = engine.run("Read it")

            started = next(event for event in events if event.get("type") == "tool_start")
            completed = next(event for event in events if event.get("type") == "tool_done")
            batch = next(event for event in events if event.get("type") == "tool_batch")
            self.assertEqual(started["operationId"], completed["operationId"])
            self.assertEqual(completed["result"], result_text)
            self.assertEqual(next(message["content"] for message in messages if message.get("role") == "tool"), result_text)
            self.assertEqual(started["toolCallId"], "call-1")
            self.assertEqual(batch["calls"], [{"id": "call-1", "name": "read_file"}])

    def test_finish_after_active_tool_stops_before_the_next_model_call(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            calls = []
            engine.state["registry"].run = lambda *_: (engine.finish_after_operation(), calls.append("tool"), "saved")[2]
            responses = [{"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call-1", "function": {"name": "write_file", "arguments": {"path": "x", "content": "saved"}}},
                {"id": "call-2", "function": {"name": "write_file", "arguments": {"path": "y", "content": "pending"}}},
            ]}}]
            engine.runtime.stream = lambda *_args: (calls.append("model"), responses.pop(0))[1]

            messages = engine.run("Write both")

            self.assertEqual(calls, ["model", "tool"])
            self.assertEqual([message.get("tool_name") for message in messages if message.get("role") == "tool"], ["write_file", "write_file"])
            self.assertIn("not executed", messages[-1]["content"].lower())
            self.assertTrue(any(event.get("type") == "stopped_after_operation" for event in events))

    def test_explicit_build_task_continues_to_tools_after_plan_only_response(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            calls = []
            responses = [
                {"message": {"role": "assistant", "content": "I will create the page and then verify it."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-write", "function": {"name": "write_file", "arguments": {"path": "index.html", "content": "<h1>Ready</h1>"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-inspect", "function": {"name": "read_file", "arguments": {"path": "index.html"}}}
                ]}},
                {"message": {"role": "assistant", "content": "The page is ready."}},
            ]
            def stream(*_args):
                calls.append("model")
                return responses.pop(0)
            engine.runtime.stream = stream

            messages = engine.run("Build a simple website in this project.")

            self.assertEqual(calls, ["model", "model", "model", "model"])
            self.assertEqual((Path(root) / "index.html").read_text(encoding="utf-8"), "<h1>Ready</h1>")
            self.assertTrue(any(event.get("type") == "tool_done" and event.get("status") == "completed" for event in events))
            self.assertTrue(any(event.get("type") == "complete" for event in events))
            completion=next(event for event in events if event.get("type")=="complete")
            self.assertEqual(completion["verification"]["status"],"inspected")
            self.assertEqual(completion["verification"]["steps"],[{"kind":"inspection","tool":"read_file","result":"completed"}])
            self.assertEqual(completion["verification"]["contract"]["project_change"], "observed")
            self.assertEqual(completion["verification"]["contract"]["post_change_inspection"], "completed")
            self.assertEqual(messages[-1]["content"], "The page is ready.")

    def test_implementation_with_command_but_no_review_continues_for_inspection(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            actions = []
            engine.state["registry"].run = lambda name, _args: (
                actions.append(name) or {
                    "write_file": "Wrote app.py",
                    "run_command": "Checks completed [exit 0]",
                    "read_file": "Reviewed app.py",
                }[name]
            )
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "write", "function": {"name": "write_file", "arguments": {"path": "app.py", "content": "pass"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "command", "function": {"name": "run_command", "arguments": {"argv": ["python", "-m", "compileall", "."]}}}
                ]}},
                {"message": {"role": "assistant", "content": "The command completed successfully."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect", "function": {"name": "read_file", "arguments": {"path": "app.py"}}}
                ]}},
                {"message": {"role": "assistant", "content": "The change is implemented and reviewed."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Implement a small application change.")

            self.assertEqual(actions, ["write_file", "run_command", "read_file"])
            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["verification"]["contract"]["post_change_inspection"], "completed")
            self.assertEqual(completion["verification"]["contract"]["approved_command_results"], ["completed"])
            self.assertFalse(completion["verification"]["contract"]["command_success_is_test_pass"])

    def test_implementation_that_uses_its_continuation_without_a_change_is_not_discussion(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            responses = [
                {"message": {"role": "assistant", "content": "I will implement the requested change."}},
                {"message": {"role": "assistant", "content": "I could not make the change."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Implement a small application change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["workflowOutcome"], "implementation_incomplete")
            self.assertEqual(completion["verification"]["status"], "not_implemented")

    def test_plan_only_request_does_not_force_a_tool_followup(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append("model") or {"message": {"role": "assistant", "content": "Here is the plan."}})

            engine.run("Plan only: describe how to build a simple website.")

            self.assertEqual(calls, ["model"])

    def test_denied_action_is_not_retried_by_implementation_continuation(self):
        with tempfile.TemporaryDirectory() as root:
            engine=AgentEngine(root,access="ask",approve=lambda *_:False)
            responses=[
                {"message":{"role":"assistant","content":"","tool_calls":[{"id":"denied-write","function":{"name":"write_file","arguments":{"path":"no.txt","content":"no"}}}]}},
                {"message":{"role":"assistant","content":"The file action was denied, so no change was made."}},
            ]
            calls=[]
            engine.runtime.stream=lambda *_args:(calls.append("model") or responses.pop(0))
            engine.run("Build a small project file.")
            self.assertEqual(calls,["model","model"])
            self.assertFalse((Path(root)/"no.txt").exists())

    def test_executes_more_than_sixteen_model_requested_tool_actions(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            engine.state["registry"].run = lambda *_args: "completed"
            calls = [{"id": f"call-{index}", "function": {"name": "update_plan", "arguments": {"items": []}}} for index in range(17)]
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": calls}},
                {"message": {"role": "assistant", "content": "All requested operations are complete."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Complete the requested project work.")

            completed = [event for event in events if event.get("type") == "tool_done" and event.get("status") == "completed"]
            self.assertEqual(len(completed), 17)

    def test_run_can_continue_beyond_thirty_two_tool_turns(self):
        with tempfile.TemporaryDirectory() as root:
            engine = AgentEngine(root, context=CONTEXT_MAX, access="edit")
            engine.state["registry"].run = lambda *_args: "completed"
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"call-{index}", "function": {"name": "update_plan", "arguments": {"items": []}}}
                ]}}
                for index in range(34)
            ]
            responses.append({"message": {"role": "assistant", "content": "Verified after 34 operations."}})
            engine.runtime.stream = lambda *_args: responses.pop(0)

            messages = engine.run("Handle these 34 project operations in order.")

            self.assertEqual(messages[-1]["content"], "Verified after 34 operations.")
            self.assertEqual(responses, [])


if __name__ == "__main__":
    unittest.main()
