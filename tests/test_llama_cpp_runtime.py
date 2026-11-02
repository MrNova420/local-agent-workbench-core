import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from desktop_engine import InferenceStreamError, RequestCancelled
from llama_cpp_runtime import LlamaCppRuntime, LlamaCppServer


class FakeSSE:
    def __init__(self, events): self.lines = iter([f"data: {json.dumps(event)}\n".encode() for event in events] + [b"data: [DONE]\n", b""])
    def readline(self): return next(self.lines, b"")
    def close(self): pass


def test_openai_stream_preserves_usage_reasoning_and_tool_call_deltas():
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "think", "content": "answer"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "read_file", "arguments": "{\"path\":"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"a.py\"}"}}]}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 110, "prompt_tokens_details": {"cached_tokens": 72}, "completion_tokens": 8},
         "timings": {"cache_n": 72, "prompt_n": 38, "prompt_ms": 850.5, "predicted_n": 8, "predicted_ms": 4000}},
    ]
    server = Mock()
    server.status.return_value = {"running": True, "context": 32768}
    server.base_url = "http://127.0.0.1:11436"
    runtime = LlamaCppRuntime(server)
    state = {"model": "gguf::x.gguf", "temperature": 0.1, "context": 32768, "max_tokens": 100, "request_index": 4,
             "registry": type("R", (), {"schemas": []})(), "mcp_tools": []}
    events = []
    with patch("llama_cpp_runtime.open_http_stream", return_value=FakeSSE(chunks)) as open_url:
        result = runtime.stream(state, [], threading.Event(), events.append)
    payload = json.loads(open_url.call_args.args[0].data)
    assert payload["max_tokens"] == 100
    assert payload["temperature"] == 0.1
    assert "num_ctx" not in payload
    assert result["message"]["content"] == "answer"
    assert result["message"]["thinking"] == "think"
    assert json.loads(result["message"]["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}
    assert result["eval_count"] == 8
    assert result["prompt_eval_count"] == 110
    assert result["prompt_eval_cached_count"] == 72
    assert result["prompt_eval_duration"] == 850_500_000
    assert result["eval_duration"] == 4_000_000_000
    assert result["done_reason"] == "tool_calls"
    assert next(event for event in events if event["type"] == "request_complete")["provider_done_reason"] == "tool_calls"
    progress = next(event for event in events if event["type"] == "tool_call_progress")
    assert progress["tool_name"] == "read_file"
    assert progress["argument_characters"] > 0
    assert "arguments" not in progress
    assert "arguments_preview" not in progress
    settings = next(event for event in events if event["type"] == "inference_settings")
    assert settings["requested_context"] == 32768
    assert settings["request_index"] == 4
    assert settings["request_api"] == "/v1/chat/completions"
    assert settings["provider"] == "llama.cpp"
    assert settings["api_model"] == "local-workbench"
    assert settings["stream"] is True
    assert settings["reasoning_field_sent"] is False
    assert settings["runtime_context"] == 32768
    assert settings["additional_fields"] == {"stream_options": {"include_usage": True}}
    assert settings["max_output_tokens"] == 100
    assert settings["request_options"] == {"temperature": 0.1, "max_tokens": 100}
    assert [event["type"] for event in events].count("thinking") == 1


def test_llama_cpp_sends_selected_per_request_thinking_budget_without_changing_output_cap():
    server = Mock()
    server.status.return_value = {"running": True, "context": 32768}
    server.base_url = "http://127.0.0.1:11436"
    runtime = LlamaCppRuntime(server)
    state = {"model": "gguf::x.gguf", "temperature": 0.1, "context": 32768, "max_tokens": 8192,
             "reasoning_budget_tokens": 4096, "reasoning": None,
             "registry": type("R", (), {"schemas": []})(), "mcp_tools": []}
    events = []
    with patch("llama_cpp_runtime.open_http_stream", return_value=FakeSSE([])) as open_url:
        runtime.stream(state, [], threading.Event(), events.append)

    payload = json.loads(open_url.call_args.args[0].data)
    assert payload["thinking_budget_tokens"] == 4096
    assert payload["max_tokens"] == 8192
    settings = next(event for event in events if event["type"] == "inference_settings")
    assert settings["reasoning_budget_tokens"] == 4096
    assert settings["reasoning_budget_applied_by"] == "request"


def test_llama_cpp_omits_thinking_budget_when_unlimited():
    server = Mock()
    server.status.return_value = {"running": True, "context": 32768}
    server.base_url = "http://127.0.0.1:11436"
    runtime = LlamaCppRuntime(server)
    state = {"model": "gguf::x.gguf", "temperature": 0.1, "context": 32768, "max_tokens": 8192,
             "reasoning_budget_tokens": None, "reasoning": None,
             "registry": type("R", (), {"schemas": []})(), "mcp_tools": []}
    with patch("llama_cpp_runtime.open_http_stream", return_value=FakeSSE([])) as open_url:
        runtime.stream(state, [], threading.Event(), lambda _: None)
    payload = json.loads(open_url.call_args.args[0].data)
    assert "thinking_budget_tokens" not in payload


@pytest.mark.parametrize("budget", [-1, True, 2_147_483_648])
def test_llama_cpp_rejects_thinking_budgets_outside_request_schema(budget):
    server = Mock()
    server.status.return_value = {"running": True, "context": 32768}
    server.base_url = "http://127.0.0.1:11436"
    runtime = LlamaCppRuntime(server)
    state = {"model": "gguf::x.gguf", "temperature": 0.1, "context": 32768, "max_tokens": 8192,
             "reasoning_budget_tokens": budget,
             "registry": type("R", (), {"schemas": []})(), "mcp_tools": []}
    with pytest.raises(ValueError, match="non-negative integer"):
        runtime.stream(state, [], threading.Event(), lambda _: None)


def test_slot_metrics_expose_token_counts_without_prompt_text(tmp_path):
    server = LlamaCppServer(tmp_path)
    payload = [{"id": 0, "id_task": 12, "n_ctx": 32768, "is_processing": True,
                "n_prompt_tokens": 100, "n_prompt_tokens_cache": 72,
                "n_prompt_tokens_processed": 28,
                "next_token": [{"n_decoded": 5}], "prompt": "must not leak"}]
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(payload).encode()
    with patch("urllib.request.urlopen", return_value=response):
        metrics = server._slot_metrics()
    assert metrics["available"] is True
    assert metrics["slots"] == [{"id": 0, "is_processing": True, "n_ctx": 32768,
                                 "n_prompt_tokens": 100, "n_prompt_tokens_cache": 72,
                                 "n_prompt_tokens_processed": 28, "n_decoded": 5,
                                 "context_tokens_in_slot": 105}]
    assert "must not leak" not in json.dumps(metrics)


def test_runtime_status_reports_only_chat_template_reasoning_levels(tmp_path):
    server = LlamaCppServer(tmp_path)
    server.owned = True
    server.proc = Mock()
    server.proc.poll.return_value = None
    server.model = tmp_path / "model.gguf"
    server.context = 32768
    props = {
        "default_generation_settings": {"n_ctx": 32768},
        "chat_template_caps": {
            "supports_reasoning_effort": True,
            "reasoning_effort_levels": ["low", "medium", "high"],
        },
    }
    response = Mock()
    response.read.return_value = json.dumps(props).encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    with patch("llama_cpp_runtime.urllib.request.urlopen", return_value=response):
        status = server.status()
    assert status["effective_context"] == 32768
    assert status["reasoning_capability"] == {
        "support": "levels", "default": None, "values": [False, "low", "medium", "high"],
    }


def test_runtime_status_does_not_guess_reasoning_when_template_levels_are_unknown(tmp_path):
    server = LlamaCppServer(tmp_path)
    server.owned = True
    server.proc = Mock()
    server.proc.poll.return_value = None
    server.model = tmp_path / "model.gguf"
    server.context = 8192
    response = Mock()
    response.read.return_value = json.dumps({
        "default_generation_settings": {"n_ctx": 8192},
        "chat_template_caps": {"supports_reasoning_effort": True},
    }).encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    with patch("llama_cpp_runtime.urllib.request.urlopen", return_value=response):
        status = server.status()
    assert status["reasoning_capability"] == {
        "support": "unknown", "default": None, "values": [],
    }


def test_reasoning_phase_transitions_to_generation_only_when_user_facing_text_arrives():
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "considering"}}]},
        {"choices": [{"delta": {"content": "answer"}}]},
    ]
    server = Mock()
    server.status.return_value = {"running": True, "context": 4096}
    server.base_url = "http://127.0.0.1:11436"
    runtime = LlamaCppRuntime(server)
    state = {"model": "gguf::x.gguf", "temperature": 0.1, "context": 4096, "max_tokens": 128,
             "registry": type("R", (), {"schemas": []})(), "mcp_tools": []}
    events = []
    with patch("llama_cpp_runtime.open_http_stream", return_value=FakeSSE(chunks)):
        runtime.stream(state, [], threading.Event(), events.append)

    assert [event["phase"] for event in events if event["type"] == "phase"] == ["reasoning", "generating"]


