"""Effective listening gains retain ordering and the originating native owner."""
from __future__ import annotations

import json
import logging
import threading
from unittest.mock import Mock

import pytest

from core.jamulus_rpc_client import JamulusRpcClient, JamulusRpcMonitorIdentity
from jamulus_controller import JamulusController


class MemorySocket:
    def __init__(self):
        self.messages = []
        self.on_send = None

    def sendall(self, payload):
        self.messages.append(json.loads(payload))
        if self.on_send is not None:
            callback, self.on_send = self.on_send, None
            callback()

    def close(self):
        pass


class HeldWorker:
    instances = []

    def __init__(self, *, target, **kwargs):
        self.target = target
        self.started = False
        self.finished = False
        self.instances.append(self)

    def start(self):
        self.started = True

    def run(self):
        self.target()
        self.finished = True

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return self.started and not self.finished


def _ready_rpc(epoch=1):
    rpc = JamulusRpcClient()
    rpc._running = rpc._available = rpc._authed = True
    rpc._monitor_epoch = rpc._sock_epoch = epoch
    rpc._monitor_identity = JamulusRpcMonitorIdentity(epoch, 17, 9001)
    rpc._sock = MemorySocket()
    return rpc


@pytest.fixture
def mixer(monkeypatch):
    HeldWorker.instances = []
    controller = JamulusController.__new__(JamulusController)
    controller.callbacks = []
    controller._lock = threading.Lock()
    controller.logger = logging.getLogger("webjam.test.gain_dispatch")
    controller.protocol = Mock()
    controller.audio_engine = Mock()
    controller.rpc_client = _ready_rpc()
    controller.running = True
    controller.monitor_thread = None
    controller._live_audio_route_owned = True
    controller._rpc_monitor_identity = controller.rpc_client._monitor_identity
    controller.add_participant("Local fixture", 1)
    monkeypatch.setattr("jamulus_controller.threading.Thread", HeldWorker)
    return controller, controller.rpc_client._sock


def levels(socket):
    assert all(row["method"] == "jamulusclient/setFaderLevel" for row in socket.messages)
    return [row["params"]["level"] for row in socket.messages]


def test_pending_fader_then_mute_coalesces_to_silence(mixer):
    controller, socket = mixer
    controller.set_fader_level(1, 110)
    controller.set_mute(1, True)
    assert len(HeldWorker.instances) == 1
    HeldWorker.instances[0].run()
    assert levels(socket) == [0]
    assert controller.participants[1].fader_level == 110


def test_new_mute_while_native_send_is_entered_follows_that_send(mixer):
    controller, socket = mixer
    controller.set_fader_level(1, 127)
    socket.on_send = lambda: controller.set_mute(1, True)
    HeldWorker.instances[0].run()
    assert levels(socket) == [100, 0]
    assert len(HeldWorker.instances) == 1


def test_queued_gain_cannot_cross_rpc_monitor_epoch(mixer):
    controller, old_socket = mixer
    controller.set_fader_level(1, 90)
    rpc = controller.rpc_client
    rpc._monitor_epoch = rpc._sock_epoch = 2
    rpc._monitor_identity = JamulusRpcMonitorIdentity(2, 18, 9002)
    rpc._sock = MemorySocket()
    HeldWorker.instances[0].run()
    assert levels(old_socket) == []
    assert levels(rpc._sock) == []


def test_queued_gain_cannot_cross_rpc_client_replacement(mixer):
    controller, old_socket = mixer
    controller.set_fader_level(1, 90)
    controller.rpc_client = _ready_rpc()
    HeldWorker.instances[0].run()
    assert levels(old_socket) == []
    assert levels(controller.rpc_client._sock) == []


def test_queued_gain_cannot_apply_to_reused_participant_channel(mixer):
    controller, socket = mixer
    controller.set_fader_level(1, 90)
    controller.remove_participant(1)
    controller.add_participant("Replacement fixture", 1)
    HeldWorker.instances[0].run()
    assert levels(socket) == []
    controller.set_fader_level(1, 127)
    HeldWorker.instances[-1].run()
    assert levels(socket) == [100]


def test_many_fader_changes_keep_one_worker_and_latest_effective_gain(mixer):
    controller, socket = mixer
    for level in range(1000):
        controller.set_fader_level(1, level % 128)
    assert len(HeldWorker.instances) == 1
    HeldWorker.instances[0].run()
    assert levels(socket) == [round((999 % 128) / 127 * 100)]


