from __future__ import annotations

import os
import time
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.remote_invitation import issue_remote_invitation  # noqa: E402
from core.session_transport import ConnectionQuality, SessionRole, TransportPath  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from services.remote_session_runtime import (  # noqa: E402
    RemoteGuestConnection,
    RemoteSessionPhase,
    RemoteSessionSnapshot,
)
from services.transport_runtime import TransportEvent  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _drain(qapp, predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert predicate()


def _app(tmp_path):
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Offline help fixture",
    )
    with mock.patch.object(ApplicationController, "_start_routing_scan"):
        controller = ApplicationController(
            window,
            settings=AppSettings(config_file=str(tmp_path / "settings.json")),
        )
    controller.begin_startup_journey = mock.Mock()
    return controller, window


def test_default_door_has_no_help_preview(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("WEBJAM_ENABLE_REFERENCE_LOCAL", raising=False)
    controller, window = _app(tmp_path)
    try:
        assert not controller._room_help_enabled
        assert window._room_help_button.isHidden()
        window._show_room_help()
        assert not window._room_help_dialog.isVisible()
        assert not controller._room_help_timer.isActive()
    finally:
        controller.shutdown()
        window.deleteLater()


def test_guest_help_works_before_jamulus_without_notes_or_delivery_fiction(
    qapp, tmp_path, monkeypatch, caplog
):
    from core.room_state import RoomIdentity, RoomState

    monkeypatch.setenv("WEBJAM_ENABLE_REFERENCE_LOCAL", "1")
    backends = []

    class Backend:
        def __init__(self, *, on_help, schedule_help_callback, on_room_state,
                     on_connection_lost, schedule_callback):
            self.on_room_state = on_room_state
            self.on_connection_lost = on_connection_lost
            self.schedule_callback = schedule_callback
            self.connection_available = False
            self.room_identity = None
            self.on_help = on_help
            self.schedule_help_callback = schedule_help_callback
            self.help_available = False
            self.generation = 0
            self.sent = []
            backends.append(self)

        def start_guest(self, invitation, *, generation):
            self.generation = generation
            self.help_available = True
            self.connection_available = True
            self.room_identity = RoomIdentity.from_invitation(invitation)
            # A proved IPC message may arrive before the UI receives its
            # CONNECTED snapshot. It must be staged, not lost or shown early.
            self.on_help(self.event("help_received", text="fixture first hello", request_id=20))
            # The authenticated host profile selects the Music route without
            # requiring Jamulus. Preserve the early-help ordering above.
            self.schedule_callback(lambda: self.on_room_state(TransportEvent(
                event_id=0, event_type="room_state_received", code="ok",
                state="connected", mode="guest", generation=generation,
                profile_id="reference-local", room_state=RoomState(1, "music"),
            )))
            return RemoteGuestConnection(
                43123, TransportPath.SECURE_RELAY,
                ConnectionQuality.UNKNOWN, generation,
            )

        def event(self, kind, *, text="", request_id=23):
            return TransportEvent(
                event_id=request_id if kind == "help_accepted" else 0,
                event_type=kind, code="ok", state="connected", mode="guest",
                generation=self.generation, profile_id="reference-local",
                request_id=request_id, help_text=text,
            )

        def send_help(self, text, *, expected_generation=None):
            assert expected_generation == self.generation
            assert self.help_available
            self.sent.append(text)
            # The real sidecar can acknowledge before the sending worker
            # returns its local acceptance. Exercise that ordering end-to-end.
            self.schedule_help_callback(
                lambda: self.on_help(self.event("help_delivered"))
            )
            return self.event("help_accepted")

        def stop(self):
            self.help_available = False
            self.connection_available = False
            self.room_identity = None

    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", Backend
    )
    controller, window = _app(tmp_path)
    controller.jamulus.send_chat = mock.Mock(return_value=False)
    panel = window.room_help
    try:
        assert not window._room_help_button.isHidden()
        assert not panel._input.isEnabled()
        invitation = issue_remote_invitation(
            "reference-local", allowed_profiles={"reference-local"},
            host_spki_sha256=bytes.fromhex("44" * 32),
        ).invitation
        assert controller.accept_invitation(invitation)
        _drain(qapp, lambda: panel._input.isEnabled())
        _drain(qapp, lambda: "fixture first hello" in panel._messages.toPlainText())
        assert controller.bridge.jamulus_process is None
        assert controller._remote_session.help_available
        _drain(qapp, lambda: not controller._room_participant.probing)
        assert controller.creator_profile.key == "music"
        assert controller._room_participant.native_state == RoomState(1, "music")
        window._show_room_help()
        assert window._room_help_dialog.isVisible()
        assert not window._room_help_dialog.isModal()
        panel._input.setText("fixture setup question")
        panel._submit()
        _drain(qapp, lambda: "Peer acknowledged" in panel._messages.toPlainText())
        assert "not a read receipt" in panel._messages.toPlainText()
        assert backends[0].sent == ["fixture setup question"]
        assert panel.draft_text() == ""
        backends[0].on_help(
            backends[0].event("help_received", text="fixture peer answer", request_id=30)
        )
        _drain(qapp, lambda: "fixture peer answer" in panel._messages.toPlainText())
        assert "fixture" not in window.session_canvas.current_notes()
        controller.jamulus.send_chat.assert_not_called()
        assert "fixture setup question" not in caplog.text
        assert "fixture peer answer" not in caplog.text

        panel._input.setText("must disappear on leave")
        assert controller._stop_remote_transport()
        _drain(qapp, lambda: panel._messages.toPlainText() == "")
        assert panel.draft_text() == ""
        assert not panel._input.isEnabled()
        backends[0].on_help(
            backends[0].event("help_received", text="retired peer answer", request_id=31)
        )
        qapp.processEvents()
        assert panel._messages.toPlainText() == ""
    finally:
        controller.shutdown()
        window._room_help_dialog.close()
        window.deleteLater()