def test_llama_stream_failure_preserves_only_received_assistant_text():
    class BrokenSSE:
        def __init__(self):
            self.lines = iter([b'data: {"choices":[{"delta":{"reasoning_content":"thinking","content":"partial"}}]}\n'])
        def readline(self):
            line = next(self.lines, None)
            if line:
                return line
            raise ConnectionResetError("server disconnected")
        def close(self): pass
    server = Mock()
    server.status.return_value = {"running": True, "context": 4096}
    server.base_url = "http://127.0.0.1:11436"
    runtime = LlamaCppRuntime(server)
    state = {"model": "gguf::x.gguf", "temperature": 0.1, "context": 4096, "max_tokens": 32,
             "registry": type("R", (), {"schemas": []})(), "mcp_tools": []}
    with patch("llama_cpp_runtime.open_http_stream", return_value=BrokenSSE()):
        with pytest.raises(InferenceStreamError) as raised:
            runtime.stream(state, [], threading.Event(), lambda _: None)
    assert raised.value.partial_message == {"role": "assistant", "thinking": "thinking", "content": "partial"}


def test_llama_cancel_interrupts_delayed_response_headers():
    received = threading.Event()
    release = threading.Event()
    errors = []
    class DelayedHeaders(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.set()
            release.wait(5)
            try:
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except OSError:
                pass
        def log_message(self, *_args): pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), DelayedHeaders)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    provider = Mock()
    provider.status.return_value = {"running": True, "context": 4096}
    provider.base_url = f"http://127.0.0.1:{httpd.server_port}"
    runtime = LlamaCppRuntime(provider)
    state = {"model": "gguf::x.gguf", "temperature": 0.1, "context": 4096, "max_tokens": 32,
             "registry": type("R", (), {"schemas": []})(), "mcp_tools": []}
    cancel = threading.Event()
    dispatched = threading.Event()
    def emit(event):
        if event.get("type") == "request_dispatched":
            dispatched.set()
    def run():
        try:
            runtime.stream(state, [], cancel, emit)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    request_received = received.wait(2)
    request_dispatched = dispatched.wait(2)
    cancel.set()
    runtime.cancel()
    thread.join(2)
    cancelled_before_release = not thread.is_alive()
    release.set()
    httpd.shutdown()
    httpd.server_close()
    server_thread.join(2)
    assert request_received and request_dispatched
    assert cancelled_before_release, "llama.cpp Cancel left the request blocked awaiting headers until the server was released"
    assert len(errors) == 1 and isinstance(errors[0], RequestCancelled)


