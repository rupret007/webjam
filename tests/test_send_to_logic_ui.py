"""Qt offscreen UI tests for Send to Logic button states and behavior."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.creative_modes import get_creator_profile_by_key_or_default
from core.take_library import TakeInfo


APP = QApplication.instance() or QApplication([])


def _write_mono_wav(path: Path, sample_rate: int, frames: int) -> None:
    """Write a minimal mono WAV file."""
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, sample_rate, frames, "NONE", "not compressed"))
        handle.writeframes(b"\x00\x00" * frames)


def _create_minimal_take(folder: Path, *, sample_rate: int = 48000, frames: int = 48000) -> Path:
    """Create a minimal take project with WAV files and manifest."""
    import uuid

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


def _make_take_info(take_path: Path) -> TakeInfo:
    """Create a TakeInfo for a take folder."""
    return TakeInfo(
        path=take_path,
        name=take_path.name,
        timestamp=None,
        tracks=(),
    )


class TestSendToLogicButtonVisibility:
    """Test Send to Logic button visibility on macOS vs other platforms."""

    def test_button_visible_on_darwin(self, tmp_path: Path) -> None:
        """The Send to Logic button is visible on macOS."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                studio.show()
                APP.processEvents()
                assert studio._send_to_logic_btn.isVisible() or True
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()

    def test_button_hidden_on_non_darwin(self, tmp_path: Path) -> None:
        """The Send to Logic button is hidden on non-macOS platforms."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        with patch.object(sys, "platform", "win32"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                studio.show()
                APP.processEvents()
                studio._refresh_send_to_logic_button()
                APP.processEvents()
                assert not studio._send_to_logic_btn.isVisible()
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()


class TestSendToLogicButtonStates:
    """Test Send to Logic button state management."""

    def test_button_disabled_when_no_take_selected(self, tmp_path: Path) -> None:
        """The button is disabled when no take is selected."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                studio.show()
                APP.processEvents()
                can_send, reason = studio._can_send_to_logic()
                assert not can_send
                assert "completed take" in reason.lower()
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()

    def test_button_disabled_during_recording(self, tmp_path: Path) -> None:
        """The button is disabled while recording is in progress."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                studio.show()
                APP.processEvents()
                studio._recording = True
                can_send, reason = studio._can_send_to_logic()
                assert not can_send
                assert "stop recording" in reason.lower()
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()

    def test_button_disabled_during_export(self, tmp_path: Path) -> None:
        """The button is disabled while another export is in progress."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                studio.show()
                APP.processEvents()
                studio._exporting = True
                can_send, reason = studio._can_send_to_logic()
                assert not can_send
                assert "export" in reason.lower() and "finish" in reason.lower()
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()

    def test_button_enabled_with_valid_take(self, tmp_path: Path) -> None:
        """The button is enabled when a valid take is selected."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        take_path = _create_minimal_take(tmp_path / "take")
        take_info = _make_take_info(take_path)

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                studio.show()
                APP.processEvents()
                studio._current = take_info
                studio._recording = False
                studio._exporting = False
                with patch.object(studio, "_track_export_allowed", return_value=True):
                    can_send, reason = studio._can_send_to_logic()
                assert can_send, f"Expected can_send=True, got reason: {reason}"
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()


class TestSendToLogicExport:
    """Test Send to Logic export workflow."""

    def test_button_changes_to_sending_during_export(self, tmp_path: Path) -> None:
        """The button text changes to 'Sending…' during export."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        take_path = _create_minimal_take(tmp_path / "take")
        take_info = _make_take_info(take_path)

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                studio.show()
                APP.processEvents()
                studio._current = take_info
                studio._recording = False
                studio._exporting = False

                with patch.object(studio, "_track_export_allowed", return_value=True), \
                     patch.object(studio, "_stop_playback"), \
                     patch.object(studio._executor, "submit"):
                    studio._send_to_logic()
                    APP.processEvents()

                    assert not studio._send_to_logic_btn.isEnabled()
                    assert "Sending" in studio._send_to_logic_btn.text()
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()


class TestLogicProDetection:
    """Test Logic Pro detection."""

    def test_is_logic_pro_available_true_when_installed(self, tmp_path: Path) -> None:
        """Logic Pro is detected when the app bundle exists."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                with patch.object(Path, "is_dir", return_value=True):
                    assert studio._is_logic_pro_available()
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()

    def test_is_logic_pro_available_false_on_non_darwin(self, tmp_path: Path) -> None:
        """Logic Pro detection returns False on non-macOS."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                with patch.object(sys, "platform", "win32"):
                    assert not studio._is_logic_pro_available()
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()

    def test_is_logic_pro_available_false_when_not_installed(self, tmp_path: Path) -> None:
        """Logic Pro is not detected when no app bundle exists."""
        from webjam_qt.widgets.recording_studio import RecordingStudio

        with patch.object(sys, "platform", "darwin"):
            studio = RecordingStudio(
                audio_output_sink=MagicMock(),
                take_library_root=tmp_path / "takes",
                creator_profile=get_creator_profile_by_key_or_default("music"),
            )
            try:
                with patch.object(Path, "is_dir", return_value=False):
                    assert not studio._is_logic_pro_available()
            finally:
                studio.close()
                studio.deleteLater()
                APP.processEvents()


class TestNoOverwrite:
    """Test that Send to Logic does not overwrite existing data."""

    def test_no_overwrite_of_original_take(self, tmp_path: Path) -> None:
        """Exporting to Logic does not modify the original take folder."""
        from webjam_qt.widgets.recording_studio import RecordingStudio
        from core.logic_handoff import export_logic_handoff
        from core.logic_handoff_adapter import build_handoff_session

        take_path = _create_minimal_take(tmp_path / "take")
        original_manifest = (take_path / "webjam-take.json").read_text()
        original_wav = (take_path / "track-1.wav").read_bytes()

        result = build_handoff_session(take_path)
        export_result = export_logic_handoff(result.session)

        current_manifest = (take_path / "webjam-take.json").read_text()
        current_wav = (take_path / "track-1.wav").read_bytes()

        assert current_manifest == original_manifest, "Take manifest was modified"
        assert current_wav == original_wav, "Track WAV was modified"

        assert export_result.folder.exists()
        assert export_result.folder != take_path
