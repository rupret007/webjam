"""Core Reference Track state, decoding, and bounded-stream contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import threading
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
    reference_track_file_filter,
    reference_track_supported_extensions,
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


class _BlockingPrepareBackend(_Backend):
    def __init__(self, available: bool = True) -> None:
        super().__init__(available=available)
        self.prepare_entered = threading.Event()
        self.release_prepare = threading.Event()

    def prepare(self, context: ReferenceTrackLaunchContext):
        self.prepared.append(context)
        self.prepare_entered.set()
        assert self.release_prepare.wait(timeout=3.0)
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

    unsafe_capability = ReferenceTrackCapability(
        False,
        "/Users/private",
        "private backend detail",
        backend="/Users/private",
        reason_code="/Users/private",
    )
    public = ReferenceTrackSnapshot(
        state=ReferenceTrackState.IDLE,
        capability=unsafe_capability,
    ).public_diagnostics()
    assert public["route_platform"] == "unknown"
    assert public["route_backend"] == "unavailable"
    assert public["route_reason"] == "unavailable"
    assert "private" not in repr(public).casefold()

    sanitized_source = ReferenceTrackSnapshot(
        state=ReferenceTrackState.READY,
        capability=capability,
        source_name="/Users/private/Secret Song.wav",
        duration_s=2.0,
    )
    assert sanitized_source.source_name == "Selected song"


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

    with pytest.raises(ReferenceTrackError, match="regular local") as linked_error:
        ReferenceTrackDecoder(link)
    assert str(tmp_path) not in str(linked_error.value)
    with pytest.raises(ReferenceTrackError, match="local WAV"):
        ReferenceTrackDecoder(wrong)
    with pytest.raises(ReferenceTrackError) as malformed_error:
        ReferenceTrackDecoder(malformed)
    assert str(tmp_path) not in str(malformed_error.value)
    assert malformed_error.value.__cause__ is None
    with pytest.raises(ReferenceTrackError, match="one- or two-channel"):
        ReferenceTrackDecoder(surround)


@pytest.mark.parametrize(
    ("suffix", "format_name", "subtype", "channels"),
    (
        (".wav", "WAV", "PCM_16", 1),
        (".WAVE", "WAV", "PCM_16", 2),
        (".aif", "AIFF", "PCM_16", 1),
        (".aiff", "AIFF", "PCM_16", 2),
        (".flac", "FLAC", "PCM_16", 2),
        (".mp3", "MP3", "MPEG_LAYER_III", 2),
    ),
)
def test_decoder_accepts_truthful_formats_spaces_and_unicode(
    tmp_path: Path,
    suffix: str,
    format_name: str,
    subtype: str,
    channels: int,
) -> None:
    if format_name == "MP3" and not sf.check_format("MP3"):
        pytest.skip("the locked decoder build has no MP3 capability")
    folder = tmp_path / "Band rehearsal – été"
    folder.mkdir()
    source = folder / f"Reference Song{suffix}"
    mono = np.linspace(-0.25, 0.25, 1_024, dtype=np.float32)
    samples = mono if channels == 1 else np.column_stack((mono, -mono))
    sf.write(
        source,
        samples,
        48_000,
        format=format_name,
        subtype=subtype,
    )

    decoder = ReferenceTrackDecoder(source)
    try:
        assert decoder.info.name == source.name
        assert decoder.info.container == format_name
        assert decoder.info.source_samplerate == 48_000
        assert decoder.info.channels == channels
        assert decoder.read_48k(0, 128).shape == (128, 2)
        assert str(folder) not in repr(decoder)
    finally:
        decoder.close()


def test_decoder_rejects_renamed_container_and_unavailable_source(
    tmp_path: Path,
) -> None:
    disguised = tmp_path / "renamed.wav"
    sf.write(
        disguised,
        np.zeros(128, dtype=np.float32),
        48_000,
        format="FLAC",
    )
    missing = tmp_path / "missing song.wav"

    with pytest.raises(ReferenceTrackError) as disguised_error:
        ReferenceTrackDecoder(disguised)
    assert str(tmp_path) not in str(disguised_error.value)
    assert disguised.name not in str(disguised_error.value)
    assert disguised_error.value.__cause__ is None

    with pytest.raises(ReferenceTrackError, match="unavailable") as missing_error:
        ReferenceTrackDecoder(missing)
    assert str(tmp_path) not in str(missing_error.value)
    assert missing.name not in str(missing_error.value)
    assert missing_error.value.__cause__ is None


def test_mp3_is_advertised_only_when_runtime_decoder_proves_support(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "core.reference_track.project_audio_mp3_available",
        lambda: False,
    )

    assert ".mp3" not in reference_track_supported_extensions()
    assert "*.mp3" not in reference_track_file_filter()
    with pytest.raises(ReferenceTrackError, match="MP3 decoding is unavailable"):
        ReferenceTrackDecoder(tmp_path / "private song.mp3")

    monkeypatch.setattr(
        "core.reference_track.project_audio_mp3_available",
        lambda: True,
    )
    assert ".mp3" in reference_track_supported_extensions()
    assert "*.mp3" in reference_track_file_filter()


def test_reference_decoder_inherits_project_duration_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _audio_file(tmp_path / "too long.wav")
    monkeypatch.setattr("core.project_audio.PROJECT_AUDIO_MAX_OUTPUT_FRAMES", 1)

    with pytest.raises(ReferenceTrackError) as caught:
        ReferenceTrackDecoder(source)
    assert str(tmp_path) not in str(caught.value)


def test_long_source_is_inspected_and_read_in_bounded_windows(
    tmp_path: Path,
) -> None:
    source = _audio_file(
        tmp_path / "Long rehearsal reference.wav",
        samplerate=48_000,
        seconds=20.0,
    )
    decoder = ReferenceTrackDecoder(source)
    try:
        assert decoder.info.duration_s == pytest.approx(20.0)
        near_end = decoder.read_48k(decoder.output_frames - 128, 128)
        assert near_end.shape == (128, 2)
        with pytest.raises(ValueError, match="bounded decoder"):
            decoder.read_48k(0, 4_097)
    finally:
        decoder.close()


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
    assert failed.cleanup_pending is True
    assert failed.active is True
    assert failed.public_diagnostics()["route_active"] is True
    assert failed.public_diagnostics()["cleanup_pending"] is True
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

    assert unavailable.state is ReferenceTrackState.READY
    assert unavailable.capability.available is False
    assert unavailable.loaded is True
    assert unavailable.can_play is False
    assert session.stopped == 1
    controller.close()


@pytest.mark.parametrize("operation", ("stop", "close"))
def test_stop_or_close_cancels_a_concurrent_prepare_without_resurrection(
    tmp_path: Path,
    operation: str,
) -> None:
    backend = _BlockingPrepareBackend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    result: list[ReferenceTrackSnapshot] = []
    worker = threading.Thread(
        target=lambda: result.append(controller.play(_context())),
    )
    worker.start()
    assert backend.prepare_entered.wait(timeout=3.0)

    cancelled = getattr(controller, operation)()
    backend.release_prepare.set()
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert result
    expected = (
        ReferenceTrackState.CLOSED
        if operation == "close"
        else ReferenceTrackState.READY
    )
    assert cancelled.state is expected
    assert controller.snapshot.state is expected
    session = backend.sessions[-1]
    assert session.started == 0
    assert session.stopped == 1
    assert controller.snapshot.active is False
    if operation == "stop":
        controller.close()


def test_capability_loss_during_prepare_cancels_unpublished_session(
    tmp_path: Path,
) -> None:
    backend = _BlockingPrepareBackend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    worker = threading.Thread(target=lambda: controller.play(_context()))
    worker.start()
    assert backend.prepare_entered.wait(timeout=3.0)

    backend.available = False
    refreshed = controller.refresh_capability()
    backend.release_prepare.set()
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert refreshed.capability.available is False
    assert controller.snapshot.state is ReferenceTrackState.READY
    assert controller.snapshot.can_play is False
    session = backend.sessions[-1]
    assert session.started == 0
    assert session.stopped == 1
    controller.close()


def test_capability_refresh_observes_session_published_while_probe_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend(available=True)
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    capability_ready = threading.Event()
    release_capability = threading.Event()
    original_safe_capability = controller._safe_capability
    capability_calls = 0
    capability_calls_lock = threading.Lock()

    def _blocked_capability(audience_bridge_active: bool):
        nonlocal capability_calls
        capability = original_safe_capability(audience_bridge_active)
        with capability_calls_lock:
            capability_calls += 1
            should_block = capability_calls == 1
        if should_block:
            capability_ready.set()
            assert release_capability.wait(timeout=3.0)
        return capability

    backend.available = False
    monkeypatch.setattr(controller, "_safe_capability", _blocked_capability)
    refreshed: list[ReferenceTrackSnapshot] = []
    worker = threading.Thread(
        target=lambda: refreshed.append(controller.refresh_capability()),
    )
    worker.start()
    assert capability_ready.wait(timeout=3.0)

    backend.available = True
    playing = controller.play(_context())
    assert playing.state is ReferenceTrackState.PLAYING
    session = backend.sessions[-1]
    release_capability.set()
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert refreshed[-1].capability.available is False
    assert controller.snapshot.state is ReferenceTrackState.READY
    assert controller.snapshot.capability.available is False
    assert session.stopped == 1
    controller.close()


def test_close_supersedes_a_concurrent_source_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.reference_track as reference_track_module

    source = _audio_file(tmp_path / "song.wav")
    real_decoder = ReferenceTrackDecoder
    decode_entered = threading.Event()
    release_decode = threading.Event()

    class _BlockingDecoder:
        def __new__(cls, path):
            decode_entered.set()
            assert release_decode.wait(timeout=3.0)
            return real_decoder(path)

    monkeypatch.setattr(
        reference_track_module,
        "ReferenceTrackDecoder",
        _BlockingDecoder,
    )
    controller = ReferenceTrackController(_Backend(), is_host=lambda: True)
    result: list[ReferenceTrackSnapshot] = []
    worker = threading.Thread(
        target=lambda: result.append(controller.load(source)),
    )
    worker.start()
    assert decode_entered.wait(timeout=3.0)

    closed = controller.close()
    release_decode.set()
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert closed.state is ReferenceTrackState.CLOSED
    assert result[-1].state is ReferenceTrackState.CLOSED
    assert controller.snapshot.state is ReferenceTrackState.CLOSED
    assert controller.snapshot.loaded is False


def test_unavailable_route_keeps_source_ready_and_playback_fail_closed(
    tmp_path: Path,
) -> None:
    backend = _Backend(available=False)
    controller = ReferenceTrackController(backend, is_host=lambda: True)

    assert controller.snapshot.state is ReferenceTrackState.IDLE
    loaded = controller.load(_audio_file(tmp_path / "Reference Song.wav"))

    assert loaded.state is ReferenceTrackState.READY
    assert loaded.loaded is True
    assert loaded.can_play is False
    assert loaded.capability.available is False
    controller.set_trim_db(-4.0)
    controller.set_loop(0.01, 0.10)
    assert backend.prepared == []

    blocked = controller.play(_context())
    assert blocked.state is ReferenceTrackState.FAILED
    assert "route is unavailable" in blocked.error
    assert backend.prepared == []
    controller.close()


def test_route_recheck_recovers_loaded_source_after_unavailable_play(
    tmp_path: Path,
) -> None:
    backend = _Backend(available=False)
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "Reference Song.wav"))

    blocked = controller.play(_context())
    assert blocked.state is ReferenceTrackState.FAILED
    assert blocked.can_play is False

    backend.available = True
    recovered = controller.refresh_capability()

    assert recovered.state is ReferenceTrackState.READY
    assert recovered.error == ""
    assert recovered.can_play is True
    assert recovered.loaded is True
    controller.close()


def test_route_recheck_recovers_after_audience_bridge_is_disabled(
    tmp_path: Path,
) -> None:
    backend = _Backend(available=True)
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "Reference Song.wav"))

    blocked = controller.play(_context(audience_bridge_active=True))
    assert blocked.state is ReferenceTrackState.FAILED
    assert "audience bridge" in blocked.error

    recovered = controller.refresh_capability(audience_bridge_active=False)

    assert recovered.state is ReferenceTrackState.READY
    assert recovered.error == ""
    assert recovered.can_play is True
    assert backend.prepared == []
    controller.close()


def test_route_recheck_never_hides_non_route_failure(tmp_path: Path) -> None:
    backend = _Backend(available=True)
    controller = ReferenceTrackController(backend, is_host=lambda: False)
    controller.load(_audio_file(tmp_path / "Reference Song.wav"))

    denied = controller.play(_context())
    refreshed = controller.refresh_capability()

    assert denied.state is ReferenceTrackState.FAILED
    assert refreshed.state is ReferenceTrackState.FAILED
    assert refreshed.error == denied.error
    controller.close()


def test_public_diagnostics_are_path_and_filename_free(
    tmp_path: Path,
) -> None:
    backend = _Backend(available=False)
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    private_folder = tmp_path / "Jeff private rehearsal"
    private_folder.mkdir()
    source = _audio_file(private_folder / "Secret Song.wav")
    controller.load(source)

    diagnostics = controller.public_diagnostics()
    serialized = repr(diagnostics)
    assert str(private_folder) not in serialized
    assert source.name not in serialized
    assert diagnostics == {
        "playback_state": "ready",
        "source_state": "loaded",
        "source_format": "WAV",
        "source_sample_rate_hz": 44_100,
        "source_channels": 1,
        "source_duration_s": pytest.approx(0.25),
        "route_available": False,
        "route_platform": "macos",
        "route_backend": "blackhole",
        "route_reason": "unavailable",
        "route_active": False,
        "cleanup_pending": False,
    }
    controller.close()

    failed = ReferenceTrackController(backend, is_host=lambda: True)
    failed.load(private_folder / "Missing Secret Song.wav")
    failed_public = failed.public_diagnostics()
    assert failed_public["source_state"] == "failed"
    assert "Secret" not in repr(failed_public)
    failed.close()


def test_controller_seeking_is_paused_only(tmp_path: Path) -> None:
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    controller.load(_audio_file(tmp_path / "song.wav"))
    controller.play(_context())

    with pytest.raises(ReferenceTrackError, match="Pause"):
        controller.seek(0.1)
    controller.close()
