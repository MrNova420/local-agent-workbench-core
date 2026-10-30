"""Immutable inputs captured for one background agent turn."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


def parse_run_settings(context_text: str, max_tokens_text: str, temperature_text: str) -> tuple[int, int, float]:
    """Validate user-selected inference limits without silently changing them."""
    try:
        context = int(context_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Context must be a whole number.") from exc
    try:
        max_tokens = int(max_tokens_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Maximum output must be a whole number.") from exc
    try:
        temperature = float(temperature_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Temperature must be a number from 0 to 2.") from exc
    if context < 1024:
        raise ValueError("Context must be at least 1,024 tokens.")
    if max_tokens < 1:
        raise ValueError("Maximum output must be positive.")
    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise ValueError("temperature must be between 0 and 2.")
    return context, max_tokens, temperature


def should_apply_advertised_context(session_active: bool, user_edited_context: bool) -> bool:
    """Metadata is only a default; it must not overwrite a session/user choice."""
    return not session_active and not user_edited_context


def validate_selected_workspace(selected: bool, root: Path | str) -> Path:
    """Require an explicit project choice instead of treating a fallback as consent."""
    if not selected:
        raise ValueError("Choose a project folder before starting a task.")
    try:
        workspace = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ValueError("The selected project folder is unavailable.") from exc
    if not workspace.is_dir():
        raise ValueError("The selected project must be a directory.")
    return workspace


@dataclass(frozen=True)
class TurnContext:
    workspace: Path
    model: str
    context: int
    access: str
    approval_mode: str
    max_tokens: int
    temperature: float
    runtime_options: dict[str, Any]
    prompt: str
    session: dict[str, Any]
    mcp_tools: tuple[dict[str, Any], ...]

    @classmethod
    def capture(
        cls,
        *,
        workspace: Path | str,
        model: str,
        context: int,
        access: str,
        approval_mode: str,
        max_tokens: int,
        temperature: float,
        runtime_options: dict[str, Any],
        prompt: str,
        session: dict[str, Any],
        mcp_tools: list[dict[str, Any]],
    ) -> "TurnContext":
        return cls(
            workspace=Path(workspace).resolve(strict=True),
            model=str(model),
            context=int(context),
            access=str(access),
            approval_mode=str(approval_mode),
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            runtime_options=deepcopy(runtime_options),
            prompt=str(prompt),
            session=deepcopy(session),
            mcp_tools=tuple(deepcopy(mcp_tools)),
        )
