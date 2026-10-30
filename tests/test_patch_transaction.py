import tempfile
import unittest
from pathlib import Path

from desktop_engine import ToolRegistry, Workspace


class PatchTransactionTests(unittest.TestCase):
    def test_multi_file_patch_failure_leaves_every_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("old one\n", encoding="utf-8")
            second.write_text("old two\n", encoding="utf-8")
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            patch = (
                "--- a/first.txt\n+++ b/first.txt\n@@ -1 +1 @@\n-old one\n+new one\n"
                "--- a/second.txt\n+++ b/second.txt\n@@ -1 +1 @@\n-wrong text\n+new two\n"
            )

            result = registry.run("apply_patch", {"patch": patch})

            self.assertIn("mismatch", result.casefold())
            self.assertEqual(first.read_text(encoding="utf-8"), "old one\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old two\n")

    def test_unified_diff_deletion_removes_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "remove.txt"
            target.write_text("remove me\n", encoding="utf-8")
            registry = ToolRegistry(Workspace(root, "edit"), lambda *_: True)
            patch = "--- a/remove.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-remove me\n"

            result = registry.run("apply_patch", {"patch": patch})

            self.assertIn("remove.txt", result)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
