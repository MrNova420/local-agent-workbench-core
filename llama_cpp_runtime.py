"""Owned llama.cpp server and OpenAI-compatible streaming adapter for GGUF."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from cancellable_http import interrupt_connection, open_http_stream
from desktop_engine import InferenceStreamError, RequestCancelled, _inference_retry_metadata


class LlamaCppServer:
    def __init__(self, data_dir: Path | str, host: str = "127.0.0.1", port: int = 11436):
        self.data_dir = Path(data_dir).resolve()
        self.host, self.port = host, int(port)
        self.proc: subprocess.Popen | None = None
        self.owned = False
        self.binary: Path | None = None
        self.model: Path | None = None
        self.context: int | None = None
        self.reasoning: bool | str | None = None
        self.lock = threading.RLock()
        self.log_path = self.data_dir / "runtime" / "llama-server.log"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=1.5) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _runtime_props(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(self.base_url + "/props", timeout=2) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, AttributeError):
            return {}

    def _effective_context(self) -> int | None:
        value = (self._runtime_props().get("default_generation_settings") or {}).get("n_ctx")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _reasoning_capability(props: dict[str, Any]) -> dict[str, Any]:
        """Expose only reasoning modes explicitly reported by the loaded template."""
        caps = props.get("chat_template_caps")
        levels = caps.get("reasoning_effort_levels") if isinstance(caps, dict) else None
        values = list(dict.fromkeys(value for value in levels if isinstance(value, str) and value.strip())) if isinstance(levels, list) else []
        # llama.cpp's explicit reasoning-off control is available alongside
        # template-probed effort levels; do not advertise it for unknown templates.
        if values:
            values.insert(0, False)
        support = "levels" if values else "unknown"
        return {"support": support, "default": None, "values": values}

    def _slot_metrics(self) -> dict[str, Any]:
        """Read public llama-server slot counters without exposing prompt text."""
        try:
            with urllib.request.urlopen(self.base_url + "/slots", timeout=0.5) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, list):
                return {"available": False, "slots": []}
            slots = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                next_token = item.get("next_token")
                if isinstance(next_token, list):
                    next_token = next_token[0] if next_token else {}
                if not isinstance(next_token, dict):
                    next_token = {}
                fields = ("n_ctx", "n_prompt_tokens", "n_prompt_tokens_cache",
                          "n_prompt_tokens_processed")
                values = {key: item.get(key) for key in fields
                          if isinstance(item.get(key), int) and not isinstance(item.get(key), bool)}
                decoded = next_token.get("n_decoded")
                if isinstance(decoded, int) and not isinstance(decoded, bool):
                    values["n_decoded"] = decoded
                cache = values.get("n_prompt_tokens_cache")
                processed = values.get("n_prompt_tokens_processed")
                if isinstance(cache, int) and isinstance(processed, int) and isinstance(decoded, int):
                    values["context_tokens_in_slot"] = cache + processed + decoded
                slots.append({"id": item.get("id"), "is_processing": item.get("is_processing") is True,
                              **values})
            return {"available": True, "slots": slots, "sampled_at": time.time()}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return {"available": False, "slots": []}

    def load(self, binary: Path | str, model: Path | str, context: int, reasoning: bool | str | None,
             cancel: threading.Event, emit: Callable[[dict[str, Any]], None]) -> None:
        exe, gguf = Path(binary).resolve(strict=True), Path(model).resolve(strict=True)
        if not exe.is_file() or exe.suffix.casefold() != ".exe":
            raise ValueError("Choose a llama.cpp Windows server executable (.exe).")
        if not gguf.is_file() or gguf.suffix.casefold() != ".gguf":
            raise ValueError("Choose a local GGUF model file.")
        if isinstance(context, bool) or not isinstance(context, int) or context < 1:
            raise ValueError("Runtime context must be a positive integer.")
        valid_reasoning = {None, True, False, "minimal", "low", "medium", "high", "xhigh", "max"}
        if reasoning not in valid_reasoning:
            raise ValueError("Unsupported llama.cpp reasoning selection.")
        with self.lock:
            if self.owned and self.proc and self.proc.poll() is None and (self.binary, self.model, self.context, self.reasoning) == (exe, gguf, context, reasoning):
                emit({"type": "llama_ready", "model": str(gguf), "context": context, "effective_context": self._effective_context(), "reasoning": reasoning, "owned": True})
                return
            if self.owned:
                self.stop()
            elif self._healthy():
                raise RuntimeError("The llama.cpp port is already in use by another process. The app will not attach to or stop it.")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            args = [str(exe), "--model", str(gguf), "--ctx-size", str(context), "--host", self.host,
                    "--port", str(self.port), "--alias", "local-workbench", "--jinja"]
            if isinstance(reasoning, bool):
                args.extend(("--reasoning", "on" if reasoning else "off"))
            elif reasoning in {"minimal", "low", "medium", "high", "xhigh", "max"}:
                args.extend(("--reasoning-effort", reasoning))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            with self.log_path.open("ab") as log:
                proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                        shell=False, close_fds=True, creationflags=flags)
            self.proc, self.owned = proc, True
            self.binary, self.model, self.context, self.reasoning = exe, gguf, context, reasoning
        emit({"type": "llama_loading", "model": str(gguf), "context": context, "owned": True, "pid": proc.pid})
        while True:
            if cancel.is_set():
                self.stop()
                raise RequestCancelled("GGUF model load cancelled.")
            if proc.poll() is not None:
                tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
                with self.lock:
                    if self.proc is proc:
                        self.proc, self.owned = None, False
                raise RuntimeError(f"llama.cpp server exited with code {proc.returncode}. {tail}")
            if self._healthy():
                effective = self._effective_context()
                if effective is not None and effective != context:
                    self.stop()
                    raise RuntimeError(f"llama.cpp allocated {effective:,} context tokens; {context:,} were requested. The model was stopped rather than using a lower context.")
                emit({"type": "llama_ready", "model": str(gguf), "context": context, "effective_context": effective, "reasoning": reasoning, "owned": True, "pid": proc.pid})
                return
            time.sleep(0.5)

    def stop(self) -> bool:
        with self.lock:
            proc = self.proc if self.owned else None
            if not proc:
                return False
            self.proc, self.owned = None, False
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        return True

    def status(self, include_slot_metrics: bool = False) -> dict[str, Any]:
        with self.lock:
            live = bool(self.owned and self.proc and self.proc.poll() is None)
            model = str(self.model) if live and self.model else None
            context = self.context if live else None
            binary = str(self.binary) if live and self.binary else None
        props = self._runtime_props() if live else {}
        effective_value = (props.get("default_generation_settings") or {}).get("n_ctx")
        effective = effective_value if isinstance(effective_value, int) and not isinstance(effective_value, bool) else None
        return {"running": live, "owned": live, "model": model, "context": context,
                "effective_context": effective, "reasoning": self.reasoning if live else None,
                "reasoning_capability": self._reasoning_capability(props) if live else {"support": "unknown", "default": None, "values": []},
                "binary": binary, "base_url": self.base_url if live else None,
                "slot_metrics": self._slot_metrics() if live and include_slot_metrics else None}

    def shutdown(self) -> None:
        self.stop()


class LlamaCppRuntime:
    def __init__(self, server: LlamaCppServer):
        self.server = server
        self.response = None
        self.connection = None
        self.lock = threading.Lock()

    def cancel(self) -> None:
        with self.lock:
            connection = self.connection
            response = self.response
        threading.Thread(target=interrupt_connection, args=(connection, response), daemon=True).start()

    def stream(self, state: dict[str, Any], messages: list[dict[str, Any]], cancel: threading.Event,
               emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        status = self.server.status()
        if not status["running"]:
            raise RuntimeError("Load this GGUF through the app before starting a task.")
        if int(state["context"]) != status["context"]:
            raise RuntimeError(f"Requested context {state['context']:,} does not match loaded runtime context {status['context']:,}. Load the model with the selected context first.")
        if state.get("reasoning") != status.get("reasoning"):
            raise ValueError("The selected reasoning setting does not match the loaded llama.cpp model. Load it from Model Library with this setting first.")
        tools = state["registry"].schemas + state.get("mcp_tools", [])
        payload = {"model": "local-workbench", "messages": messages, "stream": True,
                   "temperature": state["temperature"], "max_tokens": state["max_tokens"],
                   "tools": tools, "stream_options": {"include_usage": True}}
        reasoning_budget_tokens = state.get("reasoning_budget_tokens")
        if reasoning_budget_tokens is not None:
            if (isinstance(reasoning_budget_tokens, bool) or not isinstance(reasoning_budget_tokens, int)
                    or reasoning_budget_tokens < 0 or reasoning_budget_tokens > 2_147_483_647):
                raise ValueError("Thinking budget must be a non-negative integer or unlimited.")
            payload["thinking_budget_tokens"] = reasoning_budget_tokens
        emit({"type": "inference_settings", "provider": "llama.cpp", "request_api": "/v1/chat/completions",
              "request_index": state.get("request_index"), "model": state["model"], "api_model": payload["model"],
              "stream": payload["stream"], "tool_count": len(payload["tools"]),
              "requested_context": state["context"], "max_output_tokens": state["max_tokens"],
              "reasoning_budget_tokens": reasoning_budget_tokens,
              "reasoning_budget_applied_by": "request" if reasoning_budget_tokens is not None else "unlimited",
              "reasoning_override": None, "reasoning_field_sent": False,
              "runtime_reasoning": status.get("reasoning"),
              "request_options": {"temperature": payload["temperature"], "max_tokens": payload["max_tokens"]},
              "additional_fields": {key: payload[key] for key in ("stream_options", "thinking_budget_tokens") if key in payload},
              "runtime_context": status["context"]})
        req = urllib.request.Request(self.server.base_url + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "Accept": "text/event-stream"}, method="POST")
        result: dict[str, Any] = {"message": {"role": "assistant", "content": ""}}
        calls: dict[int, dict[str, Any]] = {}
        started, first_output, user_facing_output = time.monotonic(), True, False
        done_marker_received = False
        dispatch_time: dict[str, float | None] = {"value": None}
        request_index = state.get("request_index")
        try:
            def register(connection, response):
                with self.lock:
                    self.connection = connection
                    self.response = response
            def dispatched():
                dispatch_time["value"] = time.monotonic()
                emit({"type": "request_dispatched", "provider": "llama.cpp", "request_index": request_index})
            def headers_received(response):
                elapsed = time.monotonic() - dispatch_time["value"] if dispatch_time["value"] is not None else None
                emit({"type": "request_headers", "provider": "llama.cpp", "request_index": request_index,
                      "http_status": response.status, "http_reason": response.reason,
                      "header_wait_seconds": elapsed})
            response = open_http_stream(req, cancel, register, on_dispatched=dispatched, on_headers=headers_received)
            with self.lock:
                if self.response is None:
                    self.response = response
            while True:
                if cancel.is_set():
                    raise RequestCancelled("Cancelled")
                line = response.readline()
                if not line:
                    break
                if not line.startswith(b"data:"):
                    continue
                raw = line[5:].strip()
                if raw == b"[DONE]":
                    done_marker_received = True
                    break
                if not raw:
                    continue
                item = json.loads(raw.decode("utf-8"))
                if isinstance(item.get("usage"), dict):
                    usage = item["usage"]
                    if isinstance(usage.get("completion_tokens"), int):
                        result["eval_count"] = usage["completion_tokens"]
                    if isinstance(usage.get("prompt_tokens"), int):
                        result["prompt_eval_count"] = usage["prompt_tokens"]
                    prompt_details = usage.get("prompt_tokens_details")
                    cached_tokens = prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None
                    if isinstance(cached_tokens, int):
                        result["prompt_eval_cached_count"] = cached_tokens
                timings = item.get("timings")
                if isinstance(timings, dict):
                    cache_tokens = timings.get("cache_n")
                    prompt_tokens = timings.get("prompt_n")
                    if isinstance(cache_tokens, int) and isinstance(prompt_tokens, int):
                        result["prompt_eval_count"] = cache_tokens + prompt_tokens
                        result["prompt_eval_cached_count"] = cache_tokens
                    if isinstance(timings.get("prompt_ms"), (int, float)):
                        result["prompt_eval_duration"] = int(timings["prompt_ms"] * 1_000_000)
                    if isinstance(timings.get("predicted_n"), int):
                        result["eval_count"] = timings["predicted_n"]
                    if isinstance(timings.get("predicted_ms"), (int, float)):
                        result["eval_duration"] = int(timings["predicted_ms"] * 1_000_000)
                for choice in item.get("choices", []):
                    if choice.get("finish_reason") is not None:
                        result["done_reason"] = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    thinking = delta.get("reasoning_content") or ""
                    if first_output and (text or thinking or delta.get("tool_calls")):
                        first_output = False
                        phase = "generating" if text or delta.get("tool_calls") else "reasoning"
                        user_facing_output = phase == "generating"
                        emit({"type": "phase", "phase": phase, "elapsed": time.monotonic() - started})
                    elif not user_facing_output and (text or delta.get("tool_calls")):
                        user_facing_output = True
                        emit({"type": "phase", "phase": "generating", "elapsed": time.monotonic() - started})
                    if thinking:
                        result["message"]["thinking"] = result["message"].get("thinking", "") + thinking
                        emit({"type": "thinking", "text": thinking, "at": time.monotonic()})
                    if text:
                        result["message"]["content"] += text
                        emit({"type": "text", "text": text, "at": time.monotonic()})
                    for call in delta.get("tool_calls") or []:
                        index = int(call.get("index", 0))
                        slot = calls.setdefault(index, {"id": f"call-{index}", "type": "function", "function": {"name": "", "arguments": ""}})
                        if call.get("id"):
                            slot["id"] = call["id"]
                        function = call.get("function") or {}
                        if function.get("name"):
                            slot["function"]["name"] += function["name"]
                        if isinstance(function.get("arguments"), str):
                            slot["function"]["arguments"] += function["arguments"]
                        emit({"type": "tool_call_progress", "provider": "llama.cpp",
                              "request_index": request_index, "call_index": index,
                              "tool_name": slot["function"].get("name", ""),
                              "argument_characters": len(slot["function"].get("arguments", "")),
                              "at": time.time()})
            if calls:
                result["message"]["tool_calls"] = [calls[index] for index in sorted(calls)]
            emit({"type": "request_complete", "provider": "llama.cpp", "request_index": request_index,
                  "provider_done": done_marker_received,
                  "provider_done_reason": result.get("done_reason")})
            return result
        except Exception as exc:
            with self.lock:
                active_response = self.response
                headers_received_flag = active_response is not None
            failure_phase = "provider_http_error" if headers_received_flag and getattr(active_response, "status", 0) >= 400 else "streaming_response" if headers_received_flag else "waiting_response_headers" if dispatch_time["value"] is not None else "connecting_or_sending"
            emit({"type": "request_error", "provider": "llama.cpp", "request_index": request_index,
                  "phase": failure_phase,
                  "cancelled": cancel.is_set(), "error": str(exc)})
            if cancel.is_set():
                partial = {key: result["message"][key] for key in ("content", "thinking") if result["message"].get(key)}
                raise RequestCancelled("Cancelled", {"role": "assistant", **partial} if partial else None) from exc
            partial = {key: result["message"][key] for key in ("content", "thinking") if result["message"].get(key)}
            status_code, retry_after, retryable = _inference_retry_metadata(exc, active_response)
            raise InferenceStreamError(str(exc), {"role": "assistant", **partial} if partial else None,
                                       status_code=status_code, retry_after=retry_after,
                                       retryable=retryable and not partial,
                                       failure_phase=failure_phase) from exc
        finally:
            with self.lock:
                connection = self.connection
                active_response = self.response
                self.connection = None
                self.response = None
            interrupt_connection(connection, active_response)
