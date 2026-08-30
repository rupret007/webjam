"""Concurrency / thread-safety tests for JamulusRpcClient.

Focus on Python-side races in the new TCP client: request-id uniqueness under
contention, safe no-op commands when disconnected, heartbeat read/write races,
and stop() racing with concurrent callers.  No real socket is used.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from core.jamulus_rpc_client import JamulusRpcClient


class TestRpcClientConcurrency(unittest.TestCase):
    def test_next_id_is_unique_under_threads(self):
        client = JamulusRpcClient(port=22222)
        ids: list[int] = []
        ids_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait(timeout=2)
            local = [client._next_id() for _ in range(200)]
            with ids_lock:
                ids.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        for t in threads:
            self.assertFalse(t.is_alive())
        self.assertEqual(len(ids), 8 * 200)
        self.assertEqual(len(set(ids)), len(ids), "request ids collided")

    def test_set_channel_gain_safe_when_disconnected(self):
        """No socket → commands must no-op (return False) under concurrency
        without raising or leaving torn state."""
        client = JamulusRpcClient(port=22222)
        errors: list[BaseException] = []
        lock = threading.Lock()
        barrier = threading.Barrier(4)

        def writer(ch):
            try:
                barrier.wait(timeout=2)
                for level in range(50):
                    self.assertFalse(client.set_channel_gain(ch, level))
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        for t in threads:
            self.assertFalse(t.is_alive(), "deadlock")
        self.assertFalse(errors, f"raised: {errors!r}")

    def test_stop_racing_with_callers(self):
        client = JamulusRpcClient(port=22222)
        with patch.object(client, "_read_secret", return_value=None):
            client.start()  # reader thread retries without opening a socket
            errors: list[BaseException] = []
            lock = threading.Lock()
            stop_evt = threading.Event()

            def hammer():
                try:
                    while not stop_evt.is_set():
                        client.set_channel_gain(0, 64)
                        client._next_id()
                        client.last_activity_age()
                except BaseException as exc:  # noqa: BLE001
                    with lock:
                        errors.append(exc)

            t = threading.Thread(target=hammer)
            t.start()
            time.sleep(0.1)
            client.stop()  # race stop against the hammer
            time.sleep(0.1)
            stop_evt.set()
            t.join(timeout=5)

        self.assertFalse(t.is_alive(), "caller deadlocked across stop()")
        self.assertFalse(errors, f"raised: {errors!r}")
        self.assertFalse(client._available)

    def test_last_activity_age_thread_safe(self):
        client = JamulusRpcClient(port=22222)
        client._last_activity_at = time.monotonic()
        stop = threading.Event()
        negatives: list[float] = []

        def stamper():
            while not stop.is_set():
                client._stamp()

        def reader():
            for _ in range(3000):
                if client.last_activity_age() < 0:
                    negatives.append(client.last_activity_age())

        stampers = [threading.Thread(target=stamper) for _ in range(3)]
        readers = [threading.Thread(target=reader) for _ in range(2)]
        for t in stampers + readers:
            t.start()
        for t in readers:
            t.join(timeout=5)
        stop.set()
        for t in stampers:
            t.join(timeout=5)

        for t in stampers + readers:
            self.assertFalse(t.is_alive())
        self.assertEqual(negatives, [], "last_activity_age went negative")


if __name__ == "__main__":
    unittest.main()
