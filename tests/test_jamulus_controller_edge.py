from __future__ import annotations

import threading
import unittest

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

    def connect(self):
        return None

    def close(self):
        return None


class _AudioEngineStub:
    def __init__(self):
        self.level_overrides: dict[int, float] = {}

    def set_level_override(self, channel_id: int, level: float):
        self.level_overrides[channel_id] = level


class _LoggerStub:
    def warning(self, *_args, **_kwargs):
        return None

    def exception(self, *_args, **_kwargs):
        return None

    def getChild(self, *_args, **_kwargs):
        return self


def _build_controller() -> JamulusController:
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
    return controller


class TestJamulusControllerEdge(unittest.TestCase):
    def setUp(self):
        self.controller = _build_controller()

    def test_check_participants_clears_local_state_when_protocol_empty(self):
        self.controller.add_participant("Alice", 0)
        self.assertIn(0, self.controller.participants)

        self.controller.protocol.cached_participants = {}
        self.controller._check_participants()

        self.assertEqual(self.controller.participants, {})

    def test_add_participant_during_active_solo_stays_muted_until_unsolo(self):
        self.controller.add_participant("Lead", 0)
        self.controller.add_participant("Rhythm", 1)
        self.controller.set_solo(0, True)

        self.controller.add_participant("Late Joiner", 2)
        self.assertTrue(self.controller.participants[2].muted)
        self.assertFalse(self.controller.participants[2].solo)

        self.controller.set_solo(0, False)
        self.assertFalse(self.controller.participants[2].muted)

    def test_remove_soloed_participant_restores_pre_solo_mutes(self):
        self.controller.add_participant("Solo", 0)
        self.controller.add_participant("Muted", 1)
        self.controller.add_participant("Open", 2)
        self.controller.set_mute(1, True)
        self.controller.set_solo(0, True)

        self.controller.remove_participant(0)

        self.assertFalse(any(p.solo for p in self.controller.get_participants()))
        self.assertTrue(self.controller.participants[1].muted)
        self.assertFalse(self.controller.participants[2].muted)
        self.assertEqual(self.controller._pre_solo_mute, {})

    def test_check_participants_removing_solo_channel_restores_snapshot(self):
        self.controller.add_participant("Solo", 0)
        self.controller.add_participant("Muted", 1)
        self.controller.set_mute(1, True)
        self.controller.set_solo(0, True)

        self.controller.protocol.cached_participants = {1: "Muted"}
        self.controller._check_participants()

        self.assertIn(1, self.controller.participants)
        self.assertFalse(self.controller.participants[1].solo)
        self.assertTrue(self.controller.participants[1].muted)
        self.assertEqual(self.controller._pre_solo_mute, {})

    def test_set_solo_recovers_when_snapshot_missing(self):
        self.controller.add_participant("A", 0)
        self.controller.add_participant("B", 1)
        self.controller.set_mute(1, True)

        # Simulate a desynchronized state with solo flags but no stored snapshot.
        self.controller.participants[0].solo = True
        self.controller.participants[0].muted = False
        self.controller._pre_solo_mute = {}

        self.controller.set_solo(1, True)
        self.controller.set_solo(1, False)

        self.assertFalse(self.controller.participants[0].muted)
        self.assertTrue(self.controller.participants[1].muted)


if __name__ == "__main__":
    unittest.main()
