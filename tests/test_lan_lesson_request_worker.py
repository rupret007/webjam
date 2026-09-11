"""Explicit guest intents share the existing observer worker, with no real I/O."""
from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.lesson_request import (
    MAX_REVISION,
    LessonRequestError,
    LessonRequestIntent,
    LessonRequestReceipt,
    LessonRequestStore,
    LessonRequestView,
)
from core.network_invite import BandInvite
from core.session_transfer import (
    LanRoomPollResult,
    RecordingSignal,
    SessionCredentials,
    SessionPeerClient,
    SessionStateSnapshot,
    SessionTransferError,
    TransferAuthenticationError,
)
from services.lan_room_guest import LanRoomGuest, LanRoomTerminalReason


class Clock:
    value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class MemoryPeer:
    """Real bounded store, controlled authenticated HTTP response boundary."""

    participant = "10000000-0000-4000-8000-000000000001"

    def __init__(self, clock, snapshot):
        self.clock, self.snapshot = clock, snapshot
        self.store = LessonRequestStore(clock=clock)
        self.store.activate()
        self.calls = []
        self.commands = []
        self.post_hook = None
        self.get_hook = None

    def enroll(self, *_args):
        self.calls.append("enroll")
        return object()

    def state(self, _enrollment):
        self.calls.append("legacy_get")
        return self.snapshot

    def state_with_lesson_requests(self, _enrollment):
        self.calls.append("get")
        view = self.store.record_state_read(self.participant, read_at=self.clock())
        if self.get_hook is not None:
            view = self.get_hook(view)
        return LanRoomPollResult(self.snapshot, view)

    def post_lesson_request(self, _enrollment, command):
        self.calls.append("post")
        self.commands.append(command)
        if self.post_hook is not None:
            return self.post_hook(command)
        return self.store.submit(self.participant, command)


@pytest.fixture
def rig(monkeypatch):
    clock = Clock()
    credentials = SessionCredentials.create()
    invite = BandInvite("192.168.1.20", session_id=credentials.session_id,
                        peer_port=22125, invite_token=credentials.invite_token)
    snapshot = SessionStateSnapshot(credentials.session_id, 0, RecordingSignal.IDLE,
                                    creator_profile_key="art")
    peer = MemoryPeer(clock, snapshot)
    monkeypatch.setattr("services.lan_room_guest.SessionPeerClient", lambda *a, **k: peer)
    states, features, losses = [], [], []
    owner = LanRoomGuest(invite, display_name="Synthetic artist",
                         on_state=lambda *args: states.append(args),
                         on_loss=lambda *args: losses.append(args),
                         on_lesson_request_state=lambda *args: features.append(args),
                         clock=clock)
    yield SimpleNamespace(owner=owner, peer=peer, clock=clock, states=states,
                          features=features, losses=losses, snapshot=snapshot)
    assert owner.stop()


def arm(rig):
    rig.owner.set_lesson_requests_enabled(True)
    assert not rig.owner.lesson_request_state.can_submit
    assert rig.owner.poll_once() == rig.snapshot
    assert rig.owner.lesson_request_state.status == "available"
    assert rig.owner.lesson_request_state.can_submit


def test_opening_and_polling_never_posts_but_explicit_intent_posts_once_then_gets(rig):
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.status == "inactive"
    arm(rig)
    for _ in range(3):
        rig.owner.poll_once()
    assert rig.peer.commands == []
    assert rig.owner.queue_lesson_request("pause")
    pending = rig.owner.lesson_request_state
    assert pending.status == "sending" and not pending.can_retry
    assert rig.peer.commands == [], "The caller/Qt thread cannot perform a POST"
    rig.peer.calls.clear()
    rig.owner.poll_once()
    assert rig.peer.calls == ["post", "get"]
    assert rig.owner.lesson_request_state.status == "accepted"
    assert not rig.owner.lesson_request_state.can_retry
    rig.owner.poll_once()
    assert len(rig.peer.commands) == 1
    assert len(rig.peer.store.host_notices()) == 1


