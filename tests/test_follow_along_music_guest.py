"""The real cold-joined Music owner keeps deliberate practice through updates."""
from unittest.mock import Mock

import pytest

from tests.test_composed_music_invitation_conversation import (
    ROOM, callback_errors as callback_errors, copied_room as copied_room, qapp as qapp,
)


@pytest.mark.parametrize("setup_phase", [
    "launching_client", "native_sound_setup", "verifying_music", "confirm_sound",
])
def test_music_guest_updates_preserve_choices_and_video_practice(copied_room, qapp, monkeypatch, setup_phase):
    def inspect(pair):
        app = pair.app
        app.window.show()
        qapp.processEvents()
        assert not app._reference_track_is_host()
        assert app._startup_attempt["phase"] == "launching_client"
        app._startup_attempt["phase"] = setup_phase
        startup_title = app._startup_guidance_override(app._startup_attempt, "music").title
        app.follow_along.show_music_choices()
        choice = app.follow_along._choice
        app._render_guest_peer_state()
        assert app.follow_along._choice is choice and choice.isVisible()
        choice.buttons["lesson"].click()
        qapp.processEvents()
        attempt = app._startup_attempt
        phase = attempt["phase"]
        launches = pair.launch.call_count
        assert phase == setup_phase
        assert app.window.session_hud._status.text() == "Video practice in your meeting"
        assert "Jamulus is not required" in app.window.session_hud._detail.text()
        assert not app.window.session_hud._action.isVisibleTo(app.window)
        panel = app.follow_along.panel
        assert panel.hosting is False and panel.isVisibleTo(app.window)
        assert app.window.webex_embed._mute_btn.isHidden()
        assert panel.open_button.isHidden() and panel.choose_button.isHidden()
        app._render_guest_peer_state()
        app._render_startup_journey()
        assert app.window.session_hud._status.text() == "Video practice in your meeting"
        assert app._startup_attempt is attempt and attempt["phase"] == phase
        assert pair.launch.call_count == launches
        assert not app._jamulus_connected and not app.audio.connected
        assert panel.hosting is False and panel.isVisibleTo(app.window)
        assert "Take turns" in app.window.webex_embed._mode_label.text()
        app.window.side_rail.trigger("canvas")
        assert app.window.session_hud._status.text() == startup_title
        app._render_guest_peer_state()
        app.window.session_strip._video_button.click()
        qapp.processEvents()
        assert panel.hosting is False and panel.isVisibleTo(app.window)
        assert app.window.session_hud._status.text() == "Video practice in your meeting"
        assert panel.open_button.isHidden() and panel.choose_button.isHidden()
        assert app.window.webex_embed._mute_btn.isHidden()
        mixer = Mock()
        monkeypatch.setattr(app, "_bring_jamulus_forward", mixer)
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["ensemble"].click()
        mixer.assert_called_once()
        assert app.window.session_hud._status.text() == startup_title
        assert not app.window.webex_embed._mute_btn.isHidden()
        assert app._stop_session_peer(clear_invite=True)
        qapp.processEvents()
        assert panel.isHidden()
    copied_room(ROOM, inspect)


@pytest.mark.parametrize("change", ["role", "generation"])
def test_stale_practice_cannot_replace_current_audio_setup(copied_room, qapp, monkeypatch, change):
    def inspect(pair):
        app = pair.app
        app.window.show()
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["lesson"].click()
        assert app.window.session_hud._status.text() == "Video practice in your meeting"
        if change == "role":
            monkeypatch.setattr(app, "_reference_track_is_host", lambda: True)
        else:
            app.follow_along.panel.generation += 1
        app._update_session_hud()
        assert app.window.session_hud._status.text() == "Set up your sound in Jamulus"
        assert not app.follow_along.music_practice_visible()
    copied_room(ROOM, inspect)


@pytest.mark.parametrize("phase", ["failed", "cancelling"])
def test_video_practice_keeps_startup_recovery_visible(copied_room, qapp, phase):
    def inspect(pair):
        app = pair.app
        app.window.show()
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["lesson"].click()
        assert app.window.session_hud._status.text() == "Video practice in your meeting"
        app._startup_attempt["phase"] = phase
        app._render_startup_journey()
        assert app.window.session_hud._status.text() != "Video practice in your meeting"
        assert app._startup_attempt["phase"] == phase
        if phase == "cancelling":
            assert not app.window.session_strip._audio_button.isEnabled()
        else:
            assert app.window.session_hud._action.isVisibleTo(app.window)
    copied_room(ROOM, inspect)


def test_retiring_practice_cannot_overwrite_shutdown_cleanup(copied_room, qapp, monkeypatch):
    def inspect(pair):
        app = pair.app
        app.window.show()
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["lesson"].click()
        app._shutdown_cleanup_pending = True
        try:
            app._render_shutdown_cleanup_pending()
            hud = app.window.session_hud
            before = (hud._status.text(), hud._detail.text(),
                      app.window.session_strip._audio_button.isEnabled())
            renderer = Mock(wraps=app._render_startup_journey)
            monkeypatch.setattr(app, "_render_startup_journey", renderer)
            app.follow_along.retire()
            renderer.assert_not_called()
            assert (hud._status.text(), hud._detail.text(),
                    app.window.session_strip._audio_button.isEnabled()) == before
        finally:
            app._shutdown_cleanup_pending = False
    copied_room(ROOM, inspect)


def test_failed_music_guest_can_share_and_open_its_meeting_without_restarting_audio(copied_room, qapp, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from webjam_qt.windows.launch_dialog import LaunchDialog

    def inspect(pair):
        app = pair.app
        app.window.show()
        app._fail_startup_journey(app._startup_attempt["generation"], "component_open_failed")
        app.window.session_strip._play_along_button.click()
        app.follow_along._choice.buttons["lesson"].click()
        qapp.processEvents()
        panel = app.window.webex_embed
        launches = pair.launch.call_count
        assert panel.lesson_handoff.hosting is False
        panel._copy_link_btn.click()
        QApplication.clipboard().setText.assert_called_with(ROOM)
        assert "not a WebJam room invitation" in panel._sound_tips.text()
        opener = Mock(return_value=True)
        monkeypatch.setattr(app.bridge, "launch_webex", opener)
        panel._fallback_btn.click()
        opener.assert_called_once_with(manual=True, meeting_url=ROOM)
        assert app._startup_attempt["phase"] == "failed"
        assert pair.launch.call_count == launches
        assert not app.audio.connected
        direct = LaunchDialog(app.settings)
        try:
            direct.show_join()
            assert not direct.accept_invite(ROOM)
        finally:
            direct.close()
            direct.deleteLater()
    copied_room(ROOM, inspect)
