from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from core.lesson_request import (
    MAX_REVISION, LessonRequestCommand, LessonRequestError,
    LessonRequestIntent, LessonRequestReceipt, LessonRequestStore, LessonRequestView,
)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def room():
    clock = Clock()
    store = LessonRequestStore(clock=clock)
    store.activate()
    participant = str(uuid.uuid4())
    view = store.record_state_read(participant, read_at=clock())
    command = LessonRequestCommand(view.context_id, view.admission_id, 1, "pause")
    return clock, store, participant, command


def test_lost_pause_cannot_replace_newer_ready_after_expiry_or_full_capacity(room):
    clock, store, participant, pause = room
    first = store.submit(participant, pause)
    assert first.own_receipt.state == "accepted"
    clock.now += 2
    ready = replace(pause, revision=2, intent="ready")
    assert store.submit(participant, ready).own_receipt.intent is LessonRequestIntent.READY
    for _ in range(31):
        store.record_state_read(str(uuid.uuid4()), read_at=clock())
    clock.now += 30
    store.record_state_read(participant, read_at=clock())
    capacity = store.record_state_read(str(uuid.uuid4()), read_at=clock())
    assert capacity.availability == "unavailable" and capacity.reason == "capacity"
    with pytest.raises(LessonRequestError, match="superseded"):
        store.submit(participant, pause)
    duplicate = store.submit(participant, ready)
    assert duplicate.own_receipt.state == "expired"
    assert duplicate.own_receipt.expires_in_ms == 0
    assert duplicate.next_revision == 3
    assert store.host_notices() == ()


def test_duplicate_preserves_ack_deadline_and_rate_budget(room):
    clock, store, participant, command = room
    store.submit(participant, command)
    clock.now += 1
    store.acknowledge(command.context_id, command.admission_id, 1)
    for _ in range(50):
        view = store.submit(participant, command)
        assert view.own_receipt.state == "acknowledged"
        assert view.own_receipt.expires_in_ms == 29_000
    clock.now += 1
    view = store.submit(participant, replace(command, revision=2, intent="ready"))
    assert view.own_receipt.state == "accepted"
    assert view.own_receipt.expires_in_ms == 30_000


def test_newer_ack_cannot_be_completed_by_old_button(room):
    clock, store, participant, command = room
    store.submit(participant, command)
    clock.now += 2
    store.submit(participant, replace(command, revision=2, intent="ready"))
    with pytest.raises(LessonRequestError, match="superseded"):
        store.acknowledge(command.context_id, command.admission_id, 1)
    assert store.current_view(participant).own_receipt.state == "accepted"


def test_presence_is_only_refreshed_by_state_read_and_exact_boundary_refuses(room):
    clock, store, participant, command = room
    store.submit(participant, command)
    clock.now += 4.999
    store.submit(participant, command)
    store.host_notices()
    store.current_view(participant)
    clock.now += .001
    with pytest.raises(LessonRequestError, match="presence_stale"):
        store.submit(participant, command)
    with pytest.raises(LessonRequestError, match="presence_stale"):
        store.acknowledge(command.context_id, command.admission_id, 1)
    assert store.host_notices() == ()
    store.record_state_read(participant, read_at=clock())
    store.acknowledge(command.context_id, command.admission_id, 1)
    assert store.current_view(participant).own_receipt.state == "acknowledged"


def test_retirement_rejects_old_requests_and_acks_and_new_admission_is_distinct(room):
    clock, store, participant, command = room
    store.submit(participant, command)
    store.retire()
    assert store.current_view(participant).availability == "unavailable"
    assert store.host_notices() == ()
    with pytest.raises(LessonRequestError, match="context_stale"):
        store.submit(participant, command)
    store.activate()
    new = store.record_state_read(participant, read_at=clock())
    assert new.context_id != command.context_id
    assert new.admission_id != command.admission_id
    assert new.next_revision == 1 and new.own_receipt is None
    with pytest.raises(LessonRequestError, match="context_stale"):
        store.acknowledge(command.context_id, command.admission_id, 1)


def test_other_participant_cannot_use_admission(room):
    clock, store, _participant, command = room
    other = str(uuid.uuid4())
    store.record_state_read(other, read_at=clock())
    with pytest.raises(LessonRequestError, match="admission_stale"):
        store.submit(other, command)
    assert store.current_view(other).own_receipt is None


