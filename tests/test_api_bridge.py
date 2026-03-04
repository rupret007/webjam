from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient
    TESTCLIENT_AVAILABLE = True
except Exception:
    TESTCLIENT_AVAILABLE = False

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


@unittest.skipUnless(TESTCLIENT_AVAILABLE, "fastapi.testclient not available")
class TestLocalApiBridgeEndpoints(unittest.TestCase):
    def setUp(self):
        self.bridge = LocalApiBridge(
            get_participants=lambda: [{"name": "Alice", "channel": 0}],
            get_diagnostics=lambda: {"status": "ok", "backend": "test"},
        )
        from fastapi import FastAPI, HTTPException
        self.app = self.bridge._create_app(FastAPI, HTTPException)
        self.client = TestClient(self.app)

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

    def test_participants_callback_error_returns_500(self):
        def failing_cb():
            raise RuntimeError("boom")
        bridge = LocalApiBridge(get_participants=failing_cb, get_diagnostics=lambda: {})
        from fastapi import FastAPI, HTTPException
        app = bridge._create_app(FastAPI, HTTPException)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/participants")
        self.assertEqual(resp.status_code, 500)

    def test_diagnostics_callback_error_returns_500(self):
        def failing_cb():
            raise RuntimeError("boom")
        bridge = LocalApiBridge(get_participants=lambda: [], get_diagnostics=failing_cb)
        from fastapi import FastAPI, HTTPException
        app = bridge._create_app(FastAPI, HTTPException)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/diagnostics")
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
