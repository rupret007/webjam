"""The real cold-joined Music owner keeps deliberate practice through updates."""
from unittest.mock import Mock

from tests.test_composed_music_invitation_conversation import (
    ROOM, callback_errors as callback_errors, copied_room as copied_room, qapp as qapp,
)


def test_music_guest_updates_preserve_choices_and_video_practice(copied_room, qapp, monkeypatch):
    def inspect(pair):
        app = pair.app
        app.window.show()
        qapp.processEvents()
        assert not app._reference_track_is_host()
        app.follow_along.show_music_choices()
        choice = app.follow_along._choice
        app._render_guest_peer_state()
        assert app.follow_along._choice is choice and choice.isVisible()
        choice.buttons["lesson"].click()
        qapp.processEvents()
        panel = app.follow_along.panel
        assert panel.hosting is False and panel.isVisibleTo(app.window)
        assert app.window.webex_embed._mute_btn.isHidden()
        assert panel.open_button.isHidden() and panel.choose_button.isHidden()
        app._render_guest_peer_state()
        assert panel.hosting is False and panel.isVisibleTo(app.window)
        assert "Take turns" in app.window.webex_embed._mode_label.text()
        mixer = Mock()
        monkeypatch.setattr(app, "_bring_jamulus_forward", mixer)
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["ensemble"].click()
        mixer.assert_called_once()
        assert not app.window.webex_embed._mute_btn.isHidden()
        assert app._stop_session_peer(clear_invite=True)
        qapp.processEvents()
        assert panel.isHidden()
    copied_room(ROOM, inspect)
