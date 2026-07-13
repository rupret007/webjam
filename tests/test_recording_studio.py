from __future__ import annotations

import json
import os
import struct
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication, QLabel

from core.settings import AppSettings
from core.network_invite import local_band_address
from core.take_player import TakePlayer
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.windows.recording_setup import RecordingSetupDialog
from webjam_qt.windows.simple_settings import SimpleSettingsDialog


APP = QApplication.instance() or QApplication([])
RATE = 8000


class _SilentSink:
    def start(self, samplerate, blocksize, pull):
        self.pull = pull

    def stop(self):
        pass


def _wav(path: Path, frequency: float = 220.0) -> None:
    frames = np.sin(np.arange(RATE) * frequency * 2 * np.pi / RATE) * 0.35
    samples = np.int16(frames * 32767)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples.tolist()))


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
            assert all(lane.waveform._peaks for lane in studio._lanes.values())
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


def test_recording_setup_never_offers_local_capture_to_a_joiner(tmp_path):
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
    assert not dialog._capture.isEnabled()
    assert not dialog._capture.isChecked()
    assert dialog._input.isHidden()


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
