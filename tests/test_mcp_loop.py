import asyncio
import tempfile
import unittest
from pathlib import Path

from mcp_support import MCPManager


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


if __name__ == "__main__":
    unittest.main()
