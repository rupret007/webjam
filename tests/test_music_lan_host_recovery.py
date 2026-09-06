"""Music host invitations follow their real listener across LAN recovery."""
from __future__ import annotations

import hashlib
import logging
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from core.logging_config import configure_logging
from core.network_invite import parse_invite_link
from core.session_conductor import SessionConductorPhase
from core.settings import AppSettings
from tests.test_art_lan_host_recovery import (
    Clipboard,
    ControlledHost,
    Network,
    _drain,
    qapp as qapp,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.recording_coordinator import RecorderPhase
from webjam_qt.windows.conductor_window import ConductorWindow

_PRIVATE_NOTES = "PRIVATE_MUSIC_RECOVERY_NOTES: resolve the bridge harmony"
_PRIVATE_STOP = "PRIVATE_MUSIC_LISTENER_STOP_DETAIL"


class MusicHostJourney(SimpleNamespace):
    def __repr__(self):
        return "MusicLanHostJourney()"

    def tick(self):
        self.app._refresh_readiness()

    def invite_fingerprint(self):
        invite = self.app._current_invite_url()
        return hashlib.sha256(invite.encode()).hexdigest() if invite else ""

    def copy(self):
        with self.monkeypatch.context() as patch:
            patch.setattr(QApplication, "clipboard", lambda: self.clipboard)
            self.app._copy_band_invite()


@pytest.fixture
def music_host(qapp, monkeypatch, tmp_path):
    network, events, clipboard = Network(), [], Clipboard()
    notes = tmp_path / "local-notes"
    notes.mkdir()
    (notes / ".webjam_notes.md").write_text(_PRIVATE_NOTES, encoding="utf-8")
    monkeypatch.setattr("webjam_qt.controllers.session_persistence._persistence_home", lambda: notes)
    monkeypatch.setattr("core.network_invite.local_band_address", lambda: network.address)
    monkeypatch.setattr("services.native_remote_transport.reference_local_host_requested", lambda: False)
    monkeypatch.setattr("webjam_qt.platform_permissions.microphone_permission_status", lambda: "granted")
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: False)
    monkeypatch.setattr(ApplicationController, "_start_routing_scan", lambda self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    made = []

    def create(*, connected=True):
        root = tmp_path / f"app-{len(made)}"
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            host_server_enabled=True, last_creator_profile_key="music", jamulus_server="127.0.0.1",
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Rehearsal",
        )
        app = ApplicationController(window, settings=settings)
        # Network supervision is delivered explicitly. No real audio process,
        # socket, recording, meeting, or periodic worker belongs to this rig.
        for name in ("_reconnect_timer", "_level_timer", "_connection_timer"):
            getattr(app, name).stop()
        owner = ControlledHost(root / "peer", network, events)
        app.host_peer = owner
        runtime = SimpleNamespace(server_alive=True, primary_alive=True)
        app.bridge.hosted_server_alive = Mock(side_effect=lambda: runtime.server_alive)
        app.bridge.hosted_server_owned = Mock(side_effect=lambda: runtime.server_alive)
        app.bridge._port_free = Mock(return_value=False)
        app.bridge.jamulus_launch_intended = True
        app.bridge.jamulus_state = "Running"
        app._is_jamulus_running = Mock(side_effect=lambda: runtime.primary_alive)

        def stop_primary(*args, **kwargs):
            runtime.primary_alive = False
            app.bridge.jamulus_launch_intended = False
            app.bridge.jamulus_state = "Stopped"
            return True

        def stop_server(*args, **kwargs):
            runtime.server_alive = False
            return True

        app.bridge.stop_jamulus = Mock(side_effect=stop_primary)
        app.bridge.stop_hosted_server = Mock(side_effect=stop_server)
        app.bridge.launch_webex = Mock()
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        app._reset_remote_invite = Mock(wraps=app._reset_remote_invite)
        app.window.flash_message = Mock()
        app.audio.connected = connected
        app._conductor_setup_requested = True
        app._start_session_conductor_attempt("host")
        rig = MusicHostJourney(
            app=app, owner=owner, network=network, events=events,
            clipboard=clipboard, root=root, runtime=runtime, monkeypatch=monkeypatch,
        )
        made.append(rig)
        window.resize(1040, 720)
        window.show()
        qapp.processEvents()
        rig.tick()
        assert owner.active and owner.start_count == 1
        assert app._last_session_conductor.phase is SessionConductorPhase.INVITE_READY
        rig.copy()
        assert clipboard.value and app._last_shared_lan_address == network.address
        clipboard.value = ""
        return rig

    yield create
    for rig in reversed(made):
        app = rig.app
        for owner in {rig.owner, app.host_peer}:
            owner.stop_outcomes.clear()
            owner.before_stop = None
        _drain(qapp, lambda: not app.audio.stopping)
        assert app.shutdown()
        window = app.window
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        assert not isValid(window)
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _assert_audio_untouched(rig):
    rig.app.bridge.stop_jamulus.assert_not_called()
    rig.app.bridge.stop_hosted_server.assert_not_called()
    rig.app._launch_native_jamulus_for_startup.assert_not_called()
    rig.app._start_hosted_server_for_startup.assert_not_called()
    rig.app.bridge.launch_webex.assert_not_called()
    rig.app._reset_remote_invite.assert_not_called()


