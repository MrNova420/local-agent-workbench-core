import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch

from disposable_workspace import apply_reviewed_changes, create_disposable_workspace, managed_copy_path, review_changes


class DisposableWorkspaceTests(unittest.TestCase):
    def test_copy_omits_links_and_vcs_folders_without_touching_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            source.mkdir()
            (source / ".git").mkdir()
            (source / ".git" / "private").write_text("private")
            (source / "main.py").write_text("print('base')\n")
            external = base / "outside.txt"
            external.write_text("outside")
            linked = False
            try:
                (source / "linked.txt").symlink_to(external)
                linked = True
            except OSError:
                pass
            working = base / "store" / "session" / "project"
            manifest = create_disposable_workspace(source, working)

            self.assertEqual((working / "main.py").read_text(), "print('base')\n")
            self.assertFalse((working / ".git").exists())
            if linked:
                self.assertFalse((working / "linked.txt").exists())
            self.assertEqual(manifest, {"main.py": hashlib.sha256((source / "main.py").read_bytes()).hexdigest()})
            self.assertEqual((source / "main.py").read_text(), "print('base')\n")

    def test_copy_omits_gitignored_files_and_nested_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            (source / ".gitignore").write_text("*.secret\ncache/\n", encoding="utf-8")
            (source / "app.py").write_text("visible", encoding="utf-8")
            (source / "token.secret").write_text("ignored", encoding="utf-8")
            cache = source / "cache"
            cache.mkdir()
            (cache / "data.bin").write_bytes(b"ignored")
            nested = source / "src"
            nested.mkdir()
            (nested / ".gitignore").write_text("local.json\n", encoding="utf-8")
            (nested / "local.json").write_text("ignored", encoding="utf-8")
            (nested / "code.py").write_text("visible", encoding="utf-8")
            working = Path(temp) / "store" / "session" / "project"

            create_disposable_workspace(source, working)

            self.assertTrue((working / "app.py").exists())
            self.assertTrue((working / "src" / "code.py").exists())
            self.assertFalse((working / "token.secret").exists())
            self.assertFalse((working / "cache").exists())
            self.assertFalse((working / "src" / "local.json").exists())

    def test_review_lists_changes_and_flags_source_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            working = base / "working"
            source.mkdir()
            (source / "edit.py").write_text("original\n")
            (source / "delete.txt").write_text("remove me\n")
            manifest = create_disposable_workspace(source, working)
            (working / "edit.py").write_text("agent version\n")
            (working / "delete.txt").unlink()
            (working / "new.md").write_text("new file\n")
            (source / "edit.py").write_text("changed outside session\n")

            changes = {item["path"]: item for item in review_changes(source, working, manifest)}

            self.assertEqual(set(changes), {"edit.py", "delete.txt", "new.md"})
            self.assertTrue(changes["edit.py"]["conflict"])
            self.assertFalse(changes["delete.txt"]["conflict"])
            self.assertFalse(changes["new.md"]["conflict"])

    def test_reviewed_changes_apply_and_source_races_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            source.mkdir()
            (source / "edit.py").write_text("original\n")
            working = base / "store" / "project"
            manifest = create_disposable_workspace(source, working)
            (working / "edit.py").write_text("reviewed version\n")
            changes = review_changes(source, working, manifest)

            self.assertEqual(apply_reviewed_changes(source, changes), ["edit.py"])
            self.assertEqual((source / "edit.py").read_text(), "reviewed version\n")

            (working / "edit.py").write_text("next version\n")
            stale_review = review_changes(source, working, manifest)
            (source / "edit.py").write_text("external edit\n")
            with self.assertRaisesRegex(ValueError, "conflicts|changed"):
                apply_reviewed_changes(source, stale_review)
            self.assertEqual((source / "edit.py").read_text(), "external edit\n")

    def test_copy_file_cannot_replace_a_source_file_created_after_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            source.mkdir()
            working = base / "store" / "project"
            manifest = create_disposable_workspace(source, working)
            (working / "notes.md").write_text("agent draft\n")
            (source / "notes.md").write_bytes(b"x" * 600_000)
            changes = review_changes(source, working, manifest)

            self.assertTrue(changes[0]["conflict"])
            with self.assertRaisesRegex(ValueError, "conflicts"):
                apply_reviewed_changes(source, changes)
            self.assertEqual((source / "notes.md").stat().st_size, 600_000)

    def test_oversized_copy_file_is_not_misreported_as_a_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            source.mkdir()
            (source / "main.py").write_text("base\n")
            working = base / "store" / "project"
            manifest = create_disposable_workspace(source, working)
            (working / "main.py").write_bytes(b"x" * 600_000)

            changes = review_changes(source, working, manifest)

            self.assertEqual(changes[0]["action"], "unsupported")
            self.assertTrue(changes[0]["conflict"])

    def test_baseline_hashes_match_the_copy_even_if_source_changes_mid_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            source.mkdir()
            original = source / "main.py"
            original.write_text("copied version\n")
            copied_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            destination = base / "store" / "project"
            real_copy = __import__("shutil").copy2

            def copy_then_change_source(source_path, destination_path, **kwargs):
                result = real_copy(source_path, destination_path, **kwargs)
                original.write_text("external concurrent change\n")
                return result

            with patch("disposable_workspace.shutil.copy2", side_effect=copy_then_change_source):
                manifest = create_disposable_workspace(source, destination)

            (destination / "main.py").write_text("agent edit\n")
            changes = review_changes(source, destination, manifest)
            self.assertEqual(manifest["main.py"], copied_hash)
            self.assertTrue(changes[0]["conflict"])

    def test_apply_batch_rolls_back_if_a_later_file_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            source.mkdir()
            (source / "a.py").write_text("old a\n")
            (source / "b.py").write_text("old b\n")
            working = base / "store" / "project"
            manifest = create_disposable_workspace(source, working)
            (working / "a.py").write_text("new a\n")
            (working / "b.py").write_text("new b\n")
            changes = review_changes(source, working, manifest)
            real_replace = __import__("os").replace
            calls = 0

            def fail_once(source_path, target_path):
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("simulated second file placement failure")
                return real_replace(source_path, target_path)

            with patch("disposable_workspace.os.replace", side_effect=fail_once):
                with self.assertRaisesRegex(OSError, "simulated"):
                    apply_reviewed_changes(source, changes)

            self.assertEqual((source / "a.py").read_text(), "old a\n")
            self.assertEqual((source / "b.py").read_text(), "old b\n")

    def test_session_copy_path_is_confined_to_managed_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspaces"
            path = managed_copy_path(root, "20261030-120000-ab12cd34")
            self.assertEqual(path, root / "20261030-120000-ab12cd34" / "project")
            with self.assertRaisesRegex(ValueError, "session"):
                managed_copy_path(root, "..\\outside")


if __name__ == "__main__":
    unittest.main()
