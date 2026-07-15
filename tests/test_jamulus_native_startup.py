"""High-signal orchestration checks for the Jamulus-native startup journey."""
from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.settings import AppSettings
from webjam_qt.controllers.application_controller import ApplicationController


class _ImmediateThread:
    """Run one worker synchronously so ordering remains observable."""

    def __init__(self, *, target, **_kwargs) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _AttemptStore:
    def __init__(self) -> None:
        self.cleared = 0
        self.saved = []

    def load(self):
        return None

    def next_generation(self) -> int:
        return 1

    def save(self, record) -> None:
        self.saved.append(record)

    def clear(self) -> None:
        self.cleared += 1


def _controller(*, hosting: bool) -> ApplicationController:
    controller = ApplicationController.__new__(ApplicationController)
    controller._shutdown = False
    controller._startup_generation = 0
    controller._startup_attempt = None
    controller._startup_profile_plan = None
    controller._startup_recovery_record = None
    controller._startup_attempt_store = _AttemptStore()
    controller._startup_readiness_store = SimpleNamespace(
        is_current=mock.Mock(return_value=False)
    )
    controller._remote_invitation_requires_replacement = False
    controller._remote_invitation = None
    controller._remote_invite_owner = None
    controller._conductor_setup_requested = False
    controller._conductor_band_check = None
    controller._local_audio_seen = False
    controller._remote_audio_seen = False
    controller.settings = AppSettings(
        host_server_enabled=hosting,
        jamulus_server="127.0.0.1" if hosting else "192.168.1.42",
    )
    controller.bridge = SimpleNamespace(
        jamulus_state="Not launched",
        ensure_hosted_server=mock.Mock(return_value=(True, "ready")),
        launch_jamulus=mock.Mock(return_value=True),
        hosted_server_alive=mock.Mock(return_value=hosting),
        native_profile_plan=None,
    )
    controller.audio = SimpleNamespace(
        connected=False,
        stopping=False,
        ended_by_user=False,
        connection_timed_out=False,
        recovering=False,
        reset_to_idle=mock.Mock(),
    )
    controller._jamulus_connected = False
    controller._connection_timer = mock.Mock()
    controller._ui_invoker = SimpleNamespace(invoke=lambda callback: callback())
    controller.window = SimpleNamespace(
        session_strip=SimpleNamespace(
            set_recording_available=mock.Mock(),
            set_audio_state=mock.Mock(),
        ),
        session_hud=SimpleNamespace(set_state=mock.Mock()),
    )
    controller._transition_lifecycle = mock.Mock()
    controller._render_startup_journey = mock.Mock()
    return controller


def test_host_starts_private_server_before_opening_jamulus() -> None:
    controller = _controller(hosting=True)
    events: list[str] = []
    controller.bridge.ensure_hosted_server.side_effect = (
        lambda **_kwargs: events.append("server") or (True, "ready")
    )
    controller._launch_native_jamulus_for_startup = mock.Mock(
        side_effect=lambda _generation: events.append("jamulus")
    )

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller.begin_startup_journey()

    assert events == ["server", "jamulus"]
    assert controller._startup_attempt is not None
    assert controller._startup_attempt["role"] == "host"


def test_guest_launches_native_jamulus_once_without_a_second_start_decision() -> None:
    controller = _controller(hosting=False)
    controller._launch_native_jamulus_for_startup = mock.Mock()

    controller.begin_startup_journey()
    controller.begin_startup_journey()

    controller.bridge.ensure_hosted_server.assert_not_called()
    controller._launch_native_jamulus_for_startup.assert_called_once_with(1)


def test_cancelled_host_setup_releases_owned_client_and_server() -> None:
    controller = _controller(hosting=True)
    controller._startup_attempt = {
        "generation": 1,
        "role": "host",
        "phase": "native_sound_setup",
    }
    events: list[str] = []
    controller.bridge.stop_jamulus = mock.Mock(
        side_effect=lambda: events.append("client") or True
    )
    controller.bridge.stop_hosted_server = mock.Mock(
        side_effect=lambda: events.append("server") or True
    )

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller._cancel_startup_journey()

    assert events == ["client", "server"]
    assert controller._startup_attempt is None
    assert controller._startup_attempt_store.cleared == 1
    controller.audio.reset_to_idle.assert_called_once_with()


