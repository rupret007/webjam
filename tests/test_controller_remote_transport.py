from __future__ import annotations

import os
import sys
import time
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.network_invite import create_invite_link
from core.remote_invitation import issue_remote_invitation
from core.session_transport import ConnectionQuality, SessionRole, TransportPath
from core.settings import AppSettings, save_settings
from services.remote_session_runtime import (
    RemoteGuestConnection,
    RemoteSessionPhase,
    RemoteSessionSnapshot,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


def _invitation():
    return issue_remote_invitation(
        "reference-local",
        allowed_profiles={"reference-local"},
        host_spki_sha256=bytes.fromhex("44" * 32),
    ).invitation


def _controller(tmp_path, *, hosting: bool = False):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Remote",
    )
    controller = ApplicationController(
        window,
        settings=AppSettings(
            config_file=str(tmp_path / "settings.json"),
            host_server_enabled=hosting,
            jamulus_server="127.0.0.1",
            jamulus_port=22124,
        ),
    )
    return controller


def _drain_until(qapp, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_v3_guest_waits_for_authenticated_backend_then_routes_jamulus(
    qapp, tmp_path, monkeypatch
) -> None:
    class Backend:
        def start_guest(self, invitation, *, generation):
            assert invitation is remote
            return RemoteGuestConnection(
                loopback_port=43123,
                path=TransportPath.SECURE_RELAY,
                quality=ConnectionQuality.UNKNOWN,
                generation=generation,
            )

        def stop(self):
            return None

    remote = _invitation()
    controller = _controller(tmp_path)
    controller.start_session_or_band_check = mock.MagicMock()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        Backend,
    )

    assert controller.accept_invitation(remote)
    assert controller.bridge.jamulus_process is None
    _drain_until(
        qapp,
        lambda: controller.settings.jamulus_port == 43123,
    )

    assert controller._remote_invitation is None
    assert controller._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED
    assert controller.settings.jamulus_server == "127.0.0.1"
    assert controller.bridge.remote_guest_mode_enabled
    assert controller._remote_band_check_required()
    controller.start_session_or_band_check.assert_called_once_with()

    controller._stop_remote_transport()
    assert controller.settings.jamulus_port == 22124
    assert not controller.bridge.remote_guest_mode_enabled
    controller.shutdown()


def test_v3_guest_failure_never_falls_through_to_legacy_jamulus(
    qapp, tmp_path, monkeypatch
) -> None:
    class Backend:
        def start_guest(self, invitation, *, generation):
            raise RuntimeError("PRIVATE-CAPABILITY-SENTINEL")

        def stop(self):
            return None

    controller = _controller(tmp_path)
    controller.bridge.launch_jamulus = mock.MagicMock()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        Backend,
    )

    assert controller.accept_invitation(_invitation())
    _drain_until(
        qapp,
        lambda: (
            controller._remote_session.snapshot.phase is RemoteSessionPhase.FAILED
            and controller._remote_invitation is None
        ),
    )

    assert controller._remote_invitation is None
    controller.bridge.launch_jamulus.assert_not_called()
    assert "temporarily unreachable" in controller.window.session_hud._status.text()
    controller.shutdown()


def test_v3_guest_replaces_idle_v2_peer_before_enrollment(
    qapp, tmp_path, monkeypatch
) -> None:
    class Backend:
        def start_guest(self, invitation, *, generation):
            old_peer.stop.assert_called_once_with()
            return RemoteGuestConnection(
                loopback_port=43123,
                path=TransportPath.SECURE_RELAY,
                quality=ConnectionQuality.UNKNOWN,
                generation=generation,
            )

        def stop(self):
            return None

    controller = _controller(tmp_path)
    old_peer = mock.MagicMock()
    controller.guest_peer = old_peer
    controller._guest_invite = mock.sentinel.v2_invitation
    controller._guest_peer_configuration_failed = True
    controller.start_session_or_band_check = mock.MagicMock()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        Backend,
    )

    assert controller.accept_invitation(_invitation())
    _drain_until(
        qapp,
        lambda: controller.settings.jamulus_port == 43123,
    )

    old_peer.stop.assert_called_once_with()
    old_peer.start.assert_not_called()
    assert controller.guest_peer is None
    assert controller._guest_invite is None
    assert not controller._guest_peer_configuration_failed

    # Defense in depth: even if a stale peer is accidentally reintroduced,
    # an active v3 session may never start the v2 plaintext service.
    stale_peer = mock.MagicMock()
    controller.guest_peer = stale_peer
    controller.audio.on_launch_toggle = mock.MagicMock(return_value=True)
    controller._on_launch_audio()
    stale_peer.start.assert_not_called()
    controller.guest_peer = None

    controller._stop_remote_transport()
    controller.shutdown()


def test_v3_guest_fails_closed_when_v2_peer_cleanup_fails(tmp_path) -> None:
    controller = _controller(tmp_path)
    old_peer = mock.MagicMock()
    old_peer.stop.side_effect = RuntimeError("cleanup failed")
    controller.guest_peer = old_peer
    controller._guest_invite = mock.sentinel.v2_invitation
    controller._begin_remote_join = mock.MagicMock()
    controller.window.flash_message = mock.MagicMock()

    assert not controller.accept_invitation(_invitation())

    old_peer.stop.assert_called_once_with()
    controller._begin_remote_join.assert_not_called()
    assert controller._remote_invitation is None
    assert controller._guest_invite is None
    assert "Close WebJam" in controller.window.flash_message.call_args.args[0]
    controller.shutdown()


