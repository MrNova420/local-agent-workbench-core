import sys
import threading
import unittest
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


if __name__ == "__main__":
    unittest.main()