def test_cancel_during_host_startup_never_completes_into_a_client_launch() -> None:
    controller = _controller(hosting=True)
    cancel_event = threading.Event()
    attempt = {
        "generation": 1,
        "role": "host",
        "phase": "starting_server",
        "cancel_event": cancel_event,
    }
    controller._startup_attempt = attempt
    deliveries = []
    controller._ui_invoker = SimpleNamespace(invoke=deliveries.append)
    controller.bridge.ensure_hosted_server.side_effect = (
        lambda **_kwargs: (True, "ready")
    )
    controller._launch_native_jamulus_for_startup = mock.Mock()

    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ):
        controller._start_hosted_server_for_startup(1)

    assert len(deliveries) == 1
    cancel_event.set()
    attempt["phase"] = "cancelling"
    deliveries[0]()

    controller._launch_native_jamulus_for_startup.assert_not_called()


def test_v2_guest_peer_starts_once_after_native_connection_proof() -> None:
    controller = _controller(hosting=False)
    guest = mock.Mock()
    controller.guest_peer = guest
    controller._guest_invite = object()
    controller._remote_session = None
    controller.bridge.jamulus_state = "Running"
    controller._startup_music_is_proven = mock.Mock(return_value=True)
    controller._startup_attempt = {
        "generation": 1,
        "role": "guest",
        "phase": "native_sound_setup",
        "cancel_event": threading.Event(),
        "setup_finished": False,
    }

    controller._poll_startup_connection(1)
    controller._poll_startup_connection(1)

    guest.start.assert_called_once_with()


def test_native_guest_peer_never_starts_for_a_cancelled_or_remote_journey() -> None:
    controller = _controller(hosting=False)
    guest = mock.Mock()
    controller.guest_peer = guest
    cancelled = threading.Event()
    cancelled.set()
    controller._start_guest_peer_for_native_startup(
        {"role": "guest", "phase": "cancelling", "cancel_event": cancelled}
    )
    controller._remote_session = object()
    controller._start_guest_peer_for_native_startup(
        {"role": "guest", "phase": "native_sound_setup"}
    )

    guest.start.assert_not_called()


def test_webex_save_failure_restores_the_in_memory_settings() -> None:
    controller = _controller(hosting=False)
    controller.settings.webex_url = "https://old.webex.com/meet/band"
    controller.settings.webex_audio_mode = "mute"
    controller._startup_attempt = {"generation": 1, "role": "guest"}
    controller.window.session_hud.input_text = mock.Mock(
        return_value="https://new.webex.com/meet/band"
    )
    controller.window.session_strip.set_video_configured = mock.Mock()
    controller.webex = SimpleNamespace(meeting_url="https://old.webex.com/meet/band")
    controller.bridge.webex_controller = controller.webex

    with mock.patch("core.settings.save_settings", side_effect=OSError("full")):
        controller._save_startup_webex_link()

    assert controller.settings.webex_url == "https://old.webex.com/meet/band"
    assert controller.settings.webex_audio_mode == "mute"
    assert controller.webex.meeting_url == "https://old.webex.com/meet/band"
    assert controller._startup_attempt["input_error"]


def test_music_readiness_requires_authenticated_connection_and_one_local_identity() -> None:
    controller = _controller(hosting=True)
    controller.bridge.jamulus_state = "Running"
    controller._jamulus_connected = True
    controller.jamulus = SimpleNamespace(
        rpc_client=SimpleNamespace(available=True)
    )
    controller.participants = {
        3: SimpleNamespace(channel_id=3, is_local=True),
    }
    attempt = {"role": "host"}

    assert controller._startup_music_is_proven(attempt) is True

    controller.participants[4] = SimpleNamespace(channel_id=4, is_local=True)
    assert controller._startup_music_is_proven(attempt) is False

    controller.participants.pop(4)
    controller.jamulus.rpc_client.available = False
    assert controller._startup_music_is_proven(attempt) is False
