from __future__ import annotations

from core.session_lifecycle import SessionLifecycle, SessionLifecyclePhase


def test_lifecycle_records_truthful_host_path_and_bounded_public_timeline():
    lifecycle = SessionLifecycle(role="host", max_events=3)

    assert lifecycle.transition(SessionLifecyclePhase.PREPARING, reason="host selected")
    assert lifecycle.transition(SessionLifecyclePhase.RUNNING_PREFLIGHT, reason="band check")
    assert lifecycle.transition(SessionLifecyclePhase.STARTING_HOST, reason="starting owned server")
    assert lifecycle.transition(
        SessionLifecyclePhase.WAITING_FOR_REACHABILITY,
        reason="waiting for private LAN address",
    )
    assert lifecycle.transition(SessionLifecyclePhase.READY_TO_SHARE, reason="private invite ready")

    snapshot = lifecycle.snapshot
    assert snapshot.phase is SessionLifecyclePhase.READY_TO_SHARE
    assert snapshot.role == "host"
    assert snapshot.transition_count == 5
    timeline = lifecycle.public_timeline()
    assert len(timeline) == 3
    assert timeline[-1]["to_state"] == "ready_to_share"
    assert set(timeline[-1]) == {
        "at", "component", "event", "from_state", "to_state", "status", "reason"
    }


def test_terminal_lifecycle_rejects_stale_worker_callback_until_reset():
    lifecycle = SessionLifecycle(role="join")
    assert lifecycle.transition(SessionLifecyclePhase.JOINING)
    assert lifecycle.transition(SessionLifecyclePhase.ENDING)
    assert lifecycle.transition(SessionLifecyclePhase.COMPLETED)

    assert not lifecycle.transition(SessionLifecyclePhase.CONNECTED)
    assert lifecycle.reset()
    assert lifecycle.phase is SessionLifecyclePhase.IDLE
    assert lifecycle.transition(SessionLifecyclePhase.JOINING)


def test_reset_closes_an_active_attempt_through_ending_before_idle():
    lifecycle = SessionLifecycle(role="host")
    assert lifecycle.transition(SessionLifecyclePhase.STARTING_HOST)

    assert lifecycle.reset(reason="host cancelled")

    assert lifecycle.phase is SessionLifecyclePhase.IDLE
    assert [event["to_state"] for event in lifecycle.public_timeline()] == [
        "starting_host",
        "ending",
        "completed",
        "idle",
    ]


def test_same_transition_is_idempotent_and_preserves_latest_recovery_attempt():
    lifecycle = SessionLifecycle()
    assert lifecycle.transition(SessionLifecyclePhase.JOINING)
    assert lifecycle.transition(SessionLifecyclePhase.RECONNECTING, recovery_attempt=1)
    assert lifecycle.transition(SessionLifecyclePhase.RECONNECTING, recovery_attempt=2)

    assert lifecycle.snapshot.recovery_attempt == 2
    assert lifecycle.snapshot.transition_count == 2


def test_public_snapshot_and_timeline_do_not_retain_private_token_value():
    lifecycle = SessionLifecycle()
    lifecycle.transition(
        SessionLifecyclePhase.PREPARING,
        reason="private token=secret-value",
    )

    assert "last_reason" not in lifecycle.snapshot.to_public_dict()
    assert "secret-value" not in str(lifecycle.public_timeline())
