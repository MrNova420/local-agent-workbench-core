import tempfile
import unittest
from pathlib import Path

from desktop_engine import ToolRegistry, Workspace


class SearchBudgetTests(unittest.TestCase):
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
