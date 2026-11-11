"""Stage 2 diagnosis: tool-schema stability across consecutive agent requests.

Ollama's automatic prompt cache reuses the rendered prompt prefix only while
it is byte-identical. Chat templates render the tools array early in the
prompt, so a per-request change in the *selected tool subset* (or inline
skill-catalog text) invalidates the whole prefix and forces full re-evaluation
of every prior token — the observed 0%-cached, 1-3-minute-per-request behavior.

These tests pin the required contract: within one task, consecutive requests
must present an identical tools array (same names, same order, same schema
bytes) unless the budget itself forces a change, and any deferral must be
append-only (previously sent schemas stay).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_engine import AgentEngine, ContextManager  # noqa: E402


class _Context:
    """Minimal stand-in exposing the estimator API _tools_for_request uses."""

    def __init__(self, context: int = 65536) -> None:
        self.context = context
        self._cm = ContextManager

    def budget(self, max_tokens: int) -> int:
        return self.context - max_tokens

    def estimate_request(self, text: str) -> int:
        return ContextManager.estimate(text)


class _Registry:
    def __init__(self, schemas: list[dict]) -> None:
        self.schemas = schemas
        self.skill_rows = []


class ToolSelectionStabilityTests(unittest.TestCase):
    def _engine_tools(self, schemas: list[dict], prompts: list[str]):
        """Call _tools_for_request once per prompt, as consecutive requests would."""

        class _Harness:
            def __init__(self) -> None:
                self.state = {"registry": _Registry(schemas), "mcp_tools": [], "max_tokens": 8192,
                              "deferred_tool_names": []}
                self.context = _Context()
                self._deferred_tool_names: list[str] = []
                self._catalog_skill_names: list[str] = []
                self._selected_tool_names: set[str] = set()
                self.mcp_tools = []
                self.registry = self.state["registry"]

            def _tools_for_request(self, base_messages, selected_names=None):
                return AgentEngine._tools_for_request(self, base_messages, selected_names)

            def _load_tools(self, args, base_messages, response_byte_budget=None):
                return AgentEngine._load_tools(self, args, base_messages, response_byte_budget)

        harness = _Harness()
        results = []
        for prompt in prompts:
            base = [{"role": "system", "content": "sys"}, {"role": "user", "content": prompt}]
            tools = AgentEngine._tools_for_request(harness, base)
            results.append(json.dumps(tools, sort_keys=True, ensure_ascii=False))
        return harness, results

    def _schemas(self) -> list[dict]:
        def schema(name: str, description: str) -> dict:
            return {"type": "function", "function": {"name": name, "description": description, "parameters": {}}}

        return [schema("load_tools", "load additional tool schemas"),
                schema("update_plan", "update the task plan"),
                schema("list_files", "list project files"),
                schema("read_file", "read a project file"),
                schema("write_file", "write a project file"),
                schema("edit_file", "edit a project file"),
                schema("run_command", "run an approved command"),
                schema("git_status", "git status of the project"),
                schema("append_file", "append to a project file")]

    def test_same_prompt_repeated_selects_identical_tools(self):
        _, results = self._engine_tools(self._schemas(), ["fix the login bug"] * 2)
        self.assertEqual(results[0], results[1])

    def test_evolved_task_prompt_keeps_tool_array_stable(self):
        # Request 2's prompt contains different words; the rendered prefix
        # must not change, or the provider re-evaluates everything.
        _, results = self._engine_tools(
            self._schemas(),
            ["fix the login bug in the auth module",
             "the auth module now needs a test; run the suite after the fix"])
        self.assertEqual(results[0], results[1], "tool schema array changed between consecutive requests")

    def test_explicitly_loaded_tool_is_merged_and_then_stays_sticky(self):
        harness, _ = self._engine_tools(self._schemas(), ["fix the login bug"])
        original = list(harness._sticky_request_tools)
        # Exercise the same explicit-schema loading path used by the model's
        # load_tools action, rather than mutating the selection state directly.
        message = [{"role": "system", "content": "sys"}, {"role": "user", "content": "continue"}]
        result = AgentEngine._load_tools(harness, {"names": ["append_file"]}, message)
        self.assertIn("append_file", result)
        request2 = AgentEngine._tools_for_request(harness, message)
        request2_names = [(tool.get("function") or {}).get("name") for tool in request2]
        self.assertIn("append_file", request2_names)
        self.assertNotIn("append_file", harness._deferred_tool_names)
        self.assertEqual(request2[:len(original)], original)
        request3 = AgentEngine._tools_for_request(
            harness, [{"role": "system", "content": "sys"}, {"role": "user", "content": "another continuation"}])
        self.assertEqual(request2, request3)


if __name__ == "__main__":
    unittest.main()


class MessagePrefixStabilityTests(unittest.TestCase):
    def test_system_prompt_is_deterministic(self):
        from desktop_engine import agent_system_prompt
        first = agent_system_prompt("ask", "Project rules here", "standard", True, "auto", "")
        second = agent_system_prompt("ask", "Project rules here", "standard", True, "auto", "")
        self.assertEqual(first, second)
        self.assertNotIn("20", first[:20])  # no leading timestamp

    def test_continuation_guidance_appends_without_mutating_history(self):
        from desktop_engine import _append_continuation_guidance
        history = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                   {"role": "assistant", "content": "a"}]
        frozen = json.dumps(history, sort_keys=True)
        request = _append_continuation_guidance(history, "continue with the next step")
        self.assertEqual(json.dumps(history, sort_keys=True), frozen, "history was mutated")
        self.assertEqual(request[-1]["role"], "user")
        self.assertIn("[Current Workbench continuation]", request[-1]["content"])
        self.assertEqual(_append_continuation_guidance(history, None), history)

    def test_provider_history_preserves_order_and_strips_metadata(self):
        from desktop_engine import _provider_message_history
        msgs = [{"role": "system", "content": "s"},
                {"role": "user", "content": "q", "turn_id": "t1"},
                {"role": "assistant", "content": "a", "steering_id": "x"}]
        out = _provider_message_history(msgs)
        self.assertEqual([m.get("role") for m in out], ["system", "user", "assistant"])
        self.assertTrue(all("turn_id" not in m and "steering_id" not in m for m in out))
