"""Wiring test: the companion API is connected to the live ApplicationController.

Verifies the previously-orphaned LocalApiBridge is now instantiated by the
controller, fed real participant/diagnostics data, gated by the opt-in setting,
started by the app bootstrap when enabled, and stopped on shutdown.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402

try:
    from fastapi.testclient import TestClient
    _HAVE_TESTCLIENT = True
except Exception:
    _HAVE_TESTCLIENT = False


def _jp(cid, name, **kw):
    return SimpleNamespace(channel_id=cid, name=name, instrument=kw.get("instrument", "Guitar"),
                           is_local=kw.get("is_local", False), fader_level=kw.get("fader_level", 100),
                           muted=kw.get("muted", False), solo=kw.get("solo", False), is_connected=True)


class _ControllerFixture(unittest.TestCase):
    def _build(self, **settings_kw):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp.name
        self.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Wire",
        )
        self.controller = ApplicationController(self.window, settings=AppSettings(**settings_kw))
        return self.controller

    def tearDown(self):
        try:
            self.controller.shutdown()
        finally:
            if self._old_home is not None:
                os.environ["HOME"] = self._old_home
            else:
                os.environ.pop("HOME", None)
            self._tmp.cleanup()


class TestCompanionApiWiring(_ControllerFixture):
    def test_bridge_is_instantiated_with_configured_port(self):
        c = self._build(companion_api_port=8765)
        self.assertTrue(hasattr(c, "api_bridge"))
        self.assertEqual(c.api_bridge.port, 8765)

    def test_participants_callback_reflects_live_state(self):
        c = self._build()
        c._apply_jamulus_participants([_jp(0, "Me", is_local=True), _jp(1, "Alice", fader_level=80)])
        data = c._companion_get_participants()
        by_slot = {d["slot"]: d for d in data}
        self.assertEqual(set(by_slot), {1, 2})
        self.assertTrue(by_slot[1]["is_local"])
        self.assertEqual(by_slot[2]["fader_level"], 80)
        self.assertNotIn("name", str(data))
        self.assertNotIn("Alice", str(data))
        self.assertNotIn("channel_id", str(data))

    def test_diagnostics_callback_has_no_secret(self):
        c = self._build(
            sentry_dsn="TOPSECRET",
            jamulus_server="private.example",
        )
        diag = c._companion_get_diagnostics()
        self.assertNotIn("TOPSECRET", " ".join(map(str, diag.values())))
        self.assertIn("musician_guidance", diag)
        self.assertNotIn("jamulus_server", diag)
        self.assertNotIn("private.example", str(diag))

    def test_start_respects_disabled_setting(self):
        c = self._build(companion_api_enabled=False)
        with mock.patch.object(c.api_bridge, "start") as start:
            self.assertFalse(c.start_companion_api())
            start.assert_not_called()

    def test_start_when_enabled_invokes_bridge(self):
        c = self._build(companion_api_enabled=True)
        with mock.patch.object(c.api_bridge, "start", return_value=True) as start:
            self.assertTrue(c.start_companion_api())
            start.assert_called_once()

    def test_shutdown_stops_bridge(self):
        c = self._build()
        with mock.patch.object(c.api_bridge, "stop") as stop:
            c.shutdown()
            stop.assert_called_once()

    def test_settings_reconfigure_restarts_enabled_api_on_port_change(self):
        c = self._build(companion_api_enabled=True, companion_api_port=8765)
        old_settings = c.settings
        c.api_bridge._running = True
        c.settings = AppSettings(companion_api_enabled=True, companion_api_port=9876)
        with mock.patch.object(c.api_bridge, "stop") as stop, \
             mock.patch.object(c, "start_companion_api", return_value=True) as start:
            c._reconfigure_services_after_settings(old_settings)
        stop.assert_called_once()
        start.assert_called_once()
        self.assertEqual(c.api_bridge.port, 9876)

    def test_settings_reconfigure_stops_api_when_disabled(self):
        c = self._build(companion_api_enabled=True, companion_api_port=8765)
        old_settings = c.settings
        c.api_bridge._running = True
        c.settings = AppSettings(companion_api_enabled=False, companion_api_port=8765)
        with mock.patch.object(c.api_bridge, "stop") as stop, \
             mock.patch.object(c, "start_companion_api", return_value=True) as start:
            c._reconfigure_services_after_settings(old_settings)
        stop.assert_called_once()
        start.assert_not_called()

    def test_settings_reconfigure_replaces_rpc_client_on_port_change(self):
        c = self._build(jamulus_rpc_port=22222)
        old_settings = c.settings
        old_rpc = mock.MagicMock()
        c.jamulus.rpc_client = old_rpc
        c.settings = AppSettings(jamulus_rpc_port=23333)

        c._reconfigure_services_after_settings(old_settings)

        old_rpc.stop.assert_called_once()
        self.assertEqual(c.jamulus.rpc_client._port, 23333)

    @unittest.skipUnless(_HAVE_TESTCLIENT, "fastapi testclient unavailable")
    def test_end_to_end_http_serves_live_participants(self):
        """Build the real app from the controller's callbacks and query it."""
        c = self._build()
        c._apply_jamulus_participants([_jp(0, "Me", is_local=True), _jp(2, "Bob", muted=True)])
        from fastapi import FastAPI, HTTPException
        app = c.api_bridge._create_app(FastAPI, HTTPException)
        client = TestClient(app, base_url="http://127.0.0.1")
        resp = client.get("/participants")
        self.assertEqual(resp.status_code, 200)
        participants = resp.json()["participants"]
        self.assertEqual({p["slot"] for p in participants}, {1, 2})
        self.assertNotIn("Me", str(participants))
        self.assertNotIn("Bob", str(participants))
        diag = client.get("/diagnostics")
        self.assertEqual(diag.status_code, 200)
        self.assertIn("participant_count", diag.json()["diagnostics"])
        self.assertIn("musician_guidance", diag.json()["diagnostics"])


if __name__ == "__main__":
    unittest.main()
