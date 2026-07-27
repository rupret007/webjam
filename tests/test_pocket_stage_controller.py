from __future__ import annotations

import os
import tempfile
import unicodedata
import uuid
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core.pocket_stage import (
    PairingScope,
    PocketCommand,
    PocketCommandRejectionReason,
    PocketCommandRequest,
    PocketCommandStatus,
)
from core.settings import AppSettings
from core.session_conductor import SessionRole
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.widgets.participant_card import ParticipantPresentation
from webjam_qt.windows.conductor_window import ConductorWindow


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def controller():
    temporary = tempfile.TemporaryDirectory()
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = temporary.name
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Pocket Test",
    )
    instance = ApplicationController(window, settings=AppSettings())
    try:
        yield instance
    finally:
        instance.shutdown()
        window.close()
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        temporary.cleanup()


def _request(controller, command: PocketCommand, arguments: dict) -> PocketCommandRequest:
    projection = controller._get_pocket_projection()
    return PocketCommandRequest(
        command_id=str(uuid.uuid4()),
        command=command,
        generation=projection.generation,
        expected_revision=projection.revision,
        arguments=arguments,
    )


def test_gateway_is_constructed_but_listener_and_projection_timer_are_opt_in(controller) -> None:
    assert controller.pocket_stage_gateway.running is False
    assert controller._pocket_projection_timer.isActive() is False
    labels = [
        action.text() for action in controller.window.session_strip._tools_button.menu().actions()
    ]
    assert "Use iPhone as Pocket Stage…" in labels


def test_pairing_is_blocked_until_the_desktop_has_a_live_jam(controller) -> None:
    controller._jamulus_connected = False
    with (
        mock.patch.object(controller, "_is_jamulus_running", return_value=False),
        mock.patch.object(controller.pocket_stage_gateway, "start") as start,
        mock.patch.object(controller.window, "flash_message") as flash,
    ):
        controller._open_pocket_stage()

    start.assert_not_called()
    assert "Connect to the jam first" in flash.call_args.args[0]


def test_pairing_is_blocked_during_session_teardown(controller) -> None:
    controller._jamulus_connected = True
    controller.audio.stopping = True
    with (
        mock.patch.object(controller, "_is_jamulus_running", return_value=True),
        mock.patch.object(controller.pocket_stage_gateway, "start") as start,
        mock.patch.object(controller.window, "flash_message") as flash,
    ):
        controller._open_pocket_stage()

    start.assert_not_called()
    assert "session change" in flash.call_args.args[0]
    controller.audio.stopping = False


def test_projection_is_paired_private_and_scales_jamulus_gain(controller) -> None:
    controller._jamulus_connected = True
    controller.participants = {
        7: ParticipantPresentation(
            channel_id=7,
            name="Private Name",
            fader_level=127,
            is_local=True,
        )
    }

    controller._refresh_pocket_projection()
    projection = controller._get_pocket_projection()

    assert projection.participants[0].slot == 1
    assert projection.participants[0].label == "Private Name"
    assert projection.participants[0].fader_level == 100
    assert projection.participants[0].is_local is True
    assert "channel_id" not in str(projection.to_dict())


def test_projection_normalizes_and_bounds_ui_owned_text(controller) -> None:
    raw_name = "Cafe\u0301\n" + ("🎸" * 40)
    controller.participants = {
        4: ParticipantPresentation(channel_id=4, name=raw_name)
    }

    controller._refresh_pocket_projection()
    projection = controller._get_pocket_projection()
    label = projection.participants[0].label

    assert label == unicodedata.normalize("NFC", label)
    assert "\n" not in label
    assert len(label.encode("utf-8")) <= 80
    # The strict protocol constructor and serialization must remain usable
    # even when a Jamulus peer supplies non-canonical or oversized text.
    assert projection.to_dict()["participants"][0]["label"] == label


