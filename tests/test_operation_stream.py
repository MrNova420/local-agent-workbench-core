import os
import copy
import sys
import threading
import tempfile
import unittest
from pathlib import Path

from desktop_engine import AgentEngine, CONTEXT_MAX, RequestCancelled, ToolRegistry, Workspace, _needs_implementation_continuation, _tool_activity_status


class OperationStreamTests(unittest.TestCase):
    def test_output_limit_response_continues_in_the_same_task_then_completes(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            requests = []
            settings = []
            responses = [
                {"message": {"role": "assistant", "content": "The project has three sections."}, "done_reason": "length"},
                {"message": {"role": "assistant", "content": " I will summarize each one."}, "done_reason": "stop"},
            ]
            engine = AgentEngine(root, context=65536, max_tokens=8192, access="edit", task_intent="discuss", event=events.append)

            def stream(state, messages, *_args):
                requests.append(copy.deepcopy(messages))
                settings.append((state["context"], state["max_tokens"], state["temperature"]))
                return responses.pop(0)

            engine.runtime.stream = stream
            engine.run("Summarize the project")

            self.assertEqual(len(requests), 2)
            self.assertIn("selected output-token limit", requests[1][0]["content"])
            self.assertEqual(settings, [settings[0], settings[0]])
            self.assertEqual(settings[0][:2], (65536, 8192))
            self.assertEqual(next(event["workflowOutcome"] for event in events if event["type"] == "complete"), "discussion")

    def test_repeated_identical_output_limit_response_ends_as_explicitly_incomplete(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            requests = []
            responses = [
                {"message": {"role": "assistant", "content": "The result is only partially written"}, "done_reason": "length"},
                {"message": {"role": "assistant", "content": "The result is only partially written"}, "done_reason": "length"},
            ]
            engine = AgentEngine(root, access="edit", task_intent="discuss", event=events.append)

            def stream(_state, messages, *_args):
                requests.append(copy.deepcopy(messages))
                return responses.pop(0)

            engine.runtime.stream = stream
            engine.run("Summarize the project")

            self.assertEqual(len(requests), 2)
            terminal = next(event for event in events if event["type"] == "error")
            self.assertEqual(terminal["workflowOutcome"], "response_truncated")
            self.assertIn("output-token limit", terminal["error"])

    def test_truncated_final_reply_preserves_implementation_evidence_without_replaying_write(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            responses = [
                {"message": {"role": "assistant", "tool_calls": [
                    {"id": "write-once", "function": {"name": "write_file", "arguments": {"path": "index.html", "content": "<main>Done</main>"}}}
                ]}, "done_reason": "tool_calls"},
                {"message": {"role": "assistant", "content": "The page is implemented."}, "done_reason": "length"},
                {"message": {"role": "assistant", "content": "The page is implemented."}, "done_reason": "length"},
            ]
            engine = AgentEngine(root, access="edit", task_intent="implement", event=events.append)
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Create the page")

            self.assertEqual(Path(root, "index.html").read_text(encoding="utf-8"), "<main>Done</main>")
            self.assertEqual(len([event for event in events if event.get("type") == "tool_done"]), 1)
            terminal = next(event for event in events if event["type"] == "error")
            self.assertEqual(terminal["workflowOutcome"], "response_truncated")
            self.assertEqual(terminal["verification"]["contract"]["implementation_requested"], True)
            self.assertEqual(terminal["verification"]["contract"]["project_change"], "observed")

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

    def test_steering_interrupts_active_reasoning_and_resumes_same_task_with_partial_output(self):
        with tempfile.TemporaryDirectory() as root:
            interrupt = threading.Event()
            request_started = threading.Event()
            pending = [{"id": "steer-active", "instruction": "Start implementing after the needed inspection."}]
            requests = []
            events = []

            def steer(messages, _finishing):
                if not pending:
                    return False
                update = pending.pop(0)
                messages.append({"role": "user", "content": update["instruction"],
                                 "turn_id": "turn-live", "steering_id": update["id"]})
                return True

            engine = AgentEngine(root, access="plan", event=events.append,
                                 steering_handler=steer, steering_interrupt_event=interrupt,
                                 task_intent="plan")

            def stream(_state, messages, signal, _emit):
                requests.append(copy.deepcopy(messages))
                if len(requests) == 1:
                    request_started.set()
                    if not signal.wait(3):
                        raise AssertionError("active generation was not interrupted by the steering update")
                    raise RequestCancelled("Redirected by user", {
                        "role": "assistant", "content": "Partial visible response", "thinking": "Partial reasoning"})
                self.assertTrue(any(row.get("steering_id") == "steer-active" for row in messages))
                self.assertTrue(any(row.get("steering_interrupted") for row in messages))
                return {"message": {"role": "assistant", "content": "I continued the same implementation."}}

            engine.runtime.stream = stream
            result_holder = []
            worker = threading.Thread(target=lambda: result_holder.append(engine.run("Implement the feature")))
            worker.start()
            self.assertTrue(request_started.wait(2), "provider request did not start")
            interrupt.set()
            worker.join(3)

            self.assertFalse(worker.is_alive(), "redirected task did not continue")
            self.assertEqual(len(requests), 2)
            partial = next(row for row in result_holder[0] if row.get("steering_interrupted"))
            self.assertEqual(partial["content"], "Partial visible response")
            self.assertEqual(partial["thinking"], "Partial reasoning")
            self.assertNotIn("tool_calls", partial)
            self.assertTrue(any(event.get("type") == "steering_interrupted" for event in events))
            self.assertEqual(result_holder[0][-1]["content"], "I continued the same implementation.")

    def test_steering_during_action_approval_waits_for_decision_and_does_not_bypass_denial(self):
        with tempfile.TemporaryDirectory() as root:
            approval_waiting = threading.Event()
            release_denial = threading.Event()
            steering_delivered = threading.Event()
            pending = []
            requests = []
            events = []
            approval_calls = []
            responses = [
                {"message": {"role": "assistant", "tool_calls": [
                    {"id": "write-needs-approval", "function": {"name": "write_file", "arguments": {"path": "index.html", "content": "<main>Must not be written</main>"}}}
                ]}},
                {"message": {"role": "assistant", "content": "The write was denied; I preserved the project and noted the style update."}},
            ]

            def approve(name, arguments):
                approval_calls.append((name, arguments))
                approval_waiting.set()
                if not release_denial.wait(3):
                    raise AssertionError("approval decision was not released")
                return False

            def steer(messages, _finishing):
                if not pending:
                    return False
                update = pending.pop(0)
                messages.append({"role": "user", "content": update["instruction"],
                                 "turn_id": "turn-approval", "steering_id": update["id"]})
                steering_delivered.set()
                return True

            engine = AgentEngine(root, access="ask", approve=approve, event=events.append,
                                 steering_handler=steer, task_intent="implement")
            engine.runtime.stream = lambda _state, messages, *_args: (requests.append(copy.deepcopy(messages)) or responses.pop(0))
            worker = threading.Thread(target=lambda: engine.run("Implement the requested page."))
            worker.start()
            self.assertTrue(approval_waiting.wait(2), "model did not reach the approval boundary")
            self.assertFalse(Path(root, "index.html").exists())
            self.assertFalse(steering_delivered.is_set(), "steering must wait for the active approval decision")
            pending.append({"id": "steer-during-approval", "instruction": "Use the existing project style"})
            release_denial.set()
            worker.join(3)

            self.assertFalse(worker.is_alive(), "steered task did not finish")
            self.assertFalse(Path(root, "index.html").exists())
            self.assertEqual(len(requests), 2)
            second = requests[1]
            tool_index = next(index for index, message in enumerate(second) if message.get("role") == "tool")
            steer_index = next(index for index, message in enumerate(second) if message.get("steering_id") == "steer-during-approval")
            self.assertIn("Denied by user", second[tool_index]["content"])
            self.assertGreater(steer_index, tool_index)
            self.assertEqual([name for name, _arguments in approval_calls], ["write_file"])
            self.assertTrue(steering_delivered.is_set())

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

    def test_append_file_preserves_large_unicode_files_across_incremental_chunks(self):
        with tempfile.TemporaryDirectory() as root:
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            self.assertIn("write_file", {row["function"]["name"] for row in registry.schemas})
            self.assertIn("append_file", {row["function"]["name"] for row in registry.schemas})
            self.assertEqual(registry.run("write_file", {"path": "large.txt", "content": "start\n"}), "Wrote large.txt")
            self.assertEqual(registry.run("append_file", {"path": "large.txt", "content": "雪\n" * 12000}), "Appended large.txt")
            self.assertEqual(Path(root, "large.txt").read_bytes(), ("start\n" + "雪\n" * 12000).encode("utf-8"))

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
                self.assertEqual(registry.run("write_file", {"path": "large.txt", "content": "after"}), "Wrote large.txt")
            self.assertEqual(len(attempts), 2)
            self.assertEqual(path.read_text(encoding="utf-8"), "after")

    def test_atomic_file_replace_survives_a_sharing_lock_longer_than_short_retry_burst(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "large.txt")
            path.write_text("before", encoding="utf-8")
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            real_replace = os.replace
            attempts = []
            clock = [0.0]

            def replace_after_extended_lock(source, destination):
                attempts.append(1)
                if len(attempts) <= 9:
                    raise PermissionError("temporary Windows sharing violation")
                return real_replace(source, destination)

            def advance_clock(delay):
                clock[0] += delay

            with patch("desktop_engine.os.replace", side_effect=replace_after_extended_lock), \
                 patch("desktop_engine.time.monotonic", side_effect=lambda: clock[0]), \
                 patch("desktop_engine.time.sleep", side_effect=advance_clock):
                result = registry.run("write_file", {"path": "large.txt", "content": "after"})

            self.assertEqual(result, "Wrote large.txt")
            self.assertEqual(len(attempts), 10)
            self.assertGreaterEqual(clock[0], 2.0)
            self.assertEqual(path.read_text(encoding="utf-8"), "after")

    def test_atomic_file_replace_keeps_waiting_past_legacy_deadline_until_lock_releases(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "large.txt")
            path.write_text("before", encoding="utf-8")
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            real_replace = os.replace
            attempts = []
            clock = [0.0]

            def replace_after_long_lock(source, destination):
                attempts.append(1)
                if clock[0] < 35.0:
                    raise PermissionError("workspace file remains open")
                return real_replace(source, destination)

            def advance_clock(delay):
                clock[0] += delay

            with patch("desktop_engine.os.replace", side_effect=replace_after_long_lock), \
                 patch("desktop_engine.time.monotonic", side_effect=lambda: clock[0]), \
                 patch("desktop_engine.time.sleep", side_effect=advance_clock):
                result = registry.run("write_file", {"path": "large.txt", "content": "after"})

            self.assertEqual(result, "Wrote large.txt")
            self.assertGreaterEqual(clock[0], 35.0)
            self.assertEqual(path.read_text(encoding="utf-8"), "after")

    def test_append_cancellation_rolls_back_a_partial_chunk(self):
        import threading
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "large.txt")
            path.write_text("before", encoding="utf-8")
            cancel_event = threading.Event()
            real_write = os.write
            writes = 0

            def cancel_after_partial_chunk(fd, data):
                nonlocal writes
                writes += 1
                if writes == 1:
                    count = real_write(fd, data[:3])
                    cancel_event.set()
                    return count
                raise AssertionError("cancellation should be observed before another write")

            with patch("desktop_engine.os.write", side_effect=cancel_after_partial_chunk):
                with self.assertRaisesRegex(RequestCancelled, "Append cancelled"):
                    ToolRegistry._append_with_rollback(path, b"after", cancel_event)

            self.assertEqual(path.read_text(encoding="utf-8"), "before")
            self.assertEqual(list(Path(root).glob("*.tmp")), [])

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

    def test_command_does_not_inherit_sidecar_stdin(self):
        import subprocess
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as root:
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            captured = {}
            real_popen = subprocess.Popen

            def recording_popen(*args, **kwargs):
                captured.update(kwargs)
                return real_popen(*args, **kwargs)

            with patch("subprocess.Popen", side_effect=recording_popen):
                result = registry._command({"argv": [sys.executable, "-c", "import sys; print('eof' if sys.stdin.read() == '' else 'inherited')"]})

            self.assertIs(captured["stdin"], subprocess.DEVNULL)
            self.assertIn("eof", result)

    def test_command_cancellation_uses_and_closes_windows_process_job(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as root:
            cancel = threading.Event()
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True, cancel_event=cancel)
            job = SimpleNamespace(terminate=Mock(return_value=True), close=Mock())
            timer = threading.Timer(0.2, cancel.set)
            with patch("desktop_engine.WindowsProcessJob.attach", return_value=job) as attach, \
                    patch("desktop_engine.WindowsProcessJob.resume_primary_thread", return_value=True) as resume:
                timer.start()
                try:
                    result = registry._command({"argv": [sys.executable, "-c", "import time; time.sleep(20)"]})
                finally:
                    timer.cancel()
                    timer.join()

            attach.assert_called_once()
            if os.name == "nt":
                resume.assert_called_once()
            job.terminate.assert_called_once()
            job.close.assert_called_once()
            self.assertIn("[command cancelled by user]", result)

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

    def test_independent_file_writes_in_one_model_batch_run_concurrently(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", approval_mode="auto", task_intent="implement", event=events.append)
            registry = engine.state["registry"]
            original_run = registry.run
            both_started = threading.Barrier(2)

            def wait_for_independent_write(name, args, **kwargs):
                both_started.wait(timeout=1)
                return original_run(name, args, **kwargs)

            registry.run = wait_for_independent_write
            responses = [
                {"message": {"role": "assistant", "tool_calls": [
                    {"id": "write-a", "function": {"name": "write_file", "arguments": {"path": "a.txt", "content": "alpha"}}},
                    {"id": "write-b", "function": {"name": "write_file", "arguments": {"path": "b.txt", "content": "beta"}}},
                ]}},
                {"message": {"role": "assistant", "content": "Both files are written."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            messages = engine.run("Create two independent files")

            self.assertEqual(Path(root, "a.txt").read_text(encoding="utf-8"), "alpha")
            self.assertEqual(Path(root, "b.txt").read_text(encoding="utf-8"), "beta")
            completed = [event for event in events if event.get("type") == "tool_done"]
            self.assertEqual([event["status"] for event in completed], ["completed", "completed"])
            self.assertEqual(sorted(event["affectedFiles"][0] for event in completed), ["a.txt", "b.txt"])
            self.assertEqual([message["content"] for message in messages if message.get("role") == "tool"],
                             ["Wrote a.txt", "Wrote b.txt"])

    def test_ask_access_never_qualifies_for_parallel_writes_even_when_task_was_approved(self):
        with tempfile.TemporaryDirectory() as root:
            engine = AgentEngine(root, access="ask", approval_mode="per-task", task_intent="implement")
            engine.approved_task = True
            parsed = [
                (0, {}, "write_file", {"path": "a.txt", "content": "alpha"}),
                (1, {}, "write_file", {"path": "b.txt", "content": "beta"}),
            ]

            self.assertFalse(engine._can_parallelize_file_writes(parsed))

    def test_cancel_during_parallel_write_batch_preserves_submitted_results_once(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", approval_mode="auto", task_intent="implement", event=events.append)
            barrier = threading.Barrier(2)

            def complete_then_cancel(_name, args, **_kwargs):
                barrier.wait(timeout=1)
                engine.cancel_event.set()
                return f"Saved {args['path']}"

            engine.state["registry"].run = complete_then_cancel
            response = {"message": {"role": "assistant", "tool_calls": [
                {"id": "write-a", "function": {"name": "write_file", "arguments": {"path": "a.txt", "content": "alpha"}}},
                {"id": "write-b", "function": {"name": "write_file", "arguments": {"path": "b.txt", "content": "beta"}}},
            ]}}
            engine.runtime.stream = lambda *_args: response

            messages = engine.run("Create two independent files")

            completed = [event for event in events if event.get("type") == "tool_done"]
            self.assertEqual(len(completed), 2)
            self.assertEqual({event["result"] for event in completed}, {"Saved a.txt", "Saved b.txt"})
            self.assertEqual([message["content"] for message in messages if message.get("role") == "tool"],
                             ["Saved a.txt", "Saved b.txt"])
            self.assertEqual(len({event["operationId"] for event in completed}), 2)
            self.assertEqual(events[-1]["type"], "cancelled")

    def test_finish_after_operation_drains_parallel_write_batch_without_next_model_call(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", approval_mode="auto", task_intent="implement", event=events.append)
            barrier = threading.Barrier(2)
            request_count = 0

            def finish_after_current_batch(_name, args, **_kwargs):
                barrier.wait(timeout=1)
                engine.finish_after_operation()
                return f"Saved {args['path']}"

            engine.state["registry"].run = finish_after_current_batch
            response = {"message": {"role": "assistant", "tool_calls": [
                {"id": "write-a", "function": {"name": "write_file", "arguments": {"path": "a.txt", "content": "alpha"}}},
                {"id": "write-b", "function": {"name": "write_file", "arguments": {"path": "b.txt", "content": "beta"}}},
            ]}}

            def stream(*_args):
                nonlocal request_count
                request_count += 1
                return response

            engine.runtime.stream = stream
            messages = engine.run("Create two independent files")

            completed = [event for event in events if event.get("type") == "tool_done"]
            self.assertEqual(len(completed), 2)
            self.assertEqual({event["result"] for event in completed}, {"Saved a.txt", "Saved b.txt"})
            self.assertEqual([message["content"] for message in messages if message.get("role") == "tool"],
                             ["Saved a.txt", "Saved b.txt"])
            self.assertEqual(request_count, 1)
            self.assertEqual(events[-1]["type"], "stopped_after_operation")

    def test_per_action_approval_stays_in_call_order_for_independent_writes(self):
        with tempfile.TemporaryDirectory() as root:
            approvals = []
            engine = AgentEngine(root, access="ask", approval_mode="per-action", task_intent="implement",
                                 approve=lambda _name, args: approvals.append(args["path"]) or True)
            responses = [
                {"message": {"role": "assistant", "tool_calls": [
                    {"id": "write-a", "function": {"name": "write_file", "arguments": {"path": "a.txt", "content": "alpha"}}},
                    {"id": "write-b", "function": {"name": "write_file", "arguments": {"path": "b.txt", "content": "beta"}}},
                ]}},
                {"message": {"role": "assistant", "content": "Both files are written."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Create two independent files")

            self.assertEqual(approvals, ["a.txt", "b.txt"])

    def test_same_path_write_batch_preserves_call_order(self):
        with tempfile.TemporaryDirectory() as root:
            engine = AgentEngine(root, access="edit", approval_mode="auto", task_intent="implement")
            responses = [
                {"message": {"role": "assistant", "tool_calls": [
                    {"id": "write", "function": {"name": "write_file", "arguments": {"path": "ordered.txt", "content": "first\n"}}},
                    {"id": "append", "function": {"name": "append_file", "arguments": {"path": "ordered.txt", "content": "second\n"}}},
                ]}},
                {"message": {"role": "assistant", "content": "The file is complete."}},
            ]
            engine.runtime.stream = lambda *_args: responses.pop(0)

            engine.run("Write and extend one file")

            self.assertEqual(Path(root, "ordered.txt").read_text(encoding="utf-8"), "first\nsecond\n")

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

    def test_failed_file_action_can_be_corrected_and_retried_in_the_same_task(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append, task_intent="implement")
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "bad-path", "function": {"name": "write_file", "arguments": {"path": "../escape.txt", "content": "unsafe"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "corrected-path", "function": {"name": "write_file", "arguments": {"path": "result.txt", "content": "ready\\n"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect-result", "function": {"name": "read_file", "arguments": {"path": "result.txt"}}}
                ]}},
                {"message": {"role": "assistant", "content": "Created and inspected result.txt after correcting the rejected path."}},
            ]
            request_snapshots = []

            def stream(_state, request_messages, *_args):
                request_snapshots.append(copy.deepcopy(request_messages))
                return responses.pop(0)

            engine.runtime.stream = stream

            engine.run("Implement result.txt and inspect the finished file.")

            completion = next(event for event in events if event.get("type") == "complete")
            operations = [event for event in events if event.get("type") == "tool_done"]
            self.assertEqual([event["status"] for event in operations], ["failed", "completed", "completed"])
            self.assertIn("escapes the selected project", request_snapshots[1][-1]["content"])
            self.assertEqual((Path(root) / "result.txt").read_text(encoding="utf-8"), "ready\\n")
            self.assertEqual(completion["workflowOutcome"], "inspected")

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
            saved_write = next(row for row in engine.state["task_checkpoint"]["current"]["operations"]
                               if row.get("name") == "write_file")
            self.assertTrue(saved_write.get("arguments_fingerprint"))
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
            plan_events = [event for event in events if event.get("type") == "plan"]
            self.assertEqual(len(plan_events), 2)
            self.assertEqual(plan_events[0]["items"][0]["phase"], "task_progress")
            self.assertEqual(plan_events[0]["items"][0]["status"], "in_progress")
            self.assertEqual(plan_events[-1]["items"][0]["status"], "completed")
            self.assertEqual(checkpoint["plan"][0]["status"], "completed")
            self.assertFalse(any(event.get("type") == "tool_start" for event in events))

    def test_automatic_unclassified_response_gets_state_based_classification_followup(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            requests = []
            responses = [
                {"message": {"role": "assistant", "content": "I can explain the project."}},
                {"message": {"role": "assistant", "content": "It contains application code and configuration."}},
            ]
            engine = AgentEngine(root, access="edit", task_intent="auto", event=events.append)
            engine.runtime.stream = lambda _state, messages, *_args: (requests.append(copy.deepcopy(messages)) or responses.pop(0))

            engine.run("What does this project do?")

            self.assertEqual(len(requests), 2)
            self.assertIn("Automatic task intent is not classified", requests[1][0]["content"])
            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["workflowOutcome"], "no_action")
            self.assertFalse(any(event.get("type") == "tool_start" for event in events))

    def test_automatic_prose_plan_must_classify_then_implement_before_completion(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            requests = []
            plan = [
                {"title": "Create the page", "status": "pending"},
                {"title": "Inspect the finished page", "status": "pending"},
            ]
            responses = [
                {"message": {"role": "assistant", "content": "I will plan the page and then create it."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan", "function": {"name": "update_plan", "arguments": {"task_mode": "plan", "items": plan}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "write", "function": {"name": "write_file", "arguments": {"path": "index.html", "content": "<h1>Ready</h1>"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect", "function": {"name": "read_file", "arguments": {"path": "index.html"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "finish-plan", "function": {"name": "update_plan", "arguments": {
                        "task_mode": "implement",
                        "items": [
                            {"title": "Create the page", "status": "completed"},
                            {"title": "Inspect the finished page", "status": "completed"},
                        ],
                    }}}
                ]}},
                {"message": {"role": "assistant", "content": "The page is written and inspected."}},
            ]
            engine = AgentEngine(root, access="edit", task_intent="auto", event=events.append)
            engine.runtime.stream = lambda _state, messages, *_args: (requests.append(copy.deepcopy(messages)) or responses.pop(0))

            engine.run("Create a simple home page.")

            self.assertEqual(len(requests), 6)
            self.assertIn("Automatic task intent is not classified", requests[1][0]["content"])
            self.assertEqual(Path(root, "index.html").read_text(encoding="utf-8"), "<h1>Ready</h1>")
            completion = next(event for event in events if event.get("type") == "complete")
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

    def test_distinct_reasoning_after_implementation_nudge_does_not_end_run_as_no_progress(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append)
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan", "function": {"name": "update_plan", "arguments": {
                        "items": [{"title": "Create the source file", "status": "pending"}]}}}
                ]}},
                {"message": {"role": "assistant", "content": "The entry point must match the existing package layout."}},
                {"message": {"role": "assistant", "content": "I found the package layout; now I can write its entry point."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "write", "function": {"name": "write_file", "arguments": {
                        "path": "app.py", "content": "print('ready')\\n"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "inspect", "function": {"name": "read_file", "arguments": {"path": "app.py"}}}
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "finish-plan", "function": {"name": "update_plan", "arguments": {
                        "items": [{"title": "Create the source file", "status": "completed"}]}}}
                ]}},
                {"message": {"role": "assistant", "content": "The file is implemented and inspected."}},
            ]
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append(1) or responses.pop(0))

            engine.run("Implement a small project file.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 7)
            self.assertEqual((Path(root) / "app.py").read_text(encoding="utf-8"), "print('ready')\\n")
            self.assertEqual(completion["workflowOutcome"], "inspected")

    def test_repeated_response_cycle_is_detected_across_intervening_reasoning(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append, task_intent="implement")
            responses = [
                {"message": {"role": "assistant", "content": "I am considering the project structure."}},
                {"message": {"role": "assistant", "content": "I should inspect before choosing a file."}},
                {"message": {"role": "assistant", "content": "I am considering the project structure."}},
                {"message": {"role": "assistant", "content": "I am considering the project structure."}},
            ]
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append(1) or responses.pop(0))

            engine.run("Implement the requested project change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 3)
            self.assertEqual(completion["workflowOutcome"], "implementation_incomplete")

    def test_repeated_empty_searches_with_different_queries_do_not_fake_progress(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="edit", event=events.append, task_intent="implement")
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "search-one", "function": {"name": "search_files", "arguments": {"query": "first-pattern"}}}
                ]}},
                {"message": {"role": "assistant", "content": "No source lines match; I will check another pattern."}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "search-two", "function": {"name": "search_files", "arguments": {"query": "second-pattern"}}}
                ]}},
                {"message": {"role": "assistant", "content": "No source lines match; I will check another pattern."}},
                {"message": {"role": "assistant", "content": "A different search still found no source lines."}},
            ]
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append(1) or responses.pop(0))

            engine.run("Implement the requested source change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 4)
            self.assertEqual(completion["workflowOutcome"], "implementation_incomplete")

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
                {"message": {"role": "assistant", "content": "I am still refining the plan."}},
            ]
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append(1) or responses.pop(0))

            engine.run("Implement a small project change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(calls), 5)
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

    def test_repeated_plan_tool_calls_in_read_only_implement_mode_end_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="plan", event=events.append, task_intent="implement")
            plan_call = lambda call_id, title: {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": call_id, "function": {"name": "update_plan", "arguments": {
                    "add_items": [{"title": title, "status": "pending"}]}}
                }
            ]}}
            responses = [
                plan_call("plan-1", "Implement the requested project"),
                plan_call("plan-2", "Refine the implementation plan"),
                plan_call("plan-3", "Refine the plan again"),
            ]
            requests = []
            def stream(_state, messages, *_args):
                requests.append(copy.deepcopy(messages))
                return responses.pop(0)
            engine.runtime.stream = stream

            engine.run("Implement a small project change.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(completion["workflowOutcome"], "blocked")
            self.assertIn("read-only", requests[2][0]["content"].casefold())

    def test_denied_project_action_cannot_loop_through_plan_tool_calls(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="ask", approve=lambda *_args: False,
                                 event=events.append, task_intent="implement")
            write = {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "write", "function": {"name": "write_file", "arguments": {
                    "path": "app.py", "content": "print('ready')"}}
                }
            ]}}
            plan_call = lambda call_id, title: {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": call_id, "function": {"name": "update_plan", "arguments": {
                    "add_items": [{"title": title, "status": "pending"}]}}
                }
            ]}}
            responses = [write, plan_call("plan-1", "Revisit the plan"),
                         plan_call("plan-2", "Expand the plan"), plan_call("plan-3", "Expand again")]
            requests = []
            def stream(_state, messages, *_args):
                requests.append(copy.deepcopy(messages))
                return responses.pop(0)
            engine.runtime.stream = stream

            engine.run("Implement the requested project file.")

            completion = next(event for event in events if event.get("type") == "complete")
            self.assertEqual(len(requests), 4)
            self.assertTrue(completion["actionDenied"])
            self.assertEqual(completion["workflowOutcome"], "blocked")
            self.assertIn("denied", requests[3][0]["content"].casefold())

    def test_explicit_plan_mode_repeated_plan_calls_end_with_saved_plan_and_missing_reply(self):
        with tempfile.TemporaryDirectory() as root:
            events = []
            engine = AgentEngine(root, access="plan", event=events.append, task_intent="plan")
            responses = [
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan-1", "function": {"name": "update_plan", "arguments": {
                        "items": [{"title": "Inspect and describe the project", "status": "pending"}]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan-2", "function": {"name": "update_plan", "arguments": {
                        "add_items": [{"title": "Add more planning detail", "status": "pending"}]}}
                    }
                ]}},
                {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan-3", "function": {"name": "update_plan", "arguments": {
                        "add_items": [{"title": "Add still more detail", "status": "pending"}]}}
                    }
                ]}},
            ]
            calls = []
            engine.runtime.stream = lambda *_args: (calls.append(1) or responses.pop(0))

            engine.run("Make a plan for this project, without editing files.")

            error = next(event for event in events if event.get("type") == "error")
            self.assertEqual(len(calls), 3)
            self.assertEqual(error["workflowOutcome"], "response_missing")
            self.assertTrue(engine.state["registry"].plan)

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
                    {"id": f"call-{index}", "function": {"name": "read_file", "arguments": {"path": f"file-{index}.txt"}}}
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
