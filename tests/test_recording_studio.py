from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
import threading
import time
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QWidget,
)

from core.creative_modes import get_creator_profile_by_key_or_default
from core.network_invite import local_band_address
from core.recording_sources import (
    RecordingSourceKind,
    RecordingSourcePresentation,
    RecordingSourceState,
)
from core.settings import AppSettings
from core.studio_project import (
    MarkerKind,
    StudioMarker,
    StudioProjectError,
    default_studio_document,
)
from core.studio_sections import StudioSectionError
from core.studio_source_catalog import StudioSourceCatalogError
from core.studio_state import load_studio_state
from core.studio_store import StudioStoreError
from core.take_library import TakeInfo, TrackInfo
from core.take_player import PlaybackDeviceError, StudioPlaybackSourceError, TakePlayer
from core.take_project import (
    AlignmentState,
    GapInterval,
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SessionEvidence,
    SourceQuality,
    SourceType,
    TakeProject,
    load_take_project,
    new_project_id,
    write_take_project,
)
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.recording_studio import (
    RecordingStudio,
    _composite_waveform_peaks,
    _CompositeWaveformSpec,
    _studio_document_differs_from_default,
    _studio_export_failure_message,
    _track_export_failure_message,
    _waveform_peaks,
    _waveform_source_key,
    _WaveformPeakCache,
    _WaveformSegmentSpec,
)
from webjam_qt.widgets.studio_arrange import _format_frame_time
from webjam_qt.widgets.studio_messages import (
    ARRANGE_EDIT_FALLBACK,
    MAX_DETAIL_CHARACTERS,
    arrange_edit_failure_message,
    safe_detail,
)
from webjam_qt.widgets.studio_review import (
    _is_synchronized_source,
    _safe_source_description,
    _safe_source_label,
)
from webjam_qt.widgets.studio_waveforms import StudioWaveformCoordinatorError
from webjam_qt.windows.recording_setup import (
    LocalOriginalsChoiceDialog,
    RecordingSetupDialog,
)
from webjam_qt.windows.simple_settings import SimpleSettingsDialog

APP = QApplication.instance() or QApplication([])
RATE = 8000


def _visible_enabled_focus_chain(
    window: QWidget,
    start: QWidget,
) -> list[QWidget]:
    """Return one complete keyboard-focus cycle, excluding skipped controls."""

    result: list[QWidget] = []
    visited: set[QWidget] = set()
    current = start
    while current not in visited:
        visited.add(current)
        if (
            current.focusPolicy() != Qt.FocusPolicy.NoFocus
            and current.isVisibleTo(window)
            and current.isEnabled()
        ):
            result.append(current)
        current = current.nextInFocusChain()
    return result


def _widget_rect_in(window: QWidget, widget: QWidget):
    """Return one visible widget rectangle in window coordinates."""

    return widget.rect().translated(widget.mapTo(window, QPoint(0, 0)))


class _SilentSink:
    def start(self, samplerate, blocksize, pull):
        self.pull = pull

    def stop(self):
        pass


class _InspectableSink:
    def __init__(self):
        self.pull = None
        self.starts = 0
        self.stops = 0

    def start(self, _samplerate, _blocksize, pull):
        self.pull = pull
        self.starts += 1

    def stop(self):
        self.stops += 1


def _wav(path: Path, frequency: float = 220.0) -> None:
    frames = np.sin(np.arange(RATE) * frequency * 2 * np.pi / RATE) * 0.35
    samples = np.int16(frames * 32767)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples.tolist()))


def _impulse_wav(
    path: Path,
    *,
    frames: int,
    rate: int,
    impulses: dict[int, float],
) -> None:
    """Write a bounded-memory mono fixture with impulses at exact frames."""
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        for start in range(0, frames, 65_536):
            count = min(65_536, frames - start)
            block = np.zeros(count, dtype="int16")
            for frame, amplitude in impulses.items():
                if start <= frame < start + count:
                    block[frame - start] = int(max(-1.0, min(1.0, amplitude)) * 32767)
            audio.writeframes(block.tobytes())


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    APP.processEvents()
    return bool(predicate())


