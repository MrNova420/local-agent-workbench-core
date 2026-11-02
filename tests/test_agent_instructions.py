from desktop_engine import agent_system_prompt, approval_policy_guidance


def test_agent_prompt_uses_structured_tools_to_start_actions_and_trigger_approval():
    prompt = agent_system_prompt("Request approval before every project file change and command.", "Project-specific guidance")

    assert "Do not ask for approval in chat text" in prompt
    assert "submit the action through its structured tool" in prompt
    assert "Approval mode: per-action" in prompt
    assert "a separate host decision is required for each eligible action" in prompt
    assert "Do not claim that an action ran unless its tool result confirms it" in prompt
    assert "missing requirement or user choice" in prompt
    assert "never for permission to run a file change or command" in prompt
    assert "Project-specific guidance" in prompt
    assert "concise user-relevant design decisions and assumptions" in prompt
    assert "not private chain-of-thought" in prompt


def test_tool_failure_recovery_is_evidence_based_and_preserves_retry_scope():
    prompt = agent_system_prompt("Editing is allowed", "")

    assert "inspect the reported error and current project state" in prompt.casefold()
    assert "correct the cause before retrying" in prompt.casefold()
    assert "inspect for partial effects before retrying" in prompt.casefold()
    assert "do not retry denied actions or route around a denial" in prompt.casefold()
    assert "no fixed retry or iteration cap" in prompt.casefold()


def test_implementation_pacing_prefers_early_tool_work_without_a_scope_or_iteration_cap():
    for profile in ("standard", "small-model"):
        prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile=profile)
        assert "plan only enough to identify the next safe, useful action" in prompt
        assert "Do not repeat a plan or reread a checkpoint section" in prompt
        assert "This is a pacing preference, not a cap" in prompt
        assert "context, output, or runtime" in prompt
        assert "revise the plan when new evidence or user direction changes scope or priorities" in prompt.casefold()
        assert "do not keep reworking a plan for speculative improvements" in prompt.casefold()


def test_small_model_workflow_guides_adaptive_work_without_a_fixed_tool_call_limit():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="small-model")

    assert "group independent read-only operations when useful" in prompt.casefold()
    assert "batch independent tool calls in one response when their inputs are already known" in prompt.casefold()
    assert "writes, commands, and mcp calls preserve order and approval" in prompt.casefold()
    assert "choose action sizes that fit the task" in prompt.casefold()
    assert "do not skip required project work or validation" in prompt.casefold()
    assert "inspect results" in prompt.casefold()


def test_small_model_workflow_allows_iterative_planning_and_plan_expansion():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="small-model")

    assert "living, expandable plan" in prompt.casefold()
    assert "add independent workstreams" in prompt.casefold()
    assert "revisit or revise" in prompt.casefold()
    assert "plan only enough to identify the next safe, useful action" in prompt.casefold()
    assert "move into implementation or inspection without waiting for a perfect plan" in prompt.casefold()
    assert "do not limit the number of planning passes" not in prompt.casefold()
    assert "plan once" not in prompt.casefold()
    assert "take the next unfinished action" in prompt.casefold()
    assert "after compaction, continue from the saved checkpoint" in prompt.casefold()


def test_small_model_continuity_guidance_preserves_discussion_and_plan_intents():
    prompt = agent_system_prompt("Read-only", "", harness_profile="small-model", task_intent="plan")

    assert "for implementation tasks" in prompt.casefold()
    assert "respect discussion and plan-only intent" in prompt.casefold()


def test_standard_workflow_remains_general():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="standard")

    assert "group independent read-only operations when useful" not in prompt.casefold()
    assert "living, expandable plan" not in prompt.casefold()


def test_approval_guidance_matches_the_selected_host_mode_and_rule_precedence():
    expected = {
        "auto": "eligible actions run automatically",
        "per-action": "a separate host decision is required for each eligible action",
        "per-task": "the first eligible action in each task requires a host decision",
        "per-session": "the first eligible action in each session requires a host decision",
    }
    for mode, phrase in expected.items():
        guidance = approval_policy_guidance(mode)
        assert phrase in guidance.casefold()
        assert "persistent deny rules always block" in guidance.casefold()
        assert "persistent ask rules still require a host decision" in guidance.casefold()
        prompt = agent_system_prompt("Editing is allowed", "", approval_guidance=guidance)
        assert guidance in prompt


def test_default_approval_guidance_never_claims_commands_always_require_manual_review():
    prompt = agent_system_prompt("Edit files; commands use host policy", "", approval_guidance=approval_policy_guidance("auto"))

    assert "Approval mode: auto" in prompt
    assert "run_shell" in prompt
    assert "eligible actions run automatically" in prompt.casefold()
    assert "always requires approval" not in prompt.casefold()


def test_engine_injects_selected_approval_mode_and_truthful_command_schemas(tmp_path):
    from desktop_engine import AgentEngine

    engine = AgentEngine(tmp_path, access="edit", approval_mode="auto", task_intent="discuss")
    requests = []
    engine.runtime.stream = lambda _state, messages, *_args: (
        requests.append(messages.copy()) or {"message": {"role": "assistant", "content": "Reviewed."}}
    )

    engine.run("Review the project configuration.")

    system = requests[0][0]["content"]
    schemas = engine.state["registry"].schemas
    command_descriptions = " ".join(
        schema["function"]["description"] for schema in schemas
        if schema["function"]["name"] in {"run_command", "run_shell"}
    )
    assert "Approval mode: auto" in system
    assert "eligible actions run automatically" in system.casefold()
    assert "subject to the configured host approval mode" in command_descriptions
    assert "always requires approval" not in command_descriptions.casefold()
