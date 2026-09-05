"""A cold Art invitation borrows room context and preserves personal work."""
from __future__ import annotations

import json
import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from core.network_invite import create_invite_link
from core.remote_invitation import issue_remote_invitation
from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from core.settings import AppSettings, load_settings, save_settings
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_room_controller import (
    RoomBackend,
    arm_lan,
    drain,
    qapp as _qapp_fixture,
)
from webjam_qt.app import _apply_launch_session_context
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog

qapp = _qapp_fixture
PROFILES = ("music", "art", "podcast_voice", "review_rehearsal")


@pytest.fixture
def context(qapp, tmp_path, monkeypatch):
    home = tmp_path / "personal-work"
    home.mkdir()
    monkeypatch.setattr(
        "webjam_qt.controllers.session_persistence._persistence_home", lambda: home,
    )
    monkeypatch.setattr(
        "webjam_qt.windows.launch_dialog._windows_jamulus_installer", lambda settings: None,
    )
    # Report unexpected cleanup errors as assertions instead of waiting in a
    # modal native dialog during a failed test or fixture teardown.
    information = mock.Mock(return_value=QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", information)
    made = []

    def create(settings, *, invite=None, remote=None):
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Local room",
        )
        app = ApplicationController(
            window, settings=settings, session_invite=invite, remote_invitation=remote,
        )
        made.append(app)
        return app

    def retire(app):
        assert app.shutdown()
        app.window.deleteLater()
        made.remove(app)
        qapp.processEvents()

    yield create, retire, home
    for app in reversed(made):
        if app.audio.stopping:
            drain(qapp, lambda: not app.audio.stopping)
        assert app.shutdown()
        app.window.deleteLater()
    qapp.processEvents()
    information.assert_not_called()


def settings_for(tmp_path, profile):
    return AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "takes"),
        musician_name="Artist",
        host_server_enabled=False,
        last_creator_profile_key=profile,
        last_creator_start_key="paint_along" if profile == "art" else "",
    )


def full_invitation(transport):
    if transport == "native":
        return issue_remote_invitation(
            "reference-local", allowed_profiles={"reference-local"},
            host_spki_sha256=b"p" * 32,
        ).private_link.reveal_for_clipboard()
    return create_invite_link(
        "192.168.1.42", session_name="Saturday Drawing",
        session_id="11111111-1111-1111-1111-111111111111",
        peer_port=42001, invite_token="a" * 64,
    )


def cold_join(settings, transport):
    door = LaunchDialog(load_settings(settings.config_file))
    door.show_join()
    assert door.accept_invite(full_invitation(transport))
    assert door.selected_role == "join"
    assert door._invite_input.text() == ""
    return door


