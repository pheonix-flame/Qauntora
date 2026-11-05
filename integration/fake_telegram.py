"""A local HTTP server that speaks enough of the Telegram Bot API for integration tests.

Notify talks to it over real HTTP exactly as it talks to api.telegram.org
(only the base URL differs: TELEGRAM_API_BASE). Records every call.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                _, bot, method = self.path.split("/", 2)
                fake.calls.append({"token": bot[3:], "method": method, "body": body})
                result = {"username": "quantora_test_bot", "first_name": "Q"} if method == "getMe" else \
                    [] if method == "getUpdates" else {"message_id": len(fake.calls)}
                data = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def messages(self) -> list[dict]:
        return [c for c in self.calls if c["method"] == "sendMessage"]

    def stop(self) -> None:
        self.server.shutdown()
