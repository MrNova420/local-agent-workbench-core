from task_checkpoint import begin_task, finish_task, record_operation, record_plan, render_checkpoint


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


def test_new_task_archives_completed_checkpoint_without_losing_it():
    previous = begin_task(None, prompt="First task", turn_id="t1", workspace="C:/p", settings={})
    finish_task(previous, "completed", {"status": "inspected"})

    next_task = begin_task(previous, prompt="Second task", turn_id="t2", workspace="C:/p", settings={})

    assert next_task["current"]["goal"] == "Second task"
    assert next_task["task_history"][0]["goal"] == "First task"
    assert next_task["task_history"][0]["verification"] == {"status": "inspected"}


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
