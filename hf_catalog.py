"""Small, credential-safe Hugging Face catalog and explicitly selected file downloader."""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Callable


class _CredentialSafeRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a private Hub bearer token to a different origin."""
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        original = urllib.parse.urlsplit(request.full_url)
        target = urllib.parse.urlsplit(newurl)
        if redirected is not None and (original.scheme.casefold(), original.netloc.casefold()) != (target.scheme.casefold(), target.netloc.casefold()):
            redirected.remove_header("Authorization")
            redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


class HuggingFaceCatalog:
    def __init__(self, cache_root: Path | str, token: str | None = None, endpoint: str = "https://huggingface.co"):
        self.cache_root = Path(cache_root).resolve()
        self.endpoint = endpoint.rstrip("/")
        self.token = token if token is not None else self._saved_token()

    @staticmethod
    def _saved_token() -> str | None:
        for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
            if os.environ.get(name):
                return os.environ[name].strip() or None
        candidates = []
        if os.environ.get("HF_HOME"):
            candidates.append(Path(os.environ["HF_HOME"]) / "token")
        candidates.extend((Path.home() / ".cache" / "huggingface" / "token", Path.home() / ".huggingface" / "token"))
        for path in candidates:
            try:
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return value
            except OSError:
                continue
        return None

    def _request(self, url: str, timeout: int = 30) -> urllib.request.Request:
        headers = {"Accept": "application/json", "User-Agent": "Local-Agent-Workbench"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(url, headers=headers)

    def _json(self, url: str) -> Any:
        return self._json_with_headers(url)[0]

    def _json_with_headers(self, url: str) -> tuple[Any, Any]:
        request = self._request(url)
        try:
            with urllib.request.build_opener(_CredentialSafeRedirect()).open(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8")), response.headers
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError("Hugging Face denied this request. Sign in with `hf auth login` for gated repositories.") from None
            raise RuntimeError(f"Hugging Face request failed (HTTP {exc.code}).") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Hugging Face request failed: {type(exc).__name__}.") from None

    def search_page(self, query: str, cursor: str | None = None, limit: int = 30) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > 160:
            raise ValueError("Enter a search term from 1 to 160 characters.")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Hugging Face page size must be a positive integer.")
        if cursor is None:
            url = self.endpoint + "/api/models?" + urllib.parse.urlencode({"search": query, "limit": limit, "sort": "downloads", "direction": "-1"})
        else:
            parsed = urllib.parse.urlsplit(cursor)
            endpoint = urllib.parse.urlsplit(self.endpoint)
            if ((parsed.scheme.casefold(), parsed.netloc.casefold()) !=
                    (endpoint.scheme.casefold(), endpoint.netloc.casefold()) or parsed.path != "/api/models"):
                raise ValueError("Hugging Face continuation link is outside the configured Hub model endpoint.")
            url = cursor
        rows, headers = self._json_with_headers(url)
        if not isinstance(rows, list):
            raise RuntimeError("Hugging Face returned an unexpected model list.")
        models = [{"repo": str(row.get("id", "")), "revision": str(row.get("sha", "main")),
                   "downloads": int(row.get("downloads") or 0), "likes": int(row.get("likes") or 0),
                   "private": bool(row.get("private")), "gated": bool(row.get("gated"))}
                  for row in rows if isinstance(row, dict) and row.get("id")]
        next_url = None
        link_header = headers.get("Link", "") if headers is not None else ""
        for match in re.finditer(r"<([^>]+)>\s*((?:;[^,]*)+)", link_header):
            if re.search(r"(?:^|;)\s*rel\s*=\s*\"?next\"?(?:\s*;|$)", match.group(2), re.IGNORECASE):
                candidate = urllib.parse.urljoin(self.endpoint + "/", match.group(1))
                parsed = urllib.parse.urlsplit(candidate)
                endpoint = urllib.parse.urlsplit(self.endpoint)
                if ((parsed.scheme.casefold(), parsed.netloc.casefold()) ==
                        (endpoint.scheme.casefold(), endpoint.netloc.casefold()) and parsed.path == "/api/models"):
                    next_url = candidate
                break
        return {"models": models, "next_cursor": next_url}

    def search(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        """Compatibility helper for callers that only need the first page."""
        return self.search_page(query, limit=limit)["models"]

    @staticmethod
    def _repo_path(repo: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(part in {".", ".."} for part in repo.split("/")):
            raise ValueError("Repository must use the owner/name format.")
        return urllib.parse.quote(repo, safe="/")

    @staticmethod
    def _file_path(filename: str) -> str:
        path = PurePosixPath(filename)
        if not filename or path.is_absolute() or any(part in {".", ".."} for part in path.parts) or "\\" in filename:
            raise ValueError("Repository file path is invalid.")
        return "/".join(urllib.parse.quote(part, safe="") for part in path.parts)

    def repo_files(self, repo: str, revision: str) -> list[dict[str, Any]]:
        repo_path = self._repo_path(repo)
        revision = revision.strip()
        if not revision or revision in {".", ".."}:
            raise ValueError("Repository revision is required.")
        url = f"{self.endpoint}/api/models/{repo_path}/tree/{urllib.parse.quote(revision, safe='')}?recursive=true&expand=true"
        rows = self._json(url)
        if not isinstance(rows, list):
            raise RuntimeError("Hugging Face returned an unexpected repository file list.")
        files = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("path"):
                continue
            name = str(row["path"])
            suffix = PurePosixPath(name).suffix.casefold()
            fmt = "gguf" if suffix == ".gguf" else "safetensors" if suffix == ".safetensors" else "other"
            lfs = row.get("lfs") if isinstance(row.get("lfs"), dict) else {}
            size = row.get("size", lfs.get("size"))
            files.append({"path": name, "format": fmt, "size": int(size) if isinstance(size, int) else None,
                          "is_lfs": bool(lfs), "revision": revision})
        return files

    def download(self, repo: str, revision: str, filename: str, cancel: threading.Event,
                 emit: Callable[[dict[str, Any]], None]) -> Path:
        repo_path = self._repo_path(repo)
        file_path = self._file_path(filename)
        if not revision.strip() or revision in {".", ".."}:
            raise ValueError("Repository revision is required.")
        revision_path = urllib.parse.quote(revision, safe="")
        target = self.cache_root.joinpath(*repo.split("/"), revision, *PurePosixPath(filename).parts)
        if Path(os.path.commonpath((str(self.cache_root), str(target.resolve(strict=False))))) != self.cache_root:
            raise ValueError("Download path escapes the selected model cache.")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        url = f"{self.endpoint}/{repo_path}/resolve/{revision_path}/{file_path}"
        try:
            request = self._request(url)
            with urllib.request.build_opener(_CredentialSafeRedirect()).open(request, timeout=60) as response, partial.open("wb") as output:
                length = response.getheader("Content-Length")
                total = int(length) if length and length.isdigit() else None
                received = 0
                while True:
                    if cancel.is_set():
                        raise InterruptedError("Hugging Face download cancelled.")
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    received += len(block)
                    emit({"type": "hf_download_progress", "repo": repo, "revision": revision, "file": filename,
                          "received_bytes": received, "total_bytes": total})
            partial.replace(target)
            emit({"type": "hf_download_complete", "repo": repo, "revision": revision, "file": filename,
                  "path": str(target), "received_bytes": target.stat().st_size})
            return target
        except Exception:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            raise
