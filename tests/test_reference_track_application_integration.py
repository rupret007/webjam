from __future__ import annotations

import math
import os
import threading
import time
from types import SimpleNamespace
import uuid
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.jamulus_rpc_client import (  # noqa: E402
    JamulusRpcMonitorIdentity,
    JamulusRpcMonitorSnapshot,
)
from core.reference_track import (  # noqa: E402
    ReferenceTrackCapability,
    ReferenceTrackSnapshot,
    ReferenceTrackState,
)
from core.session_transfer import (  # noqa: E402
    RecordingSignal,
    SessionStateSnapshot,
    SharedTrackPlaybackState,
    SharedTrackSessionSnapshot,
)
from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402
from webjam_qt.windows.reference_track import ReferenceTrackPrimaryGate  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _controller(*, host: bool) -> ApplicationController:
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Shared Track Test",
    )
    return ApplicationController(
        window,
        settings=AppSettings(host_server_enabled=host),
    )


def _set_primary_rpc(
    controller: ApplicationController,
    *,
    available: bool = True,
    age: float = 0.0,
) -> MagicMock:
    rpc = MagicMock()
    rpc.available = available
    rpc.last_activity_age.return_value = age
    controller.jamulus.rpc_client = rpc
    if (
        getattr(controller.bridge, "jamulus_process", None) is not None
        and int(
            getattr(controller.bridge, "_jamulus_process_generation", 0)
        )
        <= 0
    ):
        controller.bridge._jamulus_process_generation_counter = 1
        controller.bridge._jamulus_process_generation = 1
    if getattr(controller.bridge, "jamulus_process", None) is not None:
        controller.bridge.jamulus_launch_intended = True

    def monitor_snapshot_for(
        *,
        process_generation: int,
        process_id: int,
    ) -> JamulusRpcMonitorSnapshot:
        observed_age = rpc.last_activity_age()
        usable_age = bool(
            isinstance(observed_age, (int, float))
            and not isinstance(observed_age, bool)
            and math.isfinite(float(observed_age))
            and float(observed_age) >= 0.0
        )
        return JamulusRpcMonitorSnapshot(
            identity=JamulusRpcMonitorIdentity(
                monitor_epoch=1,
                process_generation=process_generation,
                process_id=process_id,
            ),
            running=True,
            available=bool(rpc.available),
            authenticated=bool(rpc.available),
            last_activity_at=(
                time.monotonic() - float(observed_age)
                if usable_age
                else None
            ),
            last_activity_age_seconds=(
                float(observed_age) if usable_age else None
            ),
        )

    controller.jamulus.rpc_monitor_snapshot_for = MagicMock(
        side_effect=monitor_snapshot_for
    )
    recovery = controller._primary_jamulus_recovery_snapshot()
    if (
        recovery is not None
        and recovery.process_alive
        and recovery.process_id > 0
        and recovery.rpc_freshness.value == "fresh"
    ):
        controller._record_primary_local_roster_proof(recovery)
    return rpc


