"""Listening state reaches native gain commands without live audio or sockets."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.jamulus_rpc_client import ChannelInfo, JamulusRpcClient, JamulusRpcMonitorIdentity
from core.settings import AppSettings
from jamulus_controller import JamulusController


class _InlineGainWorker:
    """Deterministic send order; this suite does not certify thread scheduling."""

    def __init__(self, *, target, **kwargs):
        self.target = target
        self.alive = False

    def start(self):
        self.alive = True
        try:
            self.target()
        finally:
            self.alive = False

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        assert not self.alive


class _MemorySocket:
    def __init__(self):
        self.commands = []

    def sendall(self, raw):
        assert raw.endswith(b"\n")
        self.commands.append(json.loads(raw))

    @property
    def gains(self):
        result = {}
        for command in self.commands:
            assert command["jsonrpc"] == "2.0"
            assert command["method"] == "jamulusclient/setFaderLevel"
            assert isinstance(command["id"], int) and command["id"] > 0
            params = command["params"]
            assert set(params) == {"channelIndex", "level"}
            result[params["channelIndex"]] = params["level"]
        return result


@pytest.fixture
def mixer(monkeypatch, tmp_path):
    settings = AppSettings(config_file=str(tmp_path / "unused-settings.json"))
    audio = Mock()
    monkeypatch.setattr("jamulus_controller.load_settings", lambda: settings)
    monkeypatch.setattr("jamulus_controller.configure_logging", lambda _: logging.getLogger("webjam.test.mix"))
    monkeypatch.setattr("jamulus_controller.RealAudioEngine", lambda *a, **kw: audio)
    monkeypatch.setattr("jamulus_controller.threading.Thread", _InlineGainWorker)
    monkeypatch.setattr("core.jamulus_rpc_client.socket.create_connection", Mock(side_effect=AssertionError("No real RPC connection")))
    controller = JamulusController()
    assert isinstance(controller.rpc_client, JamulusRpcClient)
    sink = _MemorySocket()
    # Install a synthetic already-connected socket at the client's transport
    # boundary; constructor, participant actions, RPC mapping and JSON framing
    # are production. No reader, engine, or monitoring thread starts.
    rpc = controller.rpc_client
    rpc._monitor_epoch = 1
    rpc._sock_epoch = 1
    rpc._monitor_identity = JamulusRpcMonitorIdentity(1, 3, 8123)
    rpc._sock = sink
    rpc._running = True
    rpc._available = True
    rpc._authed = True
    for channel, name in ((7, "WebJam Track"), (8, "Collaborator"), (9, "My monitor")):
        controller.add_participant(name, channel)
    yield SimpleNamespace(controller=controller, sink=sink, audio=audio)
    assert not controller.running
    assert audio.mock_calls == []
    rpc._sock = None
    rpc._running = False
    rpc._available = False


def _roster(controller, channels, source):
    names = {channel: controller.participants[channel].name for channel in channels}
    if source == "rpc":
        controller._on_rpc_participants([ChannelInfo(channel, name) for channel, name in names.items()])
    else:
        # Exercise the actual UDP merge implementation attached to the real
        # controller. The normal polling gate correctly skips UDP while RPC
        # is available; this is shared-merge coverage, not a UDP delivery claim.
        controller._state.apply_udp_clients_payload(names)


@pytest.mark.parametrize("channel", [7, 8, 9])
def test_muted_listening_fader_stays_zero_until_explicit_unmute(mixer, channel):
    controller, sink = mixer.controller, mixer.sink
    controller.set_mute(channel, True)
    assert sink.gains[channel] == 0
    controller.set_fader_level(channel, 40)
    assert controller.participants[channel].muted
    assert controller.participants[channel].fader_level == 40
    assert sink.gains[channel] == 0
    controller.set_mute(channel, False)
    assert sink.gains[channel] == 31
    assert not controller.participants[channel].muted


def test_solo_suppressed_fader_preserves_choice_and_prior_mute(mixer):
    controller, sink = mixer.controller, mixer.sink
    controller.set_mute(9, True)
    controller.set_solo(8, True)
    controller.set_fader_level(7, 55)
    assert controller.participants[7].muted
    assert sink.gains[7] == 0
    controller.set_mute(7, False)
    assert sink.gains[7] == 0
    controller.set_solo(8, False)
    assert not controller.participants[7].muted
    assert controller.participants[7].fader_level == 55
    assert sink.gains[7] == 43
    assert controller.participants[9].muted
    assert sink.gains[9] == 0


def test_switching_solo_keeps_suppression_and_restores_personal_choices(mixer):
    controller, sink = mixer.controller, mixer.sink
    controller.set_fader_level(7, 55)
    controller.set_mute(9, True)
    controller.set_solo(8, True)
    assert sink.gains == {7: 0, 8: 79, 9: 0}
    controller.set_solo(7, True)
    assert sink.gains == {7: 43, 8: 0, 9: 0}
    assert [channel for channel, participant in controller.participants.items() if participant.solo] == [7]
    controller.set_fader_level(8, 90)
    assert sink.gains[8] == 0
    controller.set_solo(7, False)
    assert sink.gains == {7: 43, 8: 71, 9: 0}
    assert not any(participant.solo for participant in controller.participants.values())
    assert controller.participants[9].muted


def test_unknown_channel_controls_do_not_change_any_listening_command(mixer):
    controller, sink = mixer.controller, mixer.sink
    controller.set_solo(8, True)
    before = list(sink.commands)
    chosen = {channel: (p.fader_level, p.muted, p.solo) for channel, p in controller.participants.items()}
    controller.set_fader_level(44, 40)
    controller.set_mute(44, True)
    controller.set_solo(44, False)
    controller.remove_participant(44)
    assert sink.commands == before
    assert {channel: (p.fader_level, p.muted, p.solo) for channel, p in controller.participants.items()} == chosen


@pytest.mark.parametrize("source", ["remove", "rpc", "udp_merge"])
def test_soloed_departure_restores_selected_gain_and_prior_mute(mixer, source):
    controller, sink = mixer.controller, mixer.sink
    controller.set_fader_level(7, 55)
    controller.set_mute(9, True)
    controller.set_solo(8, True)
    assert sink.gains[7] == 0
    if source == "remove":
        controller.remove_participant(8)
    else:
        _roster(controller, [7, 9], source)
    assert set(controller.participants) == {7, 9}
    assert not controller.participants[7].muted
    assert not controller.participants[7].solo
    assert controller.participants[7].fader_level == 55
    assert sink.gains[7] == 43
    assert controller.participants[9].muted
    assert sink.gains[9] == 0


@pytest.mark.parametrize("source", ["add", "rpc", "udp_merge"])
def test_late_arrival_during_solo_sends_zero_then_restores_default(mixer, source):
    controller, sink = mixer.controller, mixer.sink
    controller.set_solo(8, True)
    if source == "add":
        controller.add_participant("Late collaborator", 10)
    elif source == "rpc":
        roster = [ChannelInfo(channel, participant.name) for channel, participant in controller.participants.items()]
        controller._on_rpc_participants(roster + [ChannelInfo(10, "Late collaborator")])
    else:
        roster = {channel: participant.name for channel, participant in controller.participants.items()}
        controller._state.apply_udp_clients_payload({**roster, 10: "Late collaborator"})
    assert controller.participants[10].muted
    assert sink.gains.get(10) == 0
    controller.set_solo(8, False)
    assert not controller.participants[10].muted
    assert controller.participants[10].fader_level == 100
    assert sink.gains[10] == 79


@pytest.mark.parametrize("solo", [False, True])
@pytest.mark.parametrize("source", ["rpc", "udp_merge"])
def test_unchanged_roster_does_not_reset_listening_mix(mixer, solo, source):
    controller, sink = mixer.controller, mixer.sink
    controller.set_fader_level(7, 55)
    controller.set_mute(9, True)
    if solo:
        controller.set_solo(8, True)
    before = list(sink.commands)
    chosen = {channel: (p.fader_level, p.muted, p.solo) for channel, p in controller.participants.items()}
    _roster(controller, [7, 8, 9], source)
    assert sink.commands == before
    assert {channel: (p.fader_level, p.muted, p.solo) for channel, p in controller.participants.items()} == chosen


@pytest.mark.parametrize(("requested", "stored", "native"), [(-20, 0, 0), (40, 40, 31), (300, 127, 100)])
def test_unmuted_fader_maps_only_its_addressed_channel(mixer, requested, stored, native):
    controller, sink = mixer.controller, mixer.sink
    controller.set_fader_level(7, requested)
    assert controller.participants[7].fader_level == stored
    assert sink.gains == {7: native}
    assert controller.participants[8].fader_level == 100
    assert controller.participants[9].fader_level == 100
