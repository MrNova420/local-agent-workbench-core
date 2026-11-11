"""Read-only same-task Ollama prefix-cache acceptance check.

Uses the installed Ornith 9B Q4_K_M model, if present, and creates ONE
AgentEngine/turn with two user messages. It neither loads/unloads models nor
writes project files. Provider-reported exact cached-token counts and header
waits are captured. Requires the exact model already resident, to avoid
changing the user's runner state.
"""
from __future__ import annotations
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desktop_engine import AgentEngine  # noqa: E402
from urllib.parse import urlparse
from urllib.request import urlopen

TARGET = "ornith-1.5-9b-q4-k-m:64k"

class UsageRuntime:
    """Same usage-event adapter used by the desktop sidecar."""
    def __init__(self, runtime): self.runtime = runtime
    def cancel(self): self.runtime.cancel()
    def stream(self, state, messages, cancel, emit):
        result = self.runtime.stream(state, messages, cancel, emit)
        usage = {key: result[key] for key in (
            "prompt_eval_count", "prompt_eval_cached_count", "prompt_eval_duration",
            "eval_count", "eval_duration", "load_duration", "total_duration")
            if isinstance(result.get(key), (int, float)) and not isinstance(result.get(key), bool)}
        if usage:
            emit({"type":"generation_usage", "request_index":state.get("request_index"), "usage":usage})
        return result
rows: list[dict] = []

def event(payload: dict) -> None:
    kind = payload.get("type")
    if kind == "request_headers":
        row = {"type": kind, "request_index": payload.get("request_index"),
               "header_wait_seconds": payload.get("header_wait_seconds"), "status": payload.get("http_status")}
        rows.append(row); print(json.dumps(row), flush=True)
    elif kind == "generation_usage":
        row = {"type": kind, "request_index": payload.get("request_index"), **payload.get("usage", {})}
        rows.append(row); print(json.dumps(row), flush=True)

def deny(name, args):
    print(f"DENIED {name} in read-only acceptance", flush=True)
    return False

def main() -> int:
    req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=5) as response:
        installed = {m.get("model") for m in json.loads(response.read()).get("models", [])}
    if TARGET not in installed:
        print(f"Required model not installed: {TARGET}"); return 2
    with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as response:
        loaded = {m.get("name") for m in json.loads(response.read()).get("models", [])}
    if TARGET not in loaded:
        print(f"Refusing to load or evict models; {TARGET} is installed but not resident. Current loaded: {sorted(loaded)}")
        return 3
    print(f"Using already-resident {TARGET}; no model lifecycle change.", flush=True)
    prompt = (
        "Read-only acceptance: inspect nothing, write nothing, run no commands. "
        "Reply READY, then on my next message reply CONFIRMED."
    )
    followup = "As requested, reply with exactly CONFIRMED; do not inspect or change files."
    with tempfile.TemporaryDirectory(prefix="workbench-ornith-cache-") as root:
        engine = AgentEngine(Path(root), model=TARGET, context=65536, max_tokens=8192,
                             temperature=0.2, access="plan", approval_mode="per-action",
                             task_intent="discuss", event=event, approve=deny)
        engine.runtime = UsageRuntime(engine.runtime)
        started = time.monotonic()
        engine.run(prompt)
        first_elapsed = time.monotonic() - started
        print(json.dumps({"type":"user_message_finished", "message":1,
                          "elapsed_seconds":round(first_elapsed,2)}), flush=True)
        started = time.monotonic()
        engine.run(followup, engine.state.get("messages", []))
        print(json.dumps({"type":"user_message_finished", "message":2,
                          "elapsed_seconds":round(time.monotonic()-started,2)}), flush=True)
    headers = [r for r in rows if r["type"] == "request_headers"]
    usage = [r for r in rows if r["type"] == "generation_usage"]
    print("SUMMARY", json.dumps({"requests":len(headers), "headers":headers, "usage":usage}), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