def _retry(rig, qapp):
    hud = rig.app.window.session_hud
    assert hud._action.text() == "Try Again"
    assert hud._action.isVisibleTo(rig.app.window) and hud._action.isEnabled()
    assert hud._action_kind in {"retry", "try_reconnect", "retry_startup"}
    # Dispatch directly so injected private exceptions become normal failures,
    # rather than unhandled exceptions escaping a Qt signal callback.
    rig.app._on_conductor_action_requested(hud._action_kind)
    qapp.processEvents()


def test_changed_address_never_copies_a_mismatched_music_listener(music_host):
    rig = music_host()
    listener, credentials = rig.owner.server, rig.owner.credentials
    rig.network.key = "changed"
    for _ in range(3):
        rig.tick()
    # Real serializer and endpoint ownership matter: a mock invite would hide
    # this old-listener/new-address failure behind a successful Copy action.
    assert not rig.invite_fingerprint(), "A changed IP must not advertise the old listener"
    assert not rig.app._last_observed_invite_available
    rig.copy()
    assert not rig.clipboard.value
    assert rig.owner.server is listener and rig.owner.credentials is credentials
    assert rig.events == ["start:original"]
    _assert_audio_untouched(rig)


@pytest.mark.parametrize("loss", ["missing", "changed"])
@pytest.mark.parametrize("connected", [False, True])
def test_return_to_original_network_preserves_music_listener_and_evidence(
    music_host, loss, connected,
):
    rig = music_host(connected=connected)
    app, owner = rig.app, rig.owner
    listener, credentials = owner.server, owner.credentials
    token, fingerprint = app.session_conductor.token, rig.invite_fingerprint()
    notes = app.window.session_canvas.current_notes()
    rig.network.key = loss
    rig.tick()
    assert not rig.invite_fingerprint()
    rig.copy()
    assert not rig.clipboard.value
    rig.network.key = "original"
    rig.tick()
    assert owner.server is listener and owner.credentials is credentials
    assert app.session_conductor.token == token
    assert app.audio.connected is connected
    assert app._last_session_conductor.phase is SessionConductorPhase.INVITE_READY
    assert rig.invite_fingerprint() == fingerprint
    assert app.window.session_canvas.current_notes() == notes
    assert rig.events == ["start:original"]
    _assert_audio_untouched(rig)


