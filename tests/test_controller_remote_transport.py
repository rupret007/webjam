from __future__ import annotations

import os
import sys
import threading
import time
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from core.network_invite import create_invite_link
from core.room_state import RoomIdentity, RoomState
from services.transport_runtime import TransportEvent
from core.remote_invitation import issue_remote_invitation
from core.session_transport import ConnectionQuality, SessionRole, TransportPath
from core.settings import AppSettings, save_settings
from services.remote_session_runtime import (
    RemoteBackendError,
    RemoteGuestConnection,
    RemoteSessionErrorCode,
    RemoteSessionPhase,
    RemoteSessionSnapshot,
    RemoteSessionStage,
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


class _AuthenticatedMusicRoomBackend:
    """Existing routing tests supply the host profile required by the wire contract."""

    def __init__(self, *, on_room_state=None, schedule_callback=lambda fn: fn(), **kwargs):
        self.on_room_state = on_room_state
        self.schedule_callback = schedule_callback
        self.connection_available = False
        self.room_identity = None

    def __init_subclass__(cls):
        start = cls.start_guest

        def start_with_room(self, invitation, *, generation):
            connection = start(self, invitation, generation=generation)
            self.connection_available = True
            self.room_identity = RoomIdentity.from_invitation(invitation)
            event = TransportEvent(
                0, "room_state_received", code="ok", state="connected",
                mode="guest", generation=generation, room_state=RoomState(1, "music"),
            )
            if self.on_room_state is not None:
                self.schedule_callback(lambda: self.on_room_state(event))
            return connection

        cls.start_guest = start_with_room


def test_v3_guest_waits_for_authenticated_backend_then_routes_jamulus(
    qapp, tmp_path, monkeypatch
) -> None:
    class Backend(_AuthenticatedMusicRoomBackend):
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
    controller.begin_startup_journey = mock.MagicMock()
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
    controller.begin_startup_journey.assert_called_once_with()

    controller._stop_remote_transport()
    assert controller.settings.jamulus_port == 22124
    assert not controller.bridge.remote_guest_mode_enabled
    controller.shutdown()


def test_direct_remote_route_activation_retires_reference_track_once(
    qapp, tmp_path, monkeypatch
) -> None:
    class Backend(_AuthenticatedMusicRoomBackend):
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
    controller.begin_startup_journey = mock.MagicMock()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        Backend,
    )

    with mock.patch.object(
        controller,
        "_stop_reference_track_for_session_end",
        return_value=True,
    ) as stop_reference:
        assert controller.accept_invitation(remote)
        _drain_until(qapp, lambda: controller.settings.jamulus_port == 43123)

        stop_reference.assert_called_once_with(background=True)
        assert controller._reference_track_remote_route_pre_retired is False

    controller._stop_remote_transport()
    controller.shutdown()


