"""Explicit, persistent per-tool permission rules for local agent actions."""
from __future__ import annotations

import fnmatch
import shlex
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

DECISIONS = {"allow", "ask", "deny"}


def validate_permission_rules(value: Any) -> list[dict[str, Any]]:
    """Validate the portable rule shape; malformed rules fail closed at load time."""
    if not isinstance(value, list):
        raise ValueError("Permission rules must be a list.")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Permission rule {index + 1} must be an object.")
        unknown = set(item) - {"tool", "decision", "path_glob", "command_prefix"}
        if unknown:
            raise ValueError(f"Permission rule {index + 1} has unsupported fields: {', '.join(sorted(unknown))}.")
        tool = item.get("tool")
        decision = item.get("decision")
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(f"Permission rule {index + 1} needs a tool name.")
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError(f"Permission rule {index + 1} decision must be allow, ask, or deny.")
        rule: dict[str, Any] = {"tool": tool.strip(), "decision": decision}
        path_glob = item.get("path_glob")
        if path_glob is not None:
            normalized_glob = path_glob.replace("\\", "/").strip() if isinstance(path_glob, str) else ""
            posix_glob = PurePosixPath(normalized_glob)
            windows_glob = PureWindowsPath(normalized_glob)
            external_scope = tool == "external_directory"
            if external_scope:
                if not normalized_glob or not windows_glob.is_absolute() or not windows_glob.drive or ".." in windows_glob.parts:
                    raise ValueError(f"Permission rule {index + 1} external_directory path_glob must be an absolute path pattern.")
                if not normalized_glob.endswith("/**"):
                    raise ValueError(f"Permission rule {index + 1} external_directory pattern must end in /** to scope a directory.")
            elif (not normalized_glob or posix_glob.is_absolute() or windows_glob.is_absolute()
                    or windows_glob.drive or ".." in posix_glob.parts):
                raise ValueError(f"Permission rule {index + 1} path_glob must be a project-relative pattern.")
            if tool in {"run_command", "run_shell"}:
                raise ValueError(f"Permission rule {index + 1} command tools need command_prefix rather than path_glob.")
            rule["path_glob"] = normalized_glob
        command_prefix = item.get("command_prefix")
        if command_prefix is not None:
            if isinstance(command_prefix, list) and all(isinstance(token, str) and token for token in command_prefix):
                rule["command_prefix"] = list(command_prefix)
            elif isinstance(command_prefix, str) and command_prefix.strip():
                rule["command_prefix"] = command_prefix.strip()
            else:
                raise ValueError(f"Permission rule {index + 1} command_prefix must be text or a non-empty token list.")
            if tool not in {"*", "run_command", "run_shell"}:
                raise ValueError(f"Permission rule {index + 1} command_prefix requires run_command, run_shell, or wildcard tool scope.")
            if tool == "run_command" and not isinstance(rule["command_prefix"], list):
                raise ValueError(f"Permission rule {index + 1} run_command prefix must be an argv token list.")
        if "path_glob" in rule and "command_prefix" in rule:
            raise ValueError(f"Permission rule {index + 1} cannot combine path_glob and command_prefix.")
        rows.append(rule)
    return rows


def _paths(tool: str, args: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if isinstance(args.get("path"), str):
        values.append(args["path"])
    if tool == "apply_patch" and isinstance(args.get("patch"), str):
        for line in args["patch"].splitlines():
            if line.startswith("+++ "):
                path = line[4:].strip()
                if path.startswith("b/"):
                    path = path[2:]
                if path and path != "/dev/null":
                    values.append(path)
    normalized = []
    for value in values:
        path = value.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        normalized.append(path.casefold())
    return normalized


def _command_matches(tool: str, args: dict[str, Any], prefix: Any) -> bool:
    if tool == "run_command":
        command = args.get("argv")
        if isinstance(prefix, list) and isinstance(command, list):
            return len(command) >= len(prefix) and command[:len(prefix)] == prefix
        return False
    if tool == "run_shell":
        command = args.get("command")
        if not isinstance(command, str):
            return False
        if isinstance(prefix, list):
            try:
                return shlex.split(command, posix=False)[:len(prefix)] == prefix
            except ValueError:
                return False
        folded = command.lstrip().casefold()
        expected = str(prefix).casefold()
        return folded == expected or folded.startswith(expected + " ")
    return False


def permission_decision(tool: str, args: dict[str, Any], rules: list[dict[str, Any]] | None) -> str | None:
    """Resolve matching rules with deny > ask > allow; absent matches retain old behavior."""
    matches: list[str] = []
    path_values = _paths(tool, args)
    for rule in rules or []:
        if rule.get("tool") not in {tool, "*"}:
            continue
        if "path_glob" in rule:
            pattern = str(rule["path_glob"]).replace("\\", "/").casefold()
            external_root = pattern[:-3].rstrip("/") if pattern.endswith("/**") else ""
            if external_root.endswith(":"):
                external_root += "/"
            if tool == "external_directory" and external_root:
                base=external_root.rstrip("/")
                if base.endswith(":"):base+="/"
                path_matches=[path.rstrip("/")==base.rstrip("/") or path.startswith(base.rstrip("/") + "/") for path in path_values]
            else:
                path_matches=[fnmatch.fnmatchcase(path,pattern) for path in path_values]
            # An allow rule must cover every target of a multi-file operation.
            # Ask/deny rules can match when any target falls within the scope.
            # Inverting the allow predicate here would let a scoped allow grant
            # access to a patch that also changes files outside the scope.
            rule_matches = bool(path_matches) and (
                all(path_matches) if rule.get("decision") == "allow" else any(path_matches)
            )
            if not rule_matches:
                continue
        if "command_prefix" in rule:
            if tool not in {"run_command", "run_shell"} or not _command_matches(tool, args, rule["command_prefix"]):
                continue
        matches.append(str(rule["decision"]))
    for decision in ("deny", "ask", "allow"):
        if decision in matches:
            return decision
    return None


def external_directory_decision(path: str, rules: list[dict[str, Any]] | None) -> str | None:
    """Resolve the explicit directory scope for an absolute external file target."""
    return permission_decision("external_directory", {"path": path}, rules)
