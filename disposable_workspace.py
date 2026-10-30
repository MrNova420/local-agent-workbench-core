"""Disposable project copies with conflict-checked, explicit apply-back."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from project_walk import DEFAULT_IGNORED, iter_project_files

REVIEWABLE_SUFFIXES = {
    ".py", ".gd", ".cs", ".cpp", ".h", ".hpp", ".js", ".jsx", ".ts", ".tsx",
    ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".xml", ".shader",
    ".glsl", ".html", ".css", ".rs", ".go", ".ini", ".cfg", ".ps1", ".bat",
}
MAX_REVIEW_FILE_BYTES = 500_000


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reviewable(path: Path) -> bool:
    try:
        return path.suffix.casefold() in REVIEWABLE_SUFFIXES and path.stat().st_size <= MAX_REVIEW_FILE_BYTES
    except OSError:
        return False


def _safe_path(root: Path, relative: str) -> Path:
    candidate_rel = Path(relative)
    if not relative or candidate_rel.is_absolute() or ".." in candidate_rel.parts:
        raise ValueError(f"Invalid project-relative path: {relative!r}")
    target = root
    for part in candidate_rel.parts:
        if part in {"", "."}:
            continue
        target = target / part
        if target.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(target):
            raise ValueError(f"Refusing linked path: {relative}")
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path escapes project: {relative}")
    return resolved


def capture_manifest(root: Path | str) -> dict[str, str]:
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise ValueError("Workspace must be a directory.")
    return {
        path.relative_to(base).as_posix(): _digest(path)
        for path in iter_project_files(base)
        if _reviewable(path)
    }


def managed_copy_path(store_root: Path | str, session_id: str, *, require_exists: bool = False) -> Path:
    """Resolve a session copy only below its app-managed workspace directory."""
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", session_id):
        raise ValueError("Invalid session identifier for disposable workspace.")
    base = Path(store_root).resolve()
    session_dir = base / session_id
    project = session_dir / "project"
    is_junction = getattr(os.path, "isjunction", lambda _p: False)
    if any(path.is_symlink() or is_junction(path) for path in (session_dir, project)):
        raise ValueError("Disposable workspace cannot contain linked path components.")
    resolved = project.resolve(strict=require_exists)
    if resolved == base or base not in resolved.parents:
        raise ValueError("Disposable workspace path escapes its managed directory.")
    return resolved


def create_disposable_workspace(source: Path | str, destination: Path | str) -> dict[str, str]:
    """Copy a project without VCS/cache directories or links and record text baselines."""
    source_root = Path(source).resolve(strict=True)
    destination_root = Path(destination).resolve()
    if not source_root.is_dir():
        raise ValueError("Copy source must be a directory.")
    if destination_root == source_root or source_root in destination_root.parents:
        raise ValueError("Disposable copy cannot be inside its source project.")
    if destination_root.exists():
        raise FileExistsError(f"Disposable workspace already exists: {destination_root}")

    destination_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{destination_root.name}-copy-", dir=destination_root.parent))
    try:
        staged_project = staging_root / "project"
        staged_project.mkdir()
        for source_file in iter_project_files(source_root):
            if source_file.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(source_file):
                continue
            target = staged_project / source_file.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
        manifest = capture_manifest(staged_project)
        os.rename(staged_project, destination_root)
        return manifest
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def review_changes(source: Path | str, working: Path | str, manifest: dict[str, str]) -> list[dict[str, Any]]:
    """Describe text edits/new files/deletions and flag source changes since copy time."""
    source_root = Path(source).resolve(strict=True)
    working_root = Path(working).resolve(strict=True)
    current: dict[str, Path] = {}
    for path in iter_project_files(working_root):
        if _reviewable(path):
            current[path.relative_to(working_root).as_posix()] = path

    changes: list[dict[str, Any]] = []
    for relative in sorted(set(manifest) | set(current), key=str.casefold):
        old_hash = manifest.get(relative)
        working_path = current.get(relative)
        unsupported_reason = ""
        if working_path is None and old_hash is not None:
            try:
                candidate = _safe_path(working_root, relative)
                if candidate.exists() and not _reviewable(candidate):
                    action, content = "unsupported", None
                    unsupported_reason = "Copy target is not a reviewable UTF-8 text file or exceeds the review limit."
                else:
                    action, content = "delete", None
            except (OSError, ValueError) as exc:
                action, content = "unsupported", None
                unsupported_reason = str(exc)
        elif working_path is not None:
            if old_hash is not None and _digest(working_path) == old_hash:
                continue
            action = "write"
            try:
                content = working_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
        else:
            continue

        try:
            source_path = _safe_path(source_root, relative)
            source_exists = source_path.exists()
            source_content = None
            if source_exists and source_path.is_file() and _reviewable(source_path):
                source_bytes = source_path.read_bytes()
                source_hash = hashlib.sha256(source_bytes).hexdigest()
                try:
                    source_content = source_bytes.decode("utf-8")
                except UnicodeError:
                    pass
            else:
                source_hash = None
            conflict = action == "unsupported" or (old_hash is None and source_exists) or (old_hash is not None and source_hash != old_hash)
            reason = unsupported_reason or ("Source changed since this copy was created." if conflict else "")
        except (OSError, ValueError) as exc:
            source_exists = False
            source_hash = None
            source_content = None
            conflict = True
            reason = unsupported_reason or str(exc)
        changes.append({
            "path": relative, "action": action, "content": content,
            "expected_source_hash": old_hash, "source_hash": source_hash,
            "expected_source_exists": source_exists, "source_exists": source_exists,
            "source_content": source_content, "conflict": conflict, "reason": reason,
        })
    return changes


def apply_reviewed_changes(source: Path | str, changes: list[dict[str, Any]]) -> list[str]:
    """Atomically apply a reviewed batch if no source files changed after review."""
    root = Path(source).resolve(strict=True)
    if any(change.get("conflict") for change in changes):
        raise ValueError("Resolve source conflicts before applying disposable-copy changes.")
    plans = []
    for change in changes:
        relative = change["path"]
        target = _safe_path(root, relative)
        exists = target.exists()
        current_hash = _digest(target) if exists and target.is_file() and _reviewable(target) else None
        if exists != change.get("expected_source_exists") or current_hash != change.get("expected_source_hash"):
            raise ValueError(f"Source changed after review: {relative}")
        action = change.get("action")
        content = change.get("content")
        if action not in {"write", "delete"} or (action == "write" and not isinstance(content, str)):
            raise ValueError(f"Invalid reviewed change for {relative}")
        if action == "write" and len(content.encode("utf-8")) > MAX_REVIEW_FILE_BYTES:
            raise ValueError(f"File exceeds review limit: {relative}")
        plans.append((relative, target, action, content, exists))

    staged: list[tuple[Path, Path | None]] = []
    backups: list[tuple[Path, Path]] = []
    placed: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for _, target, action, content, _ in plans:
            missing = []
            parent = target.parent
            while not parent.exists() and parent != root:
                missing.append(parent)
                parent = parent.parent
            for folder in reversed(missing):
                folder.mkdir()
                created_dirs.append(folder)
            if action == "delete":
                staged.append((target, None))
            else:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=target.parent, prefix=".agentdesk-", suffix=".tmp") as handle:
                    handle.write(content)
                    staged.append((target, Path(handle.name)))
        for (relative, target, action, _, exists), (_, temporary) in zip(plans, staged):
            if exists:
                fd, backup_name = tempfile.mkstemp(prefix=".agentdesk-backup-", dir=target.parent)
                os.close(fd)
                Path(backup_name).unlink()
                backup = Path(backup_name)
                os.replace(target, backup)
                backups.append((target, backup))
            if temporary is not None:
                os.replace(temporary, target)
                placed.append(target)
    except Exception as exc:
        rollback_errors = []
        for target in reversed(placed):
            try:
                target.unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"remove {target}: {rollback_exc}")
        for target, backup in reversed(backups):
            if backup.exists():
                try:
                    os.replace(backup, target)
                except OSError as rollback_exc:
                    rollback_errors.append(f"restore {target} from {backup}: {rollback_exc}")
        for _, temporary in staged:
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        for folder in reversed(created_dirs):
            try:
                folder.rmdir()
            except OSError:
                pass
        if rollback_errors:
            raise RuntimeError(f"Copy-apply failed ({exc}); rollback also failed: {'; '.join(rollback_errors)}") from exc
        raise
    for _, backup in backups:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass
    return [relative for relative, *_ in plans]
