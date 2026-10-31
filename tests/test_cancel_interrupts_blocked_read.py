import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from desktop_engine import OllamaRuntime, RequestCancelled


class CancelBlockedReadTests(unittest.TestCase):
    def test_cancel_interrupts_a_live_ollama_read_without_waiting_for_next_token(self):
        chunk_sent = threading.Event()
        release_server = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                line = b'{"message":{"content":"partial"}}\n'
                self.wfile.write(f"{len(line):X}\r\n".encode() + line + b"\r\n")
                self.wfile.flush()
                chunk_sent.set()
                release_server.wait(3)
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except OSError:
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        runtime = OllamaRuntime(f"http://127.0.0.1:{server.server_port}")
        cancel = threading.Event()
        received_text = threading.Event()
        errors = []
        state = {
            "model": "mock",
            "registry": type("Registry", (), {"schemas": []})(),
            "temperature": 0.2,
            "context": 4096,
            "max_tokens": 12,
        }

        def run():
            try:
                runtime.stream(state, [], cancel, lambda event: received_text.set() if event.get("type") == "text" else None)
            except Exception as exc:
                errors.append(exc)

        stream_thread = threading.Thread(target=run, daemon=True)
        cancel_thread = None
        try:
            stream_thread.start()
            self.assertTrue(chunk_sent.wait(1), "the fixture did not deliver its first token")
            self.assertTrue(received_text.wait(1), "the runtime did not expose its first token")
            cancel.set()
            cancel_thread = threading.Thread(target=runtime.cancel, daemon=True)
            cancel_thread.start()
            cancel_thread.join(0.75)
            cancel_returned_promptly = not cancel_thread.is_alive()
        finally:
            release_server.set()
            stream_thread.join(2)
            if cancel_thread is not None:
                cancel_thread.join(2)
            server.shutdown()
            server.server_close()

        self.assertTrue(cancel_returned_promptly, "cancel blocked while the Ollama stream awaited another token")
        self.assertFalse(stream_thread.is_alive(), "the generation reader remained blocked after cancellation")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RequestCancelled)
        self.assertEqual(errors[0].partial_message, {"role": "assistant", "content": "partial"})


if __name__ == "__main__":
    unittest.main()
