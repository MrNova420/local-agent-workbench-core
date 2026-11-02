import os
import copy
import sys
import tempfile
import unittest
from pathlib import Path

from desktop_engine import AgentEngine, CONTEXT_MAX, ToolRegistry, Workspace, _needs_implementation_continuation, _tool_activity_status


class OperationStreamTests(unittest.TestCase):
    def test_steering_during_final_response_is_inserted_before_same_task_continues(self):
        with tempfile.TemporaryDirectory() as root:
            requests = []
            responses = [
                {"message": {"role": "assistant", "content": "I am still working through the task."}},
                {"message": {"role": "assistant", "content": "I incorporated the user's update."}},
            ]
            pending = [{"id": "steer-1", "instruction": "Keep the existing style"}]

            def steer(messages, finishing):
                if not finishing or not pending:
                    return False
                update = pending.pop(0)
                messages.append({"role": "user", "content": update["instruction"], "turn_id": "turn-1", "steering_id": update["id"]})
                return True

            engine = AgentEngine(root, access="plan", task_intent="plan", steering_handler=steer)
            def stream(_state, messages, *_args):
                requests.append(copy.deepcopy(messages))
                return responses.pop(0)
            engine.runtime.stream = stream

            result = engine.run("Review this project")

            self.assertEqual(len(requests), 2)
            self.assertTrue(any(row.get("steering_id") == "steer-1" for row in requests[1]))
            self.assertEqual(result[-1]["content"], "I incorporated the user's update.")

    def test_continuation_uses_verified_work_state_not_model_wording(self):
        self.assertTrue(_needs_implementation_continuation(True, "edit", False, False, False, False))
        self.assertTrue(_needs_implementation_continuation(True, "edit", False, True, False, True))
        self.assertTrue(_needs_implementation_continuation(True, "edit", False, True, True, False))
        self.assertFalse(_needs_implementation_continuation(True, "plan", False, False, False, False))
        self.assertFalse(_needs_implementation_continuation(True, "edit", True, False, False, True))
        self.assertFalse(_needs_implementation_continuation(True, "edit", False, True, False, False))

    def test_append_file_result_has_truthful_activity_status(self):
        self.assertEqual(_tool_activity_status("append_file", "Appended src/generated.ts"), "completed")
        self.assertEqual(_tool_activity_status("append_file", "Cannot append to missing.ts: no file"), "failed")

    def test_cancelled_or_timed_out_command_is_marked_uncertain_about_partial_effects(self):
        self.assertEqual(_tool_activity_status("run_command", "output\n[command cancelled by user]"), "uncertain")
        self.assertEqual(_tool_activity_status("run_shell", "partial output\n[command timed out after 30s]"), "uncertain")
        self.assertEqual(_tool_activity_status("run_command", "Cancelled before this tool action ran."), "cancelled")

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
        def explode(*_args, **_kwargs):
            raise OSError("disk unavailable")

        event = self._run_one_tool("write_file", {"path": "x.txt", "content": "x"}, explode)

        self.assertEqual(event["status"], "failed")
        self.assertIn("Tool error: OSError", event["result"])

    def test_model_receives_failed_tool_result_and_can_retry_in_same_task(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "present.txt").write_text("recovered", encoding="utf-8")
            events = []
            requests = []
            engine = AgentEngine(root, access="edit", event=events.append)
            responses = [
                {"message": {"role": "assistant", "tool_calls": [
                    {"id": "call-missing", "function": {"name": "read_file", "arguments": {"path": "missing.txt"}}}
                ]}},
                {"message": {"role": "assistant", "tool_calls": [
                    {"id": "call-retry", "function": {"name": "read_file", "arguments": {"path": "present.txt"}}}
                ]}},
                {"message": {"role": "assistant", "content": "Recovered and continued."}},
            ]

            def stream(_state, messages, *_args):
                requests.append(copy.deepcopy(messages))
                return responses.pop(0)

            engine.runtime.stream = stream
            result = engine.run("Read the project file")

            self.assertEqual(len(requests), 3)
            self.assertTrue(any("Tool error: FileNotFoundError" in str(row.get("content", "")) for row in requests[1]))
            tool_results = [row for row in requests[2] if row.get("role") == "tool"]
            self.assertTrue(any(row.get("content") == "recovered" for row in tool_results))
            completed = [event for event in events if event.get("type") == "tool_done"]
            self.assertEqual([event["status"] for event in completed], ["failed", "completed"])
            self.assertEqual(result[-1]["content"], "Recovered and continued.")

    def test_append_file_builds_large_unicode_files_in_multiple_atomic_chunks(self):
        with tempfile.TemporaryDirectory() as root:
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            self.assertIn("write_file", {row["function"]["name"] for row in registry.schemas})
            self.assertIn("append_file", {row["function"]["name"] for row in registry.schemas})
            self.assertEqual(registry.run("write_file", {"path": "large.txt", "content": "start\n"}), "Wrote large.txt")
            self.assertEqual(registry.run("append_file", {"path": "large.txt", "content": "雪\n" * 12000}), "Appended large.txt")
            self.assertEqual(Path(root, "large.txt").read_text(encoding="utf-8"), "start\n" + "雪\n" * 12000)

    def test_atomic_file_replace_retries_transient_windows_sharing_violation(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "large.txt")
            path.write_text("before", encoding="utf-8")
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            real_replace = os.replace
            attempts = []

            def replace_with_one_transient_lock(source, destination):
                attempts.append(1)
                if len(attempts) == 1:
                    raise PermissionError("temporary Windows sharing violation")
                return real_replace(source, destination)

            with patch("desktop_engine.os.replace", side_effect=replace_with_one_transient_lock):
                self.assertEqual(registry.run("append_file", {"path": "large.txt", "content": "after"}), "Appended large.txt")
            self.assertEqual(len(attempts), 2)
            self.assertEqual(path.read_text(encoding="utf-8"), "beforeafter")

    def test_append_file_requires_an_existing_project_file_and_respects_access(self):
        with tempfile.TemporaryDirectory() as root:
            editable = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            self.assertIn("does not exist", editable.run("append_file", {"path": "missing.txt", "content": "x"}))
            self.assertFalse(Path(root, "missing.txt").exists())
            Path(root, "existing.txt").write_text("kept", encoding="utf-8")
            ask = ToolRegistry(Workspace(root, "ask"), lambda *_: False)
            self.assertEqual(ask.run("append_file", {"path": "existing.txt", "content": "x"}), "Denied by user.")
            self.assertEqual(Path(root, "existing.txt").read_text(encoding="utf-8"), "kept")
            readonly = ToolRegistry(Workspace(root, "plan"), lambda *_: True)
            self.assertEqual(readonly.run("append_file", {"path": "missing.txt", "content": "x"}), "Blocked: project is in read-only plan mode.")

    def test_error_like_file_contents_remain_completed(self):
        event = self._run_one_tool("read_file", {"path": "x.txt"}, lambda *_args, **_kwargs: "Invalid unified diff is an example")

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
            engine.state["registry"].run = lambda _name, _args, **_kwargs: result_text
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
            engine.state["registry"].run = lambda *_args, **_kwargs: (engine.finish_after_operation(), calls.append("tool"), "saved")[2]
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

    def test_automatic_plan_phase_continues_into_project_implementation(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            calls = []
            responses = [
                {"message": {"role": "assistant", "content": "I have a plan.", "tool_calls": [
                    {"id": "call-plan", "function": {"name": "update_plan", "arguments": {"task_mode": "plan", "items": [{"title": "Create and inspect the page", "status": "pending"}]}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-write", "function": {"name": "write_file", "arguments": {"path": "index.html", "content": "<h1>Ready</h1>"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-inspect", "function": {"name": "read_file", "arguments": {"path": "index.html"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-finish-plan", "function": {"name": "update_plan", "arguments": {"task_mode": "implement", "items": [{"title": "Create and inspect the page", "status": "completed"}]}}}
                ]}},
                {"message": {"role": "assistant", "content": "The page is ready."}},
            ]
            def stream(*_args):
                calls.append("model")
                return responses.pop(0)
            engine.runtime.stream = stream

            messages = engine.run("Build a simple website in this project.")

            self.assertEqual(calls, ["model", "model", "model", "model", "model"])
            self.assertEqual((Path(root) / "index.html").read_text(encoding="utf-8"), "<h1>Ready</h1>")
            self.assertTrue(any(event.get("type") == "tool_done" and event.get("status") == "completed" for event in events))
            self.assertTrue(any(event.get("type") == "complete" for event in events))
            completion=next(event for event in events if event.get("type")=="complete")
            self.assertTrue(completion["implementationRequested"])
            self.assertEqual(completion["verification"]["status"],"inspected")
            self.assertEqual(completion["verification"]["steps"],[{"kind":"inspection","tool":"read_file","arguments":{"path":"index.html"},"result":"completed"}])
            self.assertEqual(completion["verification"]["contract"]["project_change"], "observed")
            self.assertEqual(completion["verification"]["contract"]["post_change_inspection"], "completed")
            self.assertEqual(messages[-1]["content"], "The page is ready.")

    def test_implementation_with_command_but_no_review_continues_for_inspection(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            actions = []
            engine.state["registry"].run = lambda name, _args, **_kwargs: (
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

    def test_command_only_implementation_records_possible_effects_and_inspects_after_command(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", approve=lambda *_args: True, event=events.append)
            actions = []
            engine.state["registry"].run = lambda name, args, **_kwargs: (
                actions.append((name, args)) or {
                    "run_command": "Generated project files [exit 0]",
                    "read_file": "Observed generated file contents",
                }[name]
            )
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "command", "function": {"name": "run_command", "arguments": {"argv": ["scaffold", "site"]}}}
                ]}},
                {"message": {"role": "assistant", "content": "The project is ready."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect", "function": {"name": "read_file", "arguments": {"path": "index.html"}}}
                ]}},
                {"message": {"role": "assistant", "content": "I inspected the generated file."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Implement a small website using the project scaffolder.")

            self.assertEqual([name for name, _args in actions], ["run_command", "read_file"])
            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["workflowOutcome"], "implementation_unverified")
            contract = completion["verification"]["contract"]
            self.assertEqual(contract["project_change"], "not_observed")
            self.assertTrue(contract["command_side_effects_possible"])
            self.assertEqual(contract["post_command_inspection"], "completed")
            self.assertEqual(contract["approved_command_results"], ["completed"])
            self.assertEqual(completion["verification"]["steps"][0]["arguments"]["argv"], ["scaffold", "site"])
            self.assertEqual(completion["verification"]["steps"][1]["arguments"]["path"], "index.html")

    def test_failed_implementation_command_is_inspected_for_partial_effects(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", approve=lambda *_args: True, event=events.append)
            engine.state["registry"].run = lambda name, _args, **_kwargs: {
                "run_command": "Generated package.json, then build failed [exit 1]",
                "git_status": " M package.json",
            }[name]
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "command", "function": {"name": "run_command", "arguments": {"argv": ["builder"]}}}
                ]}},
                {"message": {"role": "assistant", "content": "The build failed."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect", "function": {"name": "git_status", "arguments": {}}}
                ]}},
                {"message": {"role": "assistant", "content": "I inspected the workspace after the failure."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Implement the requested package change and build it.")

            completion = next(event for event in events if event.get("type") == "complete")
            contract = completion["verification"]["contract"]
            self.assertEqual(completion["workflowOutcome"], "command_failed")
            self.assertTrue(contract["command_side_effects_possible"])
            self.assertEqual(contract["post_command_inspection"], "completed")
            self.assertEqual(contract["approved_command_results"], ["failed"])

    def test_denied_implementation_command_does_not_claim_possible_side_effects(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="ask", approve=lambda *_args: False, event=events.append)
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "command", "function": {"name": "run_command", "arguments": {"argv": ["builder"]}}}
                ]}},
                {"message": {"role": "assistant", "content": "The command was denied."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Implement the requested package change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["workflowOutcome"], "blocked")
            self.assertFalse(completion["verification"]["contract"]["command_side_effects_possible"])
            self.assertEqual(completion["verification"]["contract"]["approved_command_results"], [])

    def test_implementation_that_uses_its_continuation_without_a_change_is_not_discussion(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append, task_intent="implement")
            responses = [
                {"message": {"role": "assistant", "content": "I will implement the requested change."}},
                {"message": {"role": "assistant", "content": "I could not make the change."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Implement a small application change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["workflowOutcome"], "implementation_incomplete")
            self.assertEqual(completion["verification"]["status"], "implementation_incomplete")

    def test_open_agent_plan_continues_across_planning_until_work_and_plan_are_complete(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan", "function": {"name": "update_plan", "arguments": {
                        "items": [{"title": "Create and inspect the project file", "status": "pending"}]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "I have a plan; now I will implement it."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "write", "function": {"name": "write_file", "arguments": {"path": "app.py", "content": "print('ready')\\n"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect", "function": {"name": "read_file", "arguments": {"path": "app.py"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "finish-plan", "function": {"name": "update_plan", "arguments": {
                        "items": [{"title": "Create and inspect the project file", "status": "completed"}]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "The project file is implemented and inspected."}},
            ]
            calls = []
            request_snapshots = []
            def stream(_state, request_messages, *_args):
                calls.append(1)
                request_snapshots.append(copy.deepcopy(request_messages))
                return responses.pop(0)
            engine.runtime.stream = stream

            engine.run("Implement a small project file.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 6)
            continuation_system = request_snapshots[2][0]["content"]
            self.assertIn("Create and inspect the project file", continuation_system)
            self.assertIn("next open step", continuation_system)
            self.assertIn("Do not re-plan unless new evidence changes the work", continuation_system)
            self.assertEqual((Path(root) / "app.py").read_text(encoding="utf-8"), "print('ready')\\n")
            self.assertEqual(completion["workflowOutcome"], "inspected")
            self.assertFalse(any(event.get("type") == "implementation_incomplete" for event in events))

    def test_automatic_direct_write_gets_a_live_plan_without_prompt_keyword_matching(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append, task_intent="auto")
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "write", "function": {"name": "write_file", "arguments": {"path": "app.py", "content": "print('ready')\\n"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect", "function": {"name": "read_file", "arguments": {"path": "app.py"}}}
                ]}},
                {"message": {"role": "assistant", "content": "The requested file is written and inspected."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Make this usable")

            plan_index = next(index for index, event in enumerate(events)
                              if event.get("type") == "plan" and event.get("source") == "workbench")
            write_index = next(index for index, event in enumerate(events)
                               if event.get("type") == "tool_start" and event.get("name") == "write_file")
            completion = next(event for event in events if event.get("type") == "complete")
            self.assertLess(plan_index, write_index)
            self.assertEqual((Path(root) / "app.py").read_text(encoding="utf-8"), "print('ready')\\n")
            self.assertEqual(completion["workflowOutcome"], "inspected")

    def test_automatic_no_action_is_persisted_as_observed_outcome(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append, task_intent="auto")
            engine.runtime.stream = lambda *_args: {"message": {"role": "assistant", "content": "Here is the explanation."}}

            engine.run("What does this project do?")

            completion = next(event for event in events if event.get("type") == "complete")
            checkpoint = engine.state["task_checkpoint"]["current"]
            self.assertEqual(completion["workflowOutcome"], "no_action")
            self.assertEqual(checkpoint["workflow_outcome"], "no_action")
            self.assertFalse(any(event.get("type") == "tool_start" for event in events))

    def test_repeated_identical_plan_tool_calls_are_detected_as_no_progress(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            item = {"title": "Implement the requested project", "status": "pending"}
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan", "function": {"name": "update_plan", "arguments": {"items": [item]}}}
                ]}},
                {"message": {"role": "assistant", "content": "I am still preparing the same plan."}},
                {"message": {"role": "assistant", "content": "I am still preparing the same plan."}},
            ]
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append(1) or responses.pop(0))

            engine.run("Implement a small project file.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 3)
            self.assertEqual(completion["workflowOutcome"], "implementation_incomplete")
            self.assertIn("no new observable tool, plan, or verification progress", completion["verification"]["incomplete_reason"])

    def test_pending_plan_expansion_alone_does_not_keep_automatic_run_planning(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan", "function": {"name": "update_plan", "arguments": {
                        "items": [{"title": "Implement the requested project", "status": "pending"}],
                        "decisions": ["Consider a second architecture option"]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "The initial plan is ready."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "expand", "function": {"name": "update_plan", "arguments": {
                        "add_items": [{"title": "Refine another implementation detail", "status": "pending"}],
                        "decisions": ["Consider a third architecture option"]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "I am still refining the plan."}},
            ]
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append(1) or responses.pop(0))

            engine.run("Implement a small project change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 4)
            self.assertEqual(len(engine.state["registry"].plan), 2)
            self.assertEqual(completion["workflowOutcome"], "implementation_incomplete")
            self.assertIn("no new observable tool, plan, or verification progress", completion["verification"]["incomplete_reason"])

    def test_repeated_plan_only_tool_calls_return_to_implementation_then_stop_if_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            plan_call = lambda call_id, title: {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": call_id, "function": {"name": "update_plan", "arguments": {
                    "add_items": [{"title": title, "status": "pending"}]}}
                }
            ]}}
            responses = [
                plan_call("plan-1", "Implement the requested project"),
                plan_call("plan-2", "Consider another architecture option"),
                plan_call("plan-3", "Consider one more architecture option"),
            ]
            calls = []
            request_snapshots = []
            def stream(_state, request_messages, *_args):
                calls.append(1)
                request_snapshots.append(copy.deepcopy(request_messages))
                return responses.pop(0)
            engine.runtime.stream = stream

            engine.run("Implement a small project change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 3)
            self.assertIn("Start the next open step now", request_snapshots[2][0]["content"])
            self.assertEqual(completion["workflowOutcome"], "implementation_incomplete")
            self.assertTrue(engine.state["registry"].plan)

    def test_plan_expansion_followed_by_project_work_continues_normally(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan", "function": {"name": "update_plan", "arguments": {
                        "items": [{"title": "Create and inspect app.py", "status": "pending"}]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "expand", "function": {"name": "update_plan", "arguments": {
                        "add_items": [{"title": "Check the app entry point", "status": "pending"}]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "write", "function": {"name": "write_file", "arguments": {
                        "path": "app.py", "content": "print('ready')\\n"}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect", "function": {"name": "read_file", "arguments": {"path": "app.py"}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "finish-plan", "function": {"name": "update_plan", "arguments": {
                        "items": [{"title": "Create and inspect app.py", "status": "completed"},
                                  {"title": "Check the app entry point", "status": "completed"}]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "The implementation is written and inspected."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Implement a small project file.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual((Path(root) / "app.py").read_text(encoding="utf-8"), "print('ready')\\n")
            self.assertEqual(completion["workflowOutcome"], "inspected")

    def test_plan_only_request_does_not_force_a_tool_followup(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="plan", event=events.append, task_intent="plan")
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append("model") or {"message": {"role": "assistant", "content": "Here is the plan."}})

            engine.run("Describe how to build a simple website.")

            self.assertEqual(calls, ["model"])

    def test_automatic_structured_plan_only_request_remains_non_mutating(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append, task_intent="auto")
            responses = [
                {"message": {"role": "assistant", "content": "I will provide the requested plan.", "tool_calls": [
                    {"id": "plan-only", "function": {"name": "update_plan", "arguments": {
                        "task_mode": "plan_only", "items": [{"title": "Build later", "status": "deferred"}]
                    }}}
                ]}},
                {"message": {"role": "assistant", "content": "Here is the plan."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Plan the project without implementing it.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["workflowOutcome"], "plan_only")
            self.assertFalse(completion["implementationRequested"])
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_explicit_automatic_plan_only_followup_can_clear_saved_obligation_before_changes(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            checkpoint = {"current": {
                "status": "active", "task_mode": "implement", "plan_source": "agent",
                "implementation_requested": True,
                "plan": [{"title": "Build feature", "status": "pending"}],
            }}
            engine = AgentEngine(root, access="edit", event=events.append,
                                 task_checkpoint=checkpoint, task_intent="auto")
            responses = [
                {"message": {"role": "assistant", "content": "I will provide the plan only.", "tool_calls": [
                    {"id": "plan-only", "function": {"name": "update_plan", "arguments": {
                        "task_mode": "plan_only", "items": [{"title": "Build feature later", "status": "deferred"}]
                    }}}
                ]}},
                {"message": {"role": "assistant", "content": "Here is the plan."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Only plan this; do not implement it.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["workflowOutcome"], "plan_only")
            self.assertFalse(completion["implementationRequested"])
            self.assertFalse(checkpoint["current"]["implementation_requested"])
            self.assertEqual(list(Path(root).iterdir()), [])

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
            engine.state["registry"].run = lambda *_args, **_kwargs: "completed"
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
            engine.state["registry"].run = lambda *_args, **_kwargs: "completed"
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
