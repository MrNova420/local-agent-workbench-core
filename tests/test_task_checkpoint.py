import copy

from task_checkpoint import begin_task, finish_task, record_blocker, record_decision, record_operation, record_plan, render_checkpoint


def test_checkpoint_keeps_active_task_and_user_followups_across_turns():
    first = begin_task(None, prompt="Build a site", turn_id="t1", workspace="C:/p",
                       settings={"context": 65536, "max_tokens": 8192})
    record_plan(first, [{"title": "Inspect", "status": "done"}, {"title": "Build", "status": "pending"}])
    record_operation(first, {"operationId": "op1", "name": "write_file", "status": "completed",
                             "affectedFiles": ["index.html"], "result": "Wrote index.html"})
    second = begin_task(first, prompt="Keep the visual style, then verify", turn_id="t2",
                        workspace="C:/p", settings={"context": 65536, "max_tokens": 8192})

    checkpoint = second["current"]
    assert checkpoint["task_id"] == "t1"
    assert checkpoint["user_updates"] == [{"turn_id": "t2", "instruction": "Keep the visual style, then verify"}]
    assert checkpoint["changed_files"] == ["index.html"]
    assert checkpoint["operations"][0]["result_ref"] == "op1"
    assert "Build a site" in render_checkpoint(second)
    assert "65536" in render_checkpoint(second)


def test_checkpoint_separates_user_decisions_blockers_and_next_action():
    checkpoint = begin_task(None, prompt="Implement feature", turn_id="t1", workspace="C:/p", settings={})
    record_decision(checkpoint, {"kind": "tool_approval", "tool": "run_command", "decision": "denied"})
    record_blocker(checkpoint, "Requested dependency is unavailable.")
    record_plan(checkpoint, [{"title": "Inspect", "status": "done"}, {"title": "Implement", "status": "pending"}])

    rendered = render_checkpoint(checkpoint)

    assert "denied" in rendered
    assert "Requested dependency is unavailable." in rendered
    assert "Implement" in checkpoint["current"]["next_action"]


def test_plan_decisions_and_assumptions_are_checkpointed_without_duplicates():
    checkpoint = begin_task(None, prompt="Build the project", turn_id="t1", workspace="C:/p", settings={})
    items = [{"title": "Implement the existing React app", "status": "in_progress"}]
    decisions = ["Preserve the existing router; replacing it would break deep links."]

    record_plan(checkpoint, items, ["Deep links continue to work."], decisions)
    record_plan(checkpoint, items, ["Deep links continue to work."], decisions)

    assert checkpoint["current"]["next_action"] == "Implement the existing React app"
    assert [row["text"] for row in checkpoint["current"]["decisions"]] == decisions


