from __future__ import annotations

import json
import os
import struct
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QWidget

from core.settings import AppSettings
from core.network_invite import local_band_address
from core.take_player import TakePlayer
from webjam_qt.widgets.recording_studio import (
    _CompositeWaveformSpec,
    _WaveformSegmentSpec,
    RecordingStudio,
    _WaveformPeakCache,
    _composite_waveform_peaks,
    _waveform_peaks,
    _waveform_source_key,
)
from webjam_qt.windows.recording_setup import RecordingSetupDialog
from webjam_qt.windows.simple_settings import SimpleSettingsDialog


APP = QApplication.instance() or QApplication([])
RATE = 8000


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
                    block[frame - start] = int(
                        max(-1.0, min(1.0, amplitude)) * 32767
                    )
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


def _mark_verified(take: Path, *filenames: str) -> None:
    (take / "webjam-take.json").write_text(
        json.dumps({
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
        }),
        encoding="utf-8",
    )


def test_live_session_arms_one_lane_per_musician():
    studio = RecordingStudio(player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    try:
        studio.set_can_record(True)
        studio.set_live_participants([
            SimpleNamespace(channel_id=0, name="Jeff", is_local=True),
            SimpleNamespace(channel_id=3, name="Sam", is_local=False),
        ])
        studio.set_recording_phase("recording")
        assert set(studio._lanes) == {0, 3}
        assert studio._record_btn.text() == "■ Stop"
        assert "one track per musician" in studio._phase.text()
        assert "ARMED · you" in studio._lanes[0]._detail.text()
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
                lambda: all(
                    lane.waveform._peaks for lane in studio._lanes.values()
                )
            )
            lane = studio._lanes[0]
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
            studio.set_local_originals_directory(
                tmp_path / "WebJam Local Originals"
            )
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
                index for index, take in enumerate(studio._takes)
                if take.path == first
            )
            second_row = next(
                index for index, take in enumerate(studio._takes)
                if take.path == second
            )
            studio._take_list.setCurrentRow(first_row)
            assert first_started.wait(timeout=1.0)
            stale_generation = studio._waveform_generation

            studio._take_list.setCurrentRow(second_row)

            assert first_cancelled.wait(timeout=1.0)
            assert _wait_until(
                lambda: studio._lanes[0].waveform._peaks == (0.35,)
            )
            # Even a late/non-cooperative producer cannot overwrite the new
            # lane because results carry the selection generation.
            studio._waveform_results.put((
                stale_generation,
                0,
                first / "track.wav",
                _waveform_source_key(first / "track.wav"),
                (0.95,),
            ))
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


def test_studio_actions_fit_the_supported_compact_workspace():
    with patch(
        "webjam_qt.widgets.recording_studio.list_output_devices",
        return_value=[{
            "name": "Very long SSL audio interface output name for layout testing",
            "channels": 2,
            "index": 4,
        }],
    ):
        studio = RecordingStudio(
            player=TakePlayer(samplerate=RATE, sink=_SilentSink())
        )
    try:
        studio._set_playback_controls_visible(True)
        studio._live_btn.setVisible(True)
        studio._library.setVisible(True)
        studio.resize(760, 600)
        studio.show()
        APP.processEvents()
        assert studio.minimumSizeHint().width() <= 760
        assert studio.width() == 760
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
        assert studio._export_btn.accessibleName() == (
            "Export aligned stems for Logic Pro"
        )
    finally:
        studio.shutdown()


def test_logic_export_failure_keeps_take_available_and_actionable():
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
        studio.set_live_participants([
            SimpleNamespace(channel_id=0, name="Jeff", is_local=True),
        ])
        lane = studio._lanes[0]
        assert lane._pan.isHidden()
        assert lane._pan_value.isHidden()
        assert studio._play_btn.isHidden()
        assert studio._output_picker.isHidden()
        assert studio._export_btn.isHidden()
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