@pytest.mark.parametrize("prior_kind", ["owner", "runtime"])
def test_v3_replacement_fails_closed_when_prior_v3_cleanup_fails(
    tmp_path,
    prior_kind,
) -> None:
    controller = _controller(tmp_path)
    prior = mock.MagicMock()
    prior.stop.side_effect = RuntimeError("cleanup failed")
    if prior_kind == "owner":
        controller._remote_invite_owner = prior
    controller._remote_session = prior
    controller._begin_remote_join = mock.MagicMock()
    controller.window.flash_message = mock.MagicMock()

    assert not controller.accept_invitation(_invitation())

    prior.stop.assert_called_once_with()
    controller._begin_remote_join.assert_not_called()
    assert controller._remote_invitation is None
    assert controller._remote_session is None
    assert controller._remote_invite_owner is None
    assert "Close WebJam" in controller.window.flash_message.call_args.args[0]
    controller.shutdown()


def test_legacy_invite_replaces_pending_v3_guest_and_remote_mode(
    qapp, tmp_path
) -> None:
    controller = _controller(tmp_path)
    pending = _invitation()
    runtime = mock.MagicMock()
    controller._remote_invitation = pending
    controller._remote_session = runtime
    controller.bridge.enable_remote_guest_mode()
    controller.start_session_or_band_check = mock.MagicMock()
    save_settings(controller.settings)

    assert controller.accept_invite_url(
        create_invite_link("192.168.1.42", session_name="Legacy Jam")
    )

    runtime.stop.assert_called_once_with()
    assert controller._remote_invitation is None
    assert controller._remote_session is None
    assert not controller.bridge.remote_guest_mode_enabled
    assert controller.settings.jamulus_server == "192.168.1.42"
    controller.start_session_or_band_check.assert_called_once_with()
    controller.shutdown()


def test_legacy_invite_fails_closed_when_v3_cleanup_fails(tmp_path) -> None:
    controller = _controller(tmp_path)
    runtime = mock.MagicMock()
    runtime.stop.side_effect = RuntimeError("cleanup failed")
    controller._remote_session = runtime
    controller.start_session_or_band_check = mock.MagicMock()
    controller.window.flash_message = mock.MagicMock()
    save_settings(controller.settings)

    assert not controller.accept_invite_url(
        create_invite_link("192.168.1.42", session_name="Legacy Jam")
    )

    runtime.stop.assert_called_once_with()
    controller.start_session_or_band_check.assert_not_called()
    assert controller.settings.jamulus_server == "127.0.0.1"
    assert "Close WebJam" in controller.window.flash_message.call_args.args[0]
    controller.shutdown()


def test_remote_generation_or_path_change_reopens_live_band_check(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    controller._is_jamulus_running = mock.MagicMock(return_value=True)
    controller._open_band_check = mock.MagicMock()
    initial_settings_generation = controller._settings_generation
    first = RemoteSessionSnapshot(
        phase=RemoteSessionPhase.CONNECTED,
        role=SessionRole.GUEST,
        generation=1,
        path=TransportPath.SECURE_RELAY,
    )
    next_generation = RemoteSessionSnapshot(
        phase=RemoteSessionPhase.CONNECTED,
        role=SessionRole.GUEST,
        generation=2,
        path=TransportPath.SECURE_RELAY,
    )
    next_path = RemoteSessionSnapshot(
        phase=RemoteSessionPhase.CONNECTED,
        role=SessionRole.GUEST,
        generation=2,
        path=TransportPath.INTERNET_DIRECT,
    )

    assert controller._mark_remote_band_check_path(first, connected=True)
    assert not controller._mark_remote_band_check_path(first, connected=True)
    assert controller._mark_remote_band_check_path(
        next_generation,
        connected=True,
    )
    assert controller._mark_remote_band_check_path(next_path, connected=True)

    assert controller._settings_generation == initial_settings_generation + 3
    assert controller._open_band_check.call_count == 3
    assert controller._remote_band_check_required()
    controller.shutdown()


def test_lab_host_owner_is_installed_before_any_audio_launch(
    qapp, tmp_path, monkeypatch
) -> None:
    events = []

    class Owner:
        invitation_available = True
        snapshot = RemoteSessionSnapshot(
            phase=RemoteSessionPhase.PREPARING,
            role=SessionRole.HOST,
            generation=1,
            path=TransportPath.SECURE_RELAY,
        )

        def __init__(self, *, target_port, **_kwargs):
            assert target_port == 22124
            events.append("owner-created")

        def stop(self):
            events.append("owner-stopped")

    controller = _controller(tmp_path, hosting=True)
    controller.start_session_or_band_check = mock.MagicMock()
    original_enable = controller.bridge.enable_remote_host_mode

    def enable():
        events.append("mode-enabled")
        original_enable()

    controller.bridge.enable_remote_host_mode = enable
    monkeypatch.setattr(
        "services.native_remote_transport.NativeHostTransportOwner",
        Owner,
    )

    controller._begin_remote_host()
    _drain_until(qapp, lambda: controller._remote_invite_owner is not None)

    assert events[:2] == ["owner-created", "mode-enabled"]
    assert controller.bridge.remote_host_mode_enabled
    assert controller.bridge.jamulus_process is None
    assert controller._remote_band_check_required()
    controller.start_session_or_band_check.assert_called_once_with()
    controller.shutdown()


def test_host_hud_hides_consumed_copy_but_keeps_reset_available(
    qapp, tmp_path
) -> None:
    class Owner:
        invitation_available = True

    controller = _controller(tmp_path, hosting=True)
    owner = Owner()
    controller._remote_invite_owner = owner

    controller._update_session_hud()
    assert not controller.window.session_strip._invite_button.isHidden()
    assert controller.window.session_strip._reset_invite_action.isVisible()

    owner.invitation_available = False
    controller._update_session_hud()
    assert controller.window.session_strip._invite_button.isHidden()
    assert controller.window.session_strip._reset_invite_action.isVisible()
    controller.shutdown()