def test_plan_decisions_recover_from_malformed_legacy_decision_field():
    checkpoint = {"current": {"decisions": "legacy value", "plan": []}}

    record_plan(checkpoint, [{"title": "Continue safely", "status": "pending"}],
                decisions=["Inspect the interrupted write before retrying."])

    decisions = checkpoint["current"]["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["kind"] == "agent_plan_decision"
    assert decisions[0]["text"] == "Inspect the interrupted write before retrying."


def test_task_acceptance_criteria_survive_followup_compaction_and_archive():
    checkpoint = begin_task(None, prompt="Build a site", turn_id="t1", workspace="C:/p", settings={})
    record_plan(checkpoint, [{"title": "Build", "status": "in_progress"}], [
        "The home page renders without a console error.",
        "The primary action navigates to the requested section.",
    ])

    followup = begin_task(checkpoint, prompt="Keep the layout and finish the test", turn_id="t2",
                          workspace="C:/p", settings={})
    rendered = render_checkpoint(followup)

    assert followup["current"]["acceptance_criteria_source"] == "agent"
    assert followup["current"]["acceptance_criteria"] == [
        "The home page renders without a console error.",
        "The primary action navigates to the requested section.",
    ]
    assert "acceptance_criteria" in rendered
    finish_task(followup, "completed", {"status": "inspected"}, outcome="inspected")
    next_task = begin_task(followup, prompt="Another task", turn_id="t3", workspace="C:/p", settings={})
    assert next_task["task_history"][0]["acceptance_criteria"] == followup["current"]["acceptance_criteria"]


def test_uncertain_operation_checkpoint_requires_inspection_before_replay():
    checkpoint = begin_task(None, prompt="Generate the package", turn_id="t1", workspace="C:/p", settings={})
    record_operation(checkpoint, {
        "operationId": "cmd-1", "name": "run_command", "status": "uncertain",
        "affectedFiles": [], "result": "partial output; timed out",
    })

    rendered = render_checkpoint(checkpoint)

    assert checkpoint["current"]["operations"][0]["status"] == "uncertain"
    assert checkpoint["current"]["operations"][0]["result_ref"] == "cmd-1"
    assert "inspect the current workspace" in rendered
    assert "Never treat an uncertain operation as safe to replay automatically." in rendered


def test_budgeted_checkpoint_keeps_the_active_goal_plan_and_recent_progress_with_omission_counts():
    checkpoint = begin_task(None, prompt="Finish the complete project implementation", turn_id="t0",
                            workspace="C:/p", settings={"context": 65536})
    record_plan(checkpoint, [
        {"title": f"Completed stage {index}", "status": "done"} for index in range(30)
    ] + [{"title": "Inspect final build", "status": "in_progress"}])
    for index in range(45):
        checkpoint["current"].setdefault("user_updates", []).append(
            {"turn_id": f"t{index + 1}", "instruction": f"Follow-up detail {index} " + ("important " * 20)})
        record_operation(checkpoint, {"operationId": f"op-{index}", "name": "read_file",
                                      "status": "completed", "result": "detail" * 30})

    original = copy.deepcopy(checkpoint)
    rendered = render_checkpoint(checkpoint, token_budget=420)

    assert "Finish the complete project implementation" in rendered
    assert "Inspect final build" in rendered
    assert "op-44" in rendered
    assert "checkpoint_omissions" in rendered
    assert '"operations"' in rendered
    assert '"user_updates"' in rendered
    assert len(render_checkpoint(checkpoint).encode("utf-8")) > len(rendered.encode("utf-8"))
    assert checkpoint == original


def test_new_task_archives_completed_checkpoint_without_losing_it():
    previous = begin_task(None, prompt="First task", turn_id="t1", workspace="C:/p", settings={})
    finish_task(previous, "completed", {"status": "inspected"}, outcome="inspected")

    next_task = begin_task(previous, prompt="Second task", turn_id="t2", workspace="C:/p", settings={})

    assert next_task["current"]["goal"] == "Second task"
    assert next_task["task_history"][0]["goal"] == "First task"
    assert next_task["task_history"][0]["verification"] == {"status": "inspected"}
    assert next_task["task_history"][0]["workflow_outcome"] == "inspected"
    assert "immediately_preceding_task" in render_checkpoint(next_task)


def test_legacy_checkpoint_shape_migrates_and_continues():
    legacy = {"schema_version": 1, "task_id": "old", "goal": "Continue legacy work",
              "workspace": "C:/p", "settings": {"context": 65536}, "status": "active",
              "operations": [], "changed_files": ["src/main.py"], "plan": []}

    migrated = begin_task(legacy, prompt="Finish the same task", turn_id="t2",
                         workspace="C:/p", settings={"context": 65536})

    assert migrated["current"]["task_id"] == "old"
    assert migrated["current"]["goal"] == "Continue legacy work"
    assert migrated["current"]["changed_files"] == ["src/main.py"]
    assert migrated["current"]["user_updates"][0]["instruction"] == "Finish the same task"


def test_update_plan_schema_accepts_task_specific_criteria_without_making_them_required(tmp_path):
    from desktop_engine import ToolRegistry, Workspace

    events = []
    checkpoint = begin_task(None, prompt="Build a page", turn_id="t1", workspace="C:/p", settings={})
    registry = ToolRegistry(Workspace(tmp_path, "edit"), lambda *_args: True, events.append, task_checkpoint=checkpoint)
    schema = next(row["function"]["parameters"] for row in registry.schemas
                  if row["function"]["name"] == "update_plan")

    assert registry.plan == []
    assert "acceptance_criteria" not in schema["required"]
    assert schema["properties"]["acceptance_criteria"] == {"type": "array", "items": {"type": "string"}}
    assert schema["properties"]["decisions"] == {"type": "array", "items": {"type": "string"}}
    registry.run("update_plan", {"items": [{"title": "Build", "status": "pending"}],
                                "acceptance_criteria": ["The requested page renders."],
                                "decisions": ["Use the existing router to preserve deep links."]})
    assert registry.plan == [{"title": "Build", "status": "pending"}]
    assert events[-1]["acceptanceCriteria"] == ["The requested page renders."]
    assert events[-1]["decisions"] == ["Use the existing router to preserve deep links."]
    assert checkpoint["current"]["decisions"][0]["text"] == "Use the existing router to preserve deep links."


def test_workbench_starter_plan_advances_from_observed_tools_but_agent_plan_is_left_alone(tmp_path):
    from desktop_engine import AgentEngine

    events = []
    plan = [
        {"title": "Inspect relevant project context", "status": "pending", "source": "workbench"},
        {"title": "Refine the checklist with affected files and acceptance criteria", "status": "pending", "source": "workbench"},
        {"title": "Implement the requested changes under the selected access and approval rules", "status": "pending", "source": "workbench"},
        {"title": "Inspect the changes and run relevant approved checks", "status": "pending", "source": "workbench"},
    ]
    checkpoint = {"current": {"plan": copy.deepcopy(plan), "acceptance_criteria": ["The page renders."]}}
    engine = AgentEngine(tmp_path, event=events.append, task_checkpoint=checkpoint)

    engine._advance_workbench_plan("read_file", "completed")
    assert engine.state["registry"].plan[0]["status"] == "completed"
    assert engine.state["task_checkpoint"]["current"]["next_action"] == "Refine the checklist with affected files and acceptance criteria"
    assert events[-1]["type"] == "plan"
    assert events[-1]["acceptanceCriteria"] == ["The page renders."]

    engine._advance_workbench_plan("write_file", "completed")
    assert engine.state["registry"].plan[2]["status"] == "in_progress"
    engine._advance_workbench_plan("run_command", "completed", final=True,
                                   verification="command_completed", project_changed=True)
    assert engine.state["registry"].plan[2]["status"] == "completed"
    assert engine.state["registry"].plan[3]["status"] == "completed"

    registry = engine.state["registry"]
    registry.run("update_plan", {"items": [{"title": "Agent-owned plan", "status": "pending"}]})
    agent_plan = copy.deepcopy(registry.plan)
    engine._advance_workbench_plan("write_file", "completed")
    assert registry.plan == agent_plan

    resumed = AgentEngine(tmp_path, event=events.append, task_checkpoint={
        "current": {"plan": [{"title": "Agent-owned plan", "status": "pending", "source": "agent"}]}
    })
    resumed_plan = copy.deepcopy(resumed.state["registry"].plan)
    resumed._advance_workbench_plan("write_file", "completed")
    assert resumed.state["registry"].plan == resumed_plan
    assert resumed.state["registry"].plan_source == "agent"
