"""Authenticated Art adoption refreshes actual ApplicationController Notes guidance."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from core.settings import AppSettings, save_settings
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_room_controller import RoomBackend, arm_lan, drain, invitation, remote
from tests.test_shared_canvas_coordinator import FakeLauncher
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.session_persistence import _PROFILE_NOTES_FILES
from webjam_qt.windows.conductor_window import ConductorWindow

_PERSONAL_NOTES = "Next: PRIVATE_PERSONAL_PLAN\nKeep the original local notes."
_ART_NOTES = "Next: shape the clay base\nPRIVATE_ART_WORK remains on this computer."
_PENDING_ART_NOTES = "Next: refine the clay silhouette\nPRIVATE_PENDING_ART_WORK"
_PERSONAL_TITLE = "PRIVATE_PERSONAL_STUDIO"


class Journey(SimpleNamespace):
    def __repr__(self):
        return f"ArtGuidanceJourney(transport={self.transport!r}, profile={self.profile!r})"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def guest(qapp, monkeypatch, tmp_path):
    made = []
    note_home = tmp_path / "personal-notes"
    note_home.mkdir()
    monkeypatch.setattr(
        "webjam_qt.controllers.session_persistence._persistence_home", lambda: note_home,
    )
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    information = Mock(return_value=QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", information)
    launcher = FakeLauncher()
    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", lambda settings: launcher)
    player_factory = Mock(side_effect=AssertionError("Profile adoption must not open a player"))
    monkeypatch.setattr(
        "webjam_qt.widgets.reference_video_player.create_qt_reference_video_player", player_factory,
    )

    def create(*, transport="native", profile="music", notes="saved"):
        root = tmp_path / str(len(made))
        root.mkdir()
        personal = _ART_NOTES if profile == "art" else _PERSONAL_NOTES
        art_notes = "" if notes == "empty" else _ART_NOTES
        (note_home / _PROFILE_NOTES_FILES[profile]).write_text(personal, encoding="utf-8")
        (note_home / _PROFILE_NOTES_FILES["art"]).write_text(art_notes, encoding="utf-8")
        if profile == "art":
            personal = art_notes
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            last_creator_profile_key=profile, last_creator_start_key="talk_and_make",
        )
        save_settings(settings)
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title=_PERSONAL_TITLE,
        )
        app = ApplicationController(window, settings=settings)
        pair = Journey(
            app=app, transport=transport, profile=profile, personal_notes=personal,
            art_notes=art_notes, home=note_home, launcher=launcher, player_factory=player_factory,
            write_failure=None,
        )
        made.append(pair)
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        app.bridge.launch_webex = Mock()
        app.window.flash_message = Mock()
        app._set_session_entry_title(_PERSONAL_TITLE, borrowed=False)
        if notes == "pending":
            # Prepare an actual failed Art draft before joining. Adoption must
            # use this retained draft, without making the guest type again.
            app._apply_creator_profile_key("art")
            app.window.session_canvas.set_notes(_PENDING_ART_NOTES)
            failure = monkeypatch.context()
            patcher = failure.__enter__()
            pair.write_failure = failure
            patcher.setattr(
                "webjam_qt.controllers.session_persistence.atomic_write_text",
                Mock(side_effect=OSError("Controlled notes write failure")),
            )
            assert app._save_notes() is False
            app._apply_creator_profile_key(profile)
            pair.art_notes = _PENDING_ART_NOTES
        # Drain only work from the explicit setup above. No edit or refresh is
        # synthesized after authentication to mask the original missing update.
        drain(qapp, lambda: not app._pulse_refresh_timer.isActive())
        assert app.creator_profile.key == profile
        assert app._current_session_pulse.mode_key == profile
        assert window.session_canvas.current_notes() == personal
        window.resize(1040, 720)
        window.show()
        qapp.processEvents()

        def connect():
            if transport == "lan":
                invite = invitation()
                arm_lan(monkeypatch, invite)
                assert app.accept_invitation(invite)
                owner = app._room_participant.lan_guest
                assert owner is not None
                owner.poll_once()
                pair.owner = owner
                pair.invite = invite
            else:
                assert app.accept_invitation(remote())
                drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
                pair.owner = app._remote_session
                pair.backend = RoomBackend.instances[-1]
                pair.revision = 1
                pair.backend.emit(RoomState(pair.revision, "art", "talk_and_make"))
            drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
            if app._last_session_conductor_snapshot.facts.creator_profile_key != "art":
                # Let the existing one-second room/readiness cadence settle;
                # never invent a conductor token or mutate transport evidence.
                QTest.qWait(1100)
            pair.generation = app._room_participant.generation
            pair.identity = app._reference_video_identity()

        def repeat_receipt():
            if transport == "lan":
                pair.owner.poll_once()
                qapp.processEvents()
            else:
                pair.revision += 1
                pair.backend.emit(RoomState(pair.revision, "art", "talk_and_make"))
                drain(qapp, lambda: app._room_participant.native_state.revision == pair.revision)

        pair.connect, pair.repeat_receipt = connect, repeat_receipt
        return pair

    yield create
    for pair in reversed(made):
        if pair.write_failure is not None:
            pair.write_failure.__exit__(None, None, None)
            pair.write_failure = None
        app = pair.app
        if app.audio.stopping:
            drain(qapp, lambda: not app.audio.stopping)
        assert app.shutdown()
        app.window.close()
        app.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(app.window)
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    information.assert_not_called()


def _assert_art_guidance(pair):
    app = pair.app
    canvas = app.window.session_canvas
    pulse = app._current_session_pulse
    assert app.creator_profile.key == "art"
    assert canvas.current_notes() == pair.art_notes
    assert pulse is canvas._current_pulse
    assert pulse.mode_key == "art" and pulse.mode_label == "Art"
    expected = (
        "Work the next action: refine the clay silhouette" if pair.art_notes == _PENDING_ART_NOTES
        else "Work the next action: shape the clay base" if pair.art_notes
        else "Start with set up the table."
    )
    assert pulse.next_step == expected
    assert canvas._pulse_next.text() == f"Next: {expected}"
    assert pulse.title == app.window.session_strip.current_title()
    assert pulse.participant_signal.count == 0
    assert "PRIVATE_PERSONAL_PLAN" not in pulse.next_step
    observed = app._last_session_conductor_snapshot
    current = app._session_conductor_facts()
    assert canvas._current_guidance.creative is pulse, (
        observed.facts.creator_profile_key, observed.presentation.phase.value,
        current.creator_profile_key, current.art_room.value,
    )
    assert app.settings.last_creator_profile_key == pair.profile


def _assert_private_and_owned(pair, caplog):
    app = pair.app
    assert app._room_participant.generation == pair.generation
    assert app._reference_video_identity() == pair.identity
    if pair.transport == "lan":
        assert app._room_participant.lan_guest is pair.owner
    else:
        assert app._remote_session is pair.owner
    assert app.guest_peer is None
    pair.player_factory.assert_not_called()
    assert pair.launcher.joined == [] and pair.launcher.host_pages == 0
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    canvas = app.window.session_canvas
    public = (
        repr(canvas._current_guidance.to_public_dict())
        + repr(app.window.art_room_overview._overview)
        + app.window.art_room_overview.accessibleDescription()
        + repr(app.window.flash_message.call_args_list) + caplog.text
    )
    for marker in (
        "PRIVATE_PERSONAL_PLAN", "PRIVATE_ART_WORK", "PRIVATE_PENDING_ART_WORK", _PERSONAL_TITLE,
    ):
        assert marker not in public


def _leave(pair, qapp):
    app = pair.app
    app.window.session_strip.launch_audio_requested.emit()
    drain(qapp, lambda: not app.audio.stopping)
    drain(qapp, lambda: app.creator_profile.key == pair.profile)
    assert not app.audio.cleanup_retry_required
    canvas = app.window.session_canvas
    assert canvas.current_notes() == pair.personal_notes
    assert app._current_session_pulse.mode_key == pair.profile
    assert canvas._current_pulse is app._current_session_pulse
    assert app._current_session_pulse.title == app.window.session_strip.current_title()
    assert app.window.session_strip.current_title() == _PERSONAL_TITLE


@pytest.mark.parametrize("transport", ["lan", "native"])
@pytest.mark.parametrize("profile", ["music", "podcast_voice"])
@pytest.mark.parametrize("notes", ["empty", "saved", "pending"])
def test_authenticated_art_adoption_uses_current_notes_without_typing(
    guest, qapp, caplog, transport, profile, notes,
):
    pair = guest(transport=transport, profile=profile, notes=notes)
    app, canvas = pair.app, pair.app.window.session_canvas
    changed = QSignalSpy(canvas.notes_changed)
    art_path = pair.home / _PROFILE_NOTES_FILES["art"]
    disk_before = art_path.read_bytes()
    pending_before = dict(app._persistence._pending_notes)

    pair.connect()

    _assert_art_guidance(pair)
    assert changed.count() == 0
    assert art_path.read_bytes() == disk_before
    assert app._persistence._pending_notes == pending_before
    if notes == "pending":
        assert canvas._notes_save_state == "failed"
        assert canvas._save_notes_button.isVisibleTo(canvas)
    pulse = app._current_session_pulse
    for _ in range(3):
        pair.repeat_receipt()
    assert app._current_session_pulse is pulse
    assert canvas._current_pulse is pulse
    assert changed.count() == 0
    _assert_private_and_owned(pair, caplog)

    _leave(pair, qapp)
    assert changed.count() == 0
    assert canvas.current_notes() == _PERSONAL_NOTES
    assert app._current_session_pulse.next_step == "Work the next action: PRIVATE_PERSONAL_PLAN"
    pair.connect()
    _assert_art_guidance(pair)
    assert changed.count() == 0
    assert art_path.read_bytes() == disk_before
    _assert_private_and_owned(pair, caplog)


@pytest.mark.parametrize("transport", ["lan", "native"])
def test_same_art_profile_owner_change_preserves_notes_and_current_title(
    guest, qapp, caplog, transport,
):
    pair = guest(transport=transport, profile="art")
    app, canvas = pair.app, pair.app.window.session_canvas
    cursor = canvas._notes.textCursor()
    cursor.setPosition(4)
    from PySide6.QtGui import QTextCursor
    cursor.setPosition(14, QTextCursor.MoveMode.KeepAnchor)
    canvas._notes.setTextCursor(cursor)
    before = (canvas.current_notes(), cursor.position(), cursor.anchor(), canvas._notes.document().isUndoAvailable())
    changed = QSignalSpy(canvas.notes_changed)

    pair.connect()

    _assert_art_guidance(pair)
    current = canvas._notes.textCursor()
    assert (canvas.current_notes(), current.position(), current.anchor(), canvas._notes.document().isUndoAvailable()) == before
    pulse = app._current_session_pulse
    pair.repeat_receipt()
    assert app._current_session_pulse is pulse
    assert changed.count() == 0
    _assert_private_and_owned(pair, caplog)
    _leave(pair, qapp)
    assert canvas.current_notes() == pair.art_notes
    assert changed.count() == 0
