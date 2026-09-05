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
from services.transport_runtime import TransportEvent


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


class HelpBackend(Backend):
    def __init__(self) -> None:
        super().__init__()
        self.help_available = False
        self.generation = 0
        self.sent = []

    def start_guest(self, invitation, *, generation):
        result = super().start_guest(invitation, generation=generation)
        self.generation = generation
        self.help_available = True
        return result

    def send_help(self, text, *, expected_generation=None):
        if expected_generation is not None and expected_generation != self.generation:
            raise RemoteBackendError(RemoteSessionErrorCode.TRANSPORT_FAILED)
        self.sent.append(text)
        return TransportEvent(
            event_id=31,
            event_type="help_accepted",
            code="ok",
            state="connected",
            mode="guest",
            generation=self.generation,
            request_id=31,
        )

    def stop(self) -> None:
        super().stop()
        self.help_available = False


def test_runtime_help_requires_current_backend_proof_and_forwards_receipt() -> None:
    backend = HelpBackend()
    runtime = RemoteSessionRuntime(backend, on_snapshot=lambda _value: None)
    assert not runtime.help_available
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        runtime.send_help("not connected")
    assert backend.sent == []
    runtime.start_guest(_invitation())
    runtime.wait_until_settled()
    assert runtime.help_available
    accepted = runtime.send_help("Try headphones")
    assert backend.sent == ["Try headphones"]
    assert accepted.event_type == "help_accepted"
    assert accepted.generation == runtime.snapshot.generation
    backend.help_available = False
    assert not runtime.help_available
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        runtime.send_help("disconnected")
    assert backend.sent == ["Try headphones"]
    runtime.stop()


def test_runtime_legacy_backend_has_no_implicit_help_capability() -> None:
    runtime = RemoteSessionRuntime(Backend(), on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())
    runtime.wait_until_settled()
    assert not runtime.help_available
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        runtime.send_help("unsupported")
    runtime.stop()


def test_runtime_stop_does_not_wait_for_send_and_rejects_old_generation_result() -> None:
    entered = threading.Event()
    release = threading.Event()
    results = []

    class DelayedHelpBackend(HelpBackend):
        def send_help(self, text, *, expected_generation=None):
            accepted = super().send_help(text, expected_generation=expected_generation)
            entered.set()
            assert release.wait(2)
            return accepted

    backend = DelayedHelpBackend()
    runtime = RemoteSessionRuntime(backend, on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())
    runtime.wait_until_settled()

    def send():
        try:
            results.append(runtime.send_help("old room"))
        except RemoteBackendError as error:
            results.append(error)

    worker = threading.Thread(target=send)
    worker.start()
    assert entered.wait(1)
    try:
        runtime.stop()
        runtime.start_guest(_invitation())
        runtime.wait_until_settled()
        assert runtime.help_available
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert len(results) == 1
    assert isinstance(results[0], RemoteBackendError)
    assert results[0].code is RemoteSessionErrorCode.TRANSPORT_FAILED
    runtime.stop()


def test_runtime_rejects_receipt_after_backend_replacement() -> None:
    backend = HelpBackend()
    runtime = RemoteSessionRuntime(backend, on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())
    runtime.wait_until_settled()
    send = backend.send_help

    def replace(text, *, expected_generation=None):
        accepted = send(text, expected_generation=expected_generation)
        replacement = HelpBackend()
        replacement.help_available = True
        runtime._backend = replacement
        return accepted

    backend.send_help = replace
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        runtime.send_help("replaced owner")
    runtime.stop()


def test_runtime_binds_generation_before_restart_racing_backend_dispatch() -> None:
    class RestartBeforeDispatch(HelpBackend):
        def send_help(self, text, *, expected_generation=None):
            runtime.stop()
            runtime.start_guest(_invitation())
            runtime.wait_until_settled()
            return super().send_help(text, expected_generation=expected_generation)

    backend = RestartBeforeDispatch()
    runtime = RemoteSessionRuntime(backend, on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())
    first = runtime.wait_until_settled()
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        runtime.send_help("old room draft", expected_generation=first.generation)
    assert runtime.snapshot.generation != first.generation
    assert runtime.help_available
    assert backend.sent == []
    runtime.stop()


def test_runtime_rejects_stale_caller_generation_before_backend_entry() -> None:
    backend = HelpBackend()
    runtime = RemoteSessionRuntime(backend, on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())
    current = runtime.wait_until_settled()
    for generation in [current.generation + 1, True]:
        with pytest.raises(RemoteBackendError, match="^transport_failed$"):
            runtime.send_help("stale caller", expected_generation=generation)
    assert backend.sent == []
    runtime.send_help("current caller", expected_generation=current.generation)
    assert backend.sent == ["current caller"]
    runtime.stop()


@pytest.mark.parametrize("field,value", [
    ("mode", "host"), ("generation", 99), ("event_type", "help_delivered"),
    ("code", "help_rate_limited"), ("state", "failed"), ("request_id", 99),
    ("help_text", "PRIVATE-TEXT"),
])
def test_runtime_rejects_mismatched_acceptance_receipts(field, value) -> None:
    from dataclasses import replace

    backend = HelpBackend()
    runtime = RemoteSessionRuntime(backend, on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())
    runtime.wait_until_settled()
    send = backend.send_help
    backend.send_help = lambda text, **kwargs: replace(send(text, **kwargs), **{field: value})
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        runtime.send_help("private draft")
    runtime.stop()


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_runtime_help_failures_never_expose_backend_text(caplog, error_type) -> None:
    class FailingBackend(HelpBackend):
        def send_help(self, text, *, expected_generation=None):
            raise error_type("PRIVATE-MESSAGE-SENTINEL")

    runtime = RemoteSessionRuntime(FailingBackend(), on_snapshot=lambda _value: None)
    runtime.start_guest(_invitation())
    runtime.wait_until_settled()
    with pytest.raises((ValueError, RemoteBackendError)) as caught:
        runtime.send_help("PRIVATE-MESSAGE-SENTINEL")
    assert "PRIVATE-MESSAGE-SENTINEL" not in str(caught.value)
    assert "PRIVATE-MESSAGE-SENTINEL" not in caplog.text
    runtime.stop()
