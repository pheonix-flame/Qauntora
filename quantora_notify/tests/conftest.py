import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeTelegram:
    """A local HTTP server speaking enough of the Telegram Bot API to test real requests.

    Set .script to a list of (status, body) to return for the next calls (default: ok).
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.script: list[tuple[int, dict]] = []
        self.updates: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                _, bot, method = self.path.split("/", 2)
                fake.calls.append({"token": bot[3:], "method": method, "body": body})
                if fake.script:
                    status, resp = fake.script.pop(0)
                elif method == "getMe":
                    status, resp = 200, {"ok": True, "result": {"username": "quantora_test_bot", "first_name": "Q"}}
                elif method == "getUpdates":
                    status, resp = 200, {"ok": True, "result": fake.updates}
                else:
                    status, resp = 200, {"ok": True, "result": {"message_id": len(fake.calls)}}
                data = json.dumps(resp).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def messages(self):
        return [c["body"]["text"] for c in self.calls if c["method"] == "sendMessage"]


@pytest.fixture
def tg():
    fake = FakeTelegram()
    yield fake
    fake.server.shutdown()