def test_busy_legacy_switch_latest_remote_invite_reuses_reference_retirement(
    qapp, tmp_path, monkeypatch
) -> None:
    class Backend(_AuthenticatedMusicRoomBackend):
        def start_guest(self, invitation, *, generation):
            assert invitation is remote
            return RemoteGuestConnection(
                loopback_port=43124,
                path=TransportPath.SECURE_RELAY,
                quality=ConnectionQuality.UNKNOWN,
                generation=generation,
            )

        def stop(self):
            return None

    class DeferredSwitchThread:
        def __init__(self, *, target=None, **_kwargs):
            self._target = target

        def start(self):
            return None

    remote = _invitation()
    controller = _controller(tmp_path)
    controller.bridge.jamulus_state = "Running"
    controller.bridge.hosted_server_alive = mock.MagicMock(return_value=False)
    controller.bridge.hosted_server_owned = mock.MagicMock(return_value=False)
    controller.begin_startup_journey = mock.MagicMock()
    switch_worker = {}
    real_thread = threading.Thread

    def thread_factory(*args, **kwargs):
        if kwargs.get("name") == "webjam-invite-switch":
            thread = DeferredSwitchThread(*args, **kwargs)
            switch_worker["target"] = thread._target
            return thread
        return real_thread(*args, **kwargs)

    def stop_primary():
        controller.bridge.jamulus_state = "Stopped"
        return True

    controller.bridge.stop_jamulus = mock.MagicMock(side_effect=stop_primary)
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        Backend,
    )

    with (
        mock.patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ),
        mock.patch(
            "webjam_qt.controllers.application_controller.threading.Thread",
            side_effect=thread_factory,
        ),
        mock.patch.object(
            controller,
            "_stop_reference_track_for_session_end",
            return_value=True,
        ) as stop_reference,
    ):
        assert controller.accept_invite_url(
            create_invite_link("192.168.1.42", session_name="Legacy Replacement")
        )
        assert controller._invite_switch_in_flight is True
        assert controller.accept_invitation(remote)
        assert controller._pending_invitation is remote

        switch_worker["target"]()
        _drain_until(qapp, lambda: controller.settings.jamulus_port == 43124)

        stop_reference.assert_called_once_with(background=False)
        assert controller._reference_track_remote_route_pre_retired is False
        assert controller._pending_invitation is None
        assert controller._invite_switch_in_flight is False

    controller._stop_remote_transport()
    controller.shutdown()


def test_retry_safe_remote_failure_retains_pre_retired_marker_until_cleanup(
    qapp, tmp_path, monkeypatch
) -> None:
    class Backend(_AuthenticatedMusicRoomBackend):
        def start_guest(self, _invitation, *, generation):
            raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE)

        def stop(self):
            return None

    invitation = _invitation()
    controller = _controller(tmp_path)
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        Backend,
    )

    assert controller._accept_remote_invitation(
        invitation,
        reference_track_already_retired=True,
    )
    _drain_until(
        qapp,
        lambda: (
            controller._remote_session.snapshot.phase is RemoteSessionPhase.FAILED
            and controller._remote_invitation is invitation
        ),
    )

    assert controller._reference_track_remote_route_pre_retired is True
    assert controller._stop_remote_transport() is True
    assert controller._reference_track_remote_route_pre_retired is False
    controller.shutdown()


