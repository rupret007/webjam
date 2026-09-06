"""Real ApplicationController Art exit actions retain the completed room's role."""
from __future__ import annotations

import pytest

from core.session_conductor import ArtRoomState, SessionConductorPhase, SessionPrimaryAction
from core.settings import save_settings
from tests.test_art_lan_host_recovery import host as _host_fixture, qapp as _qapp_fixture
from tests.test_art_profile_guidance_journey import guest as _guest_fixture, _leave

qapp = _qapp_fixture
guest = _guest_fixture
host = _host_fixture


def _connected_guest(guest, *, transport="native", profile="art", hosting=True):
    pair = guest(transport=transport, profile=profile)
    pair.app.settings.host_server_enabled = hosting
    save_settings(pair.app.settings)
    pair.connect()
    return pair


@pytest.mark.parametrize("transport", ["lan", "native"])
@pytest.mark.parametrize("profile", ["art", "music"])
@pytest.mark.parametrize("hosting", [False, True])
def test_art_guest_leave_offers_fresh_invitation_across_saved_workspaces(
    guest, qapp, transport, profile, hosting,
):
    pair = _connected_guest(guest, transport=transport, profile=profile, hosting=hosting)
    app = pair.app
    _leave(pair, qapp)
    assert app._room_participant.state is ArtRoomState.NONE
    assert app._remote_invitation is None and app._guest_invite is None
    presentation = app._last_session_conductor
    assert presentation.phase is SessionConductorPhase.IDLE
    assert presentation.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    assert presentation.action_label == "Paste New Invite"
    assert app.window.session_hud._action_kind == "paste_invite"
    assert app.window.session_hud._action.text() == "Paste New Invite"
    assert app.window.session_hud._action.isVisibleTo(app.window)
    assert "Paste New Invite" in app.window.session_canvas._guidance_next.text()
    assert app._session_conductor_facts().creator_profile_key == profile


def _invitation_dialog(monkeypatch, *, invitation=None):
    """Exercise the real invitation-only modal and its accept/cancel boundary."""
    from PySide6.QtCore import QTimer
    from webjam_qt.windows.launch_dialog import LaunchDialog

    opened = []
    original_exec = LaunchDialog.exec

    def execute(dialog):
        failures = []

        def finish():
            try:
                opened.append(dialog._pages.currentWidget() is dialog._join_page)
                assert opened[-1], "The completed guest must return to the Join page"
                if invitation is None:
                    dialog.reject()
                else:
                    assert dialog.accept_invitation(invitation)
            except BaseException as error:
                failures.append(error)
                dialog.reject()

        QTimer.singleShot(0, finish)
        result = original_exec(dialog)
        if failures:
            raise failures[0]
        return result

    monkeypatch.setattr(LaunchDialog, "exec", execute)
    return opened


def _workspace(app):
    from pathlib import Path

    return (
        app.creator_profile.key,
        app.window.session_canvas.current_notes(),
        app.window.session_strip.current_title(),
        Path(app.settings.config_file).read_bytes(),
    )


def _assert_no_guest_launch(pair):
    app = pair.app
    assert app.host_peer.active is False
    assert app.guest_peer is None
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    pair.player_factory.assert_not_called()
    assert pair.launcher.joined == [] and pair.launcher.host_pages == 0


@pytest.mark.parametrize("transport", ["lan", "native"])
@pytest.mark.parametrize("entry", ["hud", "header", "primary"])
def test_cancelled_art_guest_rejoin_preserves_workspace_and_never_hosts(
    guest, qapp, monkeypatch, transport, entry,
):
    from unittest.mock import Mock

    pair = _connected_guest(guest, transport=transport, profile="art", hosting=True)
    app = pair.app
    _leave(pair, qapp)
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    before = _workspace(app)
    token, generation = app.session_conductor.token, app._room_participant.generation
    host_start = Mock(side_effect=AssertionError("A completed Art guest cannot silently host"))
    monkeypatch.setattr(app._room_participant, "start_lan_host", host_start)
    monkeypatch.setattr(app, "_begin_remote_host", host_start)
    opened = _invitation_dialog(monkeypatch)

    if entry == "hud":
        app.window.session_hud._action.click()
    elif entry == "header":
        app.window.session_strip.launch_audio_requested.emit()
    else:
        app._on_conductor_action_requested("primary")
    qapp.processEvents()

    assert opened == [True]
    assert _workspace(app) == before
    assert app.session_conductor.token == token
    assert app._room_participant.generation == generation
    assert app._room_participant.state is ArtRoomState.NONE
    assert app.window.art_room_overview._overview.phase == "ended"
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    assert app._remote_invitation is None and app._guest_invite is None
    host_start.assert_not_called()
    _assert_no_guest_launch(pair)