def test_queue_coalesces_unsent_intents_and_never_reuses_allocated_revisions(rig):
    arm(rig)
    assert rig.owner.queue_lesson_request("pause")
    assert rig.owner.queue_lesson_request("ready")
    assert rig.owner.queue_lesson_request("pause")
    assert rig.owner.lesson_request_state.command.revision == 3
    rig.owner.poll_once()
    assert [command.revision for command in rig.peer.commands] == [3]
    assert rig.peer.commands[0].intent is LessonRequestIntent.PAUSE
    rig.clock.advance(2)
    assert rig.owner.queue_lesson_request("ready")
    rig.owner.set_lesson_requests_enabled(False)
    rig.owner.set_lesson_requests_enabled(True)
    rig.owner.poll_once()
    assert len(rig.peer.commands) == 1
    assert rig.owner.queue_lesson_request("ready")
    assert rig.owner.lesson_request_state.command.revision == 5


def test_one_inflight_plus_latest_queue_cannot_present_old_receipt_as_new_intent(rig):
    arm(rig)
    assert rig.owner.queue_lesson_request("pause")

    def during_post(command):
        assert rig.owner.queue_lesson_request("pause")
        assert rig.owner.queue_lesson_request("ready")
        return rig.peer.store.submit(rig.peer.participant, command)

    rig.peer.post_hook = during_post
    rig.owner.poll_once()
    latest = rig.owner.lesson_request_state
    assert latest.command.revision == 3 and latest.command.intent is LessonRequestIntent.READY
    assert latest.status == "sending" and not latest.can_retry
    assert all(state.status != "accepted" or state.command is None
               or state.command.revision != 3 for _, state in rig.features)
    assert len(rig.peer.commands) == 1
    rig.peer.post_hook = None
    rig.clock.advance(2)
    rig.owner.poll_once()
    assert [command.revision for command in rig.peer.commands] == [1, 3]
    assert rig.owner.lesson_request_state.status == "accepted"
    assert rig.peer.store.host_notices()[0].intent is LessonRequestIntent.READY


def test_lost_post_response_reconciles_from_get_without_resend_or_inferred_ack(rig):
    arm(rig)
    assert rig.owner.queue_lesson_request("pause")

    def accepted_but_lost(command):
        rig.peer.store.submit(rig.peer.participant, command)
        raise SessionTransferError("PRIVATE-HTTP-DETAIL")

    rig.peer.post_hook = accepted_but_lost
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.status == "accepted"
    assert not rig.owner.lesson_request_state.can_retry
    receipt = rig.owner.lesson_request_state.view.own_receipt
    assert receipt.state == "accepted"
    rig.owner.poll_once()
    assert len(rig.peer.commands) == 1
    command = rig.peer.commands[0]
    rig.peer.store.acknowledge(command.context_id, command.admission_id, command.revision)
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.status == "acknowledged"
    assert len(rig.peer.commands) == 1


def test_uncertain_delivery_retries_only_by_explicit_same_identity_gesture(rig):
    arm(rig)
    assert rig.owner.queue_lesson_request("pause")
    rig.peer.post_hook = Mock(side_effect=SessionTransferError("PRIVATE-HTTP-DETAIL"))
    rig.owner.poll_once()
    first = rig.peer.commands[0]
    assert rig.owner.lesson_request_state.status == "unconfirmed"
    assert rig.owner.lesson_request_state.can_retry
    for _ in range(3):
        rig.owner.poll_once()
    assert len(rig.peer.commands) == 1
    rig.peer.post_hook = None
    assert rig.owner.retry_lesson_request()
    assert not rig.owner.retry_lesson_request()
    assert len(rig.peer.commands) == 1
    rig.owner.poll_once()
    assert rig.peer.commands == [first, first]
    assert rig.owner.lesson_request_state.status == "accepted"


