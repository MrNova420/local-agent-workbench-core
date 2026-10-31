"""Non-following traversal shared by every project-scoped reader."""
from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Iterator
from pathspec import GitIgnoreSpec


DEFAULT_IGNORED = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".agent-workbench", "dist", "build", "target", "Library", "Temp",
}


def _link_or_junction(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", lambda value: False)
    return path.is_symlink() or is_junction(path)


def _is_ignored(relative: str, is_directory: bool, rules: tuple[tuple[str, GitIgnoreSpec], ...]) -> bool:
    candidate = relative.rstrip("/") + ("/" if is_directory else "")
    ignored = False
    for base, spec in rules:
        if base and candidate != base and not candidate.startswith(base + "/"):
            continue
        scoped = candidate[len(base):].lstrip("/") if base else candidate
        result = spec.check_file(scoped)
        if result.include is not None:
            ignored = result.include
    return ignored


def iter_project_files(root: Path | str, ignored: set[str] | None = None, max_files: int | None = None) -> Iterator[Path]:
    """Yield project files under ignore rules without descending into links or generated folders."""
    base = Path(root).resolve(strict=True)
    ignored_names = DEFAULT_IGNORED if ignored is None else ignored
    yielded = 0
    rules_by_directory: dict[Path, tuple[tuple[str, GitIgnoreSpec], ...]] = {base: ()}
    for current, directories, files in os.walk(base, topdown=True, followlinks=False):
        current_path = Path(current)
        rules = rules_by_directory.pop(current_path, ())
        gitignore = current_path / ".gitignore"
        if gitignore.is_file() and not _link_or_junction(gitignore):
            try:
                spec = GitIgnoreSpec.from_lines(gitignore.read_text(encoding="utf-8").splitlines())
                base_relative = current_path.relative_to(base).as_posix()
                rules += (("" if base_relative == "." else base_relative, spec),)
            except (OSError, UnicodeError, ValueError):
                pass

        visible_directories = []
        for name in sorted(directories):
            path = current_path / name
            if name in ignored_names or _link_or_junction(path):
                continue
            relative = path.relative_to(base).as_posix()
            if _is_ignored(relative, True, rules):
                continue
            rules_by_directory[path] = rules
            visible_directories.append(name)
        directories[:] = visible_directories
        for name in sorted(files):
            path = current_path / name
            if _link_or_junction(path):
                continue
            relative = path.relative_to(base).as_posix()
            if _is_ignored(relative, False, rules):
                continue
            if max_files is not None and yielded>=max_files:return
            yielded+=1
            yield path
