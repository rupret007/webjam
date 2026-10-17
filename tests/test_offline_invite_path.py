"""Offline paste → validate → join for v1 and v2 invitations.

Covers the Art-door regressions fixed by #263 and #268: a parsed non-peer
(v1) BandInvite must start the existing Music guest journey instead of
reopening Join. Peer-enabled (v2) invitations still discover the host
profile over the LAN path. Native audio is intercepted; these cases do not
claim a physical connection.
"""

from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid

from core.network_invite import BandInvite, create_invite_link, parse_invite_link
from core.session_transfer import SessionCredentials
from core.settings import AppSettings, load_settings, save_settings
from tests.test_art_room_controller import arm_lan
from webjam_qt.app import _apply_launch_session_context
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def apps(qapp, tmp_path):
    made = []

    def create(settings, *, invite=None):
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Local room",
        )
        app = ApplicationController(window, settings=settings, session_invite=invite)
        made.append(app)
        return app

    yield create
    for app in reversed(made):
        assert app.shutdown()
        window = app.window
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        assert not isValid(window)
    qapp.processEvents()


def _settings(tmp_path, profile: str) -> AppSettings:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "takes"),
        musician_name="Artist",
        host_server_enabled=False,
        last_creator_profile_key=profile,
        last_creator_start_key="paint_along" if profile == "art" else "",
    )
    save_settings(settings)
    return settings


def v1_link() -> str:
    return create_invite_link("192.168.1.42", port=22130, session_name="Band Rehearsal")


def v2_link() -> str:
    credentials = SessionCredentials.create()
    return create_invite_link(
        "192.168.1.42",
        port=22130,
        session_name="Saturday Drawing",
        session_id=credentials.session_id,
        peer_port=22125,
        invite_token=credentials.invite_token,
    )


def _paste_validate_join(qapp, settings, link: str, *, profile: str) -> LaunchDialog:
    """Drive the live door: Join, paste, then validate with the Join button."""

    door = LaunchDialog(settings)
    door._menu_bar.setNativeMenuBar(False)
    door.show()
    qapp.processEvents()
    if profile == "art":
        door._art_profile_card.click()
    else:
        door._music_profile_card.click()
    qapp.processEvents()
    assert door.selected_creator_profile_key == profile
    door._join_button.click()
    qapp.processEvents()
    assert door._pages.currentWidget() is door._join_page
    door._invite_input.setText(link)
    qapp.processEvents()
    assert "Invitation pasted" in door._join_status.text()
    door._join_button_primary.click()
    qapp.processEvents()
    assert door.result() == QDialog.DialogCode.Accepted
    assert door.selected_role == "join"
    assert door._invite_input.text() == ""
    assert isinstance(door.band_invite, BandInvite)
    return door


def _intercept_native(app, monkeypatch) -> None:
    monkeypatch.setattr(app, "_launch_native_jamulus_for_startup", mock.Mock())
    monkeypatch.setattr(app, "_start_hosted_server_for_startup", mock.Mock())
    monkeypatch.setattr(app, "_configure_guest_peer", mock.Mock())
    monkeypatch.setattr(
        app,
        "_paste_new_invitation",
        mock.Mock(side_effect=AssertionError("Join must not reopen after a validated invite")),
    )


def _start_from_door(apps, qapp, settings, door):
    app = apps(load_settings(settings.config_file), invite=door.band_invite)
    _apply_launch_session_context(app, door)
    return app


def _assert_music_guest(app, invite: BandInvite) -> None:
    assert app.creator_profile.key == "music"
    assert app._startup_attempt["role"] == "guest"
    assert app._startup_attempt["phase"] == "launching_client"
    app._launch_native_jamulus_for_startup.assert_called_once_with(app._startup_generation)
    app._start_hosted_server_for_startup.assert_not_called()
    app._paste_new_invitation.assert_not_called()
    app._room_participant.start_lan_guest.assert_not_called()
    assert not app._room_participant.probing
    assert not app.settings.host_server_enabled
    assert (app.settings.jamulus_server, app.settings.jamulus_port) == (invite.host, invite.port)
    assert not app._jamulus_connected
    assert app.guest_peer is None
    app._configure_guest_peer.assert_not_called()


@pytest.mark.parametrize("profile", ["art", "music"])
def test_paste_validate_join_v1_starts_music_guest(apps, qapp, tmp_path, monkeypatch, profile):
    settings = _settings(tmp_path, profile)
    door = _paste_validate_join(qapp, settings, v1_link(), profile=profile)
    try:
        invite = door.band_invite
        assert not invite.peer_enabled
        assert invite.version == 1
        app = _start_from_door(apps, qapp, settings, door)
    finally:
        door.close()
        door.deleteLater()
        qapp.processEvents()

    _intercept_native(app, monkeypatch)
    monkeypatch.setattr(app._room_participant, "start_lan_guest", mock.Mock())
    assert app.begin_startup_journey()
    _assert_music_guest(app, invite)
    assert app.window.session_strip.current_title() == "Band Rehearsal"
    assert app.settings.last_creator_profile_key == profile
    assert load_settings(settings.config_file).last_creator_profile_key == profile
    assert app._stop_session_peer(clear_invite=True)
    assert app.creator_profile.key == profile
    assert app._guest_invite is None