def test_explicit_changed_network_retry_repairs_only_music_room_invitation(music_host, qapp):
    rig = music_host()
    app, owner = rig.app, rig.owner
    listener, credentials = owner.server, owner.credentials
    token, fingerprint = app.session_conductor.token, rig.invite_fingerprint()
    rig.network.key = "changed"
    rig.tick()
    _retry(rig, qapp)
    rig.tick()
    assert rig.events == ["start:original", "stop", "stopped", "start:changed"]
    assert owner.server is not listener and owner.credentials is not credentials
    assert listener._httpd.stopping
    assert owner.server.address[0] == rig.network.address
    assert app.session_conductor.token == token and app.audio.connected
    assert rig.invite_fingerprint() and rig.invite_fingerprint() != fingerprint
    assert app.window.session_hud._action.text() == "Copy New Invite"
    assert app._last_musician_guidance.next_step == "Copy New Invite"
    invitation = parse_invite_link(app._current_invite_url())
    assert invitation.peer_enabled and invitation.host == owner.server.address[0]
    assert invitation.peer_port == owner.peer_port
    rig.copy()
    assert rig.clipboard.value and app._last_shared_lan_address == rig.network.address
    _assert_audio_untouched(rig)


@pytest.mark.parametrize("outcome", ["false", "none", "exception"])
def test_unconfirmed_music_listener_stop_keeps_cleanup_and_blocks_copy(
    music_host, qapp, outcome,
):
    rig = music_host()
    app, owner = rig.app, rig.owner
    listener, credentials = owner.server, owner.credentials
    rig.network.key = "changed"
    rig.tick()
    owner.stop_outcomes.append({"false": False, "none": None, "exception": OSError(_PRIVATE_STOP)}[outcome])
    _retry(rig, qapp)
    assert app.audio.cleanup_retry_required and app.audio._stop_hosting
    assert not app.audio._stop_art_room
    assert owner.server is listener and owner.credentials is credentials
    assert owner.start_count == 1
    rig.network.key = "original"
    rig.tick()
    assert not rig.invite_fingerprint()
    rig.copy()
    assert not rig.clipboard.value
    end = app.window.session_strip._audio_button
    assert end.isVisibleTo(app.window) and end.isEnabled()
    assert "Try End" in end.accessibleName()
    assert app._last_musician_guidance.primary_action.value == "end_session"
    assert app._last_musician_guidance.next_step == "Try End Session"
    assert app.window.session_hud._action.isHidden()
    _assert_audio_untouched(rig)
    app._on_conductor_action_requested("end_session")
    _drain(qapp, lambda: not app.audio.stopping)
    assert app.audio.ended_by_user and not app.audio.cleanup_retry_required
    assert not owner.active
    for _ in range(3):
        rig.tick()
    assert owner.start_count == 1 and not rig.invite_fingerprint()


@pytest.mark.parametrize("conversation_open", [False, True])
def test_compact_music_network_recovery_has_one_reachable_action(
    music_host, qapp, conversation_open,
):
    rig = music_host()
    app, window = rig.app, rig.app.window
    window.webex_embed.setVisible(conversation_open)
    window.resize(760, 600)
    rig.network.key = "changed"
    rig.tick()
    for _ in range(3):
        qapp.processEvents()
    assert window.size() == QSize(760, 600)
    hud = window.session_hud
    assert hud._action.text() == "Try Again"
    assert hud._action.isVisibleTo(window) and hud._action.isEnabled()
    assert app._last_musician_guidance.next_step == hud._action.text()
    assert window.session_canvas._current_guidance is app._last_musician_guidance
    assert "Try Again" in window.session_canvas._guidance_next.text()
    assert window.participant_grid._empty_primary.isHidden()
    detail = QRect(hud._detail.mapTo(hud, QPoint()), hud._detail.size())
    action = QRect(hud._action.mapTo(hud, QPoint()), hud._action.size())
    assert hud.rect().contains(detail) and hud.rect().contains(action)
    assert not detail.intersects(action)
    assert hud._detail.height() >= hud._detail.heightForWidth(hud._detail.width())
    end = window.session_strip._audio_button
    assert end.isVisibleTo(window) and end.isEnabled()
    assert window.rect().contains(QRect(end.mapTo(window, QPoint()), end.size()))
    assert "End Session" in end.accessibleName()
    public = repr(app._last_musician_guidance.to_public_dict())
    assert _PRIVATE_NOTES not in public and rig.network.address not in public
    _retry(rig, qapp)
    assert rig.owner.active and rig.owner.server.address[0] == rig.network.address
    _assert_audio_untouched(rig)


