"""
Verify that ``set_solo`` and ``_sync_participants_from_protocol`` racing on
two threads cannot leave the controller in an inconsistent state. Both
operations take ``_participants_lock``, so post-conditions must hold no
matter which thread wins.
"""
from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from jamulus_controller import JamulusController


class _ProtocolStub:
    def __init__(self):
        self.cached_participants: dict[int, str] = {}

    def request_clients(self):
        return dict(self.cached_participants)

    def set_cached_participants(self, participants):
        self.cached_participants = dict(participants)

    def apply_mixer(self, channel_id, fader_level, pan, muted):
        return None


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


class TestConcurrentMixer(unittest.TestCase):
    def test_set_solo_under_concurrent_rpc_update(self):
        controller = _build_controller()
        controller.add_participant("a", 0)
        controller.add_participant("b", 1)
        controller.add_participant("c", 2)

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def _solo_thread():
            try:
                barrier.wait(timeout=2)
                controller.set_solo(1, True)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def _sync_thread():
            try:
                barrier.wait(timeout=2)
                controller._sync_participants_from_protocol(
                    {0: "a", 1: "b", 2: "c"}
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=_solo_thread)
        t2 = threading.Thread(target=_sync_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertFalse(errors, f"Worker threads raised: {errors!r}")
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())

        # Channel 1 must be the only soloed channel post-conditions:
        # solo invariant holds and mute states are consistent with a single
        # soloed channel.
        self.assertEqual({0, 1, 2}, set(controller.participants.keys()))
        soloed = [cid for cid, p in controller.participants.items() if p.solo]
        self.assertEqual(soloed, [1])
        self.assertTrue(controller.participants[1].solo)
        self.assertFalse(controller.participants[1].muted)
        # All other channels are muted while ch 1 is soloed.
        self.assertTrue(controller.participants[0].muted)
        self.assertTrue(controller.participants[2].muted)

        # Leaving solo must restore the pre-solo state cleanly (no leftover
        # _pre_solo_mute snapshot, no stuck mutes).
        controller.set_solo(1, False)
        self.assertFalse(any(p.solo for p in controller.participants.values()))
        self.assertEqual(controller._pre_solo_mute, {})
        self.assertFalse(controller.participants[0].muted)
        self.assertFalse(controller.participants[2].muted)


if __name__ == "__main__":
    unittest.main()
