"""Host and guest role processes for the acceptance harness."""

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.network_invite import local_band_address, parse_invite_link
from core.session_transfer import SessionTransferError
from core.session_transfer_runtime import GuestPeerSession, HostPeerSession

NETWORK_CONNECT_EXCEPTIONS = (SessionTransferError, OSError)
_PROGRAMMING_EXCEPTIONS = (TypeError, ValueError, ImportError, SyntaxError, AttributeError, NameError)
_STARTUP_OS_ERRORS = (FileNotFoundError, NotADirectoryError, PermissionError, IsADirectoryError)

ART_START = "paint_along"
_INVITE_NAME = "invite"
_READY_NAME = "host.ready"
_STOP_NAME = "host.stop"
_RESULT_NAME = "guest.result"
_URL_RE = re.compile(r"https?://\S+|webjam://\S+|webjam-signal:\S+", re.IGNORECASE)
_HONEST_FAIL_SNIPPETS = (
    "couldn't reach the host", "could not open", "couldn’t open",
    "private room could not open", "private music path could not open",
    "private music connection", "fresh invitation", "timed out",
    "incomplete", "unreachable",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _sanitize_text(value: object) -> str:
    text = _URL_RE.sub("[redacted]", str(value or ""))
    return text.replace("\n", " ").strip()[:240]


def _honest_failure(text: str) -> bool:
    lowered = text.lower()
    return any(snippet in lowered for snippet in _HONEST_FAIL_SNIPPETS)


def guest_failure_from_exception(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, _PROGRAMMING_EXCEPTIONS):
        klass = "programming_error"
    elif isinstance(exc, _STARTUP_OS_ERRORS):
        klass = "startup_failure"
    elif isinstance(exc, NETWORK_CONNECT_EXCEPTIONS):
        return {
            "reason": "unreachable", "failure_class": "honest_unreachable",
            "attempt_finished": True,
        }
    else:
        klass = "unknown"
    return {"reason": klass, "failure_class": klass, "attempt_finished": False}


def classify_native_guest_failure(
    *, transport_connected: bool, state_exchanged: bool,
    failure_text: str, room_failed: bool,
    attempt_finished: bool = False, error_code: str = "",
) -> str:
    if state_exchanged:
        return ""
    if transport_connected:
        return "state_exchange_failure"
    code = str(error_code or "")
    if code in {"unavailable", "invitation_unusable", "local_operation_pending"}:
        return "startup_failure"
    if code == "peer_protocol_unsupported":
        return "programming_error"
    if not attempt_finished:
        return "unknown"
    if _honest_failure(failure_text) or room_failed or code in {
        "transport_failed", "timed_out", "expired", "enrollment_rejected",
    }:
        return "honest_unreachable"
    return "unknown"


def _read_invite(work_dir: Path) -> str:
    path = work_dir / _INVITE_NAME
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise RuntimeError("The host did not publish an invitation.")


def _store_invite(work_dir: Path, url: str) -> None:
    path = work_dir / _INVITE_NAME
    path.write_text(url, encoding="utf-8")
    path.chmod(0o600)


def _wait_stop(work_dir: Path, timeout_s: float, qapp=None) -> None:
    stop = work_dir / _STOP_NAME
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not stop.exists():
        if qapp is not None:
            qapp.processEvents()
        time.sleep(0.05)


def _direct_stun_available() -> bool:
    try:
        from services.native_remote_transport import ordinary_native_host_profile

        return ordinary_native_host_profile() == "direct-stun"
    except Exception:
        return False


def _guest_result(
    work_dir: Path, profile: str, transport: str, *,
    connected: bool, exchanged: bool, **extra: Any,
) -> int:
    extra.setdefault("attempt_finished", bool(connected and exchanged))
    _write_json(work_dir / _RESULT_NAME, {
        "ok": exchanged, "profile": profile, "guest_connected": connected,
        "session_state_exchanged": exchanged, "transport": transport, **extra,
    })
    return 0 if exchanged else 1


def _guest_fail(
    work_dir: Path, profile: str, transport: str, *, reason: str,
    extra: dict[str, Any] | None = None,
) -> int:
    extra = dict(extra or {})
    extra.setdefault(
        "attempt_finished",
        extra.get("failure_class") == "honest_unreachable" and reason == "unreachable",
    )
    return _guest_result(
        work_dir, profile, transport, connected=False, exchanged=False,
        reason=reason, **extra,
    )


def _guest_connect_fail(work_dir: Path, profile: str, transport: str) -> int:
    extra = guest_failure_from_exception(sys.exception())
    return _guest_fail(
        work_dir, profile, transport, reason=str(extra.pop("reason")), extra=extra,
    )


def _ready(work_dir: Path, **payload: Any) -> int:
    _write_json(work_dir / _READY_NAME, payload)
    return 0 if payload.get("ok") else 1


def _music_host(work_dir: Path) -> int:
    address = local_band_address()
    if not address:
        return _ready(work_dir, ok=False, reason="no_private_address")
    host = HostPeerSession()
    host.start(
        address, takes_root=work_dir / "host-takes",
        installation_path=work_dir / "host-installation.json",
        display_name="Host musician", creator_profile_key="music",
    )
    try:
        url = host.invite_link(host=address, jamulus_port=22124, session_name="Music room")
        if not url or not host.active:
            return _ready(work_dir, ok=False, reason="host_inactive")
        _store_invite(work_dir, url)
        _ready(work_dir, ok=True, profile="music", peer_bound=True,
               creator_profile_key="music", transport="lan")
        _wait_stop(work_dir, 90.0)
        return 0
    finally:
        host.stop()


def _music_guest(work_dir: Path) -> int:
    url = _read_invite(work_dir)
    try:
        invite = parse_invite_link(url)
        guest = GuestPeerSession(
            invite,
            display_name="Guest musician",
            takes_root=work_dir / "guest-takes",
            installation_path=work_dir / "guest-installation.json",
            capture_enabled=lambda: False,
            capture_config=lambda: (0, 48_000, 128),
        )
        state = guest.poll_once()
        connected = state.session_id == invite.session_id
        exchanged = connected and state.creator_profile_key == "music"
        return _guest_result(
            work_dir, "music", "lan", connected=connected, exchanged=exchanged,
            creator_profile_key=state.creator_profile_key,
            failure_class="" if connected else "honest_unreachable",
            attempt_finished=True,
        )
    except NETWORK_CONNECT_EXCEPTIONS:
        return _guest_connect_fail(work_dir, "music", "lan")
    finally:
        url = ""


def _art_patches():
    from unittest.mock import Mock, patch

    return (
        patch("webjam_qt.platform_permissions.microphone_permission_status",
              return_value="authorized"),
        patch("webex_integration.open_webex_meeting", Mock()),
        patch("PySide6.QtGui.QDesktopServices.openUrl", Mock()),
        patch("services.drawpile_service.DrawpileLauncher.open_canvas", Mock()),
        patch("services.drawpile_service.DrawpileLauncher.open_host_page", Mock()),
    )


def _make_qt_app(*, work_dir: Path, hosting: bool, profile: str, invite=None, remote=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core.settings import AppSettings
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    QApplication.instance() or QApplication([])
    art = profile == "art"
    settings = AppSettings(
        config_file=str(work_dir / "settings.json"),
        takes_directory=str(work_dir / "takes"),
        musician_name="Host artist" if hosting else "Guest artist",
        host_server_enabled=hosting,
        last_creator_profile_key="art" if art else "music",
        last_creator_start_key=ART_START if art else "",
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Art room" if art else "Music room",
    )
    kwargs: dict[str, Any] = {"settings": settings}
    if invite is not None:
        kwargs["session_invite"] = invite
    if remote is not None:
        kwargs["remote_invitation"] = remote
    return ApplicationController(window, **kwargs)


def _drain(qapp, predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    qapp.processEvents()
    return bool(predicate())


def _shutdown_art(app) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    try:
        app.shutdown()
    except Exception:
        pass
    window = app.window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


def _attach_flash_log(app, bucket: list[str]) -> None:
    original = getattr(app.window, "flash_message", None)

    def captured(message, *args, **kwargs):
        bucket.append(_sanitize_text(message))
        if callable(original):
            return original(message, *args, **kwargs)
        return None

    app.window.flash_message = captured


def _hud_text(app) -> str:
    hud = getattr(app.window, "session_hud", None)
    parts: list[str] = []
    for attr in ("_title", "_detail", "_status", "_message"):
        widget = getattr(hud, attr, None)
        text = getattr(widget, "text", None)
        if callable(text):
            parts.append(_sanitize_text(text()))
    return " ".join(item for item in parts if item)


@contextmanager
def _art_runtime() -> Iterator[dict[str, Any]]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    started = []
    for item in _art_patches():
        item.start()
        started.append(item)
    holder: dict[str, Any] = {"app": None}
    try:
        yield holder
    finally:
        if holder["app"] is not None:
            _shutdown_art(holder["app"])
        for item in reversed(started):
            item.stop()


def _prepare_controller(
    work_dir: Path, *, hosting: bool, profile: str, invite=None, remote=None,
    flashes=None,
):
    from PySide6.QtWidgets import QApplication

    app = _make_qt_app(
        work_dir=work_dir, hosting=hosting, profile=profile, invite=invite, remote=remote,
    )
    app._launch_native_jamulus_for_startup = lambda *a, **k: None
    app._start_hosted_server_for_startup = lambda *a, **k: None
    if flashes is not None:
        _attach_flash_log(app, flashes)
    return app, QApplication.instance()


def _art_host(work_dir: Path) -> int:
    from core.session_conductor import ArtRoomState

    with _art_runtime() as holder:
        app, qapp = _prepare_controller(work_dir / "host-qt", hosting=True, profile="art")
        holder["app"] = app
        if not app.begin_startup_journey():
            return _ready(work_dir, ok=False, reason="startup_refused")
        ready = _drain(
            qapp, lambda: bool(app.host_peer.active and app._current_invite_url()), 15.0,
        )
        url = app._current_invite_url() if ready else ""
        if not url or app._room_participant.state is ArtRoomState.FAILED:
            return _ready(work_dir, ok=False, reason="invite_unavailable")
        _store_invite(work_dir, url)
        _ready(work_dir, ok=True, profile="art", peer_bound=True,
               art_start_key=ART_START, transport="lan")
        _wait_stop(work_dir, 90.0, qapp)
        return 0


def _art_guest(work_dir: Path) -> int:
    from core.session_conductor import ArtRoomState

    url = _read_invite(work_dir)
    with _art_runtime() as holder:
        try:
            invite = parse_invite_link(url)
            url = ""
            app, qapp = _prepare_controller(
                work_dir / "guest-qt", hosting=False, profile="art", invite=invite,
            )
            holder["app"] = app
            if not app.begin_startup_journey():
                return _guest_fail(
                    work_dir, "art", "lan", reason="startup_refused",
                    extra={"failure_class": "startup_failure"})
            connected = _drain(
                qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED, 32.0,
            )
            room = app._room_participant
            exchanged = connected and room.borrowed_start == ART_START
            finished = bool(connected or room.state is ArtRoomState.FAILED)
            return _guest_result(
                work_dir, "art", "lan", connected=connected, exchanged=exchanged,
                art_start_key=room.borrowed_start, room_state=room.state.value,
                failure_class="" if connected else "honest_unreachable",
                attempt_finished=finished,
            )
        except NETWORK_CONNECT_EXCEPTIONS:
            return _guest_connect_fail(work_dir, "art", "lan")


def _native_owner_ready(app) -> bool:
    owner = getattr(app, "_remote_invite_owner", None)
    return bool(owner is not None and getattr(owner, "invitation_available", False))


def _native_host(work_dir: Path, profile: str) -> int:
    from core.session_conductor import ArtRoomState

    flashes: list[str] = []
    with _art_runtime() as holder:
        app, qapp = _prepare_controller(
            work_dir / "host-qt", hosting=True, profile=profile, flashes=flashes,
        )
        holder["app"] = app
        if not app.begin_startup_journey():
            return _ready(
                work_dir, ok=False, reason="startup_refused", transport="direct-stun",
                failure_text=_sanitize_text(_hud_text(app)),
            )
        ready = _drain(qapp, lambda: _native_owner_ready(app), 40.0)
        owner = getattr(app, "_remote_invite_owner", None)
        if not ready or owner is None:
            return _ready(
                work_dir, ok=False, reason="invite_unavailable", transport="direct-stun",
                host_failed=app._room_participant.state is ArtRoomState.FAILED,
                failure_text=_sanitize_text(" ".join(flashes) or _hud_text(app)),
            )
        try:
            paste = owner.copy_for_clipboard()
        except Exception:
            return _ready(
                work_dir, ok=False, reason="clipboard_unavailable",
                transport="direct-stun",
            )
        _store_invite(work_dir, paste)
        paste = ""
        _ready(
            work_dir, ok=True, profile=profile, peer_bound=True,
            art_start_key=ART_START if profile == "art" else "",
            transport="direct-stun",
            share_reaches_internet=bool(getattr(owner, "share_reaches_internet", False)),
            share_route=str(getattr(owner, "_share_route", "") or ""),
        )
        _wait_stop(work_dir, 120.0, qapp)
        return 0


def _native_connect_attempt_finished(app) -> bool:
    snap = getattr(getattr(app, "_remote_session", None), "snapshot", None)
    if snap is None:
        return False
    phase = getattr(getattr(snap, "phase", None), "value", "")
    if phase == "connected":
        return True
    if phase != "failed":
        return False
    code = getattr(getattr(snap, "error_code", None), "value", "")
    if code in {"", "unavailable", "invitation_unusable", "local_operation_pending"}:
        return False
    if code == "timed_out":
        return getattr(getattr(snap, "stage", None), "value", "") in {
            "contacting_host", "securing_connection", "opening_jamulus",
            "needs_attention",
        }
    return True


def _native_guest_connected(app, profile: str) -> bool:
    from services.remote_session_runtime import RemoteSessionPhase

    snapshot = getattr(getattr(app, "_remote_session", None), "snapshot", None)
    return getattr(snapshot, "phase", None) is RemoteSessionPhase.CONNECTED


def _native_state_exchanged(app, profile: str) -> bool:
    room = app._room_participant
    if profile == "art":
        return room.borrowed_start == ART_START
    native = getattr(room, "native_state", None)
    return bool(native is not None and getattr(native, "creator_profile_key", "") == "music")


def _native_guest(work_dir: Path, profile: str) -> int:
    from core.session_conductor import ArtRoomState
    from webjam_qt.invitation_ingress import (
        InvitationIngressError,
        InvitationSource,
        parse_invitation_at_ingress,
    )

    paste = _read_invite(work_dir)
    flashes: list[str] = []
    with _art_runtime() as holder:
        try:
            try:
                remote = parse_invitation_at_ingress(paste, source=InvitationSource.PASTE)
            except InvitationIngressError as exc:
                paste = ""
                return _guest_fail(
                    work_dir, profile, "direct-stun", reason="ingress_rejected",
                    extra={"failure_class": "startup_failure",
                           "failure_text": _sanitize_text(str(exc))})
            paste = ""
            app, qapp = _prepare_controller(
                work_dir / "guest-qt", hosting=False, profile=profile,
                remote=remote, flashes=flashes,
            )
            holder["app"] = app
            if not app.begin_startup_journey():
                return _guest_fail(
                    work_dir, profile, "direct-stun", reason="startup_refused",
                    extra={"failure_class": "startup_failure",
                           "failure_text": _sanitize_text(_hud_text(app))})
            _drain(qapp, lambda: (
                _native_guest_connected(app, profile)
                and _native_state_exchanged(app, profile)
            ), 50.0)
            connected = _native_guest_connected(app, profile)
            exchanged = connected and _native_state_exchanged(app, profile)
            finished = _native_connect_attempt_finished(app)
            room = app._room_participant
            snap = getattr(getattr(app, "_remote_session", None), "snapshot", None)
            error_code = getattr(getattr(snap, "error_code", None), "value", "")
            failure_text = _sanitize_text(" ".join(flashes) or _hud_text(app))
            return _guest_result(
                work_dir, profile, "direct-stun",
                connected=connected, exchanged=exchanged,
                art_start_key=room.borrowed_start, room_state=room.state.value,
                error_code=error_code, attempt_finished=finished,
                failure_text=failure_text,
                failure_class=classify_native_guest_failure(
                    transport_connected=connected, state_exchanged=exchanged,
                    failure_text=failure_text,
                    room_failed=room.state is ArtRoomState.FAILED,
                    attempt_finished=finished, error_code=str(error_code or ""),
                ),
            )
        except NETWORK_CONNECT_EXCEPTIONS:
            return _guest_connect_fail(work_dir, profile, "direct-stun")


def run_role(role: str, profile: str, work_dir: Path) -> int:
    if profile not in {"art", "music"} or role not in {"host", "guest"}:
        return 2
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        native = _direct_stun_available()
        if native:
            return _native_host(work_dir, profile) if role == "host" else _native_guest(
                work_dir, profile)
        if profile == "music":
            return _music_host(work_dir) if role == "host" else _music_guest(work_dir)
        return _art_host(work_dir) if role == "host" else _art_guest(work_dir)
    except Exception:
        if role != "guest":
            raise
        return _guest_connect_fail(work_dir, profile, "lan")
