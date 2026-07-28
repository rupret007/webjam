from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.reference_track import (  # noqa: E402
    ReferenceTrackCapability,
    ReferenceTrackSnapshot,
    ReferenceTrackState,
)
from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _controller(*, host: bool) -> ApplicationController:
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Reference Track Test",
    )
    return ApplicationController(
        window,
        settings=AppSettings(host_server_enabled=host),
    )


def _snapshot(state: ReferenceTrackState) -> ReferenceTrackSnapshot:
    return ReferenceTrackSnapshot(
        state=state,
        capability=ReferenceTrackCapability(
            True,
            "macos",
            "Isolated route ready.",
            "BlackHole 16ch",
        ),
        source_name="Reference.wav",
        duration_s=60.0,
        position_s=5.0,
        route_detail="BlackHole 16ch",
    )


class _FakeReferenceTrack:
    def __init__(self, state: ReferenceTrackState = ReferenceTrackState.READY):
        self.snapshot = _snapshot(state)
        self.contexts = []
        self.refreshes = []
        self.loaded = []
        self.stops = 0
        self.closed = 0
        self.play_entered = threading.Event()
        self.release_play = threading.Event()
        self.block_play = False

    def refresh_capability(self, audience_bridge_active=False):
        self.refreshes.append(bool(audience_bridge_active))
        return self.snapshot

    def load(self, path):
        self.loaded.append(path)
        return self.snapshot

    def play(self, context):
        self.contexts.append(context)
        self.play_entered.set()
        if self.block_play:
            assert self.release_play.wait(timeout=3.0)
        self.snapshot = _snapshot(ReferenceTrackState.PLAYING)
        return self.snapshot

    def pause(self):
        self.snapshot = _snapshot(ReferenceTrackState.PAUSED)
        return self.snapshot

    def restart(self):
        self.snapshot = _snapshot(ReferenceTrackState.PLAYING)
        return self.snapshot

    def stop(self):
        self.stops += 1
        self.snapshot = _snapshot(ReferenceTrackState.READY)
        return self.snapshot

    def handle_session_end(self):
        self.stops += 1
        self.snapshot = _snapshot(ReferenceTrackState.READY)
        return self.snapshot

    def refresh_health(self):
        return self.snapshot

    def close(self):
        self.closed += 1
        self.snapshot = _snapshot(ReferenceTrackState.CLOSED)
        return self.snapshot


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_reference_track_menu_is_host_only() -> None:
    guest = _controller(host=False)
    host = _controller(host=True)
    try:
        assert guest.window.session_strip._reference_track_action.isVisible() is False
        assert host.window.session_strip._reference_track_action.isVisible() is True

        guest.window.flash_message = MagicMock()
        guest._on_rail_view_changed("reference_track")
        guest.window.flash_message.assert_called_once()
        assert guest._reference_track is None
    finally:
        guest.shutdown()
        host.shutdown()


def test_host_panel_renders_controller_snapshot_without_starting_audio() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    try:
        controller._open_reference_track()

        dialog = controller._reference_track_dialog
        assert dialog is not None
        assert dialog._source.text() == "Reference.wav"
        assert fake.contexts == []
        assert fake.refreshes == [False]
    finally:
        controller.shutdown()


def test_play_builds_ephemeral_separate_client_context_off_ui_thread() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._jamulus_connected = True
    primary_process = MagicMock()
    primary_process.pid = 4242
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    try:
        with (
            patch.object(
                controller.bridge,
                "find_reference_track_jamulus",
                return_value=(
                    "/Applications/WebJam.app/Contents/Resources/"
                    "JamulusHeadlessClient.app/Contents/MacOS/"
                    "JamulusHeadlessClient"
                ),
            ),
            patch.object(
                controller.bridge,
                "effective_server",
                return_value="127.0.0.1:22124",
            ),
            patch.object(
                controller,
                "_reference_track_primary_device_names",
                return_value=("Built-in Microphone", "Built-in Output"),
            ),
        ):
            controller._play_reference_track()
            assert fake.play_entered.wait(timeout=3.0)

        assert len(fake.contexts) == 1
        context = fake.contexts[0]
        assert context.server_address == "127.0.0.1:22124"
        assert context.primary_udp_port == controller.settings.jamulus_port
        assert context.primary_rpc_port == controller.settings.jamulus_rpc_port
        assert context.primary_process_id == 4242
        assert context.jamulus_binary.endswith(
            "JamulusHeadlessClient.app/Contents/MacOS/JamulusHeadlessClient"
        )
        assert context.primary_input_device_name == "Built-in Microphone"
        assert context.primary_output_device_name == "Built-in Output"
        assert context.audience_bridge_active is False
    finally:
        controller._jamulus_connected = False
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_session_end_cancels_a_late_reference_route_before_it_can_persist() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    fake.block_play = True
    controller._reference_track = fake
    controller._jamulus_connected = True
    primary_process = MagicMock()
    primary_process.pid = 4243
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    try:
        with (
            patch.object(
                controller.bridge,
                "find_reference_track_jamulus",
                return_value="/Applications/WebJam.app/JamulusHeadlessClient",
            ),
            patch.object(
                controller.bridge,
                "effective_server",
                return_value="127.0.0.1:22124",
            ),
        ):
            controller._play_reference_track()
            assert fake.play_entered.wait(timeout=3.0)
            controller._stop_reference_track_for_session_end(background=True)
            fake.release_play.set()

        assert _wait_until(lambda: fake.stops >= 1)
    finally:
        fake.release_play.set()
        controller._jamulus_connected = False
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_play_refuses_without_a_live_owned_primary_jamulus_pid() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._jamulus_connected = True
    controller.window.flash_message = MagicMock()
    try:
        with (
            patch.object(
                controller.bridge,
                "find_reference_track_jamulus",
                return_value="/Applications/WebJam.app/JamulusHeadlessClient",
            ),
            patch.object(
                controller.bridge,
                "effective_server",
                return_value="127.0.0.1:22124",
            ),
        ):
            controller._play_reference_track()

        assert fake.contexts == []
        message = controller.window.flash_message.call_args.args[0]
        assert "active primary Jamulus process" in message
    finally:
        controller._jamulus_connected = False
        controller.shutdown()