def test_false_transport_stop_retains_runtime_and_route_for_retry(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    runtime = mock.MagicMock()
    runtime.stop.return_value = False
    runtime.snapshot.error_code = None
    base_settings = controller.settings
    controller._remote_session = runtime
    controller._remote_route_base_settings = base_settings
    controller._remote_route_generation = 7
    controller.bridge.disable_remote_guest_mode = mock.MagicMock()

    assert controller._stop_remote_transport() is False

    runtime.stop.assert_called_once_with()
    assert controller._remote_session is runtime
    assert controller._remote_route_base_settings is base_settings
    assert controller._remote_route_generation == 7
    controller.bridge.disable_remote_guest_mode.assert_not_called()

    runtime.stop.return_value = None
    controller.shutdown()


def test_successful_leave_restores_remote_base_after_worker_cleanup(
    qapp,
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    base_settings = controller.settings
    routed_settings = AppSettings(
        config_file=base_settings.config_file,
        host_server_enabled=False,
        jamulus_server="127.0.0.1",
        jamulus_port=43123,
    )
    controller.settings = routed_settings
    controller._remote_route_base_settings = base_settings
    runtime = mock.MagicMock()
    runtime.snapshot.error_code = None
    controller._remote_session = runtime

    with (
        mock.patch.object(
            controller,
            "_stop_reference_track_for_session_end",
            return_value=True,
        ),
        mock.patch.object(
            controller,
            "_stop_session_peer",
            return_value=True,
        ),
        mock.patch.object(
            controller.bridge,
            "stop_jamulus",
            return_value=True,
        ),
        mock.patch.object(
            controller.bridge,
            "hosted_server_alive",
            return_value=False,
        ),
        mock.patch.object(
            controller._ui_invoker,
            "invoke",
            side_effect=lambda callback: callback(),
        ),
        mock.patch.object(
            controller,
            "_reconfigure_services_after_settings",
            wraps=controller._reconfigure_services_after_settings,
        ) as reconfigure,
    ):
        controller.audio._stop_session_services(False)

    runtime.stop.assert_called_once_with()
    assert controller._remote_session is None
    assert controller._remote_route_base_settings is None
    assert controller.settings is base_settings
    reconfigure.assert_called_once_with(routed_settings)
    controller.shutdown()


def test_leave_failure_keeps_unstopped_remote_transport_retryable(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    runtime = mock.MagicMock()
    runtime.stop.return_value = False
    runtime.snapshot.error_code = None
    controller._remote_session = runtime
    controller.audio.stopping = True

    with (
        mock.patch.object(
            controller,
            "_stop_reference_track_for_session_end",
            return_value=True,
        ),
        mock.patch.object(
            controller,
            "_stop_session_peer",
            return_value=True,
        ),
        mock.patch.object(
            controller.bridge,
            "stop_jamulus",
            return_value=True,
        ),
        mock.patch.object(
            controller.bridge,
            "hosted_server_alive",
            return_value=False,
        ),
        mock.patch.object(
            controller._ui_invoker,
            "invoke",
            side_effect=lambda callback: callback(),
        ),
    ):
        controller.audio._stop_session_services(False)

    assert controller._remote_session is runtime
    assert controller.audio.stopping is False
    assert controller.audio.cleanup_retry_required is True
    assert controller.window.session_strip._audio_button.text() == "Try Leave Jam"
    assert controller.window.session_strip._audio_button.isEnabled()

    runtime.stop.return_value = None
    controller.audio.cleanup_retry_required = False
    controller.shutdown()


def test_v3_guest_enrollment_failure_requires_fresh_invitation_and_never_falls_through(
    qapp, tmp_path, monkeypatch
) -> None:
    attempts = []

    class Backend(_AuthenticatedMusicRoomBackend):
        def start_guest(self, invitation, *, generation):
            attempts.append(invitation)
            raise RuntimeError("PRIVATE-CAPABILITY-SENTINEL")

        def stop(self):
            return None

    controller = _controller(tmp_path)
    controller.bridge.launch_jamulus = mock.MagicMock()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        Backend,
    )

    invitation = _invitation()
    assert controller.accept_invitation(invitation)
    _drain_until(
        qapp,
        lambda: (
            controller._remote_session.snapshot.phase is RemoteSessionPhase.FAILED
            and controller._remote_invitation is None
            and controller._remote_invitation_requires_replacement
            and "Needs attention" in controller.window.session_hud._status.text()
        ),
    )

    # The guest backend entered enrollment, so this one-use invitation may have
    # been consumed. The UI cannot replay it or fall back to a LAN/localhost
    # Jamulus start behind the musician's back.
    assert attempts == [invitation]
    assert controller.window.participant_grid._empty_primary.isHidden()
    assert not controller.window.session_hud._action.isHidden()
    assert controller.window.session_hud._action.text() == "Paste New Invite"
    assert controller.window.session_hud._action_kind == "paste_invite"
    controller.audio.on_launch_toggle = mock.MagicMock(return_value=True)
    controller._retry_session()
    controller._on_launch_audio()
    controller.start_session_or_band_check()
    assert attempts == [invitation]
    controller.audio.on_launch_toggle.assert_not_called()
    controller.bridge.launch_jamulus.assert_not_called()
    controller.shutdown()


def test_v3_guest_pre_enrollment_failure_stage_and_hud_retry_same_invitation(
    qapp, tmp_path, monkeypatch
) -> None:
    attempts = []

    class Backend(_AuthenticatedMusicRoomBackend):
        def start_guest(self, invitation, *, generation):
            attempts.append(invitation)
            raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE)

        def stop(self):
            return None

    invitation = _invitation()
    controller = _controller(tmp_path)
    controller.bridge.launch_jamulus = mock.MagicMock()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        Backend,
    )

    assert controller.accept_invitation(invitation)
    _drain_until(
        qapp,
        lambda: (
            controller._remote_session.snapshot.phase is RemoteSessionPhase.FAILED
            and controller._remote_invitation is invitation
            and "Needs attention" in controller.window.session_hud._status.text()
        ),
    )

    # A routine HUD refresh preserves the one retry because the backend proved
    # the sidecar failed before it entered open_guest().
    controller._update_session_hud()
    assert controller.window.participant_grid._empty_primary.isHidden()
    assert not controller.window.session_hud._action.isHidden()
    assert controller.window.session_hud._action.text() == "Try Again"
    assert controller.window.session_hud._action_kind == "retry"

    # Exercise the real connected controls after the proven pre-enrollment
    # failure. Both route back to v3 enrollment, never Band Check or legacy
    # localhost Jamulus.
    controller.window.participant_grid._empty_primary.click()
    _drain_until(
        qapp,
        lambda: (
            len(attempts) == 2
            and controller._remote_session.snapshot.phase is RemoteSessionPhase.FAILED
            and controller._remote_invitation is invitation
            and controller.window.session_hud._action_kind == "retry"
        ),
    )
    controller.window.session_hud._action.click()
    _drain_until(
        qapp,
        lambda: (
            len(attempts) == 3
            and controller._remote_session.snapshot.phase is RemoteSessionPhase.FAILED
            and controller._remote_invitation is invitation
        ),
    )

    assert attempts == [invitation, invitation, invitation]
    controller.bridge.launch_jamulus.assert_not_called()
    controller.shutdown()


