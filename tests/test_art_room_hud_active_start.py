"""W12: connected Art HUD second line names the active start."""

from __future__ import annotations

import os
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core.meeting_companion import art_room_hud_active_start_detail
from core.remote_invitation import issue_remote_invitation
from core.room_state import RoomIdentity, RoomState
from core.session_conductor import ArtRoomState
from core.settings import AppSettings
from core.session_transport import ConnectionQuality, TransportPath
from services.remote_session_runtime import RemoteGuestConnection, RemoteSessionPhase
from services.transport_runtime import TransportEvent
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controllers(qapp, tmp_path):
    made = []

    def create(*, invite=None, profile="music", hosting=False):
        root = tmp_path / str(len(made))
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"),
            takes_directory=str(root / "takes"),
            host_server_enabled=hosting,
            last_creator_profile_key=profile,
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Making room",
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
        assert not isValid(app)
    qapp.processEvents()


def drain(qapp, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert predicate()


@pytest.mark.parametrize(
    ("start_key", "expected"),
    (
        ("talk_and_make", "Make together with your own tools."),
        ("", "Make together with your own tools."),
        (
            "paint_along",
            "Paint along: follow a silent video from a file or lesson link.",
        ),
    ),
)
def test_art_room_hud_active_start_detail_names_start(start_key, expected):
    assert art_room_hud_active_start_detail(start_key) == expected


def test_connected_make_together_host_hud_names_active_start(controllers):
    app = controllers(profile="art", hosting=True)
    app.settings.last_creator_start_key = "talk_and_make"
    app._conductor_setup_requested = True
    app._room_participant.role = "host"
    app._room_participant.state = ArtRoomState.CONNECTED
    app._update_session_hud()
    assert app.window.session_hud._status.text() == "You’re in"
    assert app.window.session_hud._detail.text() == (
        "Make together with your own tools."
    )


def test_connected_paint_along_guest_hud_uses_host_start(controllers):
    app = controllers(profile="art")
    app._conductor_setup_requested = True
    app._room_participant.role = "guest"
    app._room_participant.state = ArtRoomState.CONNECTED
    app._room_participant.borrowed_start = "paint_along"
    app._update_session_hud()
    assert app.window.session_hud._detail.text() == (
        "Paint along: follow a silent video from a file or lesson link."
    )


class RoomBackend:
    instances: list[RoomBackend] = []

    def __init__(self, *, on_room_state, schedule_callback, **kwargs):
        self.receive = on_room_state
        self.schedule = schedule_callback
        self.connection_available = False
        self.room_identity = None
        self.generation = 0
        self.instances.append(self)

    def start_guest(self, invite, *, generation):
        self.generation = generation
        self.room_identity = RoomIdentity.from_invitation(invite)
        self.connection_available = True
        return RemoteGuestConnection(
            43123,
            TransportPath.SECURE_RELAY,
            ConnectionQuality.UNKNOWN,
            generation,
        )

    def emit(self, room):
        event = TransportEvent(
            0,
            "room_state_received",
            code="ok",
            state="connected",
            mode="guest",
            generation=self.generation,
            room_state=room,
        )
        self.schedule(lambda: self.receive(event))

    def stop(self):
        self.connection_available = False
        self.room_identity = None
        return True


def remote():
    return issue_remote_invitation(
        "reference-local",
        allowed_profiles={"reference-local"},
        host_spki_sha256=bytes.fromhex("44" * 32),
    ).invitation


def test_native_paint_along_guest_hud_names_active_start(
    qapp, controllers, monkeypatch,
):
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend",
        RoomBackend,
    )
    app = controllers()
    app._activate_remote_guest_route = mock.Mock()
    assert app.accept_invitation(remote())
    drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
    backend = RoomBackend.instances[-1]
    backend.emit(RoomState(1, "art", "paint_along"))
    drain(qapp, lambda: app.creator_profile.key == "art")
    app._update_session_hud()
    assert app.window.session_hud._detail.text() == (
        "Paint along: follow a silent video from a file or lesson link."
    )