def _primary_source_identity(
    controller: ApplicationController,
    *,
    monitor_epoch: int = 1,
) -> JamulusRpcMonitorIdentity:
    recovery = controller._primary_jamulus_recovery_snapshot()
    assert recovery is not None
    return JamulusRpcMonitorIdentity(
        monitor_epoch=monitor_epoch,
        process_generation=recovery.generation,
        process_id=recovery.process_id,
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


def _locked_local_snapshot() -> ReferenceTrackSnapshot:
    return ReferenceTrackSnapshot(
        state=ReferenceTrackState.READY,
        capability=ReferenceTrackCapability(
            False,
            "macos",
            "Shared Track needs the official BlackHole 16ch or 64ch "
            "device at 48 kHz.",
            backend="blackhole",
            reason_code="physical_certification_required",
        ),
        source_name="Taylor Swift - The Fate of Ophelia.mp3",
        duration_s=90.0,
        position_s=3.0,
        source_format="MP3",
    )


class _FakeReferenceTrack:
    def __init__(self, state: ReferenceTrackState = ReferenceTrackState.READY):
        self.snapshot = _snapshot(state)
        self.contexts = []
        self.refreshes = []
        self.loaded = []
        self.stops = 0
        self.restarts = 0
        self.closed = 0
        self.play_entered = threading.Event()
        self.release_play = threading.Event()
        self.block_play = False
        self.block_refresh = False
        self.refresh_entered = threading.Event()
        self.release_refresh = threading.Event()
        self.cancelled_starts = 0

    def refresh_capability(self, audience_bridge_active=False):
        self.refreshes.append(bool(audience_bridge_active))
        self.refresh_entered.set()
        if self.block_refresh:
            assert self.release_refresh.wait(timeout=3.0)
        return self.snapshot

    def cancel_pending_start(self):
        self.cancelled_starts += 1
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
        self.restarts += 1
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

    def public_diagnostics(self):
        return {
            "playback_state": self.snapshot.state.value,
            "source_state": "loaded",
            "source_format": "WAV",
            "source_sample_rate_hz": 48_000,
            "source_channels": 2,
            "source_duration_s": 60.0,
            "route_available": True,
            "route_platform": "macos",
            "route_backend": "blackhole",
            "route_reason": "ready",
            "route_active": self.snapshot.active,
            "cleanup_pending": bool(
                getattr(self.snapshot, "cleanup_pending", False)
            ),
        }

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
        assert guest.window.session_strip._reference_track_button.isHidden() is True
        assert host.window.session_strip._reference_track_button.isHidden() is False

        guest.window.flash_message = MagicMock()
        guest._on_rail_view_changed("reference_track")
        guest.window.flash_message.assert_called_once()
        assert guest._reference_track is None
    finally:
        guest.shutdown()
        host.shutdown()


def test_loaded_song_without_a_route_opens_shared_track_setup() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    fake.snapshot = _locked_local_snapshot()
    controller._reference_track = fake
    try:
        controller._render_reference_track_snapshot(fake.snapshot)
        dialog = controller._reference_track_dialog
        assert dialog is not None
        assert dialog.isVisible()
        assert dialog._blackhole_setup.isHidden() is False
        assert dialog._blackhole_setup.text() == "Set Up Shared Track…"
        assert "Needs attention" not in dialog._status.text()
        assert (
            controller.window.session_strip._shared_track_state.text()
            == "Set up the audio device"
        )
        assert (
            controller.window.session_strip._reference_track_button.text()
            == "Set up the audio device"
        )
        assert _wait_until(lambda: fake.refreshes == [False])
        assert _wait_until(lambda: dialog._recheck_route.text() == "Recheck Route")
    finally:
        controller.shutdown()


def test_play_when_route_is_locked_opens_setup_instead_of_failing() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    fake.snapshot = _locked_local_snapshot()
    controller._reference_track = fake
    try:
        controller._play_reference_track()
        assert fake.contexts == []
        dialog = controller._reference_track_dialog
        assert dialog is not None
        assert dialog.isVisible()
        assert dialog._blackhole_setup.text() == "Set Up Shared Track…"
    finally:
        controller.shutdown()


def test_host_panel_renders_controller_snapshot_without_starting_audio() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    try:
        controller._open_reference_track()

        dialog = controller._reference_track_dialog
        assert dialog is not None
        assert dialog._source.text() == "Reference.wav"
        assert dialog._play.isEnabled() is False
        assert "waiting for a verified primary Jamulus control connection" in (
            dialog._status.text()
        )
        assert fake.contexts == []
        assert _wait_until(lambda: fake.refreshes == [False])
        assert _wait_until(lambda: dialog._recheck_route.isEnabled())

        controller._jamulus_connected = True
        controller._render_reference_track_snapshot(fake.snapshot)
        assert dialog._play.isEnabled() is False

        primary_process = MagicMock()
        primary_process.pid = 4241
        primary_process.poll.return_value = None
        controller.bridge.jamulus_process = primary_process
        _set_primary_rpc(controller)
        controller._render_reference_track_snapshot(fake.snapshot)
        assert dialog._play.isEnabled() is True

        dialog._recheck_route.click()
        assert _wait_until(lambda: fake.refreshes == [False, False])
    finally:
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_enabled_restart_button_invokes_controller_once() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack(ReferenceTrackState.PLAYING)
    controller._reference_track = fake
    controller._jamulus_connected = True
    primary_process = MagicMock()
    primary_process.pid = 4247
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    _set_primary_rpc(controller)
    try:
        controller._open_reference_track()
        dialog = controller._reference_track_dialog
        assert dialog is not None
        controller._render_reference_track_snapshot(fake.snapshot)
        assert dialog._primary_gate is ReferenceTrackPrimaryGate.READY
        assert dialog._restart.isEnabled() is True

        dialog._restart.click()

        assert _wait_until(lambda: fake.restarts == 1)
        assert _wait_until(
            lambda: not controller._reference_track_operation_inflight
        )
        assert fake.restarts == 1
    finally:
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_open_panel_reports_host_authority_loss_separately() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._jamulus_connected = True
    primary_process = MagicMock()
    primary_process.pid = 4248
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    _set_primary_rpc(controller)
    try:
        controller._open_reference_track()
        dialog = controller._reference_track_dialog
        assert dialog is not None
        assert dialog._primary_gate is ReferenceTrackPrimaryGate.READY
        assert dialog._play.isEnabled() is True

        controller.settings.host_server_enabled = False
        controller._render_reference_track_snapshot(fake.snapshot)

        assert dialog._primary_gate is ReferenceTrackPrimaryGate.HOST_REQUIRED
        assert dialog._play.isEnabled() is False
        assert "only to the host" in dialog._status.text()
        assert "primary Jamulus connection" not in dialog._status.text()
    finally:
        controller.bridge.jamulus_process = None
        controller.shutdown()


@pytest.mark.parametrize(
    ("available", "age"),
    ((False, 0.0), (True, float("inf"))),
)
def test_host_roster_cannot_unlock_play_before_fresh_client_rpc(
    available: bool,
    age: float,
) -> None:
    from jamulus_controller import JamulusParticipant

    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    primary_process = MagicMock()
    primary_process.pid = 4249
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    controller.bridge.jamulus_launch_intended = True
    rpc = _set_primary_rpc(controller, available=available, age=age)
    local_roster = [
        JamulusParticipant(channel_id=3, name="Host", is_local=True)
    ]
    try:
        controller._apply_jamulus_participants(
            local_roster,
            source_identity=_primary_source_identity(controller),
        )
        controller._open_reference_track()
        dialog = controller._reference_track_dialog
        assert dialog is not None

        assert controller._jamulus_connected is False
        assert dialog._primary_gate is ReferenceTrackPrimaryGate.NOT_CONNECTED
        assert dialog._play.isEnabled() is False

        rpc.available = True
        rpc.last_activity_age.return_value = 0.0
        controller._apply_jamulus_participants(
            local_roster,
            source_identity=_primary_source_identity(controller),
        )
        controller._sync_reference_track_primary_gate()

        assert controller._jamulus_connected is True
        assert dialog._primary_gate is ReferenceTrackPrimaryGate.READY
        assert dialog._play.isEnabled() is True
    finally:
        controller.bridge.jamulus_launch_intended = False
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_repeated_route_checks_coalesce_to_first_and_newest_request() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    fake.block_refresh = True
    controller._reference_track = fake
    try:
        controller._open_reference_track()
        assert fake.refresh_entered.wait(timeout=3.0)
        dialog = controller._reference_track_dialog
        assert dialog is not None
        assert dialog._recheck_route.isEnabled() is False

        for index in range(20):
            controller._request_reference_track_route_check(
                audience_bridge_active=bool(index % 2),
            )
        fake.release_refresh.set()

        assert _wait_until(lambda: len(fake.refreshes) == 2)
        assert fake.refreshes == [False, True]
        assert _wait_until(lambda: dialog._recheck_route.isEnabled())
        assert controller._reference_track_operation_inflight is False
    finally:
        fake.release_refresh.set()
        controller.shutdown()


def test_song_selected_during_route_probe_loads_once_after_probe() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    fake.block_refresh = True
    controller._reference_track = fake
    private_path = "/Users/private/Rehearsal Song.wav"
    try:
        controller._open_reference_track()
        assert fake.refresh_entered.wait(timeout=3.0)

        controller._load_reference_track(private_path)

        dialog = controller._reference_track_dialog
        assert dialog is not None
        assert fake.loaded == []
        assert dialog._load.text() == "Waiting to Load…"
        assert dialog._load.isEnabled() is False

        fake.release_refresh.set()
        assert _wait_until(lambda: fake.loaded == [private_path])
        assert _wait_until(
            lambda: not controller._reference_track_operation_inflight
        )
        assert dialog._load.text() == "Replace…"
    finally:
        fake.release_refresh.set()
        controller.shutdown()


def test_companion_diagnostics_include_only_public_reference_track_facts() -> None:
    controller = _controller(host=True)
    controller._reference_track = _FakeReferenceTrack()
    try:
        diagnostics = controller._companion_get_diagnostics()["reference_track"]
        assert diagnostics["source_format"] == "WAV"
        assert diagnostics["route_backend"] == "blackhole"
        assert "source_name" not in diagnostics
        assert "path" not in diagnostics
    finally:
        controller.shutdown()


def test_host_projects_bounded_shared_track_truth_to_private_peers() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    publish = MagicMock()
    controller.host_peer = SimpleNamespace(
        active=True,
        publish_shared_track_state=publish,
    )
    controller._shared_track_peer_publish_failed = False
    base = _snapshot(ReferenceTrackState.PLAYING)
    snapshot = ReferenceTrackSnapshot(
        state=ReferenceTrackState.PLAYING,
        capability=base.capability,
        source_name=base.source_name,
        duration_s=base.duration_s,
        position_s=base.position_s,
        loop_start_s=2.0,
        loop_end_s=20.0,
        count_in_active=True,
    )

    controller._publish_shared_track_peer_state(snapshot)

    publish.assert_called_once_with(
        state="playing",
        loaded=True,
        source_display_name="Reference.wav",
        position_s=5.0,
        duration_s=60.0,
        loop_start_s=2.0,
        loop_end_s=20.0,
        count_in_active=True,
        cleanup_pending=False,
        needs_attention=False,
    )


def test_guest_renders_authoritative_shared_track_and_recording_count_in() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    strip = SimpleNamespace(
        set_recording_phase=MagicMock(),
        set_shared_track_snapshot=MagicMock(),
    )
    studio = SimpleNamespace(set_recording_phase=MagicMock())
    controller.window = SimpleNamespace(
        session_strip=strip,
        recording_studio=studio,
    )
    peer_state = SessionStateSnapshot(
        session_id=str(uuid.uuid4()),
        generation=4,
        signal=RecordingSignal.RECORDING,
        take_id=str(uuid.uuid4()),
        shared_track=SharedTrackSessionSnapshot(
            generation=7,
            playback_generation=2,
            state=SharedTrackPlaybackState.PLAYING,
            loaded=True,
            source_display_name="Band Song.wav",
            position_s=3.5,
            duration_s=90.0,
            loop_start_s=2.0,
            loop_end_s=30.0,
            count_in_active=True,
        ),
    )
    controller.guest_peer = SimpleNamespace(last_state=peer_state)

    controller._render_guest_peer_state()

    strip.set_recording_phase.assert_called_once_with("count_in")
    studio.set_recording_phase.assert_called_once_with("count_in")
    projection = strip.set_shared_track_snapshot.call_args.args[0]
    assert projection.source_name == "Band Song.wav"
    assert projection.state is SharedTrackPlaybackState.PLAYING
    assert projection.position_s == pytest.approx(3.5)
    assert projection.count_in_active is True
    assert not hasattr(projection, "can_control")


def test_successful_peer_teardown_clears_guest_projection_only_after_proof() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    clear_projection = MagicMock()
    controller.window = SimpleNamespace(
        session_strip=SimpleNamespace(
            clear_shared_track_projection=clear_projection,
        )
    )
    controller._ui_invoker = SimpleNamespace(
        invoke=lambda callback: callback(),
    )
    controller.host_peer = None
    controller._guest_invite = object()
    controller._guest_peer_configuration_failed = False
    controller._host_peer_warning = ""
    guest = MagicMock()
    guest.stop.return_value = False
    controller.guest_peer = guest

    assert controller._stop_session_peer(clear_invite=True) is False
    clear_projection.assert_not_called()
    assert controller.guest_peer is guest

    guest.stop.return_value = True
    assert controller._stop_session_peer(clear_invite=True) is True
    clear_projection.assert_called_once_with()
    assert controller.guest_peer is None
    assert controller._guest_invite is None


def test_play_builds_ephemeral_separate_client_context_off_ui_thread() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._jamulus_connected = True
    primary_process = MagicMock()
    primary_process.pid = 4242
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    _set_primary_rpc(controller)
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


def test_primary_process_swap_before_play_core_skips_stale_start() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._jamulus_connected = True
    old_process = MagicMock()
    old_process.pid = 4250
    old_process.poll.return_value = None
    controller.bridge.jamulus_process = old_process
    controller.bridge._jamulus_process_generation_counter = 1
    controller.bridge._jamulus_process_generation = 1
    _set_primary_rpc(controller)
    controller._reference_track_operation_lock.acquire()
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

            replacement = MagicMock()
            replacement.pid = 4251
            replacement.poll.return_value = None
            controller.bridge.jamulus_process = replacement
            controller.bridge._jamulus_process_generation_counter = 2
            controller.bridge._jamulus_process_generation = 2
            _set_primary_rpc(controller)
    finally:
        controller._reference_track_operation_lock.release()

    try:
        assert _wait_until(
            lambda: not controller._reference_track_operation_inflight
        )
        assert fake.contexts == []
    finally:
        controller._jamulus_connected = False
        controller.bridge.jamulus_launch_intended = False
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_primary_process_swap_during_play_retires_stale_reference_client() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    fake.block_play = True
    controller._reference_track = fake
    controller._jamulus_connected = True
    old_process = MagicMock()
    old_process.pid = 4252
    old_process.poll.return_value = None
    controller.bridge.jamulus_process = old_process
    controller.bridge._jamulus_process_generation_counter = 1
    controller.bridge._jamulus_process_generation = 1
    _set_primary_rpc(controller)
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

            replacement = MagicMock()
            replacement.pid = 4253
            replacement.poll.return_value = None
            controller.bridge.jamulus_process = replacement
            controller.bridge._jamulus_process_generation_counter = 2
            controller.bridge._jamulus_process_generation = 2
            _set_primary_rpc(controller)
            fake.release_play.set()

        assert _wait_until(lambda: fake.stops == 1)
        assert len(fake.contexts) == 1
        assert fake.contexts[0].primary_process_id == old_process.pid
    finally:
        fake.release_play.set()
        controller._jamulus_connected = False
        controller.bridge.jamulus_launch_intended = False
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
    _set_primary_rpc(controller)
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


def test_session_end_skips_play_queued_before_core_entry() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._jamulus_connected = True
    primary_process = MagicMock()
    primary_process.pid = 4244
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    _set_primary_rpc(controller)
    controller._reference_track_operation_lock.acquire()
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
            controller._stop_reference_track_for_session_end(background=True)
    finally:
        controller._reference_track_operation_lock.release()

    try:
        assert _wait_until(
            lambda: not controller._reference_track_operation_inflight
        )
        assert fake.contexts == []
        assert fake.stops == 1
    finally:
        controller._jamulus_connected = False
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_wake_loss_invalidates_a_queued_start_even_after_fast_reconnect() -> None:
    from core.session_lifecycle import SessionLifecyclePhase

    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._transition_lifecycle(SessionLifecyclePhase.JOINING)
    controller._transition_lifecycle(SessionLifecyclePhase.CONNECTED)
    controller._jamulus_connected = True
    primary_process = MagicMock()
    primary_process.pid = 4245
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    _set_primary_rpc(controller)
    controller.bridge.jamulus_launch_intended = True
    controller._last_reconnect_tick_monotonic = (
        time.monotonic() - controller._WAKE_REVALIDATION_GAP_SECONDS - 1
    )
    controller._last_reconnect_tick_wall = (
        time.time() - controller._WAKE_REVALIDATION_GAP_SECONDS - 1
    )
    initial_generation = controller._reference_track_session_generation
    controller._reference_track_operation_lock.acquire()
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
            controller._revalidate_after_wake_gap()
            assert controller._jamulus_connected is False
            assert (
                controller._reference_track_session_generation
                == initial_generation + 1
            )
            assert fake.cancelled_starts == 1

            # Fresh roster truth can arrive before the old worker gets the
            # operation lock. Generation identity, not a transient False,
            # must prevent the stale launch.
            controller._jamulus_connected = True
    finally:
        controller._reference_track_operation_lock.release()

    try:
        assert _wait_until(
            lambda: not controller._reference_track_operation_inflight
        )
        assert fake.contexts == []
        assert fake.stops == 1
    finally:
        controller._jamulus_connected = False
        controller.bridge.jamulus_launch_intended = False
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_rpc_hang_retires_an_active_reference_track() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack(ReferenceTrackState.PLAYING)
    controller._reference_track = fake
    controller._jamulus_connected = True
    primary_process = MagicMock()
    primary_process.pid = 4246
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    controller.bridge.jamulus_launch_intended = True
    controller.bridge.jamulus_state = "Running"
    controller.bridge.attempt_auto_reconnects = MagicMock()
    controller.jamulus.rpc_client = MagicMock()
    controller.jamulus.rpc_client.available = True
    controller.jamulus.rpc_client.last_activity_age.return_value = (
        controller._RPC_HANG_THRESHOLD_S + 1
    )
    controller._refresh_reference_track_health = MagicMock()
    try:
        controller._open_reference_track()
        dialog = controller._reference_track_dialog
        assert dialog is not None
        controller._on_reconnect_tick()

        assert controller._jamulus_connected is False
        assert controller._rpc_hang_banner_shown is True
        assert dialog._primary_gate is ReferenceTrackPrimaryGate.RECOVERING
        assert fake.cancelled_starts == 1
        assert _wait_until(lambda: fake.stops == 1)
        assert fake.snapshot.active is False
    finally:
        controller.bridge.jamulus_launch_intended = False
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_play_refuses_without_a_live_owned_primary_jamulus_pid() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._jamulus_connected = True
    _set_primary_rpc(controller)
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


@pytest.mark.parametrize(
    "blocked_state",
    ("stopping", "cleanup_retry", "invite_switch"),
)
def test_play_is_blocked_while_session_ownership_is_changing(
    blocked_state: str,
) -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack()
    controller._reference_track = fake
    controller._jamulus_connected = True
    controller.window.flash_message = MagicMock()
    primary_process = MagicMock()
    primary_process.pid = 4247
    primary_process.poll.return_value = None
    controller.bridge.jamulus_process = primary_process
    controller._open_reference_track()
    dialog = controller._reference_track_dialog
    assert dialog is not None
    if blocked_state == "stopping":
        controller.audio.stopping = True
    elif blocked_state == "cleanup_retry":
        controller.audio.cleanup_retry_required = True
    else:
        controller._invite_switch_in_flight = True
    try:
        controller._render_reference_track_snapshot(fake.snapshot)
        assert dialog._play.isEnabled() is False
        assert "current session change" in dialog._status.text()
        assert "session change" in dialog._play.toolTip()
        assert "Finish Jamulus sound setup" not in dialog._route_guidance.text()

        controller._play_reference_track()

        assert fake.contexts == []
        message = controller.window.flash_message.call_args.args[0]
        assert "session change" in message
    finally:
        controller.audio.stopping = False
        controller.audio.cleanup_retry_required = False
        controller._invite_switch_in_flight = False
        controller._jamulus_connected = False
        controller.bridge.jamulus_process = None
        controller.shutdown()


def test_programmatic_stop_cannot_create_a_second_session_cleanup_owner() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack(ReferenceTrackState.PAUSED)
    controller._reference_track = fake
    controller._open_reference_track()
    dialog = controller._reference_track_dialog
    assert dialog is not None
    controller.audio.stopping = True
    controller._sync_reference_track_primary_gate()
    try:
        with patch.object(
            controller,
            "_queue_reference_track_teardown",
        ) as queue_teardown:
            dialog.stop_requested.emit()
        queue_teardown.assert_not_called()
        assert dialog._stop.isEnabled() is False
        assert fake.stops == 0
    finally:
        controller.audio.stopping = False
        controller.shutdown()


def test_repeated_stop_requests_coalesce_while_teardown_is_inflight() -> None:
    controller = _controller(host=True)
    fake = _FakeReferenceTrack(ReferenceTrackState.PLAYING)
    controller._reference_track = fake
    controller._reference_track_operation_inflight = True
    controller._reference_track_operation_kind = "teardown"
    try:
        controller._queue_reference_track_teardown()
        controller._queue_reference_track_teardown()
        assert fake.cancelled_starts == 2
        assert controller._reference_track_teardown_pending is False
    finally:
        controller._reference_track_operation_inflight = False
        controller._reference_track_operation_kind = ""
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
        assert "Shared Track client did not stop cleanly" in message
    finally:
        controller._stop_reference_track_for_session_end = MagicMock(return_value=True)
        controller.shutdown()


@pytest.mark.parametrize("retain_remote_guest", (False, True))
def test_roster_loss_retires_an_active_reference_track(
    retain_remote_guest: bool,
) -> None:
    from jamulus_controller import JamulusParticipant

    controller = _controller(host=True)
    fake = _FakeReferenceTrack(ReferenceTrackState.PLAYING)
    controller._reference_track = fake
    controller._jamulus_connected = True
    participants = (
        [JamulusParticipant(channel_id=7, name="Guest", is_local=False)]
        if retain_remote_guest
        else []
    )
    try:
        with patch.object(
            controller,
            "_stop_reference_track_for_session_end",
        ) as stop:
            controller._apply_jamulus_participants(participants)
            controller._apply_jamulus_participants(participants)
        stop.assert_called_once_with(background=True)
        assert controller._jamulus_connected is False
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
                "Shared Track couldn't confirm that its owned Jamulus "
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
