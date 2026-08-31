"""Controller contracts for managed Jamulus updates and native Webex discovery."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from core.component_store import ComponentBusyReason  # noqa: E402
from core.jamulus_roster_identity import (  # noqa: E402
    JamulusCommonProfile,
    ordered_client_local_roster_fingerprint,
    ordered_common_roster_digest,
)
from core.jamulus_rpc_client import (  # noqa: E402
    JamulusOrderedRosterProof,
    JamulusOrderedRosterRow,
    JamulusRpcMonitorIdentity,
)
from core.settings import AppSettings  # noqa: E402
from services.webex_app import (  # noqa: E402
    WebexAppInfo,
    WebexAppState,
)
from storage.repository import WebJamRepository  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.widgets.participant_card import ParticipantPresentation  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def controller(tmp_path: Path):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Component integration test",
    )
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        mix_file=str(tmp_path / "mix.json"),
        log_file=str(tmp_path / "webjam.log"),
    )
    repository = WebJamRepository(str(tmp_path / "webjam_app.db"))
    with patch(
        "webjam_qt.controllers.application_controller.WebJamRepository",
        return_value=repository,
    ):
        value = ApplicationController(window, settings=settings)
    try:
        yield value
    finally:
        if not value._shutdown:
            value._jamulus_update_service = None
            value.shutdown()


class _BusyHarness:
    _jamulus_component_client_active = (
        ApplicationController._jamulus_component_client_active
    )
    _jamulus_component_busy_status = (
        ApplicationController._jamulus_component_busy_status
    )

    def __init__(self) -> None:
        self.recording = SimpleNamespace(
            is_recording_active=False,
            take_in_progress=False,
        )
        self._reference_track = None
        self.audio = SimpleNamespace(connected=False)
        self.bridge = SimpleNamespace(
            practice_mode=False,
            jamulus_reconnect_inflight=False,
            jamulus_launch_intended=False,
            jamulus_state="Not launched",
            jamulus_process=None,
            hosted_server_alive=lambda: False,
        )
        self._shutdown_in_progress = False
        self._invite_switch_in_flight = False
        self._startup_attempt = None


@pytest.mark.parametrize(
    ("configure", "expected"),
    [
        (
            lambda value: setattr(value.recording, "take_in_progress", True),
            ComponentBusyReason.RECORDING_ACTIVE,
        ),
        (
            lambda value: setattr(
                value,
                "_reference_track",
                SimpleNamespace(snapshot=SimpleNamespace(active=True)),
            ),
            ComponentBusyReason.REFERENCE_TRACK_ACTIVE,
        ),
        (
            lambda value: setattr(value.bridge, "practice_mode", True),
            ComponentBusyReason.PRACTICE_ACTIVE,
        ),
        (
            lambda value: setattr(
                value.bridge,
                "jamulus_reconnect_inflight",
                True,
            ),
            ComponentBusyReason.RECONNECT_PENDING,
        ),
        (
            lambda value: setattr(value, "_startup_attempt", {"phase": "launching"}),
            ComponentBusyReason.LAUNCH_IN_PROGRESS,
        ),
        (
            lambda value: setattr(value.bridge, "jamulus_state", "Running"),
            ComponentBusyReason.CLIENT_ACTIVE,
        ),
        (
            lambda value: setattr(
                value.bridge,
                "hosted_server_alive",
                lambda: True,
            ),
            ComponentBusyReason.SERVER_ACTIVE,
        ),
    ],
)
def test_component_busy_check_reports_each_owned_runtime(configure, expected):
    harness = _BusyHarness()
    configure(harness)

    status = harness._jamulus_component_busy_status()

    assert status is not None
    assert status.reason is expected
    assert status.message == ""


def test_component_busy_check_returns_none_only_for_proven_idle_state():
    assert _BusyHarness()._jamulus_component_busy_status() is None


def test_updater_snapshot_is_dispatched_before_touching_qt(controller):
    callbacks = []
    controller._ui_invoker = SimpleNamespace(invoke=callbacks.append)
    controller._jamulus_update_dialog = MagicMock()
    controller._last_jamulus_update_state = ""
    snapshot = SimpleNamespace(state=SimpleNamespace(value="up-to-date"))

    controller._on_jamulus_update_snapshot(snapshot)

    controller._jamulus_update_dialog.set_snapshot.assert_not_called()
    assert len(callbacks) == 1
    callbacks[0]()
    controller._jamulus_update_dialog.set_snapshot.assert_called_once_with(
        snapshot
    )


def test_more_route_opens_jamulus_updates(controller):
    controller._open_jamulus_updates = MagicMock()

    controller._on_rail_view_changed("jamulus_updates")

    controller._open_jamulus_updates.assert_called_once_with()


def test_ready_update_message_points_to_the_visible_more_menu(controller):
    controller.window.flash_message = MagicMock()
    controller._last_jamulus_update_state = ""

    controller._render_jamulus_update_snapshot(
        SimpleNamespace(state=SimpleNamespace(value="ready"))
    )

    message = controller.window.flash_message.call_args.args[0]
    assert "More → Jamulus Updates" in message
    assert "Session Tools" not in message


def test_host_presence_delegates_lease_renewal_without_double_bind(
    controller,
):
    profile = JamulusCommonProfile("Host", 3, "Chicago", 2)
    proof = JamulusOrderedRosterProof(
        identity=JamulusRpcMonitorIdentity(2, 7, 4321),
        rpc_connection_generation=3,
        audio_connection_generation=5,
        roster_revision=11,
        observed_at=123.0,
        rows=(JamulusOrderedRosterRow(0, 0, profile),),
        own_ordinal=0,
        common_digest=ordered_common_roster_digest((profile,)),
        host_roster_fingerprint=ordered_client_local_roster_fingerprint(
            (0,),
            own_ordinal=0,
        ),
    )
    challenges = [
        SimpleNamespace(challenge="first", challenge_epoch=1, topology_epoch=1),
        SimpleNamespace(challenge="first", challenge_epoch=1, topology_epoch=1),
        SimpleNamespace(challenge="renewed", challenge_epoch=2, topology_epoch=1),
    ]
    fake_host = SimpleNamespace(
        active=True,
        install_recording_presence_roster=MagicMock(side_effect=challenges),
        bind_host_recording_presence=MagicMock(return_value=object()),
        invalidate_recording_presence=MagicMock(),
    )
    person = SimpleNamespace(channel_id=0, name="Host", is_local=True)
    old_host = controller.host_peer
    old_capture = controller.settings.local_capture_enabled
    try:
        controller.host_peer = fake_host
        controller.settings.local_capture_enabled = True
        controller._host_recording_presence_generation = 0
        controller._host_recording_presence_bound_key = None

        controller._publish_ordered_recording_presence(
            person, proof, capture_enabled=True, publish_guest=False
        )
        first_generation = (
            fake_host.bind_host_recording_presence.call_args.kwargs[
                "presence_generation"
            ]
        )
        assert (
            fake_host.install_recording_presence_roster.call_args.kwargs[
                "ambiguous_ordinals"
            ]
            == ()
        )
        assert (
            fake_host.bind_host_recording_presence.call_args.kwargs[
                "ambiguous_ordinals"
            ]
            == ()
        )
        controller._publish_ordered_recording_presence(
            person, proof, capture_enabled=True, publish_guest=False
        )
        assert fake_host.bind_host_recording_presence.call_count == 1

        controller._primary_ordered_roster_proof = proof
        controller.participants = {0: person}
        with (
            patch.object(
                controller.jamulus,
                "request_ordered_roster_refresh",
                return_value=True,
            ),
            patch.object(
                controller.jamulus,
                "ordered_roster_proof_for",
                return_value=proof,
            ),
            patch.object(
                controller.recording,
                "retry_pending_authenticated_roster_observation",
            ) as retry_pending,
        ):
            controller._renew_ordered_recording_presence()
        # HostPeerSession.install_recording_presence_roster owns the atomic
        # challenge renewal. ApplicationController must not immediately bind
        # the same host a second time merely because the challenge changed.
        assert fake_host.install_recording_presence_roster.call_count == 3
        assert fake_host.bind_host_recording_presence.call_count == 1
        retry_pending.assert_called_once_with()
        assert first_generation > 0
    finally:
        controller.host_peer = old_host
        controller.settings.local_capture_enabled = old_capture
        controller._primary_ordered_roster_proof = None
        controller._primary_ordered_roster_refresh_identity = None
        controller._primary_ordered_roster_refresh_key = None
        controller._host_recording_presence_bound_key = None
        controller.participants = {}


def test_host_cards_use_v2_ordinals_when_every_guest_calls_itself_local_zero(
    controller,
):
    profiles = (
        JamulusCommonProfile("Alex", 3, "Chicago", 2),
        JamulusCommonProfile("Alex", 4, "Chicago", 2),
        JamulusCommonProfile("Alex", 5, "Chicago", 2),
    )
    proof = JamulusOrderedRosterProof(
        identity=JamulusRpcMonitorIdentity(5, 9, 8765),
        rpc_connection_generation=2,
        audio_connection_generation=3,
        roster_revision=4,
        observed_at=456.0,
        rows=tuple(
            JamulusOrderedRosterRow(index, index, profile)
            for index, profile in enumerate(profiles)
        ),
        own_ordinal=0,
        common_digest=ordered_common_roster_digest(profiles),
        host_roster_fingerprint=ordered_client_local_roster_fingerprint(
            (0, 1, 2),
            own_ordinal=0,
        ),
    )
    durable_ids = tuple(str(uuid.uuid4()) for _ in range(3))
    claims = tuple(
        SimpleNamespace(
            self_ordinal=index,
            participant_id=participant_id,
            recorder_eligible=True,
            topology_epoch=1,
            process_generation=(9 if index == 0 else index + 1),
            rpc_connection_generation=(2 if index == 0 else 1),
            audio_connection_generation=(3 if index == 0 else 1),
            roster_count=3,
            ordered_roster_digest=proof.common_digest,
        )
        for index, participant_id in enumerate(durable_ids)
    )
    fake_host = SimpleNamespace(
        active=True,
        host_enrollment=SimpleNamespace(participant_id=durable_ids[0]),
        recording_presence_snapshot=MagicMock(return_value=claims),
        # A legacy private-local-ID lookup would collapse both guests onto the
        # same zero. Production mapping must never call it.
        participant_id_for_channel=MagicMock(return_value="wrong-v1-owner"),
    )
    old_host = controller.host_peer
    old_guest = controller.guest_peer
    old_proof = controller._primary_ordered_roster_proof
    try:
        controller.host_peer = fake_host
        controller._primary_ordered_roster_proof = proof
        controller.participants = {
            index: ParticipantPresentation(
                channel_id=index,
                name="Alex",
                roster_ordinal=index,
            )
            for index in range(3)
        }
        with patch.object(
            controller.jamulus,
            "ordered_roster_proof_for",
            return_value=proof,
        ):
            assert [
                controller.peer_participant_id_for_channel(index)
                for index in range(3)
            ] == list(durable_ids)
        fake_host.participant_id_for_channel.assert_not_called()
    finally:
        controller.host_peer = old_host
        controller.guest_peer = old_guest
        controller._primary_ordered_roster_proof = old_proof
        controller.participants = {}


def test_expired_ordered_presence_requests_epoch_refresh_before_invalidation(
    controller,
):
    profile = JamulusCommonProfile("Host", 3, "Chicago", 2)
    proof = JamulusOrderedRosterProof(
        identity=JamulusRpcMonitorIdentity(3, 8, 6789),
        rpc_connection_generation=2,
        audio_connection_generation=4,
        roster_revision=1,
        observed_at=1.0,
        rows=(JamulusOrderedRosterRow(0, 0, profile),),
        own_ordinal=0,
        common_digest=ordered_common_roster_digest((profile,)),
        host_roster_fingerprint=ordered_client_local_roster_fingerprint(
            (0,), own_ordinal=0
        ),
    )
    fake_host = SimpleNamespace(
        active=True,
        invalidate_recording_presence=MagicMock(),
    )
    old_host = controller.host_peer
    try:
        controller.host_peer = fake_host
        controller._primary_ordered_roster_proof = proof
        controller.participants = {
            0: ParticipantPresentation(
                channel_id=0,
                name="Host",
                is_local=True,
                roster_ordinal=0,
            )
        }
        refreshed = JamulusOrderedRosterProof(
            identity=proof.identity,
            rpc_connection_generation=proof.rpc_connection_generation,
            audio_connection_generation=proof.audio_connection_generation,
            roster_revision=proof.roster_revision,
            observed_at=9.0,
            rows=proof.rows,
            own_ordinal=proof.own_ordinal,
            common_digest=proof.common_digest,
            host_roster_fingerprint=proof.host_roster_fingerprint,
        )
        with (
            patch.object(
                controller.jamulus,
                "request_ordered_roster_refresh",
                return_value=True,
            ) as refresh,
            patch.object(
                controller.jamulus,
                "ordered_roster_proof_for",
                side_effect=(None, refreshed),
            ),
            patch.object(
                controller,
                "_publish_ordered_recording_presence",
            ) as publish,
        ):
            controller._renew_ordered_recording_presence()
            assert controller._primary_ordered_roster_proof is None
            assert (
                controller._primary_ordered_roster_refresh_identity
                == proof.identity
            )
            assert (
                controller._primary_ordered_roster_refresh_key
                == proof.authority_key
            )
            controller._renew_ordered_recording_presence()
        assert refresh.call_count == 2
        assert all(
            item.args == (proof.identity,) for item in refresh.call_args_list
        )
        assert controller._primary_ordered_roster_proof == refreshed
        fake_host.invalidate_recording_presence.assert_called_once_with()
        publish.assert_called_once_with(
            controller.participants[0],
            refreshed,
            capture_enabled=False,
            publish_guest=True,
        )
    finally:
        controller.host_peer = old_host
        controller._primary_ordered_roster_proof = None
        controller._primary_ordered_roster_refresh_identity = None
        controller._primary_ordered_roster_refresh_key = None
        controller.participants = {}


def test_ordered_presence_refresh_send_failure_retires_authority(controller):
    profile = JamulusCommonProfile("Host", 3, "Chicago", 2)
    proof = JamulusOrderedRosterProof(
        identity=JamulusRpcMonitorIdentity(3, 8, 6789),
        rpc_connection_generation=2,
        audio_connection_generation=4,
        roster_revision=1,
        observed_at=1.0,
        rows=(JamulusOrderedRosterRow(0, 0, profile),),
        own_ordinal=0,
        common_digest=ordered_common_roster_digest((profile,)),
        host_roster_fingerprint=ordered_client_local_roster_fingerprint(
            (0,), own_ordinal=0
        ),
    )
    fake_host = SimpleNamespace(
        active=True,
        invalidate_recording_presence=MagicMock(),
    )
    old_host = controller.host_peer
    try:
        controller.host_peer = fake_host
        controller._primary_ordered_roster_proof = proof
        with (
            patch.object(
                controller.jamulus,
                "request_ordered_roster_refresh",
                return_value=False,
            ),
            patch.object(
                controller.jamulus,
                "ordered_roster_proof_for",
            ) as read_proof,
        ):
            controller._renew_ordered_recording_presence()
        assert controller._primary_ordered_roster_proof is None
        assert controller._primary_ordered_roster_refresh_identity == proof.identity
        assert controller._primary_ordered_roster_refresh_key == proof.authority_key
        fake_host.invalidate_recording_presence.assert_called_once_with()
        read_proof.assert_not_called()
    finally:
        controller.host_peer = old_host
        controller._primary_ordered_roster_proof = None
        controller._primary_ordered_roster_refresh_identity = None
        controller._primary_ordered_roster_refresh_key = None


def test_guest_seed_recovery_republishes_invalidated_v2_observation(controller):
    profile = JamulusCommonProfile("Guest", 5, "Chicago", 2)
    proof = JamulusOrderedRosterProof(
        identity=JamulusRpcMonitorIdentity(4, 9, 7890),
        rpc_connection_generation=3,
        audio_connection_generation=5,
        roster_revision=2,
        observed_at=1.0,
        rows=(JamulusOrderedRosterRow(0, 0, profile),),
        own_ordinal=0,
        common_digest=ordered_common_roster_digest((profile,)),
        host_roster_fingerprint=ordered_client_local_roster_fingerprint(
            (0,), own_ordinal=0
        ),
    )
    refreshed = JamulusOrderedRosterProof(
        identity=proof.identity,
        rpc_connection_generation=proof.rpc_connection_generation,
        audio_connection_generation=proof.audio_connection_generation,
        roster_revision=proof.roster_revision,
        observed_at=9.0,
        rows=proof.rows,
        own_ordinal=proof.own_ordinal,
        common_digest=proof.common_digest,
        host_roster_fingerprint=proof.host_roster_fingerprint,
    )
    guest = SimpleNamespace(
        invalidate_recording_presence=MagicMock(),
        observe_presence_v2=MagicMock(),
    )
    inactive_host = SimpleNamespace(active=False)
    old_host = controller.host_peer
    old_guest = controller.guest_peer
    try:
        controller.host_peer = inactive_host
        controller.guest_peer = guest
        controller._primary_ordered_roster_proof = proof
        controller.participants = {
            0: ParticipantPresentation(
                channel_id=0,
                name="Guest",
                is_local=True,
                roster_ordinal=0,
            )
        }
        with (
            patch.object(
                controller.jamulus,
                "request_ordered_roster_refresh",
                return_value=True,
            ),
            patch.object(
                controller.jamulus,
                "ordered_roster_proof_for",
                side_effect=(None, refreshed),
            ),
        ):
            controller._renew_ordered_recording_presence()
            controller._renew_ordered_recording_presence()

        guest.invalidate_recording_presence.assert_called_once_with()
        guest.observe_presence_v2.assert_called_once()
        args = guest.observe_presence_v2.call_args.args
        kwargs = guest.observe_presence_v2.call_args.kwargs
        assert args == ("Guest",)
        assert kwargs["ordered_roster_digest"] == proof.common_digest
        assert kwargs["self_ordinal"] == 0
        assert kwargs["process_generation"] == 9
        assert kwargs["rpc_connection_generation"] == 3
        assert kwargs["audio_connection_generation"] == 5
    finally:
        controller.host_peer = old_host
        controller.guest_peer = old_guest
        controller._primary_ordered_roster_proof = None
        controller._primary_ordered_roster_refresh_identity = None
        controller._primary_ordered_roster_refresh_key = None
        controller.participants = {}


def test_settings_capture_save_never_publishes_stale_ordered_proof(controller):
    profile = JamulusCommonProfile("Host", 3, "Chicago", 2)
    proof = JamulusOrderedRosterProof(
        identity=JamulusRpcMonitorIdentity(7, 12, 9876),
        rpc_connection_generation=3,
        audio_connection_generation=5,
        roster_revision=2,
        observed_at=1.0,
        rows=(JamulusOrderedRosterRow(0, 0, profile),),
        own_ordinal=0,
        common_digest=ordered_common_roster_digest((profile,)),
        host_roster_fingerprint=ordered_client_local_roster_fingerprint(
            (0,), own_ordinal=0
        ),
    )
    fake_host = SimpleNamespace(
        active=True,
        invalidate_recording_presence=MagicMock(),
        bind_host_presence=MagicMock(),
    )
    old_host = controller.host_peer
    old_guest = controller.guest_peer
    try:
        controller.host_peer = fake_host
        controller.guest_peer = None
        controller._primary_ordered_roster_proof = proof
        controller.participants = {
            0: ParticipantPresentation(
                channel_id=0,
                name="Host",
                is_local=True,
                roster_ordinal=0,
            )
        }
        with (
            patch.object(
                controller.jamulus,
                "ordered_roster_proof_for",
                return_value=None,
            ),
            patch.object(
                controller.jamulus,
                "request_ordered_roster_refresh",
                return_value=True,
            ) as refresh,
            patch.object(
                controller,
                "_publish_ordered_recording_presence",
            ) as publish,
        ):
            controller._refresh_local_recording_presence_after_settings(True)

        publish.assert_not_called()
        refresh.assert_called_once_with(proof.identity)
        assert controller._primary_ordered_roster_proof is None
        assert controller._primary_ordered_roster_refresh_identity == proof.identity
        assert controller._primary_ordered_roster_refresh_key == proof.authority_key
        fake_host.invalidate_recording_presence.assert_called_once_with()
        fake_host.bind_host_presence.assert_called_once_with(
            0,
            "Host",
            capture_enabled=True,
        )
    finally:
        controller.host_peer = old_host
        controller.guest_peer = old_guest
        controller._primary_ordered_roster_proof = None
        controller._primary_ordered_roster_refresh_identity = None
        controller._primary_ordered_roster_refresh_key = None
        controller.participants = {}


def test_controller_registers_catalog_verified_component_providers(controller):
    client = object()
    server = object()
    service = MagicMock()
    service.managed_client_component.return_value = client
    service.managed_server_component.return_value = server
    controller._jamulus_update_service = service

    client_provider = controller.bridge._verified_jamulus_client_provider
    server_provider = controller.bridge._verified_jamulus_server_provider

    assert callable(client_provider)
    assert callable(server_provider)
    assert client_provider() is client
    assert server_provider() is server
    service.managed_client_component.assert_called_once_with()
    service.managed_server_component.assert_called_once_with()


def test_post_show_integrations_are_scheduled_once_without_constructor_io(
    controller,
):
    callbacks: list[tuple[int, object]] = []
    controller._desktop_integrations_started = False
    service = MagicMock()
    with patch.object(
        type(controller._level_timer),
        "singleShot",
        side_effect=lambda delay, callback: callbacks.append((delay, callback)),
    ), patch.object(
        controller,
        "_ensure_jamulus_update_service",
        return_value=service,
    ) as ensure_service:
        controller.start_desktop_integrations()
        controller.start_desktop_integrations()

    assert [delay for delay, _callback in callbacks] == [0, 1_500]
    ensure_service.assert_called_once_with()
    assert controller._webex_detection_thread is None


def test_dialog_actions_delegate_to_one_lazy_updater(controller):
    snapshot = SimpleNamespace(
        state=SimpleNamespace(value="idle"),
        message="Checking has not started.",
        active_version="3.12.2",
        available_version="",
        previous_version="",
        progress_percent=0,
        can_download=False,
        can_activate=False,
        can_approve=False,
        can_rollback=False,
        approve_label="Open verified installer",
        detail="",
    )
    service = MagicMock(snapshot=snapshot)
    service.target = SimpleNamespace(value="linux-x64")
    controller._jamulus_update_service = service

    controller._open_jamulus_updates()
    dialog = controller._jamulus_update_dialog
    assert dialog is not None

    dialog.check_requested.emit()
    dialog.download_requested.emit()
    dialog.activate_requested.emit()
    dialog.cancel_requested.emit()

    service.check_now.assert_called_once_with()
    service.download_available.assert_called_once_with()
    service.activate_when_idle.assert_called_once_with()
    service.cancel.assert_called_once_with()


def test_macos_approval_requires_visible_license_acceptance(controller):
    service = MagicMock()
    service.target = SimpleNamespace(value="macos-arm64")
    service.license_text.return_value = "Jamulus license terms"
    controller._jamulus_update_service = service

    with patch(
        "webjam_qt.windows.jamulus_update.JamulusLicenseDialog.exec",
        return_value=QDialog.DialogCode.Rejected,
    ):
        controller._approve_jamulus_update()
    service.approve_ready.assert_not_called()

    with patch(
        "webjam_qt.windows.jamulus_update.JamulusLicenseDialog.exec",
        return_value=QDialog.DialogCode.Accepted,
    ):
        controller._approve_jamulus_update()
    service.approve_ready.assert_called_once_with(license_accepted=True)


def test_non_macos_approval_never_infers_license_acceptance(controller):
    service = MagicMock()
    service.target = SimpleNamespace(value="windows-x64")
    controller._jamulus_update_service = service

    controller._approve_jamulus_update()

    service.approve_ready.assert_called_once_with(license_accepted=False)


def test_webex_detection_result_is_applied_on_ui_dispatch(controller):
    callbacks = []
    controller._ui_invoker = SimpleNamespace(invoke=callbacks.append)
    controller.window.webex_embed.set_app_status = MagicMock()
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
        path=Path("/Applications/Webex.app"),
    )
    with patch("services.webex_app.detect_webex_app", return_value=info):
        assert controller._start_webex_app_detection() is True
        controller._webex_detection_thread.join(timeout=2.0)

    assert len(callbacks) == 1
    controller.window.webex_embed.set_app_status.assert_not_called()
    callbacks[0]()
    controller.window.webex_embed.set_app_status.assert_called_once_with(
        WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
        reason_code="",
    )
    assert controller._webex_app_info is info


def test_webex_detection_exception_can_retry_to_verified_install(controller):
    callbacks = []
    controller._ui_invoker = SimpleNamespace(invoke=callbacks.append)

    with patch(
        "services.webex_app.detect_webex_app",
        side_effect=RuntimeError("transient verifier timeout"),
    ):
        assert controller._start_webex_app_detection() is True
        controller._webex_detection_thread.join(timeout=2.0)
    callbacks.pop(0)()

    embed = controller.window.webex_embed
    assert embed._app_status_label.text() == "Webex app check failed"
    assert not embed.recheck_button().isHidden()
    assert embed.recheck_button().isEnabled()

    verified = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
        path=Path("/Applications/Webex.app"),
    )
    with patch("services.webex_app.detect_webex_app", return_value=verified):
        embed.recheck_button().click()
        controller._webex_detection_thread.join(timeout=2.0)
    callbacks.pop(0)()

    assert embed.recheck_button().isHidden()
    assert embed.bring_forward_button().isEnabled()
    assert controller._webex_app_info is verified


def test_webex_install_action_opens_only_ciscos_official_handoff(controller):
    controller.window.flash_message = MagicMock()
    with patch.object(
        QMessageBox,
        "question",
        return_value=QMessageBox.StandardButton.Yes,
    ), patch(
        "services.webex_app.open_official_webex_installer",
        return_value=True,
    ) as handoff:
        controller._on_install_webex_requested()

    handoff.assert_called_once_with()
    message = controller.window.flash_message.call_args.args[0]
    assert "Cisco's official Webex download opened" in message
    assert "WebJam" not in message.split("opened", 1)[-1]


def test_integration_diagnostics_never_include_local_application_path(controller):
    controller._webex_app_info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
        path=Path("/Users/private/Applications/Webex.app"),
    )
    controller._jamulus_update_service = None

    diagnostics = controller._companion_get_diagnostics()

    assert diagnostics["jamulus_updater"] == {"state": "not-checked"}
    assert diagnostics["webex_app"]["state"] == "installed"
    assert "private" not in str(diagnostics).lower()
    assert "path" not in str(diagnostics).lower()


def test_webex_action_diagnostics_are_bounded_and_identity_free(controller):
    controller._webex_app_info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
        path=Path("/Users/private/Applications/Webex.app"),
    )
    controller._webex_events = []
    controller._record_webex_event("conversation-panel", "shown")
    controller._record_webex_event("show-webex-app", "activated-running")
    controller._record_webex_event(
        "meeting-handoff",
        "open-failed",
        reason_code="/meet/private-room",
    )

    diagnostics = controller._webex_app_public_diagnostics()

    assert diagnostics["events"] == [
        {"action": "conversation-panel", "result": "shown"},
        {"action": "show-webex-app", "result": "activated-running"},
        {"action": "meeting-handoff", "result": "open-failed"},
    ]
    assert "private" not in str(diagnostics).lower()
    assert "meet/" not in str(diagnostics).lower()
    assert "path" not in str(diagnostics).lower()


def test_controller_passes_component_trust_facts_to_saved_support_bundle(
    controller,
):
    controller._jamulus_update_service = SimpleNamespace(
        diagnostics=lambda: {
            "update": {
                "state": "up-to-date",
                "active_version": "3.12.3",
                "available_version": "3.12.3",
                "target": "macos-arm64",
                "progress_percent": 100,
                "reason_code": "",
                "restart_when_idle": False,
                "checked_at_utc": "2026-07-28T12:34:56Z",
                "message": "/Users/private/raw failure",
            },
            "catalog": {
                "status": "verified",
                "sequence": 9,
                "expires_at": "2026-08-20T12:34:56Z",
                "signer_fingerprint_sha256": "b" * 64,
                "catalog_url": "https://private.invalid/catalog",
            },
            "embedded_fallback_version": "3.12.2",
            "catalog_transport": {
                "last_check": "online",
                "trust_source": "packaged-certifi",
                "trust_status": "ready",
                "environment_ca_overrides": "ignored",
                "redirect_policy": "explicit-allowlist",
                "private_path": "/Users/private/cacert.pem",
            },
        }
    )
    controller._webex_app_info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        version="46.7.0.35472",
        publisher_verified=True,
        path=Path("/Users/private/Applications/Webex.app"),
    )

    report = controller._diagnostics_exporter().artifact().structured_report

    assert report["jamulus_update"]["active_version"] == "3.12.3"
    assert report["jamulus_update"]["catalog_sequence"] == 9
    assert report["jamulus_update"]["fallback_version"] == "3.12.2"
    assert report["jamulus_update"]["catalog_fetch_status"] == "online"
    assert report["jamulus_update"]["tls_trust_source"] == "packaged-certifi"
    assert report["jamulus_update"]["tls_trust_status"] == "ready"
    assert report["jamulus_update"]["tls_environment_ca_overrides"] == "ignored"
    assert (
        report["jamulus_update"]["catalog_redirect_policy"]
        == "explicit-allowlist"
    )
    assert report["webex_app"] == {
        "installed": True,
        "publisher_verified": True,
        "state": "installed",
        "version": "46.7.0.35472",
    }
    assert "private" not in str(report).lower()
    assert "catalog_url" not in str(report)


def test_shutdown_stops_updater_before_primary_jamulus(controller):
    order: list[str] = []
    service = MagicMock()
    service.close.side_effect = lambda **_kwargs: order.append("updater") or True
    controller._jamulus_update_service = service
    original_stop = controller.bridge.stop_jamulus
    controller.bridge.stop_jamulus = MagicMock(
        side_effect=lambda: order.append("jamulus") or True
    )
    try:
        assert controller.shutdown() is True
    finally:
        controller.bridge.stop_jamulus = original_stop

    assert order[:2] == ["updater", "jamulus"]
    service.close.assert_called_once_with(timeout=3.0)


def test_shutdown_stays_open_if_updater_worker_cannot_stop(controller):
    service = MagicMock()
    service.close.return_value = False
    controller._jamulus_update_service = service
    controller.bridge.stop_jamulus = MagicMock(return_value=True)

    with patch.object(QMessageBox, "information"):
        assert controller.shutdown() is False

    controller.bridge.stop_jamulus.assert_not_called()
    assert controller._shutdown is False
    assert controller._shutdown_cleanup_pending is True
    controller._jamulus_update_service = None
