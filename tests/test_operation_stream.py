import sys
import tempfile
import unittest

from desktop_engine import AgentEngine, ToolRegistry, Workspace


class OperationStreamTests(unittest.TestCase):
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
            self.assertEqual(started["operationId"], completed["operationId"])
            self.assertEqual(completed["result"], result_text)
            self.assertEqual(next(message["content"] for message in messages if message.get("role") == "tool"), result_text)


if __name__ == "__main__":
    unittest.main()
