"""Core Reference Track state, decoding, and bounded-stream contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import time

import numpy as np
import pytest
import soundfile as sf

from core.reference_track import (
    REFERENCE_SAMPLE_RATE,
    ReferenceTrackCapability,
    ReferenceTrackController,
    ReferenceTrackDecoder,
    ReferenceTrackError,
    ReferenceTrackLaunchContext,
    ReferenceTrackSnapshot,
    ReferenceTrackState,
    ReferenceTrackStream,
)


def _audio_file(
    path: Path,
    *,
    samplerate: int = 44_100,
    channels: int = 1,
    seconds: float = 0.25,
) -> Path:
    frames = round(samplerate * seconds)
    timeline = np.arange(frames, dtype=np.float32) / samplerate
    tone = (0.25 * np.sin(2 * np.pi * 440.0 * timeline)).astype(np.float32)
    audio = tone if channels == 1 else np.column_stack([tone] * channels)
    sf.write(path, audio, samplerate)
    return path


class _Session:
    route_name = "Test BlackHole"

    def __init__(self) -> None:
        self.pull = None
        self.error = ""
        self.stop_error = ""
        self.start_error = ""
        self.started = 0
        self.stopped = 0

    def start(self, pull) -> None:
        self.pull = pull
        self.started += 1
        if self.start_error:
            raise ReferenceTrackError(self.start_error)

    def health_error(self) -> str:
        return self.error

    def stop(self) -> None:
        self.stopped += 1
        if self.stop_error:
            raise ReferenceTrackError(self.stop_error)


class _Backend:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.prepared: list[ReferenceTrackLaunchContext] = []
        self.sessions: list[_Session] = []

    def capability(self, audience_bridge_active: bool = False):
        available = self.available and not audience_bridge_active
        return ReferenceTrackCapability(
            available,
            "macos",
            (
                "safe route"
                if available
                else "Reference Track route is unavailable."
            ),
            "Test BlackHole" if available else "",
        )

    def prepare(self, context: ReferenceTrackLaunchContext):
        self.prepared.append(context)
        session = _Session()
        self.sessions.append(session)
        return session


def _context(**changes) -> ReferenceTrackLaunchContext:
    values = {
        "server_address": "127.0.0.1:22124",
        "jamulus_binary": "/Applications/Jamulus",
        "primary_udp_port": 22124,
        "primary_rpc_port": 22222,
        "primary_process_id": 4242,
    }
    values.update(changes)
    return ReferenceTrackLaunchContext(**values)


def _await_nonzero(stream: ReferenceTrackStream) -> np.ndarray:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        block = stream.pull(512)
        if np.max(np.abs(block)) > 0.001:
            return block
        time.sleep(0.01)
    raise AssertionError("bounded producer never supplied audio")


def test_snapshot_and_context_are_immutable_and_validate_ports() -> None:
    capability = ReferenceTrackCapability(True, "macos", "ready", "BlackHole 16ch")
    snapshot = ReferenceTrackSnapshot(
        state=ReferenceTrackState.READY,
        capability=capability,
        source_name="song.wav",
        duration_s=2.0,
    )

    with pytest.raises(FrozenInstanceError):
        snapshot.state = ReferenceTrackState.PLAYING  # type: ignore[misc]
    with pytest.raises(ValueError, match="primary_rpc_port"):
        _context(primary_rpc_port=0)
    with pytest.raises(ValueError, match="primary_process_id"):
        _context(primary_process_id=0)
    with pytest.raises(ValueError, match="primary_input_device_name"):
        _context(primary_input_device_name="unsafe\nroute")
    assert snapshot.loaded is True
    assert snapshot.can_play is True
    assert snapshot.active is False


def test_decoder_streams_mono_as_stereo_and_resamples_to_48k(
    tmp_path: Path,
) -> None:
    source = _audio_file(tmp_path / "song with spaces.wav")
    decoder = ReferenceTrackDecoder(source)

    assert decoder.info.name == source.name
    assert decoder.info.source_samplerate == 44_100
    assert decoder.info.channels == 1
    assert decoder.info.output_frames == round(0.25 * REFERENCE_SAMPLE_RATE)
    block = decoder.read_48k(0, 1_024)
    assert block.shape == (1_024, 2)
    assert block.dtype == np.float32
    np.testing.assert_allclose(block[:, 0], block[:, 1])
    assert str(tmp_path) not in repr(decoder)
    decoder.close()


def test_decoder_rejects_symlink_wrong_extension_and_multichannel(
    tmp_path: Path,
) -> None:
    source = _audio_file(tmp_path / "source.wav")
    link = tmp_path / "linked.wav"
    link.symlink_to(source)
    wrong = tmp_path / "song.txt"
    wrong.write_bytes(source.read_bytes())
    malformed = tmp_path / "malformed.wav"
    malformed.write_bytes(b"not-a-wave-file")
    surround = _audio_file(tmp_path / "surround.wav", channels=3)

    with pytest.raises(ReferenceTrackError, match="local WAV"):
        ReferenceTrackDecoder(link)
    with pytest.raises(ReferenceTrackError, match="local WAV"):
        ReferenceTrackDecoder(wrong)
    with pytest.raises(ReferenceTrackError) as malformed_error:
        ReferenceTrackDecoder(malformed)
    assert str(tmp_path) not in str(malformed_error.value)
    assert malformed_error.value.__cause__ is None
    with pytest.raises(ReferenceTrackError, match="one or two channels"):
        ReferenceTrackDecoder(surround)


def test_bounded_stream_play_pause_seek_loop_trim_and_count_in(
    tmp_path: Path,
) -> None:
    decoder = ReferenceTrackDecoder(
        _audio_file(tmp_path / "loop.wav", samplerate=48_000, seconds=0.5)
    )
    stream = ReferenceTrackStream(decoder, block_frames=256, queue_blocks=4)
    try:
        stream.configure_trim(-6.0)
        stream.configure_count_in(2, 120.0)
        stream.configure_loop(0.05, 0.10)
        stream.seek(0.05)
        stream.play(count_in=True)

        click = _await_nonzero(stream)
        assert click.shape == (512, 2)
        stream.pause()
        paused = stream.position_s
        assert np.count_nonzero(stream.pull(256)) == 0
        assert stream.position_s == paused

        stream.seek(0.075)
        stream.play(count_in=False)
        song = _await_nonzero(stream)
        assert np.max(np.abs(song)) <= 0.51
        assert 0.05 <= stream.position_s <= 0.10
    finally:
        stream.close()


def test_stream_underrun_is_bounded_silence_and_recovers(
    tmp_path: Path,
) -> None:
    decoder = ReferenceTrackDecoder(
        _audio_file(tmp_path / "underrun.wav", samplerate=48_000)
    )
    stream = ReferenceTrackStream(decoder, block_frames=256, queue_blocks=2)
    try:
        stream.play()
        # Pull emits bounded silence if it beats the producer to the queue.
        # This does not certify its locks/allocations for a real-time thread.
        first = stream.pull(256)
        assert first.shape == (256, 2)
        assert np.isfinite(first).all()
        assert stream.finished is False
        recovered = _await_nonzero(stream)
        assert np.max(np.abs(recovered)) > 0.001
    finally:
        stream.close()


def test_stream_latches_a_path_free_decode_failure(tmp_path: Path) -> None:
    decoder = ReferenceTrackDecoder(_audio_file(tmp_path / "source.wav"))
    stream = ReferenceTrackStream(decoder, block_frames=256, queue_blocks=2)

    def fail_decode(_start: int, _frames: int):
        raise ReferenceTrackError("WebJam lost access to the selected song.")

    decoder.read_48k = fail_decode  # type: ignore[method-assign]
    try:
        stream.play()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not stream.error:
            time.sleep(0.01)
        assert stream.error == "WebJam lost access to the selected song."
        assert str(tmp_path) not in stream.error
        assert np.count_nonzero(stream.pull(256)) == 0
    finally:
        stream.close()


def test_controller_requires_host_and_refuses_audience_bridge(
    tmp_path: Path,
) -> None:
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: False)
    controller.load(_audio_file(tmp_path / "song.wav"))

    denied = controller.play(_context())
    assert denied.state is ReferenceTrackState.FAILED
    assert "Only the session host" in denied.error
    assert not backend.prepared

    host = ReferenceTrackController(backend, is_host=lambda: True)
    host.load(_audio_file(tmp_path / "other.wav"))
    conflict = host.play(_context(audience_bridge_active=True))
    assert conflict.state is ReferenceTrackState.FAILED
    assert "audience bridge" in conflict.error
    assert not backend.prepared
    controller.close()
    host.close()


def test_controller_full_lifecycle_is_host_only_ephemeral_and_clean(
    tmp_path: Path,
) -> None:
    backend = _Backend()
    updates = []
    controller = ReferenceTrackController(
        backend, is_host=lambda: True, on_snapshot=updates.append
    )
    source = tmp_path / "private folder" / "song.wav"
    source.parent.mkdir()
    _audio_file(source)
    loaded = controller.load(source)
    assert loaded.state is ReferenceTrackState.READY
    assert loaded.source_name == "song.wav"
    assert str(source.parent) not in repr(loaded)
    controller.set_count_in(4, 100.0)
    controller.set_trim_db(-3.0)
    controller.set_loop(0.02, 0.15)

    playing = controller.play(_context())
    session = backend.sessions[-1]
    assert playing.state is ReferenceTrackState.PLAYING
    assert session.started == 1
    assert session.pull is not None
    assert backend.prepared == [_context()]
    assert "Jamulus-routed" in playing.warning
    assert "separate stem" in playing.warning

    paused = controller.pause()
    assert paused.state is ReferenceTrackState.PAUSED
    sought = controller.seek(0.05)
    assert sought.position_s == pytest.approx(0.05)
    resumed = controller.play(_context())
    assert resumed.state is ReferenceTrackState.PLAYING
    assert len(backend.sessions) == 1
    stopped = controller.handle_session_end()
    assert stopped.state is ReferenceTrackState.READY
    assert stopped.position_s == 0.0
    assert session.stopped == 1
    assert updates

    closed = controller.close()
    assert closed.state is ReferenceTrackState.CLOSED
    assert not closed.loaded


def test_controller_route_failure_stops_only_owned_reference_session(
    tmp_path: Path,
) -> None:
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    controller.play(_context())
    session = backend.sessions[-1]
    session.error = "Reference Track route changed."

    failed = controller.refresh_health()

    assert failed.state is ReferenceTrackState.FAILED
    assert failed.error == session.error
    assert session.stopped == 1
    assert controller.snapshot.loaded is True
    controller.close()


@pytest.mark.parametrize("operation", ("resume", "restart"))
def test_controller_refuses_resume_or_restart_after_live_route_failure(
    tmp_path: Path,
    operation: str,
) -> None:
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    controller.play(_context())
    session = backend.sessions[-1]
    if operation == "resume":
        controller.pause()
    session.error = "Reference Track live primary route proof became stale."

    result = (
        controller.play(_context())
        if operation == "resume"
        else controller.restart()
    )

    assert result.state is ReferenceTrackState.FAILED
    assert "proof became stale" in result.error
    assert session.stopped == 1
    controller.close()


def test_controller_teardown_failure_never_reports_ready(
    tmp_path: Path,
) -> None:
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    controller.play(_context())
    session = backend.sessions[-1]
    session.stop_error = (
        "Reference Track couldn't confirm that its owned Jamulus client stopped."
    )

    failed = controller.stop()

    assert failed.state is ReferenceTrackState.FAILED
    assert failed.error == session.stop_error
    assert controller.close().state is ReferenceTrackState.FAILED
    session.stop_error = ""
    assert controller.close().state is ReferenceTrackState.CLOSED
    assert session.stopped == 3


def test_failed_owned_teardown_blocks_source_replacement_until_retry(
    tmp_path: Path,
) -> None:
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    first = _audio_file(tmp_path / "first.wav")
    second = _audio_file(tmp_path / "second.wav")
    controller.load(first)
    controller.play(_context())
    session = backend.sessions[-1]
    session.stop_error = (
        "Reference Track couldn't confirm that its owned Jamulus client stopped."
    )

    blocked = controller.load(second)

    assert blocked.state is ReferenceTrackState.FAILED
    assert blocked.source_name == first.name
    assert controller.snapshot.source_name == first.name
    session.stop_error = ""
    replaced = controller.load(second)
    assert replaced.state is ReferenceTrackState.READY
    assert replaced.source_name == second.name
    assert session.stopped == 2
    controller.close()


def test_start_failure_retains_session_when_cleanup_cannot_be_confirmed(
    tmp_path: Path,
) -> None:
    class _FailingBackend(_Backend):
        def prepare(self, context):
            self.prepared.append(context)
            session = _Session()
            session.start_error = "Reference Track control did not become ready."
            session.stop_error = (
                "Reference Track couldn't confirm that its owned Jamulus "
                "client stopped."
            )
            self.sessions.append(session)
            return session

    backend = _FailingBackend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))

    failed = controller.play(_context())

    session = backend.sessions[-1]
    assert failed.state is ReferenceTrackState.FAILED
    assert failed.error == session.stop_error
    assert failed.route_detail == session.route_name
    blocked = controller.load(_audio_file(tmp_path / "new.wav"))
    assert blocked.source_name == "song.wav"
    session.stop_error = ""
    assert controller.close().state is ReferenceTrackState.CLOSED


def test_capability_conflict_stops_an_already_active_route(
    tmp_path: Path,
) -> None:
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    controller.play(_context())
    session = backend.sessions[-1]

    unavailable = controller.refresh_capability(audience_bridge_active=True)

    assert unavailable.state is ReferenceTrackState.UNAVAILABLE
    assert unavailable.capability.available is False
    assert session.stopped == 1
    controller.close()


def test_controller_seeking_is_paused_only(tmp_path: Path) -> None:
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    controller.play(_context())

    with pytest.raises(ReferenceTrackError, match="Pause"):
        controller.seek(0.1)
    controller.close()