def test_server_refuses_existing_external_endpoint_and_never_claims_ownership(tmp_path):
    server = LlamaCppServer(tmp_path)
    binary = tmp_path / "llama-server.exe"; binary.write_bytes(b"test")
    model = tmp_path / "model.gguf"; model.write_bytes(b"test")
    with patch.object(server, "_healthy", return_value=True):
        with pytest.raises(RuntimeError, match="already in use"):
            server.load(binary, model, 8192, None, threading.Event(), lambda _: None)
    assert server.owned is False


def test_server_requires_explicit_valid_model_binary_and_context(tmp_path):
    server = LlamaCppServer(tmp_path)
    binary = tmp_path / "llama-server.exe"; binary.write_bytes(b"test")
    model = tmp_path / "model.bin"; model.write_bytes(b"test")
    with pytest.raises(ValueError, match="GGUF"):
        server.load(binary, model, 8192, None, threading.Event(), lambda _: None)


def test_server_load_passes_exact_context_and_reasoning_then_only_stops_its_own_process(tmp_path):
    server = LlamaCppServer(tmp_path)
    binary = tmp_path / "llama-server.exe"; binary.write_bytes(b"test")
    model = tmp_path / "model.gguf"; model.write_bytes(b"test")
    process = Mock()
    process.pid = 123
    process.poll.return_value = None
    events = []
    with patch.object(server, "_healthy", side_effect=[False, True]), \
         patch.object(server, "_effective_context", return_value=32768), \
         patch.object(server, "_slot_metrics", return_value={"available": False, "slots": []}), \
         patch("subprocess.Popen", return_value=process) as popen, \
         patch("time.sleep"):
        server.load(binary, model, 32768, "high", threading.Event(), events.append)
    args = popen.call_args.args[0]
    assert args[args.index("--ctx-size") + 1] == "32768"
    assert args[args.index("--reasoning-effort") + 1] == "high"
    assert server.status()["owned"] is True
    assert events[-1]["effective_context"] == 32768
    assert server.stop() is True
    process.terminate.assert_called_once()


def test_server_maps_template_supported_off_choice_to_reasoning_off_flag(tmp_path):
    server = LlamaCppServer(tmp_path)
    binary = tmp_path / "llama-server.exe"; binary.write_bytes(b"test")
    model = tmp_path / "model.gguf"; model.write_bytes(b"test")
    process = Mock()
    process.pid = 124
    process.poll.return_value = None
    with patch.object(server, "_healthy", side_effect=[False, True]), \
         patch.object(server, "_effective_context", return_value=8192), \
         patch("subprocess.Popen", return_value=process) as popen, \
         patch("time.sleep"):
        server.load(binary, model, 8192, False, threading.Event(), lambda _: None)
    args = popen.call_args.args[0]
    assert args[args.index("--reasoning") + 1] == "off"
    assert "--reasoning-effort" not in args
