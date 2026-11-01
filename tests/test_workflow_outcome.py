from desktop_engine import AgentEngine, ToolRegistry, Workspace, _workflow_outcome, agent_system_prompt
from task_checkpoint import initial_task_plan


def test_workflow_outcome_distinguishes_plan_change_inspection_and_command():
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, []) == "plan_only"
    assert _workflow_outcome("edit", False, True, {"status": "not_verified"}, []) == "changed"
    assert _workflow_outcome("edit", False, True, {"status": "inspected"}, []) == "inspected"
    assert _workflow_outcome("edit", False, True, {"status": "command_completed"}, []) == "command_completed"
    assert _workflow_outcome("edit", False, True, {"status": "command_failed"}, []) == "command_failed"
    assert _workflow_outcome("ask", True, False, {"status": "not_verified"}, []) == "blocked"
    assert _workflow_outcome("ask", False, False, {"status": "not_verified"}, []) == "discussion"
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, [], "discuss") == "discussion"
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, [], "plan") == "plan_only"


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

    assert plan[0] == {
        "title": "Requested outcome", "text": "Add a settings page",
        "status": "pending", "source": "workbench",
    }
    assert [row["title"] for row in plan[1:]] == [
        "Inspect relevant project context",
        "Refine the checklist with affected files and acceptance criteria",
        "Implement the requested changes under the selected access and approval rules",
        "Inspect the changes and run relevant approved checks",
    ]
    assert all(row["source"] == "workbench" for row in plan)
    assert automatic_plan and automatic_plan[0]["text"] == "Build a small website"
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

