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
    registry = ToolRegistry(Workspace(tmp_path, "edit"), lambda *_args: True, events.append)
    schema = next(row["function"]["parameters"] for row in registry.schemas
                  if row["function"]["name"] == "update_plan")

    assert registry.plan == []
    assert "acceptance_criteria" not in schema["required"]
    assert schema["properties"]["acceptance_criteria"] == {"type": "array", "items": {"type": "string"}}
    registry.run("update_plan", {"items": [{"title": "Build", "status": "pending"}],
                                "acceptance_criteria": ["The requested page renders."]})
    assert registry.plan == [{"title": "Build", "status": "pending"}]
    assert events[-1]["acceptanceCriteria"] == ["The requested page renders."]
