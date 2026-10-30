"""Immutable inputs captured for one background agent turn."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
