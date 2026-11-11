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

DEFAULT_TARGET = "ornith-1.5-9b-q4-k-m:64k"


def _selected_target() -> str:
    """Accept an explicitly named already-resident model.

    Passing a model never authorises loading or evicting anything: the probe
    still refuses unless that exact model is already resident.
    """
    argv = sys.argv[1:]
    if "--model" in argv:
        index = argv.index("--model")
        if index + 1 < len(argv):
            return argv[index + 1].strip()
    return DEFAULT_TARGET

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
    if kind in {"complete", "error", "cancelled", "stopped_after_operation"}:
        row = {"type": kind, "workflowOutcome": payload.get("workflowOutcome"), "error": payload.get("error")}
        rows.append(row); print(json.dumps(row), flush=True)
    elif kind == "request_headers":
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
    TARGET = _selected_target()
    req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            installed = {m.get("model") for m in json.loads(response.read()).get("models", [])}
    except Exception as exc:
        print(json.dumps({"pass":False,"blocked":"ollama_unavailable","error":f"{type(exc).__name__}: {exc}"}), flush=True)
        return 3
    if TARGET not in installed:
        print(f"Required model not installed: {TARGET}"); return 2
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as response:
            loaded = {m.get("name") for m in json.loads(response.read()).get("models", [])}
    except Exception as exc:
        print(json.dumps({"pass":False,"blocked":"ollama_runner_state_unavailable","error":f"{type(exc).__name__}: {exc}"}), flush=True)
        return 3
    if TARGET not in loaded:
        print(f"Refusing to load or evict models; {TARGET} is installed but not resident. Current loaded: {sorted(loaded)}")
        return 3
    print(f"Using already-resident {TARGET}; no model lifecycle change.", flush=True)
    prompts = [
        "Read-only check: do not inspect or change files. Reply exactly READY.",
        "Read-only check: do not inspect or change files. Reply exactly CONFIRMED.",
    ]
    message_completions = 0
    with tempfile.TemporaryDirectory(prefix="workbench-ornith-cache-") as root:
        engine = AgentEngine(Path(root), model=TARGET, context=65536, max_tokens=8192,
                             temperature=0.2, access="plan", approval_mode="per-action",
                             task_intent="discuss", event=event, approve=deny)
        engine.runtime = UsageRuntime(engine.runtime)
        for index, prompt in enumerate(prompts, start=1):
            started = time.monotonic()
            result = engine.run(prompt, engine.state.get("messages", []))
            completed = bool(result and result[-1].get("role") == "assistant"
                             and str(result[-1].get("content", "")).strip())
            message_completions += int(completed)
            print(json.dumps({"type":"user_message_finished", "message":index,
                              "content_completed":completed,
                              "elapsed_seconds":round(time.monotonic()-started,2)}), flush=True)
    headers = [r for r in rows if r["type"] == "request_headers"]
    usage = [r for r in rows if r["type"] == "generation_usage"]
    request_ids = sorted({row.get("request_index") for row in headers
                          if isinstance(row.get("request_index"), int)})
    usage_by_request = {row.get("request_index"): row for row in usage
                        if isinstance(row.get("request_index"), int)}
    second_usage = usage_by_request.get(request_ids[1]) if len(request_ids) > 1 else None
    cached = second_usage.get("prompt_eval_cached_count") if second_usage else None
    total = second_usage.get("prompt_eval_count") if second_usage else None
    cache_percent = round(cached * 100 / total) if isinstance(cached, int) and isinstance(total, int) and total > 0 else None
    passed = (message_completions == 2 and len(request_ids) >= 2
              and all(request_id in usage_by_request for request_id in request_ids[:2])
              and isinstance(cached, int) and isinstance(total, int) and total > 0 and cached > 0)
    print("SUMMARY", json.dumps({"pass":passed, "completed_user_messages":message_completions,
          "request_ids":request_ids, "headers":headers, "usage":usage,
          "second_request_cache_percent":cache_percent}), flush=True)
    return 0 if passed else 4

if __name__ == "__main__":
    raise SystemExit(main())
