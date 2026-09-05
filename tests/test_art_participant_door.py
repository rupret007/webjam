"""Actual Art door and room controls, without starting session providers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractButton

from core.creative_modes import get_creator_profile_by_key
from core.network_invite import create_invite_link
from core.remote_invitation import issue_remote_invitation
from core.settings import AppSettings
from tests.support.start_ux import (
    assert_no_banned_first_screen_words,
    harvest_first_screen,
    harvest_spoken_page,
)
from webjam_qt.theme import load_stylesheet
from webjam_qt.theme.tokens import Color
from webjam_qt.widgets.session_strip import SessionStrip
from webjam_qt.widgets.webex_embed import WebexEmbed
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def door(qapp, tmp_path):
    dialogs = []

    def make(profile="art", platform="darwin"):
        with (
            patch("webjam_qt.windows.launch_dialog.sys.platform", platform),
            patch(
                "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
                return_value="",
            ),
        ):
            dialog = LaunchDialog(
                AppSettings(
                    config_file=str(tmp_path / "settings.json"),
                    musician_name="Alex",
                    last_creator_profile_key=profile,
                )
            )
        dialog._menu_bar.setNativeMenuBar(False)
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(620, 520)
        dialog.show()
        dialog.activateWindow()
        qapp.processEvents()
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("profile", ["art", "music"])
@pytest.mark.parametrize("activation", ["click", "space"])
def test_repeating_the_selected_profile_keeps_it_selected(
    door, qapp, profile, activation
):
    dialog = door(profile)
    card = dialog._profile_cards[profile]
    for _ in range(3):
        if activation == "click":
            card.click()
        else:
            card.setFocus()
            QTest.keyClick(card, Qt.Key.Key_Space)
        qapp.processEvents()
        assert dialog.selected_creator_profile_key == profile
        assert [
            key for key, item in dialog._profile_cards.items() if item.isChecked()
        ] == [profile]
        assert dialog.selected_role == ""


@pytest.mark.parametrize("profile", ["podcast_voice", "review_rehearsal"])
def test_file_profiles_can_clear_both_door_cards_then_return_to_art(door, profile):
    dialog = door()
    dialog._workspace_actions[profile].trigger()
    assert dialog.selected_creator_profile_key == profile
    assert not any(card.isChecked() for card in dialog._profile_cards.values())
    assert dialog._creator_profile_selector.isVisibleTo(dialog)
    dialog._art_profile_card.click()
    dialog._art_profile_card.click()
    assert dialog.selected_creator_profile_key == "art"
    assert dialog._art_profile_card.isChecked()
    assert not dialog._music_profile_card.isChecked()
    assert not dialog._creator_profile_selector.isVisibleTo(dialog)


@pytest.mark.parametrize("profile", ["art", "music"])
@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_profile_activity_and_room_actions_form_a_compact_hierarchy(
    door, profile, platform
):
    dialog = door(profile, platform)
    buttons = [
        button
        for button in dialog._choice_page.findChildren(QAbstractButton)
        if button.isVisibleTo(dialog._choice_page)
    ]
    expected = ["Art", "Music"]
    if profile == "art":
        expected += ["Make together", "Paint along"]
    assert [button.text() for button in buttons] == expected + ["Host", "Join"]
    assert dialog.width() <= 620
    assert dialog.height() <= 520
    assert dialog.height() + 40 <= 600
    rects = {
        button.text(): QRect(button.mapTo(dialog, QPoint()), button.size())
        for button in buttons
    }
    for button in buttons:
        assert dialog.rect().contains(rects[button.text()])
        assert button.height() >= 48
    assert rects["Art"].right() < rects["Music"].left()
    assert rects["Host"].bottom() < rects["Join"].top()
    if profile == "art":
        assert rects["Art"].bottom() < rects["Make together"].top()
        assert rects["Make together"].bottom() < rects["Paint along"].top()
        assert rects["Paint along"].bottom() < rects["Host"].top()
        assert rects["Make together"].left() > rects["Art"].left()
        assert rects["Paint along"].right() < rects["Music"].right()
        assert rects["Make together"].height() == rects["Paint along"].height() == 64
    else:
        assert rects["Art"].bottom() < rects["Host"].top()
    assert_no_banned_first_screen_words(harvest_first_screen(dialog))


def _top_border_colors(widget):
    image = widget.grab().toImage()
    depth = max(1, round(2 * widget.devicePixelRatioF()))
    return {
        image.pixelColor(x, y).name().upper()
        for x in range(image.width() // 3, image.width() * 2 // 3)
        for y in range(depth)
    }


@pytest.mark.parametrize("group", ["profile", "activity"])
def test_keyboard_focus_does_not_paint_an_unselected_card_orange(door, qapp, group):
    dialog = door()
    if group == "profile":
        selected, focused = dialog._art_profile_card, dialog._music_profile_card
    else:
        selected, focused = dialog._visible_start_cards()
    focused.setFocus(Qt.FocusReason.TabFocusReason)
    qapp.processEvents()
    assert focused.hasFocus()
    assert selected.isChecked() and not focused.isChecked()
    assert Color.ACCENT_PRIMARY in _top_border_colors(selected)
    assert Color.TEXT_PRIMARY in _top_border_colors(focused)
    assert Color.ACCENT_PRIMARY not in _top_border_colors(focused)
    selected.setFocus(Qt.FocusReason.TabFocusReason)
    qapp.processEvents()
    assert Color.ACCENT_PRIMARY in _top_border_colors(selected)


@pytest.mark.parametrize("reset", ["replace", "back"])
def test_paste_is_unchecked_and_replacing_it_clears_spoken_errors(door, reset):
    dialog = door()
    dialog.show_join()
    assert dialog.accept_invite("not an invitation") is False
    assert dialog._join_error.accessibleDescription()
    if reset == "replace":
        with patch(
            "webjam_qt.windows.launch_dialog.parse_invitation_at_ingress"
        ) as parse:
            dialog._invite_input.setText("another unchecked value")
        parse.assert_not_called()
        assert dialog._join_status.text() == "Invitation pasted — choose Join"
    else:
        dialog.show_choices()
        dialog.show_join()
        assert dialog._join_status.text() == "Paste your invitation"
        assert dialog._invite_input.text() == ""
    assert dialog._join_error.text() == ""
    assert dialog._join_error.accessibleDescription() == ""


@pytest.mark.parametrize("version", [2, 3])
def test_failed_save_requests_a_fresh_paste_and_that_retry_succeeds(
    door, tmp_path, version
):
    dialog = door()
    if version == 2:
        invitation = create_invite_link(
            "192.168.1.42",
            session_name="Making",
            session_id="11111111-1111-1111-1111-111111111111",
            peer_port=42001,
            invite_token="a" * 64,
        )
    else:
        invitation = issue_remote_invitation(
            "reference-local",
            allowed_profiles={"reference-local"},
            host_spki_sha256=b"p" * 32,
        ).private_link.reveal_for_clipboard()
    dialog.show_join()
    dialog._invite_input.setText(invitation)
    with patch(
        "webjam_qt.windows.launch_dialog.save_settings",
        side_effect=OSError("private path"),
    ):
        dialog._join_button_primary.click()
    assert dialog.selected_role == ""
    assert dialog._join_error.isVisibleTo(dialog)
    assert "paste the full invitation again" in dialog._join_error.text().lower()
    assert dialog._join_error.accessibleDescription() == dialog._join_error.text()
    assert (
        dialog._choice_error.text()
        == dialog._choice_error.accessibleDescription()
        == ""
    )
    assert dialog._invite_input.text() == ""
    assert dialog.band_invite is None and dialog.remote_invitation is None
    assert invitation not in harvest_spoken_page(dialog)
    assert "private path" not in harvest_spoken_page(dialog)
    assert not (tmp_path / "settings.json").exists()
    # An empty retry cannot silently reuse the cleared private capability.
    dialog._join_button_primary.click()
    assert dialog.selected_role == ""
    assert not (tmp_path / "settings.json").exists()
    dialog._invite_input.setText(invitation)
    assert dialog._join_error.accessibleDescription() == ""
    dialog._join_button_primary.click()
    assert dialog.selected_role == "join"
    assert dialog.result() == dialog.DialogCode.Accepted
    assert invitation not in (tmp_path / "settings.json").read_text()


def test_art_room_menu_hides_music_setup_and_keeps_conversation_and_leave(qapp):
    strip = SessionStrip(
        mode_entries=[("music_jam", "Music")],
        initial_mode_key="music_jam",
        initial_title="Making",
    )
    try:
        strip.set_creator_profile(get_creator_profile_by_key("art"))
        strip.set_tools_enabled(False)
        strip.set_tools_enabled(True)
        strip.set_audio_state("Leave Jam")
        strip.show()
        qapp.processEvents()
        for action in (
            strip._ready_action,
            strip._practice_action,
            strip._audio_settings_action,
            strip._diagnostics_action,
            strip._jamulus_updates_action,
        ):
            assert not action.isVisible()
            assert not action.isEnabled()
        assert strip._audio_button.isVisibleTo(strip)
        assert strip._audio_button.accessibleName() == "Leave Room"
        assert "Jamulus" not in strip._audio_button.accessibleDescription()
        assert strip._video_button.isVisibleTo(strip)
        visible = [
            action.text()
            for action in strip._tools_button.menu().actions()
            if action.isVisible() and not action.isSeparator()
        ]
        assert "Conversation" in visible
        assert "Paint along…" in visible
        assert not any(
            "Sound" in text or "Audio" in text or "Check" in text for text in visible
        )
        assert "sound settings" not in strip._tools_button.accessibleDescription()
        strip.set_creator_profile(get_creator_profile_by_key("music"))
        assert strip._audio_settings_action.isVisible()
        assert strip._diagnostics_action.isVisible()
        assert strip._ready_action.isEnabled()
    finally:
        strip.close()
        strip.deleteLater()


@pytest.mark.parametrize("mode", ["talkback", "video_only", "audience_bridge"])
@pytest.mark.parametrize("service", ["", "Webex", "Zoom"])
def test_art_conversation_stays_optional_without_music_mute_controls(
    qapp, mode, service
):
    panel = WebexEmbed()
    calls = []
    panel.open_meeting_requested.connect(lambda: calls.append("open"))
    panel.bring_forward_requested.connect(lambda: calls.append("focus"))
    panel.mute_in_webex_requested.connect(lambda: calls.append("mute"))
    try:
        panel.set_creator_profile(get_creator_profile_by_key("art"))
        panel.set_audio_mode(mode)
        panel.set_service_label(service)
        panel.set_meeting_configured(bool(service))
        panel.show()
        qapp.processEvents()
        visible = harvest_spoken_page(panel)
        assert "share a demonstration" in visible
        assert "if you like" in visible
        assert "own tools" in visible
        assert "separate silent local video" in visible
        for phrase in ("muted while", "audio interface", "jamulus", "to mute"):
            assert phrase not in visible
        assert panel.mute_button().isHidden()
        assert not panel.mute_button().isEnabled()
        assert panel.fallback_button().isVisibleTo(panel)
        assert calls == []
        panel.set_creator_profile(get_creator_profile_by_key("music"))
        assert not panel.mute_button().isHidden()
        assert calls == []
    finally:
        panel.close()
        panel.deleteLater()


@pytest.mark.parametrize(
    "action",
    [
        "_on_ready_check",
        "_open_band_check",
        "_on_practice_requested",
        "_bring_jamulus_forward",
        "_on_mute_all",
    ],
)
def test_art_rejects_audio_actions_even_when_a_stale_shortcut_calls_them(qapp, action):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from webjam_qt.controllers.application_controller import ApplicationController

    controller = SimpleNamespace(
        _active_creator_profile_key="art",
        window=MagicMock(),
        bridge=MagicMock(),
        audio=MagicMock(),
        jamulus=MagicMock(),
        _is_jamulus_running=MagicMock(return_value=True),
        _open_band_check=MagicMock(),
        _shutdown_cleanup_blocks_action=MagicMock(return_value=False),
        participants={1: SimpleNamespace(muted=False)},
        _jamulus_connected=True,
    )
    getattr(ApplicationController, action)(controller)
    assert not controller.window.mock_calls
    assert not controller.bridge.mock_calls
    assert not controller.audio.mock_calls
    assert not controller.jamulus.mock_calls
    controller._open_band_check.assert_not_called()
    assert not controller.participants[1].muted


@pytest.mark.parametrize(
    "source,visible,spoken",
    [
        ("Try End Session", "Try End", "Try End Room"),
        ("Try Leave Jam", "Try Leave", "Try Leave Room"),
    ],
)
def test_art_cleanup_retry_remains_compact_and_accessible(
    qapp, source, visible, spoken
):
    strip = SessionStrip(
        mode_entries=[("music_jam", "Music")],
        initial_mode_key="music_jam",
        initial_title="Making",
    )
    try:
        strip.set_creator_profile(get_creator_profile_by_key("art"))
        strip.set_audio_state(source)
        assert strip._audio_button.text() == spoken
        strip.set_compact_control_labels(True)
        strip.show()
        qapp.processEvents()
        assert strip._audio_button.isVisibleTo(strip)
        assert strip._audio_button.text() == visible
        assert strip._audio_button.accessibleName() == spoken
    finally:
        strip.close()
        strip.deleteLater()