def test_local_retry_window_cannot_claim_late_host_acceptance_has_expired(rig):
    arm(rig)
    without_receipt = rig.owner.lesson_request_state.view
    assert rig.owner.queue_lesson_request("pause")

    def accepted_late_but_lost(command):
        rig.clock.advance(0.8)
        rig.peer.store.submit(rig.peer.participant, command)
        raise SessionTransferError("PRIVATE-HTTP-DETAIL")

    rig.peer.post_hook = accepted_late_but_lost
    # An older, valid GET response cannot establish whether the POST arrived.
    rig.peer.get_hook = lambda _view: without_receipt
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.can_retry
    for _ in range(7):
        rig.clock.advance(4)
        rig.owner.poll_once()
    rig.clock.advance(1.3)
    rig.owner.poll_once()
    uncertain = rig.owner.lesson_request_state
    assert uncertain.status == "unconfirmed"
    assert uncertain.view.own_receipt is None
    assert not uncertain.can_retry and not rig.owner.retry_lesson_request()
    assert uncertain.can_submit
    assert len(rig.peer.commands) == 1
    notice = rig.peer.store.host_notices()[0]
    assert notice.state == "accepted" and 0 < notice.expires_in_ms < 1000

    # A later actual receipt can prove acceptance, and then its own expiry.
    rig.peer.get_hook = None
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.status == "accepted"
    rig.clock.advance(0.8)
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.status == "expired"
    assert len(rig.peer.commands) == 1
    assert rig.owner.queue_lesson_request("ready")
    assert rig.owner.lesson_request_state.command.revision == 2


@pytest.mark.parametrize("boundary", ["disable", "stop"])
def test_retirement_during_post_suppresses_late_result_and_never_resends(rig, boundary):
    arm(rig)
    rig.owner.queue_lesson_request("pause")
    callback_count = []

    def retiring(command):
        if boundary == "disable":
            rig.owner.set_lesson_requests_enabled(False)
        else:
            assert rig.owner.stop()
        callback_count.append(len(rig.features))
        return rig.peer.store.submit(rig.peer.participant, command)

    rig.peer.post_hook = retiring
    rig.owner.poll_once()
    assert len(rig.features) == callback_count[0]
    assert rig.owner.lesson_request_state.status == "inactive"
    assert not rig.owner.retry_lesson_request()
    assert not rig.owner.queue_lesson_request("ready")
    if boundary == "disable":
        rig.owner.set_lesson_requests_enabled(True)
        rig.owner.poll_once()
        assert len(rig.peer.commands) == 1


def test_held_get_cannot_rearm_after_disable_enable_and_does_not_overlap_poll(rig):
    entered, release = threading.Event(), threading.Event()
    arm(rig)

    def held_get(view):
        entered.set()
        assert release.wait(1.0)
        return view

    rig.peer.get_hook = held_get
    errors = []

    def poll():
        try:
            rig.owner.poll_once()
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=poll)
    worker.start()
    try:
        assert entered.wait(1.0)
        rig.owner.set_lesson_requests_enabled(False)
        rig.owner.set_lesson_requests_enabled(True)
        count = len(rig.features)
        assert rig.owner.poll_once() is None
    finally:
        release.set()
        worker.join(1.0)
    assert not worker.is_alive() and errors == []
    assert len(rig.features) == count
    assert not rig.owner.lesson_request_state.can_submit
    rig.peer.get_hook = None
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.can_submit


@pytest.mark.parametrize("code", ["invalid_request", "context_stale", "admission_stale",
                                 "presence_stale", "superseded", "revision_conflict",
                                 "revision_exhausted", "unsupported", "rate_limited"])
def test_feature_refusals_continue_the_base_room_without_automatic_retry(rig, code):
    arm(rig)
    rig.owner.queue_lesson_request("pause")
    rig.peer.post_hook = Mock(side_effect=LessonRequestError(
        code, retry_after_ms=2000 if code == "rate_limited" else None,
    ))
    rig.peer.calls.clear()
    assert rig.owner.poll_once() == rig.snapshot
    assert rig.peer.calls == ["post", "get"]
    assert rig.owner.connection_available and not rig.owner._stop.is_set()
    assert rig.losses == [] and rig.owner.terminal_reason is None
    assert not rig.owner.retry_lesson_request()
    rig.owner.poll_once()
    assert len(rig.peer.commands) == 1
    if code == "rate_limited":
        assert not rig.owner.lesson_request_state.can_submit
        assert rig.owner.lesson_request_state.retry_after_ms == 2000
        rig.clock.advance(2)
        assert rig.owner.lesson_request_state.can_submit
        assert rig.owner.queue_lesson_request("ready")


