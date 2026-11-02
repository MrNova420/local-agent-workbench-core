import pytest

from permission_policy import permission_decision, validate_permission_rules


def test_rule_validation_rejects_unknown_fields_and_project_escape_patterns():
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_permission_rules([{"tool": "run_command", "decision": "allow", "timeout": 30}])
    with pytest.raises(ValueError, match="project-relative"):
        validate_permission_rules([{"tool": "read_file", "decision": "deny", "path_glob": "../secrets/**"}])
    with pytest.raises(ValueError, match="project-relative"):
        validate_permission_rules([{"tool": "read_file", "decision": "allow", "path_glob": r"..\secrets\**"}])
    with pytest.raises(ValueError, match="project-relative"):
        validate_permission_rules([{"tool": "read_file", "decision": "deny", "path_glob": r"C:\secrets\**"}])
    with pytest.raises(ValueError, match="project-relative"):
        validate_permission_rules([{"tool": "read_file", "decision": "deny", "path_glob": "C:secrets/**"}])
    with pytest.raises(ValueError, match="decision"):
        validate_permission_rules([{"tool": "read_file", "decision": []}])
    with pytest.raises(ValueError, match="argv token list"):
        validate_permission_rules([{"tool": "run_command", "decision": "allow", "command_prefix": "python -m pytest"}])
    with pytest.raises(ValueError, match="requires run_command"):
        validate_permission_rules([{"tool": "read_file", "decision": "allow", "command_prefix": ["python"]}])
    with pytest.raises(ValueError, match="need command_prefix"):
        validate_permission_rules([{"tool": "run_command", "decision": "deny", "path_glob": "src/**"}])


def test_hard_deny_wins_even_when_an_allow_rule_matches():
    rules = validate_permission_rules([
        {"tool": "run_command", "decision": "allow", "command_prefix": ["python", "-m", "pytest"]},
        {"tool": "run_command", "decision": "deny", "command_prefix": ["python", "-m", "pytest"]},
    ])
    assert permission_decision("run_command", {"argv": ["python", "-m", "pytest", "-q"]}, rules) == "deny"


def test_path_rules_are_project_relative_case_insensitive_and_deny_any_patch_target():
    file_rules = validate_permission_rules([
        {"tool": "edit_file", "decision": "deny", "path_glob": "src/generated/**"},
    ])
    assert permission_decision("edit_file", {"path": "SRC/Generated/api.py"}, file_rules) == "deny"
    patch_rules = validate_permission_rules([
        {"tool": "apply_patch", "decision": "deny", "path_glob": "src/generated/**"},
    ])
    patch = "--- a/src/generated/api.py\n+++ b/src/generated/api.py\n@@ -1 +1 @@\n-old\n+new\n--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-old\n+new\n"
    assert permission_decision("apply_patch", {"patch": patch}, patch_rules) == "deny"


def test_path_allow_requires_every_patch_target_to_match():
    rules = validate_permission_rules([
        {"tool": "apply_patch", "decision": "allow", "path_glob": "src/**"},
    ])
    patch = "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-old\n+new\n--- a/secret.txt\n+++ b/secret.txt\n@@ -1 +1 @@\n-old\n+new\n"
    assert permission_decision("apply_patch", {"patch": patch}, rules) is None


def test_external_directory_rules_accept_absolute_windows_paths_and_match_scoped_access():
    from permission_policy import external_directory_decision

    rules = validate_permission_rules([
        {"tool": "external_directory", "decision": "ask", "path_glob": r"C:\Users\kayde\Documents\**", "workspace": r"C:\project"},
        {"tool": "external_directory", "decision": "deny", "path_glob": r"C:\Users\kayde\Documents\private\**", "workspace": r"C:\project"},
    ])
    assert external_directory_decision(r"C:\Users\kayde\Documents\project\src\main.py", rules) == "ask"
    assert external_directory_decision(r"C:\Users\kayde\Documents", rules) == "ask"
    assert external_directory_decision(r"C:\Users\kayde\Documents\private\token.txt", rules) == "deny"
    with pytest.raises(ValueError, match="project-relative"):
        validate_permission_rules([{"tool": "read_file", "decision": "allow", "path_glob": r"C:\Users\**"}])


