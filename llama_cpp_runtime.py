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

from desktop_engine import RequestCancelled


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

    def _effective_context(self) -> int | None:
        try:
            with urllib.request.urlopen(self.base_url + "/props", timeout=2) as response:
                data = json.loads(response.read().decode("utf-8"))
            value = (data.get("default_generation_settings") or {}).get("n_ctx")
            return value if isinstance(value, int) and not isinstance(value, bool) else None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, AttributeError):
            return None

    def load(self, binary: Path | str, model: Path | str, context: int, reasoning: bool | str | None,
             cancel: threading.Event, emit: Callable[[dict[str, Any]], None]) -> None:
        exe, gguf = Path(binary).resolve(strict=True), Path(model).resolve(strict=True)
        if not exe.is_file() or exe.suffix.casefold() != ".exe":
            raise ValueError("Choose a llama.cpp Windows server executable (.exe).")
        if not gguf.is_file() or gguf.suffix.casefold() != ".gguf":
            raise ValueError("Choose a local GGUF model file.")
        if isinstance(context, bool) or not isinstance(context, int) or context < 1024:
            raise ValueError("Runtime context must be an integer of at least 1,024 tokens.")
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

    def status(self) -> dict[str, Any]:
        with self.lock:
            live = bool(self.owned and self.proc and self.proc.poll() is None)
            model = str(self.model) if live and self.model else None
            context = self.context if live else None
            binary = str(self.binary) if live and self.binary else None
        effective = self._effective_context() if live else None
        return {"running": live, "owned": live, "model": model, "context": context,
                "effective_context": effective, "reasoning": self.reasoning if live else None,
                "binary": binary, "base_url": self.base_url if live else None}

    def shutdown(self) -> None:
        self.stop()


class LlamaCppRuntime:
    def __init__(self, server: LlamaCppServer):
        self.server = server
        self.response = None
        self.lock = threading.Lock()

    def cancel(self) -> None:
        with self.lock:
            response = self.response
        if response:
            try:
                response.close()
            except OSError:
                pass

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
        emit({"type": "inference_settings", "provider": "llama.cpp", "model": state["model"],
              "requested_context": state["context"], "max_output_tokens": state["max_tokens"],
              "reasoning_override": None, "request_options": {"temperature": payload["temperature"], "max_tokens": payload["max_tokens"]},
              "runtime_context": status["context"]})
        req = urllib.request.Request(self.server.base_url + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "Accept": "text/event-stream"}, method="POST")
        result: dict[str, Any] = {"message": {"role": "assistant", "content": ""}}
        calls: dict[int, dict[str, Any]] = {}
        started, first_output = time.monotonic(), True
        try:
            response = urllib.request.urlopen(req, timeout=None)
            with self.lock:
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
                    break
                if not raw:
                    continue
                item = json.loads(raw.decode("utf-8"))
                if isinstance(item.get("usage"), dict):
                    usage = item["usage"]
                    if isinstance(usage.get("completion_tokens"), int):
                        result["eval_count"] = usage["completion_tokens"]
                for choice in item.get("choices", []):
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    thinking = delta.get("reasoning_content") or ""
                    if first_output and (text or thinking or delta.get("tool_calls")):
                        first_output = False
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
                            slot["function"]["name"] = function["name"]
                        if isinstance(function.get("arguments"), str):
                            slot["function"]["arguments"] += function["arguments"]
            if calls:
                result["message"]["tool_calls"] = [calls[index] for index in sorted(calls)]
            return result
        finally:
            with self.lock:
                self.response = None
            try:
                response.close()
            except (UnboundLocalError, OSError, AttributeError):
                pass
