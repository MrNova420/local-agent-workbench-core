import unittest
from desktop_engine import ContextManager


class ContextBudgetTests(unittest.TestCase):
    def test_selected_output_is_reserved_without_a_hidden_fixed_token_cushion(self):
        small = ContextManager(context=4096).budget(output_tokens=128)
        large = ContextManager(context=65536).budget(output_tokens=8192)

        self.assertEqual(small, 3968)
        self.assertEqual(large, 57344)

    def test_compaction_counts_tool_schemas_and_output_reserve(self):
        manager = ContextManager(context=4096, reserve=256)
        messages = [{"role": "system", "content": "system"},
                    {"role": "user", "content": "small task"}]
        tools = [{"type": "function", "function": {"name": "huge", "description": "x" * 6000}}]

        with self.assertRaisesRegex(ValueError, "Tool definitions"):
            manager.compact(messages, tools=tools, output_tokens=2048)


if __name__ == "__main__":
    unittest.main()
