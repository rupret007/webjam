"""Tests for round-2 memory-leak fixes.

Covers:
* ``JamulusController.unregister_callback`` add/remove + silent-on-missing.
* ``JamulusController.stop`` preserves callbacks for Stop Audio -> Start Audio.
* ``JamulusProtocolAdapter._handle_packet`` caps the unknown-msg-id set.
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from core.jamulus_protocol import (
    JamulusProtocolAdapter,
    _MAX_UNKNOWN_MSG_IDS_REMEMBERED,
    _build_packet,
)
from jamulus_controller import JamulusController


class _ProtocolStub:
    def request_clients(self):
        return {}

    def set_cached_participants(self, participants):
        return None

    def apply_mixer(self, channel_id, fader_level, pan, muted):
        return None

    def connect(self):
        return None

    def close(self):
        return None

    def stop_receiving(self):
        return None


class _AudioEngineStub:
    def set_level_override(self, channel_id, level):
        return None

    def clear_level_overrides(self):
        return None

    def stop(self):
        return None

    def diagnostics(self):
        class _Diag:
            backend = "test"
            samplerate = 48000
            blocksize = 0
            latency_mode = "low"
            active = True
            message = "ok"

        return _Diag()


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
    rpc = MagicMock()
    rpc.available = False
    controller.rpc_client = rpc
    return controller


class TestJamulusControllerCallbackUnregister(unittest.TestCase):
    def test_unregister_callback_removes_from_list(self):
        controller = _build_controller()
        cb_a = lambda _participants: None  # noqa: E731
        cb_b = lambda _participants: None  # noqa: E731
        controller.register_callback(cb_a)
        controller.register_callback(cb_b)

        controller.unregister_callback(cb_a)

        self.assertEqual(controller.callbacks, [cb_b])

    def test_unregister_callback_silent_on_missing(self):
        controller = _build_controller()
        never_registered = lambda _participants: None  # noqa: E731

        # Should not raise even though callback was never registered.
        controller.unregister_callback(never_registered)
        self.assertEqual(controller.callbacks, [])

    def test_stop_preserves_callbacks_for_relaunch(self):
        controller = _build_controller()
        controller.register_callback(lambda _p: None)
        controller.register_callback(lambda _p: None)
        self.assertEqual(len(controller.callbacks), 2)

        controller.stop()

        self.assertEqual(len(controller.callbacks), 2)


class TestJamulusProtocolUnknownMsgIdCap(unittest.TestCase):
    def test_unknown_msg_ids_capped(self):
        adapter = JamulusProtocolAdapter("127.0.0.1", 22124, enabled=False)
        # Disable ACK send (no socket bound) so _send is a no-op.
        adapter._sock = None

        # Feed 1000 distinct unknown msg_ids.  Use msg_ids well outside the
        # known range so none get handled as a real message.
        for raw_id in range(10_000, 11_000):
            # Build a valid packet with empty payload so CRC passes.
            packet = _build_packet(raw_id, 0, b"")
            adapter._handle_packet(packet)

        self.assertLessEqual(
            len(adapter._unknown_msg_ids_seen),
            _MAX_UNKNOWN_MSG_IDS_REMEMBERED,
        )
        self.assertEqual(
            len(adapter._unknown_msg_ids_seen),
            _MAX_UNKNOWN_MSG_IDS_REMEMBERED,
        )


if __name__ == "__main__":
    unittest.main()
