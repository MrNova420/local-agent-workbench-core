import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from turn_context import TurnContext


class TurnContextTests(unittest.TestCase):
    def test_turn_isolated_from_later_ui_and_session_mutation(self):
        session = {"session_id": "s1", "messages": [{"role": "user", "content": "old"}]}
        runtime_options = {"num_thread": 4}
        with TemporaryDirectory() as root:
            turn = TurnContext.capture(
                workspace=Path(root), model="model-a", context=262144,
                access="ask", approval_mode="per-action", max_tokens=4096,
                temperature=0.2, runtime_options=runtime_options, prompt="task",
                session=session, mcp_tools=[{"name": "tool-a"}], skills_enabled=False,
                skill_sources=["project"],
            )

        session["messages"].append({"role": "user", "content": "later"})
        runtime_options["num_thread"] = 12
        with self.assertRaises((AttributeError, TypeError)):
            turn.model = "model-b"

        self.assertEqual(turn.model, "model-a")
        self.assertEqual(turn.session["messages"][-1]["content"], "old")
        self.assertEqual(turn.runtime_options["num_thread"], 4)
        self.assertEqual(turn.mcp_tools[0]["name"], "tool-a")
        self.assertFalse(turn.skills_enabled)
        self.assertEqual(turn.skill_sources, ("project",))


if __name__ == "__main__":
    unittest.main()
