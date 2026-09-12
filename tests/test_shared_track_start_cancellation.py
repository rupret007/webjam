"""Cancel admitted route work without revoking an already published track."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np
import pytest
import soundfile as sf

from core.reference_track import (
    ReferenceTrackCapability,
    ReferenceTrackController,
    ReferenceTrackError,
    ReferenceTrackLaunchContext,
    ReferenceTrackOwnershipClaim,
    ReferenceTrackState,
)


class _Gate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def wait(self):
        self.entered.set()
        assert self.release.wait(3), "controlled operation was not released"


class _Session:
    route_name = "Controlled private track route"

    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.health_gate = None

    def start(self, pull):
        # The callback is retained by production; this fake never opens or
        # pulls an audio device, starts a process, or produces a recording.
        self.starts += 1

    def stop(self):
        self.stops += 1

    def health_error(self):
        if self.health_gate is not None:
            self.health_gate.wait()
        return ""

    def recording_ownership_claim(self):
        return ReferenceTrackOwnershipClaim(
            udp_port=22125, process_id=9123, generation="a" * 32,
        )


class _Backend:
    def __init__(self):
        self.capability_gate = None
        self.prepare_gate = None
        self.cleanup_gate = None
        self.cleanup_pending = False
        self.cleanup_error = ""
        self.cleanup_calls = 0
        self.available = True
        self.prepare_calls = 0
        self.sessions = []

    def capability(self, audience_bridge_active=False):
        if self.capability_gate is not None:
            self.capability_gate.wait()
        if self.cleanup_pending:
            return ReferenceTrackCapability(
                False, "macos", "Controlled cleanup is pending",
                backend="blackhole", reason_code="cleanup_pending",
            )
        return ReferenceTrackCapability(
            self.available, "macos", "Controlled route capability",
            "Controlled route", backend="blackhole",
        )

    def prepare(self, context):
        self.prepare_calls += 1
        if self.prepare_gate is not None:
            self.prepare_gate.wait()
        session = _Session()
        self.sessions.append(session)
        return session

    def retry_cleanup(self):
        self.cleanup_calls += 1
        if self.cleanup_gate is not None:
            self.cleanup_gate.wait()
        if self.cleanup_error:
            raise ReferenceTrackError(self.cleanup_error)
        self.cleanup_pending = False


_CONTEXT = ReferenceTrackLaunchContext(
    server_address="127.0.0.1:22124",
    jamulus_binary="/test-only/no-executable",
    primary_udp_port=22124,
    primary_rpc_port=22222,
    primary_process_id=8123,
)


@pytest.fixture
def loaded_track(tmp_path):
    backend = _Backend()
    controller = ReferenceTrackController(backend, is_host=lambda: True)
    source = tmp_path / "retained-reference.wav"
    frames = np.arange(24_000, dtype=np.float32)
    sf.write(source, 0.1 * np.sin(frames * 0.04), 48_000, subtype="PCM_16")
    controller.load(source)
    controller.set_loop(0.05, 0.25)
    controller.set_trim_db(-4)
    controller.set_count_in(4, 90)
    try:
        yield controller, backend
    finally:
        for gate in (backend.capability_gate, backend.prepare_gate, backend.cleanup_gate):
            if gate is not None:
                gate.release.set()
        for session in backend.sessions:
            if session.health_gate is not None:
                session.health_gate.release.set()
        backend.cleanup_error = ""
        controller.close()


def _source_settings(controller):
    snapshot = controller.snapshot
    return (
        controller.recording_source_fingerprint(), snapshot.source_name,
        snapshot.duration_s, snapshot.loop_start_s, snapshot.loop_end_s,
        snapshot.trim_db, snapshot.count_in_beats, snapshot.count_in_bpm,
    )


@pytest.mark.parametrize("available", [True, False])
def test_cancel_during_capability_never_prepares_or_replaces_loaded_source(
    loaded_track, available,
):
    controller, backend = loaded_track
    before = _source_settings(controller)
    gate = backend.capability_gate = _Gate()
    backend.available = available
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(controller.play, _CONTEXT)
        try:
            assert gate.entered.wait(2)
            assert controller.snapshot.state is ReferenceTrackState.READY
            controller.cancel_pending_start()
        finally:
            gate.release.set()
        result = work.result(timeout=2)
    assert result.state is ReferenceTrackState.READY
    assert backend.prepare_calls == 0
    assert backend.sessions == []
    assert _source_settings(controller) == before

    # Cancellation revokes the old gesture, not the host's next explicit Play.
    backend.capability_gate = None
    backend.available = True
    assert controller.play(_CONTEXT).state is ReferenceTrackState.PLAYING
    assert backend.prepare_calls == 1
    assert backend.sessions[0].starts == 1


def test_cancel_paused_resume_during_health_preserves_owned_capture(loaded_track):
    controller, backend = loaded_track
    controller.play(_CONTEXT)
    controller.pause()
    before = _source_settings(controller)
    claim = controller.recording_ownership_claim()
    assert claim is not None
    session = backend.sessions[0]
    gate = session.health_gate = _Gate()
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(controller.play, _CONTEXT)
        try:
            assert gate.entered.wait(2)
            controller.cancel_pending_start()
        finally:
            gate.release.set()
        result = work.result(timeout=2)
    assert result.state is ReferenceTrackState.PAUSED
    assert result.active
    assert session.starts == 1 and session.stops == 0
    assert controller.recording_ownership_claim() == claim
    assert _source_settings(controller) == before

    session.health_gate = None
    assert controller.play(_CONTEXT).state is ReferenceTrackState.PLAYING
    assert backend.prepare_calls == 1
    assert session.starts == 1 and session.stops == 0


def test_cancel_during_prepare_retires_only_unpublished_session(loaded_track):
    controller, backend = loaded_track
    before = _source_settings(controller)
    gate = backend.prepare_gate = _Gate()
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(controller.play, _CONTEXT)
        try:
            assert gate.entered.wait(2)
            assert controller.snapshot.state is ReferenceTrackState.ROUTING
            assert controller.recording_ownership_claim() is None
            assert controller.cancel_pending_start().state is ReferenceTrackState.READY
        finally:
            gate.release.set()
        result = work.result(timeout=2)
    assert result.state is ReferenceTrackState.READY
    assert not result.active
    assert backend.prepare_calls == 1
    assert len(backend.sessions) == 1
    assert backend.sessions[0].starts == 0
    assert backend.sessions[0].stops == 1
    assert controller.recording_ownership_claim() is None
    assert _source_settings(controller) == before


def test_cancel_preserves_failed_backend_cleanup_receipt(loaded_track):
    controller, backend = loaded_track
    before = _source_settings(controller)
    backend.cleanup_pending = True
    assert controller.refresh_capability().cleanup_pending
    gate = backend.cleanup_gate = _Gate()
    backend.cleanup_error = "Controlled cleanup did not receive a close receipt."
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(controller.play, _CONTEXT)
        try:
            assert gate.entered.wait(2)
            controller.cancel_pending_start()
        finally:
            gate.release.set()
        result = work.result(timeout=2)
    assert result.state is ReferenceTrackState.FAILED
    assert result.cleanup_pending
    assert result.error == backend.cleanup_error
    assert backend.cleanup_calls == 1
    assert backend.prepare_calls == 0
    assert backend.sessions == []
    assert _source_settings(controller) == before


def test_cancel_after_playing_publication_preserves_track_and_capture(loaded_track):
    controller, backend = loaded_track
    gate = _Gate()

    def observe(snapshot):
        if snapshot.state is ReferenceTrackState.PLAYING:
            gate.wait()

    controller._on_snapshot = observe
    before = _source_settings(controller)
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(controller.play, _CONTEXT)
        try:
            assert gate.entered.wait(2)
            assert controller.snapshot.state is ReferenceTrackState.PLAYING
            claim = controller.recording_ownership_claim()
            assert claim is not None
            assert controller.cancel_pending_start().state is ReferenceTrackState.PLAYING
        finally:
            gate.release.set()
        result = work.result(timeout=2)
    assert result.state is ReferenceTrackState.PLAYING
    assert result.active
    assert controller.recording_ownership_claim() == claim
    assert _source_settings(controller) == before
    assert backend.sessions[0].starts == 1
    assert backend.sessions[0].stops == 0
    controller._on_snapshot = None
    assert controller.stop().state is ReferenceTrackState.READY
    assert backend.sessions[0].stops == 1
    assert controller.recording_ownership_claim() is None


def test_cancel_closed_controller_remains_closed(loaded_track):
    controller, backend = loaded_track
    controller.close()
    assert controller.cancel_pending_start().state is ReferenceTrackState.CLOSED
    assert backend.prepare_calls == 0
