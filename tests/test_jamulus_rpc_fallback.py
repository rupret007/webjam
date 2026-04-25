"""
RPC-unavailable fallback path: when ``rpc_client.available`` is False, the
UDP-style participant updates remain authoritative and mixer state survives
subsequent fader changes.
"""
from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from jamulus_controller import JamulusController


class _ProtocolStub:
    def __init__(self):
        self.cached_participants: dict[int, str] = {}
        self.mixer_events: list[tuple[int, int, int, bool]] = []

    def request_clients(self):
        return dict(self.cached_participants)

    def set_cached_participants(self, participants):
        self.cached_participants = dict(participants)

    def apply_mixer(self, channel_id: int, fader_level: int, pan: int, muted: bool):
        self.mixer_events.append((channel_id, fader_level, pan, muted))


class _AudioEngineStub:
    def __init__(self):
        self.level_overrides: dict[int, float] = {}

    def set_level_override(self, channel_id: int, level: float):
        self.level_overrides[channel_id] = level


class _LoggerStub:
    def warning(self, *_a, **_kw): return None
    def exception(self, *_a, **_kw): return None
    def getChild(self, *_a, **_kw): return self


def _build_controller_rpc_unavailable() -> JamulusController:
    controller = JamulusController.__new__(JamulusController)
    controller.host = "127.0.0.1"
    controller.port = 22124
    controller.participants = {}
    controller.callbacks = []
    controller._lock = threading.Lock()
    controller._participants_lock = threading.RLock()
    controller._pre_solo_mute = {}
    controller.running = False
    controller.monitor_thread = None
    controller.last_error = ""
    controller.protocol = _ProtocolStub()
    controller.audio_engine = _AudioEngineStub()
    controller.logger = _LoggerStub()
    controller.rpc_client = MagicMock()
    controller.rpc_client.available = False
    return controller


class TestJamulusRpcFallback(unittest.TestCase):
    def test_rpc_unavailable_falls_back_to_udp_without_dropping_state(self):
        controller = _build_controller_rpc_unavailable()

        # UDP-style participant update — should be applied because RPC is down.
        controller._on_udp_participants({0: "Alice", 1: "Bob"})

        self.assertIn(0, controller.participants)
        self.assertIn(1, controller.participants)
        self.assertEqual(controller.participants[0].name, "Alice")
        self.assertEqual(controller.participants[1].name, "Bob")

        # Adjust a fader — should update local state and not require RPC.
        controller.set_fader_level(1, 64)
        self.assertEqual(controller.participants[1].fader_level, 64)

        # The protocol stub records the mixer apply (UDP path).
        applied = [evt for evt in controller.protocol.mixer_events if evt[0] == 1]
        self.assertTrue(applied, "UDP fallback should have applied mixer for ch 1")
        self.assertEqual(applied[-1][1], 64)

        # RPC client should NOT have been used for set_channel_gain when
        # available=False (the controller short-circuits in _send_rpc_gain).
        controller.rpc_client.set_channel_gain.assert_not_called()

        # A subsequent UDP update with the same ids preserves fader_level.
        controller._on_udp_participants({0: "Alice", 1: "Bob"})
        self.assertEqual(controller.participants[1].fader_level, 64)


if __name__ == "__main__":
    unittest.main()
