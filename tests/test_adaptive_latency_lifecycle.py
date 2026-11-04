"""Latency observations belong to one launch and one RPC monitor binding."""
from types import SimpleNamespace
from unittest.mock import patch
import threading

import pytest

from core.jamulus_rpc_client import JamulusRpcMonitorIdentity
from services.adaptive_latency_controller import AdaptiveLatencyController


class Rpc:
    def __init__(self, epoch):
        self.identity = JamulusRpcMonitorIdentity(epoch, epoch, 100 + epoch)
        self.listener = None
        self.server_ping_active = True

    def set_server_info_listener(self, listener, **kwargs):
        self.listener = listener

    def monitor_snapshot(self):
        return SimpleNamespace(identity=self.identity, running=True,
                               available=True, authenticated=True)


@pytest.fixture
def subject(tmp_path):
    profile = tmp_path / 'profile.ini'
    profile.write_text('<client></client>')
    # Same property shape as BridgeService, not the old callable fixture.
    bridge = SimpleNamespace(
        settings=SimpleNamespace(adaptive_latency_enabled=True,
                                 adaptive_latency_player_override=False,
                                 audio_latency='low'),
        native_profile_plan=SimpleNamespace(profile_path=profile),
    )
    controller = AdaptiveLatencyController(bridge)
    controller.apply_launch_defaults(profile)
    return controller, profile


def test_real_profile_property_shape_supports_policy_ticks(subject):
    controller, profile = subject
    for _ in range(3):
        controller.note_server_ping(40)
    assert '<prefsndcrdbufidx>2</prefsndcrdbufidx>' in profile.read_text()


def test_replaced_rpc_listener_cannot_change_next_profile(subject):
    controller, profile = subject
    first, second = Rpc(1), Rpc(2)
    controller.bind_jamulus_controller(SimpleNamespace(rpc_client=first))
    old_listener = first.listener
    controller.bind_jamulus_controller(SimpleNamespace(rpc_client=second))
    before = profile.read_bytes()
    for _ in range(3):
        old_listener(90, first.identity)
    assert controller._ping_ms is None
    assert profile.read_bytes() == before


def test_wrong_monitor_source_is_not_a_latency_measurement(subject):
    controller, _ = subject
    rpc = Rpc(1)
    controller.bind_jamulus_controller(SimpleNamespace(rpc_client=rpc))
    rpc.listener(80, JamulusRpcMonitorIdentity(2, 2, 102))
    assert controller._ping_ms is None
    rpc.listener(12, rpc.identity)
    assert controller._ping_ms == 12


def test_stop_retires_listener_and_clears_session_presentations(subject):
    controller, profile = subject
    rpc = Rpc(1)
    controller.bind_jamulus_controller(SimpleNamespace(rpc_client=rpc))
    listener = rpc.listener
    controller.note_local_channel_id(7)
    listener(20, rpc.identity)
    controller.stop()
    before = profile.read_bytes()
    for _ in range(3):
        listener(80, rpc.identity)
    assert rpc.listener is None
    assert controller.participant_delays() == {}
    assert controller._ping_ms is None
    assert profile.read_bytes() == before


def test_new_launch_drops_previous_ping_and_policy_streak(subject):
    controller, profile = subject
    controller.note_local_channel_id(7)
    controller.note_server_ping(40)
    controller.note_server_ping(40)
    controller.apply_launch_defaults(profile)
    assert controller.participant_delays() == {}
    before = profile.read_bytes()
    controller.note_server_ping(40)
    assert profile.read_bytes() == before


def test_unreadable_profile_disables_optional_tuning_without_escaping_callback(subject):
    controller, profile = subject
    with patch('services.adaptive_latency_controller.apply_frame_factor_to_profile',
               side_effect=OSError('test-only inaccessible profile')):
        for _ in range(3):
            controller.note_server_ping(40)
    assert controller._policy.enabled is False
    assert '<prefsndcrdbufidx>1</prefsndcrdbufidx>' in profile.read_text()


