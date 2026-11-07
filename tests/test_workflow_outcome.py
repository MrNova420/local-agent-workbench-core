import desktop_engine
from desktop_engine import AgentEngine, ToolRegistry, Workspace, _assistant_has_final_content, _continuation_progress_signature, _implementation_evidence, _no_progress_response_signature, _plan_has_open_work, _plan_requests_implementation, _restored_plan_requests_implementation, _saved_plan_source, _verification_report, _workflow_outcome, agent_system_prompt
from task_checkpoint import begin_task


def test_workflow_outcome_distinguishes_plan_change_inspection_and_command():
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, []) == "plan_only"
    assert _workflow_outcome("edit", False, True, {"status": "not_verified"}, []) == "changed"
    assert _workflow_outcome("edit", False, True, {"status": "inspected"}, []) == "inspected"
    assert _workflow_outcome("edit", False, True, {"status": "command_completed"}, []) == "command_completed"
    assert _workflow_outcome("edit", False, True, {"status": "command_failed"}, []) == "command_failed"
    assert _workflow_outcome("edit", False, True, {"status": "command_incomplete"}, []) == "command_incomplete"
    assert _workflow_outcome("ask", True, False, {"status": "not_verified"}, []) == "blocked"
    assert _workflow_outcome("edit", False, False, {"status": "not_verified"}, [], "auto", False, "auto") == "no_action"
    assert _workflow_outcome("ask", False, False, {"status": "not_verified"}, [], "auto", False, "auto") == "no_action"
    assert _workflow_outcome("ask", False, False, {"status": "not_verified"}, [], "auto", False, "discussion") == "discussion"
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, [], "discuss") == "discussion"
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, [], "plan") == "plan_only"
    assert _workflow_outcome("edit", False, False, {"status": "not_verified"}, [], "implement", True) == "implementation_incomplete"
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, [], "implement", True) == "blocked"
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, [], "auto", True) == "plan_only"
    assert _workflow_outcome("edit", False, False, {"status": "command_completed"}, [], "implement", True) == "implementation_unverified"
    assert _workflow_outcome("edit", False, False, {"status": "command_completed", "contract": {"command_side_effects_possible": True}}, [], "implement", True) == "implementation_unverified"
    assert _workflow_outcome("edit", False, False, {"status": "command_failed", "contract": {"command_side_effects_possible": True}}, [], "implement", True) == "command_failed"
    assert _workflow_outcome("edit", False, False, {"status": "command_failed"}, [], "implement", True) == "command_failed"


def test_verification_report_distinguishes_missing_change_review_and_command_evidence():
    missing = _verification_report([], True, False)
    inspected = _verification_report([{"kind": "inspection", "tool": "git_diff", "result": "completed"}], True, True)
    checked = _verification_report([{"kind": "command", "tool": "run_command", "result": "completed"}], True, True)
    possible_command_effects = _verification_report(
        [{"kind": "command", "tool": "run_command", "arguments": {"argv": ["tool", "build"]}, "result": "completed"}],
        True, False, command_side_effects_possible=True, post_effect_inspection=True,
    )

    assert missing["status"] == "not_implemented"
    assert missing["contract"]["project_change"] == "not_observed"
    assert inspected["contract"]["post_change_inspection"] == "completed"
    assert checked["status"] == "command_completed"
    assert checked["contract"]["post_change_inspection"] == "missing"
    assert checked["contract"]["command_success_is_test_pass"] is False
    assert possible_command_effects["contract"]["project_change"] == "not_observed"
    assert possible_command_effects["contract"]["command_side_effects_possible"] is True
    assert possible_command_effects["contract"]["post_command_inspection"] == "completed"
    assert possible_command_effects["steps"][0]["arguments"]["argv"] == ["tool", "build"]

    partial = _verification_report([{"kind": "command", "tool": "run_command", "result": "partial"}], True, True)
    cancelled = _verification_report([{"kind": "command", "tool": "run_shell", "result": "cancelled"}], True, False)
    assert partial["status"] == "command_incomplete"
    assert cancelled["status"] == "command_incomplete"


