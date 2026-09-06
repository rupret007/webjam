"""Real Art room controls offer guests only accepted canvas invitations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
from shiboken6 import isValid

from core.art_companion import ArtCompanionProjection, CanvasCompanionState
from core.drawpile import parse_canvas_invite
from core.room_state import RoomIdentity
from core.session_conductor import ArtRoomState
from core.session_transport import SessionRole
from core.settings import AppSettings
from core.shared_canvas import SharedCanvasFollowState, SharedCanvasPendingAction
from services.remote_session_runtime import RemoteSessionPhase, RemoteSessionSnapshot
from tests.test_art_room_controller import RoomBackend, drain, remote
from tests.test_shared_canvas_coordinator import FakeLauncher
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow

PRIVATE_PASSWORD = "PASSWORD_PRIVATE"
PRIVATE_CODE = "INVITEPRIVATE"
FIRST = f"drawpile://studio.example/lesson-one:{PRIVATE_CODE}?v1&p={PRIVATE_PASSWORD}"
LONG_HOST = "a" * 50 + "." + "b" * 50
SECOND = f"drawpile://{LONG_HOST}/lesson-two:{PRIVATE_CODE}?v1&p={PRIVATE_PASSWORD}"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def room_pair(qapp, monkeypatch, tmp_path):
    apps = []
    launchers = {}

    def launcher_for(settings):
        return launchers.setdefault(id(settings), FakeLauncher())

    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", launcher_for)
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)

    def app_for(name, profile, hosting=False):
        root = tmp_path / name
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            last_creator_profile_key=profile, host_server_enabled=hosting,
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Art room",
        )
        app = ApplicationController(window, settings=settings)
        apps.append(app)
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        app.bridge.launch_webex = Mock()
        app.window.flash_message = Mock()
        return app

    def create(profile="music"):
        guest = app_for("guest", profile)
        invite = remote()
        assert guest.accept_invitation(invite)
        drain(qapp, lambda: guest._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
        backend = RoomBackend.instances[-1]
        host = app_for("host", "art", hosting=True)
        host._room_participant.role = "host"
        host._room_participant.state = ArtRoomState.CONNECTED
        accepted = []
        policy = {"reject": ""}

        def publish(value):
            if policy["reject"] == "false":
                return False
            if policy["reject"] == "raise":
                raise RuntimeError(f"PRIVATE_PUBLISH_DETAIL {value.shared_canvas.join_url}")
            accepted.append(value)
            backend.emit(value)
            return True

        owner = SimpleNamespace(
            room_identity=RoomIdentity.from_invitation(invite),
            invitation_available=False, connection_available=True,
            snapshot=RemoteSessionSnapshot(RemoteSessionPhase.CONNECTED, SessionRole.HOST, 1),
            publish_room_state=publish, stop=lambda: True,
        )
        host._remote_invite_owner = host._remote_session = owner
        publisher = host._room_host_publisher()
        assert publisher.publish()
        drain(qapp, lambda: guest.creator_profile.key == "art")
        for app in (host, guest):
            app.window.resize(720, 560)
            app.window.show()
            app._tick_creator_start()
        host._open_shared_canvas()
        panel = host._shared_canvas_dialog
        QTest.mouseClick(panel._chip, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert panel._invite_input.isVisible()
        assert panel._invite_input.echoMode() is QLineEdit.EchoMode.Password
        return SimpleNamespace(
            host=host, guest=guest, owner=owner, publisher=publisher,
            accepted=accepted, policy=policy, panel=panel,
            host_launcher=launcher_for(host.settings), guest_launcher=launcher_for(guest.settings),
        )

    yield create
    for app in reversed(apps):
        qapp.processEvents()
        assert app.shutdown()
        window = app.window
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(window)
    qapp.processEvents()


def _share(pair, qapp, invitation):
    assert pair.panel._invite_input.isVisible()
    pair.panel._invite_input.setText(invitation)
    QTest.mouseClick(pair.panel._chip, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    pair.host._tick_creator_start()
    assert pair.panel._invite_input.text() == ""


def _guest_canvas(pair):
    return pair.guest._shared_canvas_coordinator()


def _await_offer(pair, qapp, invitation):
    expected = parse_canvas_invite(invitation)
    drain(qapp, lambda: (
        pair.guest._room_participant.native_state.shared_canvas.join_url == expected.join_url
    ))
    assert _guest_canvas(pair).follow_snapshot.session_label == expected.session_label


def _open_guest_canvas(pair, qapp):
    pair.guest._tick_creator_start()
    pair.guest.window.art_room_overview.activity_requested.emit("canvas")
    panel = pair.guest._shared_canvas_dialog
    assert panel is not None and panel._chip.isEnabled()
    QTest.mouseClick(panel._chip, Qt.MouseButton.LeftButton)
    qapp.processEvents()


def _assert_private_state(pair, caplog):
    for app in (pair.host, pair.guest):
        public = app.art_room_state()
        assert ArtCompanionProjection(**public.to_public_dict()) == public
        text = repr(public) + repr(app.window.art_room_overview._overview)
        text += str(app.window.flash_message.call_args_list)
        for private in (PRIVATE_PASSWORD, PRIVATE_CODE, "PRIVATE_PUBLISH_DETAIL"):
            assert private not in text
        app._launch_native_jamulus_for_startup.assert_not_called()
        app._start_hosted_server_for_startup.assert_not_called()
        app.bridge.launch_webex.assert_not_called()
        assert app._room_participant.state is ArtRoomState.CONNECTED
    for private in (PRIVATE_PASSWORD, PRIVATE_CODE, "PRIVATE_PUBLISH_DETAIL"):
        assert private not in caplog.text


@pytest.mark.parametrize("profile", ["music", "art"])
def test_long_invitation_reaches_guest_unchanged_only_explicit_open_launches(
    room_pair, qapp, caplog, profile,
):
    pair = room_pair(profile)
    _share(pair, qapp, SECOND)
    _await_offer(pair, qapp, SECOND)
    assert pair.panel._invite_input.text() == ""
    assert pair.host_launcher.joined == pair.guest_launcher.joined == []
    assert pair.publisher.canvas.join_url == parse_canvas_invite(SECOND).join_url
    assert len(pair.publisher.canvas.server_label) <= 80
    assert pair.host._shared_canvas.host_snapshot.pending_action is SharedCanvasPendingAction.NONE
    _open_guest_canvas(pair, qapp)
    assert pair.guest_launcher.joined == [parse_canvas_invite(SECOND).join_url]
    assert pair.guest.settings.last_creator_profile_key == profile
    _assert_private_state(pair, caplog)


@pytest.mark.parametrize("failure", ["false", "raise"])
@pytest.mark.parametrize("intent", ["share", "replace", "withdraw"])
def test_canvas_retry_controls_keep_host_and_guest_on_accepted_room_truth(
    room_pair, qapp, caplog, failure, intent,
):
    pair = room_pair()
    if intent != "share":
        _share(pair, qapp, FIRST)
        _await_offer(pair, qapp, FIRST)
    old_canvas = pair.publisher.canvas
    old_owner = pair.guest._room_participant.native_source
    old_generation = pair.guest._room_participant.generation
    pair.policy["reject"] = failure
    if intent == "withdraw":
        QTest.mouseClick(pair.panel._quiet, Qt.MouseButton.LeftButton)
    else:
        if intent == "replace":
            # The actual Change action reveals the masked field without
            # withdrawing the canvas artists already have.
            QTest.mouseClick(pair.panel._change_button, Qt.MouseButton.LeftButton)
        _share(pair, qapp, SECOND)
    qapp.processEvents()
    pair.host._tick_creator_start()
    pending = pair.host._shared_canvas.host_snapshot
    expected = SharedCanvasPendingAction.WITHDRAW if intent == "withdraw" else SharedCanvasPendingAction.SHARE
    assert pending.pending_action is expected and pending.can_retry_publication
    assert pair.publisher.canvas == old_canvas
    assert pending.shared == (intent != "share")
    assert "try" in pair.panel._chip.text().casefold()
    assert pair.panel._chip.isVisible() and pair.panel._chip.isEnabled()
    projection = pair.host.art_room_state()
    assert projection.canvas is (
        CanvasCompanionState.WITHDRAW_PENDING if intent == "withdraw" else CanvasCompanionState.SHARE_PENDING
    )
    overview = pair.host.window.art_room_overview._overview
    assert "retry" in overview.activity_label.casefold()
    assert overview.activity_action == "canvas" and overview.activity_enabled
    assert pair.host_launcher.joined == pair.guest_launcher.joined == []

    # A later full publication must not leak a rejected canvas candidate.
    pair.policy["reject"] = ""
    pair.publisher.publish()
    qapp.processEvents()
    assert pair.accepted[-1].shared_canvas == old_canvas
    if intent != "share":
        _open_guest_canvas(pair, qapp)
        assert pair.guest_launcher.joined[-1] == parse_canvas_invite(FIRST).join_url
    else:
        assert _guest_canvas(pair).follow_snapshot.state is SharedCanvasFollowState.NO_CANVAS

    QTest.mouseClick(pair.panel._chip, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    pair.host._tick_creator_start()
    assert pair.host._shared_canvas.host_snapshot.pending_action is SharedCanvasPendingAction.NONE
    if intent == "withdraw":
        drain(qapp, lambda: _guest_canvas(pair).follow_snapshot.state is SharedCanvasFollowState.NO_CANVAS)
        assert not pair.publisher.canvas.shared
    else:
        _await_offer(pair, qapp, SECOND)
        _open_guest_canvas(pair, qapp)
        assert pair.guest_launcher.joined[-1] == parse_canvas_invite(SECOND).join_url
    assert pair.guest._room_participant.native_source is old_owner
    assert pair.guest._room_participant.generation == old_generation
    _assert_private_state(pair, caplog)
