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

