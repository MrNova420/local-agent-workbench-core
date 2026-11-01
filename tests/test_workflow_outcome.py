from desktop_engine import _workflow_outcome


def test_workflow_outcome_distinguishes_plan_change_inspection_and_command():
    assert _workflow_outcome("plan", False, False, {"status": "not_verified"}, []) == "plan_only"
    assert _workflow_outcome("edit", False, True, {"status": "not_verified"}, []) == "changed"
    assert _workflow_outcome("edit", False, True, {"status": "inspected"}, []) == "inspected"
    assert _workflow_outcome("edit", False, True, {"status": "command_completed"}, []) == "command_completed"
    assert _workflow_outcome("edit", False, True, {"status": "command_failed"}, []) == "command_failed"
    assert _workflow_outcome("ask", True, False, {"status": "not_verified"}, []) == "blocked"
    assert _workflow_outcome("ask", False, False, {"status": "not_verified"}, []) == "discussion"