def test_explicit_task_intent_has_read_only_guidance_and_implementation_contract():
    plan_prompt = agent_system_prompt("Read-only", "", task_intent="plan")
    discuss_prompt = agent_system_prompt("Read-only", "", task_intent="discuss")
    implement_prompt = agent_system_prompt("Ask before writes", "", task_intent="implement")

    assert "Task intent: plan only" in plan_prompt
    assert "without changing project files" in plan_prompt
    assert "Task intent: discussion" in discuss_prompt
    assert "Task intent: implementation" in implement_prompt


def test_automatic_guidance_separates_plan_phase_from_plan_only_request():
    prompt = agent_system_prompt("Edit with approval", "", task_intent="auto")
    assert "task_mode=plan_only only when the user explicitly asks" in prompt
    assert "task_mode=plan_only is the explicit no-implementation choice" in prompt
    assert "Do not call update_plan solely to classify ordinary conversation" in prompt
    assert "when durable task state needs to be recorded" in prompt
    assert "Before ending without a structured project operation, record a task-mode decision" not in prompt
    assert "continue into implementation" in prompt
    assert "After publishing or revising an implementation plan, take its next open step" in prompt
    assert "not a cap on reasoning, planning" in prompt


def test_all_model_profiles_keep_safe_batching_and_full_scope():
    prompts = [
        agent_system_prompt("Edit with approval", "", harness_profile="standard"),
        agent_system_prompt("Edit with approval", "", harness_profile="small-model"),
    ]
    for prompt in prompts:
        assert "Batch independent read calls and disjoint file write/edit calls" in prompt
        assert "gathers each required approval before dispatching independent file changes concurrently" in prompt
        assert "Keep commands and MCP calls ordered" in prompt
    assert "keep the entire requested scope and expandable plan" in prompts[1]
    assert "do not cap required work" in prompts[1]


def test_plan_tool_result_hands_implementation_to_next_open_step_without_replanning(tmp_path):
    checkpoint = begin_task(None, prompt="Build a small website", turn_id="turn-1",
                            workspace=str(tmp_path), settings={})
    registry = ToolRegistry(Workspace(tmp_path, "edit"), lambda *_: True, task_checkpoint=checkpoint)
    plan = [
        {"title": "Inspect the empty project", "status": "completed"},
        {"title": "Implement the website", "status": "pending"},
        {"title": "Inspect and verify the result", "status": "pending"},
    ]

    updated = registry.run("update_plan", {"items": plan, "task_mode": "implement"})
    repeated = registry.run("update_plan", {"items": plan, "task_mode": "implement"})

    assert '"next_action": "Implement the website"' in updated
    assert "continue with this next open step through the appropriate structured project tools" in updated.casefold()
    assert '"plan_changed": true' in updated
    assert '"plan_changed": false' in repeated
    assert '"next_action": "Implement the website"' in repeated

    plan_only_checkpoint = begin_task(None, prompt="Plan a small website", turn_id="turn-plan",
                                      workspace=str(tmp_path), settings={})
    plan_only = ToolRegistry(Workspace(tmp_path, "edit"), lambda *_: True,
                             task_checkpoint=plan_only_checkpoint)
    plan_only.task_intent = "plan"
    result = plan_only.run("update_plan", {"items": plan, "task_mode": "implement"})
    assert '"task_mode": "plan"' in result
    assert "This request is read-only/plan-only" in result


def test_automatic_plan_phase_promotes_actionable_plan_to_implementation(tmp_path):
    checkpoint = begin_task(None, prompt="Build and verify the requested feature", turn_id="auto-turn",
                            workspace=str(tmp_path), settings={"task_intent": "auto"})
    events = []
    registry = ToolRegistry(Workspace(tmp_path, "edit"), lambda *_: True,
                            task_checkpoint=checkpoint, event=events.append)
    registry.task_intent = "auto"

    result = registry.run("update_plan", {
        "items": [
            {"title": "Inspect existing files", "status": "completed"},
            {"title": "Implement the requested feature", "status": "pending"},
            {"title": "Inspect and verify the change", "status": "pending"},
        ],
        "task_mode": "plan",
    })

    assert '"task_mode": "implement"' in result
    assert '"next_action": "Implement the requested feature"' in result
    assert "structured project tools now" in result
    assert events[-1]["taskMode"] == "implement"
    assert checkpoint["current"]["task_mode"] == "implement"


