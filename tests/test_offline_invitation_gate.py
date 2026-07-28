"""Offline-Studio invitation safety and visible command-surface contracts."""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
import textwrap
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QDialog

from core.network_invite import BandInvite
from core.remote_invitation import issue_remote_invitation
from core.settings import AppSettings
from webjam_qt.app import (
    WebJamApplication,
    _deliver_current_invitation,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.reference_studio_application import (
    ReferenceStudioApplicationController,
)
from webjam_qt.widgets.reference_studio_workspace import (
    ReferenceStudioWorkspace,
)
from webjam_qt.widgets.session_strip import SessionStrip
from webjam_qt.windows.conductor_window import ConductorWindow


PROFILE = "reference-local"
ALLOWED_PROFILES = frozenset({PROFILE})


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


class _PendingApplication:
    """Small owner using the production pending-invitation operations."""

    pending_invitation = WebJamApplication.pending_invitation
    invitation_is_pending = WebJamApplication.invitation_is_pending
    acknowledge_invitation = WebJamApplication.acknowledge_invitation

    def __init__(self, invitation) -> None:
        self._pending_invitation = invitation


def _remote_invitation():
    return issue_remote_invitation(
        PROFILE,
        allowed_profiles=ALLOWED_PROFILES,
        host_spki_sha256=bytes.fromhex("44" * 32),
        issued_at_unix=int(time.time()),
        session_reference=bytes.fromhex("11" * 16),
        invite_reference=bytes.fromhex("22" * 16),
        enrollment_capability=bytes.fromhex("33" * 32),
    ).invitation


def _window() -> ConductorWindow:
    return ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Reference Studio",
    )


def test_invitation_delivery_acknowledges_only_explicit_acceptance() -> None:
    invitation = BandInvite(host="192.168.1.42")
    app = _PendingApplication(invitation)
    reject = MagicMock(return_value=False)

    assert _deliver_current_invitation(app, invitation, reject) is False

    reject.assert_called_once_with(invitation)
    assert app.pending_invitation() is invitation

    accept = MagicMock(return_value=True)
    assert _deliver_current_invitation(app, invitation, accept) is True
    accept.assert_called_once_with(invitation)
    assert app.pending_invitation() is None


def test_queued_older_invitation_cannot_override_latest_wins() -> None:
    older = BandInvite(host="192.168.1.41")
    newest = BandInvite(host="192.168.1.42")
    app = _PendingApplication(newest)
    accept = MagicMock(return_value=True)

    assert _deliver_current_invitation(app, older, accept) is False

    accept.assert_not_called()
    assert app.pending_invitation() is newest


def test_newer_invalid_file_open_cancels_an_older_queued_invitation() -> None:
    raw = "webjam://join?v=3&r=reference-local&i=PRIVATE-SENTINEL"
    older = BandInvite(host="192.168.1.41")
    app = SimpleNamespace(
        _pending_invitation=older,
        _pending_invitation_error="",
        invitation_received=MagicMock(),
        invitation_error=MagicMock(),
    )
    event = SimpleNamespace(
        type=lambda: QEvent.Type.FileOpen,
        url=lambda: SimpleNamespace(toString=lambda: raw),
    )

    with patch("webjam_qt.app.sys.platform", "darwin"):
        assert WebJamApplication.event(app, event) is True

    assert app._pending_invitation is None
    app.invitation_received.emit.assert_not_called()
    app.invitation_error.emit.assert_called_once()
    assert "PRIVATE-SENTINEL" not in app._pending_invitation_error


@pytest.mark.parametrize("start_offline", [True, False])
def test_offline_studio_refuses_late_invite_without_live_state_mutation(
    qapp,
    tmp_path: Path,
    start_offline: bool,
) -> None:
    window = _window()
    settings = AppSettings(
        config_file=str(tmp_path / f"settings-{start_offline}.json"),
        host_server_enabled=False,
        jamulus_server="192.168.1.10",
    )
    controller = ApplicationController(
        window,
        settings=settings,
        offline_reference_studio=start_offline,
    )
    controller._begin_remote_join = MagicMock()
    controller.begin_startup_journey = MagicMock()
    controller.bridge.launch_jamulus = MagicMock()
    window.show()
    qapp.processEvents()
    if not start_offline:
        controller.begin_reference_studio_journey()
    else:
        window.show_reference_studio_only()
    invitation = _remote_invitation()
    before = (
        settings.host_server_enabled,
        settings.jamulus_server,
        settings.jamulus_port,
    )

    try:
        assert controller.accept_invitation(invitation) is False

        assert controller._remote_invitation is None
        assert controller._pending_invitation is None
        assert controller._invite_switch_in_flight is False
        assert (
            settings.host_server_enabled,
            settings.jamulus_server,
            settings.jamulus_port,
        ) == before
        controller._begin_remote_join.assert_not_called()
        controller.begin_startup_journey.assert_not_called()
        controller.bridge.launch_jamulus.assert_not_called()
        assert (
            window.statusBar().currentMessage()
            == ConductorWindow.OFFLINE_INVITATION_GUIDANCE
        )
        assert window.statusBar().isVisible()
        assert window.statusBar().accessibleDescription() == (
            ConductorWindow.OFFLINE_INVITATION_GUIDANCE
        )
    finally:
        controller.begin_startup_journey = MagicMock()
        controller.shutdown()
        window.deleteLater()
        qapp.processEvents()


