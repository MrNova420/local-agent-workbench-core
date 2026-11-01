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
            "acceptance_criteria": [],
            "acceptance_criteria_source": "awaiting_agent_refinement",
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
            ("inspect", "Inspect relevant project context", "pending"),
            ("planning", "Refine this request into sequenced steps and surface assumptions", "pending"),
            ("present", "Present the plan without changing workspace files", "pending"),
        ]
    elif intent == "implement" or requests_implementation(prompt):
        phases = [
            ("inspect", "Inspect relevant project context", "pending"),
            ("planning", "Refine the checklist with affected files and acceptance criteria", "pending"),
            ("implementation", "Implement the requested changes under the selected access and approval rules", "pending"),
            ("verification", "Inspect the changes and run relevant approved checks", "pending"),
        ]
    else:
        return []
    return [
        {"title": "Use the task brief and project instructions as the objective", "status": "completed", "source": "workbench", "phase": "task_brief"},
        *({"title": title, "status": status, "source": "workbench", "phase": phase} for phase, title, status in phases),
    ]


def record_plan(checkpoint: dict[str, Any], items: Any, acceptance_criteria: Any = None,
                decisions: Any = None) -> None:
    checkpoint = _current(checkpoint)
    if isinstance(items, list):
        checkpoint["plan"] = copy.deepcopy(items)
        pending = next((item for item in items if isinstance(item, dict)
                        and str(item.get("status", "pending")).casefold() in {"pending", "in_progress", "in progress"}), None)
        checkpoint["next_action"] = str((pending or {}).get("title") or (pending or {}).get("text") or "Continue the active task plan.")
        checkpoint["updated_at"] = time.time()
    if isinstance(acceptance_criteria, list):
        checkpoint["acceptance_criteria"] = [item.strip() for item in acceptance_criteria if isinstance(item, str) and item.strip()]
        checkpoint["acceptance_criteria_source"] = "agent"
        checkpoint["updated_at"] = time.time()
    if isinstance(decisions, list):
        saved = checkpoint.setdefault("decisions", [])
        if not isinstance(saved, list):
            # Preserve safe forward progress when a pre-schema or damaged checkpoint
            # contains a scalar in the decisions slot.
            saved = checkpoint["decisions"] = []
        existing = {str(item.get("text", "")) for item in saved if isinstance(item, dict) and item.get("kind") == "agent_plan_decision"}
        for decision in decisions:
            if not isinstance(decision, str) or not decision.strip() or decision.strip() in existing:
                continue
            saved.append({"kind": "agent_plan_decision", "text": decision.strip(), "timestamp": time.time()})
            existing.add(decision.strip())
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