def test_new_automatic_task_starts_without_a_host_authored_plan():
    checkpoint = begin_task(None, prompt="Build a small website", turn_id="turn-1",
                            workspace="C:/project", settings={"task_intent": "auto"})

    assert checkpoint["current"]["plan"] == []
    assert "plan_source" not in checkpoint["current"]


def test_automatic_implementation_uses_structured_evidence_not_prompt_wording():
    open_agent_plan = [{"title": "Implement and inspect", "status": "pending"}]
    completed_agent_plan = [{"title": "Implement and inspect", "status": "completed"}]
    assert _implementation_evidence("auto", "auto", "agent", open_agent_plan, False, False)
    assert _implementation_evidence("auto", "plan", "agent", open_agent_plan, False, False)
    assert _implementation_evidence("auto", "plan", "agent", completed_agent_plan, False, False)
    assert not _implementation_evidence("auto", "plan_only", "agent", open_agent_plan, False, False)
    assert not _implementation_evidence("auto", "discussion", "agent", open_agent_plan, False, False)
    assert _implementation_evidence("auto", "implement", "none", [], False, False)
    assert _implementation_evidence("auto", "plan", "none", [], True, False)
    assert _implementation_evidence("auto", "auto", "none", [], False, False, mutation_attempted=True)
    assert not _implementation_evidence("auto", "auto", "none", [], False, False)
    assert _implementation_evidence("implement", "auto", "none", [], False, False)
    assert not _implementation_evidence("plan", "auto", "agent", open_agent_plan, False, False)
    assert not _implementation_evidence("discuss", "auto", "agent", open_agent_plan, False, False)


def test_automatic_task_mode_controls_terminal_outcome_without_changing_user_settings():
    assert _workflow_outcome("edit", False, False, {"status": "not_verified"}, [{"status": "pending"}], "auto", True, "plan") == "implementation_incomplete"
    assert _workflow_outcome("edit", False, False, {"status": "not_verified"}, [{"status": "completed"}], "auto", True, "plan") == "implementation_incomplete"
    assert _workflow_outcome("edit", False, False, {"status": "not_verified"}, [{"status": "pending"}], "auto", False, "plan_only") == "plan_only"
    assert _workflow_outcome("edit", False, False, {"status": "not_verified"}, [], "auto", False, "discussion") == "discussion"


def test_open_agent_plan_requests_implementation_unless_task_is_discussion():
    pending = [{"status": "pending"}]
    completed = [{"status": "completed"}]
    assert not _plan_requests_implementation("discussion", "agent", pending)
    assert _plan_requests_implementation("plan", "agent", pending)
    assert _plan_requests_implementation("plan", "agent", completed)
    assert _plan_requests_implementation("auto", "agent", pending)
    assert _plan_requests_implementation("implement", "agent", pending)
    assert _plan_requests_implementation("implement", "workbench", [])
    assert not _plan_requests_implementation("plan_only", "agent", pending)
    assert not _plan_requests_implementation("auto", "workbench", pending)


def test_restored_open_agent_plan_keeps_automatic_implementation_obligation():
    pending = [{"title": "Implement and inspect", "status": "pending"}]
    assert _restored_plan_requests_implementation("auto", "plan", "agent", pending)
    assert _restored_plan_requests_implementation("auto", "auto", "agent", pending)
    assert _restored_plan_requests_implementation("auto", "implement", "workbench", [])
    assert not _restored_plan_requests_implementation("auto", "discussion", "agent", pending)
    assert not _restored_plan_requests_implementation("auto", "plan", "workbench", pending)
    assert not _restored_plan_requests_implementation("plan", "plan", "agent", pending)


def test_plan_ownership_migrates_from_legacy_checkpoints():
    assert _saved_plan_source("plan", [{"title": "Build", "status": "pending"}], None) == "agent"
    assert _saved_plan_source("plan", [{"title": "Build", "status": "pending", "source": "workbench"}], None) == "workbench"
    assert _saved_plan_source("auto", [{"title": "Build", "status": "pending"}], {"corrupt": True}) == "workbench"
    assert _saved_plan_source("auto", [{"title": "Build", "status": "pending"}], "agent") == "agent"


