import tempfile
import unittest
from pathlib import Path

from turn_context import validate_selected_workspace


class WorkspaceSelectionTests(unittest.TestCase):
    def test_fallback_directory_is_not_a_project_until_user_selects_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Choose a project"):
                validate_selected_workspace(False, directory)

    def test_selected_project_must_exist_as_a_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            self.assertEqual(validate_selected_workspace(True, project), project.resolve())
            with self.assertRaisesRegex(ValueError, "unavailable|directory"):
                validate_selected_workspace(True, project / "missing")


if __name__ == "__main__":
    unittest.main()
