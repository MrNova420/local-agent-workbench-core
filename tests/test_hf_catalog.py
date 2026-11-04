import json
from io import BytesIO
import threading
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from hf_catalog import HuggingFaceCatalog, _CredentialSafeRedirect


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.offset = 0

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size=-1):
        if size < 0: size = len(self.body)
        result = self.body[self.offset:self.offset + size]
        self.offset += len(result)
        return result
    def getheader(self, name): return str(len(self.body)) if name == "Content-Length" else None


def test_search_and_repo_files_keep_revision_and_format_metadata(tmp_path):
    catalog = HuggingFaceCatalog(tmp_path)
    search = [{"id": "org/model", "sha": "abc123", "downloads": 12, "likes": 3}]
    tree = [
        {"path": "model-Q4_K_M.gguf", "size": 100},
        {"path": "config.json", "size": 20},
        {"path": "model-00001-of-00002.safetensors", "size": 200},
    ]
    with patch.object(catalog, "_json", side_effect=[search, tree]):
        rows = catalog.search("model")
        files = catalog.repo_files("org/model", "abc123")
    assert rows[0]["revision"] == "abc123"
    assert files[0]["format"] == "gguf"
    assert files[0]["size"] == 100
    assert next(row for row in files if row["path"].endswith(".safetensors"))["format"] == "safetensors"


def test_search_pages_follow_hub_next_link_and_keep_results_page_sized(tmp_path):
    first_url = "https://huggingface.co/api/models?search=model&limit=30&cursor=opaque-cursor"
    first_response = FakeResponse(b'[{"id":"org/first","sha":"rev-1"}]')
    first_response.headers = Message()
    first_response.headers["Link"] = f'<{first_url}>; rel="next"'
    second_response = FakeResponse(b'[{"id":"org/second","sha":"rev-2"}]')
    second_response.headers = Message()
    first_opener, second_opener = Mock(), Mock()
    first_opener.open.return_value = first_response
    second_opener.open.return_value = second_response
    catalog = HuggingFaceCatalog(tmp_path)

    with patch("urllib.request.build_opener", side_effect=[first_opener, second_opener]):
        first = catalog.search_page("model")
        second = catalog.search_page("model", cursor=first["next_cursor"])

    assert [row["repo"] for row in first["models"]] == ["org/first"]
    assert first["next_cursor"] == first_url
    assert [row["repo"] for row in second["models"]] == ["org/second"]
    assert second["next_cursor"] is None
    assert second_opener.open.call_args.args[0].full_url == first_url


def test_search_ignores_cross_origin_next_links_and_rejects_external_cursor(tmp_path):
    response = FakeResponse(b'[]')
    response.headers = Message()
    response.headers["Link"] = '<https://attacker.example/api/models?cursor=private>; rel="next"'
    opener = Mock()
    opener.open.return_value = response
    catalog = HuggingFaceCatalog(tmp_path)
    with patch("urllib.request.build_opener", return_value=opener):
        page = catalog.search_page("model")
    assert page["next_cursor"] is None
    with pytest.raises(ValueError, match="outside the configured Hub model endpoint"):
        catalog.search_page("model", cursor="https://attacker.example/api/models?cursor=private")


def test_download_uses_auth_header_pins_revision_and_reports_progress(tmp_path):
    catalog = HuggingFaceCatalog(tmp_path, token="hf-secret")
    response = FakeResponse(b"gguf-data")
    events = []
    opener = Mock()
    opener.open.return_value = response
    with patch("urllib.request.build_opener", return_value=opener):
        saved = catalog.download("org/model", "abc123", "model.gguf", threading.Event(), events.append)
    request = opener.open.call_args.args[0]
    assert "/resolve/abc123/model.gguf" in request.full_url
    assert request.get_header("Authorization") == "Bearer hf-secret"
    assert saved.read_bytes() == b"gguf-data"
    assert events[-1]["received_bytes"] == 9
    assert "hf-secret" not in json.dumps(events)


def test_private_token_is_removed_when_hub_redirects_to_another_origin():
    request = __import__("urllib.request").request.Request("https://huggingface.co/model/file", headers={"Authorization": "Bearer hf-secret"})
    redirected = _CredentialSafeRedirect().redirect_request(request, BytesIO(), 302, "Found", {"location": "https://cdn-lfs.huggingface.co/file"}, "https://cdn-lfs.huggingface.co/file")
    assert redirected.get_header("Authorization") is None


@pytest.mark.parametrize("repo,file", [("../escape", "model.gguf"), ("org/model", "../secret")])
def test_download_rejects_path_traversal(tmp_path, repo, file):
    catalog = HuggingFaceCatalog(tmp_path)
    with pytest.raises(ValueError):
        catalog.download(repo, "main", file, threading.Event(), lambda _event: None)
