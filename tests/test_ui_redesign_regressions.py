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
from webjam_qt.session_state import SessionPhase, SessionUiState  # noqa: E402
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


def test_launch_hierarchy_is_one_primary_then_two_clear_alternatives(
    styled_qapp, tmp_path
):
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(_settings(tmp_path))
    assert dialog.height() <= 540
    dialog.resize(460, 600)
    dialog.show()
    styled_qapp.processEvents()
    try:
        assert dialog.minimumWidth() <= 460
        assert dialog.minimumHeight() <= 600
        assert dialog._host_button.objectName() == "LaunchPrimary"
        assert dialog._join_button.objectName() == "LaunchSecondary"
        assert dialog._studio_button.objectName() == "LaunchSecondary"
        assert dialog._host_button.isDefault()
        assert not dialog._join_button.isDefault()
        assert not dialog._studio_button.isDefault()
        # Music first screen is Host / Join only. Studio stays a capability
        # behind the door, not a third button, so its geometry is unused.
        assert dialog._studio_button.isHidden()
        assert not dialog._studio_button.isVisibleTo(dialog)
        assert dialog._choice_helper.text() == ""
        assert dialog._music_profile_card.description() == "Play live together."
        assert dialog._art_profile_card.description() == "Make art together."
        assert dialog._name_input.accessibleName() == "Your name"
        assert not dialog._name_label.isVisibleTo(dialog)
        assert not dialog._name_input.isVisibleTo(dialog)
        assert not dialog._name_preview.isVisibleTo(dialog)
        assert not dialog._creator_profile_label.isVisibleTo(dialog)
        assert not dialog._creator_profile_selector.isVisibleTo(dialog)
        assert dialog._art_profile_card.isVisibleTo(dialog)
        assert dialog._music_profile_card.isVisibleTo(dialog)
        assert dialog._more_rooms_button.isVisibleTo(dialog)
        assert dialog._more_rooms_button.objectName() == "LaunchMoreRooms"
        assert dialog._more_rooms_button.text() == "Podcast or review"
        assert not dialog._choice_subtitle.isVisibleTo(dialog)
        assert (
            dialog._host_button.geometry().top()
            < dialog._join_button.geometry().top()
        )
        assert dialog._host_button.width() == dialog._join_button.width()
        assert dialog._host_button.width() >= 360
        assert dialog._host_button.accessibleDescription()
        assert dialog._join_button.accessibleDescription()
        for control in (
            dialog._host_button,
            dialog._join_button,
        ):
            assert dialog.rect().contains(_rect_in(control, dialog))
    finally:
        _destroy(dialog)


def test_launch_default_leaves_physical_title_bar_room_at_760_by_600(
    styled_qapp,
    tmp_path,
):
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(_settings(tmp_path))
    dialog.show()
    styled_qapp.processEvents()
    try:
        assert dialog.width() <= 760
        assert dialog.height() <= 520
        assert dialog.height() + 40 <= 600
        assert dialog.minimumHeight() <= 480
        for control in (
            dialog._logo,
            dialog._art_profile_card,
            dialog._music_profile_card,
            dialog._host_button,
            dialog._join_button,
            dialog._more_rooms_button,
        ):
            assert control.isVisibleTo(dialog)
            assert dialog.rect().contains(_rect_in(control, dialog))
        for hidden in (
            dialog._name_label,
            dialog._name_input,
            dialog._name_preview,
            dialog._studio_button,
            dialog._creator_profile_label,
            dialog._creator_profile_selector,
            dialog._choice_subtitle,
        ):
            assert not hidden.isVisibleTo(dialog)
        assert dialog._choice_helper.text() == ""
        assert dialog._music_profile_card.description() == "Play live together."
        assert dialog._name_input.accessibleName() == "Your name"

        dialog.show_join()
        styled_qapp.processEvents()
        for control in (
            dialog._invite_input,
            dialog._join_button_primary,
        ):
            assert control.isVisibleTo(dialog)
            assert dialog.rect().contains(_rect_in(control, dialog))
        assert not dialog._name_input.isVisibleTo(dialog)
    finally:
        _destroy(dialog)


def test_art_door_keeps_two_starts_and_host_join_inside_760_by_600(
    styled_qapp,
    tmp_path,
):
    settings = _settings(tmp_path)
    settings.last_creator_profile_key = "art"
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog.show()
    styled_qapp.processEvents()
    try:
        assert dialog.width() <= 760
        assert dialog.height() + 40 <= 600
        assert dialog._choice_helper.isVisibleTo(dialog) is False
        for control in (
            dialog._art_profile_card,
            dialog._music_profile_card,
            *dialog._visible_start_cards(),
            dialog._host_button,
            dialog._join_button,
        ):
            assert control.isVisibleTo(dialog)
            assert dialog.rect().contains(_rect_in(control, dialog)), (
                control.accessibleName()
            )
            assert control.height() >= 48
        assert [card.accessibleName() for card in dialog._visible_start_cards()] == [
            "Make together",
            "Paint along",
        ]
        assert dialog._more_rooms_button.isVisibleTo(dialog) is False
        assert dialog._name_input.isVisibleTo(dialog) is False
    finally:
        _destroy(dialog)


