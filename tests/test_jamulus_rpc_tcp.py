"""End-to-end protocol tests for JamulusRpcClient against a fake Jamulus
JSON-RPC server speaking the REAL wire protocol: newline-delimited JSON-RPC 2.0
over TCP, with jamulus/apiAuth + jamulusclient/* methods and notifications.

This proves the client interoperates with the actual Jamulus 3.x JSON-RPC API
(transport, auth handshake, method names, value ranges) without a real Jamulus.
"""
from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.jamulus_rpc_client import JamulusRpcClient


class _FakeJamulus:
    """Minimal Jamulus-client JSON-RPC server for tests (NDJSON over TCP)."""

    def __init__(self, secret: str, clients=None):
        self.secret = secret
        self.clients = clients if clients is not None else [
            {"id": 0, "name": "Me", "instrument": "Bass"},
            {"id": 1, "name": "Alice", "instrument": "Guitar"},
        ]
        self.received: list[dict] = []
        self._lock = threading.Lock()
        self._conn: socket.socket | None = None
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            self._srv.settimeout(5.0)
            conn, _ = self._srv.accept()
        except Exception:
            return
        self._conn = conn
        f = conn.makefile("r", encoding="utf-8", newline="\n")
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self.received.append(obj)
            self._handle(obj)
            if not self._running:
                break

    def _handle(self, obj: dict):
        method = obj.get("method")
        rid = obj.get("id")
        if method == "jamulus/apiAuth":
            ok = (obj.get("params") or {}).get("secret") == self.secret
            self._reply({"jsonrpc": "2.0", "id": rid, "result": "ok" if ok else "bad"})
        elif method == "jamulusclient/getChannelInfo":
            self._reply({"jsonrpc": "2.0", "id": rid, "result": {"id": 0, "name": "Me"}})
        elif method == "jamulusclient/getClientList":
            self._reply({"jsonrpc": "2.0", "id": rid, "result": {"clients": self.clients}})
        elif method and method.startswith("jamulusclient/set"):
            self._reply({"jsonrpc": "2.0", "id": rid, "result": "ok"})

    def _reply(self, obj: dict):
        try:
            self._conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        except Exception:
            pass

    def push(self, method: str, params: dict):
        self._reply({"jsonrpc": "2.0", "method": method, "params": params})

    def requests_for(self, method: str) -> list[dict]:
        with self._lock:
            return [r for r in self.received if r.get("method") == method]

    def stop(self):
        self._running = False
        for s in (self._conn, self._srv):
            try:
                if s:
                    s.close()
            except Exception:
                pass


