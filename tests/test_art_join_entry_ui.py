"""Invitation guidance on every Join route, without starting room providers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton
from shiboken6 import isValid

from core.network_invite import create_invite_link
from core.settings import AppSettings
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationIngressErrorCode,
)
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    # Cocoa follows the system keyboard preference, which can skip buttons.
    # Exercise the supported full-keyboard route and restore the preference.
    previous = app.styleHints().tabFocusBehavior()
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    yield app
    app.styleHints().setTabFocusBehavior(previous)


@pytest.fixture
def join_door(qapp, tmp_path):
    dialogs = []

    def make(profile="music", size=(620, 520)):
        settings = AppSettings(
            config_file=str(tmp_path / "settings.json"), musician_name="Alex"
        )
        if profile == "art":
            settings.last_creator_profile_key = "art"
        with (
            patch("webjam_qt.windows.launch_dialog.sys.platform", "darwin"),
            patch(
                "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
                return_value="",
            ),
        ):
            dialog = LaunchDialog(settings)
        dialogs.append(dialog)
        dialog._menu_bar.setNativeMenuBar(False)
        dialog.setStyleSheet(load_stylesheet())
        if profile not in {"music", "art"}:
            dialog._workspace_actions[profile].trigger()
        assert dialog.selected_creator_profile_key == profile
        dialog.show_join()
        dialog.resize(*size)
        dialog.show()
        dialog.activateWindow()
        assert QTest.qWaitForWindowActive(dialog)
        qapp.processEvents()
        return dialog

    yield make
    for dialog in dialogs:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
        assert not isValid(dialog)
    qapp.processEvents()


@pytest.mark.parametrize("profile", ["music", "art", "podcast_voice", "review_rehearsal"])
def test_all_entry_profiles_explain_whole_message_and_conditional_network(
    join_door, profile
):
    dialog = join_door(profile)
    guidance = dialog._join_subtitle.text()
    assert "whole message" in guidance
    assert "If your invitation says “same network,”" in guidance
    assert "your host’s Wi-Fi or local network" in guidance
    assert dialog._invite_input.accessibleDescription() == guidance
    assert dialog._invite_input.echoMode() == QLineEdit.EchoMode.Password
    assert dialog._invite_input.hasFocus()
    assert len(dialog._join_page.findChildren(QLineEdit)) == 1
    assert [
        button.text()
        for button in dialog._join_page.findChildren(QPushButton)
        if button.isVisibleTo(dialog)
    ] == [dialog._join_button_primary.text(), "Back"]
    assert all(
        not card.isVisibleTo(dialog)
        for cards in dialog._start_cards.values()
        for card in cards
    )


@pytest.mark.parametrize("profile", ["music", "art"])
def test_paste_does_not_parse_or_claim_a_network_type(join_door, profile):
    dialog = join_door(profile)
    guidance = dialog._join_subtitle.text()
    with patch("webjam_qt.windows.launch_dialog.parse_invitation_at_ingress") as parse:
        for value in ("A nearby-room message", "A remote-room message"):
            dialog._invite_input.setText(value)
            assert dialog._join_status.text() == "Invitation pasted — choose Join"
            assert dialog._join_subtitle.text() == guidance
            assert dialog.selected_role == ""
        parse.assert_not_called()
    dialog._invite_input.clear()
    assert dialog._join_status.text() == "Paste your invitation"
    assert dialog._join_subtitle.text() == guidance


@pytest.mark.parametrize("profile", ["music", "art", "podcast_voice", "review_rehearsal"])
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_join_keyboard_route_keeps_field_action_and_back_reachable(
    join_door, qapp, profile, key
):
    dialog = join_door(profile)
    field = dialog._invite_input
    QTest.keyClick(field, Qt.Key.Key_Tab)
    assert dialog._join_button_primary.hasFocus()
    QTest.keyClick(dialog._join_button_primary, Qt.Key.Key_Tab)
    back = next(
        button
        for button in dialog._join_page.findChildren(QPushButton)
        if button.text() == "Back"
    )
    assert back.hasFocus()
    field.setFocus()
    field.setText("unchecked message")
    with patch(
        "webjam_qt.windows.launch_dialog.parse_invitation_at_ingress",
        side_effect=InvitationIngressError(
            InvitationIngressErrorCode.INVALID,
            "That invite link doesn’t look right. Copy it again from your host.",
        ),
    ) as parse:
        QTest.keyClick(field, key)
        qapp.processEvents()
    parse.assert_called_once()
    assert field.isEnabled()
    assert field.hasFocus()
    assert dialog._invite_input.accessibleDescription() == dialog._join_subtitle.text()
    assert dialog._join_status.text() == "Needs attention"
    assert dialog._join_error.isVisibleTo(dialog)
    assert not dialog.showing_choices
    assert dialog._join_error.accessibleDescription() == dialog._join_error.text()
    assert dialog._join_button_primary.isEnabled()
    assert dialog.selected_role == ""


@pytest.mark.parametrize("profile", ["music", "art"])
def test_whole_art_invitation_message_joins_without_another_profile_choice(
    join_door, profile
):
    dialog = join_door(profile)
    invitation = create_invite_link(
        "192.168.1.42",
        session_name="Making",
        session_id="11111111-1111-1111-1111-111111111111",
        peer_port=42001,
        invite_token="a" * 64,
    )
    dialog._invite_input.setText(
        "Join me to make art. Use the same network as the host.\n"
        f"{invitation}\nOpen WebJam, choose Join, and paste this whole message."
    )
    dialog._join_button_primary.click()
    assert dialog.selected_role == "join"
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.band_invite is not None
    assert dialog.band_invite.session_name == "Making"
    assert dialog._invite_input.text() == ""


@pytest.mark.parametrize(
    "profile,size",
    [
        ("music", (460, 480)),
        ("art", (460, 480)),
        ("music", (620, 520)),
        ("art", (620, 520)),
        ("podcast_voice", (620, 520)),
        ("review_rehearsal", (620, 520)),
    ],
)
@pytest.mark.parametrize("state", ["empty", "pasted", "error"])
def test_join_guidance_and_actions_fit_supported_window_sizes(
    join_door, qapp, profile, size, state
):
    dialog = join_door(profile, size)
    if state == "pasted":
        dialog._invite_input.setText("An unchecked invitation message")
    elif state == "error":
        dialog.show_ingress_error(
            "WebJam couldn’t save this choice. The invitation was cleared. "
            "Paste the full invitation again, then choose Join."
        )
    qapp.processEvents()
    assert (dialog.width(), dialog.height()) == size
    assert dialog.height() + 40 <= 600
    labels = [dialog._join_subtitle, dialog._join_error, dialog._join_privacy]
    for label in labels:
        if label.text():
            assert label.height() >= label.heightForWidth(label.width())
    controls = [
        dialog._join_title,
        dialog._join_subtitle,
        dialog._join_status,
        dialog._invite_input,
        dialog._join_privacy,
        dialog._join_error,
        dialog._join_button_primary,
        next(
            button
            for button in dialog._join_page.findChildren(QPushButton)
            if button.text() == "Back"
        ),
    ]
    rects = [QRect(control.mapTo(dialog, QPoint()), control.size()) for control in controls]
    for control, rect in zip(controls, rects):
        assert control.isVisibleTo(dialog)
        assert dialog.rect().contains(rect)
    assert all(first.bottom() < second.top() for first, second in zip(rects, rects[1:]))
    assert dialog._join_button_primary.height() >= 48
