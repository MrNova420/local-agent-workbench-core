import tempfile
import threading
import time
import unittest
from pathlib import Path

from desktop_engine import AgentEngine


class EngineToolBatchTests(unittest.TestCase):
    def test_repeated_identical_tool_calls_require_a_one_action_review_without_limiting_task(self):
        with tempfile.TemporaryDirectory() as root:
            reviews = []
            executed = []
            engine = AgentEngine(
                root,
                access="edit",
                approval_mode="auto",
                task_intent="discuss",
                approve_repeated_action=lambda name, args: reviews.append((name, args)) or True,
            )
            engine.state["registry"].run = lambda name, args, **kwargs: executed.append((name, args)) or "same result"
            call = {"id": "read", "function": {"name": "read_file", "arguments": {"path": "README.md"}}}
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [call]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [call]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [call]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [call]}},
                {"message": {"role": "assistant", "content": "I reviewed the unchanged result and finished."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            history = engine.run("Inspect the README")

            assert len(executed) == 4
            assert reviews == [("read_file", {"path": "README.md"})]
            decision = next(row for row in engine.state["task_checkpoint"]["current"]["decisions"]
                            if row["kind"] == "repeated_action_review")
            assert decision["decision"] == "approved"
            assert decision["approval_scope"] == "one_action"
            assert engine.state["task_checkpoint"]["current"]["status"] == "completed"
            assert "finished" in next(row for row in reversed(history) if row.get("role") == "assistant")["content"]

    def test_repeat_review_decline_blocks_only_that_exact_action_and_allows_other_work(self):
        with tempfile.TemporaryDirectory() as root:
            reviews = []
            executed = []
            engine = AgentEngine(
                root,
                access="edit",
                approval_mode="auto",
                task_intent="discuss",
                approve_repeated_action=lambda name, args: reviews.append((name, args)) or False,
            )
            engine.state["registry"].run = lambda name, args, **kwargs: executed.append((name, args)) or "same result"
            repeated = {"id": "read", "function": {"name": "read_file", "arguments": {"path": "README.md"}}}
            different = {"id": "status", "function": {"name": "git_status", "arguments": {}}}
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [repeated]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [repeated]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [repeated]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [repeated]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [repeated]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [different]}},
                {"message": {"role": "assistant", "content": "I used the other operation and finished."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            history = engine.run("Inspect the project")

            assert len(reviews) == 1
            assert executed.count(("read_file", {"path": "README.md"})) == 3
            assert ("git_status", {}) in executed
            blocked = [row for row in history if row.get("role") == "tool" and "user declined this repeated identical action" in row.get("content", "").casefold()]
            assert blocked
            assert engine.state["task_checkpoint"]["current"]["status"] == "completed"

    def test_independent_read_only_calls_execute_concurrently_and_keep_ordered_results(self):
        with tempfile.TemporaryDirectory() as root:
            engine = AgentEngine(root)
            calls = [
                {"id": "status", "function": {"name": "git_status", "arguments": {}}},
                {"id": "diff", "function": {"name": "git_diff", "arguments": {}}},
            ]
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": calls}},
                {"message": {"role": "assistant", "content": "Read both results."}},
            ]
            lock = threading.Lock(); active = 0; peak = 0
            def run_tool(name, args, **kwargs):
                nonlocal active, peak
                with lock:
                    active += 1; peak = max(peak, active)
                time.sleep(0.05)
                with lock: active -= 1
                return name
            engine.state["registry"].run = run_tool
            engine.runtime.stream = lambda *_args: responses.pop(0)

            history = engine.run("Inspect the project status and diff.")

            self.assertGreaterEqual(peak, 2)
            self.assertEqual([m["content"] for m in history if m.get("role") == "tool"], ["git_status", "git_diff"])

    def test_independent_file_writes_collect_approvals_before_parallel_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            approvals = []
            execution_count_at_approval = []
            lock = threading.Lock()
            active = 0
            peak = 0
            engine = AgentEngine(root, access="ask", approval_mode="per-action",
                                 approve=lambda name, args: (approvals.append((name, args["path"])),
                                                             execution_count_at_approval.append(executed[0]), True)[2])
            calls = [{"id": name, "function": {"name": "write_file", "arguments": {
                "path": f"{name}.txt", "content": name}}} for name in ("one", "two", "three")]
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": calls}},
                {"message": {"role": "assistant", "content": "All three independent files were written."}},
            ]
            original_run = engine.state["registry"].run
            executed = [0]

            def run_tool(name, args, **kwargs):
                nonlocal active, peak
                self.assertTrue(kwargs.get("approval_already_granted"))
                with lock:
                    active += 1
                    executed[0] += 1
                    peak = max(peak, active)
                try:
                    time.sleep(0.05)
                    return original_run(name, args, **kwargs)
                finally:
                    with lock:
                        active -= 1

            engine.state["registry"].run = run_tool
            engine.runtime.stream = lambda *_args: responses.pop(0)

            history = engine.run("Create three independent text files")

            self.assertEqual(approvals, [("write_file", "one.txt"), ("write_file", "two.txt"),
                                         ("write_file", "three.txt")])
            self.assertEqual(execution_count_at_approval, [0, 0, 0])
            self.assertGreaterEqual(peak, 2)
            self.assertEqual([Path(root, f"{name}.txt").read_text(encoding="utf-8")
                              for name in ("one", "two", "three")], ["one", "two", "three"])
            self.assertEqual([row["content"] for row in history if row.get("role") == "tool"],
                             ["Wrote one.txt", "Wrote two.txt", "Wrote three.txt"])

    def test_uncertain_mcp_result_stops_batch_then_returns_honest_final_status(self):
        with tempfile.TemporaryDirectory() as root:
            mcp_tool = {"type": "function", "function": {
                "name": "mcp__calendar__create_event", "description": "Create an event",
                "parameters": {"type": "object", "properties": {}}}}
            remote_calls = []
            def fail_remote_call(*args):
                remote_calls.append(args)
                raise TimeoutError("response timed out")
            engine = AgentEngine(root, access="edit", approval_mode="auto", task_intent="discuss", mcp_tools=[mcp_tool],
                                 mcp_call=fail_remote_call)
            events = []
            engine.event = events.append
            requests = []
            batch = {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "remote", "function": {"name": "mcp__calendar__create_event", "arguments": {}}},
                {"id": "plan", "function": {"name": "update_plan", "arguments": {"items": []}}},
            ]}}
            responses = [batch, {"message": {"role": "assistant", "content": (
                "I could not confirm whether the calendar event was created because the server timed out. "
                "I did not retry it; please inspect the calendar before trying again."
            )}}]
            def stream(*args):
                requests.append({"messages": args[1], "tools": args[0]["request_tools"]})
                return responses.pop(0)
            engine.runtime.stream = stream

            history = engine.run("Implement the requested change")

            assert len(requests) == 2
            assert len(remote_calls) == 1
            assert any((tool.get("function") or {}).get("name") == "mcp__calendar__create_event"
                       for tool in requests[1]["tools"])
            done = [row for row in events if row.get("type") == "tool_done"]
            assert [row.get("status") for row in done] == ["uncertain", "blocked"]
            assert done[1]["name"] == "update_plan"
            assert events[-1]["type"] == "complete"
            tool_results = [row for row in history if row.get("role") == "tool"]
            assert len(tool_results) == 2
            assert "Not executed" in tool_results[1]["content"]
            final = next(row for row in reversed(history) if row.get("role") == "assistant")
            assert "could not confirm" in final["content"]
            checkpoint = engine.state["task_checkpoint"]["current"]
            uncertain = next(row for row in checkpoint["operations"] if row["name"] == "mcp__calendar__create_event")
            assert uncertain["status"] == "uncertain"
            assert checkpoint["uncertain_operation"]["name"] == "mcp__calendar__create_event"
            assert checkpoint["status"] == "stopped"
            assert checkpoint["workflow_outcome"] == "blocked"
            assert checkpoint["verification"]["status"] == "blocked"

    def test_mcp_retry_after_uncertainty_requires_fresh_approval_even_in_auto_mode(self):
        with tempfile.TemporaryDirectory() as root:
            mcp_tool = {"type": "function", "function": {
                "name": "mcp__calendar__create_event", "description": "Create an event",
                "parameters": {"type": "object", "properties": {}}}}
            remote_calls = []
            approvals = []
            def remote_call(*args):
                remote_calls.append(args)
                if len(remote_calls) == 1:
                    raise TimeoutError("response timed out")
                return "created event-42"
            def approve(name, args):
                approvals.append((name, args))
                return True
            engine = AgentEngine(root, access="edit", approval_mode="auto", task_intent="discuss",
                                 mcp_tools=[mcp_tool], mcp_call=remote_call, approve=approve)
            calls = [{"id": "create", "function": {
                "name": "mcp__calendar__create_event", "arguments": {"title": "Review"}}}]
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": calls}},
                {"message": {"role": "assistant", "content": "", "tool_calls": calls}},
                {"message": {"role": "assistant", "content": "The retry was explicitly approved; inspect the calendar for a possible duplicate."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            history = engine.run("Check whether the event was created")

            assert len(remote_calls) == 2
            assert approvals == [("mcp__calendar__create_event", {"title": "Review"})]
            decisions = engine.state["task_checkpoint"]["current"]["decisions"]
            retry_decision = next(row for row in decisions if row["kind"] == "uncertain_mcp_retry")
            assert retry_decision["decision"] == "approved"
            assert retry_decision["approval_scope"] == "one_action"
            assert engine.state["task_checkpoint"]["current"]["workflow_outcome"] == "discussion"
            assert "possible duplicate" in next(row for row in reversed(history) if row.get("role") == "assistant")["content"]

    def test_finish_after_operation_drains_already_started_read_batch_before_stopping(self):
        with tempfile.TemporaryDirectory() as root:
            engine = AgentEngine(root)
            calls = [
                {"id": "status", "function": {"name": "git_status", "arguments": {}}},
                {"id": "diff", "function": {"name": "git_diff", "arguments": {}}},
            ]
            engine.runtime.stream = lambda *_args: {"message": {"role": "assistant", "content": "", "tool_calls": calls}}
            lock = threading.Lock()
            active = 0

            def run_tool(name, args, **kwargs):
                nonlocal active
                with lock:
                    active += 1
                    should_stop = active == 2
                if should_stop:
                    engine.finish_after_operation()
                time.sleep(0.03)
                return name

            engine.state["registry"].run = run_tool
            events = []
            engine.event = events.append
            history = engine.run("Inspect status and diff")

            results = [row["content"] for row in history if row.get("role") == "tool"]
            assert results == ["git_status", "git_diff"]
            assert events[-1]["type"] == "stopped_after_operation"

    def test_reports_every_tool_call_in_large_response_without_dropping_any(self):
        with tempfile.TemporaryDirectory() as root:
            engine = AgentEngine(root)
            events = []
            engine.event = events.append
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
                    captured["messages"] = messages
                    return batches.pop(0)
                return batches.pop(0)

            engine.runtime.stream = stream
            full_history=engine.run("Make a plan")
            tool_messages = [m for m in captured["messages"] if m.get("role") == "tool"]
            self.assertEqual(len(tool_messages), 17)
            self.assertTrue(all(m["tool_name"] == "update_plan" for m in tool_messages))
            self.assertEqual(len([m for m in full_history if m.get("role") == "tool"]), 17)
            batch_event = next(row for row in events if row.get("type") == "tool_batch")
            self.assertEqual(batch_event["request_index"], 1)


if __name__ == "__main__":
    unittest.main()