@pytest.mark.parametrize("code", ["context_stale", "admission_stale", "presence_stale",
                                 "superseded", "revision_conflict", "revision_exhausted", "expired"])
@pytest.mark.parametrize("newer_queued", [False, True])
def test_refused_authority_cannot_admit_another_gesture_before_fresh_get(rig, code, newer_queued):
    arm(rig)
    assert rig.owner.queue_lesson_request("pause")

    def refuse(command):
        if newer_queued:
            assert rig.owner.queue_lesson_request("ready")
        raise LessonRequestError(code)

    def before_fresh_get_delivery(view):
        current = rig.owner.lesson_request_state
        assert current.view is None
        assert current.error == code
        assert not current.can_submit and not current.can_retry
        assert not rig.owner.queue_lesson_request("pause")
        assert not rig.owner.retry_lesson_request()
        return view

    rig.peer.post_hook = refuse
    rig.peer.get_hook = before_fresh_get_delivery
    rig.peer.calls.clear()
    assert rig.owner.poll_once() == rig.snapshot
    assert rig.peer.calls == ["post", "get"]
    assert rig.owner.connection_available and rig.losses == []
    assert rig.owner.lesson_request_state.can_submit
    rig.peer.get_hook = None
    rig.owner.poll_once()
    assert len(rig.peer.commands) == 1, "Fresh GET must not replay canceled intent"
    assert rig.owner.queue_lesson_request("pause")
    assert rig.owner.lesson_request_state.command.revision == (3 if newer_queued else 2)


def test_unsupported_endpoint_cancels_newer_queued_intent_and_stays_disabled(rig):
    arm(rig)
    assert rig.owner.queue_lesson_request("pause")

    def unsupported_with_newer_intent(command):
        assert rig.owner.queue_lesson_request("ready")
        raise LessonRequestError("unsupported")

    def before_fresh_get_delivery(view):
        current = rig.owner.lesson_request_state
        assert current.view is None and current.error == "unsupported"
        assert not current.can_submit and not current.can_retry
        assert not rig.owner.queue_lesson_request("pause")
        return view

    rig.peer.post_hook = unsupported_with_newer_intent
    rig.peer.get_hook = before_fresh_get_delivery
    rig.peer.calls.clear()
    assert rig.owner.poll_once() == rig.snapshot
    assert rig.peer.calls == ["post", "get"]
    assert rig.owner.connection_available and rig.losses == []
    rig.peer.get_hook = None
    for _ in range(3):
        rig.owner.poll_once()
        assert not rig.owner.lesson_request_state.can_submit
        assert not rig.owner.retry_lesson_request()
    assert len(rig.peer.commands) == 1
    assert rig.states[-1] == (rig.owner, rig.snapshot)
    # Only a new explicit helper entry may probe capability again, and it
    # cannot replay either canceled gesture or reset allocated revisions.
    rig.owner.set_lesson_requests_enabled(True)
    assert not rig.owner.lesson_request_state.can_submit
    rig.owner.poll_once()
    assert len(rig.peer.commands) == 1
    assert rig.owner.queue_lesson_request("pause")
    assert rig.owner.lesson_request_state.command.revision == 3


def test_actual_authentication_rejection_stops_before_another_get(rig):
    arm(rig)
    rig.owner.queue_lesson_request("pause")
    rig.peer.post_hook = Mock(side_effect=TransferAuthenticationError("PRIVATE-AUTH"))
    rig.peer.calls.clear()
    rig.owner._run()
    assert rig.peer.calls == ["post"]
    assert rig.owner.terminal_reason is LanRoomTerminalReason.INVITATION_REJECTED
    assert rig.losses == [(rig.owner, True)]
    assert rig.owner.last_state is None and not rig.owner.connection_available
    assert not rig.owner.lesson_request_state.can_submit