def test_mix_command_revalidates_revision_and_uses_existing_jamulus_owner(controller) -> None:
    controller._jamulus_connected = True
    controller.participants = {
        9: ParticipantPresentation(channel_id=9, name="Guitar", fader_level=100)
    }
    controller._refresh_pocket_projection()
    request = _request(
        controller,
        PocketCommand.SET_PARTICIPANT_FADER,
        {"slot": 1, "fader_level": 50},
    )

    with mock.patch.object(controller.jamulus, "set_fader_level") as set_level:
        receipt = controller._apply_pocket_command(request, (PairingScope.MIX,))

    assert receipt.status is PocketCommandStatus.ACCEPTED
    set_level.assert_called_once_with(9, 64)
    assert controller.participants[9].fader_level == 64

    with mock.patch.object(controller.jamulus, "set_fader_level") as stale_set:
        stale = controller._apply_pocket_command(request, (PairingScope.MIX,))
    assert stale.status is PocketCommandStatus.REJECTED
    assert stale.reason is PocketCommandRejectionReason.STALE_REVISION
    stale_set.assert_not_called()


def test_pan_is_rejected_until_jamulus_has_a_proven_provider_path(controller) -> None:
    controller._jamulus_connected = True
    controller.participants = {
        9: ParticipantPresentation(channel_id=9, name="Guitar")
    }
    controller._refresh_pocket_projection()
    request = _request(
        controller,
        PocketCommand.SET_PARTICIPANT_PAN,
        {"slot": 1, "pan": 80},
    )

    with mock.patch.object(controller, "_on_pan_changed") as set_pan:
        receipt = controller._apply_pocket_command(request, (PairingScope.MIX,))

    assert receipt.status is PocketCommandStatus.REJECTED
    assert receipt.reason is PocketCommandRejectionReason.UNSUPPORTED
    set_pan.assert_not_called()


def test_roster_shift_rejects_revision_before_resolving_slot(controller) -> None:
    controller._jamulus_connected = True
    controller.participants = {
        4: ParticipantPresentation(channel_id=4, name="Guitar", fader_level=100),
        9: ParticipantPresentation(channel_id=9, name="Keys", fader_level=100),
    }
    controller._refresh_pocket_projection()
    request = _request(
        controller,
        PocketCommand.SET_PARTICIPANT_FADER,
        {"slot": 1, "fader_level": 25},
    )

    # Guitar leaves and the next current slot shifts to Keys before the queued
    # owner-thread command runs. The old slot must never be reinterpreted.
    controller.participants.pop(4)
    with mock.patch.object(controller.jamulus, "set_fader_level") as set_level:
        receipt = controller._apply_pocket_command(request, (PairingScope.MIX,))

    assert receipt.status is PocketCommandStatus.REJECTED
    assert receipt.reason is PocketCommandRejectionReason.STALE_REVISION
    set_level.assert_not_called()


def test_same_shape_channel_replacement_still_invalidates_old_slot(controller) -> None:
    controller._jamulus_connected = True
    original = ParticipantPresentation(
        channel_id=4,
        name="Guitar",
        fader_level=100,
    )
    controller.participants = {4: original}
    controller._refresh_pocket_projection()
    request = _request(
        controller,
        PocketCommand.SET_PARTICIPANT_FADER,
        {"slot": 1, "fader_level": 25},
    )

    replacement = ParticipantPresentation(
        channel_id=4,
        name="Guitar",
        fader_level=100,
    )
    controller.participants = {4: replacement}
    with mock.patch.object(controller.jamulus, "set_fader_level") as set_level:
        receipt = controller._apply_pocket_command(request, (PairingScope.MIX,))

    assert original is not replacement
    assert receipt.status is PocketCommandStatus.REJECTED
    assert receipt.reason is PocketCommandRejectionReason.STALE_REVISION
    set_level.assert_not_called()


@pytest.mark.parametrize(
    ("jamulus_connected", "participant_connected"),
    [(False, True), (True, False)],
)
def test_mix_rejects_when_audio_or_target_is_disconnected(
    controller,
    jamulus_connected: bool,
    participant_connected: bool,
) -> None:
    controller._jamulus_connected = jamulus_connected
    controller.participants = {
        6: ParticipantPresentation(
            channel_id=6,
            name="Bass",
            fader_level=100,
            is_connected=participant_connected,
        )
    }
    controller._refresh_pocket_projection()
    request = _request(
        controller,
        PocketCommand.SET_PARTICIPANT_FADER,
        {"slot": 1, "fader_level": 20},
    )

    with mock.patch.object(controller.jamulus, "set_fader_level") as set_level:
        receipt = controller._apply_pocket_command(request, (PairingScope.MIX,))

    assert receipt.status is PocketCommandStatus.REJECTED
    assert receipt.reason is PocketCommandRejectionReason.UNAVAILABLE
    assert controller.participants[6].fader_level == 100
    set_level.assert_not_called()


