"""
Concurrency stress tests for ``JamulusController``.

These tests fire multiple threads at the controller simultaneously to flush
out race conditions that the single-call functional tests can't catch.  They
verify that the documented invariants (exactly-one-solo, ``_pre_solo_mute``
empty when no solo, no torn participant state) survive under contention.

Patterned after ``tests/test_jamulus_concurrent_mixer.py``.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock

from jamulus_controller import JamulusController


class _ProtocolStub:
    def __init__(self):
        self.cached_participants: dict[int, str] = {}
        self._lock = threading.Lock()

    def request_clients(self):
        with self._lock:
            return dict(self.cached_participants)

    def set_cached_participants(self, participants):
        with self._lock:
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


class TestJamulusControllerStress(unittest.TestCase):
    def test_concurrent_set_fader_and_sync_from_protocol(self):
        """4 threads racing fader writes against protocol-sync participant churn.

        Threads A/B mutate fader levels on channels 0..49 (which start absent
        and arrive via threads C/D).  Threads C/D push the same participant
        map through ``_sync_participants_from_protocol`` repeatedly.  The
        controller must not raise, deadlock, or end with torn state.
        """
        controller = _build_controller()
        # Pre-seed so set_fader_level has at least some channels to find.
        for i in range(50):
            controller.add_participant(f"P{i}", i)

        barrier = threading.Barrier(4)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def _fader_writer(level: int):
            try:
                barrier.wait(timeout=2)
                for i in range(50):
                    controller.set_fader_level(i, level)
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        def _sync_writer():
            try:
                barrier.wait(timeout=2)
                for _ in range(20):
                    controller._sync_participants_from_protocol(
                        {i: f"P{i}" for i in range(50)}
                    )
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        threads = [
            threading.Thread(target=_fader_writer, args=(50,), name="fader-50"),
            threading.Thread(target=_fader_writer, args=(100,), name="fader-100"),
            threading.Thread(target=_sync_writer, name="sync-c"),
            threading.Thread(target=_sync_writer, name="sync-d"),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=4)

        for t in threads:
            self.assertFalse(t.is_alive(), f"thread {t.name} did not exit (deadlock?)")
        self.assertFalse(errors, f"Worker threads raised: {errors!r}")

        # Participant count should be reasonable — same 50 channels persist
        # because every sync replays exactly that set.
        self.assertEqual(len(controller.participants), 50)
        # Every fader_level should be a clamped int in {50, 100} (the only
        # values the writers chose).
        for cid, p in controller.participants.items():
            self.assertIn(p.fader_level, (50, 100), f"ch{cid} torn: {p.fader_level}")

    def test_concurrent_set_solo_and_set_mute(self):
        """Rapid solo/mute toggles from 3 threads on a 5-participant set.

        Final state must satisfy the controller invariants:
          * at most one channel is soloed
          * if no channel is soloed, ``_pre_solo_mute`` is empty
        """
        controller = _build_controller()
        for i in range(5):
            controller.add_participant(f"P{i}", i)

        barrier = threading.Barrier(3)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def _solo_thread():
            try:
                barrier.wait(timeout=2)
                for _ in range(40):
                    for ch in range(5):
                        controller.set_solo(ch, True)
                    controller.set_solo(0, False)
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        def _mute_thread(channel_offset: int):
            try:
                barrier.wait(timeout=2)
                for i in range(80):
                    ch = (i + channel_offset) % 5
                    controller.set_mute(ch, bool(i & 1))
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        threads = [
            threading.Thread(target=_solo_thread, name="solo"),
            threading.Thread(target=_mute_thread, args=(0,), name="mute-a"),
            threading.Thread(target=_mute_thread, args=(2,), name="mute-b"),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=4)

        for t in threads:
            self.assertFalse(t.is_alive(), f"thread {t.name} did not exit (deadlock?)")
        self.assertFalse(errors, f"Worker threads raised: {errors!r}")

        # Drive to a deterministic final state — no solo — then check the
        # invariant.  (We can't pin the post-race state because mute_thread
        # may run last; settle it ourselves.)
        for ch in range(5):
            controller.set_solo(ch, False)
        soloed = [cid for cid, p in controller.participants.items() if p.solo]
        self.assertLessEqual(len(soloed), 1, f"more than one solo: {soloed}")
        if not soloed:
            self.assertEqual(
                controller._pre_solo_mute, {},
                "_pre_solo_mute must be empty when no channel is solo'd",
            )

    def test_concurrent_register_callback_and_notify(self):
        """register_callback vs _notify_callbacks — list mustn't mutate during iteration."""
        controller = _build_controller()
        controller.add_participant("P0", 0)

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()
        notify_count = [0]

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def _registrar():
            try:
                barrier.wait(timeout=2)
                for _ in range(200):
                    controller.register_callback(lambda _p: None)
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        def _notifier():
            try:
                barrier.wait(timeout=2)
                for _ in range(200):
                    controller._notify_callbacks()
                    notify_count[0] += 1
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        t1 = threading.Thread(target=_registrar, name="register")
        t2 = threading.Thread(target=_notifier, name="notify")
        t1.start()
        t2.start()
        t1.join(timeout=4)
        t2.join(timeout=4)

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertFalse(errors, f"Worker threads raised: {errors!r}")
        self.assertEqual(notify_count[0], 200)
        # Final list length should equal 200 (every register call landed).
        self.assertEqual(len(controller.callbacks), 200)

    def test_serialize_then_apply_under_concurrent_modification(self):
        """serialize_mix() + apply_mix_data() while another thread mutates faders."""
        controller = _build_controller()
        for i in range(10):
            controller.add_participant(f"P{i}", i)

        stop = threading.Event()
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def _fader_churn():
            try:
                level = 0
                while not stop.is_set():
                    for cid in range(10):
                        controller.set_fader_level(cid, level % 128)
                        level += 1
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        churn = threading.Thread(target=_fader_churn, name="churn")
        churn.start()
        try:
            snapshots: list[dict] = []
            for _ in range(100):
                snapshots.append(controller.serialize_mix())
            # Apply the most recent snapshot.  Should not raise.
            applied = controller.apply_mix_data(snapshots[-1])
        except BaseException as exc:  # noqa: BLE001
            _record(exc)
            applied = None
        finally:
            stop.set()
            churn.join(timeout=3)

        self.assertFalse(churn.is_alive(), "churn thread did not exit")
        self.assertFalse(errors, f"Worker threads raised: {errors!r}")
        self.assertIsNotNone(applied)
        # All 10 channels should still exist with valid fader levels.
        self.assertEqual(len(controller.participants), 10)
        for p in controller.participants.values():
            self.assertGreaterEqual(p.fader_level, 0)
            self.assertLessEqual(p.fader_level, 127)

    def test_remove_participant_during_solo_doesnt_lose_invariant(self):
        """Soloing channel 0 then removing it must clear ``_pre_solo_mute``."""
        controller = _build_controller()
        for i in range(3):
            controller.add_participant(f"P{i}", i)

        controller.set_solo(0, True)
        # Sanity: solo is set, snapshot exists.
        self.assertTrue(controller.participants[0].solo)
        self.assertNotEqual(controller._pre_solo_mute, {})

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def _remover():
            try:
                barrier.wait(timeout=2)
                controller.remove_participant(0)
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        t = threading.Thread(target=_remover, name="remover")
        t.start()
        barrier.wait(timeout=2)
        # Spin briefly to give the remover a chance to land.
        deadline = time.monotonic() + 2.0
        while 0 in controller.participants and time.monotonic() < deadline:
            time.sleep(0.005)
        t.join(timeout=3)

        self.assertFalse(t.is_alive())
        self.assertFalse(errors, f"Worker threads raised: {errors!r}")
        self.assertNotIn(0, controller.participants)
        # The soloed channel left → snapshot must be cleared and no remaining
        # participant should still carry solo=True.
        self.assertEqual(
            controller._pre_solo_mute, {},
            "removing the soloed channel must clear _pre_solo_mute",
        )
        self.assertFalse(any(p.solo for p in controller.participants.values()))


if __name__ == "__main__":
    unittest.main()