@pytest.mark.parametrize("feature", [None, object(), LessonRequestView("unavailable", reason="capacity")])
def test_removed_or_unusable_feature_retires_queued_authority_but_preserves_room(rig, feature):
    arm(rig)
    rig.peer.get_hook = lambda _view: feature
    rig.owner.poll_once()
    assert rig.owner.connection_available and rig.states[-1][1] == rig.snapshot
    assert not rig.owner.queue_lesson_request("pause")
    assert not rig.owner.lesson_request_state.can_submit
    assert rig.peer.commands == []


def test_receipt_ages_by_http_elapsed_and_ui_delay_without_new_ttl_on_get(rig):
    arm(rig)
    rig.owner.queue_lesson_request("pause")

    def delayed_reply(command):
        result = rig.peer.store.submit(rig.peer.participant, command)
        rig.clock.advance(2)
        return result

    rig.peer.post_hook = delayed_reply
    rig.owner.poll_once()
    state = rig.owner.lesson_request_state
    assert state.status == "accepted" and state.view.own_receipt.expires_in_ms <= 28_000
    assert state.observed_at == rig.clock()
    original_deadline = state.observed_at + state.view.own_receipt.expires_in_ms / 1000
    for _ in range(7):
        rig.clock.advance(4)
        rig.owner.poll_once()
        current = rig.owner.lesson_request_state
        assert current.observed_at + current.view.own_receipt.expires_in_ms / 1000 <= original_deadline
    assert rig.owner.lesson_request_state.status == "expired"
    assert not rig.owner.lesson_request_state.can_retry
    assert rig.owner.lesson_request_state.can_submit
    assert state.view.own_receipt.state == "accepted", "Queued DTO is immutable, not freshly renewed"


def test_stale_get_cannot_grant_fresh_authority_or_receipt(rig):
    rig.owner.set_lesson_requests_enabled(True)

    def slow_get(view):
        rig.clock.advance(5)
        return view

    rig.peer.get_hook = slow_get
    assert rig.owner.poll_once() == rig.snapshot
    assert rig.owner.connection_available, "Existing base-room receipt semantics stay unchanged"
    assert not rig.owner.lesson_request_state.can_submit
    assert not rig.owner.queue_lesson_request("pause")


def test_context_change_retires_old_retry_and_server_revision_ceiling_never_wraps(rig):
    arm(rig)
    rig.owner.queue_lesson_request("pause")
    rig.peer.post_hook = Mock(side_effect=SessionTransferError("PRIVATE-HTTP-DETAIL"))
    rig.owner.poll_once()
    old = rig.owner.lesson_request_state.command
    rig.peer.store.retire()
    rig.peer.store.activate()
    rig.owner.poll_once()
    assert not rig.owner.retry_lesson_request()
    assert rig.owner.queue_lesson_request("ready")
    assert rig.owner.lesson_request_state.command.context_id != old.context_id
    assert rig.owner.lesson_request_state.command.revision == 1
    rig.owner.set_lesson_requests_enabled(False)
    rig.owner.set_lesson_requests_enabled(True)
    original = rig.peer.store.current_view(rig.peer.participant)
    rig.peer.get_hook = lambda _view: LessonRequestView(
        "active", original.context_id, original.admission_id, MAX_REVISION,
        LessonRequestReceipt(MAX_REVISION - 1, "pause", "expired", 0),
    )
    rig.owner.poll_once()
    assert rig.owner.queue_lesson_request("pause")
    assert rig.owner.lesson_request_state.command.revision == MAX_REVISION
    assert not rig.owner.queue_lesson_request("ready")
    rig.peer.get_hook = rig.peer.post_hook = None
    rig.owner.poll_once()
    assert rig.peer.commands[-1].revision == MAX_REVISION
    assert not rig.owner.lesson_request_state.can_submit
    assert not rig.owner.queue_lesson_request("pause")


