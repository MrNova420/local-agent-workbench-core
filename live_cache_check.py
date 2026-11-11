"""Stage 2 live acceptance: measure prompt-prefix cache reuse with sticky tool selection.

Two short read-only AgentEngine discussion requests against the currently
loaded Ollama model, unchanged selected settings (65,536 context / 8,192
output / model-default reasoning). All tool actions are denied; the workspace
is a throwaway temp directory. Reports provider prompt/cached counts and
header wait per request. Does not start, stop, or unload any model; leaves
the runner as found.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_engine import AgentEngine  # noqa: E402

RESULTS: list[dict] = []


def deny_everything(name: str, args) -> bool:
    print(f"[approval] DENIED {name} (live check is read-only)", flush=True)
    return False


def make_event(label: str):
    def event(payload: dict) -> None:
        kind = payload.get("type")
        if kind == "request_headers":
            RESULTS.append({"label": label, "kind": "headers",
                            "wait_s": payload.get("header_wait_seconds"),
                            "status": payload.get("http_status")})
            print(f"[{label}] headers after {payload.get('header_wait_seconds')}s "
                  f"(HTTP {payload.get('http_status')})", flush=True)
        elif kind == "generation_usage":
            RESULTS.append({"label": label, "kind": "usage", **payload.get("usage", {})})
            print(f"[{label}] usage: {payload.get('usage')}", flush=True)
        elif kind in {"error", "cancelled"}:
            print(f"[{label}] {kind}: {payload}", flush=True)
    return event


def main() -> None:
    import urllib.request

    req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=5) as response:
        models = [m.get("model") for m in json.loads(response.read()).get("models", [])]
    if not models:
        print("Ollama reachable but no models installed; nothing to check.")
        return
    model = models[0]
    print(f"Using installed model: {model} (no load/unload commands issued)", flush=True)

    prompts = [
        "Reply with exactly: READY. Do not inspect or change any files.",
        "Reply with exactly: CONFIRMED. Do not inspect or change any files.",
    ]
    with tempfile.TemporaryDirectory(prefix="workbench-cache-check-") as root:
        for index, prompt in enumerate(prompts, start=1):
            label = f"request{index}"
            engine = AgentEngine(
                Path(root), model=model, context=65536, max_tokens=8192, temperature=0.2,
                access="plan", approval_mode="per-action", task_intent="discuss",
                approve=deny_everything, event=make_event(label),
            )
            started = time.monotonic()
            engine.run(prompt)
            print(f"[{label}] run finished in {time.monotonic() - started:.1f}s", flush=True)

    print("\n=== SUMMARY ===", flush=True)
    for row in RESULTS:
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
