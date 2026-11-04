import os
import subprocess
import sys
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
        return_value=SimpleNamespace(returncode=0, stdout="NVIDIA GeForce GTX 1650 SUPER, 7, 3656, 4096, 7.20\n"),
    )
    def test_gpu_snapshot_exposes_utilization_memory_and_power(self, run, _which):
        snapshot = Telemetry().snapshot()

        self.assertEqual(
            snapshot["gpu_stats"],
            {
                "name": "NVIDIA GeForce GTX 1650 SUPER",
                "utilization_percent": 7,
                "memory_used_mib": 3656,
                "memory_total_mib": 4096,
                "power_w": 7.2,
            },
        )

    @patch("telemetry.shutil.which", return_value="nvidia-smi")
    @patch("telemetry.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=""))
    def test_empty_gpu_probe_reports_unavailable_instead_of_failing_snapshot(self, _run, _which):
        snapshot = Telemetry().snapshot()

        self.assertIsNone(snapshot["gpu_stats"])

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

    @patch("subprocess.Popen", wraps=subprocess.Popen)
    def test_approved_command_suppresses_console_window(self, popen):
        with tempfile.TemporaryDirectory() as root:
            registry = ToolRegistry(Workspace(Path(root)), lambda *_: True, skills_enabled=False)
            registry.run(
                "run_command",
                {"argv": [sys.executable, "-c", "pass"], "timeout_seconds": 10},
            )

        self.assertEqual(
            popen.call_args.kwargs.get("creationflags"),
            getattr(subprocess, "CREATE_NO_WINDOW", 0) | (0x00000004 if os.name == "nt" else 0),
        )

    @patch("subprocess.Popen")
    @patch("desktop_engine.WindowsProcessJob.attach", return_value=None)
    @patch("desktop_engine.WindowsProcessJob.resume_primary_thread", return_value=True)
    def test_git_diff_suppresses_console_window(self, resume, attach, popen):
        with tempfile.TemporaryDirectory() as root:
            class Process:
                pid = 4321
                returncode = 0
                def __init__(self, output=b""):
                    self.output = output
                def communicate(self, timeout=None):
                    return self.output, b""
                def poll(self):
                    return self.returncode
            def start(argv, **kwargs):
                return Process(str(Path(root).resolve()).encode() if "--show-toplevel" in argv else b"")
            popen.side_effect = start
            registry = ToolRegistry(Workspace(Path(root)), lambda *_: True, skills_enabled=False)
            registry.run("git_diff", {})

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | (0x00000004 if os.name == "nt" else 0)
        self.assertEqual([call.kwargs.get("creationflags") for call in popen.call_args_list], [flags, flags])


if __name__ == "__main__":
    unittest.main()
