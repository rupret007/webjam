from __future__ import annotations

import os
import sys
import time
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.direct_stun_invite import (  # noqa: E402
    DIRECT_STUN_PROFILE_ID,
    encode_guest_reply_line,
    encode_host_signal_line,
)
from core.remote_invitation import issue_remote_invitation  # noqa: E402
from core.session_transport import ConnectionQuality, SessionRole, TransportPath  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from services.remote_session_runtime import (  # noqa: E402
    RemoteGuestConnection,
    RemoteSessionPhase,
    RemoteSessionSnapshot,
)
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402

SIGNAL = b"WJSE" + bytes(range(12))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


def _controller(tmp_path, *, hosting: bool = False):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Direct stun",
    )
    return ApplicationController(
        window,
        settings=AppSettings(
            config_file=str(tmp_path / "settings.json"),
            host_server_enabled=hosting,
            jamulus_server="127.0.0.1",
            jamulus_port=22124,
        ),
    )


def _drain_until(qapp, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert predicate()


def _issued_invite():
    return issue_remote_invitation(
        DIRECT_STUN_PROFILE_ID,
        allowed_profiles={DIRECT_STUN_PROFILE_ID},
        host_spki_sha256=bytes.fromhex("44" * 32),
    )


def test_host_then_join_then_reply_ends_connected(qapp, tmp_path, monkeypatch) -> None:
    """Ordinary Host start, Join paste, then the reply, ends connected.

    The reply cannot travel over rendezvous: direct-stun is stun-only. Host
    already accepts on direct and relay, so one invite paste is enough when
    the guest can reach the published candidates. Paste Reply is the punch
    fallback. This test drives that fallback through the Host UI action.
    """

    monkeypatch.setattr(
        "services.native_remote_transport.ordinary_native_host_profile",
        lambda: "direct-stun",
    )
    issued = _issued_invite()
    invitation = issued.invitation.with_host_signal(SIGNAL)
    constructed: list[str] = []

    class HostOwner:
        invitation_available = True
        profile_id = "direct-stun"

        def __init__(self, *, target_port, profile_id="reference-local", on_snapshot=None, **_kwargs):
            assert target_port == 22124
            assert profile_id == "direct-stun"
            constructed.append(profile_id)
            self.profile_id = profile_id
            self.invitation = invitation
            self._on_snapshot = on_snapshot
            self.applied_reply = b""
            self.snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.PREPARING,
                role=SessionRole.HOST,
                generation=1,
                path=TransportPath.INTERNET_DIRECT,
            )

        def apply_guest_reply(self, envelope: bytes) -> None:
            self.applied_reply = bytes(envelope)
            self.snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.CONNECTED,
                role=SessionRole.HOST,
                generation=1,
                loopback_port=43123,
                path=TransportPath.INTERNET_DIRECT,
                quality=ConnectionQuality.UNKNOWN,
            )
            if self._on_snapshot is not None:
                self._on_snapshot(self.snapshot)

        def copy_for_clipboard(self) -> str:
            return (
                f"{issued.private_link.reveal_for_clipboard()}\n"
                f"{encode_host_signal_line(SIGNAL)}"
            )

        def stop(self):
            return None

    class GuestBackend:
        def __init__(self, **_kwargs):
            self.connection_available = False

        def start_guest(self, joined, *, generation):
            assert joined.profile_id == DIRECT_STUN_PROFILE_ID
            assert joined.host_signal == SIGNAL
            self.connection_available = True
            return RemoteGuestConnection(
                loopback_port=43124,
                path=TransportPath.INTERNET_DIRECT,
                quality=ConnectionQuality.UNKNOWN,
                generation=generation,
            )

        @property
        def guest_reply(self) -> bytes:
            return SIGNAL

        def stop(self):
            return None

    host = _controller(tmp_path / "host", hosting=True)
    host.begin_startup_journey = mock.MagicMock()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeHostTransportOwner",
        HostOwner,
    )
    assert ApplicationController.begin_startup_journey(host) is True
    _drain_until(qapp, lambda: host._remote_invite_owner is not None)
    assert constructed == ["direct-stun"]
    assert host._remote_invite_owner.profile_id == "direct-stun"
    assert host._remote_paste_reply_available()

    guest = _controller(tmp_path / "guest")
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        GuestBackend,
    )
    assert guest.accept_invitation(invitation)
    _drain_until(
        qapp,
        lambda: (
            guest._remote_session is not None
            and guest._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED
        ),
    )
    assert guest._remote_session.guest_reply == SIGNAL

    assert host._apply_guest_reply_paste(encode_guest_reply_line(SIGNAL))
    _drain_until(
        qapp,
        lambda: host._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED,
    )
    assert host._remote_invite_owner.applied_reply == SIGNAL
    assert guest._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED

    guest._stop_remote_transport()
    host._stop_remote_transport()
    guest.shutdown()
    host.shutdown()