@pytest.mark.parametrize("transport", ["lan", "native"])
@pytest.mark.parametrize("profile", ["art", "music"])
def test_fresh_invitation_from_completed_guest_enters_new_art_owner(
    guest, qapp, monkeypatch, transport, profile,
):
    from core.room_state import RoomState
    from services.remote_session_runtime import RemoteSessionPhase
    from tests.test_art_room_controller import RoomBackend, arm_lan, drain, invitation, remote, state

    pair = _connected_guest(guest, transport=transport, profile=profile, hosting=True)
    app = pair.app
    previous_owner, previous_generation = pair.owner, pair.generation
    previous_invite = getattr(pair, "invite", None)
    _leave(pair, qapp)
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    restored_notes = app.window.session_canvas.current_notes()
    fresh = invitation() if transport == "lan" else remote()
    if transport == "lan":
        arm_lan(monkeypatch, fresh)
    opened = _invitation_dialog(monkeypatch, invitation=fresh)
    app.window.session_hud._action.click()
    assert opened == [True]
    if transport == "lan":
        owner = app._room_participant.lan_guest
        assert owner is not None and owner is not previous_owner
        owner.poll_once()
    else:
        drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
        owner = app._remote_session
        assert owner is not previous_owner
        RoomBackend.instances[-1].emit(RoomState(1, "art", "talk_and_make"))
    drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
    assert app._session_conductor_facts().creator_profile_key == "art"
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.NONE
    assert app.window.art_room_overview._overview.phase == "connected"
    assert app.window.session_canvas.current_notes() == pair.art_notes
    assert app._room_participant.generation > previous_generation
    if transport == "lan":
        app._room_participant.receive_lan(previous_owner, previous_generation, state(previous_invite))
    else:
        pair.backend.emit(RoomState(99, "art", "paint_along"))
    qapp.processEvents()
    assert app._room_participant.state is ArtRoomState.CONNECTED
    current_owner = app._room_participant.lan_guest if transport == "lan" else app._remote_session
    assert current_owner is owner
    assert app._room_participant.borrowed_start != "paint_along"
    _assert_no_guest_launch(pair)
    _leave(pair, qapp)
    assert app.window.session_canvas.current_notes() == restored_notes
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE


def test_completed_art_host_starts_a_new_room_with_new_invitation_only_on_click(
    host, qapp, caplog,
):
    from tests.test_art_room_controller import drain

    rig = host()
    app, owner = rig.app, rig.owner
    old_listener, old_credentials = owner.server, owner.credentials
    old_invite = rig.invite_fingerprint()
    notes = app.window.session_canvas.current_notes()
    app.window.session_strip._audio_button.click()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert not owner.active and app._room_participant.state is ArtRoomState.NONE
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.START_SESSION
    assert app._last_session_conductor.action_label == "Start New Room"
    assert app.window.session_hud._action.text() == "Start New Room"
    assert "Start New Room" in app.window.session_canvas._guidance_next.text()
    ended_token = app.session_conductor.token
    for _ in range(3):
        rig.tick()
    assert app.session_conductor.token == ended_token
    assert owner.start_count == 1 and not rig.invite_fingerprint()
    app.window.session_hud._action.click()
    rig.tick()
    assert owner.active and owner.start_count == 2
    assert owner.server is not old_listener and owner.credentials is not old_credentials
    assert old_listener._httpd.stopping
    assert rig.invite_fingerprint() and rig.invite_fingerprint() != old_invite
    assert app._room_participant.state is ArtRoomState.WAITING
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.COPY_INVITE
    assert app.window.session_canvas.current_notes() == notes
    assert app.window.art_room_overview._overview.phase == "waiting"
    assert rig.launcher.joined == [] and rig.launcher.host_pages == 0
    rig.player.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    public = repr(app.window.session_canvas._current_guidance.to_public_dict()) + caplog.text
    assert "PRIVATE_HOST_NOTES" not in public


