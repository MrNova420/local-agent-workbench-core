import tempfile
import unittest
from pathlib import Path

from desktop_engine import ToolRegistry, Workspace


class SearchBudgetTests(unittest.TestCase):
    def test_project_search_has_no_implicit_file_or_byte_scan_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry(Workspace(directory), lambda *_: True)
            self.assertIsNone(registry.search_file_limit)
            self.assertIsNone(registry.search_byte_limit)

    def test_exact_file_scan_limit_is_not_reported_as_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("needle a", encoding="utf-8")
            (root / "b.py").write_text("needle b", encoding="utf-8")
            registry = ToolRegistry(Workspace(root), lambda *_: True, search_file_limit=2)

            result = registry.run("search_files", {"query": "needle"})

            self.assertIn("a.py:1", result)
            self.assertIn("b.py:1", result)
            self.assertNotIn("scan capped", result.casefold())

    def test_file_scan_reports_incomplete_only_when_an_extra_file_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a.py", "b.py", "c.py"):
                (root / name).write_text("needle", encoding="utf-8")
            registry = ToolRegistry(Workspace(root), lambda *_: True, search_file_limit=2)

            result = registry.run("search_files", {"query": "needle"})

            self.assertIn("scan capped", result.casefold())
            self.assertNotIn("c.py:1", result)

    def test_search_stops_at_byte_budget_and_reports_incomplete_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("needle", encoding="utf-8")
            registry = ToolRegistry(Workspace(root), lambda *_: True, search_byte_limit=3)

            result = registry.run("search_files", {"query": "needle"})

            self.assertIn("capped", result.casefold())
            self.assertNotIn("a.py:1", result)

    def test_search_rejects_an_empty_query(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry(Workspace(directory), lambda *_: True)
            self.assertIn("must not be empty", registry.run("search_files", {"query": "  "}))


if __name__ == "__main__":
    unittest.main()
