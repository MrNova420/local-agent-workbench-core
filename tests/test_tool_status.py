from desktop_engine import _tool_activity_status


def test_incomplete_project_scans_are_not_reported_as_complete():
    assert _tool_activity_status("search_files", "2 hits\n[Search scan capped at 10 files; results may be incomplete.] ") == "partial"
    assert _tool_activity_status("list_files", "files\n[File listing scan capped; results may be incomplete.] ") == "partial"


def test_failed_project_inspections_are_not_counted_as_verification():
    assert _tool_activity_status("git_diff", "No Git repository rooted at the selected project.") == "failed"
    assert _tool_activity_status("git_diff", "Git diff unavailable: git timed out") == "failed"
    assert _tool_activity_status("read_file", "Requested lines 500-600 are beyond end of file.") == "failed"
    assert _tool_activity_status("read_file", "Invalid start_line: expected a positive 1-based line number.") == "failed"


def test_successful_empty_search_remains_a_completed_operation():
    assert _tool_activity_status("search_files", "No matches.") == "completed"


def test_mcp_errors_after_dispatch_are_uncertain_not_clean_failures():
    assert _tool_activity_status("mcp__calendar__create_event", "Uncertain: response lost after dispatch") == "uncertain"
    assert _tool_activity_status("mcp__calendar__create_event", "Tool error: server returned an error") == "uncertain"
    assert _tool_activity_status("run_command", "Tool error: command not found") == "failed"
