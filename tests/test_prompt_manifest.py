from prompt_manifest import build_prompt_manifest
from desktop_engine import AgentEngine


def test_manifest_accounts_for_the_exact_request_sources_without_copying_prompt_content():
    messages = [
        {"role": "system", "content": "Coding guidance\n--- AGENTS.md ---\nUse the project convention."},
        {"role": "system", "content": "Active task continuity checkpoint (facts from saved state): {...}"},
        {"role": "user", "content": "Add a page\n\nRelevant project files:\n--- relevant file: src/app.py (relevance 8) ---\nsource content"},
    ]
    tools = [
        {"function": {"name": "read_file"}},
        {"function": {"name": "mcp__search"}},
    ]

    manifest = build_prompt_manifest(
        messages, tools, request_index=2, context=65536, output_tokens=8192, input_budget=57344,
        skills_enabled=True, skill_sources=["project", "user"],
        available_skills=[{"name": "python-review", "source": "project"}, {"name": "testing", "source": "user"}],
        catalog_skill_names=["python-review"], project_instructions="--- AGENTS.md ---\nUse the project convention.",
        harness_profile="small-model", task_intent="implement", access="ask",
    )

    assert manifest["request_index"] == 2
    assert manifest["estimated_request_input_tokens"] > 0
    assert manifest["checkpoint_included"] is True
    assert manifest["project_instruction_sources"] == [{"path": "AGENTS.md", "characters": 27, "estimated_tokens": 14}]
    assert manifest["retrieved_project_files"] == ["src/app.py"]
    assert manifest["skills"]["available"] == [
        {"name": "python-review", "source": "project"}, {"name": "testing", "source": "user"},
    ]
    assert manifest["skills"]["catalog_entries_in_request_tool_schema"] == ["python-review"]
    assert manifest["tools"] == [{"name": "read_file", "provider": "workbench"}, {"name": "mcp__search", "provider": "mcp"}]
    assert "source content" not in str(manifest)


def test_manifest_recognizes_checkpoint_embedded_in_merged_system_guidance():
    messages = [{
        "role": "system",
        "content": "Coding guidance\nFollow project instructions.\n\n"
                   "Active task continuity checkpoint (facts from saved state): {\"task_id\":\"t1\"}",
    }]

    manifest = build_prompt_manifest(
        messages, [], request_index=1, context=65536, output_tokens=8192, input_budget=57344,
        skills_enabled=False, skill_sources=[], available_skills=[], catalog_skill_names=[],
        project_instructions="(none found)", harness_profile="standard", task_intent="implement", access="ask",
    )

    assert manifest["checkpoint_included"] is True
    assert manifest["messages"][0]["source"] == "task_checkpoint"


def test_manifest_reports_only_skill_calls_with_corresponding_tool_results():
    messages = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "skill", "arguments": {"action": "load", "name": "python-review"}}}]},
        {"role": "tool", "tool_name": "skill", "content": "Skill: python-review\nreview content"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "skill", "arguments": {"action": "load", "name": "missing"}}}]},
        {"role": "tool", "tool_name": "skill", "content": "Unknown or unavailable skill"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "skill", "arguments": {"action": "list"}}}]},
        {"role": "tool", "tool_name": "skill", "content": "python-review"},
    ]
    manifest = build_prompt_manifest(
        messages, [], request_index=1, context=4096, output_tokens=512, input_budget=3072,
        skills_enabled=True, skill_sources=["project"], available_skills=[], catalog_skill_names=[],
        project_instructions="(none found)", harness_profile="standard", task_intent="auto", access="ask",
    )

    assert manifest["skills"]["load_references_in_request_history"] == [
        {"action": "load", "name": "python-review", "path": "", "result": "returned"},
        {"action": "load", "name": "missing", "path": "", "result": "error"},
    ]


def test_engine_emits_manifest_for_the_actual_tool_catalog_before_waiting(tmp_path):
    skill = tmp_path / ".agents" / "skills" / "python-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: python-review\ndescription: Review Python changes\n---\nCheck the patch.", encoding="utf8",
    )
    events = []
    engine = AgentEngine(tmp_path, access="plan", event=events.append, skill_sources=["project"])
    engine.runtime.stream = lambda *_args: {"message": {"role": "assistant", "content": "Here is the analysis."}}

    engine.run("Explain the project and review its Python conventions.")

    manifest_index = next(index for index, event in enumerate(events) if event.get("type") == "prompt_manifest")
    waiting_index = next(index for index, event in enumerate(events) if event.get("type") == "phase" and event.get("phase") == "waiting")
    manifest = events[manifest_index]
    assert manifest_index < waiting_index
    assert manifest["skills"]["catalog_entries_in_request_tool_schema"] == ["python-review"]
    assert "write_file" not in {tool["name"] for tool in manifest["tools"]}
    assert manifest["workflow"]["effective_access"] == "plan"
