"""Saved listening intent stays atomic and belongs to its matched native rows."""
from __future__ import annotations

import pytest

from core.jamulus_rpc_client import JamulusRpcMonitorIdentity
from tests.test_music_effective_gain import mixer as _inline_mixer_fixture
from tests.test_music_gain_dispatch import (
    HeldWorker,
    MemorySocket,
    mixer as _held_mixer_fixture,
)

inline_mixer = _inline_mixer_fixture
held_mixer = _held_mixer_fixture


def _payload(channel, name, *, level=127):
    return {"participants": [{
        "channel_id": channel, "name": name, "fader_level": level,
        "pan": 50, "muted": False, "solo": False,
    }]}


def _listening_state(controller):
    with controller._participants_lock:
        return {
            cid: (p.fader_level, p.muted, p.solo)
            for cid, p in controller.participants.items()
        }


def test_first_native_and_udp_callbacks_observe_the_entire_final_saved_mix(
    inline_mixer, monkeypatch,
):
    controller, sink = inline_mixer.controller, inline_mixer.sink
    native_observations, udp_observations = [], []
    sendall = sink.sendall
    apply_mixer = controller.protocol.apply_mixer

    def observe_native(raw):
        native_observations.append(_listening_state(controller))
        sendall(raw)

    def observe_udp(*args, **kwargs):
        udp_observations.append(_listening_state(controller))
        apply_mixer(*args, **kwargs)

    monkeypatch.setattr(sink, "sendall", observe_native)
    monkeypatch.setattr(controller.protocol, "apply_mixer", observe_udp)
    restored = controller.apply_mix_data({"participants": [
        {"channel_id": 7, "name": "WebJam Track", "fader_level": 55,
         "muted": False, "solo": True},
        {"channel_id": 8, "name": "Collaborator", "fader_level": 80,
         "muted": False, "solo": False},
        {"channel_id": 9, "name": "My monitor", "fader_level": 100,
         "muted": True, "solo": False},
    ]})
    expected = {7: (55, False, True), 8: (80, True, False), 9: (100, True, False)}
    assert restored == 3
    assert native_observations and udp_observations
    # Assert outside callbacks: native RPC intentionally catches send errors.
    assert all(snapshot == expected for snapshot in native_observations)
    assert all(snapshot == expected for snapshot in udp_observations)
    assert sink.gains == {7: 43, 8: 0, 9: 0}


@pytest.mark.parametrize("replacement", [False, True])
def test_matched_row_replaced_before_gain_enqueue_cannot_target_new_listener(
    inline_mixer, monkeypatch, replacement,
):
    controller, sink = inline_mixer.controller, inline_mixer.sink
    manager = controller._state
    old_participant = controller.participants[7]
    send_gain = manager._send_rpc_gain
    boundaries = []

    def replace_before_enqueue(channel_id, level, **kwargs):
        boundaries.append(channel_id)
        controller.remove_participant(channel_id)
        if replacement:
            controller.add_participant("A different listener", channel_id)
        send_gain(channel_id, level, **kwargs)

    monkeypatch.setattr(manager, "_send_rpc_gain", replace_before_enqueue)
    assert controller.apply_mix_data(_payload(7, "WebJam Track", level=33)) == 1
    assert boundaries == [7]
    assert old_participant.fader_level == 33
    assert sink.commands == []
    if replacement:
        assert controller.participants[7] is not old_participant
        assert controller.participants[7].fader_level == 100


@pytest.mark.parametrize("replacement", [False, True])
def test_matched_row_replaced_before_apply_helper_is_skipped(
    inline_mixer, monkeypatch, replacement,
):
    controller, sink = inline_mixer.controller, inline_mixer.sink
    manager = controller._state
    apply_gain = manager._apply_listening_gain
    boundaries = []

    def replace_before_apply(channel_id, **kwargs):
        boundaries.append(channel_id)
        controller.remove_participant(channel_id)
        if replacement:
            controller.add_participant("A different listener", channel_id)
        apply_gain(channel_id, **kwargs)

    monkeypatch.setattr(manager, "_apply_listening_gain", replace_before_apply)
    assert controller.apply_mix_data(_payload(7, "WebJam Track", level=33)) == 1
    assert boundaries == [7]
    assert sink.commands == []
    if replacement:
        assert controller.participants[7].fader_level == 100


@pytest.mark.parametrize("retirement", ["removed", "replaced", "epoch", "stop"])
def test_restored_gain_queued_for_old_native_owner_is_inert_after_retirement(
    held_mixer, retirement,
):
    controller, old_socket = held_mixer
    assert controller.apply_mix_data(_payload(1, "Local fixture")) == 1
    assert len(HeldWorker.instances) == 1
    worker = HeldWorker.instances[0]
    assert old_socket.messages == []
    if retirement in {"removed", "replaced"}:
        controller.remove_participant(1)
        if retirement == "replaced":
            controller.add_participant("A different listener", 1)
    elif retirement == "epoch":
        rpc = controller.rpc_client
        rpc._monitor_epoch = rpc._sock_epoch = 2
        rpc._monitor_identity = JamulusRpcMonitorIdentity(2, 18, 9002)
        rpc._sock = MemorySocket()
    else:
        controller.stop()
    worker.run()
    assert old_socket.messages == []
    if retirement == "epoch":
        assert controller.rpc_client._sock.messages == []


def test_new_explicit_choice_after_atomic_restore_wins_before_native_dispatch(held_mixer):
    controller, socket = held_mixer
    assert controller.apply_mix_data(_payload(1, "Local fixture")) == 1
    controller.set_mute(1, True)
    assert len(HeldWorker.instances) == 1
    HeldWorker.instances[0].run()
    assert [message["params"] for message in socket.messages] == [
        {"channelIndex": 1, "level": 0},
    ]
    assert controller.participants[1].fader_level == 127