def test_windows_launch_name_roles_and_installer_do_not_overlap_at_default_size(
    styled_qapp,
    tmp_path,
):
    with (
        patch.object(sys, "platform", "win32"),
        patch(
            "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
            return_value="C:/WebJam/Jamulus-installer.exe",
        ),
    ):
        dialog = LaunchDialog(_settings(tmp_path))
    dialog.show()
    styled_qapp.processEvents()
    try:
        for hidden in (
            dialog._name_label,
            dialog._name_input,
            dialog._name_preview,
            dialog._studio_button,
            dialog._creator_profile_label,
            dialog._creator_profile_selector,
        ):
            assert not hidden.isVisibleTo(dialog)
        assert dialog._name_input.accessibleName() == "Your name"
        assert dialog._music_profile_card.description() == "Play live together."
        art_rect = _rect_in(dialog._art_profile_card, dialog)
        music_rect = _rect_in(dialog._music_profile_card, dialog)
        assert dialog._art_profile_card.isVisibleTo(dialog)
        assert dialog._music_profile_card.isVisibleTo(dialog)
        assert dialog.rect().contains(art_rect)
        assert dialog.rect().contains(music_rect)
        assert art_rect.right() < music_rect.left()
        controls = (
            dialog._host_button,
            dialog._join_button,
            dialog._install_jamulus_button,
            dialog._more_rooms_button,
        )
        rects = [_rect_in(control, dialog) for control in controls]
        for control, rect in zip(controls, rects):
            assert control.isVisibleTo(dialog)
            assert dialog.rect().contains(rect)
        assert art_rect.bottom() < rects[0].top()
        for upper, lower in zip(rects, rects[1:]):
            assert upper.bottom() < lower.top()
    finally:
        _destroy(dialog)


def test_offline_reference_studio_uses_the_full_window_without_session_chrome(
    styled_qapp,
):
    window = _window()
    window.show()
    styled_qapp.processEvents()
    try:
        window.show_reference_studio_only()
        styled_qapp.processEvents()

        assert window.workspace_stack.currentWidget() is window.reference_studio
        assert not window.session_strip.isVisibleTo(window)
        assert not window.session_hud.isVisibleTo(window)
        assert not window.session_controls.isVisibleTo(window)
        assert not window.side_rail.isVisibleTo(window)
        assert "Reference Studio" in window.windowTitle()
        assert window.reference_studio.isVisibleTo(window)
    finally:
        _destroy(window)


def test_join_keeps_one_name_one_secret_invite_and_one_primary_at_460px(
    styled_qapp,
    tmp_path,
):
    dialog = LaunchDialog(_settings(tmp_path))
    dialog.resize(460, 600)
    dialog.show_join()
    dialog.show()
    styled_qapp.processEvents()
    try:
        # hide_name stays true on Music until a Host/Join name error.
        # Join is therefore a secret invite and one primary, not a name field.
        assert not dialog._name_input.isVisibleTo(dialog)
        assert dialog._name_input.accessibleName() == "Your name"
        assert dialog._invite_input.isVisibleTo(dialog)
        assert dialog._join_button_primary.isVisibleTo(dialog)
        assert dialog._invite_input.height() >= 44
        assert dialog._join_button_primary.height() >= 48
        assert dialog._invite_input.accessibleName() == "Invite"
        assert dialog._invite_input.accessibleDescription()
        assert dialog._invite_input.echoMode() is dialog._invite_input.EchoMode.Password
        for control in (
            dialog._invite_input,
            dialog._join_button_primary,
        ):
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
        # The inline field exists only for the optional Webex step and is
        # hidden for an invite-ready HUD; it never receives invite material.
        assert all(not field.isVisibleTo(hud) for field in hud.findChildren(QLineEdit))
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
    with patch.object(dialog, "accept") as accept:
        dialog._host()
        dialog._host()
    accept.assert_called_once()
    assert dialog.selected_role == "host"
    assert dialog._submitting is True
    _destroy(dialog)