@pytest.mark.parametrize("transport", ["lan", "native"])
def test_art_guest_cleanup_failure_keeps_leave_ahead_of_fresh_invitation(
    guest, qapp, monkeypatch, caplog, transport,
):
    from tests.test_art_room_controller import drain

    pair = _connected_guest(guest, transport=transport, profile="music", hosting=True)
    app = pair.app
    concrete = pair.owner if transport == "lan" else pair.backend
    original_stop = concrete.stop
    calls = []

    def fail_once():
        calls.append(True)
        if len(calls) == 1:
            if transport == "native":
                raise OSError("PRIVATE_ROOM_STOP_DETAIL")
            return False
        return original_stop()

    monkeypatch.setattr(concrete, "stop", fail_once)
    app.window.session_strip.launch_audio_requested.emit()
    drain(qapp, lambda: not app.audio.stopping)
    assert app.audio.cleanup_retry_required
    assert app._last_musician_guidance.next_step == "Try Leave Room"
    assert "Try Leave Room" in app.window.session_canvas._guidance_next.text()
    assert app.window.session_strip._audio_button.isVisibleTo(app.window)
    assert app.window.session_strip._audio_button.isEnabled()
    assert app.window.session_strip._audio_button.accessibleName() == "Try Leave Room"
    opened = _invitation_dialog(monkeypatch)
    app._on_conductor_action_requested("paste_invite")
    assert not opened
    assert app.audio.cleanup_retry_required
    app.window.session_strip._audio_button.click()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    assert app.creator_profile.key == "music"
    assert "PRIVATE_ROOM_STOP_DETAIL" not in caplog.text
    _assert_no_guest_launch(pair)


@pytest.mark.parametrize("profile", ["art", "music"])
def test_completed_native_art_guest_has_one_reachable_private_safe_action_at_760(
    guest, qapp, monkeypatch, tmp_path, caplog, profile,
):
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel
    from webjam_qt.theme import load_stylesheet

    pair = _connected_guest(guest, transport="native", profile=profile, hosting=True)
    app = pair.app
    _leave(pair, qapp)
    app.window.setStyleSheet(load_stylesheet())
    app.window.resize(760, 600)
    qapp.processEvents()
    hud = app.window.session_hud
    opened = _invitation_dialog(monkeypatch)
    for visit, view in enumerate(("stage", "canvas", "stage"), start=1):
        app._on_rail_view_changed(view)
        # Deliver queued Qt layout/paint events without synthesizing room facts
        # or calling a private refresh to conceal a navigation defect.
        qapp.processEvents()
        qapp.processEvents()
        action_rect = QRect(hud._action.mapTo(app.window, QPoint()), hud._action.size())
        geometry = {
            "profile": profile, "view": view,
            "window": app.window.rect().getRect(),
            "hud": hud.geometry().getRect(),
            "hud_hint": (hud.sizeHint().width(), hud.sizeHint().height()),
            "hud_visible": hud.isVisibleTo(app.window),
            "action": action_rect.getRect(),
            "action_visible": hud._action.isVisibleTo(app.window),
            "action_region_empty": hud._action.visibleRegion().isEmpty(),
        }
        try:
            assert hud.isVisibleTo(app.window) and hud.height() > 0, geometry
            assert hud._action.isVisibleTo(app.window) and hud._action.isEnabled(), geometry
            assert hud._action.text() == "Paste New Invite", geometry
            assert action_rect.width() > 0 and action_rect.height() > 0, geometry
            assert app.window.rect().contains(action_rect), geometry
            assert not hud._action.visibleRegion().isEmpty(), geometry
            assert app.window.childAt(action_rect.center()) is hud._action, geometry
            assert not app.window.session_strip._audio_button.isVisibleTo(app.window), geometry
        except AssertionError:
            app.window.grab().save(str(tmp_path / f"art-exit-{profile}-{view}-{visit}.png"))
            raise
        QTest.mouseClick(hud._action, Qt.MouseButton.LeftButton)
        assert opened == [True] * visit
        assert app._last_content_key == view
        assert app._last_session_conductor.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    assert "Record Session" not in app.window.participant_grid._empty_hint.text()
    token, generation = app.session_conductor.token, app._room_participant.generation
    guidance = app.window.session_canvas._current_guidance
    for _ in range(3):
        app._tick_creator_start()
        app._update_session_hud()
    assert app.session_conductor.token == token and app._room_participant.generation == generation
    assert app.window.session_canvas._current_guidance == guidance
    public = repr(guidance.to_public_dict()) + caplog.text
    for private in ("PRIVATE_PERSONAL_PLAN", "PRIVATE_ART_WORK", "PRIVATE_PERSONAL_STUDIO"):
        assert private not in public
    for label in hud.findChildren(QLabel):
        if label.isVisibleTo(app.window) and label.text():
            assert label.height() >= label.heightForWidth(label.width())
    _assert_no_guest_launch(pair)


