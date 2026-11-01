import json
import tempfile
import unittest
from pathlib import Path

from sessions import SessionStore
from desktop_engine import CONTEXT_MAX, MODEL


class SessionValidationTests(unittest.TestCase):
    def test_legacy_session_migrates_to_ollama_provider_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(directory, MODEL, CONTEXT_MAX)
            store.save(state)
            path = store.root / f"{state['session_id']}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("provider_id", None); data.pop("model_id", None)
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = store.load(state["session_id"])
            self.assertEqual(loaded["provider_id"], "ollama")
            self.assertEqual(loaded["model_id"], MODEL)

    def test_auto_approval_and_reasoning_choice_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(directory, MODEL, CONTEXT_MAX)
            state.update(approval_mode="auto", reasoning="high", max_tokens=1000000)
            store.save(state)
            loaded = store.load(state["session_id"])
            self.assertEqual(loaded["approval_mode"], "auto")
            self.assertEqual(loaded["reasoning"], "high")
            self.assertEqual(loaded["max_tokens"], 1000000)

    def test_saved_chat_session_approval_mode_and_grant_round_trip(self):
        from sessions import SessionStore

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "project"
            workspace.mkdir()
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(str(workspace), "model-a:latest", 4096)
            state.update(approval_mode="per-session", session_approval_granted=True)
            store.save(state)
            loaded = store.load(state["session_id"])
            self.assertEqual(loaded["approval_mode"], "per-session")
            self.assertTrue(loaded["session_approval_granted"])

    def test_load_rejects_malformed_message_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(directory, MODEL, CONTEXT_MAX)
            store.save(state)
            path = store.root / f"{state['session_id']}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["messages"] = {"role": "user", "content": "not a list"}
            path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "message history"):
                store.load(state["session_id"])

    def test_load_rejects_session_id_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(directory, MODEL, CONTEXT_MAX)
            store.save(state)
            path = store.root / f"{state['session_id']}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["session_id"] = "another-session"
            path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "identifier"):
                store.load(state["session_id"])

    def test_load_rejects_invalid_saved_run_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(directory, MODEL, CONTEXT_MAX)
            state.update(max_tokens=0, temperature=float("nan"))
            store.save(state)

            with self.assertRaisesRegex(ValueError, "output token"):
                store.load(state["session_id"])

    def test_load_rejects_unsafe_copy_access_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(directory, MODEL, CONTEXT_MAX)
            state["access"] = "host-write-anywhere"
            store.save(state)

            with self.assertRaisesRegex(ValueError, "access mode"):
                store.load(state["session_id"])

    def test_load_rejects_copy_baseline_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(directory, MODEL, CONTEXT_MAX)
            state.update(access="copy", copy_manifest={"../outside.py": "a" * 64})
            store.save(state)

            with self.assertRaisesRegex(ValueError, "baseline entry"):
                store.load(state["session_id"])

    def test_load_rejects_nonfinite_saved_temperature(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions")
            state = store.create(directory, MODEL, CONTEXT_MAX)
            state.update(max_tokens=4096, temperature=float("nan"))
            store.save(state)

            with self.assertRaisesRegex(ValueError, "temperature"):
                store.load(state["session_id"])


if __name__ == "__main__":
    unittest.main()
