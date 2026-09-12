"""Room discovery and bounded explicit requests on the existing private LAN.

No capture engine, originals directory, upload, device or recording recovery
is constructed here. A non-Art result is handed back to the existing Music
startup owner; it never grants recording authority to this observer.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from core.lesson_request import (
    MAX_REVISION,
    LessonRequestCommand,
    LessonRequestError,
    LessonRequestIntent,
    LessonRequestView,
)
from core.network_invite import BandInvite, _validate_private_peer_host
from core.session_transfer import (
    SessionCredentials,
    SessionPeerClient,
    SessionStateSnapshot,
    SessionTransferError,
    TransferAuthenticationError,
)

_PEER_CLIENT_TYPE = SessionPeerClient
_PEER_STATE = SessionPeerClient.state
_PEER_FEATURE_STATE = getattr(SessionPeerClient, "state_with_lesson_requests", None)


@dataclass(frozen=True, repr=False)
class LessonRequestGuestState:
    """Own request presentation; observing this object never grants media control.

    Receipt TTL already excludes HTTP elapsed time. Consumers also subtract
    time since observed_at, and revalidate this object's current-owner identity.
    """

    view: LessonRequestView | None = None
    command: LessonRequestCommand | None = None
    status: str = "inactive"
    error: str = ""
    observed_at: float = 0.0
    retry_after_ms: int = 0
    can_retry: bool = False
    can_submit: bool = False


class LanRoomTerminalReason(str, Enum):
    """Fixed local reasons; never retain a server's private error text."""

    UNAVAILABLE = "unavailable"
    INVITATION_REJECTED = "invitation_rejected"


