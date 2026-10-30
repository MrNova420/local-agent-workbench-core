import json
import tempfile
import unittest
from pathlib import Path

from sessions import SessionStore
from desktop_engine import CONTEXT_MAX, MODEL


class SessionValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