def test_offline_controller_cannot_be_seeded_with_live_invitation() -> None:
    window = _window()
    try:
        with pytest.raises(ValueError, match="offline Reference Studio"):
            ApplicationController(
                window,
                settings=AppSettings(),
                remote_invitation=_remote_invitation(),
                offline_reference_studio=True,
            )
    finally:
        window.deleteLater()


def test_offline_launch_marks_controller_before_any_queued_invite_delivery(
    monkeypatch,
) -> None:
    from webjam_qt import app as app_module

    monkeypatch.delenv("WEBJAM_SMOKE_AUTOSTART_AUDIO", raising=False)
    monkeypatch.delenv("WEBJAM_SMOKE_LAUNCH_ONLY", raising=False)
    initial = AppSettings(config_file="/missing/initial.json")
    loaded = AppSettings(config_file="/missing/loaded.json")
    launcher = MagicMock()
    launcher.exec.return_value = QDialog.DialogCode.Accepted
    launcher.selected_role = "studio"
    launcher.session_name = "Reference Studio"
    qt_app = MagicMock()
    qt_app.exec.return_value = 0
    controller = MagicMock()
    window = MagicMock()

    with (
        patch.object(app_module.sys, "argv", ["WebJam"]),
        patch.object(app_module, "load_settings", side_effect=[initial, loaded]),
        patch.object(app_module, "LaunchDialog", return_value=launcher),
        patch.object(app_module.QApplication, "instance", return_value=qt_app),
        patch.object(app_module, "load_stylesheet", return_value=""),
        patch.object(app_module, "ConductorWindow", return_value=window),
        patch.object(
            app_module,
            "ApplicationController",
            return_value=controller,
        ) as controller_class,
        patch.object(app_module.QTimer, "singleShot"),
    ):
        assert app_module._run_app() == 0

    assert controller_class.call_args.kwargs["offline_reference_studio"] is True
    assert controller_class.call_args.kwargs["remote_invitation"] is None
    controller.start_companion_api.assert_not_called()


def test_offline_workspace_transition_keeps_prior_invitation_guidance_visible(
    qapp,
) -> None:
    window = _window()
    window.show()
    qapp.processEvents()
    try:
        window.show_offline_invitation_guidance()
        window.show_reference_studio_only()

        assert window.statusBar().isVisible()
        assert (
            window.statusBar().currentMessage()
            == ConductorWindow.OFFLINE_INVITATION_GUIDANCE
        )
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_every_non_mixer_studio_menu_command_has_a_dispatch_owner() -> None:
    source = textwrap.dedent(
        inspect.getsource(ReferenceStudioApplicationController._dispatch_command)
    )
    tree = ast.parse(source)
    handler_commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "handlers"
            for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Dict):
            handler_commands.update(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )

    independently_owned = {"show_mixer", "show_automation"}
    assert (
        ReferenceStudioWorkspace.COMMAND_IDS - independently_owned
    ) <= handler_commands


def test_every_live_more_menu_action_emits_an_explicit_semantic_command() -> None:
    strip = SessionStrip(
        mode_entries=[("music_jam", "Music Jam")],
        initial_mode_key="music_jam",
    )
    events: list[str] = []
    strip.tool_requested.connect(lambda key: events.append(f"tool:{key}"))
    strip.join_video_requested.connect(lambda: events.append("open_webex"))
    strip.reset_invite_requested.connect(lambda: events.append("reset_invite"))

    actions = [
        action
        for action in strip._tools_button.menu().actions()
        if not action.isSeparator()
    ]
    for action in actions:
        action.setVisible(True)
        action.setEnabled(True)
        action.trigger()

    assert events == [
        "tool:audio_settings",
        "open_webex",
        "tool:recording_setup",
        "tool:reference_track",
        "tool:takes",
        "tool:canvas",
        "tool:pocket_stage",
        "tool:diagnostics",
        "tool:help",
        "tool:support",
        "tool:about",
        "reset_invite",
        "tool:settings",
    ]


def test_every_live_band_check_menu_action_has_an_owner() -> None:
    strip = SessionStrip(
        mode_entries=[("music_jam", "Music Jam")],
        initial_mode_key="music_jam",
    )
    events: list[str] = []
    strip.ready_check_requested.connect(lambda: events.append("band_check"))
    strip.practice_requested.connect(lambda: events.append("practice_solo"))

    for action in strip._test_button.menu().actions():
        action.trigger()

    assert events == ["band_check", "practice_solo"]