def test_mark_uses_desktop_session_time_and_plain_text(controller) -> None:
    controller._refresh_pocket_projection()
    controller.window.session_strip._elapsed_seconds = 83
    request = _request(
        controller,
        PocketCommand.ADD_MARKER,
        {"at_ms": 999_999, "label": "Keep this"},
    )

    receipt = controller._apply_pocket_command(request, (PairingScope.MARKERS,))

    assert receipt.status is PocketCommandStatus.CONFIRMED
    notes = controller.window.session_canvas.current_notes()
    assert "01:23" in notes
    assert "Keep this" in notes
    assert "999999" not in notes


def test_handle_command_uses_active_socket_lease_on_owner_thread(controller) -> None:
    controller._refresh_pocket_projection()
    request = _request(
        controller,
        PocketCommand.ADD_MARKER,
        {"at_ms": 0, "label": "Lease works"},
    )
    epoch = 8
    lease_id = "active-phone"
    gateway = controller.pocket_stage_gateway
    with gateway._state_lock:
        gateway._running = True
        gateway._connection_epoch = epoch
        gateway._active_command_leases.add(lease_id)

    with mock.patch.object(
        controller._ui_invoker,
        "invoke",
        side_effect=lambda callback: callback(),
    ):
        receipt = controller._handle_pocket_command(
            request,
            (PairingScope.MARKERS,),
            epoch,
            lease_id,
        )

    assert receipt.status is PocketCommandStatus.CONFIRMED
    assert "Lease works" in controller.window.session_canvas.current_notes()


class _DeferredEvent:
    def __init__(self) -> None:
        self.value = False

    def set(self) -> None:
        self.value = True

    def is_set(self) -> bool:
        return self.value

    def wait(self, timeout: float) -> bool:
        assert timeout == 3
        return False


def test_late_owner_apply_publishes_terminal_receipt_once(controller) -> None:
    controller._refresh_pocket_projection()
    request = _request(
        controller,
        PocketCommand.ADD_MARKER,
        {"at_ms": 0, "label": "Late but valid"},
    )
    epoch = 9
    lease_id = "slow-phone"
    gateway = controller.pocket_stage_gateway
    with gateway._state_lock:
        gateway._running = True
        gateway._connection_epoch = epoch
        gateway._active_command_leases.add(lease_id)
    callbacks = []
    deferred = _DeferredEvent()

    with (
        mock.patch.object(
            controller._ui_invoker,
            "invoke",
            side_effect=callbacks.append,
        ),
        mock.patch(
            "webjam_qt.controllers.application_controller.threading.Event",
            return_value=deferred,
        ),
        mock.patch.object(gateway, "complete_pending_command") as complete,
    ):
        pending = controller._handle_pocket_command(
            request,
            (PairingScope.MARKERS,),
            epoch,
            lease_id,
        )
        assert pending.status is PocketCommandStatus.PENDING
        assert len(callbacks) == 1

        callbacks[0]()

    assert "Late but valid" in controller.window.session_canvas.current_notes()
    complete.assert_called_once()
    assert complete.call_args.args[0].status is PocketCommandStatus.CONFIRMED


def test_disconnected_socket_revokes_delayed_owner_command(controller) -> None:
    controller._refresh_pocket_projection()
    request = _request(
        controller,
        PocketCommand.ADD_MARKER,
        {"at_ms": 0, "label": "Must not land"},
    )
    epoch = 10
    lease_id = "departed-phone"
    gateway = controller.pocket_stage_gateway
    with gateway._state_lock:
        gateway._running = True
        gateway._connection_epoch = epoch
        gateway._active_command_leases.add(lease_id)
    callbacks = []
    deferred = _DeferredEvent()
    notes_before = controller.window.session_canvas.current_notes()

    with (
        mock.patch.object(
            controller._ui_invoker,
            "invoke",
            side_effect=callbacks.append,
        ),
        mock.patch(
            "webjam_qt.controllers.application_controller.threading.Event",
            return_value=deferred,
        ),
        mock.patch.object(gateway, "complete_pending_command") as complete,
    ):
        pending = controller._handle_pocket_command(
            request,
            (PairingScope.MARKERS,),
            epoch,
            lease_id,
        )
        assert pending.status is PocketCommandStatus.PENDING
        with gateway._state_lock:
            gateway._active_command_leases.discard(lease_id)
        callbacks[0]()

    assert controller.window.session_canvas.current_notes() == notes_before
    complete.assert_called_once()
    terminal = complete.call_args.args[0]
    assert terminal.status is PocketCommandStatus.REJECTED
    assert terminal.reason is PocketCommandRejectionReason.UNAVAILABLE


