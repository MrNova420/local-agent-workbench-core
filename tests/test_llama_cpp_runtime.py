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
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"a.py\"}"}}]}, "finish_reason": "tool_calls"}], "usage": {"completion_tokens": 8}},
    ]
    server = Mock()
    server.status.return_value = {"running": True, "context": 32768}
    server.base_url = "http://127.0.0.1:11436"
    runtime = LlamaCppRuntime(server)
    state = {"model": "gguf::x.gguf", "temperature": 0.1, "context": 32768, "max_tokens": 100,
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
    settings = next(event for event in events if event["type"] == "inference_settings")
    assert settings["requested_context"] == 32768
    assert settings["max_output_tokens"] == 100
    assert settings["request_options"] == {"temperature": 0.1, "max_tokens": 100}
    assert [event["type"] for event in events].count("thinking") == 1


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