@pytest.mark.parametrize("transport", ["lan", "native"])
def test_pending_art_guest_leave_blocks_paste_and_start_until_owner_stops(
    guest, qapp, monkeypatch, transport,
):
    import threading
    from tests.test_art_room_controller import drain

    pair = _connected_guest(guest, transport=transport, profile="art", hosting=True)
    app = pair.app
    concrete = pair.owner if transport == "lan" else pair.backend
    original_stop = concrete.stop
    entered, release = threading.Event(), threading.Event()

    def pending_stop():
        entered.set()
        assert release.wait(3), "Controlled room cleanup did not get released"
        return original_stop()

    monkeypatch.setattr(concrete, "stop", pending_stop)
    opened = _invitation_dialog(monkeypatch)
    try:
        app.window.session_strip.launch_audio_requested.emit()
        drain(qapp, entered.is_set)
        assert app.audio.stopping
        app._update_session_hud()
        assert app._last_session_conductor.primary_action is SessionPrimaryAction.WAIT
        assert not app.window.session_hud._action.isVisibleTo(app.window)
        assert not app.window.session_strip._audio_button.isEnabled()
        app._on_conductor_action_requested("paste_invite")
        app._on_conductor_action_requested("start_session")
        app.window.session_strip.launch_audio_requested.emit()
        assert not opened
        assert app.audio.stopping
        _assert_no_guest_launch(pair)
    finally:
        release.set()
        drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE


@pytest.mark.parametrize("cleanup", ["confirmed", "retry"])
def test_leaving_native_reentry_before_room_profile_discards_exited_invitation(
    guest, qapp, monkeypatch, caplog, cleanup,
):
    from core.room_state import RoomState
    from services.remote_session_runtime import RemoteSessionPhase
    from tests.test_art_room_controller import RoomBackend, drain, remote

    pair = _connected_guest(guest, transport="native", profile="art", hosting=True)
    app = pair.app
    _leave(pair, qapp)
    restored_title = app.window.session_strip.current_title()
    fresh = remote()
    with monkeypatch.context() as entry_patch:
        opened = _invitation_dialog(entry_patch, invitation=fresh)
        app.window.session_hud._action.click()
        assert opened == [True]
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    drain(qapp, lambda: app._room_participant.native_wait_started > 0)
    owner, backend = app._remote_session, RoomBackend.instances[-1]
    assert owner is not pair.owner
    assert app._room_participant.probing
    assert app._room_participant.native_state is None
    assert app._remote_invitation is fresh
    before = _workspace(app)
    before = (*before[:2], restored_title, before[3])
    previous_count = len(RoomBackend.instances)
    leave = app.window.session_strip._audio_button
    assert leave.isVisibleTo(app.window) and leave.isEnabled()
    assert leave.accessibleName() == "Leave Room"
    if cleanup == "retry":
        original_stop = backend.stop
        calls = []

        def fail_once():
            calls.append(True)
            if len(calls) == 1:
                raise OSError("PRIVATE_PENDING_ROOM_STOP_DETAIL")
            return original_stop()

        monkeypatch.setattr(backend, "stop", fail_once)

    leave.click()
    drain(qapp, lambda: not app.audio.stopping)
    if cleanup == "retry":
        assert app.audio.cleanup_retry_required
        assert app._remote_session is owner
        assert app._remote_invitation is fresh
        assert app.window.session_canvas._current_guidance.next_step == "Try Leave Room"
        assert app.window.session_strip._audio_button.accessibleName() == "Try Leave Room"
        with monkeypatch.context() as blocked_patch:
            opened = _invitation_dialog(blocked_patch)
            app._on_conductor_action_requested("paste_invite")
            assert not opened
        backend.emit(RoomState(1, "art", "paint_along"))
        qapp.processEvents()
        assert app.audio.cleanup_retry_required
        assert app._room_participant.native_state is None
        app.window.session_strip._audio_button.click()
        drain(qapp, lambda: not app.audio.stopping)

    assert not app.audio.cleanup_retry_required
    assert app._remote_session is None
    assert app._remote_invitation is None
    assert app._room_participant.state is ArtRoomState.NONE
    assert not app._room_participant.probing
    assert app._last_session_conductor.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    assert app.window.session_canvas._current_guidance.next_step == "Paste New Invite"
    backend.emit(RoomState(2, "art", "paint_along"))
    qapp.processEvents()
    assert app._room_participant.native_state is None
    assert app._room_participant.state is ArtRoomState.NONE
    with monkeypatch.context() as cancel_patch:
        opened = _invitation_dialog(cancel_patch)
        app.window.session_strip.launch_audio_requested.emit()
        assert opened == [True]
    assert len(RoomBackend.instances) == previous_count
    assert app._remote_invitation is None and app._remote_session is None
    assert _workspace(app) == before
    assert "PRIVATE_PENDING_ROOM_STOP_DETAIL" not in caplog.text
    _assert_no_guest_launch(pair)
