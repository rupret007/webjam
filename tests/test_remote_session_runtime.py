from __future__ import annotations

import logging
import threading
import time

import pytest

from core.remote_invitation import issue_remote_invitation
from core.session_transport import ConnectionQuality, TransportPath
from services.remote_session_runtime import (
    RemoteBackendError,
    RemoteGuestConnection,
    RemoteSessionErrorCode,
    RemoteSessionPhase,
    RemoteSessionStage,
    RemoteSessionRuntime,
)


def _invitation(*, issued_at: int | None = None, ttl: int = 600):
    now = int(time.time()) if issued_at is None else issued_at
    return issue_remote_invitation(
        "reference-local",
        allowed_profiles={"reference-local"},
        host_spki_sha256=bytes.fromhex("44" * 32),
        issued_at_unix=now,
        ttl_seconds=ttl,
        session_reference=bytes.fromhex("11" * 16),
        invite_reference=bytes.fromhex("22" * 16),
        enrollment_capability=bytes.fromhex("33" * 32),
    ).invitation


class Backend:
    def __init__(self) -> None:
        self.seen = None
        self.stopped = 0

    def start_guest(self, invitation, *, generation):
        self.seen = invitation
        return RemoteGuestConnection(
            loopback_port=34001,
            path=TransportPath.INTERNET_DIRECT,
            quality=ConnectionQuality.PLAYABLE,
            generation=generation,
        )

    def stop(self) -> None:
        self.stopped += 1


def test_typed_invitation_is_consumed_and_only_safe_state_is_published() -> None:
    backend = Backend()
    snapshots = []
    runtime = RemoteSessionRuntime(backend, on_snapshot=snapshots.append)
    invitation = _invitation()

    assert runtime.start_guest(invitation) is True
    settled = runtime.wait_until_settled()

    assert backend.seen is invitation
    assert settled.phase is RemoteSessionPhase.CONNECTED
    assert settled.loopback_port == 34001
    assert settled.musician_status == "Connected directly"
    assert runtime._pending_invitation is None
    assert invitation.capability_for_enrollment().hex() not in repr(runtime)
    assert all(not hasattr(item, "invitation") for item in snapshots)


def test_expired_invitation_never_reaches_backend() -> None:
    backend = Backend()
    snapshots = []
    runtime = RemoteSessionRuntime(backend, on_snapshot=snapshots.append)

    assert runtime.start_guest(_invitation(issued_at=1, ttl=1)) is False

    assert runtime.snapshot.phase is RemoteSessionPhase.FAILED
    assert runtime.snapshot.error_code is RemoteSessionErrorCode.EXPIRED
    assert backend.seen is None


def test_uncertain_backend_failure_requires_a_fresh_invitation(caplog) -> None:
    sentinel = "PRIVATE-CAPABILITY-SENTINEL"

    class FailingBackend(Backend):
        def start_guest(self, invitation, *, generation):
            raise RuntimeError(sentinel)

    caplog.set_level(logging.ERROR)
    runtime = RemoteSessionRuntime(FailingBackend(), on_snapshot=lambda _value: None)

    runtime.start_guest(_invitation())
    settled = runtime.wait_until_settled()

    assert settled.error_code is RemoteSessionErrorCode.INVITATION_UNUSABLE
    assert not settled.invitation_retry_safe
    assert sentinel not in caplog.text
    assert sentinel not in repr(settled)


def test_allowlisted_backend_error_is_preserved_without_freeform_detail() -> None:
    class RejectingBackend(Backend):
        def start_guest(self, invitation, *, generation):
            raise RemoteBackendError(RemoteSessionErrorCode.ENROLLMENT_REJECTED)

    runtime = RemoteSessionRuntime(RejectingBackend(), on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())

    assert runtime.wait_until_settled().error_code is (
        RemoteSessionErrorCode.ENROLLMENT_REJECTED
    )
    assert not runtime.snapshot.invitation_retry_safe


