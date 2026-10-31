import sys
import threading
import unittest
import time
import psutil
import tempfile
from pathlib import Path

from desktop_engine import ToolRegistry, Workspace


class CommandCancelTests(unittest.TestCase):
    def test_cancel_terminates_the_active_approved_command(self):
        cancel = threading.Event()
        tools = ToolRegistry(Workspace(Path.cwd()), lambda *_: True, cancel_event=cancel)
        timer = threading.Timer(0.25, cancel.set)
        timer.start()

        result = tools._command({
            "argv": [sys.executable, "-c", "import time; time.sleep(20)"],
            "timeout_seconds": 30,
        })

        self.assertIn("cancelled", result.casefold())
        self.assertTrue(cancel.is_set())

    def test_cancel_stops_command_children_that_hold_output_pipes(self):
        cancel = threading.Event()
        tools = ToolRegistry(Workspace(Path.cwd()), lambda *_: True, cancel_event=cancel)
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "child.pid"
            child = "import time; time.sleep(20)"
            parent = f"import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',{child!r}]); open({str(pid_file)!r},'w').write(str(p.pid)); time.sleep(20)"
            timer = threading.Timer(0.5, cancel.set)
            timer.start()
            result = tools._command({"argv": [sys.executable, "-c", parent], "timeout_seconds": 30})
            timer.cancel()
            self.assertIn("cancelled", result.casefold())
            self.assertTrue(pid_file.exists())
            child_pid = int(pid_file.read_text())
            deadline = time.monotonic() + 3
            while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(psutil.pid_exists(child_pid), "child process survived command cancellation")


if __name__ == "__main__":
    unittest.main()
