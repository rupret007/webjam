"""Unit tests for the Logic handoff adapter."""

import json
import tempfile
import uuid
import wave
from pathlib import Path

import pytest

from core.logic_handoff_adapter import (
    DEFAULT_BPM,
    DEFAULT_DENOMINATOR,
    DEFAULT_NUMERATOR,
    LogicHandoffAdapterError,
    LogicHandoffAdapterResult,
    build_handoff_session,
    can_export_to_logic,
)
from core.logic_handoff import LOGIC_SAMPLE_RATES


def _write_mono_wav(path: Path, sample_rate: int, frames: int) -> None:
    """Write a minimal mono WAV file."""
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, sample_rate, frames, "NONE", "not compressed"))
        handle.writeframes(b"\x00\x00" * frames)


def _write_stereo_wav(path: Path, sample_rate: int, frames: int) -> None:
    """Write a minimal stereo WAV file."""
    with wave.open(str(path), "wb") as handle:
        handle.setparams((2, 2, sample_rate, frames, "NONE", "not compressed"))
        handle.writeframes(b"\x00\x00\x00\x00" * frames)


def _create_minimal_take(
    folder: Path,
    *,
    sample_rate: int = 48000,
    frames: int = 48000,
    track_count: int = 2,
    tempo_bpm: float | None = None,
    numerator: int = 4,
    denominator: int = 4,
    markers: list[dict] | None = None,
) -> Path:
    """Create a minimal take project with WAV files and manifest."""
    folder.mkdir(parents=True, exist_ok=True)

    session_id = str(uuid.uuid4())
    take_id = str(uuid.uuid4())

    tracks = []
    for i in range(track_count):
        track_id = str(uuid.uuid4())
        source_id = str(uuid.uuid4())
        segment_id = str(uuid.uuid4())
        wav_name = f"track-{i + 1}.wav"
        wav_path = folder / wav_name

        if i % 2 == 0:
            _write_mono_wav(wav_path, sample_rate, frames)
            channels = 1
        else:
            _write_stereo_wav(wav_path, sample_rate, frames)
            channels = 2

        tracks.append({
            "track_id": track_id,
            "source_id": source_id,
            "logical_source_id": str(uuid.uuid4()),
            "participant_id": None,
            "name": f"Track {i + 1}",
            "instrument": "",
            "source_type": "jamulus_server",
            "quality": "network_track",
            "media_status": "available",
            "order": i,
            "segments": [{
                "segment_id": segment_id,
                "path": wav_name,
                "project_start_frame": 0,
                "frame_count": frames,
                "sample_rate": sample_rate,
                "channels": channels,
                "sample_format": "PCM_16",
                "gaps": [],
            }],
            "alignment": {"state": "not_applicable"},
            "selected_for_export": True,
        })

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
        "tempo_bpm": tempo_bpm if tempo_bpm is not None else DEFAULT_BPM,
        "time_signature": {
            "numerator": numerator,
            "denominator": denominator,
        },
        "participants": [],
        "devices": [],
        "tracks": tracks,
        "markers": markers or [],
        "errors": [],
        "warnings": [],
    }

    manifest_path = folder / "webjam-take.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return folder


class TestBuildHandoffSession:
    """Test build_handoff_session function."""

    def test_builds_session_from_valid_take(self, tmp_path: Path) -> None:
        """A valid take produces a HandoffSession."""
        take_path = _create_minimal_take(tmp_path / "take", frames=96000)
        result = build_handoff_session(take_path)

        assert isinstance(result, LogicHandoffAdapterResult)
        assert result.session.sample_rate == 48000
        assert result.session.frames == 96000
        assert len(result.session.stems) == 2

    def test_uses_project_sample_rate(self, tmp_path: Path) -> None:
        """The session uses the project's sample rate."""
        take_path = _create_minimal_take(tmp_path / "take", sample_rate=96000)
        result = build_handoff_session(take_path)

        assert result.session.sample_rate == 96000

    def test_all_logic_sample_rates_supported(self, tmp_path: Path) -> None:
        """All Logic-supported sample rates work."""
        for rate in LOGIC_SAMPLE_RATES:
            take_path = _create_minimal_take(tmp_path / f"take-{rate}", sample_rate=rate)
            result = build_handoff_session(take_path)
            assert result.session.sample_rate == rate

    def test_unsupported_sample_rate_raises(self, tmp_path: Path) -> None:
        """An unsupported sample rate raises an error."""
        take_path = _create_minimal_take(tmp_path / "take", sample_rate=22050)
        with pytest.raises(LogicHandoffAdapterError, match="22050 Hz is not supported"):
            build_handoff_session(take_path)


class TestBpmDefaultLabeling:
    """Test that default BPM is clearly labeled."""

    def test_default_bpm_is_labeled(self, tmp_path: Path) -> None:
        """When project uses default BPM, it is flagged."""
        take_path = _create_minimal_take(tmp_path / "take", tempo_bpm=DEFAULT_BPM)
        result = build_handoff_session(take_path)

        assert result.bpm_is_default is True
        assert result.session.bpm == DEFAULT_BPM

    def test_custom_bpm_is_not_labeled_as_default(self, tmp_path: Path) -> None:
        """When project uses custom BPM, it is not flagged as default."""
        take_path = _create_minimal_take(tmp_path / "take", tempo_bpm=140.0)
        result = build_handoff_session(take_path)

        assert result.bpm_is_default is False
        assert result.session.bpm == 140.0

    def test_slightly_different_bpm_is_not_default(self, tmp_path: Path) -> None:
        """A BPM close to but not equal to default is not flagged."""
        take_path = _create_minimal_take(tmp_path / "take", tempo_bpm=120.5)
        result = build_handoff_session(take_path)

        assert result.bpm_is_default is False