def test_late_bind_for_retired_monitor_preserves_current_listener(subject):
    controller, _ = subject
    rpc = Rpc(2)
    controller.bind_jamulus_controller(SimpleNamespace(rpc_client=rpc))
    current_listener = rpc.listener
    controller.bind_jamulus_controller(SimpleNamespace(rpc_client=rpc),
                                      source=JamulusRpcMonitorIdentity(1, 1, 101))
    assert rpc.listener is current_listener
    rpc.listener(12, rpc.identity)
    assert controller._ping_ms == 12


def test_profile_switch_cannot_redirect_current_monitor_writes(subject, tmp_path):
    controller, old_profile = subject
    new_profile = tmp_path / 'replacement.ini'
    new_profile.write_text('<client></client>')
    before = new_profile.read_bytes()
    old_before = old_profile.read_bytes()
    controller._bridge.native_profile_plan = SimpleNamespace(profile_path=new_profile)
    for _ in range(3):
        controller.note_server_ping(80)
    assert new_profile.read_bytes() == before
    assert old_profile.read_bytes() == old_before


@pytest.mark.parametrize('invalid', [None, float('nan'), float('inf'), -1])
def test_invalid_measurement_clears_previous_delay_estimate(subject, invalid):
    controller, _ = subject
    controller.note_local_channel_id(7)
    controller.note_server_ping(20)
    assert controller.participant_delays()[7].overall_delay_ms is not None
    controller.note_server_ping(invalid)
    assert controller.participant_delays()[7].overall_delay_ms is None


def test_old_ping_expires_instead_of_becoming_current_session_delay(subject):
    controller, _ = subject
    controller.note_local_channel_id(7)
    with patch('services.adaptive_latency_controller.time.monotonic', return_value=100):
        controller.note_server_ping(20)
        assert controller.participant_delays()[7].overall_delay_ms is not None
    with patch('services.adaptive_latency_controller.time.monotonic', return_value=109):
        assert controller.participant_delays()[7].overall_delay_ms is None


def test_rpc_restart_without_rebind_cannot_reuse_measurements(subject):
    controller, _ = subject
    rpc = Rpc(1)
    controller.bind_jamulus_controller(SimpleNamespace(rpc_client=rpc))
    controller.note_local_channel_id(7)
    listener = rpc.listener
    listener(20, rpc.identity)
    assert controller.participant_delays()[7].overall_delay_ms is not None
    rpc.identity = JamulusRpcMonitorIdentity(2, 2, 102)
    listener(80, JamulusRpcMonitorIdentity(1, 1, 101))
    assert controller._ping_ms == 20
    assert controller.participant_delays()[7].overall_delay_ms is None


def test_runtime_override_stops_future_profile_recommendations(subject):
    controller, profile = subject
    controller.note_server_ping(40)
    controller.note_server_ping(40)
    controller._bridge.settings.adaptive_latency_player_override = True
    before = profile.read_bytes()
    controller.note_server_ping(40)
    assert profile.read_bytes() == before


def test_stop_drains_entered_write_and_retires_subsequent_callbacks(subject):
    controller, _ = subject
    rpc = Rpc(1)
    controller.bind_jamulus_controller(SimpleNamespace(rpc_client=rpc))
    listener = rpc.listener
    listener(40, rpc.identity)
    listener(40, rpc.identity)
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    def write(*args):
        entered.set()
        assert release.wait(2)
        return True

    def stop():
        controller.stop()
        stopped.set()

    with patch('services.adaptive_latency_controller.apply_frame_factor_to_profile',
               side_effect=write) as writer:
        worker = threading.Thread(target=lambda: listener(40, rpc.identity))
        stopper = threading.Thread(target=stop)
        worker.start()
        try:
            assert entered.wait(2)
            stopper.start()
            assert not stopped.wait(0.05)
        finally:
            release.set()
            worker.join(2)
            if stopper.ident is not None:
                stopper.join(2)
        assert not worker.is_alive() and not stopper.is_alive()
        assert stopped.is_set()
        listener(40, rpc.identity)
        assert writer.call_count == 1
