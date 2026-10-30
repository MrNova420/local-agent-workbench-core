import unittest
from desktop_engine import ContextManager


class ContextBudgetTests(unittest.TestCase):
    def test_compaction_counts_tool_schemas_and_output_reserve(self):
        manager = ContextManager(context=4096, reserve=256)
        messages = [{"role": "system", "content": "system"},
                    {"role": "user", "content": "small task"}]
        tools = [{"type": "function", "function": {"name": "huge", "description": "x" * 6000}}]

        with self.assertRaisesRegex(ValueError, "Tool definitions"):
            manager.compact(messages, tools=tools, output_tokens=2048)


if __name__ == "__main__":
    unittest.main()
