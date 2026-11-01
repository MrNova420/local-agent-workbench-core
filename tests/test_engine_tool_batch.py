import tempfile
import unittest
from pathlib import Path

from desktop_engine import AgentEngine


class EngineToolBatchTests(unittest.TestCase):
    def test_uncertain_mcp_result_stops_remaining_batched_actions(self):
        with tempfile.TemporaryDirectory() as root:
            mcp_tool = {"type": "function", "function": {
                "name": "mcp__calendar__create_event", "description": "Create an event",
                "parameters": {"type": "object", "properties": {}}}}
            remote_calls = []
            def fail_remote_call(*args):
                remote_calls.append(args)
                raise TimeoutError("response timed out")
            engine = AgentEngine(root, access="edit", approval_mode="auto", mcp_tools=[mcp_tool],
                                 mcp_call=fail_remote_call)
            events = []
            engine.event = events.append
            requests = []
            batch = {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "remote", "function": {"name": "mcp__calendar__create_event", "arguments": {}}},
                {"id": "plan", "function": {"name": "update_plan", "arguments": {"items": []}}},
            ]}}
            engine.runtime.stream = lambda *args: (requests.append(args[1]) or batch)

            history = engine.run("Implement the requested change")

            assert len(requests) == 1
            assert len(remote_calls) == 1
            done = [row for row in events if row.get("type") == "tool_done"]
            assert [row.get("status") for row in done] == ["uncertain", "blocked"]
            assert done[1]["name"] == "update_plan"
            assert events[-1]["type"] == "error"
            tool_results = [row for row in history if row.get("role") == "tool"]
            assert len(tool_results) == 2
            assert "Not executed" in tool_results[1]["content"]
            checkpoint = engine.state["task_checkpoint"]["current"]
            uncertain = next(row for row in checkpoint["operations"] if row["name"] == "mcp__calendar__create_event")
            assert uncertain["status"] == "uncertain"
            assert checkpoint["uncertain_operation"]["name"] == "mcp__calendar__create_event"

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
            full_history=engine.run("Make a plan")
            tool_messages = [m for m in captured["messages"] if m.get("role") == "tool"]
            self.assertEqual(len(tool_messages), 17)
            self.assertTrue(all(m["tool_name"] == "update_plan" for m in tool_messages))
            self.assertEqual(len([m for m in full_history if m.get("role") == "tool"]), 17)


if __name__ == "__main__":
    unittest.main()
