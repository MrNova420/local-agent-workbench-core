import desktop_engine
from desktop_engine import AgentEngine, ToolRegistry, Workspace, _assistant_has_final_content, _continuation_progress_signature, _implementation_evidence, _plan_has_open_work, _plan_requests_implementation, _restored_plan_requests_implementation, _saved_plan_source, _verification_report, _workflow_outcome, agent_system_prompt
from task_checkpoint import begin_task, initial_task_plan


def test_workflow_outcome_distinguishes_plan_change_inspection_and_command():
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, []) == "plan_only"
    assert _workflow_outcome("edit", False, True, {"status": "not_verified"}, []) == "changed"
    assert _workflow_outcome("edit", False, True, {"status": "inspected"}, []) == "inspected"
    assert _workflow_outcome("edit", False, True, {"status": "command_completed"}, []) == "command_completed"
    assert _workflow_outcome("edit", False, True, {"status": "command_failed"}, []) == "command_failed"
    assert _workflow_outcome("edit", False, True, {"status": "command_incomplete"}, []) == "command_incomplete"
    assert _workflow_outcome("ask", True, False, {"status": "not_verified"}, []) == "blocked"
    assert _workflow_outcome("ask", False, False, {"status": "not_verified"}, []) == "discussion"
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
    assert "different from task_mode=plan" in prompt
    assert "continue into implementation" in prompt
    assert "After publishing or revising an implementation plan, take its next open step" in prompt
    assert "not a cap on reasoning, planning" in prompt


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
    assert "continue with this step through the structured project tools" in updated.casefold()
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


def test_host_starter_plan_is_visible_and_intent_specific():
    assert initial_task_plan("", "implement") == []
    assert initial_task_plan("Explain this code", "discuss") == []
    plan = initial_task_plan("Add a settings page", "implement")
    automatic_plan = initial_task_plan("Build a small website", "auto")

    assert plan[0] == {"title": "Use the task brief and project instructions as the objective", "status": "completed", "source": "workbench", "phase": "task_brief"}
    assert [row["title"] for row in plan[1:]] == [
        "Inspect relevant project context",
        "Refine the checklist with affected files and acceptance criteria",
        "Implement the requested changes under the selected access and approval rules",
        "Inspect the changes and run relevant approved checks",
    ]
    assert [row["phase"] for row in plan[1:]] == ["inspect", "planning", "implementation", "verification"]
    assert all(row["source"] == "workbench" for row in plan)
    assert automatic_plan == []
    assert initial_task_plan("How do I fix this?", "auto") == []


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


def test_current_continuation_replaces_stale_guidance_without_changing_base_prompt():
    base = "Base agent contract.\nProject instructions remain here."
    replace_guidance = getattr(desktop_engine, "_replace_continuation_guidance", None)
    assert callable(replace_guidance)
    first = replace_guidance(base, "First next action: inspect the project.")
    second = replace_guidance(first, "Updated next action: implement the saved plan.")

    assert "Base agent contract." in second
    assert "Project instructions remain here." in second
    assert "Updated next action: implement the saved plan." in second
    assert "First next action:" not in second


def test_saved_implementation_obligation_counts_as_continuation_state():
    assert _continuation_progress_signature({"implementation_requested": False}, []) != _continuation_progress_signature(
        {"implementation_requested": True}, [])


def test_saved_user_relevant_decisions_count_as_continuation_progress():
    assert _continuation_progress_signature({"decisions": []}, []) != _continuation_progress_signature(
        {"decisions": [{"kind": "agent_plan_decision", "text": "Keep the existing router."}]}, [])


def test_replanning_without_new_evidence_does_not_count_as_progress():
    assert _continuation_progress_signature({"task_mode": "implement"}, []) == _continuation_progress_signature(
        {"task_mode": "implement", "operations": [{"name": "update_plan", "status": "completed",
                                                      "arguments_fingerprint": "one", "result_fingerprint": "one"}]},
        [])
    read = {"task_mode": "implement", "operations": [{"name": "read_file", "status": "completed",
                                                         "result_fingerprint": "source changed"}]}
    assert _continuation_progress_signature({"task_mode": "implement"}, []) != _continuation_progress_signature(
        read, [])


def test_real_plan_expansion_and_phase_changes_count_as_progress():
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
    assert _continuation_progress_signature(original, []) != _continuation_progress_signature(expanded, [])
    assert _continuation_progress_signature(original, []) != _continuation_progress_signature(advanced, [])


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