def _wait(predicate, timeout=4.0, interval=0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _ClientHarness:
    """Builds a JamulusRpcClient wired to a _FakeJamulus + a secret file."""

    def __init__(self, secret="topsecretkey123456", server_secret=None, clients=None):
        self.server = _FakeJamulus(server_secret if server_secret is not None else secret,
                                   clients=clients)
        self._tmp = tempfile.NamedTemporaryFile("w", suffix=".secret", delete=False)
        self._tmp.write(secret + "\n")
        self._tmp.flush()
        self._tmp.close()
        self.participants = []
        self.levels = {}
        self.chats = []
        self.client = JamulusRpcClient(
            port=self.server.port,
            on_participants_changed=lambda c: self.participants.append(c),
            on_levels=lambda d: self.levels.update(d),
            on_chat=lambda t: self.chats.append(t),
            secret_path=Path(self._tmp.name),
        )

    def close(self):
        self.client.stop()
        self.server.stop()
        try:
            Path(self._tmp.name).unlink()
        except Exception:
            pass


class TestJamulusRpcTcp(unittest.TestCase):
    def test_authenticates_and_receives_participants(self):
        h = _ClientHarness()
        try:
            h.client.start()
            self.assertTrue(_wait(lambda: h.client.available), "client never authenticated")
            self.assertTrue(_wait(lambda: h.participants), "no participant callback")
            last = h.participants[-1]
            names = {c.name for c in last}
            self.assertEqual(names, {"Me", "Alice"})
            # local channel id (0) should be flagged is_local
            local = [c for c in last if c.is_local]
            self.assertEqual([c.channel_id for c in local], [0])
            # apiAuth was the first request
            self.assertEqual(h.server.received[0]["method"], "jamulus/apiAuth")
        finally:
            h.close()

    def test_set_channel_gain_sends_real_setfaderlevel(self):
        h = _ClientHarness()
        try:
            h.client.start()
            self.assertTrue(_wait(lambda: h.client.available))
            h.client.set_channel_gain(1, 127)   # full -> level 100
            self.assertTrue(_wait(
                lambda: h.server.requests_for("jamulusclient/setFaderLevel")))
            req = h.server.requests_for("jamulusclient/setFaderLevel")[-1]
            self.assertEqual(req["params"], {"channelIndex": 1, "level": 100})
            # mute -> level 0
            h.client.set_channel_mute(2, True)
            self.assertTrue(_wait(
                lambda: any(r["params"] == {"channelIndex": 2, "level": 0}
                            for r in h.server.requests_for("jamulusclient/setFaderLevel"))))
        finally:
            h.close()

    def test_set_self_muted_sends_real_setmuted(self):
        h = _ClientHarness()
        try:
            h.client.start()
            self.assertTrue(_wait(lambda: h.client.available))
            h.client.set_self_muted(True)
            self.assertTrue(_wait(
                lambda: h.server.requests_for("jamulusclient/setMuted")))
            req = h.server.requests_for("jamulusclient/setMuted")[-1]
            self.assertEqual(req["params"], {"muted": True})
        finally:
            h.close()

    def test_level_notification_normalized(self):
        h = _ClientHarness()
        try:
            h.client.start()
            self.assertTrue(_wait(lambda: h.client.available))
            h.server.push("jamulusclient/channelLevelListReceived",
                          {"channelLevelList": [9, 0, 5]})
            self.assertTrue(_wait(lambda: h.levels))
            self.assertEqual(h.levels[0], 1.0)   # 9/9
            self.assertEqual(h.levels[1], 0.0)   # 0/9
            self.assertAlmostEqual(h.levels[2], 5 / 9, places=3)
        finally:
            h.close()

    def test_client_list_notification_updates_participants(self):
        h = _ClientHarness()
        try:
            h.client.start()
            self.assertTrue(_wait(lambda: h.client.available))
            before = len(h.participants)
            h.server.push("jamulusclient/clientListReceived",
                          {"clients": [{"id": 0, "name": "Me"}]})
            self.assertTrue(_wait(lambda: len(h.participants) > before))
            self.assertEqual({c.name for c in h.participants[-1]}, {"Me"})
        finally:
            h.close()

    def test_send_chat_text_sends_real_sendchattext(self):
        h = _ClientHarness()
        try:
            h.client.start()
            self.assertTrue(_wait(lambda: h.client.available))
            h.client.send_chat_text("hey band")
            self.assertTrue(_wait(
                lambda: h.server.requests_for("jamulusclient/sendChatText")))
            req = h.server.requests_for("jamulusclient/sendChatText")[-1]
            self.assertEqual(req["params"], {"chatText": "hey band"})
        finally:
            h.close()

    def test_incoming_chat_notification_fires_callback(self):
        h = _ClientHarness()
        try:
            h.client.start()
            self.assertTrue(_wait(lambda: h.client.available))
            h.server.push("jamulusclient/chatTextReceived", {"chatText": "<b>Al</b> hi"})
            self.assertTrue(_wait(lambda: h.chats))
            self.assertEqual(h.chats[-1], "<b>Al</b> hi")
        finally:
            h.close()

    def test_wrong_secret_never_becomes_available(self):
        h = _ClientHarness(secret="clientsecret123456", server_secret="DIFFERENT_secret_99")
        try:
            h.client.start()
            self.assertFalse(_wait(lambda: h.client.available, timeout=1.5),
                             "must not authenticate with the wrong secret")
        finally:
            h.close()

    def test_no_secret_file_no_op(self):
        # Point at a non-existent secret file: client must stay unavailable and
        # its commands must no-op (so UDP/demo fallback takes over).
        client = JamulusRpcClient(port=1, secret_path=Path("/nonexistent/webjam.secret"))
        client.start()
        try:
            self.assertFalse(_wait(lambda: client.available, timeout=1.0))
            self.assertFalse(client.set_channel_gain(0, 100))
            self.assertIsNone(client.get_channel_clients())
        finally:
            client.stop()


if __name__ == "__main__":
    unittest.main()