def test_native_send_holds_neither_participant_nor_dispatch_lock(mixer):
    controller, socket = mixer
    controller.set_mute(1, True)

    observed = []

    def check_locks():
        participants_owned = controller._participants_lock._is_owned()
        dispatch_available = controller._gain_dispatch_lock.acquire(blocking=False)
        if dispatch_available:
            controller._gain_dispatch_lock.release()
        observed.append((participants_owned, dispatch_available))

    socket.on_send = check_locks
    HeldWorker.instances[0].run()
    assert levels(socket) == [0]
    # RPC deliberately catches native failures, so assert outside sendall.
    assert observed == [(False, True)]


def test_stop_retires_pending_gain_before_delayed_worker_runs(mixer):
    controller, socket = mixer
    controller.set_fader_level(1, 127)
    worker = HeldWorker.instances[0]
    controller.stop()
    worker.run()
    assert levels(socket) == []


def test_rpc_gain_epoch_is_checked_at_actual_send_boundary():
    rpc = _ready_rpc(epoch=2)
    assert rpc.set_channel_gain(1, 127, epoch=1) is False
    assert levels(rpc._sock) == []
    assert rpc.set_channel_gain(1, 127, epoch=2) is True
    assert levels(rpc._sock) == [100]


def test_stop_timeout_blocks_start_until_the_owned_worker_exits(mixer):
    controller, old_socket = mixer
    controller.set_fader_level(1, 127)
    old_worker = HeldWorker.instances[0]
    controller.stop()
    assert old_worker.is_alive()
    assert controller.last_error == "The listening-mix worker is still closing."
    rpc = controller.rpc_client
    rpc.start = Mock()
    with pytest.raises(RuntimeError, match="still closing"):
        controller.start(process_generation=18, process_id=9002)
    rpc.start.assert_not_called()
    controller.set_mute(1, True)
    assert len(HeldWorker.instances) == 1

    old_worker.run()
    assert levels(old_socket) == []

    def start_replacement(**kwargs):
        rpc._running = rpc._available = rpc._authed = True
        rpc._monitor_epoch = rpc._sock_epoch = 3
        rpc._monitor_identity = JamulusRpcMonitorIdentity(3, 18, 9002)
        rpc._sock = MemorySocket()
        return rpc._monitor_identity

    rpc.start.side_effect = start_replacement
    assert controller.start(process_generation=18, process_id=9002) == JamulusRpcMonitorIdentity(3, 18, 9002)
    controller.set_mute(1, False)
    newest_worker = HeldWorker.instances[-1]
    # Re-entering an already retired worker cannot clear its replacement.
    old_worker.run()
    assert controller._gain_worker is newest_worker
    newest_worker.run()
    assert levels(rpc._sock) == [100]


def test_worker_start_failure_releases_intent_without_private_error_or_stuck_owner(
    mixer, monkeypatch, caplog,
):
    controller, socket = mixer

    class RefusedWorker(HeldWorker):
        def start(self):
            raise RuntimeError("PRIVATE-THREAD-FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr("jamulus_controller.threading.Thread", RefusedWorker)
        controller.set_mute(1, True)
    assert controller._gain_worker is None
    assert not controller._gain_pending
    assert levels(socket) == []
    assert "PRIVATE-THREAD-FAILURE" not in caplog.text
    controller.set_mute(1, False)
    HeldWorker.instances[-1].run()
    assert levels(socket) == [79]


def test_roster_churn_does_not_grow_pending_departed_channel_work(mixer):
    controller, socket = mixer
    for channel in range(1, 300):
        if channel > 1:
            controller.remove_participant(channel - 1)
            controller.add_participant("Current fixture", channel)
        controller.set_fader_level(channel, 127)
    assert len(HeldWorker.instances) == 1
    assert len(controller._gain_pending) == 1
    HeldWorker.instances[0].run()
    assert socket.messages[0]["params"] == {"channelIndex": 299, "level": 100}
    assert len(socket.messages) == 1


@pytest.mark.parametrize("unavailable", ["_running", "_available", "_authed"])
def test_dispatch_never_promotes_unavailable_or_unauthenticated_rpc(mixer, unavailable):
    controller, socket = mixer
    setattr(controller.rpc_client, unavailable, False)
    controller.set_mute(1, True)
    assert not HeldWorker.instances
    assert levels(socket) == []
