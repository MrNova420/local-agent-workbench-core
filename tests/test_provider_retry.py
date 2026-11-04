from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

from desktop_engine import AgentEngine, InferenceStreamError, _inference_retry_metadata


def test_retry_metadata_uses_http_status_and_retry_after_not_error_wording():
    headers = Message()
    headers["Retry-After"] = "2"
    error = HTTPError("http://localhost/api/chat", 503, "service unavailable", headers, None)

    assert _inference_retry_metadata(error) == (503, 2.0, True)

    invalid_headers = Message()
    invalid_headers["Retry-After"] = "NaN"
    assert _inference_retry_metadata(HTTPError("http://localhost/api/chat", 503, "different wording", invalid_headers, None)) == (503, None, True)
    context_headers = Message()
    assert _inference_retry_metadata(HTTPError("http://localhost/api/chat", 500, "context allocation failed", context_headers, None)) == (500, None, False)


def test_transient_provider_failure_retries_same_request_until_response(tmp_path: Path):
    events = []
    engine = AgentEngine(tmp_path, access="plan", task_intent="discuss", event=events.append)
    attempts = []

    def stream(*_args):
        attempts.append(1)
        if len(attempts) == 1:
            raise InferenceStreamError("upstream unavailable", status_code=503, retry_after=0, retryable=True,
                                       failure_phase="provider_http_error")
        return {"message": {"role": "assistant", "content": "The request completed."}}

    engine.runtime.stream = stream
    messages = engine.run("Reply with a short status.")

    assert len(attempts) == 2
    retry = next(event for event in events if event.get("type") == "phase" and event.get("retryAttempt") == 1)
    assert retry["httpStatus"] == 503
    assert retry["retryDelaySeconds"] == 0
    assert messages[-1]["content"] == "The request completed."
    assert engine.state["task_checkpoint"]["current"]["status"] == "completed"


def test_partial_provider_output_is_saved_without_automatic_replay(tmp_path: Path):
    engine = AgentEngine(tmp_path, access="plan", task_intent="discuss")
    attempts = []

    def stream(*_args):
        attempts.append(1)
        raise InferenceStreamError("connection dropped after output", {"role": "assistant", "content": "partial"},
                                   status_code=503, retryable=True)

    engine.runtime.stream = stream
    messages = engine.run("Reply with a short status.")

    assert len(attempts) == 1
    assert messages[-1]["partial_response"] is True
    assert messages[-1]["content"] == "partial"
    assert engine.state["task_checkpoint"]["current"]["status"] == "failed"
