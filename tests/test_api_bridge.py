from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    TESTCLIENT_AVAILABLE = True
except Exception:
    TESTCLIENT_AVAILABLE = False

try:
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401
    BRIDGE_RUNTIME_AVAILABLE = True
except Exception:
    BRIDGE_RUNTIME_AVAILABLE = False

from api.local_bridge import LocalApiBridge


class TestLocalApiBridgeLifecycle(unittest.TestCase):
    def test_stop_when_not_started_is_safe(self):
        bridge = LocalApiBridge(get_participants=lambda: [], get_diagnostics=lambda: {})
        bridge.stop()

    def test_start_returns_true_when_fastapi_available(self):
        bridge = LocalApiBridge(get_participants=lambda: [], get_diagnostics=lambda: {})
        result = bridge.start()
        if result:
            bridge.stop()
        # result depends on whether fastapi/uvicorn are installed

    def test_start_rejects_invalid_host(self):
        bridge = LocalApiBridge(get_participants=lambda: [], get_diagnostics=lambda: {}, host="")
        self.assertFalse(bridge.start())

    def test_start_rejects_invalid_port_type(self):
        bridge = LocalApiBridge(get_participants=lambda: [], get_diagnostics=lambda: {}, port="bad")  # type: ignore[arg-type]
        self.assertFalse(bridge.start())

    @unittest.skipUnless(BRIDGE_RUNTIME_AVAILABLE, "fastapi/uvicorn not available")
    @patch("api.local_bridge.threading.Thread")
    def test_start_thread_failure_returns_false(self, thread_cls):
        thread_cls.return_value.start.side_effect = RuntimeError("thread boom")
        bridge = LocalApiBridge(get_participants=lambda: [], get_diagnostics=lambda: {})
        self.assertFalse(bridge.start())


@unittest.skipUnless(TESTCLIENT_AVAILABLE, "fastapi.testclient not available")
class TestLocalApiBridgeEndpoints(unittest.TestCase):
    def setUp(self):
        self.bridge = LocalApiBridge(
            get_participants=lambda: [{"name": "Alice", "channel": 0}],
            get_diagnostics=lambda: {"status": "ok", "backend": "test"},
        )
        from fastapi import FastAPI, HTTPException
        self.app = self.bridge._create_app(FastAPI, HTTPException)
        # base_url sets the Host header to a loopback name the bridge accepts.
        self.client = TestClient(self.app, base_url="http://127.0.0.1")

    def test_health(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_participants(self):
        resp = self.client.get("/participants")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("participants", data)
        self.assertEqual(len(data["participants"]), 1)
        self.assertEqual(data["participants"][0]["name"], "Alice")

    def test_diagnostics(self):
        resp = self.client.get("/diagnostics")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("diagnostics", data)
        self.assertEqual(data["diagnostics"]["backend"], "test")

    def test_rejects_non_loopback_host(self):
        """A DNS-rebinding origin (non-loopback Host) must be refused."""
        evil = TestClient(self.app, base_url="http://evil.example.com")
        for path in ("/health", "/participants", "/diagnostics"):
            resp = evil.get(path)
            self.assertEqual(resp.status_code, 403, f"{path} should reject foreign Host")

    def test_accepts_loopback_variants(self):
        # ([::1] is exercised by test_host_allowed_helper; the test client
        # can't construct a bracketed-IPv6 base_url.)
        for base in ("http://localhost", "http://127.0.0.1:8765"):
            c = TestClient(self.app, base_url=base)
            self.assertEqual(c.get("/health").status_code, 200, base)

    def test_host_allowed_helper(self):
        ok = LocalApiBridge._host_allowed
        self.assertTrue(ok("127.0.0.1"))
        self.assertTrue(ok("localhost:8765"))
        self.assertTrue(ok("[::1]:8765"))
        self.assertFalse(ok("evil.com"))
        self.assertFalse(ok(""))
        self.assertFalse(ok(None))
        self.assertFalse(ok("127.0.0.1.evil.com"))

    def test_participants_callback_error_returns_500(self):
        def failing_cb():
            raise RuntimeError("boom")
        bridge = LocalApiBridge(get_participants=failing_cb, get_diagnostics=lambda: {})
        from fastapi import FastAPI, HTTPException
        app = bridge._create_app(FastAPI, HTTPException)
        client = TestClient(app, raise_server_exceptions=False, base_url="http://127.0.0.1")
        resp = client.get("/participants")
        self.assertEqual(resp.status_code, 500)

    def test_diagnostics_callback_error_returns_500(self):
        def failing_cb():
            raise RuntimeError("boom")
        bridge = LocalApiBridge(get_participants=lambda: [], get_diagnostics=failing_cb)
        from fastapi import FastAPI, HTTPException
        app = bridge._create_app(FastAPI, HTTPException)
        client = TestClient(app, raise_server_exceptions=False, base_url="http://127.0.0.1")
        resp = client.get("/diagnostics")
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
