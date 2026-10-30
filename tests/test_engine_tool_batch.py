import tempfile
import unittest
from pathlib import Path

from desktop_engine import AgentEngine


class EngineToolBatchTests(unittest.TestCase):
    def test_reports_every_tool_call_in_large_response_without_dropping_any(self):
        with tempfile.TemporaryDirectory() as root:
            engine = AgentEngine(root)
            captured = {}
            batches = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"call-{i}", "function": {"name": "update_plan", "arguments": {"items": []}}}
                    for i in range(17)
                ]}},
                {"message": {"role": "assistant", "content": "done"}},
            ]

            def stream(state, messages, cancel, emit):
                if len(batches) == 1:
                    return batches.pop(0)
                captured["messages"] = messages
                return batches.pop(0)

            engine.runtime.stream = stream
            engine.run("Make a plan")
            tool_messages = [m for m in captured["messages"] if m.get("role") == "tool"]
            self.assertEqual(len(tool_messages), 17)
            self.assertEqual(len({m["tool_call_id"] for m in tool_messages}), 17)


if __name__ == "__main__":
    unittest.main()