class TestTimeSignature:
    """Test time signature handling."""

    def test_default_time_signature_is_labeled(self, tmp_path: Path) -> None:
        """When project uses default time signature, it is flagged."""
        take_path = _create_minimal_take(
            tmp_path / "take",
            numerator=DEFAULT_NUMERATOR,
            denominator=DEFAULT_DENOMINATOR,
        )
        result = build_handoff_session(take_path)

        assert result.time_signature_is_default is True

    def test_custom_time_signature_is_not_default(self, tmp_path: Path) -> None:
        """When project uses custom time signature, it is not flagged."""
        take_path = _create_minimal_take(tmp_path / "take", numerator=3, denominator=4)
        result = build_handoff_session(take_path)

        assert result.time_signature_is_default is False
        assert result.session.numerator == 3
        assert result.session.denominator == 4


class TestMarkers:
    """Test marker handling."""

    def test_adds_start_marker_when_missing(self, tmp_path: Path) -> None:
        """A Start marker is always included."""
        take_path = _create_minimal_take(tmp_path / "take", markers=[])
        result = build_handoff_session(take_path)

        assert len(result.session.markers) >= 1
        assert any(m.name == "Start" and m.frame == 0 for m in result.session.markers)

    def test_preserves_existing_markers(self, tmp_path: Path) -> None:
        """Existing markers from the project are preserved."""
        markers = [
            {"marker_id": str(uuid.uuid4()), "position_s": 0.0, "label": "Start"},
            {"marker_id": str(uuid.uuid4()), "position_s": 2.0, "label": "Verse"},
        ]
        take_path = _create_minimal_take(tmp_path / "take", markers=markers)
        result = build_handoff_session(take_path)

        assert result.markers_added >= 2
        marker_names = {m.name for m in result.session.markers}
        assert "Start" in marker_names
        assert "Verse" in marker_names

    def test_marker_frame_alignment(self, tmp_path: Path) -> None:
        """Markers are converted to frames at the project sample rate."""
        markers = [
            {"marker_id": str(uuid.uuid4()), "position_s": 1.0, "label": "One Second"},
        ]
        take_path = _create_minimal_take(
            tmp_path / "take", sample_rate=48000, markers=markers, frames=96000
        )
        result = build_handoff_session(take_path)

        one_sec_markers = [m for m in result.session.markers if m.name == "One Second"]
        assert len(one_sec_markers) == 1
        assert one_sec_markers[0].frame == 48000  # 1 second at 48kHz


class TestNoMidiCase:
    """Test that MIDI tracks are empty when no note data exists."""

    def test_no_midi_tracks_when_no_note_data(self, tmp_path: Path) -> None:
        """WebJam does not capture MIDI, so midi_tracks should be empty."""
        take_path = _create_minimal_take(tmp_path / "take")
        result = build_handoff_session(take_path)

        assert result.session.midi_tracks == ()


class TestRefusalCases:
    """Test cases where export should be refused."""

    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        """A nonexistent path raises an error."""
        with pytest.raises(LogicHandoffAdapterError, match="does not exist"):
            build_handoff_session(tmp_path / "nonexistent")

    def test_empty_take_raises(self, tmp_path: Path) -> None:
        """A take with no tracks raises an error."""
        take_path = _create_minimal_take(tmp_path / "take", track_count=0)
        with pytest.raises(LogicHandoffAdapterError, match="no readable audio"):
            build_handoff_session(take_path)


class TestCanExportToLogic:
    """Test the can_export_to_logic function."""

    def test_valid_take_can_export(self, tmp_path: Path) -> None:
        """A valid take can be exported."""
        take_path = _create_minimal_take(tmp_path / "take")
        can_export, reason = can_export_to_logic(take_path)

        assert can_export is True
        assert reason == ""

    def test_nonexistent_path_cannot_export(self, tmp_path: Path) -> None:
        """A nonexistent path cannot be exported."""
        can_export, reason = can_export_to_logic(tmp_path / "nonexistent")

        assert can_export is False
        assert "does not exist" in reason

    def test_unsupported_sample_rate_cannot_export(self, tmp_path: Path) -> None:
        """An unsupported sample rate cannot be exported."""
        take_path = _create_minimal_take(tmp_path / "take", sample_rate=22050)
        can_export, reason = can_export_to_logic(take_path)

        assert can_export is False
        assert "22050" in reason


class TestStemAlignment:
    """Test that stems use exact start_frame alignment from the manifest."""

    def test_stems_use_project_start_frame(self, tmp_path: Path) -> None:
        """Stems should use the project_start_frame from segments."""
        take_path = _create_minimal_take(tmp_path / "take")
        result = build_handoff_session(take_path)

        for stem in result.session.stems:
            assert stem.start_frame == 0  # Our test creates all at frame 0

    def test_session_frames_covers_all_tracks(self, tmp_path: Path) -> None:
        """Session frames should be the maximum extent of all tracks."""
        take_path = _create_minimal_take(tmp_path / "take", frames=48000)
        result = build_handoff_session(take_path)

        assert result.session.frames == 48000
