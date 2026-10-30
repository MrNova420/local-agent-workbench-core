import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desktop_engine import ToolRegistry, Workspace
from telemetry import Telemetry


class TelemetryWindowsTests(unittest.TestCase):
    @patch("telemetry.shutil.which", return_value="nvidia-smi")
    @patch(
        "telemetry.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="NVIDIA GeForce, 1, 4096, 0\n"),
    )
    def test_gpu_probe_suppresses_console_window(self, run, _which):
        Telemetry().snapshot()

        self.assertEqual(
            run.call_args.kwargs.get("creationflags"),
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @patch(
        "subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    def test_approved_command_suppresses_console_window(self, run):
        with tempfile.TemporaryDirectory() as root:
            registry = ToolRegistry(Workspace(Path(root)), lambda *_: True)
            registry.run(
                "run_command",
                {"argv": ["python", "-c", "pass"], "timeout_seconds": 10},
            )

        self.assertEqual(
            run.call_args.kwargs.get("creationflags"),
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @patch(
        "subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    def test_git_diff_suppresses_console_window(self, run):
        with tempfile.TemporaryDirectory() as root:
            registry = ToolRegistry(Workspace(Path(root)), lambda *_: True)
            registry.run("git_diff", {})

        self.assertEqual(
            run.call_args.kwargs.get("creationflags"),
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


if __name__ == "__main__":
    unittest.main()