def test_participant_rate_refusal_does_not_advance_and_gaps_are_legal(room):
    clock, store, participant, command = room
    store.submit(participant, command)
    with pytest.raises(LessonRequestError) as caught:
        store.submit(participant, replace(command, revision=9, intent="ready"))
    assert caught.value.code == "rate_limited"
    assert caught.value.retry_after_ms == 2000
    assert store.current_view(participant).next_revision == 2
    clock.now += 2
    assert store.submit(participant, replace(command, revision=9)).next_revision == 10


def test_global_rate_applies_across_separate_enrollments_and_duplicates_do_not_charge(room):
    clock, store, participant, command = room
    store.submit(participant, command)
    for _ in range(40):
        store.submit(participant, command)
    for _ in range(19):
        other = str(uuid.uuid4())
        view = store.record_state_read(other, read_at=clock())
        store.submit(other, LessonRequestCommand(view.context_id, view.admission_id, 1, "pause"))
    last = str(uuid.uuid4())
    view = store.record_state_read(last, read_at=clock())
    final = LessonRequestCommand(view.context_id, view.admission_id, 1, "ready")
    with pytest.raises(LessonRequestError) as caught:
        store.submit(last, final)
    assert caught.value.code == "rate_limited" and caught.value.retry_after_ms == 10_000
    assert store.current_view(last).own_receipt is None
    clock.now += 10
    store.record_state_read(last, read_at=clock())
    assert store.submit(last, final).own_receipt.intent is LessonRequestIntent.READY


def test_concurrent_duplicates_create_one_record_and_one_global_charge(room):
    _clock, store, participant, command = room
    barrier = threading.Barrier(12)

    def send():
        barrier.wait(timeout=2)
        return store.submit(participant, command).to_mapping()

    with ThreadPoolExecutor(max_workers=12) as executor:
        receipts = list(executor.map(lambda _: send(), range(12)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(store.host_notices()) == 1
    assert len(store._new_requests) == 1


def test_revision_ceiling_never_wraps_and_duplicate_payload_cannot_change(room):
    _clock, store, participant, command = room
    command = replace(command, revision=MAX_REVISION)
    assert store.submit(participant, command).next_revision is None
    with pytest.raises(LessonRequestError, match="revision_conflict"):
        store.submit(participant, replace(command, intent="ready"))
    with pytest.raises(LessonRequestError, match="invalid_request"):
        replace(command, revision=MAX_REVISION + 1)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("revision", True), ("revision", 0),
    ("revision", -1), ("revision", 1.0), ("revision", "1"),
    ("revision", MAX_REVISION + 1), ("intent", "play"), ("intent", []),
    ("context_id", "https://private.invalid/secret"), ("context_id", "A" * 32),
    ("admission_id", "a" * 31), ("admission_id", None), ("extra", "secret"),
])
def test_command_wire_rejects_malformed_fields_without_echo(room, field, value):
    command = room[3]
    payload = command.to_mapping()
    payload[field] = value
    with pytest.raises(LessonRequestError) as caught:
        LessonRequestCommand.from_mapping(payload)
    assert str(caught.value) == "Lesson request unavailable (invalid_request)."


@pytest.mark.parametrize("value", [None, [], "secret", {}, {"version": 1},
    {"version": 1, "availability": "unavailable", "reason": "unknown"}])
def test_optional_view_parser_rejects_invalid_shapes(value):
    with pytest.raises(LessonRequestError):
        LessonRequestView.from_mapping(value)


def test_wire_roundtrip_and_repr_never_exposes_context_or_participant(room):
    _clock, store, participant, command = room
    view = store.submit(participant, command)
    assert LessonRequestCommand.from_mapping(json.loads(json.dumps(command.to_mapping()))) == command
    assert LessonRequestView.from_mapping(json.loads(json.dumps(view.to_mapping()))) == view
    for value in (command, view, view.own_receipt, store.host_notices()[0]):
        assert command.context_id not in repr(value)
        assert command.admission_id not in repr(value)
        assert participant not in repr(value)


@pytest.mark.parametrize("state,remaining", [("paused", 1), ("expired", 1), ("accepted", 0), ("accepted", True)])
def test_receipt_cannot_claim_playback_or_contradict_expiry(state, remaining):
    with pytest.raises(LessonRequestError):
        LessonRequestReceipt(1, "pause", state, remaining)
