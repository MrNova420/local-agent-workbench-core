"""Non-following traversal shared by every project-scoped reader."""
from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Iterator


DEFAULT_IGNORED = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", "dist", "build", "target", "Library", "Temp",
}


def _link_or_junction(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", lambda value: False)
    return path.is_symlink() or is_junction(path)


def iter_project_files(root: Path | str, ignored: set[str] | None = None) -> Iterator[Path]:
    """Yield regular files without descending into links, junctions, or generated folders."""
    base = Path(root).resolve(strict=True)
    ignored_names = DEFAULT_IGNORED if ignored is None else ignored
    for current, directories, files in os.walk(base, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name for name in directories
            if name not in ignored_names and not _link_or_junction(current_path / name)
        )
        for name in sorted(files):
            path = current_path / name
            if _link_or_junction(path):
                continue
            yield path
