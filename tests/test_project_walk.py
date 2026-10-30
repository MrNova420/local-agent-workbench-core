import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_walk import iter_project_files


class ProjectWalkTests(unittest.TestCase):
    def test_honors_nested_gitignore_rules_and_reinclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".gitignore").write_text("*.log\n!keep.log\n*.secret\nassets/\n", encoding="utf-8")
            (root / "main.py").write_text("visible", encoding="utf-8")
            (root / "skip.log").write_text("ignored", encoding="utf-8")
            (root / "keep.log").write_text("included", encoding="utf-8")
            (root / "assets").mkdir()
            (root / "assets" / "bundled.py").write_text("ignored dir", encoding="utf-8")
            source = root / "src"
            source.mkdir()
            (source / ".gitignore").write_text("/private/\n!keep.secret\n", encoding="utf-8")
            (source / "drop.secret").write_text("ignored", encoding="utf-8")
            (source / "keep.secret").write_text("included by nested rule", encoding="utf-8")
            (source / "private").mkdir()
            (source / "private" / "hidden.py").write_text("ignored dir", encoding="utf-8")
            (source / "visible.py").write_text("visible", encoding="utf-8")

            paths = {path.relative_to(root).as_posix() for path in iter_project_files(root)}

            self.assertEqual(paths, {".gitignore", "main.py", "keep.log", "src/.gitignore", "src/keep.secret", "src/visible.py"})

    def test_prunes_linked_directories_before_descending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            linked = root / "linked"
            linked.mkdir()
            (linked / "secret.py").write_text("outside-looking", encoding="utf-8")
            (root / "main.py").write_text("inside", encoding="utf-8")
            real_is_symlink = Path.is_symlink

            def marked_link(path):
                return path == linked or real_is_symlink(path)

            with patch.object(Path, "is_symlink", marked_link):
                paths = {path.relative_to(root).as_posix() for path in iter_project_files(root)}

            self.assertEqual(paths, {"main.py"})


if __name__ == "__main__":
    unittest.main()