def test_only_explicit_pre_enrollment_unavailable_failure_is_retry_safe() -> None:
    class UnavailableBackend(Backend):
        def start_guest(self, invitation, *, generation):
            raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE)

    runtime = RemoteSessionRuntime(
        UnavailableBackend(),
        on_snapshot=lambda _value: None,
    )
    runtime.start_guest(_invitation())

    settled = runtime.wait_until_settled()

    assert settled.error_code is RemoteSessionErrorCode.UNAVAILABLE
    assert settled.invitation_retry_safe


def test_only_one_enrollment_worker_can_be_active() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingBackend(Backend):
        def start_guest(self, invitation, *, generation):
            entered.set()
            assert release.wait(2)
            return super().start_guest(invitation, generation=generation)

    runtime = RemoteSessionRuntime(BlockingBackend(), on_snapshot=lambda _value: None)
    assert runtime.start_guest(_invitation()) is True
    assert entered.wait(1)

    with pytest.raises(RuntimeError, match="already active"):
        runtime.start_guest(_invitation())

    release.set()
    assert runtime.wait_until_settled().phase is RemoteSessionPhase.CONNECTED


def test_bounded_join_stages_precede_fast_connected_callback() -> None:
    snapshots = []
    runtime = RemoteSessionRuntime(
        Backend(),
        on_snapshot=snapshots.append,
    )

    runtime.start_guest(_invitation())
    runtime.wait_until_settled()

    assert [(item.phase, item.stage) for item in snapshots] == [
        (RemoteSessionPhase.PREPARING, RemoteSessionStage.CONTACTING_HOST),
        (RemoteSessionPhase.PREPARING, RemoteSessionStage.SECURING_CONNECTION),
        (RemoteSessionPhase.CONNECTED, RemoteSessionStage.CONNECTED),
    ]


def test_hung_enrollment_times_out_requires_fresh_invite_and_ignores_late_result() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingBackend(Backend):
        def start_guest(self, invitation, *, generation):
            entered.set()
            assert release.wait(2)
            return super().start_guest(invitation, generation=generation)

        def stop(self) -> None:
            super().stop()
            release.set()

    backend = BlockingBackend()
    snapshots = []
    runtime = RemoteSessionRuntime(
        backend,
        on_snapshot=snapshots.append,
        join_timeout_seconds=0.03,
    )

    assert runtime.start_guest(_invitation()) is True
    assert entered.wait(1)
    settled = runtime.wait_until_settled(timeout=1)

    assert settled.phase is RemoteSessionPhase.FAILED
    assert settled.stage is RemoteSessionStage.NEEDS_ATTENTION
    assert settled.error_code is RemoteSessionErrorCode.TIMED_OUT
    assert not settled.invitation_retry_safe
    assert runtime._pending_invitation is None
    assert release.wait(1)
    deadline = time.monotonic() + 1
    while backend.stopped != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert backend.stopped == 1
    time.sleep(0.02)
    assert runtime.snapshot == settled
    assert snapshots[-1] == settled


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), 301])
def test_join_timeout_must_be_positive_and_bounded(timeout: float) -> None:
    with pytest.raises(ValueError, match="join_timeout_seconds"):
        RemoteSessionRuntime(
            Backend(),
            on_snapshot=lambda _value: None,
            join_timeout_seconds=timeout,
        )


def test_stop_invalidates_late_worker_result() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingBackend(Backend):
        def start_guest(self, invitation, *, generation):
            entered.set()
            assert release.wait(2)
            return super().start_guest(invitation, generation=generation)

    backend = BlockingBackend()
    runtime = RemoteSessionRuntime(backend, on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())
    assert entered.wait(1)

    runtime.stop()
    release.set()
    time.sleep(0.02)

    assert runtime.snapshot.phase is RemoteSessionPhase.STOPPED
    assert backend.stopped == 1
