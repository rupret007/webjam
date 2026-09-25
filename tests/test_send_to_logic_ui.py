"""Tests for Send to Logic / Export for DAW functionality.

These tests focus on the adapter and export logic. Qt widget integration tests
would require proper fixture setup and are deferred to the existing integration
test infrastructure.
"""
from __future__ import annotations

import json
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.logic_handoff import export_logic_handoff
from core.logic_handoff_adapter import (
    build_handoff_session,
    can_export_to_logic,
)


def _write_mono_wav(path: Path, sample_rate: int, frames: int) -> None:
    """Write a minimal mono WAV file."""
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, sample_rate, frames, "NONE", "not compressed"))
        handle.writeframes(b"\x00\x00" * frames)


def _create_minimal_take(folder: Path, *, sample_rate: int = 48000, frames: int = 48000) -> Path:
    """Create a minimal take project with WAV files and manifest."""
    folder.mkdir(parents=True, exist_ok=True)

    session_id = str(uuid.uuid4())
    take_id = str(uuid.uuid4())
    track_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    segment_id = str(uuid.uuid4())

    wav_path = folder / "track-1.wav"
    _write_mono_wav(wav_path, sample_rate, frames)

    manifest = {
        "schema_version": 2,
        "revision": 1,
        "app_version": "0.28.3",
        "session_id": session_id,
        "take_id": take_id,
        "session_title": "Test Session",
        "take_name": "Test Take",
        "created_utc": "2026-09-24T12:00:00Z",
        "status": "complete",
        "project_sample_rate": sample_rate,
        "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "participants": [],
        "devices": [],
        "tracks": [{
            "track_id": track_id,
            "source_id": source_id,
            "logical_source_id": str(uuid.uuid4()),
            "participant_id": None,
            "name": "Track 1",
            "instrument": "",
            "source_type": "jamulus_server",
            "quality": "network_track",
            "media_status": "available",
            "order": 0,
            "segments": [{
                "segment_id": segment_id,
                "path": "track-1.wav",
                "project_start_frame": 0,
                "frame_count": frames,
                "sample_rate": sample_rate,
                "channels": 1,
                "sample_format": "PCM_16",
                "gaps": [],
            }],
            "alignment": {"state": "not_applicable"},
            "selected_for_export": True,
        }],
        "markers": [],
        "errors": [],
        "warnings": [],
    }

    manifest_path = folder / "webjam-take.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return folder


class TestExportForDAWIntegration:
    """Integration tests for Export for DAW functionality."""

    def test_full_export_creates_stems_and_midi(self, tmp_path: Path) -> None:
        """A full export produces stems and MIDI file."""
        take_path = _create_minimal_take(tmp_path / "take")
        result = build_handoff_session(take_path)
        export_result = export_logic_handoff(result.session)

        assert export_result.folder.exists()
        assert export_result.midi.exists()
        assert len(export_result.stems) == 1
        for stem in export_result.stems:
            assert stem.exists()

    def test_export_does_not_modify_original_take(self, tmp_path: Path) -> None:
        """Exporting does not modify the original take folder."""
        take_path = _create_minimal_take(tmp_path / "take")
        original_manifest = (take_path / "webjam-take.json").read_text()
        original_wav = (take_path / "track-1.wav").read_bytes()

        result = build_handoff_session(take_path)
        export_logic_handoff(result.session)

        current_manifest = (take_path / "webjam-take.json").read_text()
        current_wav = (take_path / "track-1.wav").read_bytes()

        assert current_manifest == original_manifest, "Take manifest was modified"
        assert current_wav == original_wav, "Track WAV was modified"

    def test_export_creates_separate_folder(self, tmp_path: Path) -> None:
        """Export creates a new folder separate from the take."""
        take_path = _create_minimal_take(tmp_path / "take")
        result = build_handoff_session(take_path)
        export_result = export_logic_handoff(result.session)

        assert export_result.folder != take_path
        assert not str(export_result.folder).startswith(str(take_path))

    def test_can_export_validation(self, tmp_path: Path) -> None:
        """can_export_to_logic properly validates takes."""
        take_path = _create_minimal_take(tmp_path / "take")

        can_export, reason = can_export_to_logic(take_path)
        assert can_export is True
        assert reason == ""

        can_export, reason = can_export_to_logic(tmp_path / "nonexistent")
        assert can_export is False
        assert "does not exist" in reason


