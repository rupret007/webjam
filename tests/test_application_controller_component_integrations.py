"""Controller contracts for managed Jamulus updates and native Webex discovery."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from core.component_store import ComponentBusyReason  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from services.webex_app import (  # noqa: E402
    WebexAppInfo,
    WebexAppState,
)
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def controller():
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Component integration test",
    )
    value = ApplicationController(window, settings=AppSettings())
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