def test_replaced_remote_runtime_cannot_render_a_late_failure(qapp, tmp_path) -> None:
    """Runtime-local generations are not enough after a controller replacement."""

    controller = _controller(tmp_path)
    old_runtime = object()
    controller._remote_session = object()
    controller._show_remote_session_failure = mock.MagicMock()
    failed = RemoteSessionSnapshot(
        phase=RemoteSessionPhase.FAILED,
        role=SessionRole.GUEST,
        generation=1,
        error_code=RemoteSessionErrorCode.UNAVAILABLE,
    )

    controller._on_remote_session_snapshot(failed, source=old_runtime)

    controller._show_remote_session_failure.assert_not_called()
    controller._remote_session = None
    controller.shutdown()


def test_replaced_remote_runtime_cannot_activate_a_late_guest_route(
    qapp, tmp_path
) -> None:
    controller = _controller(tmp_path)
    old_runtime = object()
    controller._remote_session = object()
    controller._activate_remote_guest_route = mock.MagicMock()
    connected = RemoteSessionSnapshot(
        phase=RemoteSessionPhase.CONNECTED,
        role=SessionRole.GUEST,
        generation=1,
        loopback_port=43123,
        path=TransportPath.SECURE_RELAY,
    )

    controller._on_remote_session_snapshot(connected, source=old_runtime)

    controller._activate_remote_guest_route.assert_not_called()
    controller._remote_session = None
    controller.shutdown()


def test_active_remote_runtime_still_accepts_its_own_failure(qapp, tmp_path) -> None:
    controller = _controller(tmp_path)
    runtime = object()
    controller._remote_session = runtime
    controller._show_remote_session_failure = mock.MagicMock()
    failed = RemoteSessionSnapshot(
        phase=RemoteSessionPhase.FAILED,
        role=SessionRole.GUEST,
        generation=1,
        error_code=RemoteSessionErrorCode.UNAVAILABLE,
    )

    controller._on_remote_session_snapshot(failed, source=runtime)

    controller._show_remote_session_failure.assert_called_once_with(
        guest_enrollment=True,
        retry_safe=True,
        error_code=RemoteSessionErrorCode.UNAVAILABLE,
    )
    controller._remote_session = None
    controller.shutdown()