def _wait_without_qt_events(predicate, timeout: float = 3.0) -> bool:
    """Wait for worker-only state without allowing Qt timers to reschedule work."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _mark_verified(take: Path, *filenames: str) -> None:
    (take / "webjam-take.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "complete",
                "tracks": [
                    {
                        "filename": name,
                        "source": "jamulus_server",
                        "offset_s": None,
                    }
                    for name in filenames
                ],
            }
        ),
        encoding="utf-8",
    )


def _schema2_studio_take(
    tmp_path: Path,
    *,
    server_source_type: SourceType = SourceType.JAMULUS_SERVER,
    server_name: str = "Band Drums",
    creator_profile_key: str = "music",
    warnings: tuple[str, ...] = (),
    server_logical_source_id: str = "",
) -> tuple[Path, tuple[str, str]]:
    """Create a small genuine v2 take so Studio exercises its sidecar boundary."""
    take_dir = tmp_path / "Studio Project Take"
    media = take_dir / "media"
    media.mkdir(parents=True)
    server = media / "server.wav"
    local = media / "local.wav"
    _wav(server, 220)
    _wav(local, 440)
    server_id, local_id = new_project_id(), new_project_id()

    def segment(path: Path, *, gap: bool = False) -> MediaSegment:
        return MediaSegment(
            segment_id=new_project_id(),
            path=path.relative_to(take_dir).as_posix(),
            project_start_frame=0,
            frame_count=RATE,
            sample_rate=RATE,
            channels=1,
            sample_format="PCM_16",
            media_status=MediaStatus.AVAILABLE,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size_bytes=path.stat().st_size,
            gaps=((GapInterval(1_000, 400, "test capture gap"),) if gap else ()),
        )

    project = TakeProject(
        session_id=new_project_id(),
        take_id=new_project_id(),
        session_title="Studio review",
        take_name="Studio Project Take",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=RATE,
        participants=(),
        tracks=(
            ProjectTrack(
                track_id=server_id,
                source_id=new_project_id(),
                participant_id=None,
                name=server_name,
                instrument=(
                    "Reference"
                    if server_source_type is SourceType.LIVE_REFERENCE
                    else "Drums"
                ),
                source_type=server_source_type,
                quality=(
                    SourceQuality.REFERENCE
                    if server_source_type is SourceType.LIVE_REFERENCE
                    else SourceQuality.NETWORK_TRACK
                ),
                media_status=MediaStatus.AVAILABLE,
                order=0,
                segments=(segment(server),),
                alignment=AlignmentState(confidence=1.0, method="server-origin"),
                logical_source_id=server_logical_source_id,
            ),
            ProjectTrack(
                track_id=local_id,
                source_id=new_project_id(),
                participant_id=None,
                name="Local Guitar",
                instrument="Guitar",
                source_type=SourceType.LOCAL_ISOLATED,
                quality=SourceQuality.VERIFIED_ISOLATED,
                media_status=MediaStatus.AVAILABLE,
                order=1,
                segments=(segment(local, gap=True),),
                alignment=AlignmentState(confidence=0.92, method="test-alignment"),
            ),
        ),
        session_evidence=SessionEvidence(
            creator_profile_key=creator_profile_key,
        ),
        warnings=warnings,
    )
    write_take_project(take_dir, project)
    return take_dir, (server_id, local_id)


def _schema2_repeated_take(
    tmp_path: Path,
    primary_dir: Path,
) -> tuple[Path, str]:
    primary = TakeProject.from_dict(
        json.loads((primary_dir / "webjam-take.json").read_text(encoding="utf-8"))
    )
    take_dir = tmp_path / "Studio Project Take 2"
    media = take_dir / "media"
    media.mkdir(parents=True)
    source = media / "server.wav"
    _wav(source, 330)
    track_id = new_project_id()
    segment = MediaSegment(
        segment_id=new_project_id(),
        path=source.relative_to(take_dir).as_posix(),
        project_start_frame=0,
        frame_count=RATE,
        sample_rate=RATE,
        channels=1,
        sample_format="PCM_16",
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        size_bytes=source.stat().st_size,
        has_signal=True,
    )
    project = TakeProject(
        session_id=primary.session_id,
        take_id=new_project_id(),
        session_title=primary.session_title,
        take_name="Studio Project Take 2",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=primary.project_sample_rate,
        participants=(),
        tracks=(
            ProjectTrack(
                track_id=track_id,
                source_id=new_project_id(),
                participant_id=None,
                name="Band Drums",
                instrument="Drums",
                source_type=SourceType.JAMULUS_SERVER,
                quality=SourceQuality.NETWORK_TRACK,
                media_status=MediaStatus.AVAILABLE,
                order=0,
                segments=(segment,),
                alignment=AlignmentState(confidence=1.0, method="server-origin"),
                logical_source_id=primary.tracks[0].logical_source_id,
            ),
        ),
        session_evidence=SessionEvidence(
            creator_profile_key=primary.session_evidence.creator_profile_key,
        ),
    )
    write_take_project(take_dir, project)
    return take_dir, track_id


def _open_schema2_waveform_studio(
    tmp_path: Path,
) -> tuple[RecordingStudio, frozenset[str]]:
    """Open a v2 arrangement with automatic UI drains held for assertions."""

    _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    studio._timer.stop()
    studio.resize(1440, 900)
    studio.show()
    APP.processEvents()
    studio._take_list.setCurrentRow(0)
    studio._studio_waveform_schedule_timer.stop()
    assert studio._studio_state is not None
    region_ids = frozenset(
        region.region_id
        for region in studio._studio_state.regions
        if region.enabled and not region.deleted
    )
    assert region_ids
    return studio, region_ids


@pytest.mark.parametrize(
    (
        "take_profile_key",
        "live_profile_key",
        "review_accessible_name",
        "review_summary",
        "review_participant",
        "restored_live_key",
    ),
    (
        (
            "podcast_voice",
            "music",
            "Podcast & Voice Multitrack Studio",
            "synchronized voice tracks",
            "speaker",
            "music",
        ),
        (
            "music",
            "podcast_voice",
            "Music Multitrack Studio",
            "synchronized tracks",
            "musician",
            "podcast_voice",
        ),
        (
            "",
            "podcast_voice",
            "Multitrack Take Review",
            "recorded tracks",
            "participant",
            "podcast_voice",
        ),
    ),
)
def test_historical_take_profile_owns_review_copy_without_mutating_live_profile(
    tmp_path,
    take_profile_key,
    live_profile_key,
    review_accessible_name,
    review_summary,
    review_participant,
    restored_live_key,
):
    take_dir = tmp_path / "Historical Take"
    take_dir.mkdir()
    audio = take_dir / "voice.wav"
    _wav(audio)
    take = TakeInfo(
        path=take_dir,
        name="Historical Take",
        tracks=[TrackInfo(audio, "Voice", duration_s=1.0, samplerate=RATE)],
        creator_profile_key=take_profile_key,
    )
    with patch(
        "webjam_qt.widgets.recording_studio.discover_takes",
        return_value=[take],
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio.set_creator_profile(
                get_creator_profile_by_key_or_default(live_profile_key)
            )
            studio._take_list.setCurrentRow(0)

            assert studio.accessibleName() == review_accessible_name
            assert review_summary in studio._subtitle.text()
            assert studio._participant_singular == review_participant
            assert studio._live_creator_profile.key == live_profile_key

            studio._show_live_session()

            assert studio._creator_profile_key == restored_live_key
            assert studio._live_creator_profile.key == live_profile_key
            if restored_live_key == "music":
                assert studio._participant_singular == "musician"
            else:
                assert studio._participant_singular == "speaker"
        finally:
            studio.shutdown()


def test_review_preview_live_session_can_record_webjam_audio() -> None:
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    requested: list[bool] = []
    try:
        studio.set_creator_profile(
            get_creator_profile_by_key_or_default("review_rehearsal")
        )
        studio.record_requested.connect(lambda: requested.append(True))
        studio.set_can_record(True)
        studio.set_live_participants(
            [SimpleNamespace(channel_id=4, name="Reviewer", is_local=True)]
        )

        assert studio._record_btn.isEnabled()
        assert "Record Session" in studio._record_btn.accessibleName()
        assert "never directly or automatically taps a meeting app" in (
            studio._record_btn.accessibleDescription()
        )
        assert "Record Session captures Jamulus server stems" in (
            studio._subtitle.text()
        )
        assert "Do not route meeting or system audio into those inputs" in (
            studio._subtitle.text()
        )
        assert studio._lanes[4]._source_badge.text() == "WEBJAM AUDIO"
        assert studio._inspector_values["source"].text() == (
            "Live participant WebJam-audio input"
        )

        studio._record_btn.click()
        assert requested == [True]
    finally:
        studio.shutdown()


@pytest.mark.parametrize(
    ("profile_key", "expected"),
    (
        (
            "music",
            "No completed take was found. Run Band Check, then record a short test take.",
        ),
        (
            "podcast_voice",
            "No completed recording was found. Run Sound Check, then record a short test take.",
        ),
        (
            "review_rehearsal",
            "No completed Review Preview take was found. Run Session Check, then record a short WebJam-audio test take.",
        ),
    ),
)
def test_missing_completed_take_uses_creator_check_copy(
    profile_key,
    expected,
) -> None:
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.set_creator_profile(
            get_creator_profile_by_key_or_default(profile_key)
        )
        studio.on_take_completed(
            None,
            SimpleNamespace(errors=("missing",), warnings=()),
        )
        assert studio._hint.text() == expected
    finally:
        studio.shutdown()


def test_review_preview_take_is_playback_only_without_sidecar_or_export(
    tmp_path,
) -> None:
    take_dir, _track_ids = _schema2_studio_take(
        tmp_path,
        creator_profile_key="review_rehearsal",
        warnings=("Inspect this source",),
    )
    sidecar = take_dir / ".webjam-studio-state.json"
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.resize(1440, 900)
        studio.show()
        studio._on_take_selected(0)
        APP.processEvents()

        assert studio.accessibleName() == (
            "Review and Rehearsal Preview Take Review"
        )
        assert studio._eyebrow.text() == "TAKE REVIEW · PREVIEW"
        assert "read-only playback" in studio._subtitle.text()
        assert "Playback remains read-only" in studio._hint.text()
        assert "before export" not in studio._hint.text()
        assert studio._studio_state is None
        assert studio._studio_controller.take_path is None
        assert not sidecar.exists()
        assert studio._play_btn.isEnabled()
        assert studio._scrub.isEnabled()
        assert studio._reveal_btn.isEnabled()
        assert studio._export_btn.isHidden()
        assert not studio._export_btn.isEnabled()
        assert studio._studio_arrange.isHidden()
        assert not studio._studio_arrange.isEnabled()
        assert studio._arrange_toolbar.isHidden()
        assert studio._comp_toolbar.isHidden()

        lane = studio._lanes[0]
        assert lane._source_badge.text() == "WEBJAM AUDIO"
        assert lane._source_badge.accessibleName() == "WEBJAM AUDIO source"
        for control in (
            lane._track_export_include,
            lane._mute,
            lane._solo,
            lane._trim,
            lane._gain,
            lane._pan,
        ):
            assert control.isHidden()
        assert "Playback and source review only" in lane.accessibleDescription()

        # At the supported compact Studio floor the complete capability
        # boundary stays visibly readable instead of becoming a clipped
        # one-line status. The track list remains a named, reachable scroll
        # surface below it.
        studio.resize(760, 600)
        APP.processEvents()
        APP.processEvents()
        assert studio._hint.wordWrap()
        required = studio._hint.fontMetrics().boundingRect(
            QRect(0, 0, studio._hint.contentsRect().width(), 10_000),
            Qt.TextFlag.TextWordWrap,
            studio._hint.text(),
        )
        assert required.height() <= studio._hint.contentsRect().height()
        assert studio._track_scroll.height() >= 88
        assert studio._track_scroll.accessibleName() == "Recorded track lanes"
        assert not _widget_rect_in(studio, studio._hint).intersects(
            _widget_rect_in(studio, studio._track_scroll)
        )

        studio._update_studio_state(0, gain=0.25)
        assert not studio._perform_arrange_edit(
            "Synthetic edit",
            lambda document: document,
            reload_audio=False,
        )
        with patch(
            "webjam_qt.widgets.recording_studio.export_track_package"
        ) as export:
            studio._export_tracks()
        export.assert_not_called()
        assert not sidecar.exists()
        assert "Track export is unavailable" in studio._hint.text()

        studio._select_track(0)
        assert studio._inspector_values["source"].text() == (
            "Participant (WebJam server track)"
        )
        assert studio._inspector_values["export"].text() == (
            "Unavailable in playback-only Review Preview"
        )
    finally:
        studio.shutdown()


def test_compact_take_title_elides_without_losing_full_accessible_name(
    tmp_path,
) -> None:
    take_dir = tmp_path / "Long Podcast Take"
    take_dir.mkdir()
    audio = take_dir / "voice.wav"
    _wav(audio)
    full_title = (
        "Episode 12 Remote Recording With Three Guests and Extended Director Notes"
    )
    take = TakeInfo(
        path=take_dir,
        name="Long Podcast Take",
        tracks=[TrackInfo(audio, "Voice", duration_s=1.0, samplerate=RATE)],
        session_title=full_title,
        creator_profile_key="podcast_voice",
    )
    with patch(
        "webjam_qt.widgets.recording_studio.discover_takes",
        return_value=[take],
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio.resize(760, 600)
            studio.show()
            studio._take_list.setCurrentRow(0)
            APP.processEvents()
            APP.processEvents()

            assert studio._title.text() != full_title
            assert studio._title.text().endswith("…")
            assert studio._title.fontMetrics().horizontalAdvance(
                studio._title.text()
            ) <= studio._title.contentsRect().width()
            assert studio._title.accessibleName() == full_title
            assert studio._title.accessibleDescription() == (
                f"Studio title: {full_title}"
            )
            assert studio._title.toolTip() == full_title

            studio.resize(1440, 900)
            APP.processEvents()
            APP.processEvents()
            assert studio._title.text() == full_title
            assert studio._title.toolTip() == ""
        finally:
            studio.shutdown()


def test_editable_music_take_restores_arrange_after_live_view(tmp_path) -> None:
    _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.resize(1440, 900)
        studio.show()
        studio._take_list.setCurrentRow(0)
        APP.processEvents()

        assert studio._studio_state is not None
        assert not studio._studio_arrange.isHidden()
        assert studio._studio_arrange.isEnabled()
        assert not studio._arrange_toolbar.isHidden()

        studio._show_live_session()
        assert studio._studio_arrange.isHidden()
        assert not studio._studio_arrange.isEnabled()
        assert studio._arrange_toolbar.isHidden()

        studio._on_take_selected(0)
        APP.processEvents()
        assert not studio._studio_arrange.isHidden()
        assert studio._studio_arrange.isEnabled()
        assert not studio._arrange_toolbar.isHidden()
    finally:
        studio.shutdown()


@pytest.mark.parametrize(
    ("profile_key", "badge", "source_description"),
    (
        ("music", "MUSICIAN", "Musician (band server track)"),
        ("podcast_voice", "SPEAKER", "Speaker (WebJam server track)"),
        (
            "review_rehearsal",
            "WEBJAM AUDIO",
            "Participant (WebJam server track)",
        ),
    ),
)
def test_recorded_server_source_copy_follows_take_profile(
    tmp_path,
    profile_key,
    badge,
    source_description,
) -> None:
    take_dir = tmp_path / profile_key
    take_dir.mkdir()
    audio = take_dir / "source.wav"
    _wav(audio)
    take = TakeInfo(
        path=take_dir,
        name="Profile Take",
        tracks=[TrackInfo(audio, "Source", duration_s=1.0, samplerate=RATE)],
        creator_profile_key=profile_key,
    )
    with patch(
        "webjam_qt.widgets.recording_studio.discover_takes",
        return_value=[take],
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._on_take_selected(0)
            lane = studio._lanes[0]
            assert lane._source_badge.text() == badge
            assert lane._source_badge.accessibleName() == f"{badge} source"
            studio._select_track(0)
            assert studio._inspector_values["source"].text() == source_description
        finally:
            studio.shutdown()


def test_finish_stop_remains_enabled_if_profile_loses_record_permission() -> None:
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        review = get_creator_profile_by_key_or_default("review_rehearsal")
        no_new_recording = replace(
            review,
            capabilities=replace(
                review.capabilities,
                session_recording=False,
            ),
        )
        studio.set_creator_profile(no_new_recording)
        studio.set_live_participants(
            [SimpleNamespace(channel_id=8, name="Reviewer", is_local=True)]
        )
        assert not studio._record_btn.isEnabled()

        studio.set_recording_phase("recording")
        assert studio._record_btn.isEnabled()
        assert studio._record_btn.text() == "■ Stop Recording"

        studio.set_recording_phase("stop_failed")
        assert studio._record_btn.isEnabled()
        assert studio._record_btn.text() == "■ Finish Stop"
    finally:
        studio.shutdown()


@pytest.mark.parametrize(
    ("profile_key", "expected"),
    (
        ("music", "song sections"),
        ("podcast_voice", "chapters"),
        ("review_rehearsal", "sections"),
    ),
)
def test_arrangement_section_copy_follows_creator_profile(
    profile_key,
    expected,
) -> None:
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.set_creator_profile(
            get_creator_profile_by_key_or_default(profile_key)
        )
        assert studio._arrangement_section_plural() == expected
    finally:
        studio.shutdown()


def test_live_session_arms_one_lane_per_musician():
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.set_can_record(True)
        studio.set_live_participants(
            [
                SimpleNamespace(channel_id=0, name="Jeff", is_local=True),
                SimpleNamespace(channel_id=3, name="Sam", is_local=False),
            ]
        )
        studio.set_recording_phase("recording")
        assert set(studio._lanes) == {0, 3}
        assert studio._record_btn.text() == "■ Stop Recording"
        assert "one track per musician" in studio._phase.text()
        assert "RECORDING · you" in studio._lanes[0]._detail.text()
    finally:
        studio.shutdown()


def test_live_roster_never_claims_tracks_before_recorder_proof():
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.set_can_record(True)
        studio.set_live_participants(
            [SimpleNamespace(channel_id=2, name="Sam", is_local=False)]
        )

        assert "awaiting recorder proof" in studio._lanes[2]._detail.text()
        assert "mapped automatically after recorder proof" in studio._subtitle.text()
        assert "armed" not in studio._hint.text().casefold()
    finally:
        studio.shutdown()


def _exact_live_sources() -> tuple[RecordingSourcePresentation, ...]:
    return (
        RecordingSourcePresentation(
            participant_id="host",
            display_name="Host server stem",
            kind="musician",
            state=RecordingSourceState.RECORDING,
            channels=1,
            logical_source_id="00000000-0000-4000-8000-000000000301",
            source_kind=RecordingSourceKind.JAMULUS_SERVER,
            channel_id=4,
            meter_level=0.35,
            dropout_count=0,
            overloaded=False,
        ),
        RecordingSourcePresentation(
            participant_id="host",
            display_name="Host Local Original",
            kind="local_original",
            state=RecordingSourceState.RECORDING,
            channels=2,
            logical_source_id="00000000-0000-4000-8000-000000000302",
            source_kind=RecordingSourceKind.LOCAL_ORIGINAL,
            meter_level=0.6,
            dropout_count=2,
            overloaded=True,
        ),
        RecordingSourcePresentation(
            participant_id="",
            display_name="Shared Track",
            kind="shared_track",
            state=RecordingSourceState.RECORDING,
            channels=2,
            logical_source_id="00000000-0000-4000-8000-000000000303",
            source_kind=RecordingSourceKind.SHARED_TRACK,
            meter_level=None,
            dropout_count=None,
            overloaded=None,
        ),
    )


@pytest.mark.parametrize(
    ("profile_key", "server_badge"),
    (
        ("music", "MUSICIAN"),
        ("podcast_voice", "SPEAKER"),
        ("review_rehearsal", "WEBJAM AUDIO"),
    ),
)
def test_exact_live_sources_render_topology_health_and_profile_boundaries(
    profile_key,
    server_badge,
) -> None:
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.resize(760, 600)
        studio.show()
        studio.set_creator_profile(get_creator_profile_by_key_or_default(profile_key))
        studio.set_live_participants(
            [SimpleNamespace(channel_id=4, name="Host", is_local=True)]
        )
        roster_lane = studio._lanes[4]
        assert studio.set_recording_sources(_exact_live_sources())
        assert roster_lane.isHidden()
        studio.set_recording_phase("count_in")
        APP.processEvents()

        source_lanes = {
            row.logical_source_id: studio._lanes[
                studio._recording_source_lane_ids[row.logical_source_id]
            ]
            for row in _exact_live_sources()
        }
        server, local, shared = (
            source_lanes[row.logical_source_id] for row in _exact_live_sources()
        )
        assert len(studio._lanes) == 3
        assert server.channel_id == 4
        assert local.channel_id < 0 and shared.channel_id < 0
        assert server._source_badge.text() == server_badge
        assert local._source_badge.text() == "LOCAL ORIGINAL"
        assert shared._source_badge.text() == "SHARED TRACK"
        assert server._detail.text() == "REC · MONO · HEALTH OK"
        assert "STEREO" in local._detail.text()
        assert "DROP 2" in local._detail.text()
        assert "OVER" in local._detail.text()
        assert "METER ?" in shared._detail.text()
        assert local._meter._clipped is True
        assert local._meter._level == pytest.approx(0.6)
        assert "stereo" in local.accessibleDescription().lower()
        assert "2 reported dropouts" in local.accessibleDescription()
        assert not local._mute.isVisible()
        assert not shared._gain.isVisible()
        assert server._mute.isVisible()
        assert studio._track_scroll.accessibleName() == "Recorded track lanes"
        assert studio._track_scroll.height() >= 88

        server_before = server
        studio.set_recording_source_levels(
            {_exact_live_sources()[0].logical_source_id: 0.82}
        )
        assert studio._lanes[4] is server_before
        assert server_before._meter._level == pytest.approx(0.82)
        assert "3 exact sources" in studio._hint.text()
        assert "1 source needs attention" in studio._hint.text()
    finally:
        studio.shutdown()


def test_incomplete_exact_source_snapshot_fails_closed_without_roster_fallback():
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.set_live_participants(
            [SimpleNamespace(channel_id=4, name="Host", is_local=True)]
        )
        assert studio.set_recording_sources(_exact_live_sources())
        invalid = (
            replace(_exact_live_sources()[0], logical_source_id=""),
            *_exact_live_sources()[1:],
        )

        assert studio.set_recording_sources(invalid) is False
        assert studio._recording_sources_authoritative is True
        assert studio._lanes == {}
        assert "evidence is unavailable" in studio._hint.text()
        assert "Host" not in tuple(
            lane._name.text() for lane in studio._lanes.values()
        )
    finally:
        studio.shutdown()


def test_exact_source_rows_render_stopping_as_unconfirmed_not_planned():
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        stopping = tuple(
            replace(row, state=RecordingSourceState.STOPPING)
            for row in _exact_live_sources()
        )
        assert studio.set_recording_sources(stopping)
        studio.set_recording_phase("stopping")

        assert studio._hint.text().startswith("Stopping 3 exact sources")
        assert "Planned" not in studio._hint.text()
        assert all(
            lane._detail.text().startswith("STOPPING")
            for lane in studio._lanes.values()
        )
        assert all(
            "stopping" in lane.accessibleDescription()
            for lane in studio._lanes.values()
        )
    finally:
        studio.shutdown()


@pytest.mark.parametrize(
    ("source", "badge", "description", "synchronized"),
    (
        (SourceType.JAMULUS_SERVER, "MUSICIAN", "Musician (band server track)", True),
        ("live_reference", "SHARED TRACK", "Shared Track", True),
        (SourceType.LOCAL_ISOLATED, "LOCAL ORIGINAL", "Local Original", False),
        ("unknown", "TRACK", "Recorded track", False),
    ),
)
def test_recorded_source_presentation_has_one_truthful_classification(
    source,
    badge,
    description,
    synchronized,
):
    assert _safe_source_label(source) == badge
    assert _safe_source_description(source) == description
    assert _is_synchronized_source(source) is synchronized


def test_shared_track_take_is_distinct_and_synchronized_in_studio(tmp_path):
    _schema2_studio_take(
        tmp_path,
        server_source_type=SourceType.LIVE_REFERENCE,
        server_name="Shared Track",
    )
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.resize(1440, 900)
        studio.show()
        studio._take_list.setCurrentRow(0)
        APP.processEvents()

        lane = studio._lanes[0]
        assert lane._source_badge.text() == "SHARED TRACK"
        assert lane._source_badge.accessibleName() == "SHARED TRACK source"
        assert lane._detail.text() == "SYNCHRONIZED"
        assert lane.waveform._source == "live_reference"
        assert _is_synchronized_source(lane.waveform._source)

        studio._select_track(0)
        assert studio._inspector_values["source"].text() == "Shared Track"
        assert (
            studio._inspector_values["alignment"].text()
            == "Shared Track · band server timeline reference"
        )
        assert "Local Original" not in studio._inspector_values["source"].text()
    finally:
        studio.shutdown()


def test_recorded_take_renders_waveform_lanes_and_mixer_controls():
    with tempfile.TemporaryDirectory() as tmp:
        take = Path(tmp) / "Take 01"
        take.mkdir()
        _wav(take / "guitar.wav", 220)
        _wav(take / "drums.wav", 440)
        _mark_verified(take, "guitar.wav", "drums.wav")
        player = TakePlayer(samplerate=RATE, sink=_SilentSink())
        studio = RecordingStudio(tmp, player=player)
        try:
            assert studio._take_list.count() == 1
            studio._take_list.setCurrentRow(0)
            assert len(studio._lanes) == 2
            assert _wait_until(
                lambda: all(lane.waveform._peaks for lane in studio._lanes.values())
            )
            lane = studio._lanes[0]
            assert lane._trim.isHidden()
            assert lane._trim_value.isHidden()
            lane._gain.setValue(50)
            lane._mute.setChecked(True)
            lane._pan.setValue(-40)
            track = next(item for item in player.tracks if item.channel_id == 0)
            assert track.gain == 0.5
            assert track.muted is True
            assert track.pan == -0.4
            assert studio._export_btn.isEnabled()
        finally:
            studio.shutdown()


def test_legacy_ruler_origin_aligns_with_track_waveform_origin(tmp_path):
    take = tmp_path / "Take 01"
    take.mkdir()
    _wav(take / "guitar.wav")
    _mark_verified(take, "guitar.wav")
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.resize(1200, 760)
        studio.show()
        studio._take_list.setCurrentRow(0)
        APP.processEvents()

        lane = studio._lanes[0]
        waveform_x = lane.waveform.mapTo(studio, QPoint(0, 0)).x()
        ruler_x = studio._timeline_ruler.mapTo(studio, QPoint(0, 0)).x()
        assert ruler_x == waveform_x
    finally:
        studio.shutdown()


def test_studio_ruler_selection_gap_and_meter_share_the_recorded_timeline(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.resize(1440, 900)
        studio.show()
        studio._take_list.setCurrentRow(0)
        APP.processEvents()
        assert studio._inspector.isVisible()
        assert studio._timeline_ruler._seek_enabled
        assert studio._lanes[1].waveform._gaps

        studio._seek_from_ruler(0.5)
        assert studio._player.position_s == pytest.approx(0.5)
        assert studio._timeline_ruler._playhead == pytest.approx(0.5)
        assert all(
            lane.waveform._playhead == pytest.approx(0.5)
            for lane in studio._lanes.values()
        )

        epoch = studio._player.playback_epoch
        studio._on_levels_bg(epoch, {1: 0.73})
        studio._on_stereo_levels_bg(epoch, {1: (0.25, 0.75, True)})
        studio._on_stereo_levels_bg(epoch, {1: (0.3, 0.7, False)})
        studio._on_master_level_bg(epoch, (0.4, 0.8, True))
        studio._on_master_level_bg(epoch, (0.5, 0.6, False))
        studio._tick()
        assert studio._lanes[1]._meter._left == pytest.approx(0.3)
        assert studio._lanes[1]._meter._right == pytest.approx(0.7)
        assert studio._lanes[1]._meter._clipped is True
        assert studio._master_meter._right == pytest.approx(0.6)
        assert studio._master_meter._clipped is True

        studio._on_stereo_levels_bg(epoch, {1: (0.2, 0.4, False)})
        studio._on_master_level_bg(epoch, (0.1, 0.2, False))
        studio._tick()
        # The overload latch is sticky within one playback epoch: a clip
        # earlier this pass stays lit even though this tick is clean. It
        # clears only when transport restarts or seeks; the dedicated
        # overload-latch test covers that epoch-reset path.
        assert studio._lanes[1]._meter._clipped is True
        assert studio._master_meter._clipped is True
        studio._select_track(1)
        assert studio._lanes[1]._selected
        assert not studio._lanes[0]._selected
        assert "Local Original" in studio._inspector_values["source"].text()
        assert "1 recorded gap" in studio._inspector_values["gaps"].text()
        assert studio._timeline_ruler._trailing_inset >= 8
    finally:
        studio.shutdown()


def test_schema2_waveforms_activate_schedule_and_publish_on_ui_drain(
    tmp_path,
    monkeypatch,
):
    studio, region_ids = _open_schema2_waveform_studio(tmp_path)
    callback_threads: list[int] = []
    original_add_tile = studio._studio_arrange.add_region_waveform_tile

    def observed_add_tile(region_id, tile, *, generation=None):
        callback_threads.append(threading.get_ident())
        original_add_tile(region_id, tile, generation=generation)

    monkeypatch.setattr(
        studio._studio_arrange,
        "add_region_waveform_tile",
        observed_add_tile,
    )
    monkeypatch.setattr(
        studio._studio_arrange,
        "visible_frame_range",
        lambda: (0, RATE),
    )
    monkeypatch.setattr(
        studio._studio_arrange,
        "visible_region_ids",
        lambda: region_ids,
    )
    try:
        coordinator = studio._studio_waveforms
        activated = coordinator.stats
        assert activated.active_regions == len(region_ids)
        assert activated.sources == len(region_ids)
        assert studio._studio_waveform_document_key is not None

        studio._schedule_studio_waveforms()
        scheduled = coordinator.stats
        assert scheduled.schedules == activated.schedules + 1
        assert studio._studio_arrange._waveform_generation == scheduled.generation
        assert scheduled.in_flight <= 2
        assert _wait_without_qt_events(
            lambda: (
                coordinator.stats.in_flight == 0
                and coordinator.stats.pending == 0
                and coordinator.stats.queued_results > 0
            )
        )

        # Worker completion may enqueue results, but only the UI timer drain may
        # mutate the Arrange widget.
        assert callback_threads == []
        assert studio._studio_arrange._waveform_tiles == {}
        assert studio.thread() == QThread.currentThread()
        ui_thread = threading.get_ident()
        studio._tick()

        assert callback_threads
        assert set(callback_threads) == {ui_thread}
        assert set(studio._studio_arrange._waveform_tiles).issubset(region_ids)
        assert studio._studio_arrange._waveform_tiles
        assert coordinator.stats.published_region_tiles == len(callback_threads)
    finally:
        studio.shutdown()


def test_schema2_waveform_activation_failure_cancels_and_clears_ui(
    tmp_path,
):
    studio, _region_ids = _open_schema2_waveform_studio(tmp_path)
    try:
        studio._studio_waveform_document_key = None
        with (
            patch.object(
                studio._studio_waveforms,
                "activate",
                side_effect=StudioWaveformCoordinatorError("changed source"),
            ),
            patch.object(
                studio._studio_waveforms,
                "cancel",
                wraps=studio._studio_waveforms.cancel,
            ) as cancel,
            patch.object(
                studio._studio_arrange,
                "clear_waveforms",
                wraps=studio._studio_arrange.clear_waveforms,
            ) as clear,
        ):
            studio._activate_studio_waveforms()

        cancel.assert_called_once_with()
        clear.assert_called_once_with()
        assert studio._studio_waveform_document_key is None
        assert studio._studio_arrange._waveform_tiles == {}
    finally:
        studio.shutdown()


def test_schema2_repeated_viewport_schedules_stay_bounded_and_publish_current(
    tmp_path,
    monkeypatch,
):
    studio, region_ids = _open_schema2_waveform_studio(tmp_path)
    viewport = [0, 1_000]
    delivered = []
    coordinator = studio._studio_waveforms
    original_publish = coordinator._publish_tile

    def observed_publish(result):
        delivered.append(result)
        original_publish(result)

    monkeypatch.setattr(coordinator, "_publish_tile", observed_publish)
    monkeypatch.setattr(
        studio._studio_arrange,
        "visible_frame_range",
        lambda: (viewport[0], viewport[1]),
    )
    monkeypatch.setattr(
        studio._studio_arrange,
        "visible_region_ids",
        lambda: region_ids,
    )
    try:
        baseline = coordinator.stats
        generations: list[int] = []
        maximum_in_flight = 0
        for start in (0, 6_000, 1_000, 5_000, 2_000, 4_000, 3_000, 7_000):
            viewport[:] = (start, min(RATE, start + 1_000))
            studio._schedule_studio_waveforms()
            stats = coordinator.stats
            generations.append(stats.generation)
            maximum_in_flight = max(maximum_in_flight, stats.in_flight)
            assert stats.in_flight <= 2
            assert stats.pending <= 256

        assert generations == sorted(set(generations))
        current_generation = generations[-1]
        assert coordinator.stats.schedules == baseline.schedules + len(generations)
        assert maximum_in_flight <= 2
        assert _wait_without_qt_events(
            lambda: (
                coordinator.stats.in_flight == 0
                and coordinator.stats.pending == 0
                and coordinator.stats.queued_results > 0
            )
        )

        studio._tick()
        assert delivered
        assert {item.generation for item in delivered} == {current_generation}
        assert coordinator.stats.in_flight == 0
        assert coordinator.stats.pending == 0
    finally:
        studio.shutdown()


@pytest.mark.parametrize("transition", ["live", "shutdown"])
def test_schema2_waveform_lifecycle_stops_pending_publication(
    tmp_path,
    monkeypatch,
    transition,
):
    studio, region_ids = _open_schema2_waveform_studio(tmp_path)
    delivered = []
    coordinator = studio._studio_waveforms
    original_publish = coordinator._publish_tile

    def observed_publish(result):
        delivered.append(result)
        original_publish(result)

    monkeypatch.setattr(coordinator, "_publish_tile", observed_publish)
    monkeypatch.setattr(
        studio._studio_arrange,
        "visible_frame_range",
        lambda: (0, RATE),
    )
    monkeypatch.setattr(
        studio._studio_arrange,
        "visible_region_ids",
        lambda: region_ids,
    )
    try:
        studio._schedule_studio_waveforms()
        assert coordinator.stats.schedules >= 1
        assert coordinator.stats.in_flight <= 2

        if transition == "live":
            studio._show_live_session()
            assert studio._viewing_live is True
            assert studio._studio_waveform_document_key is None
        else:
            studio.shutdown()
            assert coordinator.stats.shutdown is True

        assert _wait_without_qt_events(lambda: coordinator.stats.in_flight == 0)
        assert coordinator.drain() == 0
        assert delivered == []
        assert coordinator.stats.published_region_tiles == 0
        assert studio._studio_arrange._waveform_tiles == {}
    finally:
        studio.shutdown()


def test_arrange_toolbar_edits_reload_cycle_and_preserve_source_truth(tmp_path):
    take_dir, _track_ids = _schema2_studio_take(tmp_path)
    manifest = take_dir / "webjam-take.json"
    media = tuple(sorted((take_dir / "media").glob("*.wav")))
    truth = {path: path.read_bytes() for path in (manifest, *media)}
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    studio.setStyleSheet(load_stylesheet())
    try:
        studio._take_list.setCurrentRow(0)
        document = studio._studio_state
        assert document is not None
        region = document.regions[0]
        studio._studio_arrange._user_select_region(region)
        studio._studio_arrange.set_playhead(1_234)

        studio._add_arrange_marker("Verse")
        studio._add_arrange_section("Chorus")
        studio._toggle_selected_cycle()
        studio._toggle_selected_region_fades()

        edited = studio._studio_state
        assert edited is not None
        active_markers = [item for item in edited.markers if not item.deleted]
        assert [(item.label, item.kind.value) for item in active_markers] == [
            ("Verse", "marker"),
            ("Chorus", "section"),
        ]
        assert active_markers[0].start_frame == 1_234
        assert active_markers[1].start_frame == region.timeline_start_frame
        assert active_markers[1].end_frame == region.timeline_end_frame
        assert edited.cycle_range is not None
        assert studio._player._studio_cycle_range == (
            region.timeline_start_frame,
            region.timeline_end_frame,
        )
        faded = edited.region_for(region.region_id)
        assert faded.fade_in_frames == round(RATE * 0.005)
        assert faded.fade_out_frames == round(RATE * 0.005)
        assert faded.fade_in_curve.value == "equal_power"

        duplicate_id = new_project_id()
        assert studio._perform_arrange_edit(
            "Create overlap",
            lambda value: value.duplicate_region(
                region.region_id,
                new_region_id=duplicate_id,
                timeline_start_frame=RATE // 2,
            ),
            reload_audio=True,
        )
        studio._toggle_selected_crossfade()
        crossfades = [
            item for item in studio._studio_state.crossfades if not item.deleted
        ]
        assert len(crossfades) == 1
        assert crossfades[0].start_frame == RATE // 2
        assert crossfades[0].frame_count == RATE // 2
        assert crossfades[0].curve.value == "equal_power"
        assert studio._crossfade_btn.text() == "No Xfade"

        studio.resize(760, 600)
        studio.show()
        APP.processEvents()
        bounds = studio.contentsRect()
        assert studio._arrange_toolbar.isVisible()
        for button in (
            studio._add_marker_btn,
            studio._add_section_btn,
            studio._cycle_region_btn,
            studio._region_fades_btn,
            studio._crossfade_btn,
        ):
            assert bounds.contains(button.mapTo(studio, button.rect().topLeft()))
            assert bounds.contains(button.mapTo(studio, button.rect().bottomRight()))
            assert button.width() >= button.minimumSizeHint().width()
        assert studio._add_marker_btn.text() == "＋ Mark"
        assert studio._cycle_region_btn.text() == "Clear"
        assert studio._region_fades_btn.text() == "No Fades"
        assert studio._crossfade_btn.text() == "No Xfade"
        assert studio._crossfade_btn.accessibleName() == (
            "Remove crossfade from selected overlapping regions"
        )

        studio._toggle_selected_crossfade()
        assert all(item.deleted for item in studio._studio_state.crossfades)
        assert studio._crossfade_btn.accessibleName() == (
            "Add crossfade to selected overlapping regions"
        )
        assert studio._flush_studio_state()
    finally:
        studio.shutdown()

    assert all(path.read_bytes() == before for path, before in truth.items())


def test_mixer_drag_is_one_undo_and_separate_drags_remain_separate(tmp_path):
    _take_dir, (track_id, _local_id) = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        lane = studio._lanes[0]

        lane._gain.sliderPressed.emit()
        lane._gain.setValue(110)
        lane._gain.setValue(120)
        lane._gain.setValue(130)
        lane._gain.sliderReleased.emit()
        assert studio._studio_state.state_for(track_id).fader_gain == pytest.approx(1.3)

        studio._undo_arrange_edit()
        assert studio._studio_state.state_for(track_id).fader_gain == pytest.approx(1.0)
        assert lane._gain.value() == 100
        assert studio._player.tracks[0].gain == pytest.approx(1.0)

        studio._redo_arrange_edit()
        assert studio._studio_state.state_for(track_id).fader_gain == pytest.approx(1.3)
        assert lane._gain.value() == 130

        lane._gain.sliderPressed.emit()
        lane._gain.setValue(140)
        lane._gain.setValue(150)
        lane._gain.sliderReleased.emit()
        assert studio._studio_state.state_for(track_id).fader_gain == pytest.approx(1.5)

        studio._undo_arrange_edit()
        assert studio._studio_state.state_for(track_id).fader_gain == pytest.approx(1.3)
        assert lane._gain.value() == 130
    finally:
        studio.shutdown()


def test_schema2_studio_choices_reopen_and_export_by_durable_track_id(tmp_path):
    take_dir, (server_id, local_id) = _schema2_studio_take(tmp_path)
    manifest = take_dir / "webjam-take.json"
    audio = take_dir / "media" / "local.wav"
    manifest_before = manifest.read_bytes()
    audio_before = audio.read_bytes()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        lane = studio._lanes[1]
        lane._trim.setValue(125)
        lane._gain.setValue(400)
        lane._pan.setValue(-40)
        lane._mute.setChecked(True)
        lane._solo.setChecked(True)
        lane._track_export_include.setChecked(False)
        studio._master_gain.setValue(85)
        studio._master_limiter.setChecked(False)
        studio._flush_studio_state()
    finally:
        studio.shutdown()

    state = load_studio_state(take_dir).state_for(local_id)
    assert state.trim_gain == pytest.approx(1.25)
    assert state.gain == pytest.approx(4.0)
    assert state.pan == pytest.approx(-0.4)
    assert state.muted is True
    assert state.solo is True
    assert state.export_included is False
    saved_document = load_studio_state(take_dir)
    assert saved_document.master.gain == pytest.approx(0.85)
    assert saved_document.master.limiter_enabled is False
    assert manifest.read_bytes() == manifest_before
    assert audio.read_bytes() == audio_before

    called = threading.Event()
    result = SimpleNamespace(
        folder=take_dir / "Studio Export",
        edited_stems=(),
        original_stems=(),
        sample_rate=RATE,
    )
    with patch(
        "webjam_qt.widgets.recording_studio.export_studio_arrangement",
        side_effect=lambda *_args, **_kwargs: called.set() or result,
    ) as export:
        reopened = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            reopened._take_list.setCurrentRow(0)
            lane = reopened._lanes[1]
            assert not lane._trim.isHidden()
            assert lane._trim.value() == 125
            assert lane._gain.value() == 400
            assert lane._pan.value() == -40
            assert lane._mute.isChecked()
            assert lane._solo.isChecked()
            assert not lane._track_export_include.isChecked()
            assert reopened._master_gain.value() == 85
            assert not reopened._master_limiter.isChecked()
            reopened._export_tracks()
            assert _wait_until(called.is_set)
            exported_project, exported_document, exported_root = export.call_args.args
            assert exported_project.take_id == reopened._studio_state.take_id
            assert exported_root == take_dir.resolve()
            assert exported_document.state_for(server_id).export_included is True
            local_state = exported_document.state_for(local_id)
            assert local_state.trim_gain == pytest.approx(1.25)
            assert local_state.export_included is False
            assert local_state.gain == pytest.approx(4.0)
            assert local_state.pan == pytest.approx(-0.4)
            assert local_state.muted is True
            assert local_state.solo is True
            assert export.call_args.kwargs["cancel_event"] is not None
        finally:
            reopened.shutdown()


def test_studio_default_comparison_ignores_revision_only(tmp_path):
    take_dir, _track_ids = _schema2_studio_take(tmp_path)
    project = load_take_project(take_dir)
    default = default_studio_document(project)
    resaved_default = replace(default, revision=default.revision + 20)

    assert not _studio_document_differs_from_default(resaved_default, project)

    edited = replace(
        resaved_default,
        master=replace(resaved_default.master, gain=0.75),
    )
    assert _studio_document_differs_from_default(edited, project)


def test_unsupported_platform_schema2_exports_explicit_aligned_originals(tmp_path):
    take_dir, (server_id, local_id) = _schema2_studio_take(tmp_path)
    called = threading.Event()
    result = SimpleNamespace(
        folder=take_dir / "Track Exports" / "Track Export",
        stems=(take_dir / "server.wav", take_dir / "local.wav"),
        samplerate=RATE,
    )
    with (
        patch(
            "webjam_qt.widgets.recording_studio.studio_export_supported",
            return_value=False,
        ),
        patch(
            "webjam_qt.widgets.recording_studio.QMessageBox.question"
        ) as confirmation,
        patch(
            "webjam_qt.widgets.recording_studio.export_track_package",
            side_effect=lambda *_args, **_kwargs: called.set() or result,
        ) as legacy_export,
        patch(
            "webjam_qt.widgets.recording_studio.export_studio_arrangement"
        ) as studio_export,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)

            assert studio._export_btn.text() == "Export Aligned Originals"
            assert studio._export_btn.accessibleName() == (
                "Export aligned originals without Studio edits"
            )
            assert "master controls" in studio._export_btn.toolTip().lower()
            assert "repeated take-lane recordings" in (
                studio._export_btn.toolTip().lower()
            )
            assert "excluded" in studio._hint.text().lower()

            studio._export_tracks()
            assert _wait_until(called.is_set)
            assert _wait_until(lambda: not studio.export_in_progress)

            confirmation.assert_not_called()
            studio_export.assert_not_called()
            selected = legacy_export.call_args.kwargs["selected_track_ids"]
            assert selected == {server_id, local_id}
            states = legacy_export.call_args.kwargs["mix_settings"]
            assert set(states) == {server_id, local_id}
            assert all(state.gain == pytest.approx(1.0) for state in states.values())
            assert studio._reveal_btn.text() == "Show Aligned Originals"
            assert "unity 24-bit" in studio._hint.text().lower()
            assert "master controls are excluded" in studio._hint.text().lower()
            assert "exported from their own take" in studio._hint.text().lower()
        finally:
            studio.shutdown()


def test_unsupported_platform_edited_studio_requires_confirmation_and_lane_mix(
    tmp_path,
):
    take_dir, (server_id, local_id) = _schema2_studio_take(tmp_path)
    called = threading.Event()
    result = SimpleNamespace(
        folder=take_dir / "Track Exports" / "Track Export",
        stems=(take_dir / "server.wav",),
        samplerate=RATE,
    )
    with (
        patch(
            "webjam_qt.widgets.recording_studio.studio_export_supported",
            return_value=False,
        ),
        patch(
            "webjam_qt.widgets.recording_studio.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as confirmation,
        patch(
            "webjam_qt.widgets.recording_studio.export_track_package",
            side_effect=lambda *_args, **_kwargs: called.set() or result,
        ) as legacy_export,
        patch(
            "webjam_qt.widgets.recording_studio.export_studio_arrangement"
        ) as studio_export,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            studio._lanes[0]._trim.setValue(125)
            studio._lanes[0]._gain.setValue(150)
            studio._lanes[0]._pan.setValue(-25)
            studio._lanes[0]._mute.setChecked(True)
            studio._lanes[1]._track_export_include.setChecked(False)

            studio._export_tracks()

            confirmation.assert_called_once()
            prompt = confirmation.call_args.args[2].lower()
            assert "unity aligned wav" in prompt
            assert "fades" in prompt
            assert "comp choices" in prompt
            assert "song sections" in prompt
            assert "master gain" in prompt
            assert "repeated take-lane recordings" in prompt
            assert "from its own take" in prompt
            legacy_export.assert_not_called()
            studio_export.assert_not_called()
            assert not studio.export_in_progress
            assert "export canceled" in studio._hint.text().lower()

            confirmation.reset_mock()
            confirmation.return_value = QMessageBox.StandardButton.Yes
            studio._export_tracks()
            assert _wait_until(called.is_set)
            assert _wait_until(lambda: not studio.export_in_progress)

            confirmation.assert_called_once()
            studio_export.assert_not_called()
            assert legacy_export.call_count == 1
            assert legacy_export.call_args.kwargs["selected_track_ids"] == {server_id}
            states = legacy_export.call_args.kwargs["mix_settings"]
            assert set(states) == {server_id, local_id}
            assert states[server_id].gain == pytest.approx(1.25 * 1.5)
            assert states[server_id].pan == pytest.approx(-0.25)
            assert states[server_id].muted is True
        finally:
            studio.shutdown()


def test_studio_export_failure_never_silently_falls_back(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    failed = threading.Event()

    def fail_studio_export(*_args, **_kwargs):
        failed.set()
        raise RuntimeError("private Studio export implementation detail")

    with (
        patch(
            "webjam_qt.widgets.recording_studio.studio_export_supported",
            return_value=True,
        ),
        patch(
            "webjam_qt.widgets.recording_studio.export_studio_arrangement",
            side_effect=fail_studio_export,
        ) as studio_export,
        patch(
            "webjam_qt.widgets.recording_studio.export_track_package"
        ) as legacy_export,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            studio._export_tracks()
            assert _wait_until(failed.is_set)
            assert _wait_until(lambda: not studio.export_in_progress)

            studio_export.assert_called_once()
            legacy_export.assert_not_called()
            hint = studio._hint.text().lower()
            assert "did not create an aligned-originals fallback" in hint
            assert "private" not in hint
            assert "saved studio choices are safe" in hint
        finally:
            studio.shutdown()


def test_schema2_activation_failure_locks_export_without_legacy_fallback(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        with patch.object(
            studio,
            "_source_catalog_for_document",
            side_effect=StudioSourceCatalogError("intentional inventory failure"),
        ):
            studio._take_list.setCurrentRow(0)

        assert studio._current is not None
        assert studio._current.manifest_schema_version == 2
        assert studio._studio_state is None
        assert studio._studio_project is None
        assert studio._studio_source_catalog is None
        assert not studio._export_btn.isEnabled()

        with (
            patch(
                "webjam_qt.widgets.recording_studio.export_studio_arrangement"
            ) as studio_export,
            patch(
                "webjam_qt.widgets.recording_studio.export_track_package"
            ) as legacy_export,
        ):
            studio._export_tracks()

        studio_export.assert_not_called()
        legacy_export.assert_not_called()
        assert studio.export_in_progress is False
        assert "export stays locked" in studio._hint.text().lower()
        assert "every recording is unchanged" in studio._hint.text().lower()
    finally:
        studio.shutdown()


def test_schema2_export_rechecks_source_inventory_at_enable_and_click(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        catalog = studio._studio_source_catalog
        assert catalog is not None
        assert studio._export_btn.isEnabled()

        studio._studio_source_catalog = None
        studio._refresh_export_button()
        assert not studio._export_btn.isEnabled()

        studio._studio_source_catalog = catalog
        studio._refresh_export_button()
        assert studio._export_btn.isEnabled()

        # Model a source-inventory invalidation after the last button refresh.
        # The click boundary must still refuse both worker paths.
        studio._studio_source_catalog = None
        with (
            patch(
                "webjam_qt.widgets.recording_studio.export_studio_arrangement"
            ) as studio_export,
            patch(
                "webjam_qt.widgets.recording_studio.export_track_package"
            ) as legacy_export,
        ):
            studio._export_tracks()

        studio_export.assert_not_called()
        legacy_export.assert_not_called()
        assert studio.export_in_progress is False
        assert "export stays locked" in studio._hint.text().lower()
    finally:
        studio.shutdown()


def test_schema2_export_shutdown_cancels_and_discards_worker_callback(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    started = threading.Event()
    stopped = threading.Event()
    observed_cancel: list[threading.Event] = []

    def wait_for_shutdown(
        _project,
        _document,
        _root,
        *,
        cancel_event,
        source_catalog=None,
    ):
        assert source_catalog is not None
        observed_cancel.append(cancel_event)
        started.set()
        assert cancel_event.wait(3.0)
        stopped.set()
        raise RuntimeError("cancelled after Studio shutdown")

    with patch(
        "webjam_qt.widgets.recording_studio.export_studio_arrangement",
        side_effect=wait_for_shutdown,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        finished: list[bool] = []
        studio.export_finished.connect(finished.append)
        studio._take_list.setCurrentRow(0)
        studio._export_tracks()
        assert _wait_until(started.is_set)
        worker = studio._export_thread

        studio.shutdown()
        assert observed_cancel and observed_cancel[0].is_set()
        assert _wait_until(stopped.is_set)
        assert worker is not None
        worker.join(timeout=1.0)
        studio._drain_export_results()
        assert finished == []
        assert studio.export_in_progress is False


def test_export_blocks_navigation_until_worker_result_is_drained(tmp_path):
    primary_dir, _track_ids = _schema2_studio_take(tmp_path)
    alternate_dir, _alternate_track_id = _schema2_repeated_take(tmp_path, primary_dir)
    started = threading.Event()
    release = threading.Event()

    def blocked_export(*_args, **_kwargs):
        started.set()
        assert release.wait(3.0)
        raise RuntimeError("intentional export test failure")

    with patch(
        "webjam_qt.widgets.recording_studio.export_studio_arrangement",
        side_effect=blocked_export,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        finished: list[bool] = []
        studio.export_finished.connect(finished.append)
        try:
            primary_row = next(
                row
                for row, take in enumerate(studio._takes)
                if take.path.resolve() == primary_dir.resolve()
            )
            alternate_row = next(
                row
                for row, take in enumerate(studio._takes)
                if take.path.resolve() == alternate_dir.resolve()
            )
            studio._take_list.setCurrentRow(primary_row)
            studio.set_can_record(True)
            studio.set_live_participants(
                [SimpleNamespace(channel_id=1, name="Drummer", is_local=False)]
            )
            assert studio._record_btn.isEnabled()
            current = studio._current
            studio._export_tracks()
            assert _wait_until(started.is_set)
            assert studio._export_btn.text() == "Exporting…"
            assert studio._export_btn.accessibleName() == "Exporting…"
            assert not studio._take_list.isEnabled()
            assert not studio._live_btn.isEnabled()
            assert not studio._new_take_btn.isEnabled()
            assert not studio._setup_btn.isEnabled()
            assert not studio._record_btn.isEnabled()

            studio._take_list.setCurrentRow(alternate_row)
            assert studio._current is current
            assert studio._take_list.currentRow() == primary_row
            assert "finish the current export" in studio._hint.text().lower()

            studio._show_live_session()
            assert studio._current is current
            assert studio._viewing_live is False
            assert "finish the current export" in studio._hint.text().lower()

            worker = studio._export_thread
            release.set()
            assert worker is not None
            assert _wait_without_qt_events(lambda: not worker.is_alive())
            studio._drain_export_results()
            assert finished == [False]
            assert studio._take_list.isEnabled()
            assert studio._live_btn.isEnabled()
            assert studio._new_take_btn.isEnabled()
            assert studio._setup_btn.isEnabled()
            assert studio._record_btn.isEnabled()
            assert studio._export_btn.text() == "Export Tracks"
            assert studio._export_btn.accessibleName() == "Export aligned tracks"
        finally:
            release.set()
            studio.shutdown()


def test_recording_preflight_blocks_export_before_worker_start(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        studio.set_recording_phase("preflight")
        assert not studio._export_btn.isEnabled()

        with patch(
            "webjam_qt.widgets.recording_studio.export_studio_arrangement"
        ) as export:
            studio._export_tracks()

        export.assert_not_called()
        assert studio.export_in_progress is False
    finally:
        studio.shutdown()


def test_recording_confirmation_cancels_raced_export_and_returns_live(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    started = threading.Event()
    release = threading.Event()
    observed_cancel: list[threading.Event] = []

    def blocked_export(*_args, cancel_event, **_kwargs):
        observed_cancel.append(cancel_event)
        started.set()
        assert release.wait(3.0)
        raise RuntimeError("raced export cancelled for recording")

    with patch(
        "webjam_qt.widgets.recording_studio.export_studio_arrangement",
        side_effect=blocked_export,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        finished: list[bool] = []
        studio.export_finished.connect(finished.append)
        try:
            studio._take_list.setCurrentRow(0)
            studio.set_can_record(True)
            studio.set_live_participants(
                [SimpleNamespace(channel_id=1, name="Drummer", is_local=False)]
            )
            studio._export_tracks()
            assert _wait_until(started.is_set)
            worker = studio._export_thread
            assert worker is not None

            studio.set_recording_phase("recording")

            assert observed_cancel and observed_cancel[0].is_set()
            assert studio.export_in_progress is False
            assert studio._viewing_live is True
            assert studio._current is None
            assert studio._record_btn.text() == "■ Stop Recording"
            assert studio._record_btn.isEnabled()
            assert finished == [False]

            release.set()
            assert _wait_without_qt_events(lambda: not worker.is_alive())
            studio._drain_export_results()
            assert finished == [False]
        finally:
            release.set()
            studio.shutdown()


def test_stale_export_result_restores_controls_and_emits_failure_once(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        reveal_before = studio._reveal_path
        finished: list[bool] = []
        studio.export_finished.connect(finished.append)
        studio._exporting = True
        studio._take_list.setEnabled(False)
        studio._live_btn.setEnabled(False)
        studio._new_take_btn.setEnabled(False)
        studio._setup_btn.setEnabled(False)
        studio._export_generation += 1
        stale_generation = studio._export_generation
        stale = SimpleNamespace(
            generation=stale_generation,
            take_path=tmp_path / "different-take",
            result=SimpleNamespace(folder=tmp_path / "stale-export"),
            error="",
            published_folder=None,
        )
        studio._export_results.put(stale)
        studio._export_results.put(stale)

        studio._drain_export_results()

        assert finished == [False]
        assert studio.export_in_progress is False
        assert studio._reveal_path == reveal_before
        assert studio._take_list.isEnabled()
        assert studio._live_btn.isEnabled()
        assert studio._new_take_btn.isEnabled()
        assert studio._setup_btn.isEnabled()
    finally:
        studio.shutdown()


def test_arrange_edit_undo_redo_reopens_without_touching_recording_truth(tmp_path):
    take_dir, _track_ids = _schema2_studio_take(tmp_path)
    manifest = take_dir / "webjam-take.json"
    media = tuple(sorted((take_dir / "media").glob("*.wav")))
    manifest_before = manifest.read_bytes()
    media_before = {path.name: path.read_bytes() for path in media}

    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        assert studio._studio_state is not None
        assert not studio._studio_arrange.isHidden()
        region = studio._studio_state.regions[0]
        original_start = region.timeline_start_frame

        studio._studio_arrange.region_move_requested.emit(region.region_id, 256)
        assert (
            studio._studio_state.region_for(region.region_id).timeline_start_frame
            == 256
        )
        assert studio._studio_controller.can_undo
        assert studio._studio_state_dirty

        studio._studio_arrange.undo_requested.emit()
        assert (
            studio._studio_state.region_for(region.region_id).timeline_start_frame
            == original_start
        )
        studio._studio_arrange.redo_requested.emit()
        assert (
            studio._studio_state.region_for(region.region_id).timeline_start_frame
            == 256
        )
        assert studio._flush_studio_state() is True
        saved = studio._studio_state.to_dict()
    finally:
        studio.shutdown()

    reopened = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        reopened._take_list.setCurrentRow(0)
        assert reopened._studio_state is not None
        assert reopened._studio_state.to_dict() == saved
    finally:
        reopened.shutdown()

    assert manifest.read_bytes() == manifest_before
    assert {path.name: path.read_bytes() for path in media} == media_before


def test_named_section_move_ripples_every_track_as_one_undoable_edit(tmp_path):
    take_dir, track_ids = _schema2_studio_take(tmp_path)
    manifest = take_dir / "webjam-take.json"
    media = tuple(sorted((take_dir / "media").glob("*.wav")))
    truth = {path: path.read_bytes() for path in (manifest, *media)}
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        section_id = new_project_id()
        assert studio._perform_arrange_edit(
            "Add Verse section",
            lambda document: document.upsert_marker(
                StudioMarker(
                    marker_id=section_id,
                    start_frame=0,
                    label="Verse",
                    kind=MarkerKind.SECTION,
                    end_frame=RATE // 2,
                )
            ),
            reload_audio=False,
        )
        before_move = studio._studio_state
        assert before_move is not None
        history = studio._studio_controller._history
        assert history is not None
        undo_depth = history.undo_depth

        studio._studio_arrange.section_move_requested.emit(section_id, RATE // 2)

        moved = studio._studio_state
        assert moved is not None
        assert moved.revision == before_move.revision + 1
        assert history.undo_depth == undo_depth + 1
        section = next(item for item in moved.markers if item.marker_id == section_id)
        assert (section.start_frame, section.end_frame) == (RATE // 2, RATE)
        for track_id in track_ids:
            fragments = sorted(
                (
                    item.timeline_start_frame,
                    item.timeline_frame_count,
                    item.source_start_frame,
                    item.source_frame_count,
                )
                for item in moved.regions
                if item.track_id == track_id and item.enabled and not item.deleted
            )
            assert fragments == [
                (0, RATE // 2, RATE // 2, RATE // 2),
                (RATE // 2, RATE // 2, 0, RATE // 2),
            ]
        assert studio._player._studio_renderer is not None
        assert studio._player._studio_renderer.document is moved
        hint_text = studio._hint.text()
        assert "every track" in hint_text.lower()
        assert "verse" in hint_text.lower()
        assert _format_frame_time(RATE // 2, before_move.project_sample_rate) in (
            hint_text
        )

        studio._studio_arrange.undo_requested.emit()
        assert studio._studio_state == before_move
        studio._studio_arrange.redo_requested.emit()
        assert studio._studio_state == moved
    finally:
        studio.shutdown()

    assert all(path.read_bytes() == before for path, before in truth.items())


def test_named_section_move_rejection_explains_the_specific_reason(tmp_path):
    """A rejected section move must surface its real cause, not a generic
    catch-all, so a musician knows the precise problem and corrective action."""

    take_dir, _track_ids = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        section_id = new_project_id()
        assert studio._perform_arrange_edit(
            "Add Verse section",
            lambda document: document.upsert_marker(
                StudioMarker(
                    marker_id=section_id,
                    start_frame=0,
                    label="Verse",
                    kind=MarkerKind.SECTION,
                    end_frame=RATE // 2,
                )
            ),
            reload_audio=False,
        )
        before = studio._studio_state
        assert before is not None
        history = studio._studio_controller._history
        assert history is not None
        undo_depth = history.undo_depth

        # A target strictly inside the section's own span is rejected by
        # core.studio_sections.reorder_section.
        studio._studio_arrange.section_move_requested.emit(section_id, RATE // 4)

        assert studio._studio_state == before
        assert history.undo_depth == undo_depth
        hint_text = studio._hint.text().lower()
        assert "target start cannot be inside the section" in hint_text
        assert "unchanged" in hint_text
    finally:
        studio.shutdown()


_LEAKY_TEXT = (
    "failed at /Users/musician/Music/WebJam Takes/take-01/media/server.wav "
    "via https://example.invalid/session?token=abc "
    "for device 0123456789abcdef0123456789abcdef"
)


def test_arrange_edit_message_keeps_known_reasons_and_bounds_unknown_text():
    """Specific Studio validation reasons stay useful, but arbitrary exception
    text must never become the musician-facing contract."""

    known = StudioSectionError("The target start cannot be inside the section.")
    message = arrange_edit_failure_message(known)
    assert "The target start cannot be inside the section." in message
    assert "The recorded take is unchanged." in message

    # An unrecognised error type falls back rather than echoing its text.
    unknown = ValueError("some unreviewed internal detail")
    assert arrange_edit_failure_message(unknown) == ARRANGE_EDIT_FALLBACK
    assert "unreviewed" not in arrange_edit_failure_message(unknown)

    # A store error is a ValueError too, but wraps arbitrary filesystem text.
    assert (
        arrange_edit_failure_message(StudioStoreError("Could not open Studio state."))
        == ARRANGE_EDIT_FALLBACK
    )

    # Even a displayable type is rejected when its text looks like a path,
    # URL, address, or opaque token.
    leaky = StudioProjectError(_LEAKY_TEXT)
    leaky_message = arrange_edit_failure_message(leaky)
    assert leaky_message == ARRANGE_EDIT_FALLBACK
    for fragment in (
        "/Users/musician",
        "https://",
        "example.invalid",
        "token=abc",
        "0123456789abcdef0123456789abcdef",
        "server.wav",
    ):
        assert fragment not in leaky_message

    # Long but otherwise safe text is bounded, not truncated mid-contract.
    long_reason = StudioProjectError("A region fragment crosses a seam. " * 40)
    long_message = arrange_edit_failure_message(long_reason)
    assert safe_detail(long_reason)
    assert len(safe_detail(long_reason)) <= MAX_DETAIL_CHARACTERS
    assert "The recorded take is unchanged." in long_message

    # Ordinary whitespace is normalized into one flowing sentence...
    assert (
        safe_detail(StudioProjectError("Broken\nmulti\tline  detail"))
        == "Broken multi line detail."
    )
    # ...while genuinely non-printable control bytes are rejected outright.
    assert safe_detail(StudioProjectError("Detail with \x00 a null byte")) == ""
    assert safe_detail(StudioProjectError("Bell \x07 detail")) == ""
    assert safe_detail(StudioProjectError("   ")) == ""


def test_export_failure_copy_never_echoes_raw_worker_text():
    """Export workers can raise text carrying local paths; the UI helpers must
    return their own fixed copy instead of any part of it."""

    for message in (
        _studio_export_failure_message(_LEAKY_TEXT),
        _track_export_failure_message(_LEAKY_TEXT),
        _studio_export_failure_message(""),
        _track_export_failure_message(None),
    ):
        for fragment in (
            "/Users/musician",
            "https://",
            "example.invalid",
            "token=abc",
            "0123456789abcdef0123456789abcdef",
            "server.wav",
            "failed at",
        ):
            assert fragment not in message
        assert "take is safe" in message.lower() or "are safe" in message.lower()

    # The two recognised, actionable export reasons still keep their guidance.
    silent = _studio_export_failure_message(
        "WebJam found explicitly silent segments in selected performance tracks: "
        "/Users/musician/Music/WebJam Takes/take-01/media/server.wav"
    )
    assert "explicitly silent segment" in silent
    assert "/Users/musician" not in silent


def test_arrange_edit_rejection_leaves_document_and_history_untouched(tmp_path):
    take_dir, _track_ids = _schema2_studio_take(tmp_path)
    manifest = take_dir / "webjam-take.json"
    media = tuple(sorted((take_dir / "media").glob("*.wav")))
    truth = {path: path.read_bytes() for path in (manifest, *media)}
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        before = studio._studio_state
        assert before is not None
        history = studio._studio_controller._history
        assert history is not None
        undo_depth = history.undo_depth

        def _raise_unreviewed(_document):
            raise ValueError("unreviewed internal detail at /private/tmp/secret")

        assert (
            studio._perform_arrange_edit(
                "Unsafe edit",
                _raise_unreviewed,
                reload_audio=False,
            )
            is False
        )

        assert studio._studio_state == before
        assert history.undo_depth == undo_depth
        hint = studio._hint.text()
        assert hint == ARRANGE_EDIT_FALLBACK
        assert "/private/tmp/secret" not in hint
        assert "unreviewed" not in hint
    finally:
        studio.shutdown()

    assert all(path.read_bytes() == before for path, before in truth.items())


def test_add_take_popup_action_reaches_the_selected_source(tmp_path):
    primary_dir, _track_ids = _schema2_studio_take(tmp_path)
    alternate_dir, alternate_track_id = _schema2_repeated_take(
        tmp_path,
        primary_dir,
    )
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )

    try:
        primary_row = next(
            index
            for index, take in enumerate(studio._takes)
            if take.path.resolve() == primary_dir.resolve()
        )
        studio._take_list.setCurrentRow(primary_row)
        studio._select_track(0)
        sources = studio._available_comp_sources()
        with patch.object(studio, "_add_take_lane_from_source") as add_lane:
            menu = studio._build_add_take_lane_menu(sources)
            assert menu.accessibleName() == "Matching repeated takes"
            actions = [action for action in menu.actions() if not action.isSeparator()]
            assert len(actions) == 1
            assert "Take" in actions[0].text()

            actions[0].trigger()

            add_lane.assert_called_once_with(
                alternate_dir.resolve(),
                alternate_track_id,
            )
    finally:
        studio.shutdown()


def test_completed_take_automatically_stacks_only_exact_repeated_source(tmp_path):
    logical_source_id = new_project_id()
    primary_dir, _track_ids = _schema2_studio_take(
        tmp_path,
        server_logical_source_id=logical_source_id,
    )
    repeated_dir, _repeated_track_id = _schema2_repeated_take(
        tmp_path,
        primary_dir,
    )
    primary = load_take_project(primary_dir)
    repeated = load_take_project(repeated_dir)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.on_take_completed(repeated_dir)

        assert studio._studio_project == repeated
        lanes = tuple(
            lane for lane in studio._studio_state.take_lanes if not lane.deleted
        )
        assert len(lanes) == 1
        assert lanes[0].source_take_id == primary.take_id
        assert "stacked automatically" in studio._hint.text()
        assert studio._flush_studio_state()
        assert (repeated_dir / ".webjam-studio-state.json").is_file()

        studio.on_take_completed(repeated_dir)
        assert len(
            [lane for lane in studio._studio_state.take_lanes if not lane.deleted]
        ) == 1
    finally:
        studio.shutdown()


def test_review_preview_never_auto_creates_take_lane_sidecar(tmp_path):
    primary_dir, _track_ids = _schema2_studio_take(
        tmp_path,
        creator_profile_key="review_rehearsal",
        server_logical_source_id=new_project_id(),
    )
    repeated_dir, _repeated_track_id = _schema2_repeated_take(
        tmp_path,
        primary_dir,
    )
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.on_take_completed(repeated_dir)

        assert studio._creator_profile_key == "review_rehearsal"
        assert studio._studio_state is None
        assert not (repeated_dir / ".webjam-studio-state.json").exists()
    finally:
        studio.shutdown()


def test_repeated_take_lane_comp_audition_export_and_reopen(tmp_path):
    primary_dir, (destination_track_id, _local_id) = _schema2_studio_take(tmp_path)
    alternate_dir, alternate_track_id = _schema2_repeated_take(
        tmp_path,
        primary_dir,
    )
    source_truth = {
        path: path.read_bytes()
        for path in (
            primary_dir / "webjam-take.json",
            primary_dir / "media" / "server.wav",
            alternate_dir / "webjam-take.json",
            alternate_dir / "media" / "server.wav",
        )
    }
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        primary_row = next(
            index
            for index, take in enumerate(studio._takes)
            if take.path.resolve() == primary_dir.resolve()
        )
        studio._take_list.setCurrentRow(primary_row)
        studio._select_track(0)
        sources = studio._available_comp_sources()
        assert any(item[0] == alternate_dir.resolve() for item in sources)

        studio._add_take_lane_from_source(alternate_dir, alternate_track_id)
        assert studio._studio_source_catalog is not None
        assert studio._studio_source_catalog.take_ids == (
            studio._studio_project.take_id,
            next(
                item[1].take_id
                for item in sources
                if item[0] == alternate_dir.resolve()
            ),
        )
        lane = next(
            item
            for item in studio._studio_state.take_lanes
            if item.track_id == destination_track_id and not item.deleted
        )
        studio._select_comp_range(lane.lane_id, 1_000, 4_000)
        active_comps = [
            item
            for item in studio._studio_state.comp_ranges
            if not item.deleted and item.enabled
        ]
        assert len(active_comps) == 1
        assert active_comps[0].lane_id == lane.lane_id

        persisted_before_audition = studio._studio_state.to_dict()
        studio._studio_arrange._user_select_lane(destination_track_id, lane.lane_id)
        # 1001 / 8000 does not survive an int(seconds * rate) round-trip.
        studio._player.seek_frame(1_001)
        studio._toggle_take_lane_audition(lane.lane_id)
        assert studio._player.position_frame == 1_001
        assert studio._studio_audition_lane_id == lane.lane_id
        assert studio._studio_arrange.audition_lane_id == lane.lane_id
        assert studio._studio_state.to_dict() == persisted_before_audition
        studio._toggle_take_lane_audition(lane.lane_id)
        assert studio._player.position_frame == 1_001
        assert studio._studio_audition_lane_id is None

        called = threading.Event()
        result = SimpleNamespace(
            folder=primary_dir / "Studio Export",
            edited_stems=(),
            original_stems=(),
            sample_rate=RATE,
        )
        with patch(
            "webjam_qt.widgets.recording_studio.export_studio_arrangement",
            side_effect=lambda *_args, **_kwargs: called.set() or result,
        ) as export:
            studio._export_tracks()
            assert _wait_until(called.is_set)
            assert export.call_args.kwargs["source_catalog"] is (
                studio._studio_source_catalog
            )
        assert studio._flush_studio_state()
        saved = studio._studio_state.to_dict()
    finally:
        studio.shutdown()

    reopened = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        primary_row = next(
            index
            for index, take in enumerate(reopened._takes)
            if take.path.resolve() == primary_dir.resolve()
        )
        reopened._take_list.setCurrentRow(primary_row)
        assert reopened._studio_state.to_dict() == saved
        assert reopened._studio_source_catalog is not None
        assert reopened._player.duration_s == pytest.approx(1.0)
    finally:
        reopened.shutdown()

    assert all(path.read_bytes() == before for path, before in source_truth.items())


def test_disabled_alternate_lane_source_stays_cataloged_for_reenable(tmp_path):
    primary_dir, (destination_track_id, _local_id) = _schema2_studio_take(tmp_path)
    alternate_dir, alternate_track_id = _schema2_repeated_take(tmp_path, primary_dir)
    alternate_take_id = load_take_project(alternate_dir).take_id
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        primary_row = next(
            row
            for row, take in enumerate(studio._takes)
            if take.path.resolve() == primary_dir.resolve()
        )
        studio._take_list.setCurrentRow(primary_row)
        studio._select_track(0)
        studio._add_take_lane_from_source(alternate_dir, alternate_track_id)
        lane = next(
            item
            for item in studio._studio_state.take_lanes
            if item.track_id == destination_track_id and not item.deleted
        )
        region_id = lane.region_ids[0]
        assert studio._perform_arrange_edit(
            "Disable alternate lane region",
            lambda document: document.set_region_enabled(region_id, False),
            reload_audio=True,
        )
        assert studio._studio_source_catalog.take_ids == (
            studio._studio_project.take_id,
            alternate_take_id,
        )
        assert studio._flush_studio_state()
    finally:
        studio.shutdown()

    reopened = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        primary_row = next(
            row
            for row, take in enumerate(reopened._takes)
            if take.path.resolve() == primary_dir.resolve()
        )
        reopened._take_list.setCurrentRow(primary_row)
        assert reopened._studio_state.region_for(region_id).enabled is False
        assert reopened._studio_source_catalog.take_ids == (
            reopened._studio_project.take_id,
            alternate_take_id,
        )

        reopened._enable_arrange_region(region_id, True)
        assert reopened._studio_state.region_for(region_id).enabled is True
        assert reopened._studio_source_catalog.take_ids == (
            reopened._studio_project.take_id,
            alternate_take_id,
        )
    finally:
        reopened.shutdown()


def test_lane_remove_undo_redo_rebuilds_cross_take_catalog_inventory(tmp_path):
    primary_dir, (destination_track_id, _local_id) = _schema2_studio_take(tmp_path)
    alternate_dir, alternate_track_id = _schema2_repeated_take(tmp_path, primary_dir)
    alternate_project = load_take_project(alternate_dir)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        primary_row = next(
            row
            for row, take in enumerate(studio._takes)
            if take.path.resolve() == primary_dir.resolve()
        )
        studio._take_list.setCurrentRow(primary_row)
        studio._select_track(0)
        studio._add_take_lane_from_source(alternate_dir, alternate_track_id)
        lane = next(
            item
            for item in studio._studio_state.take_lanes
            if item.track_id == destination_track_id and not item.deleted
        )
        studio._studio_arrange._user_select_lane(destination_track_id, lane.lane_id)
        studio._remove_selected_take_lane()
        assert studio._studio_source_catalog.take_ids == (
            studio._studio_project.take_id,
        )

        studio._undo_arrange_edit()
        assert studio._studio_source_catalog.take_ids == (
            studio._studio_project.take_id,
            alternate_project.take_id,
        )
        studio._redo_arrange_edit()
        assert studio._studio_source_catalog.take_ids == (
            studio._studio_project.take_id,
        )

        manifest = alternate_dir / "webjam-take.json"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        studio._studio_source_catalog.assert_current()
        studio._reload_arranged_playback()
        assert studio._player._studio_renderer is not None
    finally:
        studio.shutdown()


def test_studio_autosave_failure_stays_dirty_and_retries_without_losing_edit(
    tmp_path,
):
    take_dir, (_server_id, local_id) = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        studio._lanes[1]._pan.setValue(35)
        assert studio._studio_state_dirty is True

        with patch(
            "core.studio_controller.save_studio_document",
            side_effect=StudioStoreError("disk full"),
        ):
            assert studio.prepare_close() is False
            current = studio._current
            studio._show_live_session()
            assert studio._current is current
            assert studio._viewing_live is False
            assert studio.shutdown() is False

        assert studio._studio_state_dirty is True
        assert studio._studio_controller.dirty is True
        assert studio._studio_controller.is_shutdown is False
        assert studio._waveform_shutdown is False
        assert studio._studio_controller.document.state_for(
            local_id
        ).pan == pytest.approx(0.35)
        assert "recorded take is safe" in studio._hint.text().lower()
        assert studio.prepare_close() is True
        assert studio._studio_state_dirty is False
        assert studio._studio_persistence_failed is False
        assert studio._studio_state_error == ""
        assert "studio choices saved" in studio._hint.text().lower()
        assert "couldn't save" not in studio._hint.text().lower()
        assert studio.shutdown() is True
        assert studio.shutdown() is True
    finally:
        studio.shutdown()

    assert load_studio_state(take_dir).state_for(local_id).pan == pytest.approx(0.35)


def test_failed_post_controller_activation_unloads_before_next_take(tmp_path):
    primary_dir, _track_ids = _schema2_studio_take(tmp_path)
    next_dir, _track_id = _schema2_repeated_take(tmp_path, primary_dir)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    original_catalog_builder = studio._source_catalog_for_document

    def fail_primary(document, project, take_root):
        if take_root == primary_dir.resolve():
            raise StudioSourceCatalogError("intentional catalog failure")
        return original_catalog_builder(document, project, take_root)

    try:
        with patch.object(
            studio,
            "_source_catalog_for_document",
            side_effect=fail_primary,
        ):
            primary_row = next(
                row
                for row, take in enumerate(studio._takes)
                if take.path.resolve() == primary_dir.resolve()
            )
            next_row = next(
                row
                for row, take in enumerate(studio._takes)
                if take.path.resolve() == next_dir.resolve()
            )
            studio._take_list.setCurrentRow(primary_row)
            assert studio._studio_state is None
            assert studio._studio_controller.take_path is None
            assert studio._studio_controller.dirty is False

            studio._take_list.setCurrentRow(next_row)
            assert studio._studio_state is not None
            assert studio._studio_controller.take_path == next_dir.resolve()
            assert studio._studio_state_take_path == next_dir.resolve()
    finally:
        studio.shutdown()


def test_late_attached_track_refreshes_selected_take_without_reopening(tmp_path):
    take = tmp_path / "Take 01"
    take.mkdir()
    _wav(take / "host.wav", 220)
    _mark_verified(take, "host.wav")
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        assert studio._current is not None
        assert studio._current.track_count == 1

        _wav(take / "guest-local-original.wav", 440)
        _mark_verified(take, "host.wav", "guest-local-original.wav")
        studio.refresh_take(take)

        assert studio._current is not None
        assert studio._current.path == take
        assert studio._current.track_count == 2
        assert len(studio._lanes) == 2
        assert "2 synchronized tracks" in studio._subtitle.text()
    finally:
        studio.shutdown()


def test_studio_reveals_preserved_originals_without_touching_media(tmp_path):
    originals = tmp_path / "WebJam Local Originals" / "session" / "take"
    originals.mkdir(parents=True)
    source = originals / "input-1.wav"
    _wav(source)
    before = source.read_bytes()
    before_stat = source.stat()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        with patch(
            "webjam_qt.widgets.recording_studio.QDesktopServices.openUrl",
            return_value=True,
        ) as open_url:
            studio.set_local_originals_directory(tmp_path / "WebJam Local Originals")
            assert not studio._originals_btn.isHidden()
            assert studio._originals_btn.isEnabled()
            studio._originals_btn.click()

        opened = open_url.call_args.args[0]
        assert Path(opened.toLocalFile()) == tmp_path / "WebJam Local Originals"
        assert source.read_bytes() == before
        after_stat = source.stat()
        assert after_stat.st_size == before_stat.st_size
        assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    finally:
        studio.shutdown()


def test_waveform_builder_catches_impulse_between_former_sparse_windows(tmp_path):
    source = tmp_path / "one-hundred-seconds.wav"
    rate = 48_000
    # With 720 buckets, the former sampler read frames 0..4095 then resumed at
    # roughly frame 6666.  This full-scale transient was therefore invisible.
    _impulse_wav(
        source,
        frames=rate * 100,
        rate=rate,
        impulses={5_000: 1.0},
    )

    peaks = _waveform_peaks(source, buckets=720, chunk_frames=1024)

    assert len(peaks) == 720
    assert peaks[0] > 0.99
    assert max(peaks) > 0.99


def test_waveform_builder_includes_first_and_last_source_frames(tmp_path):
    source = tmp_path / "edges.wav"
    _impulse_wav(
        source,
        frames=1_000,
        rate=1_000,
        impulses={0: 0.25, 999: 0.75},
    )

    peaks = _waveform_peaks(source, buckets=10, chunk_frames=17)

    assert peaks[0] == pytest.approx(0.25, abs=0.01)
    assert peaks[-1] == pytest.approx(0.75, abs=0.01)
    moved = source.with_name("edges-moved.wav")
    source.replace(moved)
    assert moved.is_file()


def test_composite_waveform_preserves_reconnect_silence_and_declared_gap(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _impulse_wav(
        first,
        frames=100,
        rate=1_000,
        impulses={10: 0.4, 50: 0.9},
    )
    _impulse_wav(second, frames=100, rate=1_000, impulses={20: 0.7})
    spec = _CompositeWaveformSpec(
        segments=(
            _WaveformSegmentSpec(
                first,
                project_start_frame=0,
                frame_count=100,
                samplerate=1_000,
                channels=1,
                # Masks the larger first-file impulse while retaining frame 10.
                gaps=((40, 20, (), "writer gap"),),
            ),
            _WaveformSegmentSpec(
                second,
                project_start_frame=900,
                frame_count=100,
                samplerate=1_000,
                channels=1,
            ),
        ),
        project_samplerate=1_000,
        offset_s=0.0,
        drift_ppm=0.0,
        timeline_duration_s=1.0,
    )

    peaks = _composite_waveform_peaks(spec, buckets=10, chunk_frames=13)

    assert peaks[0] == pytest.approx(0.4, abs=0.02)
    assert peaks[1:9] == (0.0,) * 8
    assert peaks[9] == pytest.approx(0.7, abs=0.02)


def test_waveform_cache_invalidates_on_source_size_or_mtime_change(tmp_path):
    source = tmp_path / "track.wav"
    _wav(source)
    cache = _WaveformPeakCache(max_entries=2)
    original_key = _waveform_source_key(source)
    cache.put(original_key, (0.25,))
    assert cache.get(original_key) == (0.25,)

    stat = source.stat()
    os.utime(
        source,
        ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
    )
    mtime_key = _waveform_source_key(source)
    assert mtime_key != original_key
    assert cache.get(mtime_key) is None

    with source.open("ab") as audio:
        audio.write(b"\0\0")
    size_key = _waveform_source_key(source)
    assert size_key != mtime_key
    assert cache.get(size_key) is None


def test_switching_takes_cancels_and_suppresses_stale_waveform_results(tmp_path):
    first = tmp_path / "First"
    second = tmp_path / "Second"
    first.mkdir()
    second.mkdir()
    _wav(first / "track.wav", 220)
    _wav(second / "track.wav", 440)
    _mark_verified(first, "track.wav")
    _mark_verified(second, "track.wav")
    first_started = threading.Event()
    first_cancelled = threading.Event()

    def fake_peaks(path, **kwargs):
        cancel_event = kwargs.get("cancel_event")
        if Path(path).parent.name == "First":
            first_started.set()
            if cancel_event is not None and cancel_event.wait(timeout=2.0):
                first_cancelled.set()
            return (0.95,)
        return (0.35,)

    with patch(
        "webjam_qt.widgets.recording_studio._waveform_peaks",
        side_effect=fake_peaks,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            first_row = next(
                index for index, take in enumerate(studio._takes) if take.path == first
            )
            second_row = next(
                index for index, take in enumerate(studio._takes) if take.path == second
            )
            studio._take_list.setCurrentRow(first_row)
            assert first_started.wait(timeout=1.0)
            stale_generation = studio._waveform_generation

            studio._take_list.setCurrentRow(second_row)

            assert first_cancelled.wait(timeout=1.0)
            assert _wait_until(lambda: studio._lanes[0].waveform._peaks == (0.35,))
            # Even a late/non-cooperative producer cannot overwrite the new
            # lane because results carry the selection generation.
            studio._waveform_results.put(
                (
                    stale_generation,
                    0,
                    first / "track.wav",
                    _waveform_source_key(first / "track.wav"),
                    (0.95,),
                )
            )
            studio._drain_waveform_results()
            assert studio._current.path == second
            assert studio._lanes[0].waveform._peaks == (0.35,)
        finally:
            studio.shutdown()


def test_waveform_work_never_blocks_ui_and_shutdown_cancels_worker(tmp_path):
    take = tmp_path / "Take 01"
    take.mkdir()
    _wav(take / "guitar.wav")
    _mark_verified(take, "guitar.wav")
    worker_started = threading.Event()
    worker_cancelled = threading.Event()
    worker_threads: list[int] = []

    def blocking_peaks(_path, **kwargs):
        worker_threads.append(threading.get_ident())
        worker_started.set()
        cancel_event = kwargs.get("cancel_event")
        if cancel_event is not None and cancel_event.wait(timeout=2.0):
            worker_cancelled.set()
        return (0.5,)

    with patch(
        "webjam_qt.widgets.recording_studio._waveform_peaks",
        side_effect=blocking_peaks,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        shutdown = False
        try:
            started_at = time.monotonic()
            studio._take_list.setCurrentRow(0)
            selection_elapsed = time.monotonic() - started_at
            assert selection_elapsed < 0.5
            assert worker_started.wait(timeout=1.0)
            assert all(
                thread_id != threading.get_ident() for thread_id in worker_threads
            )

            ui_tick: list[bool] = []
            QTimer.singleShot(0, lambda: ui_tick.append(True))
            APP.processEvents()
            assert ui_tick == [True]

            studio.shutdown()
            shutdown = True
            assert worker_cancelled.wait(timeout=1.0)
        finally:
            if not shutdown:
                studio.shutdown()


def test_joiner_studio_explains_that_host_owns_recording():
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.set_can_record(False, "The host controls recording.")
        assert not studio._record_btn.isEnabled()
        assert "host controls recording" in studio._hint.text()
    finally:
        studio.shutdown()


@pytest.mark.parametrize(
    ("width", "height"),
    ((760, 600), (1024, 768), (1440, 900)),
)
def test_studio_actions_fit_the_supported_workspace_sizes(width, height):
    with patch(
        "webjam_qt.widgets.recording_studio.list_output_devices",
        return_value=[
            {
                "name": "Very long SSL audio interface output name for layout testing",
                "channels": 2,
                "index": 4,
            }
        ],
    ):
        studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio._set_playback_controls_visible(True)
        studio._live_btn.setVisible(True)
        studio._library.setVisible(True)
        studio.resize(width, height)
        studio.show()
        APP.processEvents()
        assert studio.minimumSizeHint().width() <= width
        assert studio.width() == width
        assert studio.height() == height
        assert studio._inspector.isVisibleTo(studio) is (width >= 1080)
        bounds = studio.contentsRect()
        for widget in (
            studio._record_btn,
            studio._setup_btn,
            studio._output_picker,
            studio._export_btn,
            studio._reveal_btn,
        ):
            top_left = widget.mapTo(studio, widget.rect().topLeft())
            bottom_right = widget.mapTo(studio, widget.rect().bottomRight())
            assert bounds.contains(top_left)
            assert bounds.contains(bottom_right)
        assert studio._export_btn.accessibleName() == ("Export aligned tracks")
    finally:
        studio.shutdown()


@pytest.mark.parametrize(
    ("width", "height"),
    ((760, 600), (1024, 768), (1440, 900)),
)
def test_loaded_schema2_studio_panels_fit_without_overlap(tmp_path, width, height):
    _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.resize(width, height)
        studio.show()
        studio._take_list.setCurrentRow(0)
        APP.processEvents()

        assert studio.size().toTuple() == (width, height)
        assert studio.minimumSizeHint().width() <= width
        assert studio.minimumSizeHint().height() <= height
        assert studio._studio_arrange.height() >= 150
        assert studio._track_scroll.height() >= 88

        visible = {
            "library": studio._library,
            "play": studio._play_btn,
            "stop": studio._stop_btn,
            "scrub": studio._scrub,
            "arrange tools": studio._arrange_toolbar,
            "comp tools": studio._comp_toolbar,
            "output": studio._output_picker,
            "export": studio._export_btn,
            "reveal": studio._reveal_btn,
            "Arrange": studio._studio_arrange,
            "mixer": studio._track_scroll,
            "hint": studio._hint,
        }
        if width >= 1080:
            visible["Inspector"] = studio._inspector
        else:
            visible["Inspector toggle"] = studio._inspector_btn

        bounds = studio.contentsRect()
        rectangles = {
            name: _widget_rect_in(studio, widget) for name, widget in visible.items()
        }
        assert all(widget.isVisibleTo(studio) for widget in visible.values())
        for name, rect in rectangles.items():
            assert bounds.contains(rect), name
        items = list(rectangles.items())
        for index, (left_name, left) in enumerate(items):
            for right_name, right in items[index + 1 :]:
                assert not left.intersects(right), f"{left_name} overlaps {right_name}"
    finally:
        studio.shutdown()


def test_compact_inspector_keyboard_toggle_restores_library_and_resizes(tmp_path):
    _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.resize(760, 600)
        studio.show()
        studio._take_list.setCurrentRow(0)
        APP.processEvents()

        assert studio._library.isVisibleTo(studio)
        assert not studio._inspector.isVisibleTo(studio)
        assert studio._inspector_btn.isVisibleTo(studio)
        assert studio._inspector_btn in studio._studio_tab_order()
        assert studio._inspector_btn.accessibleName() == "Show track details"
        assert "restore the library" in (
            studio._inspector_btn.accessibleDescription().lower()
        )

        studio._inspector_btn.setFocus()
        QTest.keyClick(studio._inspector_btn, Qt.Key.Key_Space)
        APP.processEvents()

        assert studio._inspector_btn.isChecked()
        assert studio._inspector_btn.accessibleName() == "Hide track details"
        assert not studio._library.isVisibleTo(studio)
        assert studio._inspector.isVisibleTo(studio)
        assert studio.width() == 760
        assert studio._studio_arrange.width() >= 480
        assert studio.contentsRect().contains(
            _widget_rect_in(studio, studio._inspector)
        )

        # Growing to the normal three-panel layout closes the drawer and
        # restores the exact library state. Shrinking again must not retain
        # Qt's wider Inspector minimum.
        studio.resize(1440, 900)
        APP.processEvents()
        assert studio._library.isVisibleTo(studio)
        assert studio._inspector.isVisibleTo(studio)
        assert not studio._inspector_btn.isVisibleTo(studio)
        assert not studio._inspector_btn.isChecked()

        studio.resize(760, 600)
        APP.processEvents()
        assert studio.size().toTuple() == (760, 600)
        assert studio._library.isVisibleTo(studio)
        assert not studio._inspector.isVisibleTo(studio)
        assert studio._inspector_btn.isVisibleTo(studio)

        studio._inspector_btn.setFocus()
        QTest.keyClick(studio._inspector_btn, Qt.Key.Key_Space)
        QTest.keyClick(studio._inspector_btn, Qt.Key.Key_Space)
        APP.processEvents()
        assert studio._library.isVisibleTo(studio)
        assert not studio._inspector.isVisibleTo(studio)
        assert studio._inspector_btn.accessibleName() == "Show track details"
    finally:
        studio.shutdown()


def test_studio_visible_enabled_focus_chain_covers_complete_workflow(tmp_path):
    _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio.resize(1024, 768)
        studio.show()
        studio._take_list.setCurrentRow(0)
        APP.processEvents()

        configured = [
            widget
            for widget in studio._studio_tab_order()
            if (
                widget.focusPolicy() != Qt.FocusPolicy.NoFocus
                and widget.isVisibleTo(studio)
                and widget.isEnabled()
            )
        ]
        chain = _visible_enabled_focus_chain(studio, studio._take_list)
        observed = [widget for widget in chain if widget in configured]

        assert observed == configured
        lane = studio._lanes[min(studio._lanes)]
        arrange_canvas = studio._studio_arrange._canvas.viewport()
        core_workflow = (
            studio._take_list,
            studio._play_btn,
            studio._inspector_btn,
            arrange_canvas,
            lane._gain,
            studio._export_btn,
        )
        assert all(widget in configured for widget in core_workflow)
        assert all(widget.accessibleName() for widget in core_workflow)
    finally:
        studio.shutdown()


def test_track_export_failure_keeps_take_available_and_actionable():
    with tempfile.TemporaryDirectory() as tmp:
        take = Path(tmp) / "Take 01"
        take.mkdir()
        _wav(take / "guitar.wav")
        _mark_verified(take, "guitar.wav")
        studio = RecordingStudio(
            tmp,
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            studio._exporting = True
            studio._take_list.setEnabled(False)
            studio._finish_export(None, "destination is read-only")
            assert "original take is safe" in studio._hint.text().lower()
            assert "read-only" not in studio._hint.text()
            assert studio._take_list.isEnabled()
            assert studio._export_btn.isEnabled()
        finally:
            studio.shutdown()


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (
            "WebJam found explicitly silent segments in selected performance tracks: "
            "private-guitar. Review the recording or intentionally deselect the "
            "affected track before export.",
            "explicitly silent segment",
        ),
        (
            "WebJam cannot create a timing-ready track export because these local "
            "originals have no verified timeline alignment: private-guitar. Keep "
            "the Jamulus server track for this take, or align and verify each local "
            "original before export.",
            "no verified timeline alignment",
        ),
    ),
)
def test_track_export_safety_blocks_are_actionable_without_raw_details(error, expected):
    with tempfile.TemporaryDirectory() as tmp:
        take = Path(tmp) / "Take 01"
        take.mkdir()
        _wav(take / "guitar.wav")
        _mark_verified(take, "guitar.wav")
        studio = RecordingStudio(
            tmp,
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            studio._exporting = True
            studio._take_list.setEnabled(False)
            studio._finish_export(None, error)
            hint = studio._hint.text().lower()
            assert expected in hint
            assert "original take is safe" in hint
            assert "private-guitar" not in hint
            assert studio._take_list.isEnabled()
            assert studio._export_btn.isEnabled()
        finally:
            studio.shutdown()


def test_published_but_unsynced_export_remains_revealable(tmp_path):
    take = tmp_path / "Take 01"
    take.mkdir()
    _wav(take / "guitar.wav")
    _mark_verified(take, "guitar.wav")
    published = take / "Studio Exports" / "Studio Export"
    published.mkdir(parents=True)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        finished: list[bool] = []
        studio.export_finished.connect(finished.append)
        studio._exporting = True
        studio._finish_export(None, "directory sync failed", published)

        assert studio._reveal_path == published
        assert studio._reveal_btn.text() == "Show Unverified Export"
        assert "verify sha256sums.txt" in studio._hint.text().lower()
        assert finished == [False]
    finally:
        studio.shutdown()


def test_track_export_choices_are_accessible_and_non_destructive(tmp_path):
    take_dir = tmp_path / "Take 01"
    take_dir.mkdir()
    guitar = take_dir / "guitar.wav"
    drums = take_dir / "drums.wav"
    _wav(guitar, 220)
    _wav(drums, 440)
    guitar_id = "11111111-1111-4111-8111-111111111111"
    drums_id = "22222222-2222-4222-8222-222222222222"
    take = TakeInfo(
        path=take_dir,
        name="Take 01",
        tracks=[
            TrackInfo(guitar, "Guitar", samplerate=RATE, track_id=guitar_id),
            TrackInfo(drums, "Drums", samplerate=RATE, track_id=drums_id),
        ],
        validation_status="complete",
        take_id="33333333-3333-4333-8333-333333333333",
    )
    result = SimpleNamespace(
        folder=take_dir / "Track Export",
        stems=(),
        samplerate=RATE,
    )
    called = threading.Event()
    with (
        patch(
            "webjam_qt.widgets.recording_studio.discover_takes",
            return_value=[take],
        ),
        patch(
            "webjam_qt.widgets.recording_studio.export_track_package",
            side_effect=lambda *_args, **_kwargs: called.set() or result,
        ) as export,
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            guitar_lane = studio._lanes[0]
            drums_lane = studio._lanes[1]
            assert not guitar_lane._track_export_include.isHidden()
            assert guitar_lane._track_export_include.accessibleName() == (
                "Include Guitar in track export"
            )
            assert guitar_lane._track_export_include.isChecked()

            drums_lane._track_export_include.setChecked(False)
            assert "left out" in studio._hint.text().lower()
            assert studio._export_btn.isEnabled()
            studio._export_tracks()
            assert _wait_until(called.is_set)
            assert export.call_args.kwargs["selected_track_ids"] == {guitar_id}
            assert not (take_dir / "webjam-take.json").exists()
        finally:
            studio.shutdown()


def test_track_export_requires_one_included_track_when_selection_is_available(tmp_path):
    take_dir = tmp_path / "Take 01"
    take_dir.mkdir()
    guitar = take_dir / "guitar.wav"
    drums = take_dir / "drums.wav"
    _wav(guitar, 220)
    _wav(drums, 440)
    take = TakeInfo(
        path=take_dir,
        name="Take 01",
        tracks=[
            TrackInfo(
                guitar,
                "Guitar",
                samplerate=RATE,
                track_id="11111111-1111-4111-8111-111111111111",
            ),
            TrackInfo(
                drums,
                "Drums",
                samplerate=RATE,
                track_id="22222222-2222-4222-8222-222222222222",
            ),
        ],
        validation_status="complete",
        take_id="33333333-3333-4333-8333-333333333333",
    )
    with patch(
        "webjam_qt.widgets.recording_studio.discover_takes",
        return_value=[take],
    ):
        studio = RecordingStudio(
            str(tmp_path),
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            studio._lanes[0]._track_export_include.setChecked(False)
            studio._lanes[1]._track_export_include.setChecked(False)
            assert not studio._export_btn.isEnabled()
            assert "choose at least one track" in studio._hint.text().lower()
        finally:
            studio.shutdown()


def test_unverified_take_is_truthfully_labeled_and_cannot_export():
    with tempfile.TemporaryDirectory() as tmp:
        take = Path(tmp) / "Recovered audio"
        take.mkdir()
        _wav(take / "guitar.wav")
        studio = RecordingStudio(
            tmp,
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            assert "Unverified take" in studio._hint.text()
            assert not studio._export_btn.isEnabled()
        finally:
            studio.shutdown()


def test_live_lane_hides_playback_only_pan_control():
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.set_live_participants(
            [
                SimpleNamespace(channel_id=0, name="Jeff", is_local=True),
            ]
        )
        lane = studio._lanes[0]
        assert lane._pan.isHidden()
        assert lane._pan_value.isHidden()
        assert studio._play_btn.isHidden()
        assert studio._output_picker.isHidden()
        assert studio._export_btn.isHidden()
    finally:
        studio.shutdown()


def test_studio_output_is_first_shown_when_a_take_is_opened(tmp_path):
    studio = RecordingStudio(
        tmp_path,
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        # A live jam has no review output decision at all.
        assert studio._output_picker.isHidden()

        take = tmp_path / "Take 01"
        take.mkdir()
        _wav(take / "guitar.wav")
        _mark_verified(take, "guitar.wav")
        studio.reload()
        studio._take_list.setCurrentRow(0)

        assert not studio._output_picker.isHidden()
        assert studio._output_picker.accessibleName() == "Studio playback output"
    finally:
        studio.shutdown()


@pytest.mark.parametrize(
    ("error", "expected", "unexpected"),
    (
        (
            StudioPlaybackSourceError("private source detail"),
            "source media",
            "playback output",
        ),
        (
            PlaybackDeviceError("private device detail"),
            "playback output",
            "source media",
        ),
    ),
)
def test_play_failure_distinguishes_source_media_from_output_device(
    tmp_path,
    error,
    expected,
    unexpected,
):
    take = tmp_path / "Take 01"
    take.mkdir()
    _wav(take / "guitar.wav")
    _mark_verified(take, "guitar.wav")
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        with patch.object(studio._player, "play", side_effect=error):
            studio._toggle_play()

        hint = studio._hint.text().lower()
        assert expected in hint
        assert unexpected not in hint
        assert "private" not in hint
        assert studio._play_btn.text() == "▶ Play"
    finally:
        studio.shutdown()


def test_first_studio_play_prepares_checksums_off_ui_thread(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    sink = _InspectableSink()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=sink),
    )
    ui_thread = threading.get_ident()
    checksum_threads: list[int] = []
    from core import studio_renderer

    original_hash = studio_renderer._sha256_descriptor

    def traced_hash(descriptor, cancel_check=None):
        checksum_threads.append(threading.get_ident())
        return original_hash(descriptor, cancel_check)

    try:
        studio._take_list.setCurrentRow(0)
        with patch.object(studio_renderer, "_sha256_descriptor", traced_hash):
            studio._toggle_play()

            assert studio._play_btn.text() == "Preparing…"
            assert not studio._play_btn.isEnabled()
            assert sink.pull is None
            assert _wait_until(lambda: studio._player.is_playing)

        assert checksum_threads
        assert all(thread_id != ui_thread for thread_id in checksum_threads)
        assert studio._play_btn.text() == "⏸ Pause"
        assert sink.starts == 1

        reload_threads: list[int] = []

        def traced_reload_hash(descriptor, cancel_check=None):
            reload_threads.append(threading.get_ident())
            return original_hash(descriptor, cancel_check)

        with patch.object(
            studio_renderer,
            "_sha256_descriptor",
            traced_reload_hash,
        ):
            studio._reload_arranged_playback()
            assert studio._play_btn.text() == "Preparing…"
            assert not studio._player.is_playing
            assert _wait_until(lambda: studio._player.is_playing)
        assert reload_threads
        assert all(thread_id != ui_thread for thread_id in reload_threads)

        # Stopping closes readers, while the renderer's exact current receipts
        # remain reusable. A second preparation rechecks paths but does not hash.
        studio._stop_playback()
        reused_hashes: list[int] = []

        def unexpected_rehash(descriptor, cancel_check=None):
            reused_hashes.append(descriptor)
            return original_hash(descriptor, cancel_check)

        with patch.object(
            studio_renderer,
            "_sha256_descriptor",
            unexpected_rehash,
        ):
            studio._toggle_play()
            assert studio._play_btn.text() == "Preparing…"
            assert _wait_until(lambda: studio._player.is_playing)
        assert reused_hashes == []
    finally:
        studio.shutdown()


def test_stale_studio_preparation_cannot_start_after_take_switch(tmp_path):
    primary_dir, _track_ids = _schema2_studio_take(tmp_path)
    second_dir, _track_id = _schema2_repeated_take(tmp_path, primary_dir)
    sink = _InspectableSink()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=sink),
    )

    def row_for(path: Path) -> int:
        expected = str(path)
        for row in range(studio._take_list.count()):
            if studio._take_list.item(row).data(Qt.ItemDataRole.UserRole) == expected:
                return row
        raise AssertionError(f"take row not found: {path}")

    try:
        studio._take_list.setCurrentRow(row_for(primary_dir))
        studio._toggle_play()
        future = studio._playback_prepare_future
        assert future is not None
        # Do not process Qt events: let the worker enqueue a successful old
        # result while its generation is still current.
        assert _wait_without_qt_events(future.done)
        assert sink.starts == 0

        studio._take_list.setCurrentRow(row_for(second_dir))
        assert studio._current is not None
        assert studio._current.path == second_dir
        studio._tick()

        assert sink.starts == 0
        assert not studio._player.is_playing
        assert not studio._player.studio_playback_prepared
        assert studio._play_btn.text() == "▶ Play"
    finally:
        studio.shutdown()


def test_arrangement_reload_restarts_pending_autoplay_preparation(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    sink = _InspectableSink()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=sink),
    )
    from core import studio_renderer

    original_hash = studio_renderer._sha256_descriptor
    first_hash_started = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def cancellable_first_hash(descriptor, cancel_check=None):
        nonlocal calls
        with calls_lock:
            calls += 1
            call = calls
        if call == 1:
            first_hash_started.set()
            while True:
                if cancel_check is not None:
                    cancel_check()
                time.sleep(0.005)
        return original_hash(descriptor, cancel_check)

    try:
        studio._take_list.setCurrentRow(0)
        with patch.object(
            studio_renderer,
            "_sha256_descriptor",
            cancellable_first_hash,
        ):
            studio._toggle_play()
            assert _wait_without_qt_events(first_hash_started.is_set)
            first_generation = studio._playback_prepare_generation

            # An edit reload while the requested Play is still preparing must
            # cancel the old work and carry that autoplay intent forward.
            studio._reload_arranged_playback()

            assert studio._playback_prepare_generation > first_generation
            assert studio._play_btn.text() == "Preparing…"
            assert _wait_until(lambda: studio._player.is_playing)

        assert sink.starts == 1
        assert studio._play_btn.text() == "⏸ Pause"
    finally:
        studio.shutdown()


def test_arrange_seek_preserves_integer_frame_and_catches_replaced_source(tmp_path):
    take_dir, _track_ids = _schema2_studio_take(tmp_path)
    sink = _InspectableSink()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=sink),
    )
    try:
        studio._take_list.setCurrentRow(0)

        studio._seek_from_arrange(27)
        assert studio._player.position_frame == 27
        assert studio._studio_arrange._playhead_frame == 27

        studio._toggle_play()
        assert studio._play_btn.text() == "Preparing…"
        assert _wait_until(lambda: sink.pull is not None)
        assert float(np.max(np.abs(sink.pull(1)))) > 0.0
        stops_before_failure = sink.stops

        source = take_dir / "media" / "server.wav"
        parked = take_dir / "media" / "server-original.wav"
        replacement = take_dir / "media" / "server-replacement.wav"
        _wav(replacement, 880)
        os.replace(source, parked)
        os.replace(replacement, source)

        # This is a Qt signal target: a typed source failure must be handled
        # here instead of escaping into the event loop.
        studio._seek_from_arrange(27)

        assert sink.stops > stops_before_failure
        assert studio._player.position_frame == 0
        assert studio._player._studio_stream is None
        assert studio._play_btn.text() == "▶ Play"
        hint = studio._hint.text().lower()
        assert "source media" in hint
        assert "server.wav" not in hint
    finally:
        studio.shutdown()


def test_midstream_source_error_drains_on_ui_tick_and_resets_meters(tmp_path):
    take_dir, _track_ids = _schema2_studio_take(tmp_path)
    sink = _InspectableSink()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=sink),
    )
    try:
        studio._take_list.setCurrentRow(0)
        studio._toggle_play()
        assert studio._play_btn.text() == "Preparing…"
        assert _wait_until(lambda: sink.pull is not None)
        assert float(np.max(np.abs(sink.pull(4)))) > 0.0
        stops_before_failure = sink.stops
        buffered_limit = studio._player.studio_buffer_capacity_frames
        assert 0 < buffered_limit <= max(RATE // 10, studio._player.blocksize)

        del take_dir
        opened = next(iter(studio._player._studio_stream._readers.values()))
        opened.reader.close()

        # Descriptor-verified audio already in the bounded queue remains valid.
        # Drain at most the bounded payload (100 ms or one configured callback
        # quantum); the producer then reports one typed error and every later
        # device pull is safe silence.
        delivered_frames = 0
        deadline = time.monotonic() + 1.0
        while (
            not studio._player._studio_terminal_notifications
            and time.monotonic() < deadline
        ):
            block = sink.pull(4)
            if float(np.max(np.abs(block))) > 0.0:
                delivered_frames += len(block)
            time.sleep(0.001)
        assert delivered_frames <= buffered_limit
        assert studio._player._studio_terminal_notifications
        assert studio._player.terminal_error is None
        np.testing.assert_array_equal(
            sink.pull(4),
            np.zeros((4, 2), dtype=np.float32),
        )
        assert sink.stops == stops_before_failure

        studio._tick()

        assert sink.stops > stops_before_failure
        assert studio._player.terminal_error is None
        assert studio._player._studio_stream is None
        assert studio._player.position_frame == 0
        assert studio._play_btn.text() == "▶ Play"
        assert studio._master_meter._left == pytest.approx(0.0)
        assert studio._master_meter._right == pytest.approx(0.0)
        assert all(
            lane._meter._left == pytest.approx(0.0)
            and lane._meter._right == pytest.approx(0.0)
            for lane in studio._lanes.values()
        )
        assert "source media" in studio._hint.text().lower()
    finally:
        studio.shutdown()


def test_studio_audio_callback_never_waits_on_ui_meter_lock(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    sink = _InspectableSink()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, blocksize=64, sink=sink),
    )
    try:
        studio._take_list.setCurrentRow(0)
        studio._toggle_play()
        assert _wait_until(lambda: sink.pull is not None)

        rendered: list[np.ndarray] = []
        durations: list[float] = []

        def device_pull() -> None:
            started = time.monotonic()
            rendered.append(sink.pull(64))
            durations.append(time.monotonic() - started)

        studio._level_lock.acquire()
        callback = threading.Thread(
            target=device_pull,
            name="test-portaudio-ui-lock-isolation",
        )
        callback.start()
        callback.join(0.2)
        blocked_on_ui = callback.is_alive()
        studio._level_lock.release()
        callback.join(1.0)

        assert not blocked_on_ui
        assert not callback.is_alive()
        assert durations[0] < 0.1
        assert float(np.max(np.abs(rendered[0]))) > 0.0
        assert studio._pending_levels == {}
        assert studio._pending_stereo_levels == {}
        assert studio._pending_master_level is None
        assert studio._player.studio_pending_notifications == 1

        studio._tick()

        assert studio._player.studio_pending_notifications == 0
        assert studio._master_meter._left > 0.0
        assert any(lane._meter._left > 0.0 for lane in studio._lanes.values())
    finally:
        if studio._level_lock.locked():
            studio._level_lock.release()
        studio.shutdown()


def test_pause_clears_meters_and_old_epoch_cannot_stop_resumed_playback(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    sink = _InspectableSink()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=sink),
    )
    try:
        studio._take_list.setCurrentRow(0)
        studio._toggle_play()
        assert _wait_until(lambda: sink.pull is not None)
        assert float(np.max(np.abs(sink.pull(64)))) > 0.0
        studio._tick()
        assert studio._master_meter._left > 0.0
        assert any(lane._meter._left > 0.0 for lane in studio._lanes.values())

        old_epoch = studio._player.playback_epoch
        paused_frame = studio._player.position_frame
        studio._toggle_play()

        assert studio._player.position_frame == paused_frame
        assert studio._master_meter._left == pytest.approx(0.0)
        assert studio._master_meter._right == pytest.approx(0.0)
        assert all(
            lane._meter._left == pytest.approx(0.0)
            and lane._meter._right == pytest.approx(0.0)
            for lane in studio._lanes.values()
        )

        studio._toggle_play()
        assert studio._player.is_playing
        assert studio._player.playback_epoch > old_epoch
        studio._on_stereo_levels_bg(old_epoch, {0: (0.9, 0.8, True)})
        studio._on_master_level_bg(old_epoch, (0.9, 0.8, True))
        studio._on_finished_bg(old_epoch)
        studio._on_playback_error_bg(
            old_epoch,
            StudioPlaybackSourceError("private stale source detail"),
        )

        studio._tick()

        assert studio._player.is_playing
        assert studio._play_btn.text() == "⏸ Pause"
        assert studio._master_meter._left == pytest.approx(0.0)
        assert "source media" not in studio._hint.text().lower()
    finally:
        studio.shutdown()


def test_output_change_resets_playback_label_and_position():
    with tempfile.TemporaryDirectory() as tmp:
        take = Path(tmp) / "Take 01"
        take.mkdir()
        _wav(take / "guitar.wav")
        _mark_verified(take, "guitar.wav")
        studio = RecordingStudio(
            tmp,
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            studio._toggle_play()
            assert studio._play_btn.text() == "⏸ Pause"
            studio._scrub.setValue(500)
            studio._on_output_changed(0)
            assert studio._play_btn.text() == "▶ Play"
            assert studio._scrub.value() == 0
            assert not studio._player.is_playing
        finally:
            studio.shutdown()


def test_output_change_closes_stale_schema2_preparation_without_autoplay(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    sink = _InspectableSink()
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=sink),
    )
    try:
        studio._timer.stop()
        studio._take_list.setCurrentRow(0)
        studio._toggle_play()
        future = studio._playback_prepare_future
        assert future is not None
        assert _wait_without_qt_events(future.done)

        # Inspect and requeue the completed descriptor-bound result before the
        # UI has a chance to install it. The output change must invalidate it,
        # and the normal drain must close every prepared reader.
        outcome = studio._playback_prepare_results.get_nowait()
        preparation = outcome.preparation
        assert preparation is not None
        stream = preparation.stream
        assert stream._readers
        assert not stream._closed
        studio._playback_prepare_results.put(outcome)

        studio._output_picker.addItem("Second Studio output", "second-output")
        studio._output_picker.setCurrentIndex(studio._output_picker.count() - 1)

        assert studio._play_btn.text() == "▶ Play"
        assert studio._scrub.value() == 0
        assert not studio._player.is_playing
        assert sink.starts == 0

        studio._tick()

        assert sink.starts == 0
        assert not studio._player.studio_playback_prepared
        assert stream._closed
        assert not stream._readers
    finally:
        studio.shutdown()


def test_leaving_studio_stops_playback_and_releases_output():
    with tempfile.TemporaryDirectory() as tmp:
        take = Path(tmp) / "Take 01"
        take.mkdir()
        _wav(take / "guitar.wav")
        _mark_verified(take, "guitar.wav")
        sink = _InspectableSink()
        studio = RecordingStudio(
            tmp,
            player=TakePlayer(samplerate=RATE, sink=sink),
        )
        stack = QStackedWidget()
        other = QWidget()
        stack.addWidget(studio)
        stack.addWidget(other)
        try:
            stack.setCurrentWidget(studio)
            stack.show()
            APP.processEvents()
            studio._take_list.setCurrentRow(0)
            studio._toggle_play()
            assert studio._player.is_playing
            assert sink.starts == 1
            stops_before_hide = sink.stops

            stack.setCurrentWidget(other)
            APP.processEvents()

            assert not studio._player.is_playing
            assert sink.stops == stops_before_hide + 1
        finally:
            studio.shutdown()
            stack.close()


def test_missing_manifest_track_has_lane_and_blocks_track_export():
    with tempfile.TemporaryDirectory() as tmp:
        take = Path(tmp) / "Take 01"
        take.mkdir()
        _wav(take / "host.wav")
        (take / "webjam-take.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "complete",
                    "tracks": [
                        {
                            "filename": "host.wav",
                            "name": "Host",
                            "source": "jamulus_server",
                        },
                        {
                            "filename": "guest.wav",
                            "name": "Guest",
                            "source": "jamulus_server",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        studio = RecordingStudio(
            tmp,
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            guest_lane = next(
                lane for lane in studio._lanes.values() if lane._name.text() == "Guest"
            )
            assert "MISSING MEDIA" in guest_lane._detail.text()
            assert "1 missing" in studio._subtitle.text()
            assert "needs review" in studio._hint.text()
            assert "Guest is missing" not in studio._hint.text()
            assert not studio._export_btn.isEnabled()
            assert studio._current.validation_status == "needs_attention"
        finally:
            studio.shutdown()


def test_studio_hides_raw_manifest_and_completion_findings(tmp_path):
    take = tmp_path / "Take 01"
    take.mkdir()
    _wav(take / "host.wav")
    private_path = "/Users/jeff/private/recordings/host.wav"
    secret = "Bearer take-secret-123"
    manifest_path = take / "webjam-take.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "needs_attention",
                "errors": [f"capture failed at {private_path}: {secret}"],
                "warnings": [f"warning for {private_path}: {secret}"],
                "tracks": [
                    {
                        "filename": "host.wav",
                        "name": "Host",
                        "source": "jamulus_server",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        rendered = "\n".join(
            [label.text() for label in studio.findChildren(QLabel)]
            + [
                studio._take_list.item(row).text()
                for row in range(studio._take_list.count())
            ]
        )
        assert "needs review" in rendered.lower()
        assert private_path not in rendered
        assert secret not in rendered
        assert "capture failed" not in rendered
        assert secret in manifest_path.read_text(encoding="utf-8")

        studio.on_take_completed(
            take,
            SimpleNamespace(
                errors=(f"completion failed at {private_path}: {secret}",),
                warnings=(f"warning at {private_path}",),
            ),
        )
        assert "needs review" in studio._hint.text().lower()
        assert private_path not in studio._hint.text()
        assert secret not in studio._hint.text()
        assert "completion failed" not in studio._hint.text()
    finally:
        studio.shutdown()


def test_simple_settings_changes_preferences_without_connection_plumbing(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
        jamulus_port=22124,
        server_rpc_port=22240,
    )
    dialog = SimpleSettingsDialog(settings)
    assert dialog._error.isHidden()
    dialog._name.setText("Jeff — Guitar")
    dialog._video.clear()
    dialog._save()
    data = json.loads(Path(settings.config_file).read_text())
    assert data["host_server_enabled"] is True
    assert data["jamulus_server"] == "127.0.0.1"
    assert data["jamulus_port"] == 22124
    assert data["server_rpc_port"] == 22240
    assert "webex_audio_mode" not in data
    assert data["local_capture_enabled"] is False
    assert data["musician_name"] == "Jeff — Guitar"


def test_simple_settings_does_not_disable_existing_recording_setup(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        local_capture_enabled=True,
        audio_input_device_index=7,
    )
    dialog = SimpleSettingsDialog(settings)
    dialog._save()
    data = json.loads(Path(settings.config_file).read_text())
    assert data["local_capture_enabled"] is True
    assert data["audio_input_device_index"] == 7


def test_simple_settings_has_no_live_audio_choices_and_opens_jamulus(tmp_path):
    dialog = SimpleSettingsDialog(
        AppSettings(config_file=str(tmp_path / "settings.json"))
    )
    opened = MagicMock()
    dialog.audio_settings_requested.connect(opened)

    assert not hasattr(dialog, "_input")
    assert not hasattr(dialog, "_output")
    assert dialog._open_jamulus.text() == "Open Jamulus Audio Settings"
    dialog._open_jamulus.click()
    opened.assert_called_once()


def test_simple_settings_preserves_legacy_live_route_without_displaying_it(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        jamulus_audio_input_uid="coreaudio-input",
        jamulus_audio_output_uid="coreaudio-output",
        audio_input_device_index=7,
        take_playback_output_device="Studio Monitors",
    )
    dialog = SimpleSettingsDialog(settings)
    dialog._save()

    data = json.loads(Path(settings.config_file).read_text())
    assert data["jamulus_audio_input_uid"] == "coreaudio-input"
    assert data["jamulus_audio_output_uid"] == "coreaudio-output"
    assert data["audio_input_device_index"] == 7
    assert data["take_playback_output_device"] == "Studio Monitors"


def test_simple_settings_saves_the_selected_draft_before_running_band_check(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        musician_name="Old Name",
    )
    dialog = SimpleSettingsDialog(settings)
    dialog._name.setText("New Name")

    dialog._save_and_run_band_check()

    saved = json.loads(Path(settings.config_file).read_text(encoding="utf-8"))
    assert saved["musician_name"] == "New Name"
    assert dialog.run_band_check_after_save is True
    assert dialog.result() == dialog.DialogCode.Accepted


def test_simple_settings_keeps_optional_conversation_link_out_of_the_way(tmp_path):
    dialog = SimpleSettingsDialog(
        AppSettings(config_file=str(tmp_path / "settings.json"))
    )
    assert dialog._conversation_body.isHidden()
    dialog._conversation_toggle.click()
    assert not dialog._conversation_body.isHidden()


def test_simple_settings_contains_no_blackhole_or_rpc_language(tmp_path):
    dialog = SimpleSettingsDialog(
        AppSettings(config_file=str(tmp_path / "settings.json"))
    )
    rendered = " ".join(widget.text() for widget in dialog.findChildren(QLabel)).lower()
    assert "blackhole" not in rendered
    assert "rpc" not in rendered
    assert re.search(r"\bport\b", rendered) is None


def test_recording_setup_saves_default_capture_without_moving_studio_output(
    tmp_path,
):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        takes_directory=str(tmp_path / "takes"),
        take_playback_output_device="Studio Monitors",
    )
    with patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[
            {"name": "Webcam Mic", "channels": 1, "index": 2},
            {"name": "SSL 2+", "channels": 2, "index": 7},
        ],
    ):
        dialog = RecordingSetupDialog(settings)
    assert dialog._error.isHidden()
    assert not hasattr(dialog, "_output")
    dialog._capture.setChecked(True)
    dialog._save()
    data = json.loads(Path(settings.config_file).read_text())
    assert data["take_playback_output_device"] == "Studio Monitors"
    assert data["local_capture_enabled"] is True
    assert data["local_capture_choice_made"] is True
    assert data["audio_input_device_index"] == 7


def test_recording_setup_validates_configured_channel_count_and_opt_out(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        local_capture_enabled=True,
        audio_input_device_index=2,
        input_maps=[
            {
                "name": "Voice",
                "channels": 1,
                "enabled": True,
                "local_original_enabled": True,
            }
        ],
    )
    with patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[{"name": "Mono USB", "channels": 1, "index": 2}],
    ):
        dialog = RecordingSetupDialog(settings)

    dialog._save()
    saved = json.loads(Path(settings.config_file).read_text())
    assert saved["audio_input_device_index"] == 2
    assert saved["input_maps"][0]["name"] == "Voice"

    opted_out = AppSettings(
        config_file=str(tmp_path / "opted-out.json"),
        local_capture_enabled=True,
        audio_input_device_index=2,
        input_maps=[
            {
                "name": "Guide",
                "channels": 1,
                "enabled": True,
                "local_original_enabled": False,
            }
        ],
    )
    with patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[{"name": "Mono USB", "channels": 1, "index": 2}],
    ):
        opted_out_dialog = RecordingSetupDialog(opted_out)

    opted_out_dialog._save()
    assert not opted_out_dialog._error.isHidden()
    assert "Turn on at least one Local Original" in opted_out_dialog._error.text()
    assert not Path(opted_out.config_file).exists()


def test_recording_setup_refuses_interface_with_too_few_mapped_inputs(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        local_capture_enabled=True,
        audio_input_device_index=7,
        input_maps=[
            {
                "name": "Room",
                "channels": 2,
                "enabled": True,
                "local_original_enabled": True,
            }
        ],
    )
    with patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[{"name": "Mono USB", "channels": 1, "index": 7}],
    ):
        dialog = RecordingSetupDialog(settings)

    dialog._save()

    assert not dialog._error.isHidden()
    assert "needs 2 input channels" in dialog._error.text()
    assert not Path(settings.config_file).exists()


def test_local_originals_choice_is_a_recording_time_decision():
    dialog = LocalOriginalsChoiceDialog()

    assert "shared Jamulus take" in " ".join(
        widget.text() for widget in dialog.findChildren(QLabel)
    )
    dialog._record_shared()

    assert dialog.choice == "shared"
    assert dialog.result() == dialog.DialogCode.Accepted

    local = LocalOriginalsChoiceDialog()
    local._configure_local()
    assert local.choice == "local"
    assert local.result() == local.DialogCode.Accepted


def test_recording_setup_preserves_explicit_joiner_local_original_preference(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=False,
        local_capture_enabled=True,
    )
    with patch("webjam_qt.windows.recording_setup.list_input_devices", return_value=[]):
        dialog = RecordingSetupDialog(settings)
    assert dialog._capture.isEnabled()
    assert dialog._capture.isChecked()
    assert not dialog._input.isHidden()
    assert "host confirms a take" in dialog._capture_help.text()


def test_recording_setup_keeps_compact_content_scrollable_and_footer_visible(
    tmp_path,
):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        local_capture_enabled=True,
        audio_input_device_index=7,
        takes_directory=str(tmp_path / "takes"),
        input_maps=[
            {
                "name": "Lead Vocal",
                "channels": 1,
                "enabled": True,
                "local_original_enabled": True,
            },
            {
                "name": "Stereo Keys",
                "channels": 2,
                "enabled": True,
                "local_original_enabled": True,
            },
        ],
    )
    with patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[{"name": "Studio 8", "channels": 8, "index": 7}],
    ):
        dialog = RecordingSetupDialog(settings)
    dialog.resize(620, 440)
    dialog.show()
    APP.processEvents()

    scroll = dialog.findChild(QScrollArea, "RecordingSetupScrollArea")
    save = next(
        button
        for button in dialog.findChildren(QPushButton)
        if button.text() == "Save Recording Setup"
    )
    assert scroll is not None
    for label in (dialog._capture_help, dialog._tracks_summary):
        assert label.height() >= label.heightForWidth(label.width())
    assert save.isVisibleTo(dialog)
    assert save.geometry().bottom() <= dialog.contentsRect().bottom()

    dialog.resize(620, 360)
    APP.processEvents()
    assert scroll.verticalScrollBar().maximum() > 0
    assert save.isVisibleTo(dialog)


def test_legacy_invite_disables_false_local_original_claim(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=False,
        local_capture_enabled=True,
    )
    with patch("webjam_qt.windows.recording_setup.list_input_devices", return_value=[]):
        dialog = RecordingSetupDialog(
            settings,
            local_originals_available=False,
        )
    assert not dialog._capture.isEnabled()
    assert not dialog._capture.isChecked()
    assert not dialog._capture_unavailable.isHidden()
    assert "unavailable for this session" in dialog._capture_unavailable.text()

    dialog._save()

    # The v1 session ignores capture without erasing a musician's opt-in for a
    # later Host or v2 session.
    assert settings.local_capture_enabled is True


def test_recording_setup_failed_save_does_not_mutate_live_settings(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        take_playback_output_device="Old Output",
        local_capture_enabled=False,
    )
    with patch("webjam_qt.windows.recording_setup.list_input_devices", return_value=[]):
        dialog = RecordingSetupDialog(settings)

    with patch(
        "webjam_qt.windows.recording_setup.save_settings",
        side_effect=OSError("token=do-not-show /tmp/settings"),
    ):
        dialog._save()

    assert settings.take_playback_output_device == "Old Output"
    assert settings.local_capture_enabled is False
    assert not dialog._error.isHidden()
    assert "do-not-show" not in dialog._error.text()


def test_recording_setup_can_choose_a_new_takes_folder(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "old-takes"),
    )
    chosen = tmp_path / "new-takes"
    with patch("webjam_qt.windows.recording_setup.list_input_devices", return_value=[]):
        dialog = RecordingSetupDialog(settings)

    with patch(
        "webjam_qt.windows.recording_setup.QFileDialog.getExistingDirectory",
        return_value=str(chosen),
    ):
        dialog._choose_folder()
    dialog._save()

    saved = json.loads(Path(settings.config_file).read_text())
    assert saved["takes_directory"] == str(chosen)
    assert str(chosen) in dialog._folder.text()
    # Dialog edits remain a draft until the controller reloads the saved file.
    assert settings.takes_directory == str(tmp_path / "old-takes")


def test_simple_settings_failed_save_does_not_mutate_live_settings(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        musician_name="Old Name",
        webex_url="",
    )
    dialog = SimpleSettingsDialog(settings)
    dialog._name.setText("New Name")

    with patch(
        "webjam_qt.windows.simple_settings.save_settings",
        side_effect=OSError("token=do-not-show /tmp/settings"),
    ):
        dialog._save()

    assert settings.musician_name == "Old Name"
    assert settings.webex_url == ""
    assert not dialog._error.isHidden()
    assert "do-not-show" not in dialog._error.text()


def test_invite_chooses_a_non_loopback_address():
    sock = MagicMock()
    sock.getsockname.return_value = ("192.168.1.42", 49152)
    with (
        patch("core.network_invite.sys.platform", "linux"),
        patch("core.network_invite.socket.socket", return_value=sock),
        patch(
            "core.network_invite.socket.gethostbyname_ex",
            return_value=("host", [], ["127.0.0.1"]),
        ),
    ):
        assert local_band_address() == "192.168.1.42"
    sock.close.assert_called_once()


def test_reset_mix_is_one_undo_and_preserves_export_choices(tmp_path):
    _take_dir, (track_id, _local_id) = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        lane = studio._lanes[0]
        lane._gain.setValue(180)
        lane._pan.setValue(-40)
        lane._mute.setChecked(True)
        studio._master_gain.setValue(220)
        lane._track_export_include.setChecked(False)

        studio._on_reset_mix()
        state = studio._studio_state.state_for(track_id)
        assert state.fader_gain == pytest.approx(1.0)
        assert state.pan == pytest.approx(0.0)
        assert state.muted is False
        assert state.solo is False
        assert studio._studio_state.master.gain == pytest.approx(1.0)
        # Export choices must survive a mix reset.
        assert state.export_included is False
        # Widgets and player follow the document.
        assert lane._gain.value() == 100
        assert lane._mute.isChecked() is False
        assert studio._master_gain.value() == 100
        assert studio._player.tracks[0].gain == pytest.approx(1.0)

        # A no-op reset performs no edit (no extra undo entry).
        before = studio._studio_controller.document
        studio._on_reset_mix()
        assert studio._studio_controller.document is before

        # One undo restores the whole pre-reset mix.
        studio._undo_arrange_edit()
        restored = studio._studio_state.state_for(track_id)
        assert restored.fader_gain == pytest.approx(1.8)
        assert restored.pan == pytest.approx(-0.4)
        assert restored.muted is True
        assert studio._studio_state.master.gain == pytest.approx(2.2)
    finally:
        studio.shutdown()


def test_overload_latch_is_sticky_within_a_take_and_clears_on_transport(tmp_path):
    _take_dir, _track_ids = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        studio._take_list.setCurrentRow(0)
        studio._toggle_play()
        assert _wait_until(lambda: studio._player.is_playing)
        epoch = studio._player.playback_epoch
        channel = next(iter(studio._lanes))

        # A single mid-take clip on one lane and the master.
        studio._on_stereo_levels_bg(epoch, {channel: (0.9, 0.9, True)})
        studio._on_master_level_bg(epoch, (0.9, 0.9, True))
        studio._tick()
        master_over, clipped = studio.overloaded_sources()
        assert master_over is True
        assert channel in clipped
        assert studio._master_meter._clipped is True
        assert studio._lanes[channel]._meter._clipped is True

        # The very next tick reports no clip, but the latch stays lit.
        studio._on_stereo_levels_bg(epoch, {channel: (0.2, 0.2, False)})
        studio._on_master_level_bg(epoch, (0.2, 0.2, False))
        studio._tick()
        master_over, clipped = studio.overloaded_sources()
        assert master_over is True
        assert channel in clipped
        assert studio._lanes[channel]._meter._clipped is True

        # Restarting transport (new epoch) clears the latch.
        studio._toggle_play()  # pause
        studio._toggle_play()  # resume -> new epoch
        new_epoch = studio._player.playback_epoch
        assert new_epoch != epoch
        studio._on_stereo_levels_bg(new_epoch, {channel: (0.2, 0.2, False)})
        studio._on_master_level_bg(new_epoch, (0.2, 0.2, False))
        studio._tick()
        master_over, clipped = studio.overloaded_sources()
        assert master_over is False
        assert clipped == ()
    finally:
        studio.shutdown()


def test_completed_take_is_auto_selected_and_loaded_in_studio(tmp_path):
    """Phase 11 guard: finalization surfaces the take, ready to review.

    on_take_completed reloads the take list, selects the finished take,
    and the selection loads it into Studio — so a musician who finishes a
    take lands on it without hunting. (The controller separately switches
    the live surface to the Studio view on the same completion.)
    """

    take_dir, _ids = _schema2_studio_take(tmp_path)
    studio = RecordingStudio(
        str(tmp_path),
        player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    try:
        # Nothing selected until a take completes.
        studio._take_list.setCurrentRow(-1)
        studio.on_take_completed(
            take_dir,
            SimpleNamespace(errors=(), warnings=()),
        )
        assert studio._current is not None
        assert str(studio._current.path) == str(take_dir)
        selected = studio._take_list.currentItem()
        assert selected is not None
        assert selected.data(Qt.ItemDataRole.UserRole) == str(take_dir)
    finally:
        studio.shutdown()
