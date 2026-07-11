"""Tests for ``DiagnosticsExporter`` — the Ctrl+Shift+D bug-report builder."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.settings import AppSettings
from webjam_qt import __version__
from webjam_qt.controllers.diagnostics import DiagnosticsExporter


def _make_exporter(
    *,
    settings: AppSettings | None = None,
    jamulus_state: str = "Not launched",
    webex_state: str = "Not opened",
    rpc_available: bool = False,
    participants: list | None = None,
) -> DiagnosticsExporter:
    settings = settings or AppSettings()
    bridge = MagicMock()
    bridge.jamulus_state = jamulus_state
    bridge.webex_state = webex_state
    bridge.find_jamulus.return_value = "/fake/path/Jamulus"

    rpc_client = SimpleNamespace(available=rpc_available, last_activity_age=lambda: 0.5)
    audio_engine = MagicMock()
    audio_engine.diagnostics.return_value = SimpleNamespace(
        backend="sounddevice", latency_mode="low", active=True
    )
    jamulus = MagicMock()
    jamulus.rpc_client = rpc_client
    jamulus.audio_engine = audio_engine
    jamulus.get_participants.return_value = participants or []

    return DiagnosticsExporter(
        settings=settings,
        bridge=bridge,
        jamulus_controller=jamulus,
        window_version=__version__,
    )


class TestDiagnosticsExporter(unittest.TestCase):
    def test_build_summary_includes_version(self):
        out = _make_exporter().build_summary()
        self.assertIn(__version__, out)
        self.assertIn("# WebJam Diagnostics", out)

    def test_build_summary_includes_jamulus_state(self):
        out = _make_exporter(jamulus_state="Running").build_summary()
        self.assertIn("Running", out)
        self.assertIn("Jamulus state", out)

    def test_build_summary_redacts_secret(self):
        settings = AppSettings()
        settings.sentry_dsn = "super-sensitive-secret-token-xyz"
        out = _make_exporter(settings=settings).build_summary()
        self.assertNotIn("super-sensitive-secret-token-xyz", out)
        self.assertIn("[redacted]", out)

    def test_build_summary_handles_missing_log_file(self):
        # Point HOME at a temp dir with no .webjam.log present.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}):
                # Build should not raise and should mention the log path.
                out = _make_exporter().build_summary()
        self.assertIn(".webjam.log", out)
        self.assertIn("(log file unavailable)", out)


if __name__ == "__main__":
    unittest.main()
