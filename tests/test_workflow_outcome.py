from desktop_engine import AgentEngine, ToolRegistry, Workspace, _verification_report, _workflow_outcome, agent_system_prompt
from task_checkpoint import initial_task_plan


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
    assert automatic_plan and automatic_plan[0]["source"] == "workbench"
    assert initial_task_plan("How do I fix this?", "auto") == []


def test_plan_access_hides_mutating_and_command_tools_from_the_model_catalog(tmp_path):
    registry = ToolRegistry(Workspace(tmp_path, "plan"), lambda *_: False)
    names = {schema["function"]["name"] for schema in registry.schemas}

    assert {"list_files", "search_files", "read_file", "git_diff", "update_plan"} <= names
    assert not {"write_file", "edit_file", "apply_patch", "run_command", "run_shell"} & names


def test_plan_access_omits_external_mcp_tools(tmp_path):
    external = [{"type": "function", "function": {"name": "mcp__anything", "parameters": {"type": "object"}}}]
    engine = AgentEngine(tmp_path, access="plan", mcp_tools=external)

    assert engine.mcp_tools == []

