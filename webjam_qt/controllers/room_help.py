"""Bounded, memory-only presentation of the authenticated session help plane.

Only ``receive`` and ``invalidate`` may enter from an IPC thread. UI work is
coalesced onto one scheduled drain; sending never blocks the Qt thread. This
module deliberately owns no persistence, diagnostic logging, or retry loop.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.session_transport import SessionRole
from services.remote_session_runtime import RemoteSessionPhase
from services.transport_runtime import (
    TransportEvent,
    TransportProtocolError,
    _normalize_help_text,
)

MAX_HELP_ENTRIES = 40
MAX_HELP_INGRESS = 16
MAX_EARLY_ACKS = 8
ACCEPTED_STATUS = "Accepted locally"
ACKNOWLEDGED_STATUS = "Peer acknowledged — not a read receipt"
SEND_FAILED_STATUS = (
    "Delivery not confirmed. Your draft is kept; retrying may send twice."
)
UNAVAILABLE_REASON = "Session help needs a connected, authenticated peer."
INVALID_TEXT_STATUS = (
    "Use one line of plain text, up to 500 UTF-8 bytes. No markup or controls."
)


@dataclass
class _Entry:
    label: str
    text: str = field(repr=False)
    status: str = ""
    request_id: int = 0


@dataclass
class _Send:
    source: Any = field(repr=False)
    token: int
    generation: int
    text: str = field(repr=False)
    draft: str = field(repr=False)


class RoomHelpController:
    """Present one authenticated peer's ephemeral messages, disabled by default.

    ``update`` / ``poll_availability`` / ``shutdown`` run on the UI thread.
    ``schedule_callback`` must marshal onto that same thread. A single worker
    remains busy across resets until its bounded transport call returns, so
    switching rooms cannot accumulate workers or resend old drafts.
    """

    def __init__(
        self,
        panel: Any,
        *,
        enabled: bool = False,
        schedule_callback: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._panel = panel
        self._enabled = bool(enabled)
        self._closed = False
        self._invoker = None
        if schedule_callback is None:
            from webjam_qt.controllers.ui_thread import UiThreadInvoker

            self._invoker = UiThreadInvoker()
            schedule_callback = self._invoker.invoke
        self._schedule = schedule_callback
        self._lock = threading.RLock()
        self._source: Any = None
        self._armed_source: Any = None
        self._role = ""
        self._generation = 0
        self._token = 0
        self._entries: deque[_Entry] = deque(maxlen=MAX_HELP_ENTRIES)
        self._inbox: deque[tuple[int, TransportEvent]] = deque()
        self._early_acks: deque[int] = deque(maxlen=MAX_EARLY_ACKS)
        self._last_received_id = 0
        self._last_accepted_id = 0
        self._work: _Send | None = None
        self._result: tuple[_Send, TransportEvent | None] | None = None
        self._drain_scheduled = False
        self._needs_clear = False
        self._panel.submitted.connect(self.submit)
        self._panel.setVisible(self._enabled)
        self._clear_panel()

    @staticmethod
    def _snapshot_key(snapshot: Any) -> tuple[str, int] | None:
        if getattr(snapshot, "phase", None) != RemoteSessionPhase.CONNECTED:
            return None
        role = getattr(snapshot, "role", None)
        generation = getattr(snapshot, "generation", None)
        if role not in (SessionRole.HOST, SessionRole.GUEST):
            return None
        if type(generation) is not int or not 1 <= generation <= 2**32 - 1:
            return None
        return (SessionRole(role).value, generation)

    def _source_live(self, source: Any, key: tuple[str, int]) -> bool:
        try:
            return bool(
                source is not None
                and source.help_available is True
                and callable(source.send_help)
                and self._snapshot_key(source.snapshot) == key
            )
        except Exception:  # noqa: BLE001 - no service detail or text escapes
            return False

    def _binding_live(self) -> bool:
        return bool(
            self._enabled
            and not self._closed
            and self._source_live(self._source, (self._role, self._generation))
        )

    def arm(self, source: Any) -> None:
        """Name the installed source before its queued proof reaches the UI.

        This grants no ability to display or send. It only allows the first
        authenticated transport frames to wait in the same bounded inbox.
        """

        with self._lock:
            self._reset_locked()
            self._clear_panel()
            if self._enabled and not self._closed:
                self._armed_source = source

    def _snapshot_obsolete(self, source: Any, snapshot: Any) -> bool:
        """Ignore a queued old generation only when its live owner proves it."""

        if source is None or (source is not self._source and source is not self._armed_source):
            return False
        try:
            actual = source.snapshot
            generation = snapshot.generation
            actual_generation = actual.generation
            role = snapshot.role
            return bool(
                role in (SessionRole.HOST, SessionRole.GUEST)
                and actual.role == role
                and type(generation) is int
                and type(actual_generation) is int
                and 1 <= generation < actual_generation <= 2**32 - 1
            )
        except Exception:  # noqa: BLE001 - uncertain source state never grants access
            return False

    def update(self, source: Any, snapshot: Any) -> None:
        """Bind current connected source identity, role, and generation."""

        with self._lock:
            if self._snapshot_obsolete(source, snapshot):
                return
            key = self._snapshot_key(snapshot)
            if (
                self._enabled
                and not self._closed
                and source is not None
                and source is self._armed_source
                and getattr(snapshot, "phase", None) is RemoteSessionPhase.PREPARING
            ):
                # A PREPARING callback may have queued before the transport's
                # first help frame. It is not a failure or permission to show it.
                return
            if (
                not self._enabled
                or self._closed
                or key is None
                or not self._source_live(source, key)
            ):
                self._reset_locked()
                self._clear_panel()
                return
            if source is not self._source or key != (self._role, self._generation):
                if source is not self._armed_source:
                    self._reset_locked()
                    self._clear_panel()
                self._source = source
                self._armed_source = None
                self._role, self._generation = key
                self._panel.set_available(True, "Messages stay only in this session.")
            self._panel.set_pending(self._work is not None or self._result is not None)
            if self._work is not None and self._work.token != self._token:
                self._panel.set_status("Finishing the previous send. Nothing is retried.")
            if self._inbox:
                self._schedule_drain_locked()

    def poll_availability(self) -> None:
        """Catch sidecar death even when an old UI snapshot says connected."""

        with self._lock:
            if self._source is not None and not self._binding_live():
                self._reset_locked()
                self._clear_panel()
            elif self._armed_source is not None:
                try:
                    snapshot = self._armed_source.snapshot
                    phase = snapshot.phase
                    still_waiting = phase in {
                        RemoteSessionPhase.IDLE, RemoteSessionPhase.PREPARING
                    }
                    key = self._snapshot_key(snapshot)
                    still_proved = key is not None and self._source_live(self._armed_source, key)
                except Exception:  # noqa: BLE001 - no private source detail
                    still_waiting = still_proved = False
                if not (still_waiting or still_proved):
                    self._reset_locked()
                    self._clear_panel()

    def _reset_locked(self) -> None:
        self._token += 1
        self._source = None
        self._armed_source = None
        self._role = ""
        self._generation = 0
        self._inbox.clear()
        self._entries.clear()
        self._early_acks.clear()
        self._last_received_id = 0
        self._last_accepted_id = 0
        if self._work is not None:
            self._work.text = ""
            self._work.draft = ""
        if self._result is not None:
            self._result[0].text = ""
            self._result[0].draft = ""
        self._result = None
        self._needs_clear = True

    def invalidate(self) -> None:
        """Immediately revoke queued/working text, then clear UI on its thread."""

        with self._lock:
            self._reset_locked()
            self._schedule_drain_locked()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._reset_locked()
            self._clear_panel()
            self._panel.setVisible(False)

    def _clear_panel(self) -> None:
        self._panel.clear_session()
        self._panel.set_entries(())
        self._panel.set_pending(False)
        self._panel.set_status("")
        self._panel.set_available(False, UNAVAILABLE_REASON)
        self._needs_clear = False

    def _schedule_drain_locked(self) -> None:
        if self._drain_scheduled:
            return
        self._drain_scheduled = True
        try:
            self._schedule(self._drain)
        except RuntimeError:  # Qt may already have destroyed its invoker.
            self._drain_scheduled = False
            self._reset_locked()

    @staticmethod
    def _event_shape(event: Any, *, accepted: bool = False) -> bool:
        if not isinstance(event, TransportEvent):
            return False
        kinds = {"help_accepted"} if accepted else {"help_received", "help_delivered"}
        return bool(
            event.event_type in kinds
            and event.code == "ok"
            and event.state == "connected"
            and event.mode in {"host", "guest"}
            and type(event.generation) is int
            and 1 <= event.generation <= 2**32 - 1
            and type(event.request_id) is int
            and 1 <= event.request_id <= 2**64 - 1
            and type(event.event_id) is int
            and event.event_id == (event.request_id if accepted else 0)
            and bool(event.profile_id)
            and (event.event_type == "help_received" or event.help_text == "")
        )

    def _event_matches(self, event: Any, *, accepted: bool = False) -> bool:
        return bool(
            self._event_shape(event, accepted=accepted)
            and event.mode == self._role
            and event.generation == self._generation
        )

    def receive(self, event: TransportEvent, *, source: Any) -> None:
        """Receive an IPC event into a bounded inbox, never Qt's unbounded queue."""

        with self._lock:
            staging = bool(
                self._enabled
                and not self._closed
                and source is not None
                and source is self._armed_source
            )
            if not staging and (source is not self._source or not self._binding_live()):
                return
            if not (self._event_shape(event) if staging else self._event_matches(event)):
                return
            if event.event_type == "help_received":
                try:
                    _normalize_help_text(event.help_text, require_canonical=True)
                except TransportProtocolError:
                    return
            if len(self._inbox) >= MAX_HELP_INGRESS:
                # Fail closed on presentation backpressure; do not silently
                # discard content while retaining an apparently current room.
                self._reset_locked()
                self._schedule_drain_locked()
            else:
                self._inbox.append((self._token, event))
                if not staging:
                    self._schedule_drain_locked()

    def submit(self, text: str) -> None:
        """Send once on a worker; no automatic retry and no optimistic receipt."""

        with self._lock:
            if not self._binding_live():
                self._reset_locked()
                self._clear_panel()
                return
            if self._work is not None or self._result is not None:
                return
            try:
                normalized = _normalize_help_text(text)
            except TransportProtocolError:
                self._panel.set_status(INVALID_TEXT_STATUS)
                return
            work = _Send(self._source, self._token, self._generation, normalized, text)
            self._work = work
            self._panel.set_pending(True)
            self._panel.set_status("Sending once…")
            thread = threading.Thread(
                target=self._send_worker,
                args=(work,),
                name="webjam-session-help",
                daemon=True,
            )
            try:
                thread.start()
            except RuntimeError:
                self._work = None
                work.text = ""
                work.draft = ""
                self._panel.set_pending(False)
                self._panel.set_status(SEND_FAILED_STATUS)

    def _send_worker(self, work: _Send) -> None:
        result = None
        text = ""
        try:
            with self._lock:
                if work.token != self._token or not self._binding_live():
                    return
                text = work.text
            result = work.source.send_help(text, expected_generation=work.generation)
        except Exception:  # noqa: BLE001 - never reflect/log message or service detail
            result = None
        finally:
            text = ""
            with self._lock:
                self._work = None
                self._result = (work, result)
                self._schedule_drain_locked()

    def _drain(self) -> None:
        with self._lock:
            self._drain_scheduled = False
            if self._needs_clear:
                self._clear_panel()
            if not self._binding_live():
                if self._armed_source is not None and self._enabled and not self._closed:
                    # An old worker may finish while the new source is armed.
                    # Erase its result without dropping the new bounded inbox.
                    if self._result is not None:
                        self._result[0].text = ""
                        self._result[0].draft = ""
                        self._result = None
                    return
                self._reset_locked()
                self._clear_panel()
                return
            inbox = tuple(self._inbox)
            self._inbox.clear()
            for token, event in inbox:
                if token != self._token or not self._event_matches(event):
                    continue
                if event.event_type == "help_received":
                    if event.request_id <= self._last_received_id:
                        continue
                    self._last_received_id = event.request_id
                    self._entries.append(_Entry("Peer", event.help_text, "", event.request_id))
                else:
                    self._acknowledge(event.request_id)
            result = self._result
            self._result = None
            if result is not None:
                work, event = result
                if work.token == self._token and work.source is self._source:
                    if (
                        self._event_matches(event, accepted=True)
                        and event.request_id > self._last_accepted_id
                    ):
                        self._last_accepted_id = event.request_id
                        status = (
                            ACKNOWLEDGED_STATUS
                            if event.request_id in self._early_acks
                            else ACCEPTED_STATUS
                        )
                        self._entries.append(_Entry("You", work.text, status, event.request_id))
                        if self._panel.draft_text() == work.draft:
                            self._panel.clear_draft()
                        self._panel.set_status(status)
                    else:
                        self._panel.set_status(SEND_FAILED_STATUS)
                else:
                    self._panel.set_status("Messages stay only in this session.")
                work.text = ""
                work.draft = ""
                self._early_acks.clear()
            self._panel.set_pending(self._work is not None)
            self._panel.set_entries(
                tuple((entry.label, entry.text, entry.status) for entry in self._entries)
            )

    def _acknowledge(self, request_id: int) -> None:
        for entry in self._entries:
            if entry.label == "You" and entry.request_id == request_id:
                entry.status = ACKNOWLEDGED_STATUS
                if self._work is None and self._result is None:
                    self._panel.set_status(ACKNOWLEDGED_STATUS)
                return
        if (
            (self._work is not None or self._result is not None)
            and request_id > self._last_accepted_id
            and request_id not in self._early_acks
        ):
            self._early_acks.append(request_id)
