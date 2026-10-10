"""A legacy Music invite leaves the Art door once, without a LAN probe."""
from unittest import mock

import pytest
from PySide6.QtWidgets import QDialog

from core.network_invite import create_invite_link
from core.settings import load_settings, save_settings
from tests.test_art_cold_invitation_context import (
    context as _context_fixture,
    qapp as _qapp_fixture,
    settings_for,
)
from tests.test_art_room_controller import invitation
from webjam_qt.app import _apply_launch_session_context
from webjam_qt.invitation_ingress import InvitationSource, parse_invitation_at_ingress
from webjam_qt.windows.launch_dialog import LaunchDialog, apply_join_invite

context = _context_fixture
qapp = _qapp_fixture


def legacy_link():
    return create_invite_link("192.168.1.42", port=22130, session_name="Band Rehearsal")


def block_external_start(app, monkeypatch):
    for name in ("_launch_native_jamulus_for_startup", "_start_hosted_server_for_startup",
                 "_paste_new_invitation", "_configure_guest_peer"):
        monkeypatch.setattr(app, name, mock.Mock())
    monkeypatch.setattr(app._room_participant, "start_lan_guest", mock.Mock())
    monkeypatch.setattr(app._room_participant, "start_lan_host", mock.Mock())


def assert_music_guest(app):
    assert app.creator_profile.key == "music"
    assert app._startup_attempt["role"] == "guest"
    assert app._startup_attempt["phase"] == "launching_client"
    app._launch_native_jamulus_for_startup.assert_called_once_with(app._startup_generation)
    app._start_hosted_server_for_startup.assert_not_called()
    app._paste_new_invitation.assert_not_called()
    app._room_participant.start_lan_guest.assert_not_called()
    app._room_participant.start_lan_host.assert_not_called()
    assert not app._room_participant.probing
    assert not app.settings.host_server_enabled
    assert (app.settings.jamulus_server, app.settings.jamulus_port) == ("192.168.1.42", 22130)
    assert app.settings.last_creator_profile_key == "art"
    assert not app._local_originals_available()
    app._start_guest_peer_for_native_startup(app._startup_attempt)
    app._configure_guest_peer.assert_not_called()
    assert app.guest_peer is None


@pytest.mark.parametrize("entry", ("door", "argv"))
@pytest.mark.parametrize("start", ("talk_and_make", "paint_along"))
def test_cold_art_v1_invite_starts_music_guest(context, tmp_path, monkeypatch, entry, start):
    create, _retire, _home = context
    settings = settings_for(tmp_path, "art")
    settings.last_creator_start_key = start
    save_settings(settings)
    door = LaunchDialog(settings)
    try:
        door.show_join()
        if entry == "argv":
            invite = parse_invitation_at_ingress(legacy_link(), source=InvitationSource.ARGV)
            assert door.accept_invitation(invite)
        else:
            door._invite_input.setText(legacy_link())
            door._join_button_primary.click()
        assert door.result() == QDialog.DialogCode.Accepted
        app = create(load_settings(settings.config_file), invite=door.band_invite)
        _apply_launch_session_context(app, door)
    finally:
        door.deleteLater()
    block_external_start(app, monkeypatch)
    assert app.begin_startup_journey()
    assert_music_guest(app)
    assert app.window.session_strip.current_title() == "Band Rehearsal"
    assert load_settings(settings.config_file).last_creator_start_key == start
    assert app._stop_session_peer(clear_invite=True)
    assert app._guest_invite is None
    assert app.creator_profile.key == "art"


def test_recovery_join_accepts_one_v1_paste(context, tmp_path, monkeypatch):
    create, _retire, _home = context
    settings = settings_for(tmp_path, "art")
    save_settings(settings)
    app = create(settings)
    # Exercise the real recovery door's accept/save and controller handoff.
    opened = []

    def paste(dialog):
        opened.append(dialog)
        assert len(opened) == 1, "Join reopened after accepting the same Music invite"
        assert dialog.accept_invite(legacy_link())
        return dialog.result()

    monkeypatch.setattr(LaunchDialog, "exec", paste)
    block_external_start(app, monkeypatch)
    from webjam_qt.controllers.application_controller import ApplicationController
    ApplicationController._paste_new_invitation(app)
    assert len(opened) == 1
    assert_music_guest(app)


def test_v1_profile_veto_keeps_invite_for_retry(context, tmp_path, monkeypatch):
    create, _retire, _home = context
    settings = settings_for(tmp_path, "art")
    invite = parse_invitation_at_ingress(legacy_link(), source=InvitationSource.PASTE)
    apply_join_invite(settings, invite)
    app = create(settings, invite=invite)
    block_external_start(app, monkeypatch)
    monkeypatch.setattr(app.session_library, "profile_changing", mock.Mock(return_value=False))
    assert not app.begin_startup_journey()
    assert app.creator_profile.key == "art"
    assert app._guest_invite is invite
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._paste_new_invitation.assert_not_called()
    app.session_library.profile_changing.return_value = True
    assert app.begin_startup_journey()
    assert_music_guest(app)


def test_peer_invite_from_art_still_probes_host_profile(context, tmp_path, monkeypatch):
    create, _retire, _home = context
    settings = settings_for(tmp_path, "art")
    invite = invitation()
    apply_join_invite(settings, invite)
    app = create(settings, invite=invite)
    block_external_start(app, monkeypatch)
    app._room_participant.start_lan_guest.return_value = True
    assert app.begin_startup_journey()
    app._room_participant.start_lan_guest.assert_called_once_with(invite)
    assert app._room_participant.probing
    assert app.creator_profile.key == "art"
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app._paste_new_invitation.assert_not_called()