def test_poll_loss_disarms_and_abandons_intent_until_explicit_helper_entry(rig):
    arm(rig)
    rig.owner.queue_lesson_request("pause")
    rig.peer.get_hook = Mock(side_effect=SessionTransferError("PRIVATE-GET-DETAIL"))
    with pytest.raises(SessionTransferError):
        rig.owner.poll_once()
    rig.peer.get_hook = None
    rig.owner.poll_once()
    assert not rig.owner.lesson_request_state.can_submit
    rig.owner.set_lesson_requests_enabled(True)
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.can_submit
    assert len(rig.peer.commands) == 1 and not rig.owner.retry_lesson_request()


def test_legacy_state_fake_never_falls_through_to_real_feature_http(rig):
    client = SessionPeerClient(rig.owner.invite.host, rig.owner.invite.peer_port,
                               credentials=SessionCredentials.create())
    client.enroll = Mock(return_value=object())
    client.state = Mock(return_value=rig.snapshot)
    client._request = Mock(side_effect=AssertionError("No real HTTP under a state fake"))
    rig.owner.client = client
    rig.owner.set_lesson_requests_enabled(True)
    assert rig.owner.poll_once() == rig.snapshot
    client.state.assert_called_once()
    client._request.assert_not_called()
    assert not rig.owner.lesson_request_state.can_submit


def test_wrong_room_and_non_art_never_grant_feature_authority(rig):
    rig.owner.set_lesson_requests_enabled(True)
    rig.peer.snapshot = SessionStateSnapshot(SessionCredentials.create().session_id, 0,
                                             RecordingSignal.IDLE, creator_profile_key="art")
    with pytest.raises(SessionTransferError):
        rig.owner.poll_once()
    assert rig.states == [] and not rig.owner.lesson_request_state.can_submit
    rig.peer.snapshot = SessionStateSnapshot(rig.snapshot.session_id, 0, RecordingSignal.IDLE,
                                             creator_profile_key="music")
    rig.owner.set_lesson_requests_enabled(True)
    rig.owner.poll_once()
    assert rig.owner.last_state.creator_profile_key == "music"
    assert not rig.owner.queue_lesson_request("pause")


def test_projection_is_immutable_private_and_invalid_intents_never_queue(rig):
    arm(rig)
    for invalid in [None, True, 1, "PLAY", "seek", "https://private.invalid"]:
        assert not rig.owner.queue_lesson_request(invalid)
    rig.owner.queue_lesson_request("pause")
    state = rig.owner.lesson_request_state
    with pytest.raises(FrozenInstanceError):
        state.status = "acknowledged"
    assert state.command.context_id not in repr(state)
    assert state.command.admission_id not in repr(state)
    assert not rig.peer.commands


def test_same_revision_changed_intent_cannot_inherit_an_old_acknowledgement(rig):
    arm(rig)
    rig.owner.queue_lesson_request("pause")
    rig.owner.poll_once()
    command = rig.peer.commands[0]
    rig.peer.store.acknowledge(command.context_id, command.admission_id, command.revision)
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.status == "acknowledged"
    rig.owner.set_lesson_requests_enabled(False)
    rig.owner.set_lesson_requests_enabled(True)
    rig.owner.poll_once()
    assert rig.owner.lesson_request_state.command is None
    rig.peer.get_hook = lambda _view: LessonRequestView(
        "active", command.context_id, command.admission_id, command.revision + 1,
        LessonRequestReceipt(command.revision, "ready", "accepted", 30_000),
    )
    rig.owner.poll_once()
    state = rig.owner.lesson_request_state
    assert state.status == "inactive" and state.error == "revision_conflict"
    assert not state.can_submit and not state.can_retry


def test_delayed_ui_identity_expires_and_successful_stop_wipes_retained_admission(rig):
    arm(rig)
    rig.owner.queue_lesson_request("pause")
    old = rig.owner.lesson_request_state
    rig.clock.advance(5)
    assert rig.owner.lesson_request_state is not old
    assert not rig.owner.lesson_request_state.can_submit
    rig.owner.poll_once()
    assert not rig.peer.commands, "Ageing an unsent intent must retire it, never replay it"
    assert rig.owner.stop()
    assert rig.owner._lesson_admission is None
    assert rig.owner._lesson_receipt_key is None
    assert rig.owner._lesson_allocated == 0
    assert rig.owner.lesson_request_state.command is None
