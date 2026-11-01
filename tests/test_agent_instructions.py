from desktop_engine import agent_system_prompt


def test_agent_prompt_uses_structured_tools_to_start_actions_and_trigger_approval():
    prompt = agent_system_prompt("Request approval before every project file change and command.", "Project-specific guidance")

    assert "Do not ask for approval in chat text" in prompt
    assert "call the requested tool" in prompt
    assert "the workbench will show its approval dialog" in prompt
    assert "Do not claim that an action ran unless its tool result confirms it" in prompt
    assert "Project-specific guidance" in prompt
    assert "concise user-relevant design decisions and assumptions" in prompt
    assert "not private chain-of-thought" in prompt


def test_small_model_workflow_guides_adaptive_work_without_a_fixed_tool_call_limit():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="small-model")

    assert "group independent read-only operations when useful" in prompt.casefold()
    assert "choose action sizes that fit the task" in prompt.casefold()
    assert "do not skip required project work or validation" in prompt.casefold()
    assert "inspect results" in prompt.casefold()


def test_small_model_workflow_prevents_restarting_or_repeating_completed_planning():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="small-model")

    assert "plan once" in prompt.casefold()
    assert "take the next unfinished action" in prompt.casefold()
    assert "after compaction, continue from the saved checkpoint" in prompt.casefold()


def test_small_model_continuity_guidance_preserves_discussion_and_plan_intents():
    prompt = agent_system_prompt("Read-only", "", harness_profile="small-model", task_intent="plan")

    assert "for implementation tasks" in prompt.casefold()
    assert "respect discussion and plan-only intent" in prompt.casefold()


def test_standard_workflow_remains_general():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="standard")

    assert "group independent read-only operations when useful" not in prompt.casefold()
    assert "plan once" not in prompt.casefold()
