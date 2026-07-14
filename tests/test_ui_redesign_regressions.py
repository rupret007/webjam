"""Acceptance-level regressions for the simple black/orange WebJam flow.

These tests intentionally exercise rendered geometry and the public wording a
musician sees.  Unit-only assertions did not catch the previous wide minimum,
duplicate retry actions, double submission, or technical error leakage.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QAbstractAnimation,
    QEvent,
    QPoint,
    QRect,
    QTimer,
    Qt,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLineEdit,
    QMessageBox,
    QWidget,
)

from core.network_invite import create_invite_link  # noqa: E402
from core.session_transfer import SessionCredentials  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.session_state import SessionPhase  # noqa: E402
from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.widgets.jam_signal_graphic import JamSignalGraphic  # noqa: E402
from webjam_qt.widgets.participant_card import ParticipantPresentation  # noqa: E402
from webjam_qt.widgets.session_hud import SessionHud  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402
from webjam_qt.windows.launch_dialog import LaunchDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture(scope="module")
def styled_qapp(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    qapp.processEvents()
    try:
        yield qapp
    finally:
        qapp.setStyleSheet(previous)
        qapp.processEvents()


def _settings(tmp_path: Path, **overrides) -> AppSettings:
    values = {"config_file": str(tmp_path / "settings.json"), **overrides}
    return AppSettings(**values)


def _window() -> ConductorWindow:
    return ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Band Rehearsal",
    )


def _destroy(widget: QWidget) -> None:
    """Release Qt parent/child graphs instead of leaving cycles to suite GC."""
    if hasattr(widget, "confirm_close"):
        widget.confirm_close = None
    widget.close()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _rect_in(child: QWidget, ancestor: QWidget) -> QRect:
    return QRect(child.mapTo(ancestor, QPoint(0, 0)), child.size())


def _visible_focus_chain(window: QWidget, start: QWidget) -> list[QWidget]:
    result: list[QWidget] = []
    visited: set[QWidget] = set()
    current = start
    while current not in visited:
        visited.add(current)
        if (
            current.focusPolicy() != Qt.FocusPolicy.NoFocus
            and current.isVisibleTo(window)
            and current.isEnabled()
        ):
            result.append(current)
        current = current.nextInFocusChain()
    return result


def test_launch_hierarchy_is_one_primary_then_one_secondary(
    styled_qapp, tmp_path
):
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(_settings(tmp_path))
    dialog.resize(460, 600)
    dialog.show()
    styled_qapp.processEvents()
    try:
        assert dialog.minimumWidth() <= 460
        assert dialog.minimumHeight() <= 600
        assert dialog._host_button.objectName() == "LaunchPrimary"
        assert dialog._join_button.objectName() == "LaunchSecondary"
        assert dialog._host_button.isDefault()
        assert not dialog._join_button.isDefault()
        assert dialog._host_button.geometry().top() < dialog._join_button.geometry().top()
        assert dialog._host_button.width() == dialog._join_button.width()
        assert dialog._host_button.width() >= 360
        assert dialog._host_button.accessibleDescription()
        assert dialog._join_button.accessibleDescription()
        for control in (dialog._host_button, dialog._join_button):
            assert dialog.rect().contains(_rect_in(control, dialog))
    finally:
        _destroy(dialog)


def test_join_remains_one_field_and_one_primary_at_460px(styled_qapp, tmp_path):
    dialog = LaunchDialog(_settings(tmp_path))
    dialog.resize(460, 600)
    dialog.show_join()
    dialog.show()
    styled_qapp.processEvents()
    try:
        assert dialog._invite_input.isVisibleTo(dialog)
        assert dialog._join_button_primary.isVisibleTo(dialog)
        assert dialog._invite_input.height() >= 44
        assert dialog._join_button_primary.height() >= 48
        assert dialog._invite_input.accessibleName() == "WebJam invite link"
        assert dialog._invite_input.accessibleDescription()
        assert (
            dialog._invite_input.echoMode()
            is dialog._invite_input.EchoMode.Password
        )
        for control in (dialog._invite_input, dialog._join_button_primary):
            assert dialog.rect().contains(_rect_in(control, dialog))
    finally:
        _destroy(dialog)


def test_host_invite_credential_is_never_rendered_or_exposed_to_accessibility(
    styled_qapp,
):
    hud = SessionHud()
    credentials = SessionCredentials.create()
    invite = create_invite_link(
        "192.168.1.42",
        session_name="Band Rehearsal",
        session_id=credentials.session_id,
        peer_port=43121,
        invite_token=credentials.invite_token,
    )
    hud.resize(1024, 72)
    hud.set_state(
        "Ready to share",
        "Send this link to your bandmate.",
        invite_available=True,
        action_visible=False,
    )
    hud.show()
    styled_qapp.processEvents()
    try:
        assert hud._invite_available is True
        assert not hasattr(hud, "_invite")
        assert not hasattr(hud, "_invite_url")
        assert hud.findChildren(QLineEdit) == []
        rendered = "\n".join(
            (
                hud._action.text(),
                hud._action.toolTip(),
                hud._action.accessibleName(),
                hud._action.accessibleDescription(),
                hud.accessibleDescription(),
                repr(vars(hud)),
            )
        )
        assert credentials.invite_token not in rendered
        assert invite not in rendered
        hud.resize(800, 72)
        styled_qapp.processEvents()
        assert hud._invite_available is True
        assert invite not in repr(vars(hud))
    finally:
        _destroy(hud)


def test_host_activation_is_guarded_against_duplicate_submission(tmp_path):
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(_settings(tmp_path))
    with patch.object(dialog, "_confirm_sound_setup", return_value=True) as confirm, patch.object(
        dialog, "accept"
    ) as accept:
        dialog._host()
        dialog._host()
    confirm.assert_called_once()
    accept.assert_called_once()
    assert dialog.selected_role == "host"
    assert dialog._submitting is True
    _destroy(dialog)


def test_join_activation_is_guarded_against_duplicate_submission(tmp_path):
    dialog = LaunchDialog(_settings(tmp_path))
    link = create_invite_link("192.168.1.42", session_name="Drummer Test")
    with patch.object(dialog, "_confirm_sound_setup", return_value=True) as confirm, patch.object(
        dialog, "accept"
    ) as accept:
        assert dialog.accept_invite(link) is True
        assert dialog.accept_invite(link) is False
    confirm.assert_called_once()
    accept.assert_called_once()
    assert dialog.selected_role == "join"
    assert dialog._submitting is True
    _destroy(dialog)


def test_host_canceled_sound_confirmation_is_recoverable(tmp_path):
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(_settings(tmp_path))
    with patch.object(dialog, "_confirm_sound_setup", return_value=False):
        dialog._host()
    assert dialog.selected_role == ""
    assert dialog._submitting is False
    assert dialog._host_button.isEnabled()
    _destroy(dialog)


def test_invalid_invite_error_does_not_echo_sensitive_or_technical_text(tmp_path):
    dialog = LaunchDialog(_settings(tmp_path))
    value = "https://bad.example/join?token=SUPER-SECRET&host=10.0.0.2"
    assert dialog.accept_invite(value) is False
    message = dialog._join_error.text()
    assert message == "That invite link doesn’t look right. Copy it again from your host."
    assert "SUPER-SECRET" not in message
    assert "10.0.0.2" not in message
    assert dialog._invite_input.text() == value  # preserve editable user input
    assert dialog._submitting is False
    _destroy(dialog)


def test_launch_graphic_is_static_scalable_and_accessible(styled_qapp):
    graphic = JamSignalGraphic()
    assert graphic.findChildren(QTimer) == []
    assert graphic.findChildren(QAbstractAnimation) == []
    assert graphic.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert graphic.accessibleName()
    assert "decorative" in graphic.accessibleDescription().lower()
    assert graphic.minimumSizeHint().width() <= 220
    for width, height in ((220, 104), (420, 164)):
        graphic.resize(width, height)
        graphic.show()
        styled_qapp.processEvents()
        image = graphic.grab().toImage()
        assert image.width() == width
        assert image.height() == height
        assert not image.isNull()
    _destroy(graphic)


@pytest.mark.parametrize("width", [760, 800])
def test_main_meeting_controls_fit_without_overlap(styled_qapp, width):
    window = _window()
    window.resize(width, 600)
    window.session_strip.set_invite_available(True)
    window.session_strip.set_recording_available(True)
    window.session_strip.set_audio_state("End Session")
    window.show()
    styled_qapp.processEvents()
    try:
        assert window.minimumWidth() <= 760
        assert window.minimumHeight() <= 600
        controls = [
            window.session_strip._invite_button,
            window.session_strip._record_button,
            window.session_strip._tools_button,
            window.session_strip._audio_button,
        ]
        rects = sorted(
            (_rect_in(control, window.session_controls) for control in controls),
            key=lambda rect: rect.left(),
        )
        for control, rect in zip(controls, sorted(rects, key=lambda item: item.left())):
            assert control.isVisibleTo(window)
            assert control.accessibleName()
            assert control.height() >= 40
            assert window.session_controls.rect().contains(rect)
        for left, right in zip(rects, rects[1:]):
            assert left.right() < right.left()
    finally:
        _destroy(window)


@pytest.mark.parametrize("width", [760, 800])
def test_participant_grid_wraps_without_horizontal_clipping(styled_qapp, width):
    window = _window()
    window.resize(width, 600)
    window.participant_grid.set_participants(
        [ParticipantPresentation(index, f"Musician {index}", "Bandmate") for index in range(6)]
    )
    window.show()
    styled_qapp.processEvents()
    try:
        grid = window.participant_grid
        assert grid.horizontalScrollBar().maximum() == 0
        content_width = grid.widget().width()
        for card in grid.cards():
            assert card.geometry().left() >= 0
            assert card.geometry().right() < content_width
            assert card.width() >= card.CARD_MIN_WIDTH
            for control in (card._fader, card._mute_button, card._solo_button):
                assert card.rect().contains(_rect_in(control, card))
    finally:
        _destroy(window)


def test_focus_order_moves_from_participant_to_bottom_controls(styled_qapp):
    window = _window()
    window.resize(760, 600)
    window.session_strip.set_invite_available(True)
    window.session_strip.set_recording_available(True)
    window.session_strip.set_audio_state("End Session")
    window.participant_grid.set_participants(
        [ParticipantPresentation(7, "Alice", "Guitar")]
    )
    window.show()
    styled_qapp.processEvents()
    try:
        card = window.participant_grid.cards()[0]
        chain = _visible_focus_chain(window, window.session_strip._title_input)
        expected = [
            window.session_strip._title_input,
            card._fader,
            card._mute_button,
            card._solo_button,
            window.session_strip._invite_button,
            window.session_strip._record_button,
            window.session_strip._tools_button,
            window.session_strip._audio_button,
        ]
        indices = [chain.index(widget) for widget in expected]
        assert indices == sorted(indices)
    finally:
        _destroy(window)


def test_primary_and_mixer_controls_have_names_and_desktop_targets(styled_qapp):
    window = _window()
    window.resize(760, 600)
    window.session_strip.set_invite_available(True)
    window.session_strip.set_audio_state("End Session")
    window.participant_grid.set_participants(
        [ParticipantPresentation(3, "Avery", "Drums")]
    )
    window.show()
    styled_qapp.processEvents()
    try:
        card = window.participant_grid.cards()[0]
        for control in (
            card._fader,
            card._mute_button,
            card._solo_button,
            window.session_strip._invite_button,
            window.session_strip._record_button,
            window.session_strip._tools_button,
            window.session_strip._audio_button,
        ):
            assert control.accessibleName()
            assert control.height() >= 28
        assert card._fader.accessibleDescription()
        assert window.session_controls.accessibleName() == "Session controls"
    finally:
        _destroy(window)


def test_microphone_permission_required_then_continues_to_system_prompt(
    qapp, tmp_path
):
    window = _window()
    controller = ApplicationController(window, settings=_settings(tmp_path))
    controller.bridge.jamulus_state = "Not launched"
    controller.bridge.launch_jamulus = MagicMock()
    try:
        with patch(
            "webjam_qt.platform_permissions.microphone_permission_status",
            return_value="not_determined",
        ):
            controller._on_launch_audio()
            assert controller.window.participant_grid._empty_primary.text() == "Continue"
            assert (
                controller.window.participant_grid._empty_state.property("sessionState")
                == SessionPhase.PERMISSION_REQUIRED.value
            )
            controller.bridge.launch_jamulus.assert_not_called()

            # The second explicit activation is what lets macOS present its
            # own permission prompt; it is not swallowed in an endless card.
            controller._on_launch_audio()
            controller.bridge.launch_jamulus.assert_called_once_with(manual=True)
    finally:
        controller.bridge.jamulus_launch_intended = False
        controller.shutdown()
        _destroy(window)


def test_denied_microphone_routes_to_settings_then_try_again(qapp, tmp_path):
    window = _window()
    controller = ApplicationController(window, settings=_settings(tmp_path))
    controller.bridge.jamulus_state = "Not launched"
    controller.bridge.launch_jamulus = MagicMock()
    try:
        with patch(
            "webjam_qt.platform_permissions.microphone_permission_status",
            return_value="denied",
        ):
            controller._on_launch_audio()
        grid = controller.window.participant_grid
        assert grid._empty_state.property("sessionState") == SessionPhase.PERMISSION_DENIED.value
        assert grid._empty_primary.text() == "Open System Settings"
        assert grid._empty_primary_action == "microphone_settings"
        controller.bridge.launch_jamulus.assert_not_called()

        with patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True) as opened:
            grid._empty_primary.click()
        opened.assert_called_once()
        assert grid._empty_state.property("sessionState") == SessionPhase.PERMISSION_REQUIRED.value
        assert grid._empty_primary.text() == "Try Again"
        assert grid._empty_primary_action == "start"
    finally:
        controller.shutdown()
        _destroy(window)


def test_guest_leave_never_stops_the_hosts_server_or_recorder(qapp, tmp_path):
    window = _window()
    controller = ApplicationController(
        window,
        settings=_settings(
            tmp_path,
            host_server_enabled=False,
            jamulus_server="192.168.1.42",
        ),
    )
    controller.bridge.jamulus_state = "Running"
    controller._jamulus_connected = True
    controller.recording.stop_server_recording_for_shutdown = MagicMock()
    controller.bridge.stop_jamulus = MagicMock()
    controller.bridge.stop_hosted_server = MagicMock()

    class _ImmediateThread:
        def __init__(self, *positional, target=None, args=(), **kwargs):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    try:
        with patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as question, patch(
            "webjam_qt.controllers.audio_coordinator.threading.Thread",
            _ImmediateThread,
        ), patch.object(
            controller._ui_invoker, "invoke", side_effect=lambda callback: callback()
        ):
            controller.audio.stop()
        assert question.call_args.args[1] == "Leave Jam?"
        assert "other musicians will stay connected" in question.call_args.args[2]
        assert question.call_args.args[4] == QMessageBox.StandardButton.No
        controller.bridge.stop_jamulus.assert_called_once()
        controller.bridge.stop_hosted_server.assert_not_called()
        controller.recording.stop_server_recording_for_shutdown.assert_not_called()
    finally:
        controller.shutdown()
        _destroy(window)


@pytest.mark.parametrize(
    ("hosting", "expected_title", "expected_body"),
    [
        (True, "End jam and quit?", "every connected musician"),
        (False, "Leave jam and quit?", "band can keep playing"),
    ],
)
def test_live_window_close_is_role_aware(
    hosting, expected_title, expected_body
):
    controller = SimpleNamespace(
        recording=SimpleNamespace(
            is_recording_active=False,
            confirm_quit=MagicMock(return_value=True),
        ),
        bridge=SimpleNamespace(hosted_server_alive=MagicMock(return_value=False)),
        settings=SimpleNamespace(host_server_enabled=hosting),
        window=object(),
        _is_jamulus_running=MagicMock(return_value=True),
    )
    with patch.object(
        QMessageBox,
        "question",
        return_value=QMessageBox.StandardButton.No,
    ) as question:
        assert ApplicationController._confirm_close(controller) is False
    assert question.call_args.args[1] == expected_title
    assert expected_body in question.call_args.args[2]
    assert question.call_args.args[4] == QMessageBox.StandardButton.No


def test_recording_close_confirmation_is_not_duplicated():
    controller = SimpleNamespace(
        recording=SimpleNamespace(
            is_recording_active=True,
            confirm_quit=MagicMock(return_value=True),
        ),
        bridge=SimpleNamespace(
            hosted_server_alive=MagicMock(return_value=True),
            hosted_server_owned=MagicMock(return_value=True),
        ),
        settings=SimpleNamespace(host_server_enabled=True),
        window=object(),
        _is_jamulus_running=MagicMock(return_value=True),
    )
    with patch.object(QMessageBox, "information") as information, patch.object(
        QMessageBox, "question"
    ) as question:
        assert ApplicationController._confirm_close(controller) is False
    information.assert_called_once()
    question.assert_not_called()
    controller.recording.confirm_quit.assert_not_called()


def test_logic_export_in_progress_blocks_close_without_stopping_the_jam():
    controller = SimpleNamespace(
        recording=SimpleNamespace(
            is_recording_active=False,
            confirm_quit=MagicMock(return_value=True),
        ),
        bridge=SimpleNamespace(hosted_server_alive=MagicMock(return_value=True)),
        settings=SimpleNamespace(host_server_enabled=True),
        window=SimpleNamespace(
            recording_studio=SimpleNamespace(export_in_progress=True)
        ),
        _is_jamulus_running=MagicMock(return_value=True),
    )
    with patch.object(QMessageBox, "information") as information, patch.object(
        QMessageBox, "question"
    ) as question:
        assert ApplicationController._confirm_close(controller) is False
    information.assert_called_once()
    question.assert_not_called()
    controller.recording.confirm_quit.assert_not_called()
