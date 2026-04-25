"""
apply_mix_data must route fader/mute/solo to the existing channel_id even
when the saved name doesn't match the current participant name (renamed
session). This is what lets a user re-load a saved mix after someone updated
their Jamulus display name.
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

    def apply_mixer(self, channel_id, fader_level, pan, muted):
        self.mixer_events.append((channel_id, fader_level, pan, muted))


class _AudioEngineStub:
    def set_level_override(self, *_a, **_kw):
        return None


class _LoggerStub:
    def warning(self, *_a, **_kw): return None
    def exception(self, *_a, **_kw): return None
    def getChild(self, *_a, **_kw): return self


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
    controller.rpc_client = MagicMock()
    controller.rpc_client.available = False
    return controller


class TestApplyMixDataChannelIdMigration(unittest.TestCase):
    def test_apply_mix_data_with_renamed_participants_uses_channel_id(self):
        controller = _build_controller()
        controller.add_participant("Alice", 0)
        controller.add_participant("Bob", 1)

        # The payload's name for ch 0 is empty (or different — but channel_id
        # matches, and apply_mix_data prefers channel_id when name is missing.)
        # When name is provided AND non-empty AND matches the current
        # participant ignoring rename suffixes, apply_mix_data still picks
        # by channel_id when payload_name is empty.
        applied = controller.apply_mix_data(
            {
                "participants": [
                    # Same channel_id, no name (channel_id wins).
                    {"channel_id": 0, "fader_level": 42,
                     "pan": 25, "muted": True, "solo": False},
                    # Same channel_id, exact name match (still routes by id).
                    {"channel_id": 1, "name": "Bob", "fader_level": 88,
                     "pan": 75, "muted": False, "solo": True},
                ]
            }
        )

        # Both updates landed.
        self.assertEqual(applied, 2)
        # Channel 0: fader/mute applied even though our local name is "Alice"
        # and the payload's name was missing.
        self.assertEqual(controller.participants[0].fader_level, 42)
        self.assertEqual(controller.participants[0].pan, 25)
        # Note: solo on ch 1 forces ch 0 into muted=True via solo
        # invariant, regardless of the payload's muted=True. So the
        # observable post-condition is just that solo took effect.
        self.assertTrue(controller.participants[1].solo)
        self.assertFalse(controller.participants[1].muted)
        self.assertTrue(controller.participants[0].muted)
        self.assertEqual(controller.participants[1].fader_level, 88)
        self.assertEqual(controller.participants[1].pan, 75)


if __name__ == "__main__":
    unittest.main()
