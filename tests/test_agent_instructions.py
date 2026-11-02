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

    assert "small-model workflow: keep the entire requested scope and expandable plan" in prompt.casefold()
    assert "once the next safe step is clear, act and use results to continue" in prompt.casefold()
    assert "batch independent tool calls in one response when their inputs are known" in prompt.casefold()
    assert "keep writes, commands, and mcp calls ordered under their normal approval rules" in prompt.casefold()
    assert "keep all required work and acceptance checks" in prompt.casefold()
    assert "use results to continue" in prompt.casefold()


def test_small_model_workflow_allows_iterative_planning_and_plan_expansion():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="small-model")

    assert "keep the entire requested scope and expandable plan" in prompt.casefold()
    assert "use add_items to add discovered tasks or independent workstreams" in prompt.casefold()
    assert "expand or revise the plan when new evidence" in prompt.casefold()
    assert "plan only enough to identify the next safe, useful action" in prompt.casefold()
    assert "take its next open step through an appropriate structured tool on the next response" in prompt.casefold()
    assert "do not limit the number of planning passes" not in prompt.casefold()
    assert "plan once" not in prompt.casefold()
    assert "after compaction or a user follow-up, retain prior plans and progress" in prompt.casefold()


def test_small_model_continuity_guidance_preserves_discussion_and_plan_intents():
    prompt = agent_system_prompt("Read-only", "", harness_profile="small-model", task_intent="plan")

    assert "task intent: plan only" in prompt.casefold()
    assert "the host exposes only read-only project tools" in prompt.casefold()


def test_standard_workflow_remains_general():
    prompt = agent_system_prompt("Ask before writes", "Project guidance", harness_profile="standard")

    assert "batch independent tool calls in one response" in prompt.casefold()
    assert "small-model workflow:" not in prompt.casefold()
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