def test_no_address_retry_keeps_music_room_and_local_notes(music_host, qapp):
    rig = music_host()
    app, owner = rig.app, rig.owner
    listener, credentials = owner.server, owner.credentials
    token = app.session_conductor.token
    notes = app.window.session_canvas.current_notes()
    rig.network.key = "missing"
    for _ in range(3):
        rig.tick()
        app._on_conductor_action_requested("try_reconnect")
        qapp.processEvents()
    assert not rig.invite_fingerprint()
    assert owner.server is listener and owner.credentials is credentials
    assert app.session_conductor.token == token and app.audio.connected
    assert app.window.session_canvas.current_notes() == notes
    assert rig.events == ["start:original"]
    _assert_audio_untouched(rig)


def test_failed_music_listener_replacement_never_silently_downgrades_invite(music_host, qapp):
    rig = music_host()
    rig.network.key = "changed"
    rig.tick()
    rig.owner.fail_start = True
    _retry(rig, qapp)
    for _ in range(3):
        rig.tick()
    assert not rig.owner.active and rig.owner.start_count == 2
    assert not rig.invite_fingerprint()
    rig.copy()
    assert not rig.clipboard.value
    _retry(rig, qapp)
    assert rig.owner.active and rig.owner.start_count == 3
    assert parse_invite_link(rig.app._current_invite_url()).peer_enabled
    _assert_audio_untouched(rig)


@pytest.mark.parametrize("loss", ["missing", "changed"])
@pytest.mark.parametrize("phase", [RecorderPhase.RECORDING, RecorderPhase.FINALIZING])
def test_current_take_owns_music_recovery_action_and_is_never_replaced(music_host, phase, loss):
    rig = music_host()
    app, owner = rig.app, rig.owner
    take_id = str(uuid.uuid4())
    owner.control.begin(take_id, started_utc="2026-09-06T12:00:00Z")
    if phase is RecorderPhase.FINALIZING:
        owner.control.begin_finalizing(take_id, stopped_utc="2026-09-06T12:01:00Z")
    app.recording.phase = phase
    listener, credentials, control = owner.server, owner.credentials, owner.control
    try:
        rig.network.key = loss
        rig.tick()
        guidance = app._last_musician_guidance
        assert guidance.primary_action.value == (
            "stop_recording" if phase is RecorderPhase.RECORDING else "wait"
        )
        app._on_conductor_action_requested("try_reconnect")
        assert owner.server is listener and owner.credentials is credentials
        assert owner.control is control and owner.control.snapshot().take_id == take_id
        assert rig.events == ["start:original"]
        assert not rig.invite_fingerprint()
        _assert_audio_untouched(rig)
    finally:
        # This fixture models recording-owner facts; it owns no actual recorder.
        app.recording.phase = RecorderPhase.IDLE


@pytest.mark.parametrize("work", ["registered_take", "capture_arm", "completed_take"])
def test_retained_take_or_transfer_requires_end_instead_of_peer_replacement(music_host, work):
    rig = music_host()
    app, owner = rig.app, rig.owner
    take_id = str(uuid.uuid4())
    if work == "registered_take":
        take_dir = rig.root / "retained-take"
        take_dir.mkdir()
        owner.register_take(take_id, take_dir)
    elif work == "capture_arm":
        owner.control.publish_capture_arm(
            take_id, recording_plan_fingerprint="a" * 64, requirements=(),
        )
    else:
        owner.control.begin(take_id, started_utc="2026-09-06T12:00:00Z")
        owner.control.finish(take_id, stopped_utc="2026-09-06T12:01:00Z")
    listener, credentials, control = owner.server, owner.credentials, owner.control
    rig.network.key = "changed"
    rig.tick()
    assert app._last_musician_guidance.primary_action.value == "end_session"
    assert app._last_musician_guidance.next_step == "End Session"
    assert app.window.session_hud._action.isHidden()
    app._on_conductor_action_requested("try_reconnect")
    assert owner.server is listener and owner.credentials is credentials and owner.control is control
    assert rig.events == ["start:original"]
    assert not rig.invite_fingerprint()
    _assert_audio_untouched(rig)