def test_external_directory_rule_requires_an_absolute_workspace_identity():
    with pytest.raises(ValueError, match="workspace"):
        validate_permission_rules([{"tool": "external_directory", "decision": "ask", "path_glob": r"C:\Users\kayde\Documents\**", "workspace": "relative"}])
    with pytest.raises(ValueError, match="workspace"):
        validate_permission_rules([{"tool": "external_directory", "decision": "ask", "path_glob": r"C:\Users\kayde\Documents\**"}])
    with pytest.raises(ValueError, match="only valid"):
        validate_permission_rules([{"tool": "read_file", "decision": "allow", "workspace": r"C:\project"}])


def test_external_directory_scope_treats_brackets_in_folder_names_literally(tmp_path):
    from permission_policy import external_directory_decision

    folder = tmp_path / "shared [fixtures]"
    folder.mkdir()
    rules = validate_permission_rules([{"tool": "external_directory", "decision": "allow", "path_glob": str(folder).replace("\\", "/") + "/**", "workspace": str(tmp_path)}])
    assert external_directory_decision(str(folder / "nested" / "file.txt"), rules) == "allow"
    assert external_directory_decision(str(tmp_path / "shared ffixtures" / "file.txt"), rules) is None


def test_external_file_access_requires_a_scoped_rule_and_asks_for_each_ask_scoped_action(tmp_path):
    from desktop_engine import AgentEngine

    project = tmp_path / "project"
    external = tmp_path / "shared"
    project.mkdir(); external.mkdir()
    target = external / "notes.txt"
    target.write_text("external content", encoding="utf-8")
    raw_path = str(target)

    blocked = AgentEngine(project, access="edit")
    assert "outside the selected project" in blocked.state["registry"].run("read_file", {"path": raw_path})

    approvals = []
    asked = AgentEngine(project, access="edit", approve=lambda tool, args: approvals.append((tool, args)) or True,
                        permission_rules=[{"tool": "external_directory", "decision": "ask", "path_glob": str(external).replace("\\", "/") + "/**", "workspace": str(project)}])
    assert asked.state["registry"].run("read_file", {"path": raw_path}) == "external content"
    assert approvals == [("external_directory", {"path": raw_path, "tool": "read_file"})]


def test_external_directory_allow_grants_only_its_folder_and_nested_deny_overrides(tmp_path):
    from desktop_engine import AgentEngine

    project = tmp_path / "project"
    external = tmp_path / "shared"
    private = external / "private"
    project.mkdir(); private.mkdir(parents=True)
    (external / "notes.txt").write_text("shared", encoding="utf-8")
    (private / "token.txt").write_text("secret", encoding="utf-8")
    rules = [
        {"tool": "external_directory", "decision": "allow", "path_glob": str(external).replace("\\", "/") + "/**", "workspace": str(project)},
        {"tool": "external_directory", "decision": "deny", "path_glob": str(private).replace("\\", "/") + "/**", "workspace": str(project)},
    ]
    prompts = []
    engine = AgentEngine(project, access="edit", approve=lambda *args: prompts.append(args) or True, permission_rules=rules)
    registry = engine.state["registry"]
    assert registry.run("read_file", {"path": str(external / "notes.txt")}) == "shared"
    assert "deny rule" in registry.run("read_file", {"path": str(private / "token.txt")})
    assert prompts == []


def test_copy_mode_does_not_write_through_to_an_external_original_folder(tmp_path):
    from desktop_engine import AgentEngine

    project = tmp_path / "project"
    external = tmp_path / "shared"
    project.mkdir(); external.mkdir()
    target = external / "notes.txt"
    rules = [{"tool": "external_directory", "decision": "allow", "path_glob": str(external).replace("\\", "/") + "/**", "workspace": str(project)}]
    engine = AgentEngine(project, access="copy", permission_rules=rules)
    result = engine.state["registry"].run("write_file", {"path": str(target), "content": "change"})
    assert "not staged for copy review" in result
    assert not target.exists()


