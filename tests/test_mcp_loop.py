import asyncio
import threading
import tempfile
import unittest
from pathlib import Path

from mcp_support import MCPManager, MCPRequestCancelled, MCPRequestUncertain


class MCPEventLoopTests(unittest.TestCase):
    def test_separate_calls_reuse_one_owned_event_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MCPManager(Path(directory) / "mcp.json")

            async def loop_id():
                return asyncio.get_running_loop()

            first = manager.run(loop_id())
            second = manager.run(loop_id())
            self.assertIs(first, second)
            thread = manager.loop_thread
            manager.shutdown()
            self.assertFalse(thread.is_alive())

    def test_cancel_interrupts_a_pending_mcp_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MCPManager(Path(directory) / "mcp.json")
            cancel = threading.Event()
            cleaned = threading.Event()

            async def wait_forever():
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()

            timer = threading.Timer(0.05, cancel.set)
            timer.start()
            with self.assertRaises(MCPRequestCancelled):
                manager.run(wait_forever(), cancel_event=cancel)
            self.assertTrue(cleaned.wait(1))
            manager.shutdown()

    def test_dispatched_mcp_transport_error_is_uncertain_and_does_not_echo_error_text(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MCPManager(Path(directory) / "mcp.json")

            class Session:
                async def call_tool(self, *_args):
                    raise TimeoutError("authorization=secret-value")
                async def __aexit__(self, *_args):
                    return None

            class Context:
                async def __aexit__(self, *_args):
                    return None

            manager.clients["calendar"] = (Session(), Context())
            manager.tools["mcp__calendar__create_event"] = ("calendar", "create_event")
            try:
                with self.assertRaises(MCPRequestUncertain) as caught:
                    manager.run(manager.call("mcp__calendar__create_event", {}))
                self.assertIn("TimeoutError", str(caught.exception))
                self.assertNotIn("secret-value", str(caught.exception))
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