def test_remote_join_progress_uses_plain_bounded_states(qapp, tmp_path) -> None:
    controller = _controller(tmp_path)
    runtime = object()
    controller._remote_session = runtime

    controller._on_remote_session_snapshot(
        RemoteSessionSnapshot(
            phase=RemoteSessionPhase.PREPARING,
            role=SessionRole.GUEST,
            generation=1,
            stage=RemoteSessionStage.CONTACTING_HOST,
        ),
        source=runtime,
    )
    assert controller.window.session_hud._status.text() == "Contacting host"

    controller._on_remote_session_snapshot(
        RemoteSessionSnapshot(
            phase=RemoteSessionPhase.PREPARING,
            role=SessionRole.GUEST,
            generation=1,
            stage=RemoteSessionStage.SECURING_CONNECTION,
        ),
        source=runtime,
    )
    assert controller.window.session_hud._status.text() == "Securing connection"
    assert "private path" in controller.window.session_hud._detail.text()
    controller._remote_session = None
    controller.shutdown()


def test_timed_out_enrollment_offers_only_paste_new_invite(qapp, tmp_path) -> None:
    controller = _controller(tmp_path)
    failed = RemoteSessionSnapshot(
        phase=RemoteSessionPhase.FAILED,
        role=SessionRole.GUEST,
        generation=1,
        error_code=RemoteSessionErrorCode.TIMED_OUT,
        stage=RemoteSessionStage.NEEDS_ATTENTION,
    )
    runtime = mock.MagicMock(snapshot=failed)
    controller._remote_session = runtime
    controller._remote_invitation = _invitation()

    controller._on_remote_session_snapshot(failed, source=runtime)

    assert controller._remote_invitation is None
    assert controller._remote_invitation_requires_replacement
    assert controller.window.participant_grid._empty_primary.isHidden()
    assert controller.window.session_hud._action.text() == "Paste New Invite"
    assert controller.window.session_hud._action_kind == "paste_invite"
    assert "timed out" in controller.window.session_hud._detail.text().casefold()
    assert "CAPABILITY" not in controller.window.session_hud.accessibleDescription()
    controller._remote_session = None
    controller.shutdown()


def test_paste_new_invite_reopens_the_same_masked_join_door(
    qapp, tmp_path, monkeypatch
) -> None:
    invitation = _invitation()
    events = []

    class Dialog:
        band_invite = None

        def __init__(self, settings, parent=None, *, allow_workspace_choices=True):
            assert settings is controller.settings
            assert parent is controller.window
            assert allow_workspace_choices is False
            events.append("created")

        def show_join(self):
            events.append("join")

        def exec(self):
            return QDialog.DialogCode.Accepted

        def take_remote_invitation(self):
            return invitation

        def deleteLater(self):
            events.append("deleted")

    controller = _controller(tmp_path)
    controller.accept_invitation = mock.MagicMock(return_value=True)
    monkeypatch.setattr("webjam_qt.windows.launch_dialog.LaunchDialog", Dialog)

    controller._paste_new_invitation()

    assert events == ["created", "join", "deleted"]
    controller.accept_invitation.assert_called_once_with(invitation)
    controller.shutdown()


def test_remote_guest_intent_replaces_a_host_profile_conductor_token(qapp, tmp_path) -> None:
    """A pasted guest invite wins over a stale local Host profile immediately."""

    controller = _controller(tmp_path, hosting=True)
    controller._remote_invitation = _invitation()

    assert controller._session_conductor_facts().role.value == "guest"
    controller._update_session_hud()

    assert controller.session_conductor.token.role.value == "guest"
    controller.shutdown()


