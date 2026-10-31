"""Read project guidance and discover optional Agent Skills safely."""
from __future__ import annotations

import re
import os
import subprocess
from pathlib import Path

from project_walk import DEFAULT_IGNORED, iter_project_files

IGNORED = DEFAULT_IGNORED
SKILL_ROOTS = (".agents/skills", ".claude/skills", ".opencode/skills")
GLOBAL_SKILL_ROOTS = (".agents/skills", ".claude/skills", ".config/opencode/skills", ".codex/skills")
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Read the simple scalar metadata used by Agent Skills without a YAML dependency."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    lines = text.splitlines()
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text
    values: dict[str, str] = {}
    for line in lines[1:end]:
        match = re.match(r"^(name|description|license|compatibility):\s*(.*?)\s*$", line)
        if match:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[match.group(1)] = value
    return values, "\n".join(lines[end + 1:]).lstrip("\r\n")


class ProjectKnowledge:
    def __init__(self, workspace: Path | str):
        self.root = Path(workspace).resolve(strict=True)

    def instructions(self) -> str:
        found = []
        for path in iter_project_files(self.root):
            rel = path.relative_to(self.root).as_posix()
            if path.name in {"AGENTS.md", "CLAUDE.md", "GEMINI.md"} or rel == ".github/copilot-instructions.md" or (rel.startswith(".github/instructions/") and path.name.endswith(".instructions.md")):
                try:
                    found.append((len(path.relative_to(self.root).parts), rel, path.read_text(encoding="utf8")))
                except (OSError, UnicodeError):
                    pass
        out = []
        for _, rel, text in sorted(found):
            out.append(f"--- {rel} ---\n{text}")
        return "\n\n".join(out) or "(none found)"

    def _project_skill_roots(self) -> list[Path]:
        """Follow OpenCode's worktree-aware discovery: selected dir upward through Git root."""
        directories = [self.root]
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"], cwd=self.root,
                capture_output=True, text=True, timeout=3, check=False, shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                git_root = Path(result.stdout.strip()).resolve()
                current = self.root
                while current != git_root and current.parent != current:
                    current = current.parent
                    directories.append(current)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return [directory / relative for directory in directories for relative in SKILL_ROOTS]

    def skills(self, roots: list[Path] | None = None) -> list[dict[str, str]]:
        project_roots = self._project_skill_roots() if roots is None else roots
        candidates = project_roots + [Path.home() / p for p in GLOBAL_SKILL_ROOTS] if roots is None else roots
        # Project definitions take precedence over user-global definitions with the same name.
        found: dict[str, dict[str, str]] = {}
        for base in candidates:
            try:
                if not base.is_dir() or base.is_symlink():
                    continue
                for folder in sorted(base.iterdir(), key=lambda p: p.name.casefold()):
                    path = folder / "SKILL.md"
                    if not folder.is_dir() or folder.is_symlink() or not path.is_file() or path.is_symlink():
                        continue
                    if len(folder.name)>64 or not SKILL_NAME.fullmatch(folder.name):
                        continue
                    try:
                        resolved = path.resolve(strict=True)
                        text = resolved.read_text(encoding="utf8")
                    except (OSError, UnicodeError):
                        continue
                    metadata, body = _frontmatter(text)
                    declared_name = metadata.get("name", folder.name)
                    description = metadata.get("description", "").strip()
                    has_frontmatter=text.startswith("---\n") or text.startswith("---\r\n")
                    if declared_name != folder.name or (has_frontmatter and (not metadata.get("name") or not description)):
                        continue
                    # Legacy SKILL.md files remain discoverable, with a concise fallback description.
                    if not description:
                        description = next((line.strip().lstrip("# ") for line in body.splitlines() if line.strip()), "Reusable project instructions")
                    if len(description) > 1024:
                        continue
                    found.setdefault(folder.name, {
                        "name": folder.name, "description": description,
                        "path": str(resolved), "source": "project" if any(resolved.is_relative_to(root.resolve()) for root in project_roots if root.exists()) else "user",
                    })
            except OSError:
                continue
        return list(found.values())

    def skill_catalog(self, skills: list[dict[str, str]] | None = None) -> str:
        skills = self.skills() if skills is None else skills
        if not skills:
            return "No Agent Skills are available."
        return "\n".join(f"- {item['name']}: {item['description']}" for item in skills)

    def load_skill(self, name: str, skills: list[dict[str, str]]) -> str:
        match = next((skill for skill in skills if skill["name"] == name), None)
        if not match:
            raise ValueError("Unknown or unavailable skill")
        path = Path(match["path"]).resolve(strict=True)
        # Only paths discovered from the active project/worktree or the user's skill roots are loadable.
        allowed_roots = self._project_skill_roots() + [Path.home() / p for p in GLOBAL_SKILL_ROOTS]
        if not any(path.is_relative_to(base.resolve()) for base in allowed_roots if base.exists()):
            raise ValueError("Skill path is outside the active project and configured user skill locations")
        try:
            text = path.read_text(encoding="utf8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"Could not read skill: {exc}") from exc
        _, body = _frontmatter(text)
        # Keep all instructions: silent clipping can remove required workflow steps.
        header = f"Skill: {name}\nDescription: {match['description']}\nSource: {match['source']}\n"
        return header + f"Related text resources can be read with skill(action=read, name={name!r}, path=<relative path>).\n\n" + body

    def read_skill_resource(self, name: str, relative: str, skills: list[dict[str, str]]) -> str:
        match = next((skill for skill in skills if skill["name"] == name), None)
        if not match:
            raise ValueError("Unknown or unavailable skill")
        rel = Path(relative)
        if not relative or rel.is_absolute() or rel.drive or rel.root or ".." in rel.parts:
            raise ValueError("Use a skill-relative resource path without '..'")
        base = Path(match["path"]).resolve(strict=True).parent
        target = base
        for part in rel.parts:
            if part in {"", "."}:
                continue
            target = target / part
            is_junction = getattr(os.path, "isjunction", lambda value: False)
            if target.is_symlink() or is_junction(target):
                raise ValueError("Skill resource links are not allowed")
        target = target.resolve(strict=True)
        if not target.is_relative_to(base) or not target.is_file():
            raise ValueError("Skill resource must be a file inside that skill folder")
        try:
            return target.read_text(encoding="utf8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"Could not read skill resource: {exc}") from exc