class TestCrossPlatformExportLabel:
    """Test that the export functionality works on all platforms.

    Note: These test the underlying logic, not the Qt widget label which
    varies by platform. The actual button text ("Send to Logic" on macOS,
    "Export for DAW" elsewhere) is set in RecordingStudio.__init__.
    """

    def test_export_works_regardless_of_platform(self, tmp_path: Path) -> None:
        """The core export logic works on any platform."""
        take_path = _create_minimal_take(tmp_path / "take")
        result = build_handoff_session(take_path)
        export_result = export_logic_handoff(result.session)

        assert export_result.folder.exists()
        assert (export_result.folder / "README.md").exists()
        assert (export_result.folder / "logic-import-map.csv").exists()

    def test_export_produces_daw_compatible_files(self, tmp_path: Path) -> None:
        """Export produces files compatible with any DAW."""
        take_path = _create_minimal_take(tmp_path / "take")
        result = build_handoff_session(take_path)
        export_result = export_logic_handoff(result.session)

        assert export_result.midi.suffix == ".mid"
        for stem in export_result.stems:
            assert stem.suffix == ".wav"


class TestOpenInLogicLauncher:
    """Test Logic launcher behavior used by the Send to Logic flow."""

    def test_open_in_logic_tries_bundle_then_app_name(self, monkeypatch, tmp_path: Path) -> None:
        import webjam_qt.widgets.recording_studio as studio_widget

        calls: list[list[str]] = []

        class _Result:
            def __init__(self, returncode: int) -> None:
                self.returncode = returncode

        def fake_run(command, **_kwargs):
            calls.append(command)
            if len(calls) == 1:
                return _Result(1)
            return _Result(0)

        monkeypatch.setattr(studio_widget.sys, "platform", "darwin")
        monkeypatch.setattr(studio_widget.subprocess, "run", fake_run)

        midi = tmp_path / "session.mid"
        opened = studio_widget.RecordingStudio._open_in_logic(object(), midi)

        assert opened is True
        assert calls == [
            ["open", "-b", "com.apple.logic10", str(midi)],
            ["open", "-a", "Logic Pro", str(midi)],
        ]

    def test_open_in_logic_returns_false_off_macos(self, monkeypatch, tmp_path: Path) -> None:
        import webjam_qt.widgets.recording_studio as studio_widget

        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            pytest.fail("subprocess.run should not be called off macOS")

        monkeypatch.setattr(studio_widget.sys, "platform", "linux")
        monkeypatch.setattr(studio_widget.subprocess, "run", fake_run)

        opened = studio_widget.RecordingStudio._open_in_logic(
            object(), tmp_path / "session.mid"
        )

        assert opened is False
        assert calls == []


class TestLogicHandoffStatusHints:
    """Ensure non-macOS messaging stays DAW-honest and fail-closed."""

    def test_finish_logic_handoff_non_macos_uses_daw_wording(self, monkeypatch, tmp_path: Path) -> None:
        import webjam_qt.widgets.recording_studio as studio_widget

        monkeypatch.setattr(studio_widget.sys, "platform", "linux")
        revealed: list[Path] = []
        hint_messages: list[str] = []
        dummy = SimpleNamespace()
        dummy._restore_logic_handoff_controls = lambda: None
        dummy._is_logic_pro_available = lambda: False
        dummy._reveal_folder = lambda folder: revealed.append(folder)
        dummy._hint = SimpleNamespace(setText=lambda text: hint_messages.append(text))
        dummy._reveal_path = None
        result = SimpleNamespace(
            folder=tmp_path / "handoff",
            stems=(tmp_path / "01-lead.wav",),
            sample_rate=48000,
            midi=tmp_path / "session.mid",
        )

        studio_widget.RecordingStudio._finish_logic_handoff(dummy, result, None, False)

        assert revealed == [result.folder]
        assert dummy._reveal_path == result.folder
        assert hint_messages
        assert "DAW handoff ready" in hint_messages[-1]
        assert "in your DAW" in hint_messages[-1]