def test_end_during_music_route_loss_cannot_restart_listener_on_return(music_host, qapp):
    rig = music_host()
    app = rig.app
    notes = app.window.session_canvas.current_notes()
    rig.network.key = "missing"
    rig.tick()
    app._on_session_audio_requested()
    _drain(qapp, lambda: not app.audio.stopping)
    assert app.audio.ended_by_user and not app.audio.cleanup_retry_required
    assert not rig.owner.active
    starts = rig.owner.start_count
    rig.network.key = "original"
    for _ in range(3):
        rig.tick()
    assert not rig.owner.active and rig.owner.start_count == starts
    assert not rig.invite_fingerprint()
    assert app.window.session_canvas.current_notes() == notes


@pytest.mark.parametrize("retirement", ["end", "quit", "replacement"])
def test_reentrant_retirement_cannot_restart_old_music_listener(music_host, qapp, retirement):
    rig = music_host()
    app, owner = rig.app, rig.owner
    rig.network.key = "changed"
    rig.tick()
    replacement = []

    def retire():
        if retirement == "end":
            app._on_session_audio_requested()
        elif retirement == "quit":
            assert app.shutdown()
        else:
            new = ControlledHost(rig.root / "newer-peer", rig.network, rig.events)
            new.start(rig.network.address, creator_profile_key="music")
            app.host_peer = new
            app._room_participant.generation += 1
            replacement.append(new)

    owner.before_stop = retire
    _retry(rig, qapp)
    _drain(qapp, lambda: not app.audio.stopping)
    assert owner.start_count == 1
    if retirement == "replacement":
        assert app.host_peer is replacement[0] and replacement[0].active
        assert replacement[0].start_count == 1
    else:
        assert not owner.active
        assert app._shutdown if retirement == "quit" else app.audio.ended_by_user


def test_music_recovery_events_reach_private_safe_rotating_log(music_host, qapp, tmp_path):
    logger = logging.getLogger("webjam")
    previous_handlers = list(logger.handlers)
    previous_level, previous_propagate = logger.level, logger.propagate
    for handler in previous_handlers:
        logger.removeHandler(handler)
    rig = None
    try:
        log_path = tmp_path / "isolated-logs" / "webjam.log"
        configure_logging(AppSettings(log_file=str(log_path), log_level="INFO"))
        rotating = [handler for handler in logger.handlers if isinstance(handler, RotatingFileHandler)]
        assert len(rotating) == 1 and Path(rotating[0].baseFilename) == log_path
        rig = music_host()
        private_invite = rig.app._current_invite_url()
        private_credentials = rig.owner.credentials
        original_address = rig.network.address
        rig.network.key = "missing"
        for _ in range(5):
            rig.tick()
        rig.app._on_conductor_action_requested("try_reconnect")
        rig.network.key = "original"
        for _ in range(5):
            rig.tick()
        rig.network.key = "changed"
        for _ in range(5):
            rig.tick()
        rig.owner.stop_outcomes.append(OSError(_PRIVATE_STOP))
        _retry(rig, qapp)
        assert rig.app.audio.cleanup_retry_required
        for _ in range(5):
            rig.tick()
        for handler in logger.handlers:
            handler.flush()
        payload = log_path.read_text(encoding="utf-8")
        lines = payload.splitlines()
        expected = {
            "Music LAN room network interrupted": 2,
            "Music LAN room network route restored": 1,
            "Music LAN room retry deferred: network unavailable": 1,
            "Music LAN room listener replacement requested": 1,
            "Music LAN room listener cleanup unconfirmed": 1,
        }
        for message, count in expected.items():
            assert sum(line.endswith(message) for line in lines) == count
        forbidden = (
            private_invite, _PRIVATE_NOTES, _PRIVATE_STOP, original_address,
            rig.network.address, private_credentials.invite_token,
            private_credentials.session_id,
        )
        assert not any(private in payload for private in forbidden)
    finally:
        try:
            if rig is not None:
                rig.owner.stop_outcomes.clear()
                rig.owner.before_stop = None
                _drain(qapp, lambda: not rig.app.audio.stopping)
                assert rig.app.shutdown()
                qapp.processEvents()
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                if handler not in previous_handlers:
                    handler.close()
            for handler in previous_handlers:
                logger.addHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate


