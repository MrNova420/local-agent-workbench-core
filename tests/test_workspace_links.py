import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_engine import Workspace


class WorkspaceLinkTests(unittest.TestCase):
    def test_rejects_internal_file_symlink_before_resolving_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "real.txt").write_text("inside", encoding="utf-8")
            workspace = Workspace(root)
            real_is_symlink = Path.is_symlink
            real_resolve = Path.resolve
            def marked_link(path):
                return path.name == "linked.txt" or real_is_symlink(path)
            def redirect_link(path, *args, **kwargs):
                if path.name == "linked.txt":
                    return root / "real.txt"
                return real_resolve(path, *args, **kwargs)
            with patch.object(Path, "is_symlink", marked_link), patch.object(Path, "resolve", redirect_link):
                with self.assertRaisesRegex(ValueError, "Symbolic links"):
                    workspace.resolve("linked.txt")


if __name__ == "__main__":
    unittest.main()