def render_checkpoint(checkpoint: dict[str, Any] | None, token_budget: int | None = None) -> str:
    if not isinstance(checkpoint, dict) or not checkpoint:
        return ""
    root = checkpoint
    checkpoint = _current(root)
    # Tool arguments and complete results remain in the transcript/activity log. The checkpoint
    # carries stable references and verified outcomes without duplicating large file contents.
    visible = {
        key: copy.deepcopy(checkpoint.get(key))
        for key in (
            "schema_version", "task_id", "status", "goal", "user_updates", "workspace",
            "settings", "decisions", "blockers", "plan", "operations", "changed_files", "verification", "workflow_outcome",
            "acceptance_criteria", "acceptance_criteria_source",
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
            prior_keys = ("task_id", "status", "workflow_outcome", "goal", "workspace", "decisions", "blockers", "plan", "acceptance_criteria",
                          "changed_files", "verification", "last_assistant_update", "next_action")
            prior = {key: copy.deepcopy(previous[key]) for key in prior_keys
                     if previous.get(key) not in (None, [], "")}
            if prior:
                visible["immediately_preceding_task"] = prior
    header = (
        "Active task continuity checkpoint (facts come from saved user instructions, plans, "
        "and completed host tool events; result_ref points to the complete saved operation result):\n"
    )
    footer = (
        "\nContinue this task from the recorded state. Retain the existing plan and completed work; "
        "extend, revise, or add independent workstreams when new evidence, quality improvements, "
        "or user direction warrants it. Never claim an operation was completed unless its saved "
        "status says completed. Use the saved result reference or "
        "project tools to inspect details when the recent transcript does not contain them. "
        "Use read_operation_result with an operation_id from this checkpoint to retrieve saved output. "
        "If an operation status is uncertain, inspect the current workspace and saved operation "
        "result to determine whether it partially took effect before requesting or repeating it. "
        "Never treat an uncertain operation as safe to replay automatically."
    )

    def encode() -> str:
        return header + json.dumps(visible, ensure_ascii=False, separators=(",", ":")) + footer

    def token_estimate(value: str) -> int:
        return max(1, (len(value.encode("utf-8")) + 1) // 2)

    if token_budget is None or token_estimate(encode()) <= max(1, int(token_budget)):
        return encode()

    # Canonical checkpoints on disk remain complete. Only the inference copy is
    # summarized, and every removed history category is counted in-band so the
    # model knows what is omitted and can consult session operations/files.
    omissions: dict[str, int] = {}

    def omit_oldest(key: str, minimum: int = 1) -> bool:
        rows = visible.get(key)
        if not isinstance(rows, list) or len(rows) <= minimum:
            return False
        rows.pop(0)
        omissions[key] = omissions.get(key, 0) + 1
        visible["checkpoint_omissions"] = omissions.copy()
        return True

    # Historical task and prose are less important than the active request, plan,
    # safety decisions, and most recent operation references.
    previous = visible.pop("immediately_preceding_task", None)
    if previous is not None:
        omissions["immediately_preceding_task"] = 1
        visible["checkpoint_omissions"] = omissions.copy()
    last_update = visible.get("last_assistant_update")
    if isinstance(last_update, str) and len(last_update) > 1200:
        visible["last_assistant_update"] = last_update[-1200:]
        omissions["last_assistant_update_characters"] = len(last_update) - 1200
        visible["checkpoint_omissions"] = omissions.copy()

    def current_size() -> int:
        return token_estimate(encode())

    while current_size() > token_budget:
        reduced = False
        for key in ("user_updates", "operations", "decisions", "changed_files", "blockers"):
            if omit_oldest(key):
                reduced = True
                break
        if reduced:
            continue
        plan = visible.get("plan")
        if isinstance(plan, list):
            completed_index = next((index for index, item in enumerate(plan)
                                    if isinstance(item, dict)
                                    and str(item.get("status", "")).casefold() in {"done", "completed"}), None)
            if completed_index is not None:
                plan.pop(completed_index)
                omissions["plan_completed_items"] = omissions.get("plan_completed_items", 0) + 1
                visible["checkpoint_omissions"] = omissions.copy()
                continue
        if isinstance(last_update, str) and last_update:
            current = visible.get("last_assistant_update")
            if isinstance(current, str) and current:
                shortened = current[-max(0, len(current) // 2):] if len(current) > 80 else ""
                visible["last_assistant_update"] = shortened
                omissions["last_assistant_update_characters"] = max(
                    omissions.get("last_assistant_update_characters", 0), len(last_update) - len(shortened))
                visible["checkpoint_omissions"] = omissions.copy()
                continue
        break
    return encode()


def checkpoint_omissions(rendered: str) -> dict[str, int]:
    """Return explicit inference-only omissions embedded in a rendered checkpoint."""
    try:
        payload = rendered.split("\n", 1)[1].split("\nContinue this task", 1)[0]
        visible = json.loads(payload)
        value = visible.get("checkpoint_omissions", {}) if isinstance(visible, dict) else {}
        return {str(key): int(count) for key, count in value.items()
                if isinstance(count, int) and not isinstance(count, bool) and count > 0}
    except (IndexError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def current_task(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return _current(checkpoint)


def _current(checkpoint: dict[str, Any]) -> dict[str, Any]:
    current = checkpoint.get("current")
    return current if isinstance(current, dict) else checkpoint