def test_audio_stop_orders_reference_before_primary_jamulus() -> None:
    controller = _controller(host=False)
    order: list[str] = []
    controller._stop_reference_track_for_session_end = MagicMock(
        side_effect=lambda **_kwargs: order.append("reference") or True
    )
    controller.bridge.stop_jamulus = MagicMock(
        side_effect=lambda: order.append("primary") or True
    )
    controller._ui_invoker.invoke = lambda callback: callback()
    try:
        controller.audio._stop_session_services(hosting=False)
        assert order[:2] == ["reference", "primary"]
        controller._stop_reference_track_for_session_end.assert_called_once_with(
            background=False
        )
    finally:
        controller.shutdown()


def test_audio_stop_does_not_hide_unproved_reference_teardown() -> None:
    controller = _controller(host=False)
    controller._stop_reference_track_for_session_end = MagicMock(return_value=False)
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller._ui_invoker.invoke = lambda callback: callback()
    controller.window.flash_message = MagicMock()
    try:
        controller.audio._stop_session_services(hosting=False)

        controller.bridge.stop_jamulus.assert_not_called()
        message = controller.window.flash_message.call_args.args[0]
        assert "Reference Track client did not stop cleanly" in message
    finally:
        controller._stop_reference_track_for_session_end = MagicMock(return_value=True)
        controller.shutdown()


def test_roster_loss_retires_an_active_reference_track() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack(ReferenceTrackState.PLAYING)
    controller._reference_track = fake
    controller._jamulus_connected = True
    try:
        with patch.object(
            controller,
            "_stop_reference_track_for_session_end",
        ) as stop:
            controller._apply_jamulus_participants([])
        stop.assert_called_once_with(background=True)
    finally:
        controller._jamulus_connected = False
        controller.shutdown()


def test_shutdown_closes_reference_before_primary_music_client() -> None:
    controller = _controller(host=False)
    order: list[str] = []
    fake = _FakeReferenceTrack()
    fake.close = MagicMock(
        side_effect=lambda: (
            order.append("reference") or _snapshot(ReferenceTrackState.CLOSED)
        )
    )
    controller._reference_track = fake
    controller.bridge.stop_jamulus = MagicMock(
        side_effect=lambda: order.append("primary") or True
    )

    assert controller.shutdown() is True
    assert order[:2] == ["reference", "primary"]


def test_shutdown_stays_open_when_reference_process_death_is_unproved() -> None:
    controller = _controller(host=False)
    fake = _FakeReferenceTrack(ReferenceTrackState.FAILED)
    fake.close = MagicMock(
        return_value=ReferenceTrackSnapshot(
            state=ReferenceTrackState.FAILED,
            capability=fake.snapshot.capability,
            source_name="Reference.wav",
            error=(
                "Reference Track couldn't confirm that its owned Jamulus "
                "client stopped."
            ),
        )
    )
    controller._reference_track = fake
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    try:
        with patch(
            "webjam_qt.controllers.application_controller.QMessageBox.information"
        ) as information:
            assert controller.shutdown() is False

        assert controller._shutdown is False
        controller.bridge.stop_jamulus.assert_not_called()
        information.assert_called_once()
        assert "still stopping" in information.call_args.args[1]
    finally:
        controller._reference_track = None
        controller.shutdown()
