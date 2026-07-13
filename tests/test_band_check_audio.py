from __future__ import annotations

from pathlib import Path
from unittest import mock

import numpy as np

from core.band_check_audio import (
    HeadphoneTonePlayer,
    InputActivityProbe,
    ScratchRecorder,
    validate_studio_scratch,
)


class _InputStream:
    def __init__(self, *, callback, blocks=(), **_kwargs):
        self.callback = callback
        self.blocks = list(blocks)
        self.started = False
        self.closed = False

    def start(self):
        self.started = True
        for block in self.blocks:
            self.callback(block, len(block), None, None)

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def test_input_probe_keeps_only_aggregate_level() -> None:
    block = np.full((256, 1), 0.25, dtype=np.float32)
    created = []

    def stream(**kwargs):
        value = _InputStream(blocks=[block], **kwargs)
        created.append(value)
        return value

    with mock.patch("sounddevice.check_input_settings"), mock.patch(
        "sounddevice.InputStream", side_effect=stream
    ):
        probe = InputActivityProbe(device=0)
        probe.start()
        snapshot = probe.snapshot()
        probe.stop()
    assert snapshot.active
    assert 0.24 <= snapshot.rms <= 0.26
    assert 0.24 <= snapshot.peak <= 0.26
    assert not snapshot.clipped
    assert created[0].closed
    assert not any(isinstance(value, np.ndarray) for value in probe.__dict__.values())


def test_input_probe_detects_clipping() -> None:
    block = np.array([[0.0], [1.0], [-1.0]], dtype=np.float32)
    with mock.patch("sounddevice.check_input_settings"), mock.patch(
        "sounddevice.InputStream",
        side_effect=lambda **kwargs: _InputStream(blocks=[block], **kwargs),
    ):
        probe = InputActivityProbe()
        probe.start()
        snapshot = probe.snapshot()
        probe.stop()
    assert snapshot.clipped
    assert snapshot.peak == 1.0


def test_scratch_recording_finalizes_reopens_and_deletes(tmp_path: Path) -> None:
    sample_rate = 48_000
    frames_per_block = 4_800
    times = np.arange(frames_per_block, dtype=np.float32) / sample_rate
    tone = (0.1 * np.sin(2 * np.pi * 440 * times))[:, None].astype(np.float32)
    blocks = [tone.copy() for _ in range(50)]
    with mock.patch("sounddevice.check_input_settings"), mock.patch(
        "sounddevice.InputStream",
        side_effect=lambda **kwargs: _InputStream(blocks=blocks, **kwargs),
    ):
        recorder = ScratchRecorder(tmp_path, device=0, target_duration_s=5.0)
        recorder.start()
        evidence = recorder.stop_and_validate()
    assert evidence.valid, evidence.error
    assert evidence.path is not None and evidence.path.is_file()
    assert evidence.frame_count == sample_rate * 5
    assert evidence.sample_rate == sample_rate
    assert evidence.channels == 1
    assert evidence.subtype == "PCM_24"
    assert evidence.has_signal
    assert evidence.waveform_peaks
    studio = validate_studio_scratch(evidence.path)
    assert studio.valid, studio.error
    assert studio.rendered_frames > 0
    assert studio.source_unchanged
    directory = evidence.path.parent
    recorder.delete()
    assert not directory.exists()


def test_scratch_silence_is_valid_file_but_disclosed_as_silent(tmp_path: Path) -> None:
    blocks = [np.zeros((4_800, 1), dtype=np.float32) for _ in range(50)]
    with mock.patch("sounddevice.check_input_settings"), mock.patch(
        "sounddevice.InputStream",
        side_effect=lambda **kwargs: _InputStream(blocks=blocks, **kwargs),
    ):
        recorder = ScratchRecorder(tmp_path)
        recorder.start()
        evidence = recorder.stop_and_validate()
        recorder.delete()
    assert evidence.valid
    assert not evidence.has_signal
    assert evidence.peak == 0.0


def test_scratch_dropped_block_fails_validation(tmp_path: Path) -> None:
    blocks = [np.ones((48_000, 1), dtype=np.float32) * 0.02 for _ in range(5)]
    with mock.patch("sounddevice.check_input_settings"), mock.patch(
        "sounddevice.InputStream",
        side_effect=lambda **kwargs: _InputStream(blocks=blocks, **kwargs),
    ):
        recorder = ScratchRecorder(tmp_path)
        recorder.start()
        recorder._dropped_blocks = 1
        evidence = recorder.stop_and_validate()
        recorder.delete()
    assert not evidence.valid
    assert "dropped" in evidence.error


def test_scratch_playback_is_explicit_and_conservatively_scaled(tmp_path: Path) -> None:
    blocks = [np.ones((4_800, 1), dtype=np.float32) * 0.8 for _ in range(50)]
    with mock.patch("sounddevice.check_input_settings"), mock.patch(
        "sounddevice.InputStream",
        side_effect=lambda **kwargs: _InputStream(blocks=blocks, **kwargs),
    ):
        recorder = ScratchRecorder(tmp_path)
        recorder.start()
        evidence = recorder.stop_and_validate()
    assert evidence.valid
    with mock.patch("sounddevice.query_devices", return_value=[]), mock.patch(
        "sounddevice.play"
    ) as play:
        duration = recorder.play()
    sent = play.call_args.args[0]
    assert duration == 5.0
    assert float(np.max(np.abs(sent))) <= 0.201
    assert play.call_args.kwargs["blocking"] is False
    recorder.delete()


def test_headphone_tone_is_quiet_stereo_and_left_then_right() -> None:
    with mock.patch(
        "sounddevice.query_devices",
        return_value={"max_output_channels": 2},
    ), mock.patch("sounddevice.check_output_settings") as check, mock.patch(
        "sounddevice.play"
    ) as play:
        evidence = HeadphoneTonePlayer().play()
    audio = play.call_args.args[0]
    assert evidence.duration_s > 1.4
    assert evidence.channels == 2
    assert audio.ndim == 2 and audio.shape[1] == 2
    assert float(np.max(np.abs(audio))) <= HeadphoneTonePlayer.LEVEL + 1e-6
    midpoint = len(audio) // 2
    assert float(np.max(np.abs(audio[:midpoint, 0]))) > 0.02
    assert float(np.max(np.abs(audio[:midpoint, 1]))) == 0.0
    assert float(np.max(np.abs(audio[midpoint:, 0]))) == 0.0
    assert float(np.max(np.abs(audio[midpoint:, 1]))) > 0.02
    check.assert_called_once()
    assert play.call_args.kwargs["blocking"] is False


def test_headphone_tone_falls_back_truthfully_to_mono_output() -> None:
    with mock.patch(
        "sounddevice.query_devices",
        return_value={"max_output_channels": 1},
    ), mock.patch("sounddevice.check_output_settings") as check, mock.patch(
        "sounddevice.play"
    ) as play:
        evidence = HeadphoneTonePlayer().play()
    audio = play.call_args.args[0]
    assert evidence.channels == 1
    assert audio.ndim == 2 and audio.shape[1] == 1
    midpoint = len(audio) // 2
    assert float(np.max(np.abs(audio[:midpoint, 0]))) > 0.02
    assert float(np.max(np.abs(audio[midpoint:, 0]))) > 0.02
    check.assert_called_once()