def test_join_activation_is_guarded_against_duplicate_submission(tmp_path):
    dialog = LaunchDialog(_settings(tmp_path))
    link = create_invite_link("192.168.1.42", session_name="Drummer Test")
    with patch.object(dialog, "accept") as accept:
        assert dialog.accept_invite(link) is True
        assert dialog.accept_invite(link) is False
    accept.assert_called_once()
    assert dialog.selected_role == "join"
    assert dialog._submitting is True
    _destroy(dialog)


def test_host_choice_save_failure_is_recoverable(tmp_path):
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(_settings(tmp_path))
    with patch.object(dialog, "_persist_role_choice", return_value=False):
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
    assert (
        message == "That invite link doesn’t look right. Copy it again from your host."
    )
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


@pytest.mark.parametrize(
    ("width", "height"),
    [(720, 560), (760, 600), (800, 600)],
)
def test_main_meeting_controls_fit_without_overlap(styled_qapp, width, height):
    window = _window()
    window.resize(width, height)
    window.session_strip.set_invite_available(True)
    window.session_strip.set_recording_available(True)
    window.session_strip.set_audio_state("End Session")
    window.show()
    styled_qapp.processEvents()
    try:
        assert window.minimumWidth() <= 720
        assert window.minimumHeight() <= 560
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


def test_native_jamulus_setup_guidance_fits_at_760_by_600(styled_qapp):
    window = _window()
    window.resize(760, 600)
    detail = (
        "Choose your interface, input channels, headphones, and buffer in "
        "Jamulus. WebJam uses a dedicated Jamulus profile for this app and "
        "leaves your regular Jamulus settings untouched. WebJam will continue "
        "automatically when the music connection is ready."
    )
    window.session_hud.set_state(
        "Set up your sound in Jamulus",
        detail,
        action_text="Bring Jamulus Forward",
        action_visible=True,
        action_kind="bring_jamulus",
    )
    window.show()
    styled_qapp.processEvents()
    try:
        hud = window.session_hud
        detail_rect = _rect_in(hud._detail, hud)
        action_rect = _rect_in(hud._action, hud)
        assert hud._detail.text() == detail
        assert hud._detail.isVisibleTo(window)
        assert hud._action.isVisibleTo(window)
        assert hud.rect().contains(detail_rect)
        assert hud.rect().contains(action_rect)
        assert detail_rect.right() < action_rect.left()
        assert hud._detail.height() >= hud._detail.minimumSizeHint().height()
    finally:
        _destroy(window)


