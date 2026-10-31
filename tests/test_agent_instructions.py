from desktop_engine import agent_system_prompt


def test_agent_prompt_uses_structured_tools_to_start_actions_and_trigger_approval():
    prompt = agent_system_prompt("Request approval before every project file change and command.", "Project-specific guidance")

    assert "Do not ask for approval in chat text" in prompt
    assert "call the requested tool" in prompt
    assert "the workbench will show its approval dialog" in prompt
    assert "Do not claim that an action ran unless its tool result confirms it" in prompt
    assert "Project-specific guidance" in prompt


def test_small_model_workflow_adds_stepwise_tool_guidance_without_inference_settings():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="small-model")

    assert "one project operation at a time" in prompt
    assert "inspect the result" in prompt


def test_standard_workflow_remains_general():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="standard")

    assert "one project operation at a time" not in prompt
