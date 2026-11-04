"""Small, durable provenance records for the exact messages and tools sent to a model."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

_RETRIEVED_FILE = re.compile(r"(?m)^--- relevant file: (.+?) \(relevance \d+\) ---$")
_INSTRUCTION_SOURCE = re.compile(r"(?m)^--- ([^\r\n]+) ---$")
_CHECKPOINT_PREFIX = "Active task continuity"


def _estimated_tokens(text: str) -> int:
    """Estimate UTF-8 size without letting malformed imported text break a run."""
    return max(1, (len(text.encode("utf8", errors="surrogatepass")) + 1) // 2)


def _message_source(message: dict[str, Any]) -> str:
    role = str(message.get("role", "unknown"))
    content = str(message.get("content", ""))
    if role == "system":
        return "task_checkpoint" if _CHECKPOINT_PREFIX in content else "system_guidance_and_project_instructions"
    if role == "user" and _CHECKPOINT_PREFIX in content:
        return "task_checkpoint"
    if role == "user" and "[Context notice:" in content:
        return "compaction_notice"
    if role == "user" and "Relevant project files:" in content:
        return "task_prompt_and_retrieved_files"
    if role == "assistant" and message.get("tool_calls"):
        return "assistant_tool_calls"
    if role == "tool":
        return "tool_result"
    return f"{role}_conversation"


def _skill_references(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    references: list[dict[str, str]] = []
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict) or function.get("name") != "skill":
                    continue
                args = function.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if isinstance(args, dict) and args.get("action") in {"load", "read"}:
                    pending.append({"action": str(args["action"]), "name": str(args.get("name", "")), "path": str(args.get("path", ""))})
        elif message.get("role") == "tool" and message.get("tool_name") == "skill" and pending:
            reference = pending.pop(0)
            result = str(message.get("content", ""))
            reference["result"] = "error" if result.startswith(("Unknown", "Could not", "Use a skill-relative")) else "returned"
            references.append(reference)
    return references


def _instruction_sources(text: str, estimate_tokens: Callable[[str], int] = _estimated_tokens) -> list[dict[str, Any]]:
    matches = list(_INSTRUCTION_SOURCE.finditer(text))
    rows = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.end():end].strip()
        rows.append({"path": match.group(1), "characters": len(content),
                     "estimated_tokens": estimate_tokens(content) if content else 0})
    return rows


def build_prompt_manifest(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    request_index: int,
    context: int,
    output_tokens: int,
    input_budget: int,
    skills_enabled: bool,
    skill_sources: list[str] | tuple[str, ...],
    available_skills: list[dict[str, str]],
    catalog_skill_names: list[str],
    project_instructions: str,
    harness_profile: str,
    task_intent: str,
    access: str,
    request_token_estimator: Callable[[str], int] | None = None,
) -> dict[str, Any]:
    """Describe request inputs and their provenance without retaining prompt text itself."""
    request = {"messages": messages, "tools": tools}
    estimate_request = request_token_estimator or _estimated_tokens
    message_rows = []
    retrieved_files: set[str] = set()
    for index, message in enumerate(messages):
        content = str(message.get("content", ""))
        source = _message_source(message)
        files = _RETRIEVED_FILE.findall(content) if source == "task_prompt_and_retrieved_files" else []
        retrieved_files.update(files)
        message_rows.append({
            "index": index,
            "role": str(message.get("role", "unknown")),
            "source": source,
            "characters": len(content),
            "estimated_tokens": estimate_request(content),
            "tool_name": str(message.get("tool_name", "")) or None,
            "retrieved_files": files,
            "has_tool_calls": bool(message.get("tool_calls")),
        })
    tool_names = [str(tool.get("function", {}).get("name", "")) for tool in tools if isinstance(tool.get("function"), dict)]
    return {
        "request_index": request_index,
        "configured_context": context,
        "maximum_output_tokens": output_tokens,
        "input_budget": input_budget,
        "estimated_request_input_tokens": estimate_request(json.dumps(request, ensure_ascii=False, separators=(",", ":"))),
        "message_count": len(messages),
        "messages": message_rows,
        "project_instruction_sources": _instruction_sources(project_instructions, estimate_request) if project_instructions != "(none found)" else [],
        "retrieved_project_files": sorted(retrieved_files, key=str.casefold),
        "checkpoint_included": any(row["source"] == "task_checkpoint" for row in message_rows),
        "compaction_notice_count": sum(row["source"] == "compaction_notice" for row in message_rows),
        "skills": {
            "enabled": skills_enabled,
            "selected_sources": list(skill_sources),
            "available": [{"name": row["name"], "source": row["source"]} for row in available_skills],
            "catalog_entries_in_request_tool_schema": list(catalog_skill_names),
            "catalog_additional_entries_discoverable_by_tool": max(0, len(available_skills) - len(catalog_skill_names)) if skills_enabled else 0,
            "load_references_in_request_history": _skill_references(messages),
        },
        "tools": [{"name": name, "provider": "mcp" if name.startswith("mcp__") else "workbench"} for name in tool_names],
        "workflow": {"harness_profile": harness_profile, "task_intent": task_intent, "effective_access": access},
    }