def test_missing_saved_external_folder_scope_fails_with_repairable_message(tmp_path):
    from desktop_engine import AgentEngine

    project = tmp_path / "project"
    missing = tmp_path / "disconnected"
    project.mkdir()
    engine = AgentEngine(project, access="edit", permission_rules=[
        {"tool": "external_directory", "decision": "allow", "path_glob": str(missing).replace("\\", "/") + "/**", "workspace": str(project)},
    ])
    result = engine.state["registry"].run("read_file", {"path": str(missing / "file.txt")})
    assert "scope is unavailable" in result


def test_external_folder_grant_does_not_follow_a_run_to_another_project(tmp_path):
    from desktop_engine import AgentEngine

    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    external = tmp_path / "shared"
    project_a.mkdir(); project_b.mkdir(); external.mkdir()
    target = external / "private.txt"
    target.write_text("belongs to project A", encoding="utf-8")
    grant = {"tool": "external_directory", "decision": "allow",
             "path_glob": str(external).replace("\\", "/") + "/**", "workspace": str(project_a)}
    engine_a = AgentEngine(project_a, access="edit", permission_rules=[grant])
    engine_b = AgentEngine(project_b, access="edit", permission_rules=[grant])

    assert engine_a.state["registry"].run("read_file", {"path": str(target)}) == "belongs to project A"
    assert "outside the selected project" in engine_b.state["registry"].run("read_file", {"path": str(target)})


def test_command_prefix_matches_argv_tokens_without_matching_a_longer_program_name():
    rules = validate_permission_rules([
        {"tool": "run_command", "decision": "allow", "command_prefix": ["python", "-m", "pytest"]},
    ])
    assert permission_decision("run_command", {"argv": ["python", "-m", "pytest", "-q"]}, rules) == "allow"
    assert permission_decision("run_command", {"argv": ["python-malware", "-m", "pytest"]}, rules) is None


def test_hard_deny_overrides_auto_approval_and_rule_ask_still_opens_prompt(tmp_path):
    from desktop_engine import AgentEngine

    prompts = []
    denied = AgentEngine(tmp_path, approval_mode="auto", approve=lambda *args: prompts.append(args) or True,
                         permission_rules=[{"tool": "run_command", "decision": "deny"}])
    assert denied._approval("run_command", {"argv": ["python", "-V"]}) is False
    assert prompts == []
    assert denied.state["task_checkpoint"]["decisions"][-1]["decision"] == "deny"

    asked = AgentEngine(tmp_path, approval_mode="auto", approve=lambda *args: prompts.append(args) or True,
                        permission_rules=[{"tool": "run_command", "decision": "ask"}])
    assert asked._approval("run_command", {"argv": ["python", "-V"]}) is True
    assert len(prompts) == 1


def test_session_approval_carries_forward_but_explicit_ask_and_deny_rules_override(tmp_path):
    from desktop_engine import AgentEngine

    calls = []
    trusted = AgentEngine(tmp_path, approval_mode="per-session", session_approval_granted=True,
                          approve=lambda *args: calls.append(args) or True)
    assert trusted._approval("write_file", {"path": "src/new.py"}) is True
    assert calls == []

    denied = AgentEngine(tmp_path, approval_mode="per-session", session_approval_granted=True,
                         approve=lambda *args: calls.append(args) or True,
                         permission_rules=[{"tool": "write_file", "decision": "deny"}])
    assert denied._approval("write_file", {"path": "src/new.py"}) is False

    asked = AgentEngine(tmp_path, approval_mode="per-session", session_approval_granted=True,
                        approve=lambda *args: calls.append(args) or True,
                        permission_rules=[{"tool": "write_file", "decision": "ask"}])
    assert asked._approval("write_file", {"path": "src/new.py"}) is True
    assert len(calls) == 1

