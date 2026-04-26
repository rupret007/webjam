"""
Concurrency stress tests for ``JamulusRpcClient``.

These tests focus on the client's internal lock usage, heartbeat stamp
ordering, and lifecycle safety when commands fire from multiple threads at
once.  The HTTP layer (``httpx.post``) is mocked because the goal is to
flush out Python-side races, not exercise the wire protocol.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.jamulus_rpc_client import JamulusRpcClient


def _make_canned_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


class TestRpcClientConcurrency(unittest.TestCase):
    def test_concurrent_set_channel_gain_and_get_clients(self):
        """set_channel_gain from multiple threads while another polls
        get_channel_clients.  No torn state, no exceptions."""
        client = JamulusRpcClient(port=22222)

        # Canned responses: one for setChannelGain (success), one for
        # getChannelClients (empty list result), one for getClientInfo.
        canned_set = _make_canned_response({"result": "ok"})
        canned_get = _make_canned_response({"result": []})
        canned_info = _make_canned_response({"result": {"channelId": 0}})

        def _fake_post(url, json=None, timeout=None, **kwargs):
            method = (json or {}).get("method", "")
            if method == "jamulus/setChannelGain":
                return canned_set
            if method == "jamulus/getClientInfo":
                return canned_info
            return canned_get

        barrier = threading.Barrier(4)
        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def _gain_writer(channel_id: int):
            try:
                barrier.wait(timeout=2)
                for level in range(50):
                    client.set_channel_gain(channel_id, level)
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        def _poller():
            try:
                barrier.wait(timeout=2)
                for _ in range(50):
                    result = client.get_channel_clients()
                    # result is allowed to be [] but must not be a torn list.
                    self.assertIsInstance(result, list)
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        with patch("core.jamulus_rpc_client.httpx.post", side_effect=_fake_post):
            threads = [
                threading.Thread(target=_gain_writer, args=(0,), name="gain-0"),
                threading.Thread(target=_gain_writer, args=(1,), name="gain-1"),
                threading.Thread(target=_gain_writer, args=(2,), name="gain-2"),
                threading.Thread(target=_poller, name="poller"),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=4)

        for t in threads:
            self.assertFalse(t.is_alive(), f"{t.name} deadlocked")
        self.assertFalse(errors, f"Worker threads raised: {errors!r}")
        # Request counter is monotonic and reflects all 200 calls
        # (3*50 set + 50 get = 200; getClientInfo may have been called once
        # to resolve the local channel ID).
        self.assertGreaterEqual(client._request_counter, 200)

    def test_set_channel_gain_during_stop(self):
        """``stop()`` racing with an in-flight ``set_channel_gain`` must not
        deadlock or crash."""
        client = JamulusRpcClient(port=22222)

        in_call = threading.Event()
        release = threading.Event()
        canned_set = _make_canned_response({"result": "ok"})

        def _slow_post(url, json=None, timeout=None, **kwargs):
            in_call.set()
            # Block until the stopper has had a chance to call stop().
            release.wait(timeout=2)
            return canned_set

        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def _gainer():
            try:
                client.set_channel_gain(5, 80)
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        def _stopper():
            try:
                in_call.wait(timeout=2)
                client.stop()
                # Release the in-flight call so the gainer thread finishes.
                release.set()
            except BaseException as exc:  # noqa: BLE001
                _record(exc)
                release.set()

        with patch("core.jamulus_rpc_client.httpx.post", side_effect=_slow_post):
            t_gain = threading.Thread(target=_gainer, name="gain")
            t_stop = threading.Thread(target=_stopper, name="stop")
            t_gain.start()
            t_stop.start()
            t_gain.join(timeout=3)
            t_stop.join(timeout=3)

        self.assertFalse(t_gain.is_alive(), "set_channel_gain blocked across stop")
        self.assertFalse(t_stop.is_alive(), "stop() blocked")
        self.assertFalse(errors, f"Worker threads raised: {errors!r}")
        # After stop, _available must be False — even if the in-flight call
        # eventually succeeded.
        self.assertFalse(client._available)

    def test_last_activity_age_thread_safe(self):
        """Concurrent SSE-event stamps + ``last_activity_age()`` reads.

        ``last_activity_age()`` must never return a negative value, even when
        another thread is rewriting ``_last_activity_at``.
        """
        client = JamulusRpcClient(port=22222)
        # Seed so age starts finite.
        client._last_activity_at = time.monotonic()

        stop = threading.Event()
        errors: list[BaseException] = []
        errors_lock = threading.Lock()
        observed_negative: list[float] = []

        def _record(exc: BaseException) -> None:
            with errors_lock:
                errors.append(exc)

        def _stamper():
            try:
                while not stop.is_set():
                    # Mimic the SSE handler's stamp.
                    client._last_activity_at = time.monotonic()
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        def _reader():
            try:
                for _ in range(2000):
                    age = client.last_activity_age()
                    if age < 0:
                        observed_negative.append(age)
            except BaseException as exc:  # noqa: BLE001
                _record(exc)

        stampers = [
            threading.Thread(target=_stamper, name=f"stamp-{i}")
            for i in range(3)
        ]
        readers = [
            threading.Thread(target=_reader, name=f"read-{i}")
            for i in range(2)
        ]
        for t in stampers + readers:
            t.start()
        for t in readers:
            t.join(timeout=3)
        stop.set()
        for t in stampers:
            t.join(timeout=3)

        for t in stampers + readers:
            self.assertFalse(t.is_alive(), f"{t.name} deadlocked")
        self.assertFalse(errors, f"Worker threads raised: {errors!r}")
        self.assertEqual(
            observed_negative, [],
            f"last_activity_age() returned negative: {observed_negative!r}",
        )


if __name__ == "__main__":
    unittest.main()
