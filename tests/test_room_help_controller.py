"""Offline presentation tests: real transport vocabulary, no peer or sockets."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import replace

import pytest

from core.session_transport import SessionRole
from services.remote_session_runtime import RemoteSessionPhase, RemoteSessionSnapshot
from services.transport_runtime import TransportEvent
from webjam_qt.controllers.room_help import (
    ACCEPTED_STATUS,
    ACKNOWLEDGED_STATUS,
    INVALID_TEXT_STATUS,
    MAX_EARLY_ACKS,
    MAX_HELP_ENTRIES,
    MAX_HELP_INGRESS,
    SEND_FAILED_STATUS,
    UNAVAILABLE_REASON,
    RoomHelpController,
)


class Signal:
    def connect(self, callback):
        self.callback = callback

    def emit(self, text):
        self.callback(text)


class Panel:
    def __init__(self):
        self.submitted = Signal()
        self.draft = ""
        self.entries = ()
        self.status = ""
        self.pending = False
        self.available = False
        self.visible = False
        self.ui_thread = threading.get_ident()

    def _ui(self):
        assert threading.get_ident() == self.ui_thread

    def setVisible(self, visible):
        self._ui()
        self.visible = visible

    def set_available(self, available, reason):
        self._ui()
        self.available = available
        self.reason = reason
        self.status = reason

    def set_pending(self, pending):
        self._ui()
        self.pending = pending

    def set_status(self, status):
        self._ui()
        self.status = status

    def set_entries(self, entries):
        self._ui()
        self.entries = tuple(entries)

    def draft_text(self):
        self._ui()
        return self.draft

    def clear_draft(self):
        self._ui()
        self.draft = ""

    def clear_session(self):
        self._ui()
        self.entries = ()
        self.draft = ""
        self.status = ""


class Schedule:
    def __init__(self):
        self.callbacks = deque()
        self.lock = threading.Lock()

    def __call__(self, callback):
        with self.lock:
            self.callbacks.append(callback)

    def flush(self):
        while True:
            with self.lock:
                if not self.callbacks:
                    return
                callback = self.callbacks.popleft()
            callback()


def event(kind, request_id=1, *, role="host", generation=1, text=""):
    return TransportEvent(
        event_id=request_id if kind == "help_accepted" else 0,
        event_type=kind,
        code="ok",
        state="connected",
        mode=role,
        generation=generation,
        profile_id="reference-local-v1",
        request_id=request_id,
        help_text=text,
    )


class Source:
    def __init__(self, *, role=SessionRole.HOST, generation=1):
        self.snapshot = RemoteSessionSnapshot(
            phase=RemoteSessionPhase.CONNECTED, role=role, generation=generation
        )
        self.help_available = True
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.result = event("help_accepted", role=role.value, generation=generation)
        self.error = None

    def send_help(self, text, *, expected_generation):
        if expected_generation != self.snapshot.generation:
            raise RuntimeError("stale generation")
        self.calls.append(text)
        self.started.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test worker was not released")
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def setup():
    panel = Panel()
    schedule = Schedule()
    controller = RoomHelpController(panel, enabled=True, schedule_callback=schedule)
    source = Source()
    controller.update(source, source.snapshot)
    yield controller, panel, schedule, source
    source.release.set()
    controller.shutdown()


def finish(controller, schedule, source):
    source.release.set()
    deadline = time.monotonic() + 2
    while controller._work is not None and time.monotonic() < deadline:
        time.sleep(0.001)
    assert controller._work is None
    schedule.flush()


def send(controller, panel, source, text="Try headphones"):
    panel.draft = text
    panel.submitted.emit(text)
    assert source.started.wait(timeout=1)
    assert panel.pending


def test_disabled_by_default_and_no_authenticated_peer_means_no_send():
    panel = Panel()
    schedule = Schedule()
    controller = RoomHelpController(panel, schedule_callback=schedule)
    source = Source()
    controller.update(source, source.snapshot)
    panel.submitted.emit("This cannot leave")
    assert not panel.visible
    assert not panel.available
    assert source.calls == []


def test_send_is_nonblocking_single_inflight_and_receipt_is_not_optimistic(setup):
    controller, panel, schedule, source = setup
    send(controller, panel, source)
    panel.submitted.emit("Cannot send twice")
    assert source.calls == ["Try headphones"]
    assert panel.entries == ()
    assert panel.draft == "Try headphones"
    assert panel.status == "Sending once…"
    finish(controller, schedule, source)
    assert not panel.pending
    assert panel.draft == ""
    assert panel.entries == (("You", "Try headphones", ACCEPTED_STATUS),)
    assert panel.status == ACCEPTED_STATUS


@pytest.mark.parametrize("ack_first", [True, False])
def test_acknowledgement_binds_request_id_before_or_after_acceptance(setup, ack_first):
    controller, panel, schedule, source = setup
    send(controller, panel, source)
    if ack_first:
        controller.receive(event("help_delivered"), source=source)
        schedule.flush()
        assert panel.entries == ()
    finish(controller, schedule, source)
    if not ack_first:
        controller.receive(event("help_delivered"), source=source)
        schedule.flush()
    assert panel.entries == (("You", "Try headphones", ACKNOWLEDGED_STATUS),)
    assert panel.status == ACKNOWLEDGED_STATUS


def test_unknown_ack_never_turns_acceptance_into_acknowledgement(setup):
    controller, panel, schedule, source = setup
    send(controller, panel, source)
    controller.receive(event("help_delivered", 500), source=source)
    finish(controller, schedule, source)
    assert panel.entries[0][2] == ACCEPTED_STATUS
    controller.receive(event("help_delivered", 500), source=source)
    schedule.flush()
    assert panel.entries[0][2] == ACCEPTED_STATUS


def test_duplicate_acceptance_id_never_publishes_second_send_as_accepted(setup):
    controller, panel, schedule, source = setup
    send(controller, panel, source)
    finish(controller, schedule, source)
    send(controller, panel, source, "A second message")
    finish(controller, schedule, source)
    assert panel.entries == (("You", "Try headphones", ACCEPTED_STATUS),)
    assert panel.draft == "A second message"
    assert panel.status == SEND_FAILED_STATUS


def test_failure_keeps_draft_and_does_not_leak_backend_details(setup, caplog):
    controller, panel, schedule, source = setup
    source.error = RuntimeError("private message and secret service details")
    send(controller, panel, source)
    finish(controller, schedule, source)
    assert not panel.pending
    assert panel.draft == "Try headphones"
    assert panel.entries == ()
    assert panel.status == SEND_FAILED_STATUS
    assert "private message" not in caplog.text
    assert "secret service" not in repr(controller)


@pytest.mark.parametrize("kind", ["wrong_kind", "wrong_generation", "wrong_role", "wrong_id"])
def test_invalid_local_acceptance_never_clears_draft_or_claims_delivery(setup, kind):
    controller, panel, schedule, source = setup
    changes = {
        "wrong_kind": {"event_type": "help_delivered"},
        "wrong_generation": {"generation": 2},
        "wrong_role": {"mode": "guest"},
        "wrong_id": {"event_id": 2},
    }
    source.result = replace(source.result, **changes[kind])
    send(controller, panel, source)
    finish(controller, schedule, source)
    assert panel.draft == "Try headphones"
    assert panel.entries == ()
    assert panel.status == SEND_FAILED_STATUS


def test_reset_immediately_erases_queue_and_late_worker_and_callback_cannot_reappear(setup):
    controller, panel, schedule, source = setup
    send(controller, panel, source)
    controller.receive(event("help_received", text="Old private note"), source=source)
    thread = threading.Thread(target=controller.invalidate)
    thread.start()
    thread.join(timeout=1)
    assert not controller._inbox
    assert controller._work.text == ""
    assert len(schedule.callbacks) == 1
    schedule.flush()
    assert not panel.available
    assert panel.draft == ""
    finish(controller, schedule, source)
    assert panel.entries == ()
    assert panel.status == UNAVAILABLE_REASON


def test_replacement_owner_reusing_generation_cannot_receive_old_text_or_receipts(setup):
    controller, panel, schedule, source = setup
    send(controller, panel, source)
    controller.receive(event("help_received", text="Private old room"), source=source)
    replacement = Source()
    controller.update(replacement, replacement.snapshot)
    panel.draft = "New room draft"
    panel.submitted.emit(panel.draft)
    assert replacement.calls == []  # Still only one global worker.
    controller.receive(event("help_received", 2, text="Late old room"), source=source)
    finish(controller, schedule, source)
    assert panel.entries == ()
    assert panel.draft == "New room draft"
    assert panel.available
    assert not panel.pending
    assert panel.status == "Messages stay only in this session."
    replacement.release.set()
    send(controller, panel, replacement, "New room draft")
    finish(controller, schedule, replacement)
    assert panel.entries == (("You", "New room draft", ACCEPTED_STATUS),)


def test_same_owner_generation_change_and_sidecar_death_clear_all_text(setup):
    controller, panel, schedule, source = setup
    controller.receive(event("help_received", text="Private room"), source=source)
    schedule.flush()
    panel.draft = "Private draft"
    source.snapshot = replace(source.snapshot, generation=2)
    controller.poll_availability()
    assert panel.entries == ()
    assert panel.draft == ""
    assert not panel.available
    controller.update(source, source.snapshot)
    controller.receive(event("help_received", generation=2, text="New room"), source=source)
    schedule.flush()
    assert panel.entries
    source.help_available = False
    controller.poll_availability()
    assert panel.entries == ()
    assert not panel.available


def test_receive_thread_never_mutates_panel_and_replays_are_ignored(setup):
    controller, panel, schedule, source = setup
    incoming = event("help_received", 2, text="I can hear you")
    thread = threading.Thread(target=lambda: controller.receive(incoming, source=source))
    thread.start()
    thread.join(timeout=1)
    assert panel.entries == ()
    schedule.flush()
    for replay in (incoming, event("help_received", 1, text="Older replay")):
        controller.receive(replay, source=source)
    schedule.flush()
    assert panel.entries == (("Peer", "I can hear you", ""),)


def test_ingress_coalesces_one_callback_and_backpressure_clears_closed(setup):
    controller, panel, schedule, source = setup
    for request_id in range(1, MAX_HELP_INGRESS + 1):
        controller.receive(event("help_received", request_id, text="Bounded"), source=source)
    assert len(controller._inbox) == MAX_HELP_INGRESS
    assert len(schedule.callbacks) == 1
    controller.receive(event("help_received", 100, text="Too much"), source=source)
    assert not controller._inbox
    assert len(schedule.callbacks) == 1
    schedule.flush()
    assert panel.entries == ()
    assert not panel.available


def test_armed_first_frame_waits_for_exact_connected_proof(setup):
    controller, panel, schedule, source = setup
    source.snapshot = replace(source.snapshot, phase=RemoteSessionPhase.PREPARING)
    source.help_available = False
    controller.arm(source)
    controller.receive(event("help_received", text="First setup question"), source=source)
    controller.update(source, source.snapshot)
    controller.poll_availability()
    schedule.flush()
    assert len(controller._inbox) == 1
    assert panel.entries == ()
    assert not panel.available
    source.snapshot = replace(source.snapshot, phase=RemoteSessionPhase.CONNECTED)
    source.help_available = True
    controller.update(source, source.snapshot)
    assert panel.entries == ()
    assert len(schedule.callbacks) == 1
    schedule.flush()
    assert panel.entries == (("Peer", "First setup question", ""),)
    assert panel.available


@pytest.mark.parametrize("mismatch", ["role", "generation", "source", "proof"])
def test_armed_frame_mismatched_to_connected_proof_never_displays(setup, mismatch):
    controller, panel, schedule, source = setup
    controller.arm(source)
    incoming = event("help_received", text="Wrong binding")
    if mismatch == "role":
        incoming = replace(incoming, mode="guest")
    elif mismatch == "generation":
        incoming = replace(incoming, generation=2)
    controller.receive(incoming, source=Source() if mismatch == "source" else source)
    if mismatch == "proof":
        source.help_available = False
    controller.update(source, source.snapshot)
    schedule.flush()
    assert panel.entries == ()


@pytest.mark.parametrize("end", ["failure", "replace", "overflow", "shutdown", "invalidate"])
def test_armed_text_is_erased_on_failure_replacement_overflow_or_end(setup, end):
    controller, panel, schedule, source = setup
    controller.arm(source)
    controller.receive(event("help_received", text="Never carry this forward"), source=source)
    if end == "failure":
        source.snapshot = replace(source.snapshot, phase=RemoteSessionPhase.FAILED)
        controller.poll_availability()
    elif end == "replace":
        controller.arm(Source())
    elif end == "overflow":
        for request_id in range(2, MAX_HELP_INGRESS + 2):
            controller.receive(event("help_received", request_id, text="Overflow"), source=source)
    elif end == "shutdown":
        controller.shutdown()
    else:
        controller.invalidate()
    assert not controller._inbox
    schedule.flush()
    source.snapshot = replace(source.snapshot, phase=RemoteSessionPhase.CONNECTED)
    controller.update(source, source.snapshot)
    schedule.flush()
    assert panel.entries == ()


def test_previous_send_completion_does_not_erase_new_armed_first_frame(setup):
    controller, panel, schedule, source = setup
    send(controller, panel, source)
    replacement = Source()
    replacement.snapshot = replace(replacement.snapshot, phase=RemoteSessionPhase.PREPARING)
    replacement.help_available = False
    controller.arm(replacement)
    controller.receive(event("help_received", text="New first question"), source=replacement)
    finish(controller, schedule, source)
    assert len(controller._inbox) == 1
    assert panel.entries == ()
    replacement.snapshot = replace(replacement.snapshot, phase=RemoteSessionPhase.CONNECTED)
    replacement.help_available = True
    controller.update(replacement, replacement.snapshot)
    schedule.flush()
    assert panel.entries == (("Peer", "New first question", ""),)


@pytest.mark.parametrize("stale_phase", [RemoteSessionPhase.FAILED, RemoteSessionPhase.PREPARING])
def test_reset_rearm_preserves_new_first_frame_across_queued_old_generation_snapshot(setup, stale_phase):
    controller, panel, schedule, source = setup
    old_snapshot = replace(source.snapshot, phase=stale_phase)
    controller.receive(event("help_received", text="Retired room text"), source=source)
    schedule.flush()
    controller.invalidate()
    source.snapshot = replace(source.snapshot, generation=2, phase=RemoteSessionPhase.PREPARING)
    source.help_available = False
    controller.arm(source)
    controller.receive(event("help_received", generation=2, text="New room first question"), source=source)
    controller.update(source, old_snapshot)
    schedule.flush()
    assert len(controller._inbox) == 1
    assert panel.entries == ()
    assert not panel.available
    source.snapshot = replace(source.snapshot, phase=RemoteSessionPhase.CONNECTED)
    source.help_available = True
    controller.update(source, source.snapshot)
    schedule.flush()
    assert panel.entries == (("Peer", "New room first question", ""),)
    panel.draft = "Only for the new room"
    controller.update(source, old_snapshot)
    assert panel.entries == (("Peer", "New room first question", ""),)
    assert panel.draft == "Only for the new room"
    assert panel.available


def test_same_generation_failure_is_not_mistaken_for_obsolete_snapshot(setup):
    controller, panel, schedule, source = setup
    controller.receive(event("help_received", text="Erase on current failure"), source=source)
    schedule.flush()
    controller.update(source, replace(source.snapshot, phase=RemoteSessionPhase.FAILED))
    assert panel.entries == ()
    assert not panel.available


def test_history_and_early_ack_storage_are_bounded(setup):
    controller, panel, schedule, source = setup
    for request_id in range(1, 100):
        controller.receive(event("help_received", request_id, text="Bounded"), source=source)
        schedule.flush()
    assert len(panel.entries) == MAX_HELP_ENTRIES
    send(controller, panel, source)
    for request_id in range(1, 100):
        controller.receive(event("help_delivered", request_id), source=source)
        schedule.flush()
    assert len(controller._early_acks) == MAX_EARLY_ACKS
    finish(controller, schedule, source)
    assert len(panel.entries) == MAX_HELP_ENTRIES
    assert panel.entries[-1][2] == ACCEPTED_STATUS  # Evicted ACK is not guessed.
    assert not controller._early_acks


@pytest.mark.parametrize("text", ["", " ", "<b>hello</b>", "line\nline", "a\t", "\x00", "\u200b", "\ud800", "é" * 251])
def test_outgoing_text_uses_existing_500_byte_plain_text_boundary(setup, text):
    controller, panel, _schedule, source = setup
    panel.draft = text
    panel.submitted.emit(text)
    assert source.calls == []
    assert not panel.pending
    assert panel.status == INVALID_TEXT_STATUS
    assert panel.draft == text


def test_valid_nfc_normalization_and_changed_draft_is_not_erased(setup):
    controller, panel, schedule, source = setup
    send(controller, panel, source, "Try cafe\u0301")
    assert source.calls == ["Try café"]
    panel.draft = "A newer draft"
    finish(controller, schedule, source)
    assert panel.entries == (("You", "Try café", ACCEPTED_STATUS),)
    assert panel.draft == "A newer draft"


def test_same_binding_update_preserves_delivery_and_initial_guidance(setup):
    controller, panel, schedule, source = setup
    assert panel.status == "Messages stay only in this session."
    send(controller, panel, source)
    finish(controller, schedule, source)
    controller.update(source, source.snapshot)
    assert panel.status == ACCEPTED_STATUS


def test_send_pins_generation_if_source_resets_between_check_and_dispatch(setup):
    controller, panel, schedule, source = setup
    original_send = source.send_help

    def reset_before_dispatch(text, *, expected_generation):
        source.snapshot = replace(source.snapshot, generation=2)
        return original_send(text, expected_generation=expected_generation)

    source.send_help = reset_before_dispatch
    panel.draft = "Old private room draft"
    panel.submitted.emit(panel.draft)
    finish(controller, schedule, source)
    assert source.calls == []
    assert panel.entries == ()
    assert panel.draft == ""
    assert not panel.available


@pytest.mark.parametrize("text", ["<img>", "\n", "é" * 251, "cafe\u0301", "\u200b"])
def test_incoming_noncanonical_or_invalid_text_never_enters_inbox(setup, text):
    controller, panel, schedule, source = setup
    controller.receive(event("help_received", text=text), source=source)
    assert not controller._inbox
    assert not schedule.callbacks
    assert panel.entries == ()


@pytest.mark.parametrize("phase", [RemoteSessionPhase.FAILED, RemoteSessionPhase.STOPPING, RemoteSessionPhase.STOPPED])
def test_failure_stop_and_shutdown_clear_ephemeral_state(setup, phase):
    controller, panel, schedule, source = setup
    controller.receive(event("help_received", text="Private"), source=source)
    schedule.flush()
    panel.draft = "Private draft"
    source.snapshot = replace(source.snapshot, phase=phase)
    controller.update(source, source.snapshot)
    assert panel.entries == ()
    assert panel.draft == ""
    assert not panel.available
    controller.shutdown()
    source.snapshot = replace(source.snapshot, phase=RemoteSessionPhase.CONNECTED)
    controller.update(source, source.snapshot)
    assert not panel.visible
    assert not panel.available