def test_missing_manifest_track_has_lane_and_blocks_logic_export():
    with tempfile.TemporaryDirectory() as tmp:
        take = Path(tmp) / "Take 01"
        take.mkdir()
        _wav(take / "host.wav")
        (take / "webjam-take.json").write_text(json.dumps({
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
        }), encoding="utf-8")
        studio = RecordingStudio(
            tmp,
            player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
        )
        try:
            studio._take_list.setCurrentRow(0)
            guest_lane = next(
                lane for lane in studio._lanes.values()
                if lane._name.text() == "Guest"
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
    assert data["webex_audio_mode"] == "talkback"
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


def test_simple_settings_exposes_basic_audio_choices_and_saves_them(tmp_path):
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    with patch(
        "webjam_qt.windows.simple_settings.list_input_devices",
        return_value=[{"name": "SSL 2+", "channels": 2, "index": 7}],
    ), patch(
        "webjam_qt.windows.simple_settings.list_output_devices",
        return_value=[{"name": "Studio Monitors", "channels": 2, "index": 4}],
    ):
        dialog = SimpleSettingsDialog(settings)

    assert dialog._input.findData(7) >= 0
    assert dialog._output.findData("Studio Monitors") >= 0
    dialog._input.setCurrentIndex(dialog._input.findData(7))
    dialog._output.setCurrentIndex(dialog._output.findData("Studio Monitors"))
    dialog._save()

    data = json.loads(Path(settings.config_file).read_text())
    assert data["audio_input_device_index"] == 7
    assert data["take_playback_output_device"] == "Studio Monitors"


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
    rendered = " ".join(
        widget.text() for widget in dialog.findChildren(QLabel)
    ).lower()
    assert "blackhole" not in rendered
    assert "rpc" not in rendered
    assert "port" not in rendered


def test_recording_setup_saves_output_and_two_channel_capture(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        takes_directory=str(tmp_path / "takes"),
    )
    with patch(
        "webjam_qt.windows.recording_setup.list_output_devices",
        return_value=[{"name": "SSL 2+", "channels": 2, "index": 4}],
    ), patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[
            {"name": "Webcam Mic", "channels": 1, "index": 2},
            {"name": "SSL 2+", "channels": 2, "index": 7},
        ],
    ):
        dialog = RecordingSetupDialog(settings)
    assert dialog._error.isHidden()
    dialog._output.setCurrentIndex(dialog._output.findData("SSL 2+"))
    dialog._capture.setChecked(True)
    dialog._save()
    data = json.loads(Path(settings.config_file).read_text())
    assert data["take_playback_output_device"] == "SSL 2+"
    assert data["local_capture_enabled"] is True
    assert data["audio_input_device_index"] == 7


def test_recording_setup_preserves_explicit_joiner_local_original_preference(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=False,
        local_capture_enabled=True,
    )
    with patch(
        "webjam_qt.windows.recording_setup.list_output_devices", return_value=[]
    ), patch(
        "webjam_qt.windows.recording_setup.list_input_devices", return_value=[]
    ):
        dialog = RecordingSetupDialog(settings)
    assert dialog._capture.isEnabled()
    assert dialog._capture.isChecked()
    assert not dialog._input.isHidden()
    assert "host confirms a take" in dialog._capture_help.text()


def test_legacy_invite_disables_false_local_original_claim(tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=False,
        local_capture_enabled=True,
    )
    with patch(
        "webjam_qt.windows.recording_setup.list_output_devices", return_value=[]
    ), patch(
        "webjam_qt.windows.recording_setup.list_input_devices", return_value=[]
    ):
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
    with patch(
        "webjam_qt.windows.recording_setup.list_output_devices",
        return_value=[{"name": "New Output", "channels": 2, "index": 3}],
    ), patch(
        "webjam_qt.windows.recording_setup.list_input_devices", return_value=[]
    ):
        dialog = RecordingSetupDialog(settings)
    dialog._output.setCurrentIndex(dialog._output.findData("New Output"))

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
    with patch(
        "webjam_qt.windows.recording_setup.list_output_devices", return_value=[]
    ), patch(
        "webjam_qt.windows.recording_setup.list_input_devices", return_value=[]
    ):
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
    with patch("core.network_invite.sys.platform", "linux"), patch(
        "core.network_invite.socket.socket", return_value=sock
    ), patch(
        "core.network_invite.socket.gethostbyname_ex",
        return_value=("host", [], ["127.0.0.1"]),
    ):
        assert local_band_address() == "192.168.1.42"
    sock.close.assert_called_once()
