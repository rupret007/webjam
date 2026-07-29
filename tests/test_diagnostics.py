"""Tests for ``DiagnosticsExporter`` — the Ctrl+Shift+D bug-report builder."""
from __future__ import annotations

import os
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import zipfile

from core.settings import AppSettings
from core.session_lifecycle import SessionLifecycle, SessionLifecyclePhase
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
        settings.musician_name = "Private Musician Name"
        settings.takes_directory = "/Users/private/Music/WebJam Takes"
        out = _make_exporter(settings=settings).build_summary()
        self.assertNotIn("super-sensitive-secret-token-xyz", out)
        self.assertNotIn("Private Musician Name", out)
        self.assertNotIn("WebJam Takes", out)
        self.assertIn("[redacted]", out)

    def test_preview_copy_and_bundle_share_one_cached_snapshot(self):
        exporter = _make_exporter(jamulus_state="Running")
        copied = exporter.build_summary()
        preview = exporter.build_preview()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = exporter.save_bundle(Path(temp_dir))
            with zipfile.ZipFile(output, "r") as archive:
                self.assertEqual(
                    archive.read("README.txt").decode("utf-8"), copied
                )
                self.assertEqual(copied, preview.copy_text)
                self.assertEqual(
                    json.loads(archive.read("support.json")), preview.report
                )
                self.assertEqual(
                    tuple(sorted(archive.namelist())), preview.archive_files
                )

    def test_build_summary_handles_missing_log_file(self):
        # Point HOME at a temp dir with no .webjam.log present.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}):
                # Build should not raise or disclose a source path merely to
                # explain that an optional log was unavailable.
                out = _make_exporter().build_summary()
        self.assertNotIn(tmp, out)
        self.assertIn("## Included sanitized logs\n- None", out)

    def test_structured_report_includes_available_recorder_and_cleanup_facts(self):
        settings = AppSettings(
            local_capture_enabled=True,
            jamulus_rpc_port=22222,
            server_rpc_port=22240,
            jamulus_port=22124,
        )
        bridge = SimpleNamespace(
            jamulus_state="Stopped",
            jamulus_reconnect_attempts=3,
            jamulus_process=None,
            hosted_server_process=None,
            _port_free=lambda _port, *, udp=False: True,
        )
        jamulus = SimpleNamespace(
            rpc_client=SimpleNamespace(
                available=False, last_activity_age=lambda: 1.0
            ),
            audio_engine=SimpleNamespace(
                diagnostics=lambda: SimpleNamespace(
                    backend="sounddevice",
                    active=False,
                    samplerate=48_000,
                )
            ),
        )
        take = SimpleNamespace(
            duration_s=12.5,
            tracks=(
                SimpleNamespace(samplerate=48_000, channels=1),
                SimpleNamespace(samplerate=48_000, channels=1),
            ),
        )
        recording = SimpleNamespace(
            phase=SimpleNamespace(value="complete"),
            snapshot=SimpleNamespace(armed=False, recording=False),
            last_validation=SimpleNamespace(take=take, errors=()),
        )
        metrics = SimpleNamespace(
            collect=lambda: {
                "metric_jamulus_reconnect_success": "2",
                "metric_jamulus_reconnect_failed": "1",
            }
        )
        report = DiagnosticsExporter(
            settings=settings,
            bridge=bridge,
            jamulus_controller=jamulus,
            window_version=__version__,
            build_id="a" * 40,
            recording_coordinator=recording,
            metrics_service=metrics,
        ).artifact().structured_report

        self.assertEqual(report["versions"]["build"], "a" * 40)
        self.assertEqual(report["session"]["reconnects"], {
            "attempts": 3,
            "failed": 1,
            "succeeded": 2,
        })
        self.assertEqual(report["audio"]["channels"]["recorded"], 2)
        self.assertEqual(report["recorder"]["state"], "complete")
        self.assertTrue(report["recorder"]["finalized"])
        self.assertTrue(report["recorder"]["reopened"])
        self.assertEqual(report["recorder"]["sample_rate_hz"], 48_000)
        self.assertTrue(
            all(item["released"] for item in report["cleanup"]["ports"])
        )

    def test_structured_report_includes_only_allowlisted_lifecycle_timeline(self):
        lifecycle = SessionLifecycle(role="host")
        lifecycle.transition(
            SessionLifecyclePhase.PREPARING,
            reason="invitation token=private-value",
        )
        lifecycle.transition(
            SessionLifecyclePhase.STARTING_HOST,
            reason="starting local host",
        )
        exporter = _make_exporter()
        exporter.session_lifecycle = lifecycle

        transitions = exporter.artifact().structured_report["session"]["transitions"]

        assert len(transitions) == 2
        assert transitions[-1]["to_state"] == "starting_host"
        assert set(transitions[-1]) == {
            "at", "component", "event", "from_state", "to_state", "status", "reason"
        }
        assert "private-value" not in str(transitions)

    def test_reference_track_diagnostics_exclude_source_identity(self):
        exporter = _make_exporter()
        exporter.reference_track = {
            "playback_state": "ready",
            "source_state": "loaded",
            "source_format": "WAV",
            "source_sample_rate_hz": 48_000,
            "source_channels": 2,
            "source_duration_s": 12.5,
            "route_available": False,
            "route_platform": "macos",
            "route_backend": "blackhole",
            "route_reason": "physical_certification_required",
            "route_active": False,
            "cleanup_pending": True,
            "source_name": "Private Rehearsal.wav",
            "source_path": "/Users/private/Private Rehearsal.wav",
        }

        report = exporter.artifact().structured_report
        self.assertEqual(
            report["reference_track"],
            {
                "playback_state": "ready",
                "cleanup_pending": True,
                "route_active": False,
                "route_available": False,
                "route_backend": "blackhole",
                "route_platform": "macos",
                "route_reason": "physical_certification_required",
                "source_channels": 2,
                "source_duration_s": 12.5,
                "source_format": "WAV",
                "source_sample_rate_hz": 48_000,
                "source_state": "loaded",
            },
        )
        encoded = json.dumps(report)
        self.assertNotIn("Private Rehearsal", encoded)
        self.assertNotIn("/Users/private", encoded)

    def test_structured_report_accepts_only_component_public_mappings(self):
        exporter = _make_exporter()
        exporter.jamulus_update = {
            "state": "available",
            "active_version": "3.12.2",
            "available_version": "3.12.3",
            "fallback_version": "3.12.2",
            "target": "windows-x64",
            "progress_percent": 25,
            "reason_code": "catalog-offline",
            "catalog_verified": False,
            "catalog_sequence": 0,
            "private_path": "/Users/alice/private",
        }
        exporter.webex_app = {
            "state": "not-installed",
            "installed": False,
            "publisher_verified": False,
            "reason_code": "publisher-check-deferred",
            "meeting_url": "https://private.webex.com/meet/secret",
        }

        report = exporter.artifact().structured_report

        self.assertEqual(report["jamulus_update"]["state"], "available")
        self.assertEqual(
            report["jamulus_update"]["fallback_version"],
            "3.12.2",
        )
        self.assertEqual(report["jamulus_update"]["catalog_sequence"], 0)
        self.assertFalse(report["jamulus_update"]["catalog_verified"])
        self.assertEqual(report["webex_app"]["state"], "not-installed")
        self.assertFalse(report["webex_app"]["installed"])
        self.assertNotIn("private", json.dumps(report).lower())
        self.assertNotIn("meeting_url", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