@pytest.mark.parametrize("transition", ["failed_cleanup", "listener_repair"])
def test_music_listener_transition_blocks_new_recording_and_shared_track(
    music_host, qapp, monkeypatch, transition,
):
    rig = music_host()
    app, owner = rig.app, rig.owner
    app.settings.local_capture_choice_made = True
    record_boundary = Mock(name="recording_coordinator_request")
    monkeypatch.setattr(app.recording, "on_record_requested", record_boundary)
    rig.network.key = "changed"
    rig.tick()
    observations = []

    def observe_new_take_actions():
        rig.tick()
        record = app.window.session_strip._record_button
        shown_and_enabled = record.isVisibleTo(app.window) and record.isEnabled()
        # The semantic entry point must reject a queued action too; hiding
        # chrome alone is not an authority check at the recording boundary.
        app._on_conductor_action_requested("record")
        app._on_record_requested()
        if shown_and_enabled:
            record.click()
        observations.append((
            record_boundary.call_count,
            shown_and_enabled,
            app._reference_track_lifecycle_blocks_play(),
        ))

    if transition == "failed_cleanup":
        owner.stop_outcomes.append(False)
        _retry(rig, qapp)
        assert app.audio.cleanup_retry_required
        observe_new_take_actions()
    else:
        # stop() is synchronous but may deliver an owner callback before it
        # returns. A queued Record or Shared Track action must remain fenced.
        owner.before_stop = observe_new_take_actions
        _retry(rig, qapp)
        assert not app.audio.cleanup_retry_required and owner.active
    assert observations == [(0, False, True)]
    assert app.audio.connected
    _assert_audio_untouched(rig)


@pytest.mark.parametrize("transition", ["failed_cleanup", "listener_repair"])
def test_music_listener_transition_keeps_existing_stop_recording_available(
    music_host, qapp, monkeypatch, transition,
):
    rig = music_host()
    app, owner = rig.app, rig.owner
    app.settings.local_capture_choice_made = True
    record_boundary = Mock(name="existing_recording_stop_request")
    monkeypatch.setattr(app.recording, "on_record_requested", record_boundary)
    rig.network.key = "changed"
    rig.tick()
    observations = []

    def observe_existing_stop():
        # Model the recorder's authoritative state arriving during cleanup;
        # the fixture owns no recorder, capture, or media file.
        try:
            app._recorder_armed = True
            app.recording._set_phase(RecorderPhase.RECORDING)
            rig.tick()
            record = app.window.session_strip._record_button
            observations.append((
                record.isVisibleTo(app.window) and record.isEnabled(),
                "Stop Recording" in record.accessibleName(),
            ))
            app._on_conductor_action_requested("stop_recording")
        finally:
            app._recorder_armed = False
            app.recording._set_phase(RecorderPhase.IDLE)

    if transition == "failed_cleanup":
        owner.stop_outcomes.append(False)
        _retry(rig, qapp)
        assert app.audio.cleanup_retry_required
        observe_existing_stop()
    else:
        owner.before_stop = observe_existing_stop
        _retry(rig, qapp)
        assert not app.audio.cleanup_retry_required and owner.active
    assert observations == [(True, True)]
    record_boundary.assert_called_once_with()
    _assert_audio_untouched(rig)