def test_v3_guest_replaces_idle_v2_peer_before_enrollment(
    qapp, tmp_path, monkeypatch
) -> None:
    class Backend(_AuthenticatedMusicRoomBackend):
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
    controller.begin_startup_journey = mock.MagicMock()
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
    assert controller.guest_peer is old_peer
    assert controller._guest_invite is mock.sentinel.v2_invitation
    assert "Close WebJam" in controller.window.flash_message.call_args.args[0]
    old_peer.stop.side_effect = None
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
    assert controller._remote_session is prior
    assert controller._remote_invite_owner is (
        prior if prior_kind == "owner" else None
    )
    assert "Close WebJam" in controller.window.flash_message.call_args.args[0]
    prior.stop.side_effect = None
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
    controller.begin_startup_journey = mock.MagicMock()
    save_settings(controller.settings)

    assert controller.accept_invite_url(
        create_invite_link("192.168.1.42", session_name="Legacy Jam")
    )

    runtime.stop.assert_called_once_with()
    assert controller._remote_invitation is None
    assert controller._remote_session is None
    assert not controller.bridge.remote_guest_mode_enabled
    assert controller.settings.jamulus_server == "192.168.1.42"
    controller.begin_startup_journey.assert_called_once_with()
    controller.shutdown()


def test_legacy_invite_fails_closed_when_v3_cleanup_fails(tmp_path) -> None:
    controller = _controller(tmp_path)
    runtime = mock.MagicMock()
    runtime.stop.side_effect = RuntimeError("cleanup failed")
    controller._remote_session = runtime
    controller.begin_startup_journey = mock.MagicMock()
    controller.window.flash_message = mock.MagicMock()
    save_settings(controller.settings)

    assert not controller.accept_invite_url(
        create_invite_link("192.168.1.42", session_name="Legacy Jam")
    )

    runtime.stop.assert_called_once_with()
    controller.begin_startup_journey.assert_not_called()
    assert controller.settings.jamulus_server == "127.0.0.1"
    assert "Close WebJam" in controller.window.flash_message.call_args.args[0]
    runtime.stop.side_effect = None
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
    controller.begin_startup_journey = mock.MagicMock()
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
    controller.begin_startup_journey.assert_called_once_with()
    controller.shutdown()


def test_lab_host_reconciles_an_early_owner_failure_before_startup(
    qapp, tmp_path, monkeypatch
) -> None:
    """A constructor-time owner failure cannot fall through to normal Host."""

    class Owner:
        invitation_available = False
        snapshot = RemoteSessionSnapshot(
            phase=RemoteSessionPhase.FAILED,
            role=SessionRole.HOST,
            generation=1,
            error_code=RemoteSessionErrorCode.UNAVAILABLE,
        )

        def __init__(self, *, on_snapshot, **_kwargs):
            # This callback fires before the controller can install us. The
            # delivered snapshot must still be reconciled afterward.
            on_snapshot(self.snapshot)

        def stop(self):
            return None

    controller = _controller(tmp_path, hosting=True)
    controller.begin_startup_journey = mock.MagicMock()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeHostTransportOwner",
        Owner,
    )

    controller._begin_remote_host()
    _drain_until(
        qapp,
        lambda: "could not open" in controller.window.session_hud._status.text().lower(),
    )

    controller.begin_startup_journey.assert_not_called()
    assert controller._remote_invite_owner is None
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
    controller._remote_invite_owner = None
    controller.shutdown()


def test_visible_reset_invite_requires_explicit_confirmation(tmp_path) -> None:
    controller = _controller(tmp_path, hosting=True)
    controller._remote_invite_owner = mock.MagicMock()
    controller._reset_remote_invite = mock.MagicMock()

    with mock.patch(
        "webjam_qt.controllers.application_controller.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ):
        controller.window.session_strip._reset_invite_action.trigger()
    controller._reset_remote_invite.assert_not_called()

    with mock.patch(
        "webjam_qt.controllers.application_controller.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        controller.window.session_strip._reset_invite_action.trigger()
    controller._reset_remote_invite.assert_called_once_with()
    controller.shutdown()
