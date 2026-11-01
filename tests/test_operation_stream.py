import os
import sys
import tempfile
import unittest
from pathlib import Path

from desktop_engine import AgentEngine, CONTEXT_MAX, ToolRegistry, Workspace, _needs_implementation_continuation, _tool_activity_status


class OperationStreamTests(unittest.TestCase):
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
        def explode(*_args):
            raise OSError("disk unavailable")

        event = self._run_one_tool("write_file", {"path": "x.txt", "content": "x"}, explode)

        self.assertEqual(event["status"], "failed")
        self.assertIn("Tool error: OSError", event["result"])

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

    def test_command_only_implementation_records_possible_effects_and_inspects_after_command(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            actions = []
            engine.state["registry"].run = lambda name, args: (
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
            engine = AgentEngine(root, access="edit", event=events.append)
            engine.state["registry"].run = lambda name, _args: {
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
            engine.runtime.stream = lambda *_args: (calls.append(1) or responses.pop(0))

            engine.run("Implement a small project file.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 6)
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
