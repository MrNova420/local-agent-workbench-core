import tempfile
import unittest
from pathlib import Path

from desktop_engine import AgentEngine


class ResumedInstructionTests(unittest.TestCase):
    def test_resumed_history_receives_current_workspace_instructions(self):
        with tempfile.TemporaryDirectory() as root:
            engine = AgentEngine(root, project_instructions="Do not edit generated files.")
            captured = {}

            def finish(state, messages, cancel, emit):
                captured["messages"] = messages
                return {"message": {"role": "assistant", "content": "ready"}}

            engine.runtime.stream = finish
            previous = [{"role": "system", "content": "Older generic system prompt."},
                        {"role": "user", "content": "Earlier task"}]
            engine.run("Continue this project", previous)

            self.assertIn("Do not edit generated files.", captured["messages"][0]["content"])
            self.assertTrue(any(message.get("content") == "Earlier task" for message in captured["messages"]))


if __name__ == "__main__":
    unittest.main()
