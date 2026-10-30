import tempfile
import unittest
from pathlib import Path

from desktop_engine import ContextManager


class RetrievalBoundTests(unittest.TestCase):
    def test_retrieval_stops_at_file_budget_and_reports_partial_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("unrelated", encoding="utf-8")
            (root / "b.py").write_text("unrelated", encoding="utf-8")
            (root / "z.py").write_text("needle in the last file", encoding="utf-8")
            manager = ContextManager(scan_file_limit=2)

            result = manager.retrieve(root, "needle")

            self.assertIn("scan capped", result.casefold())
            self.assertNotIn("z.py", result)


if __name__ == "__main__":
    unittest.main()
