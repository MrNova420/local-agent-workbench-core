import unittest
from desktop_engine import ContextManager


class ContextBudgetTests(unittest.TestCase):
    def test_exact_provider_count_calibrates_future_estimates_for_that_model(self):
        manager = ContextManager(context=65536)
        checkpoint = {}
        request = '{"messages":[{"role":"user","content":"hello"}],"tools":[]}'
        initial = manager.estimate(request)
        manager.configure_provider_calibration(checkpoint, "ollama", "flint-local")

        manager.observe_provider_prompt(checkpoint, "ollama", "flint-local", initial, 7385)

        self.assertEqual(manager.estimate_request(request), 7385)
        self.assertEqual(checkpoint["provider_token_calibrations"]["ollama|flint-local"]["observation_count"], 1)

    def test_calibration_keeps_highest_observed_ratio_and_is_model_specific(self):
        manager = ContextManager(context=65536)
        checkpoint = {}
        request = "x" * 100
        initial = manager.estimate(request)
        manager.configure_provider_calibration(checkpoint, "ollama", "flint-local")

        manager.observe_provider_prompt(checkpoint, "ollama", "flint-local", initial, 30)
        manager.observe_provider_prompt(checkpoint, "ollama", "flint-local", initial, 40)
        manager.observe_provider_prompt(checkpoint, "ollama", "other-model", initial, 20)

        self.assertEqual(manager.estimate_request(request), 40)
        manager.configure_provider_calibration(checkpoint, "ollama", "other-model")
        self.assertEqual(manager.estimate_request(request), 20)
        self.assertEqual(checkpoint["provider_token_calibrations"]["ollama|flint-local"]["observation_count"], 2)

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