def enter_art(qapp, monkeypatch, app, transport):
    app._launch_native_jamulus_for_startup = mock.Mock()
    app._start_hosted_server_for_startup = mock.Mock()
    if transport == "lan":
        arm_lan(monkeypatch, app._guest_invite)
    else:
        monkeypatch.setattr(
            "services.native_remote_transport.NativeGuestTransportBackend", RoomBackend,
        )
    assert app.begin_startup_journey()
    if transport == "lan":
        app._room_participant.lan_guest.poll_once()
    else:
        drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
        RoomBackend.instances[-1].emit(RoomState(1, "art", "talk_and_make"))
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    assert app.guest_peer is None


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("transport", ("lan", "native"))
def test_cold_join_art_leave_and_reopen_preserve_personal_workspace(
    context, qapp, tmp_path, monkeypatch, profile, transport,
):
    create, retire, home = context
    settings = settings_for(tmp_path, profile)
    save_settings(settings)
    title, notes = f"My {profile} project", f"My private {profile} notes"
    original = create(settings)
    original._set_session_entry_title(title, borrowed=False)
    original.window.session_canvas.set_notes(notes)
    assert original._save_notes()
    retire(original)
    original_metadata = (home / ".webjam_session.json").read_text()

    door = cold_join(settings, transport)
    try:
        joined_settings = load_settings(settings.config_file)
        assert joined_settings.last_creator_profile_key == profile
        assert joined_settings.last_creator_start_key == settings.last_creator_start_key
        app = create(
            joined_settings, invite=door.band_invite, remote=door.take_remote_invitation(),
        )
        _apply_launch_session_context(app, door)
        assert app.window.session_strip.current_title() == (
            "Saturday Drawing" if transport == "lan" else "Room"
        )
        assert (home / ".webjam_session.json").read_text() == original_metadata
    finally:
        door.deleteLater()

    enter_art(qapp, monkeypatch, app, transport)
    assert app.creator_profile.key == "art"
    assert app.settings.last_creator_profile_key == profile
    app.audio._begin_session_stop(False, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app.creator_profile.key == profile
    assert app.window.session_strip.current_title() == title
    assert app.window.session_canvas.current_notes() == notes
    retire(app)

    reopened = create(load_settings(settings.config_file))
    assert reopened.creator_profile.key == profile
    assert reopened.window.session_strip.current_title() == title
    assert reopened.window.session_canvas.current_notes() == notes
    assert reopened.settings.last_creator_start_key == settings.last_creator_start_key


@pytest.mark.parametrize("choice", ("art", "podcast_voice", "activity", "music"))
def test_join_remembers_an_explicit_workspace_or_activity_choice(
    context, qapp, tmp_path, choice,
):
    saved_profile = "art" if choice == "activity" else "review_rehearsal"
    settings = settings_for(tmp_path, saved_profile)
    settings.last_creator_start_key = "talk_and_make" if choice == "activity" else ""
    save_settings(settings)
    door = LaunchDialog(settings)
    try:
        if choice in {"art", "music"}:
            door._profile_cards[choice].click()
        elif choice == "podcast_voice":
            door._workspace_actions[choice].trigger()
        else:
            next(card for card in door._start_cards["art"]
                 if card.start_key == "paint_along").click()
        door.show_join()
        assert door.accept_invite(full_invitation("lan"))
        saved = load_settings(settings.config_file)
        expected_profile = "art" if choice == "activity" else choice
        assert saved.last_creator_profile_key == expected_profile
        if choice == "activity":
            assert saved.last_creator_start_key == "paint_along"
    finally:
        door.deleteLater()


@pytest.mark.parametrize("profile", ("art", "music"))
def test_host_keeps_its_personal_title_and_persists_the_selected_profile(
    context, qapp, tmp_path, monkeypatch, profile,
):
    create, _retire, home = context
    settings = settings_for(tmp_path, "review_rehearsal")
    save_settings(settings)
    monkeypatch.setattr("webjam_qt.windows.launch_dialog.sys.platform", "darwin")
    door = LaunchDialog(settings)
    try:
        door._profile_cards[profile].click()
        door._host_button.click()
        assert door.selected_role == "host"
        saved = load_settings(settings.config_file)
        assert saved.last_creator_profile_key == profile
        app = create(saved)
        app._set_session_entry_title("My own room", borrowed=False)
        before = (home / ".webjam_session.json").read_text()
        _apply_launch_session_context(app, door)
        assert app.window.session_strip.current_title() == "My own room"
        assert (home / ".webjam_session.json").read_text() == before
    finally:
        door.deleteLater()


def test_an_explicit_title_edit_in_joined_art_belongs_to_art(
    context, qapp, tmp_path, monkeypatch,
):
    create, retire, home = context
    settings = settings_for(tmp_path, "music")
    save_settings(settings)
    original = create(settings)
    original._set_session_entry_title("My Music project", borrowed=False)
    retire(original)
    door = cold_join(settings, "lan")
    try:
        app = create(load_settings(settings.config_file), invite=door.band_invite)
        _apply_launch_session_context(app, door)
    finally:
        door.deleteLater()
    enter_art(qapp, monkeypatch, app, "lan")
    app.window.session_strip._title_input.setText("My Art sketchbook")
    app.window.session_strip._title_input.editingFinished.emit()
    records = json.loads((home / ".webjam_session.json").read_text())["profiles"]
    assert records["music"]["title"] == "My Music project"
    assert records["art"]["title"] == "My Art sketchbook"
    app.audio._begin_session_stop(False, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    assert app.window.session_strip.current_title() == "My Music project"
    retire(app)
    art_settings = load_settings(settings.config_file)
    art_settings.last_creator_profile_key = "art"
    reopened = create(art_settings)
    assert reopened.window.session_strip.current_title() == "My Art sketchbook"


def test_warm_native_join_borrows_a_neutral_title_then_restores_personal_work(
    context, qapp, tmp_path, monkeypatch,
):
    create, _retire, home = context
    settings = settings_for(tmp_path, "music")
    save_settings(settings)
    app = create(settings)
    app._set_session_entry_title("My Music room", borrowed=False)
    app.window.session_canvas.set_notes("My Music notes")
    assert app._save_notes()
    before = (home / ".webjam_session.json").read_text()
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", RoomBackend,
    )
    invitation = issue_remote_invitation(
        "reference-local", allowed_profiles={"reference-local"},
        host_spki_sha256=b"p" * 32,
    ).invitation
    assert app.accept_invitation(invitation)
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    assert app.window.session_strip.current_title() == "Room"
    assert (home / ".webjam_session.json").read_text() == before
    RoomBackend.instances[-1].emit(RoomState(1, "art", "talk_and_make"))
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
    assert app.creator_profile.key == "art"
    assert app.window.session_strip.current_title() == "Room"
    app.audio._begin_session_stop(False, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app.creator_profile.key == "music"
    assert app.window.session_strip.current_title() == "My Music room"
    assert app.window.session_canvas.current_notes() == "My Music notes"


def waiting_for_host_profile(context, qapp, tmp_path, monkeypatch, entry):
    create, retire, home = context
    settings = settings_for(tmp_path, "music")
    save_settings(settings)
    app = create(settings)
    app._set_session_entry_title("My personal room", borrowed=False)
    app.window.session_canvas.set_notes("My personal notes")
    assert app._save_notes()
    transport = "lan" if entry == "cold_lan" else "native"
    if transport == "native":
        monkeypatch.setattr(
            "services.native_remote_transport.NativeGuestTransportBackend", RoomBackend,
        )
    if entry == "warm_native":
        invitation = issue_remote_invitation(
            "reference-local", allowed_profiles={"reference-local"},
            host_spki_sha256=b"p" * 32,
        ).invitation
        assert app.accept_invitation(invitation)
    else:
        retire(app)
        door = cold_join(settings, transport)
        try:
            app = create(
                load_settings(settings.config_file),
                invite=door.band_invite, remote=door.take_remote_invitation(),
            )
            _apply_launch_session_context(app, door)
        finally:
            door.deleteLater()
        if transport == "lan":
            arm_lan(monkeypatch, app._guest_invite)
        assert app.begin_startup_journey()
    if transport == "native":
        drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    # No LAN poll or native RoomState has delivered the host's profile yet.
    assert app._room_participant.probing
    assert not app._creator_profile_host_owned
    assert app.creator_profile.key == "music"
    assert app._persistence._borrowed_title == (
        "Saturday Drawing" if transport == "lan" else "Room"
    )
    return app, settings, home


@pytest.mark.parametrize("entry", ("cold_lan", "cold_native", "warm_native"))
@pytest.mark.parametrize("edit_title", (False, True))
def test_leave_before_host_profile_restores_personal_context_and_keeps_edits(
    context, qapp, tmp_path, monkeypatch, entry, edit_title,
):
    create, retire, _home = context
    app, settings, home = waiting_for_host_profile(
        context, qapp, tmp_path, monkeypatch, entry,
    )
    expected_title = "My renamed room" if edit_title else "My personal room"
    if edit_title:
        app.window.session_strip._title_input.setText(expected_title)
        app.window.session_strip._title_input.editingFinished.emit()
        assert app._persistence._borrowed_title is None
    app.audio._begin_session_stop(False, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app.creator_profile.key == "music"
    assert app.settings.last_creator_profile_key == "music"
    assert app.window.session_strip.current_title() == expected_title
    assert app.window.session_canvas.current_notes() == "My personal notes"
    assert app._persistence._borrowed_title is None
    retire(app)
    reopened = create(load_settings(settings.config_file))
    assert reopened.window.session_strip.current_title() == expected_title
    assert reopened.window.session_canvas.current_notes() == "My personal notes"
    records = json.loads((home / ".webjam_session.json").read_text())["profiles"]
    assert records["music"]["title"] == expected_title


def test_failed_preprofile_leave_keeps_borrowed_context_until_cleanup_succeeds(
    context, qapp, tmp_path, monkeypatch,
):
    app, _settings, home = waiting_for_host_profile(
        context, qapp, tmp_path, monkeypatch, "cold_lan",
    )
    owner = app._room_participant.lan_guest
    owner.stop = mock.Mock(wraps=owner.stop, side_effect=[False, mock.DEFAULT])
    before = (home / ".webjam_session.json").read_text()
    app.audio._begin_session_stop(False, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    assert app.audio.cleanup_retry_required
    assert app._room_participant.lan_guest is owner
    assert app._guest_invite is not None
    assert app._persistence._borrowed_title == "Saturday Drawing"
    assert app.window.session_strip.current_title() == "Saturday Drawing"
    assert app.window.session_canvas.current_notes() == "My personal notes"
    assert (home / ".webjam_session.json").read_text() == before
    app.audio.retry_stop()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert owner.stop.call_count == 2
    assert app._room_participant.lan_guest is None
    assert app._guest_invite is None
    assert app._persistence._borrowed_title is None
    assert app.window.session_strip.current_title() == "My personal room"
    assert app.window.session_canvas.current_notes() == "My personal notes"