def test_phone_cannot_open_first_record_setup_or_record_as_guest(controller) -> None:
    controller._refresh_pocket_projection()
    request = _request(controller, PocketCommand.START_RECORDING, {})

    with mock.patch.object(controller.recording, "on_record_requested") as record:
        receipt = controller._apply_pocket_command(request, (PairingScope.RECORD,))

    assert receipt.status is PocketCommandStatus.REJECTED
    assert receipt.reason is PocketCommandRejectionReason.INVALID_STATE
    record.assert_not_called()


@pytest.mark.parametrize("blocked_by_export", [False, True])
def test_record_request_rejects_missing_secret_or_active_export(
    controller,
    blocked_by_export: bool,
) -> None:
    controller.session_conductor.reset_to_idle(SessionRole.HOST)
    controller.settings.host_server_enabled = True
    controller.settings.local_capture_choice_made = True
    controller.settings.server_rpc_secret_file = (
        "/private/runtime/secret" if blocked_by_export else ""
    )
    controller._jamulus_connected = True
    controller.window.recording_studio._exporting = blocked_by_export
    controller._refresh_pocket_projection()
    request = _request(controller, PocketCommand.START_RECORDING, {})

    with mock.patch.object(controller.recording, "on_record_requested") as record:
        receipt = controller._apply_pocket_command(request, (PairingScope.RECORD,))

    assert receipt.status is PocketCommandStatus.REJECTED
    assert receipt.reason is PocketCommandRejectionReason.INVALID_STATE
    record.assert_not_called()


def test_shutdown_always_stops_mobile_gateway(controller) -> None:
    with mock.patch.object(controller.pocket_stage_gateway, "stop") as stop:
        assert controller.shutdown() is True
    stop.assert_called_once()


def test_session_end_retires_phone_authority_and_requires_fresh_pairing(
    controller,
) -> None:
    gateway = controller.pocket_stage_gateway
    with gateway._state_lock:
        gateway._running = True
        gateway._connection_epoch = 7
        gateway._active_command_leases.add("old-phone")

    def stop_gateway() -> None:
        with gateway._state_lock:
            gateway._running = False
            gateway._connection_epoch += 1
            gateway._active_command_leases.clear()

    controller._pocket_projection_timer.start()
    controller._prepare_pocket_stage_for_session_end()
    with mock.patch.object(gateway, "stop", side_effect=stop_gateway) as stop:
        assert controller._stop_pocket_stage_for_session_end() is True
    controller._complete_pocket_stage_session_end(succeeded=True)

    stop.assert_called_once_with()
    assert gateway.running is False
    assert gateway._connection_epoch == 8
    assert gateway._active_command_leases == set()
    assert controller._pocket_projection_timer.isActive() is False
    assert controller.window.session_strip._pocket_stage_action.text() == (
        "Use iPhone as Pocket Stage…"
    )


def test_late_gateway_start_after_session_end_is_retired_before_pairing(
    controller,
) -> None:
    gateway = controller.pocket_stage_gateway
    controller._pocket_stage_starting = True
    controller._prepare_pocket_stage_for_session_end()

    with mock.patch.object(gateway, "stop"):
        assert controller._stop_pocket_stage_for_session_end() is True
    controller._complete_pocket_stage_session_end(succeeded=True)

    assert controller._pocket_stage_retire_after_start is True
    with (
        mock.patch.object(controller, "_stop_pocket_stage") as stop,
        mock.patch.object(controller, "_show_pocket_stage_offer") as show,
    ):
        controller._pocket_stage_started()

    stop.assert_called_once_with(network_changed=True)
    show.assert_not_called()