@pytest.mark.parametrize("width", [760, 800])
def test_participant_grid_wraps_without_horizontal_clipping(styled_qapp, width):
    window = _window()
    window.resize(width, 600)
    window.participant_grid.set_participants(
        [
            ParticipantPresentation(index, f"Musician {index}", "Bandmate")
            for index in range(6)
        ]
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


def test_stage_can_be_passive_when_hud_owns_primary_action(styled_qapp):
    window = _window()
    window.resize(760, 600)
    window.participant_grid.set_session_state(
        SessionUiState(
            SessionPhase.NOT_CONNECTED,
            "Ready when you are",
            "Start from the session bar when your band is ready.",
            show_primary=False,
            show_ready_check=False,
            show_practice=False,
        )
    )
    window.session_hud.set_state(
        "Ready when you are",
        "Start when your band is ready.",
        action_text="Start Session",
        action_kind="primary",
    )
    window.show()
    styled_qapp.processEvents()
    try:
        grid = window.participant_grid
        assert grid._empty_state.isVisibleTo(window)
        assert not grid._empty_primary.isVisibleTo(window)
        assert not grid._empty_ready.isVisibleTo(window)
        assert not grid._empty_practice.isVisibleTo(window)
        assert window.session_hud._action.isVisibleTo(window)
        assert window.session_hud._action.accessibleName() == "Start Session"
    finally:
        _destroy(window)


def test_host_start_failure_points_to_the_real_band_check_menu():
    state = SessionUiState.host_start_failed()

    assert "More → Band Check / Verify Sound" in state.message
    assert "Troubleshooting" not in state.message


def test_initial_focus_moves_from_title_to_visible_hud_action(qapp, tmp_path):
    window = _window()
    controller = ApplicationController(window, settings=_settings(tmp_path))
    window.show()
    for _ in range(30):
        qapp.processEvents()
        if window.focusWidget() is window.session_hud._action:
            break
    try:
        assert window.session_hud._action.isVisibleTo(window)
        assert window.focusWidget() is window.session_hud._action
    finally:
        controller.shutdown()
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


def test_microphone_permission_required_then_continues_to_system_prompt(qapp, tmp_path):
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
            assert (
                controller.window.participant_grid._empty_primary.text() == "Continue"
            )
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
        assert (
            grid._empty_state.property("sessionState")
            == SessionPhase.PERMISSION_DENIED.value
        )
        assert grid._empty_primary.text() == "Open System Settings"
        assert grid._empty_primary_action == "microphone_settings"
        controller.bridge.launch_jamulus.assert_not_called()

        with patch(
            "PySide6.QtGui.QDesktopServices.openUrl", return_value=True
        ) as opened:
            grid._empty_primary.click()
        opened.assert_called_once()
        assert (
            grid._empty_state.property("sessionState")
            == SessionPhase.PERMISSION_REQUIRED.value
        )
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
        with (
            patch(
                "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ) as question,
            patch(
                "webjam_qt.controllers.audio_coordinator.threading.Thread",
                _ImmediateThread,
            ),
            patch.object(
                controller._ui_invoker,
                "invoke",
                side_effect=lambda callback: callback(),
            ),
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
def test_live_window_close_is_role_aware(hosting, expected_title, expected_body):
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
    with (
        patch.object(QMessageBox, "information") as information,
        patch.object(QMessageBox, "question") as question,
    ):
        assert ApplicationController._confirm_close(controller) is False
    information.assert_called_once()
    question.assert_not_called()
    controller.recording.confirm_quit.assert_not_called()


def test_track_export_in_progress_blocks_close_without_stopping_the_jam():
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
    with (
        patch.object(QMessageBox, "information") as information,
        patch.object(QMessageBox, "question") as question,
    ):
        assert ApplicationController._confirm_close(controller) is False
    information.assert_called_once()
    question.assert_not_called()
    controller.recording.confirm_quit.assert_not_called()


def test_close_is_vetoed_while_end_leave_or_invite_switch_is_still_running():
    controller = SimpleNamespace(
        audio=SimpleNamespace(stopping=True),
        _invite_switch_in_flight=False,
        window=object(),
    )
    with (
        patch.object(QMessageBox, "information") as information,
        patch.object(QMessageBox, "question") as question,
    ):
        assert ApplicationController._confirm_close(controller) is False
    information.assert_called_once()
    assert "still running" in information.call_args.args[1].lower()
    question.assert_not_called()


def test_close_after_failed_cleanup_points_to_the_retry_action():
    controller = SimpleNamespace(
        audio=SimpleNamespace(
            stopping=False,
            cleanup_retry_required=True,
        ),
        _invite_switch_in_flight=False,
        settings=SimpleNamespace(host_server_enabled=False),
        window=object(),
    )
    with patch.object(QMessageBox, "information") as information:
        assert ApplicationController._confirm_close(controller) is False

    assert information.call_args.args[1] == "Finish session cleanup first"
    assert "Try Leave Jam" in information.call_args.args[2]


def test_unsaved_studio_edits_veto_window_close_until_save_retry(qapp):
    window = _window()
    studio = window.recording_studio
    controller = SimpleNamespace(
        recording=SimpleNamespace(
            is_recording_active=False,
            take_in_progress=False,
            confirm_quit=MagicMock(return_value=True),
        ),
        bridge=SimpleNamespace(
            hosted_server_alive=MagicMock(return_value=False),
            hosted_server_owned=MagicMock(return_value=False),
        ),
        settings=SimpleNamespace(host_server_enabled=False),
        window=window,
        _is_jamulus_running=MagicMock(return_value=False),
    )
    shutdown_requested = MagicMock()
    window.confirm_close = lambda: ApplicationController._confirm_close(controller)
    window.close_requested.connect(shutdown_requested)
    window.show()
    qapp.processEvents()
    try:
        with (
            patch.object(
                studio,
                "prepare_close",
                side_effect=(False, True),
            ) as prepare_close,
            patch.object(
                QMessageBox,
                "information",
            ) as information,
        ):
            assert window.close() is False
            assert window.isVisible()
            shutdown_requested.assert_not_called()
            information.assert_called_once()
            assert "recorded take is safe" in information.call_args.args[2].lower()
            assert (
                "arrange and mix edits are not saved"
                in information.call_args.args[2].lower()
            )

            assert window.close() is True
            shutdown_requested.assert_called_once_with()
            assert prepare_close.call_count == 2
    finally:
        window.confirm_close = None
        studio.shutdown()
        _destroy(window)


def test_direct_shutdown_does_not_teardown_after_studio_save_failure():
    studio = SimpleNamespace(
        prepare_close=MagicMock(return_value=False),
        shutdown=MagicMock(),
    )
    controller = SimpleNamespace(
        _shutdown=False,
        window=SimpleNamespace(recording_studio=studio),
    )
    with patch.object(QMessageBox, "information"):
        assert ApplicationController.shutdown(controller) is False
    assert controller._shutdown is False
    studio.shutdown.assert_not_called()