def test_host_reset_rearms_first_message_and_ignores_old_failure(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("WEBJAM_ENABLE_REFERENCE_LOCAL", "1")
    controller, window = _app(tmp_path)

    class Owner:
        help_available = True
        snapshot = RemoteSessionSnapshot(
            RemoteSessionPhase.CONNECTED, SessionRole.HOST, 1
        )

        def send_help(self, text, *, expected_generation=None):
            raise AssertionError("This fixture never sends")

        def reset(self):
            self.snapshot = RemoteSessionSnapshot(
                RemoteSessionPhase.PREPARING, SessionRole.HOST, 2
            )
            self.help_available = False

        def stop(self):
            self.help_available = False

    owner = Owner()
    controller._remote_session = owner
    controller._remote_invite_owner = owner
    controller._room_help.update(owner, owner.snapshot)
    try:
        window.room_help._input.setText("private prior room draft")
        with mock.patch.object(controller, "_update_session_hud"):
            controller._reset_remote_invite()
        assert window.room_help.draft_text() == ""
        owner.snapshot = RemoteSessionSnapshot(
            RemoteSessionPhase.CONNECTED, SessionRole.HOST, 2
        )
        owner.help_available = True
        controller._room_help.receive(
            TransportEvent(
                event_id=0, event_type="help_received", code="ok",
                state="connected", mode="host", generation=2,
                profile_id="reference-local", request_id=1,
                help_text="new room first message",
            ),
            source=owner,
        )
        with mock.patch.object(controller, "_show_remote_session_failure") as failure:
            controller._on_remote_session_snapshot(
                RemoteSessionSnapshot(RemoteSessionPhase.FAILED, SessionRole.HOST, 1),
                source=owner,
            )
            failure.assert_not_called()
        with (
            mock.patch.object(controller, "_mark_remote_band_check_path"),
            mock.patch.object(controller, "_update_session_hud"),
        ):
            controller._on_remote_session_snapshot(owner.snapshot, source=owner)
        _drain(qapp, lambda: "new room first message" in window.room_help._messages.toPlainText())
        assert window.room_help.draft_text() == ""
        assert "private prior" not in window.room_help._messages.toPlainText()
    finally:
        controller._remote_session = None
        controller._remote_invite_owner = None
        controller.shutdown()
        window.deleteLater()
