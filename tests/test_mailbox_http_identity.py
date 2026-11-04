"""Native mailbox requests identify the app to the API's edge service.

The live edge rejects urllib's default Python identity with error1010. A local
HTTP boundary reproduces that rejection without contacting a real mailbox or
minting credentials; both production clients must complete the same round trip.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.signaling_mailbox import _HttpTransport
from services import web_join_host


@pytest.fixture
def app_identity_mailbox():
    payloads = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body=None):
            encoded = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(encoded)

        def app_request(self):
            if self.headers.get("User-Agent") == "WebJam":
                return True
            self.reply(403, {"error": "runtime_client_identity_refused"})
            return False

        def do_POST(self):
            if not self.app_request():
                return
            assert self.headers.get("Content-Type") == "application/json"
            length = int(self.headers.get("Content-Length", "0"))
            assert 0 < length < 4096
            payloads[self.path] = json.loads(self.rfile.read(length))
            self.reply(201)

        def do_GET(self):
            if self.app_request():
                self.reply(200, payloads[self.path])

        def do_DELETE(self):
            if self.app_request():
                del payloads[self.path]
                self.reply(204)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", payloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("client_kind", ["native", "web"])
def test_native_mailbox_client_completes_app_identity_round_trip(
    app_identity_mailbox, monkeypatch, client_kind
):
    origin, payloads = app_identity_mailbox
    if client_kind == "native":
        request = _HttpTransport(origin).request
    else:
        monkeypatch.setattr(web_join_host, "MAILBOX_ORIGIN", origin)
        request = web_join_host.WebJoinMailbox(b"k" * 32, b"s" * 32)._http

    path = "/mailbox/inert-test-id/answer?client=web"
    body = {"payload": "inert-envelope-not-a-real-invitation"}
    status, _ = request("POST", path, body)
    assert status == 201
    assert request("GET", path, None) == (200, body)
    status, _ = request("DELETE", path, None)
    assert status == 204
    assert payloads == {}