def test_unknown_agent_plan_status_stays_open_until_explicitly_terminal():
    assert _plan_has_open_work([{"title": "Implement", "status": "planned"}])
    assert _plan_has_open_work([{"title": "Implement", "status": "awaiting review"}])
    assert not _plan_has_open_work([{"title": "Implement", "status": "verified"}])
    assert not _plan_has_open_work([{"title": "Implement", "status": "deferred"}])


def test_final_content_requires_user_visible_text_after_tool_work():
    assert _assistant_has_final_content({"content": "The page is ready."})
    assert not _assistant_has_final_content({"content": "  \n", "thinking": "Working through the result."})
    assert not _assistant_has_final_content({"content": None})


def test_structured_task_mode_change_counts_as_continuation_progress():
    assert _continuation_progress_signature({"task_mode": "plan"}, []) != _continuation_progress_signature(
        {"task_mode": "implement"}, [])


def test_current_continuation_is_a_request_suffix_and_keeps_system_prefix_stable():
    base = [{"role":"system","content":"Base agent contract.\nProject instructions remain here."},
            {"role":"user","content":"Create the requested project."}]
    append_guidance = getattr(desktop_engine, "_append_continuation_guidance", None)
    assert callable(append_guidance)
    first = append_guidance(base, "First next action: inspect the project.")
    second = append_guidance(base, "Updated next action: implement the saved plan.")

    assert first[0] == second[0] == base[0]
    assert first[-1]["role"] == second[-1]["role"] == "user"
    assert "First next action:" in first[-1]["content"]
    assert "Updated next action: implement the saved plan." in second[-1]["content"]
    assert len(base) == 2


def test_saved_implementation_obligation_counts_as_continuation_state():
    assert _continuation_progress_signature({"implementation_requested": False}, []) != _continuation_progress_signature(
        {"implementation_requested": True}, [])


def test_agent_plan_decisions_do_not_count_as_implementation_progress_but_host_decisions_do():
    baseline = _continuation_progress_signature({"decisions": []}, [])
    assert baseline == _continuation_progress_signature(
        {"decisions": [{"kind": "agent_plan_decision", "text": "Keep the existing router."}]}, [])
    assert baseline != _continuation_progress_signature(
        {"decisions": [{"kind": "permission_rule", "tool": "write_file", "decision": "ask"}]}, [])


def test_replanning_without_new_evidence_does_not_count_as_progress():
    assert _continuation_progress_signature({"task_mode": "implement"}, []) == _continuation_progress_signature(
        {"task_mode": "implement", "operations": [{"name": "update_plan", "status": "completed",
                                                      "arguments_fingerprint": "one", "result_fingerprint": "one"}]},
        [])
    read = {"task_mode": "implement", "operations": [{"name": "read_file", "status": "completed",
                                                         "result_fingerprint": "source changed"}]}
    assert _continuation_progress_signature({"task_mode": "implement"}, []) != _continuation_progress_signature(
        read, [])


def test_no_progress_response_signature_distinguishes_new_reasoning_and_host_state():
    state = {"task_mode": "implement", "operations": []}
    first = {"role": "assistant", "content": "I will inspect the package layout.", "thinking": "First thought."}
    same = {"role": "assistant", "content": "I will inspect the package layout.", "thinking": "First thought."}
    next_response = {"role": "assistant", "content": "The entry point belongs in src.", "thinking": "Second thought."}
    changed_state = {"task_mode": "implement", "operations": [{
        "name": "read_file", "status": "completed", "result_fingerprint": "package contents",
    }]}

    first_signature = _no_progress_response_signature(state, [], first)
    assert first_signature == _no_progress_response_signature(state, [], same)
    assert first_signature != _no_progress_response_signature(state, [], next_response)
    assert first_signature != _no_progress_response_signature(changed_state, [], first)


