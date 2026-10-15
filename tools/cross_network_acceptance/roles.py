"""Host and guest role processes for the acceptance harness.

Art uses the offscreen Qt conductor (Linux can Host Art). Music uses the
existing headless HostPeerSession / GuestPeerSession control plane because
the product door ghosts Music Host on Linux.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from core.network_invite import local_band_address, parse_invite_link
from core.session_transfer import SessionTransferError
from core.session_transfer_runtime import GuestPeerSession, HostPeerSession

ART_START = "paint_along"
_INVITE_NAME = "invite"
_READY_NAME = "host.ready"
_STOP_NAME = "host.stop"
_RESULT_NAME = "guest.result"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    path.chmod(0o600)


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


def _wait_stop(work_dir: Path, timeout_s: float) -> None:
    stop = work_dir / _STOP_NAME
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if stop.exists():
            return
        time.sleep(0.05)


def _music_host(work_dir: Path) -> int:
    address = local_band_address()
    if not address:
        _write_json(work_dir / _READY_NAME, {"ok": False, "reason": "no_private_address"})
        return 1
    host = HostPeerSession()
    host.start(
        address,
        takes_root=work_dir / "host-takes",
        installation_path=work_dir / "host-installation.json",
        display_name="Host musician",
        creator_profile_key="music",
    )
    try:
        url = host.invite_link(
            host=address,
            jamulus_port=22124,
            session_name="Music room",
        )
        if not url or not host.active:
            _write_json(work_dir / _READY_NAME, {"ok": False, "reason": "host_inactive"})
            return 1
        _store_invite(work_dir, url)
        _write_json(
            work_dir / _READY_NAME,
            {
                "ok": True,
                "profile": "music",
                "peer_bound": True,
                "creator_profile_key": "music",
            },
        )
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
        _write_json(
            work_dir / _RESULT_NAME,
            {
                "ok": exchanged,
                "profile": "music",
                "guest_connected": connected,
                "session_state_exchanged": exchanged,
                "creator_profile_key": state.creator_profile_key,
            },
        )
        return 0 if exchanged else 1
    except (SessionTransferError, OSError, ValueError):
        _write_json(
            work_dir / _RESULT_NAME,
            {
                "ok": False,
                "profile": "music",
                "guest_connected": False,
                "session_state_exchanged": False,
                "reason": "unreachable",
            },
        )
        return 1
    finally:
        url = ""


def _art_patches():
    from unittest.mock import Mock, patch

    return (
        patch(
            "webjam_qt.platform_permissions.microphone_permission_status",
            return_value="authorized",
        ),
        patch("webex_integration.open_webex_meeting", Mock()),
        patch("PySide6.QtGui.QDesktopServices.openUrl", Mock()),
        patch("services.drawpile_service.DrawpileLauncher.open_canvas", Mock()),
        patch("services.drawpile_service.DrawpileLauncher.open_host_page", Mock()),
    )


def _make_art_app(*, work_dir: Path, hosting: bool, invite=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core.settings import AppSettings
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    QApplication.instance() or QApplication([])
    settings = AppSettings(
        config_file=str(work_dir / "settings.json"),
        takes_directory=str(work_dir / "takes"),
        musician_name="Host artist" if hosting else "Guest artist",
        host_server_enabled=hosting,
        last_creator_profile_key="art",
        last_creator_start_key=ART_START,
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Art room",
    )
    return ApplicationController(window, settings=settings, session_invite=invite)


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


def _art_host(work_dir: Path) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core.session_conductor import ArtRoomState

    started = []
    for item in _art_patches():
        item.start()
        started.append(item)
    app = None
    try:
        app = _make_art_app(work_dir=work_dir / "host-qt", hosting=True)
        app._launch_native_jamulus_for_startup = lambda *a, **k: None
        app._start_hosted_server_for_startup = lambda *a, **k: None
        qapp = QApplication.instance()
        if not app.begin_startup_journey():
            _write_json(work_dir / _READY_NAME, {"ok": False, "reason": "startup_refused"})
            return 1
        ready = _drain(
            qapp,
            lambda: bool(app.host_peer.active and app._current_invite_url()),
            15.0,
        )
        url = app._current_invite_url() if ready else ""
        room = app._room_participant
        if not url or room.state is ArtRoomState.FAILED:
            _write_json(work_dir / _READY_NAME, {"ok": False, "reason": "invite_unavailable"})
            return 1
        _store_invite(work_dir, url)
        _write_json(
            work_dir / _READY_NAME,
            {
                "ok": True,
                "profile": "art",
                "peer_bound": True,
                "art_start_key": ART_START,
            },
        )
        stop = work_dir / _STOP_NAME
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline and not stop.exists():
            qapp.processEvents()
            time.sleep(0.05)
        return 0
    finally:
        if app is not None:
            _shutdown_art(app)
        for item in reversed(started):
            item.stop()


def _art_guest(work_dir: Path) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core.session_conductor import ArtRoomState

    url = _read_invite(work_dir)
    started = []
    for item in _art_patches():
        item.start()
        started.append(item)
    app = None
    try:
        invite = parse_invite_link(url)
        url = ""
        app = _make_art_app(
            work_dir=work_dir / "guest-qt", hosting=False, invite=invite
        )
        app._launch_native_jamulus_for_startup = lambda *a, **k: None
        app._start_hosted_server_for_startup = lambda *a, **k: None
        qapp = QApplication.instance()
        if not app.begin_startup_journey():
            _write_json(
                work_dir / _RESULT_NAME,
                {
                    "ok": False,
                    "profile": "art",
                    "guest_connected": False,
                    "session_state_exchanged": False,
                    "reason": "startup_refused",
                },
            )
            return 1
        connected = _drain(
            qapp,
            lambda: app._room_participant.state is ArtRoomState.CONNECTED,
            32.0,
        )
        room = app._room_participant
        exchanged = connected and room.borrowed_start == ART_START
        _write_json(
            work_dir / _RESULT_NAME,
            {
                "ok": exchanged,
                "profile": "art",
                "guest_connected": connected,
                "session_state_exchanged": exchanged,
                "art_start_key": room.borrowed_start,
                "room_state": room.state.value,
            },
        )
        return 0 if exchanged else 1
    except (SessionTransferError, OSError, ValueError):
        _write_json(
            work_dir / _RESULT_NAME,
            {
                "ok": False,
                "profile": "art",
                "guest_connected": False,
                "session_state_exchanged": False,
                "reason": "unreachable",
            },
        )
        return 1
    finally:
        if app is not None:
            _shutdown_art(app)
        for item in reversed(started):
            item.stop()


def run_role(role: str, profile: str, work_dir: Path) -> int:
    if profile not in {"art", "music"} or role not in {"host", "guest"}:
        return 2
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if profile == "music":
        return _music_host(work_dir) if role == "host" else _music_guest(work_dir)
    return _art_host(work_dir) if role == "host" else _art_guest(work_dir)