def test_stop_failure_keeps_sharing_visibly_unresolved(controller) -> None:
    controller._pocket_projection_timer.start()
    with mock.patch.object(controller.window, "flash_message") as flash:
        controller._pocket_stage_stop_failed(
            "WebJam could not fully stop iPhone sharing."
        )

    assert controller._pocket_projection_timer.isActive() is True
    labels = [
        action.text()
        for action in controller.window.session_strip._tools_button.menu().actions()
    ]
    assert "iPhone Sharing Stop Unresolved" in labels
    assert controller.window.session_strip._pocket_stage_action.isEnabled() is False
    flash.assert_called_once()


def test_unresolved_stop_blocks_restart_and_repeated_stop(controller) -> None:
    controller._pocket_stage_stop_unresolved = True
    with (
        mock.patch.object(controller.pocket_stage_gateway, "start") as start,
        mock.patch.object(controller.pocket_stage_gateway, "stop") as stop,
        mock.patch.object(controller.window, "flash_message") as flash,
    ):
        controller._open_pocket_stage()
        controller._stop_pocket_stage()

    start.assert_not_called()
    stop.assert_not_called()
    assert "Quit WebJam" in flash.call_args.args[0]


def test_wake_gap_retires_running_pocket_stage_before_network_reuse(controller) -> None:
    with controller.pocket_stage_gateway._state_lock:
        controller.pocket_stage_gateway._running = True
    controller._last_reconnect_tick_monotonic = 100.0

    with (
        mock.patch(
            "webjam_qt.controllers.application_controller.time.monotonic",
            return_value=100.0 + controller._WAKE_REVALIDATION_GAP_SECONDS + 1,
        ),
        mock.patch.object(controller, "_stop_pocket_stage") as stop,
        mock.patch.object(controller.window, "flash_message"),
    ):
        controller._revalidate_after_wake_gap()

    stop.assert_called_once_with(network_changed=True)


def test_sleep_gap_uses_wall_time_when_macos_monotonic_clock_pauses(controller) -> None:
    with controller.pocket_stage_gateway._state_lock:
        controller.pocket_stage_gateway._running = True
    controller._last_reconnect_tick_monotonic = 300.0
    controller._last_reconnect_tick_wall = 1_000.0

    with (
        mock.patch(
            "webjam_qt.controllers.application_controller.time.monotonic",
            return_value=301.0,
        ),
        mock.patch(
            "webjam_qt.controllers.application_controller.time.time",
            return_value=1_000.0 + controller._WAKE_REVALIDATION_GAP_SECONDS + 1,
        ),
        mock.patch.object(controller, "_stop_pocket_stage") as stop,
        mock.patch.object(controller.window, "flash_message"),
    ):
        controller._revalidate_after_wake_gap()

    stop.assert_called_once_with(network_changed=True)


def test_wake_during_gateway_start_retires_it_before_showing_code(controller) -> None:
    controller._pocket_stage_starting = True
    controller._last_reconnect_tick_monotonic = 200.0
    with mock.patch(
        "webjam_qt.controllers.application_controller.time.monotonic",
        return_value=200.0 + controller._WAKE_REVALIDATION_GAP_SECONDS + 1,
    ):
        controller._revalidate_after_wake_gap()
    assert controller._pocket_stage_retire_after_start is True

    with (
        mock.patch.object(controller, "_stop_pocket_stage") as stop,
        mock.patch.object(controller, "_show_pocket_stage_offer") as show,
    ):
        controller._pocket_stage_started()

    stop.assert_called_once_with(network_changed=True)
    show.assert_not_called()


def test_private_address_change_retires_active_pocket_stage(controller) -> None:
    with controller.pocket_stage_gateway._state_lock:
        controller.pocket_stage_gateway._running = True
    with (
        mock.patch.object(
            controller.pocket_stage_gateway,
            "bound_route_is_current",
            return_value=False,
        ),
        mock.patch.object(controller, "_stop_pocket_stage") as stop,
        mock.patch.object(controller.window, "flash_message") as flash,
    ):
        controller._revalidate_pocket_stage_route()

    stop.assert_called_once_with(network_changed=True)
    assert "address changed" in flash.call_args.args[0]
