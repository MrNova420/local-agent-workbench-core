"""Durable, factual task state used when old conversation turns leave the prompt."""
from __future__ import annotations

import copy
import json
import re
import time
from typing import Any


def requests_implementation(prompt: str) -> bool:
    folded = prompt.strip().casefold()
    if not folded:
        return False
    if any(phrase in folded for phrase in (
        "plan only", "just plan", "only plan", "review only", "analyze only", "analyse only",
        "explain how to", "what should i change", "how do i fix", "how can i fix", "why does this", "why is this",
    )):
        return False
    if re.match(r"^(what|why|how|when|where)\b", folded):
        return False
    if re.match(r"^plan\b", folded) and not re.search(r"\b(and|then)\s+(build|create|implement|fix|edit|write|modify)\b", folded):
        return False
    return re.search(r"\b(build|create|implement|add|fix|edit|write|modify|refactor|generate|develop|update)\b", folded) is not None


def begin_task(
    previous: dict[str, Any] | None,
    *,
    prompt: str,
    turn_id: str,
    workspace: str,
    settings: dict[str, Any],
    plan: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    previous_state = copy.deepcopy(previous) if isinstance(previous, dict) else {}
    history = previous_state.get("task_history", [])
    if not isinstance(history, list):
        history = []
    current = previous_state.get("current")
    # Migrate the first checkpoint shape introduced before task-history support.
    if not isinstance(current, dict) and previous_state.get("task_id"):
        current = {key: value for key, value in previous_state.items() if key != "task_history"}
    if isinstance(current, dict) and current.get("status") in {"completed", "cancelled", "failed", "stopped"}:
        history.append(current)
        current = None
    if not isinstance(current, dict):
        current = {
            "schema_version": 1,
            "task_id": turn_id,
            "goal": prompt,
            "workspace": workspace,
            "settings": copy.deepcopy(settings),
            "decisions": [],
            "blockers": [],
            "operations": [],
            "changed_files": [],
            "verification": [],
            "plan": copy.deepcopy(plan or []),
        }
    else:
        updates = current.setdefault("user_updates", [])
        updates.append({"turn_id": turn_id, "instruction": prompt})
        current["workspace"] = workspace
        current["settings"] = copy.deepcopy(settings)
        if plan:
            current["plan"] = copy.deepcopy(plan)
    current.update(status="active", active_turn_id=turn_id, updated_at=time.time())
    current.pop("next_action", None)
    return {"task_history": history, "current": current}


def initial_task_plan(prompt: str, intent: str) -> list[dict[str, Any]]:
    """Create a visible, factual starter checklist before a model call begins."""
    if intent not in {"plan", "implement", "auto"}:
        return []
    if not str(prompt).strip():
        return []
    if intent == "plan":
        phases = [
            ("Inspect relevant project context", "pending"),
            ("Refine this request into sequenced steps and surface assumptions", "pending"),
            ("Present the plan without changing workspace files", "pending"),
        ]
    elif intent == "implement" or requests_implementation(prompt):
        phases = [
            ("Inspect relevant project context", "pending"),
            ("Refine the checklist with affected files and acceptance criteria", "pending"),
            ("Implement the requested changes under the selected access and approval rules", "pending"),
            ("Inspect the changes and run relevant approved checks", "pending"),
        ]
    else:
        return []
    return [
        {"title": "Use the task brief and project instructions as the objective", "status": "pending", "source": "workbench"},
        *({"title": title, "status": status, "source": "workbench"} for title, status in phases),
    ]


def record_plan(checkpoint: dict[str, Any], items: Any) -> None:
    checkpoint = _current(checkpoint)
    if isinstance(items, list):
        checkpoint["plan"] = copy.deepcopy(items)
        pending = next((item for item in items if isinstance(item, dict)
                        and str(item.get("status", "pending")).casefold() in {"pending", "in_progress", "in progress"}), None)
        checkpoint["next_action"] = str((pending or {}).get("title") or (pending or {}).get("text") or "Continue the active task plan.")
        checkpoint["updated_at"] = time.time()


def record_decision(checkpoint: dict[str, Any], decision: dict[str, Any]) -> None:
    task = _current(checkpoint)
    task.setdefault("decisions", []).append({**copy.deepcopy(decision), "timestamp": time.time()})
    task["updated_at"] = time.time()


def record_blocker(checkpoint: dict[str, Any], reason: str) -> None:
    task = _current(checkpoint)
    blockers = task.setdefault("blockers", [])
    if reason and reason not in blockers:
        blockers.append(reason)
    task["updated_at"] = time.time()


def record_operation(checkpoint: dict[str, Any], event: dict[str, Any]) -> None:
    checkpoint = _current(checkpoint)
    operation_id = str(event.get("operationId", ""))
    rows = checkpoint.setdefault("operations", [])
    row = next((item for item in rows if item.get("operation_id") == operation_id), None)
    if row is None:
        row = {"operation_id": operation_id}
        rows.append(row)
    row.update(
        name=str(event.get("name", "tool")),
        status=str(event.get("status", "unknown")),
        affected_files=copy.deepcopy(event.get("affectedFiles", row.get("affected_files", []))),
        result_ref=operation_id,
        result_characters=len(str(event.get("result", ""))),
        updated_at=event.get("completedAt", time.time()),
    )
    changed = checkpoint.setdefault("changed_files", [])
    for path in row["affected_files"]:
        if path and path not in changed and row["name"] in {"write_file", "edit_file", "apply_patch"} and row["status"] == "completed":
            changed.append(path)
    checkpoint["updated_at"] = time.time()
    checkpoint["next_action"] = "Continue from the saved plan and inspect the completed operation result before retrying or changing files."


def record_assistant_status(checkpoint: dict[str, Any], message: dict[str, Any]) -> None:
    """Retain the last visible assistant update; complete transcript remains authoritative."""
    checkpoint = _current(checkpoint)
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        checkpoint["last_assistant_update"] = content
    checkpoint["updated_at"] = time.time()


def finish_task(checkpoint: dict[str, Any], status: str, verification: Any = None, blocker: str | None = None, outcome: str | None = None) -> None:
    checkpoint = _current(checkpoint)
    checkpoint["status"] = status
    inferred = verification.get("status") if isinstance(verification, dict) else None
    checkpoint["workflow_outcome"] = outcome or (str(inferred) if inferred else status)
    if verification is not None:
        checkpoint["verification"] = copy.deepcopy(verification)
    if blocker:
        record_blocker(checkpoint, blocker)
    checkpoint["next_action"] = "" if status == "completed" else "Review the saved checkpoint and determine the next safe action."
    checkpoint["updated_at"] = time.time()


def render_checkpoint(checkpoint: dict[str, Any] | None) -> str:
    if not isinstance(checkpoint, dict) or not checkpoint:
        return ""
    root = checkpoint
    checkpoint = _current(root)
    # Tool arguments and complete results remain in the transcript/activity log. The checkpoint
    # carries stable references and verified outcomes without duplicating large file contents.
    visible = {
        key: checkpoint.get(key)
        for key in (
            "schema_version", "task_id", "status", "goal", "user_updates", "workspace",
            "settings", "decisions", "blockers", "plan", "operations", "changed_files", "verification", "workflow_outcome",
            "last_assistant_update", "next_action",
        )
        if checkpoint.get(key) not in (None, [], "")
    }
    history = root.get("task_history", [])
    # Keep the immediately preceding task available when a new task follows a failed,
    # canceled, or completed run. Full history and operation results remain on disk.
    if isinstance(history, list) and history:
        previous = history[-1]
        if isinstance(previous, dict):
            prior_keys = ("task_id", "status", "workflow_outcome", "goal", "workspace", "decisions", "blockers", "plan",
                          "changed_files", "verification", "last_assistant_update", "next_action")
            prior = {key: copy.deepcopy(previous[key]) for key in prior_keys
                     if previous.get(key) not in (None, [], "")}
            if prior:
                visible["immediately_preceding_task"] = prior
    return (
        "Active task continuity checkpoint (facts come from saved user instructions, plans, "
        "and completed host tool events; result_ref points to the complete saved operation result):\n"
        + json.dumps(visible, ensure_ascii=False, separators=(",", ":"))
        + "\nContinue this task from the recorded state. Do not restart planning or claim an operation "
        "was completed unless its saved status says completed. Use the saved result reference or "
        "project tools to inspect details when the recent transcript does not contain them."
    )


def current_task(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return _current(checkpoint)


def _current(checkpoint: dict[str, Any]) -> dict[str, Any]:
    current = checkpoint.get("current")
    return current if isinstance(current, dict) else checkpoint
