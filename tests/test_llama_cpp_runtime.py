import json
import threading
from unittest.mock import Mock
from unittest.mock import patch

import pytest

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
    with patch("urllib.request.urlopen", return_value=FakeSSE(chunks)) as open_url:
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
    settings = next(event for event in events if event["type"] == "inference_settings")
    assert settings["requested_context"] == 32768
    assert settings["request_index"] == 4
    assert settings["request_api"] == "/v1/chat/completions"
    assert settings["provider"] == "llama.cpp"
    assert settings["api_model"] == "local-workbench"
    assert settings["stream"] is True
    assert settings["reasoning_field_sent"] is False
    assert settings["runtime_context"] == 32768
    assert settings["max_output_tokens"] == 100
    assert settings["request_options"] == {"temperature": 0.1, "max_tokens": 100}
    assert [event["type"] for event in events].count("thinking") == 1


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