@pytest.mark.parametrize("profile", ["art", "music"])
def test_paste_validate_join_v2_discovers_host_without_music_audio(
    apps, qapp, tmp_path, monkeypatch, profile,
):
    settings = _settings(tmp_path, profile)
    door = _paste_validate_join(qapp, settings, v2_link(), profile=profile)
    try:
        invite = door.band_invite
        assert invite.peer_enabled
        assert invite.version == 2
        app = _start_from_door(apps, qapp, settings, door)
    finally:
        door.close()
        door.deleteLater()
        qapp.processEvents()

    _intercept_native(app, monkeypatch)
    arm_lan(monkeypatch, invite, profile="art")
    probe = mock.Mock(wraps=app._room_participant.start_lan_guest)
    monkeypatch.setattr(app._room_participant, "start_lan_guest", probe)
    assert app.begin_startup_journey()
    probe.assert_called_once_with(invite)
    assert app._room_participant.probing
    assert app.creator_profile.key == profile
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app._paste_new_invitation.assert_not_called()
    assert app.settings.last_creator_profile_key == profile
    assert load_settings(settings.config_file).last_creator_profile_key == profile


def test_running_art_window_v1_paste_validate_join_does_not_loop(apps, qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path, "art")
    app = apps(settings)
    _intercept_native(app, monkeypatch)
    monkeypatch.setattr(app._room_participant, "start_lan_guest", mock.Mock())
    opened: list[LaunchDialog] = []
    link = v1_link()

    def paste_validate_join(dialog):
        opened.append(dialog)
        assert len(opened) == 1, "Join reopened after accepting the same Music invite"
        assert dialog.selected_creator_profile_key == "art"
        dialog._invite_input.setText(link)
        qapp.processEvents()
        dialog._join_button_primary.click()
        qapp.processEvents()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert isinstance(dialog.band_invite, BandInvite)
        assert not dialog.band_invite.peer_enabled
        return dialog.result()

    monkeypatch.setattr(LaunchDialog, "exec", paste_validate_join)
    ApplicationController._paste_new_invitation(app)
    assert len(opened) == 1
    _assert_music_guest(app, app._guest_invite)
    assert app.settings.last_creator_profile_key == "art"
    assert load_settings(settings.config_file).last_creator_profile_key == "art"
    assert app._stop_session_peer(clear_invite=True)
    assert app.creator_profile.key == "art"


def test_running_art_window_v2_paste_validate_join_probes_host(apps, qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path, "art")
    app = apps(settings)
    _intercept_native(app, monkeypatch)
    opened: list[LaunchDialog] = []
    link = v2_link()
    accepted: list[BandInvite] = []

    def paste_validate_join(dialog):
        opened.append(dialog)
        assert len(opened) == 1, "Join reopened after accepting a valid v2 invite"
        dialog._invite_input.setText(link)
        qapp.processEvents()
        dialog._join_button_primary.click()
        qapp.processEvents()
        assert dialog.result() == QDialog.DialogCode.Accepted
        accepted.append(dialog.band_invite)
        return dialog.result()

    monkeypatch.setattr(LaunchDialog, "exec", paste_validate_join)
    probe = mock.Mock(return_value=True)
    monkeypatch.setattr(app._room_participant, "start_lan_guest", probe)
    ApplicationController._paste_new_invitation(app)
    assert len(opened) == 1
    invite = accepted[0]
    assert invite.peer_enabled
    probe.assert_called_once_with(invite)
    assert app._room_participant.probing
    assert app.creator_profile.key == "art"
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app._paste_new_invitation.assert_not_called()
    assert app.settings.last_creator_profile_key == "art"


def test_v1_invite_does_not_override_podcast_voice(apps, tmp_path, monkeypatch):
    """A v1 Music invite must not steal Podcast's local profile.

    The Art-door borrow is only for Art's paste-loop. Podcast (and other
    local profiles) keep their route while still starting the existing
    Music guest audio path.
    """

    settings = _settings(tmp_path, "podcast_voice")
    invite = parse_invite_link(v1_link())
    assert not invite.peer_enabled
    app = apps(settings, invite=invite)
    _intercept_native(app, monkeypatch)
    monkeypatch.setattr(app._room_participant, "start_lan_guest", mock.Mock())
    assert app.creator_profile.key == "podcast_voice"
    assert not app._creator_profile_host_owned
    assert app.begin_startup_journey()
    assert app.creator_profile.key == "podcast_voice"
    assert app._startup_attempt["role"] == "guest"
    assert app._startup_attempt["phase"] == "launching_client"
    app._launch_native_jamulus_for_startup.assert_called_once_with(
        app._startup_generation
    )
    app._start_hosted_server_for_startup.assert_not_called()
    app._paste_new_invitation.assert_not_called()
    app._room_participant.start_lan_guest.assert_not_called()
    assert app.settings.last_creator_profile_key == "podcast_voice"
    assert load_settings(settings.config_file).last_creator_profile_key == (
        "podcast_voice"
    )