class LanRoomGuest:
    def __init__(
        self,
        invite: BandInvite,
        *,
        display_name: str,
        on_state: Callable,
        on_loss: Callable,
        on_lesson_request_state: Callable | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(invite, BandInvite) or not invite.peer_enabled:
            raise ValueError("A complete private invitation is required.")
        _validate_private_peer_host(invite.host)
        self.invite = invite
        self._name = display_name
        self._installation_id = str(uuid.uuid4())
        self._on_state, self._on_loss, self._clock = on_state, on_loss, clock
        self.client = SessionPeerClient(
            invite.host,
            invite.peer_port,
            credentials=SessionCredentials(invite.session_id, invite.invite_token),
            timeout_s=2.0,
        )
        self._enrollment = None
        self._stop = threading.Event()
        self._thread = None
        self._last_seen = None
        self._started_at = None
        self.last_state = None
        self._terminal_reason: LanRoomTerminalReason | None = None
        self._on_lesson_request_state = on_lesson_request_state
        self._poll_lock = threading.Lock()
        self._lesson_lock = threading.RLock()
        self._lesson_enabled = False
        self._lesson_epoch = 0
        self._lesson_state = LessonRequestGuestState(observed_at=clock())
        self._lesson_view_at = None
        self._lesson_admission = None
        self._lesson_allocated = 0
        self._lesson_server_revision = 0
        self._lesson_command_at = None
        self._lesson_retry_until = 0.0
        self._lesson_receipt_key = None
        self._lesson_receipt_intent = None
        self._lesson_receipt_until = 0.0
        self._lesson_queued = None
        self._lesson_inflight = None

    @property
    def terminal_reason(self) -> LanRoomTerminalReason | None:
        return self._terminal_reason

    @property
    def connection_available(self) -> bool:
        return (
            not self._stop.is_set()
            and self._last_seen is not None
            and 0 <= self._clock() - self._last_seen < 5.0
        )

    def start(self) -> None:
        if self._thread is not None or self._stop.is_set():
            return
        self._started_at = self._clock()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="webjam-room-guest"
        )
        self._thread.start()

    @property
    def lesson_request_state(self) -> LessonRequestGuestState:
        with self._lesson_lock:
            self._refresh_lesson_locked()
            return self._lesson_state

    def _retire_lesson_locked(self, error="", *, disable=False):
        self._lesson_epoch += 1
        if disable:
            self._lesson_enabled = False
        self._lesson_queued = None
        self._lesson_view_at = None
        self._lesson_command_at = None
        self._lesson_retry_until = 0.0
        # Retain only one admission's allocation/high-water until a genuinely
        # different admission arrives. Navigation cannot reset revisions.
        self._lesson_state = LessonRequestGuestState(
            error=error, observed_at=self._clock(),
        )

    def _notify_lesson(self):
        with self._lesson_lock:
            state = self._lesson_state
            callback = self._on_lesson_request_state
        if callback is not None:
            callback(self, state)

    def set_lesson_requests_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("Lesson request availability must be a boolean.")
        with self._lesson_lock:
            enabled = enabled and not self._stop.is_set()
            if self._lesson_enabled == enabled:
                return
            self._retire_lesson_locked(disable=True)
            self._lesson_enabled = enabled
        self._notify_lesson()

    def _refresh_lesson_locked(self):
        now, state = self._clock(), self._lesson_state
        if state.view is not None and (
            self._lesson_view_at is None
            or not 0 <= now - self._lesson_view_at < 5.0
            or not self.connection_available
        ):
            self._retire_lesson_locked("unavailable")
            return
        receipt = state.view.own_receipt if state.view is not None else None
        elapsed = max(0, math.ceil((now - state.observed_at) * 1000))
        if (receipt is not None and receipt.state != "expired"
                and elapsed >= receipt.expires_in_ms):
            view = replace(state.view, own_receipt=replace(
                receipt, state="expired", expires_in_ms=0,
            ))
            status = state.status
            if state.command is None or state.command.revision == receipt.revision:
                status = "expired"
            state = replace(state, view=view, status=status)
        remaining = max(0, math.ceil((self._lesson_retry_until - now) * 1000))
        live = bool(
            self._lesson_enabled and not self._stop.is_set()
            and state.view is not None and state.view.availability == "active"
            and self._enrollment is not None and self.connection_available
        )
        can_submit = bool(live and not remaining and self._lesson_allocated < MAX_REVISION)
        can_retry = bool(
            live and not remaining and state.status == "unconfirmed"
            and state.command is not None and self._lesson_queued is None
            and self._lesson_inflight is None
            # This bounds explicit retry, not the host's acceptance lifetime.
            # Without a receipt we cannot know whether its notice has expired.
            and self._lesson_command_at is not None
            and 0 <= now - self._lesson_command_at < 30.0
            and (state.command.context_id, state.command.admission_id) == self._lesson_admission
        )
        if (state.can_submit, state.can_retry, state.retry_after_ms) != (
            can_submit, can_retry, remaining,
        ):
            state = replace(state, can_submit=can_submit, can_retry=can_retry,
                            retry_after_ms=remaining)
        self._lesson_state = state

    def queue_lesson_request(self, intent: LessonRequestIntent | str) -> bool:
        if not isinstance(intent, LessonRequestIntent) and type(intent) is not str:
            return False
        try:
            intent = LessonRequestIntent(intent)
        except ValueError:
            return False
        with self._lesson_lock:
            self._refresh_lesson_locked()
            state = self._lesson_state
            if not state.can_submit:
                return False
            revision = self._lesson_allocated + 1
            command = LessonRequestCommand(*self._lesson_admission, revision, intent)
            self._lesson_allocated = revision
            self._lesson_command_at = self._clock()
            self._lesson_queued = command
            self._lesson_state = replace(state, command=command, status="sending",
                                         error="", can_retry=False)
            self._refresh_lesson_locked()
        self._notify_lesson()
        return True

    def retry_lesson_request(self) -> bool:
        with self._lesson_lock:
            self._refresh_lesson_locked()
            state = self._lesson_state
            if not state.can_retry:
                return False
            self._lesson_queued = state.command
            self._lesson_state = replace(state, status="sending", can_retry=False, error="")
        self._notify_lesson()
        return True

    def _feature_reader(self):
        """Old state-only clients and patched state fakes never make new HTTP."""
        reader = getattr(self.client, "state_with_lesson_requests", None)
        if not callable(getattr(type(self.client), "state_with_lesson_requests", None)):
            return None
        if (isinstance(self.client, _PEER_CLIENT_TYPE)
                and getattr(reader, "__func__", None) is _PEER_FEATURE_STATE
                and getattr(self.client.state, "__func__", None) is not _PEER_STATE):
            return None
        return reader

    def _observe_lesson_view(self, view, *, started, epoch, command=None):
        with self._lesson_lock:
            if (not self._lesson_enabled or self._stop.is_set()
                    or epoch != self._lesson_epoch):
                return False
            now = self._clock()
            if not isinstance(view, LessonRequestView) or view.availability != "active":
                self._retire_lesson_locked(
                    view.reason if isinstance(view, LessonRequestView) else "unsupported",
                )
                return True
            key = (view.context_id, view.admission_id)
            if command is not None and key != (command.context_id, command.admission_id):
                self._retire_lesson_locked("context_stale")
                return True
            receipt = view.own_receipt
            server_revision = receipt.revision if receipt is not None else 0
            if key != self._lesson_admission:
                self._retire_lesson_locked()
                self._lesson_admission = key
                self._lesson_allocated = self._lesson_server_revision = 0
                self._lesson_receipt_key = None
                self._lesson_receipt_intent = None
                self._lesson_receipt_until = 0.0
            elif server_revision < self._lesson_server_revision:
                self._retire_lesson_locked("superseded")
                return True
            self._lesson_server_revision = server_revision
            self._lesson_allocated = max(self._lesson_allocated, server_revision)
            self._lesson_view_at = started
            state = self._lesson_state
            if receipt is not None:
                remaining = max(0, receipt.expires_in_ms - math.ceil(max(0, now - started) * 1000))
                receipt_key = (*key, receipt.revision)
                if (receipt_key == self._lesson_receipt_key
                        and receipt.intent != self._lesson_receipt_intent):
                    self._retire_lesson_locked("revision_conflict")
                    return True
                deadline = now + remaining / 1000.0
                if receipt_key == self._lesson_receipt_key:
                    deadline = min(deadline, self._lesson_receipt_until)
                self._lesson_receipt_key, self._lesson_receipt_until = receipt_key, deadline
                self._lesson_receipt_intent = receipt.intent
                remaining = max(0, math.floor((deadline - now) * 1000 + 1e-6))
                receipt_state = receipt.state if remaining else "expired"
                if (state.status == "acknowledged" and state.view is not None
                        and state.view.own_receipt is not None
                        and state.view.own_receipt.revision == receipt.revision and remaining):
                    receipt_state = "acknowledged"
                receipt = replace(receipt, state=receipt_state, expires_in_ms=remaining)
                view = replace(view, own_receipt=receipt)
            current = state.command
            status, error = state.status, state.error
            if current is None:
                status, error = (receipt.state if receipt is not None else "available"), ""
            elif receipt is not None and receipt.revision >= current.revision:
                if receipt.revision == current.revision and receipt.intent != current.intent:
                    status, error = "refused", "revision_conflict"
                else:
                    status, error = receipt.state, ""
                    self._lesson_retry_until = 0.0
                    if receipt.revision > current.revision:
                        current = None
                    if self._lesson_queued is not None and self._lesson_queued.revision <= receipt.revision:
                        self._lesson_queued = None
            self._lesson_state = LessonRequestGuestState(
                view, current, status, error, now,
            )
            self._refresh_lesson_locked()
            return True

    def _send_one_lesson_request(self):
        with self._lesson_lock:
            self._refresh_lesson_locked()
            command = self._lesson_queued
            state = self._lesson_state
            # An already allocated final revision is still sendable even
            # though the ceiling correctly disables allocating another one.
            if (command is None or not self._lesson_enabled or self._stop.is_set()
                    or state.view is None or state.view.availability != "active"
                    or not self.connection_available or state.retry_after_ms
                    or self._enrollment is None
                    or (command.context_id, command.admission_id) != self._lesson_admission
                    or state.command != command):
                return
            self._lesson_queued = None
            epoch = self._lesson_epoch
            self._lesson_inflight = command
            enrollment = self._enrollment
        started = self._clock()
        notify = False
        try:
            view = self.client.post_lesson_request(enrollment, command)
        except TransferAuthenticationError:
            raise
        except Exception as exc:
            with self._lesson_lock:
                if (epoch == self._lesson_epoch
                        and self._lesson_enabled and not self._stop.is_set()):
                    stale_authority = isinstance(exc, LessonRequestError) and exc.code in {
                        "context_stale", "admission_stale", "presence_stale", "superseded",
                        "revision_conflict", "revision_exhausted", "expired",
                    }
                    if isinstance(exc, LessonRequestError) and exc.code == "unsupported":
                        # Missing endpoint capability invalidates every queued
                        # intent, including one entered during this POST.
                        self._retire_lesson_locked(exc.code, disable=True)
                        notify = True
                    elif stale_authority:
                        # This refusal invalidates the cached authority even
                        # when a newer gesture arrived during this POST. Only
                        # the following GET can establish a current view.
                        self._retire_lesson_locked(exc.code)
                        notify = True
                    elif self._lesson_state.command == command:
                        if isinstance(exc, LessonRequestError):
                            status, error = "refused", exc.code
                            self._lesson_retry_until = self._clock() + (exc.retry_after_ms or 0) / 1000
                        else:
                            status, error = "unconfirmed", "delivery_unconfirmed"
                        self._lesson_state = replace(self._lesson_state, status=status, error=error)
                        notify = True
        else:
            notify = self._observe_lesson_view(view, started=started, epoch=epoch, command=command)
        finally:
            with self._lesson_lock:
                self._lesson_inflight = None
                self._refresh_lesson_locked()
            if notify:
                self._notify_lesson()

    def poll_once(self):
        if not self._poll_lock.acquire(blocking=False):
            return None
        try:
            return self._poll_once()
        except Exception:
            with self._lesson_lock:
                self._retire_lesson_locked("unavailable", disable=True)
            self._notify_lesson()
            raise
        finally:
            self._poll_lock.release()

    def _poll_once(self):
        if self._stop.is_set():
            return None
        if self._enrollment is None:
            self._enrollment = self.client.enroll(self._installation_id, self._name)
        if self._stop.is_set():
            return None
        self._send_one_lesson_request()
        if self._stop.is_set():
            return None
        with self._lesson_lock:
            epoch = self._lesson_epoch
        started = self._clock()
        reader = self._feature_reader()
        feature = None
        if reader is None:
            state = self.client.state(self._enrollment)
        else:
            result = reader(self._enrollment)
            state = getattr(result, "snapshot", None)
            feature = getattr(result, "lesson_requests", None)
        if self._stop.is_set():
            return None
        if (
            not isinstance(state, SessionStateSnapshot)
            or state.session_id != self.invite.session_id
        ):
            raise SessionTransferError("The host returned a different room.")
        self.last_state = state
        self._last_seen = self._clock()
        # Every successful receipt counts once. Replaying a cached UI state
        # must never refresh the video follower's network age.
        self._on_state(self, state)
        if state.creator_profile_key != "art":
            feature = None
        if self._observe_lesson_view(feature, started=started, epoch=epoch):
            self._notify_lesson()
        return state

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except TransferAuthenticationError:
                if not self._stop.is_set():
                    # A rejected enrollment or participant credential cannot
                    # recover by replaying this invitation. Retire connection
                    # authority before its queued UI notification is delivered.
                    self._terminal_reason = LanRoomTerminalReason.INVITATION_REJECTED
                    self.stop()
                    self._on_loss(self, True)
                break
            except Exception:  # private server details stay out of logs/UI
                if not self._stop.is_set() and not self.connection_available:
                    origin = (
                        self._last_seen
                        if self._last_seen is not None
                        else self._started_at
                    )
                    terminal = origin is not None and self._clock() - origin >= 30.0
                    if terminal:
                        self._terminal_reason = LanRoomTerminalReason.UNAVAILABLE
                    self._on_loss(self, terminal)
                    if terminal:
                        self.stop()
                        break
            self._stop.wait(0.5)

    def stop(self) -> bool:
        self._stop.set()
        with self._lesson_lock:
            self._retire_lesson_locked(disable=True)
        self._notify_lesson()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.5)
            if thread.is_alive():
                return False
        self.last_state = None
        self._enrollment = None
        self._last_seen = None
        with self._lesson_lock:
            self._lesson_admission = None
            self._lesson_allocated = self._lesson_server_revision = 0
            self._lesson_receipt_key = self._lesson_receipt_intent = None
            self._lesson_receipt_until = 0.0
        return True
