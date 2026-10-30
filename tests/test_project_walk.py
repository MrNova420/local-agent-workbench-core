import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_walk import iter_project_files


class ProjectWalkTests(unittest.TestCase):
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
