"""Real Art host actions recover a retained LAN room without phantom resets."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox
from shiboken6 import isValid

from core.logging_config import configure_logging
from core.session_conductor import ArtRoomState, SessionConductorPhase
from core.session_transfer import SessionControlState, SessionCredentials, SessionPeerServer
from core.session_transfer_runtime import HostPeerSession
from core.settings import AppSettings
from tests.test_shared_canvas_coordinator import FakeLauncher
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow

_ADDRESSES = {"original": "192.168.71.20", "changed": "192.168.72.20", "missing": ""}
_PRIVATE_NOTES = "Next: refine the PRIVATE_HOST_NOTES clay model"
_PRIVATE_CANVAS = "drawpile://example.com/studio?v1&p=PRIVATE_CANVAS_PASSWORD"
_PRIVATE_STOP = "PRIVATE_STOP_DETAIL"
_PRIVATE_START = "PRIVATE_START_DETAIL"


class Network:
    key = "original"
    now = 100.0

    def __repr__(self):
        return f"ControlledNetwork({self.key})"

    @property
    def address(self):
        return _ADDRESSES[self.key]


class Listener:
    """Controlled binding with the production authenticated-reader expiry."""

    room_participants = SessionPeerServer.room_participants

    def __init__(self, address, network, serial):
        self.address = (address, 22000 + serial)
        self._httpd = SimpleNamespace(stopping=False)
        self._room_poll_lock = threading.Lock()
        self._room_poll_clock = lambda: network.now
        self._room_polls = {}

    def __repr__(self):
        return "ControlledLanListener()"

    def observe_artist(self):
        self._room_polls["authenticated-test-artist"] = self._room_poll_clock()


class ControlledHost(HostPeerSession):
    """Keep actual credentials/publication; replace only OS listener work."""

    def __init__(self, root, network, events):
        super().__init__()
        self.root, self.network, self.events = root, network, events
        self.start_count = 0
        self.stop_outcomes = deque()
        self.before_stop = None
        self.fail_start = False

    def __repr__(self):
        return f"ControlledHost(active={self.active}, starts={self.start_count})"

    def start(self, address, **kwargs):
        assert not self.active, "A second listener cannot start before confirmed stop"
        self.events.append("start:" + self.network.key)
        self.start_count += 1
        if self.fail_start:
            self.fail_start = False
            raise OSError(_PRIVATE_START)
        credentials = SessionCredentials.create()
        self.control = SessionControlState(
            self.root / str(self.start_count), credentials.session_id,
            creator_profile_key=kwargs["creator_profile_key"],
        )
        self.credentials = credentials
        self.server = Listener(address, self.network, self.start_count)
        self._lifecycle_generation += 1

    def stop(self):
        self.events.append("stop")
        outcome = self.stop_outcomes.popleft() if self.stop_outcomes else True
        callback, self.before_stop = self.before_stop, None
        if callback is not None:
            callback()
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not True:
            return outcome
        if self.server is not None:
            self.server._httpd.stopping = True
            self.server._room_polls.clear()
        self.server = self.credentials = self.control = None
        self.events.append("stopped")
        return True


class Clipboard:
    def __init__(self):
        self.value = ""

    def __repr__(self):
        return "PrivateTestClipboard()"

    def setText(self, value):  # noqa: N802 - Qt boundary
        self.value = value


class HostJourney(SimpleNamespace):
    def __repr__(self):
        return "ArtLanHostJourney()"

    def tick(self):
        self.app._tick_creator_start()

    def invite_fingerprint(self):
        invite = self.app._current_invite_url()
        return hashlib.sha256(invite.encode()).hexdigest() if invite else ""


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_font, previous_style = app.font(), app.styleSheet()
    fonts = Path(__file__).resolve().parents[1] / "webjam_qt/theme/fonts"
    font_ids = [QFontDatabase.addApplicationFont(str(path))
                for path in sorted(fonts.glob("Inter-*.ttf"))]
    font = QFont("Inter") if "Inter" in QFontDatabase.families() else QFont(previous_font)
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(load_stylesheet())
    try:
        yield app
    finally:
        app.setStyleSheet(previous_style)
        app.setFont(previous_font)
        for font_id in font_ids:
            if font_id >= 0:
                QFontDatabase.removeApplicationFont(font_id)


def _drain(qapp, predicate):
    deadline = time.monotonic() + 3.0
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def host(qapp, monkeypatch, tmp_path):
    network, events, clipboard = Network(), [], Clipboard()
    notes = tmp_path / "local-notes"
    notes.mkdir()
    (notes / ".webjam_notes.art.md").write_text(_PRIVATE_NOTES, encoding="utf-8")
    monkeypatch.setattr("webjam_qt.controllers.session_persistence._persistence_home", lambda: notes)
    monkeypatch.setattr("core.network_invite.local_band_address", lambda: network.address)
    monkeypatch.setattr("services.native_remote_transport.reference_local_host_requested", lambda: False)
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: False)
    monkeypatch.setattr(ApplicationController, "_start_routing_scan", lambda self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    launcher = FakeLauncher()
    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", lambda settings: launcher)
    player = Mock(side_effect=AssertionError("Network recovery must not open a video player"))
    monkeypatch.setattr("webjam_qt.widgets.reference_video_player.create_qt_reference_video_player", player)
    made = []

    def create(*, start=True):
        root = tmp_path / f"app-{len(made)}"
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            host_server_enabled=True, last_creator_profile_key="art",
            last_creator_start_key="talk_and_make",
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Clay studio",
        )
        app = ApplicationController(window, settings=settings)
        owner = ControlledHost(root / "peer", network, events)
        app.host_peer = owner
        app.bridge.launch_webex = Mock()
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        app._reset_remote_invite = Mock(wraps=app._reset_remote_invite)
        app.window.flash_message = Mock()
        rig = HostJourney(
            app=app, owner=owner, network=network, events=events,
            clipboard=clipboard, launcher=launcher, player=player, root=root,
            monkeypatch=monkeypatch,
        )
        made.append(rig)
        window.resize(1040, 720)
        window.show()
        qapp.processEvents()
        if start:
            assert app.begin_startup_journey()
            rig.tick()
            assert owner.active
            assert app._room_participant.state is ArtRoomState.WAITING
            assert app._last_session_conductor.phase is SessionConductorPhase.INVITE_READY
        return rig

    yield create
    for rig in reversed(made):
        app = rig.app
        # Fault injection never owns fixture cleanup or leaves a callback live.
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


def _retry(rig, qapp, *, click=True):
    hud = rig.app.window.session_hud
    assert hud._action_kind == "retry_startup"
    assert hud._action.isVisibleTo(rig.app.window) and hud._action.isEnabled()
    if click:
        hud._action.click()
    else:
        # A synchronous exception must be a normal test failure, not an
        # unhandled exception escaping a Qt signal callback.
        rig.app._on_conductor_action_requested(hud._action_kind)
    qapp.processEvents()


def _assert_no_external_work(rig, caplog):
    rig.app._launch_native_jamulus_for_startup.assert_not_called()
    rig.app._start_hosted_server_for_startup.assert_not_called()
    rig.app.bridge.launch_webex.assert_not_called()
    rig.player.assert_not_called()
    rig.app._reset_remote_invite.assert_not_called()
    public = (repr(rig.app.art_room_state().to_public_dict())
              + repr(rig.app.window.art_room_overview._overview) + caplog.text)
    for private in (_PRIVATE_NOTES, _PRIVATE_CANVAS, _PRIVATE_STOP, _PRIVATE_START, *_ADDRESSES.values()):
        if private:
            assert private not in public


@pytest.mark.parametrize("loss", ["missing", "changed"])
@pytest.mark.parametrize("readers", ["none", "fresh", "expired"])
def test_same_bound_address_recovers_current_hud_without_native_reset(
    host, qapp, caplog, loss, readers,
):
    rig = host()
    app, owner, room = rig.app, rig.owner, rig.app._room_participant
    listener, credentials = owner.server, owner.credentials
    token, generation = app.session_conductor.token, room.generation
    invite = rig.invite_fingerprint()
    if readers != "none":
        listener.observe_artist()
        rig.tick()
        assert room.state is ArtRoomState.CONNECTED
    rig.network.key = loss
    rig.tick()
    lost_state = room.state
    why = "New room changes are not confirmed while the room network is unavailable."
    notes = app.window.session_canvas
    assert notes._current_guidance.why == why
    assert notes._guidance_why.text() == f"Why: {why}"
    assert not rig.invite_fingerprint()
    assert not app.window.art_room_overview._overview.activity_actions
    rig.network.now += 6.0 if readers == "expired" else 1.0
    rig.network.key = "original"
    rig.tick()
    expected = ArtRoomState.CONNECTED if readers == "fresh" else ArtRoomState.WAITING
    assert room.state is expected
    # The baseline falsely restores this overview while the accepted HUD
    # remains FAILED and offers native-only Reset Invite for a LAN room.
    assert app.window.session_hud._action_kind != "reset_invite"
    assert app._last_session_conductor.phase is (
        SessionConductorPhase.CONNECTED if readers == "fresh" else SessionConductorPhase.INVITE_READY
    )
    assert lost_state is ArtRoomState.RECONNECTING
    assert owner.server is listener and owner.credentials is credentials
    assert app.session_conductor.token == token and room.generation == generation
    assert rig.invite_fingerprint() == invite
    assert rig.events == ["start:original"]
    _assert_no_external_work(rig, caplog)


def test_no_address_retry_and_repeated_ticks_keep_the_existing_room(host, qapp, caplog):
    caplog.set_level("INFO")
    rig = host()
    app, owner, room = rig.app, rig.owner, rig.app._room_participant
    listener, credentials = owner.server, owner.credentials
    token, generation = app.session_conductor.token, room.generation
    canvas = app._shared_canvas_coordinator()
    canvas.share(_PRIVATE_CANVAS)
    rig.network.key = "missing"
    rig.tick()
    for _ in range(3):
        _retry(rig, qapp)
        rig.tick()
    assert owner.active and owner.server is listener and owner.credentials is credentials
    assert app.session_conductor.token == token and room.generation == generation
    assert room.state is ArtRoomState.RECONNECTING
    assert app._shared_canvas is canvas and canvas.host_snapshot.shared
    assert rig.events == ["start:original"]
    assert not rig.invite_fingerprint()
    assert rig.launcher.joined == []
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("Art LAN room retry deferred: network unavailable") == 3
    assert messages.count("Art LAN room network interrupted") == 1
    _assert_no_external_work(rig, caplog)


def test_changed_address_retries_stop_before_rebinding_and_rotating_invite(host, qapp, caplog):
    rig = host()
    app, owner = rig.app, rig.owner
    listener = owner.server
    token = app.session_conductor.token
    invite = rig.invite_fingerprint()
    canvas = app._shared_canvas_coordinator()
    canvas.share(_PRIVATE_CANVAS)
    rig.network.key = "changed"
    rig.tick()
    assert owner.server is listener and owner.start_count == 1
    _retry(rig, qapp)
    rig.tick()
    assert rig.events == ["start:original", "stop", "stopped", "start:changed"]
    assert owner.active and owner.server is not listener and listener._httpd.stopping
    assert rig.invite_fingerprint() and rig.invite_fingerprint() != invite
    assert not canvas.bound
    assert not app._shared_canvas_coordinator().host_snapshot.shared
    assert app._room_participant.state is ArtRoomState.WAITING
    assert app._last_session_conductor.phase is SessionConductorPhase.INVITE_READY
    assert app.session_conductor.token != token
    assert app.window.session_hud._action_kind == "copy_invite"
    with rig.monkeypatch.context() as copy_patch:
        copy_patch.setattr(QApplication, "clipboard", lambda: rig.clipboard)
        app.window.session_hud._action.click()
    assert rig.clipboard.value
    assert not rig.launcher.joined
    _assert_no_external_work(rig, caplog)


@pytest.mark.parametrize("outcome", ["false", "none", "exception"])
def test_unconfirmed_rebind_stop_retains_cleanup_and_blocks_stale_copy(
    host, qapp, caplog, outcome,
):
    rig = host()
    app, owner = rig.app, rig.owner
    listener, credentials = owner.server, owner.credentials
    rig.network.key = "changed"
    rig.tick()
    result = {"false": False, "none": None, "exception": OSError(_PRIVATE_STOP)}[outcome]
    owner.stop_outcomes.append(result)
    _retry(rig, qapp, click=False)
    assert app.audio.cleanup_retry_required and app.audio._stop_art_room
    assert app.audio._stop_hosting
    assert owner.server is listener and owner.credentials is credentials
    assert owner.start_count == 1
    rig.network.key = "original"
    rig.tick()
    assert app.window.art_room_overview._overview.phase == "cleanup_required"
    assert not app.window.art_room_overview._overview.activity_actions
    assert not rig.invite_fingerprint()
    with rig.monkeypatch.context() as copy_patch:
        copy_patch.setattr(QApplication, "clipboard", lambda: rig.clipboard)
        app._copy_band_invite()
    assert not rig.clipboard.value
    app._on_conductor_action_requested("retry_startup")
    assert owner.start_count == 1
    end = app.window.session_strip._audio_button
    assert end.isVisibleTo(app.window) and end.isEnabled()
    assert "Try End" in end.accessibleName()
    end.click()
    _drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required and not owner.active
    assert app._room_participant.state is ArtRoomState.NONE
    _assert_no_external_work(rig, caplog)


@pytest.mark.parametrize("failure", ["missing_address", "listener_start"])
def test_initial_start_failure_stays_terminal_until_explicit_retry(host, qapp, caplog, failure):
    rig = host(start=False)
    if failure == "missing_address":
        rig.network.key = "missing"
    else:
        rig.owner.fail_start = True
    assert rig.app.begin_startup_journey()
    rig.tick()
    assert rig.app._room_participant.state is ArtRoomState.FAILED
    assert rig.app._last_session_conductor.phase is SessionConductorPhase.FAILED
    assert not rig.owner.active and not rig.invite_fingerprint()
    rig.network.key = "original"
    for _ in range(3):
        rig.tick()
    assert rig.app._room_participant.state is ArtRoomState.FAILED
    _retry(rig, qapp)
    rig.tick()
    assert rig.owner.active and rig.invite_fingerprint()
    assert rig.app._last_session_conductor.phase is SessionConductorPhase.INVITE_READY
    _assert_no_external_work(rig, caplog)


def test_optional_canvas_route_disappears_and_returns_without_launch(host, qapp, caplog):
    rig = host()
    app = rig.app
    canvas = app._shared_canvas_coordinator()
    canvas.share(_PRIVATE_CANVAS)
    rig.tick()
    assert app.window.art_room_overview._overview.activity_actions == ("canvas",)
    app._open_shared_canvas = Mock(wraps=app._open_shared_canvas)
    rig.network.key = "missing"
    rig.tick()
    view = app.window.art_room_overview._overview
    assert view.phase == "reconnecting" and view.activity_actions == ()
    assert view.conversation_enabled
    app.window.art_room_overview.activity_requested.emit("canvas")
    app._open_shared_canvas.assert_not_called()
    assert not rig.launcher.joined
    rig.network.key = "original"
    rig.tick()
    assert app.window.art_room_overview._overview.activity_actions == ("canvas",)
    app.window.art_room_overview.activity_button().click()
    app._open_shared_canvas.assert_called_once_with()
    assert app._shared_canvas_dialog.isVisible()
    assert app._shared_canvas is canvas and not rig.launcher.joined
    _assert_no_external_work(rig, caplog)


def test_end_during_route_loss_releases_owner_and_never_resurrects(host, qapp, caplog):
    rig = host()
    app = rig.app
    canvas = app._shared_canvas_coordinator()
    canvas.share(_PRIVATE_CANVAS)
    canvas.open_canvas_as_host()
    assert len(rig.launcher.joined) == 1
    notes = app.window.session_canvas.current_notes()
    rig.network.key = "missing"
    rig.tick()
    end = app.window.session_strip._audio_button
    assert end.isVisibleTo(app.window) and end.isEnabled()
    end.click()
    _drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required and not rig.owner.active
    assert not canvas.bound
    rig.network.key = "original"
    rig.tick()
    assert app._room_participant.state is ArtRoomState.NONE
    assert app.window.art_room_overview._overview.phase == "ended"
    assert not rig.invite_fingerprint()
    assert app.window.session_canvas.current_notes() == notes
    assert len(rig.launcher.joined) == 1
    _assert_no_external_work(rig, caplog)


@pytest.mark.parametrize("retirement", ["end", "quit", "replacement"])
def test_reentrant_retirement_during_rebind_cannot_start_or_release_newer_room(
    host, qapp, retirement,
):
    rig = host()
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
            new = ControlledHost(rig.root / "replacement-peer", rig.network, rig.events)
            new.start(rig.network.address, creator_profile_key="art")
            app.host_peer = new
            # Model a completed newer room installation, including its local
            # authority boundary; replacing a pointer alone leaves the old
            # closing room intentionally unable to bind optional media.
            room = app._room_participant
            room.generation += 1
            room.stopping = False
            room.role = "host"
            room.state = ArtRoomState.WAITING
            newer_canvas = app._shared_canvas_coordinator()
            replacement.append((new, newer_canvas))

    owner.before_stop = retire
    _retry(rig, qapp, click=False)
    _drain(qapp, lambda: not app.audio.stopping)
    assert owner.start_count == 1
    if retirement == "replacement":
        new, newer_canvas = replacement[0]
        assert app.host_peer is new and new.active and new.start_count == 1
        assert app._shared_canvas is newer_canvas and newer_canvas.bound
    else:
        assert not owner.active
        assert app._shutdown if retirement == "quit" else app._room_participant.state is ArtRoomState.NONE


def test_route_transition_logs_are_bounded_and_ticks_do_not_republish(host, caplog):
    caplog.set_level("INFO")
    rig = host()
    control = rig.owner.control
    accepted = control.snapshot()
    caplog.clear()
    rig.network.key = "missing"
    for _ in range(5):
        rig.tick()
    rig.network.key = "original"
    for _ in range(5):
        rig.tick()
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("Art LAN room network interrupted") == 1
    assert messages.count("Art LAN room network route restored") == 1
    assert rig.events == ["start:original"]
    assert rig.owner.control is control and control.snapshot() == accepted
    _assert_no_external_work(rig, caplog)


@pytest.mark.parametrize("conversation_open", [False, True])
def test_compact_actual_host_recovery_keeps_retry_and_end_reachable(
    host, qapp, conversation_open,
):
    rig = host()
    app, window = rig.app, rig.app.window
    window.webex_embed.setVisible(conversation_open)
    window.resize(760, 600)
    rig.network.key = "missing"
    rig.tick()
    for _ in range(3):
        qapp.processEvents()
    assert window.size() == QSize(760, 600)
    hud = window.session_hud
    assert hud._status.text() == "Room network interrupted"
    assert hud._action.text() == "Try Again"
    assert hud._action_kind == "retry_startup"
    assert hud._action.isVisibleTo(window) and hud._action.isEnabled()
    detail = QRect(hud._detail.mapTo(hud, QPoint()), hud._detail.size())
    action = QRect(hud._action.mapTo(hud, QPoint()), hud._action.size())
    assert hud.rect().contains(detail) and hud.rect().contains(action)
    assert not detail.intersects(action)
    assert hud._detail.height() >= hud._detail.heightForWidth(hud._detail.width())
    panel = window.art_room_overview
    assert panel.isVisibleTo(window) and panel.horizontalScrollBar().maximum() == 0
    for label in panel._content.findChildren(QLabel):
        if label.isVisibleTo(window):
            assert label.height() >= label.heightForWidth(label.width())
    end = window.session_strip._audio_button
    assert end.isVisibleTo(window) and end.isEnabled()
    assert "End Room" in end.accessibleName()
    assert window.rect().contains(QRect(end.mapTo(window, QPoint()), end.size()))
    hud._action.click()
    qapp.processEvents()
    assert rig.owner.active and rig.events == ["start:original"]
    assert app._room_participant.state is ArtRoomState.RECONNECTING


def test_real_recovery_events_reach_configured_rotating_log(host, qapp, tmp_path):
    logger = logging.getLogger("webjam")
    previous_handlers = list(logger.handlers)
    previous_level, previous_propagate = logger.level, logger.propagate
    # configure_logging reuses a pre-existing file handler. Detach all prior
    # handlers without closing them so this proof cannot touch a user's log.
    for handler in previous_handlers:
        logger.removeHandler(handler)
    rig = None
    try:
        log_path = tmp_path / "isolated-logs" / "webjam.log"
        configured = configure_logging(AppSettings(log_file=str(log_path), log_level="INFO"))
        assert configured is logger
        rotating = [handler for handler in logger.handlers
                    if isinstance(handler, RotatingFileHandler)]
        assert len(rotating) == 1 and Path(rotating[0].baseFilename) == log_path

        rig = host(start=False)
        rig.owner.fail_start = True
        assert rig.app.begin_startup_journey()
        rig.tick()
        assert rig.app._room_participant.state is ArtRoomState.FAILED
        _retry(rig, qapp)
        rig.tick()
        assert rig.owner.active
        private_invite = rig.app._current_invite_url()
        assert private_invite
        rig.app._shared_canvas_coordinator().share(_PRIVATE_CANVAS)

        rig.network.key = "missing"
        for _ in range(5):
            rig.tick()
        _retry(rig, qapp)
        rig.network.key = "original"
        for _ in range(5):
            rig.tick()
        for handler in logger.handlers:
            handler.flush()
        restored_lines = log_path.read_text(encoding="utf-8").splitlines()
        assert sum(line.endswith("Art LAN room network interrupted")
                   for line in restored_lines) == 1
        assert sum(line.endswith("Art LAN room network route restored")
                   for line in restored_lines) == 1
        assert sum(line.endswith("Art LAN room retry deferred: network unavailable")
                   for line in restored_lines) == 1

        rig.network.key = "changed"
        rig.tick()
        rig.owner.stop_outcomes.append(OSError(_PRIVATE_STOP))
        _retry(rig, qapp, click=False)
        assert rig.app.audio.cleanup_retry_required
        for _ in range(5):
            rig.tick()
        for handler in logger.handlers:
            handler.flush()
        payload = log_path.read_text(encoding="utf-8")
        lines = payload.splitlines()
        expected_counts = {
            "Art LAN room listener could not start": 1,
            "Art LAN room network interrupted": 2,
            "Art LAN room network route restored": 1,
            "Art LAN room retry deferred: network unavailable": 1,
            "Art LAN room listener replacement requested": 1,
            "Art LAN room listener cleanup unconfirmed": 1,
        }
        for message, count in expected_counts.items():
            assert sum(line.endswith(message) for line in lines) == count
        forbidden = (
            private_invite, _PRIVATE_NOTES, _PRIVATE_CANVAS, _PRIVATE_START,
            _PRIVATE_STOP, _ADDRESSES["original"], _ADDRESSES["changed"],
        )
        assert not any(secret in payload for secret in forbidden), (
            "A local invitation, draft, address or private error entered the rotating log"
        )
    finally:
        # Release any owned runtime while logging is still isolated, including
        # when an earlier assertion failed. Fixture teardown is then idempotent.
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