def test_identical_read_results_do_not_count_as_new_progress_from_changed_arguments():
    first = {"task_mode": "implement", "operations": [{"name": "search_files", "status": "completed",
        "affected_files": [], "arguments_fingerprint": "pattern-a", "result_fingerprint": "no-matches"}]}
    same_result = {"task_mode": "implement", "operations": [{"name": "search_files", "status": "completed",
        "affected_files": [], "arguments_fingerprint": "pattern-b", "result_fingerprint": "no-matches"}]}
    new_result = {"task_mode": "implement", "operations": [{"name": "search_files", "status": "completed",
        "affected_files": [], "arguments_fingerprint": "pattern-c", "result_fingerprint": "found-match"}]}
    assert _continuation_progress_signature(first, []) == _continuation_progress_signature(same_result, [])
    assert _continuation_progress_signature(first, []) != _continuation_progress_signature(new_result, [])
    first_file = {"task_mode": "implement", "operations": [{"name": "read_file", "status": "completed",
        "affected_files": ["src/one.py"], "arguments_fingerprint": "path-a", "result_fingerprint": "same-contents"}]}
    second_file = {"task_mode": "implement", "operations": [{"name": "read_file", "status": "completed",
        "affected_files": ["src/two.py"], "arguments_fingerprint": "path-b", "result_fingerprint": "same-contents"}]}
    assert _continuation_progress_signature(first_file, []) != _continuation_progress_signature(second_file, [])


def test_distinct_command_arguments_remain_visible_when_results_match():
    first = {"task_mode": "implement", "operations": [{"name": "run_shell", "status": "completed",
        "affected_files": [], "arguments_fingerprint": "command-a", "result_fingerprint": "exit-zero"}]}
    second = {"task_mode": "implement", "operations": [{"name": "run_shell", "status": "completed",
        "affected_files": [], "arguments_fingerprint": "command-b", "result_fingerprint": "exit-zero"}]}
    assert _continuation_progress_signature(first, []) != _continuation_progress_signature(second, [])


def test_plan_expansion_and_phase_changes_do_not_mask_missing_project_progress():
    original = {"task_mode": "plan", "plan_source": "agent", "plan": [
        {"title": "Inspect project", "phase": "inspect", "status": "pending"},
    ]}
    expanded = {**original, "plan": [*original["plan"],
        {"title": "Build feature", "phase": "implementation", "status": "pending"}]}
    advanced = {**original, "plan": [
        {"title": "Inspect project", "phase": "inspect", "status": "completed"},
    ], "acceptance_criteria": ["The feature works"]}

    assert _continuation_progress_signature(original, []) == _continuation_progress_signature(
        {**original, "operations": [{"name": "update_plan", "status": "completed",
                                      "arguments_fingerprint": "same", "result_fingerprint": "same"}]}, [])
    assert _continuation_progress_signature(original, []) == _continuation_progress_signature(expanded, [])
    assert _continuation_progress_signature(original, []) == _continuation_progress_signature(advanced, [])


def test_plan_access_hides_mutating_and_command_tools_from_the_model_catalog(tmp_path):
    registry = ToolRegistry(Workspace(tmp_path, "plan"), lambda *_: False)
    names = {schema["function"]["name"] for schema in registry.schemas}

    assert {"list_files", "search_files", "read_file", "git_diff", "update_plan"} <= names
    assert not {"write_file", "append_file", "edit_file", "apply_patch", "run_command", "run_shell"} & names


def test_optional_ask_user_tool_is_available_in_read_only_runs_only_when_host_supports_it(tmp_path):
    handler = lambda args: f"User chose: {args['question']}"
    engine = AgentEngine(tmp_path, access="plan", ask_user=handler)
    names = {schema["function"]["name"] for schema in engine.state["registry"].schemas}
    assert "ask_user" in names
    assert engine.state["registry"].run("ask_user", {"question": "Which style?"}) == "User chose: Which style?"

    standalone = ToolRegistry(Workspace(tmp_path, "plan"), lambda *_: False)
    assert "ask_user" not in {schema["function"]["name"] for schema in standalone.schemas}

    denied = ToolRegistry(Workspace(tmp_path, "plan"), lambda *_: False,
                          permission_rules=[{"tool": "ask_user", "decision": "deny"}],
                          ask_user=handler)
    assert denied.run("ask_user", {"question": "Which style?"}) == "Denied by user: a persistent deny permission rule blocks this tool."


def test_plan_access_omits_external_mcp_tools(tmp_path):
    external = [{"type": "function", "function": {"name": "mcp__anything", "parameters": {"type": "object"}}}]
    engine = AgentEngine(tmp_path, access="plan", mcp_tools=external)

    assert engine.mcp_tools == []

