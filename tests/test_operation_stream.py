import sys
import tempfile
import unittest

from desktop_engine import AgentEngine, ToolRegistry, Workspace


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
            self.assertEqual([message.get("tool_call_id") for message in messages if message.get("role") == "tool"], ["call-1", "call-2"])
            self.assertIn("not executed", messages[-1]["content"].lower())
            self.assertTrue(any(event.get("type") == "stopped_after_operation" for event in events))


if __name__ == "__main__":
    unittest.main()
