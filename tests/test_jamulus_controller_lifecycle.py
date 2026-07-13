"""
JamulusController lifecycle + RPC/UDP callback routing coverage.

Exercises start/stop component orchestration, the monitor loop's exception
recovery, RPC participant normalization (object and dict forms), UDP-vs-RPC
precedence gating, the RPC convenience wrappers (set_self_muted / send_chat /
set_name), chat forwarding, callback bookkeeping, diagnostics, and the
save_mix error path.  No real sockets or audio devices are touched.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jamulus_controller import JamulusController


def _make_controller() -> JamulusController:
    c = JamulusController(host="127.0.0.1", port=22124, rpc_port=22222)
    c.settings.host_server_enabled = False
    c.rpc_client = MagicMock()
    c.rpc_client.available = False
    c.protocol = MagicMock()
    c.protocol.request_clients.return_value = None
    c.audio_engine = MagicMock()
    return c


class TestStartStopLifecycle(unittest.TestCase):
    def test_start_starts_components_and_monitor_thread(self):
        c = _make_controller()
        c.rpc_client.available = True  # keep _check_participants a no-op
        try:
            c.start()
            self.assertTrue(c.running)
            c.audio_engine.start.assert_called_once()
            c.rpc_client.start.assert_called_once()
            c.protocol.start_receiving.assert_called_once()
            self.assertIsNotNone(c.monitor_thread)
            self.assertTrue(c.monitor_thread.is_alive())
        finally:
            c.stop()
        self.assertFalse(c.running)
        c.rpc_client.stop.assert_called_once()
        c.protocol.stop_receiving.assert_called_once()
        c.audio_engine.clear_level_overrides.assert_called_once()
        c.audio_engine.stop.assert_called_once()

    def test_start_is_idempotent(self):
        c = _make_controller()
        c.rpc_client.available = True
        try:
            c.start()
            c.start()
            self.assertEqual(c.rpc_client.start.call_count, 1)
        finally:
            c.stop()

    def test_rpc_and_udp_start_before_optional_audio_meter(self):
        c = _make_controller()
        order = []
        c.rpc_client.start.side_effect = lambda: order.append("rpc")
        c.protocol.start_receiving.side_effect = lambda: order.append("udp")
        c.audio_engine.start.side_effect = lambda: order.append("meter")
        try:
            c.start()
        finally:
            c.stop()
        self.assertEqual(order[:3], ["rpc", "udp", "meter"])

    def test_stop_preserves_registered_callbacks_for_relaunch(self):
        c = _make_controller()
        c.rpc_client.available = True
        callback = MagicMock()
        c.register_callback(callback)
        c.start()
        c.stop()
        self.assertEqual(c.callbacks, [callback])

    def test_stop_survives_audio_engine_without_override_clear(self):
        c = _make_controller()
        c.rpc_client.available = True

        class _MinimalEngine:
            def start(self):
                pass

            def stop(self):
                pass

        c.audio_engine = _MinimalEngine()  # no clear_level_overrides attr
        c.start()
        c.stop()  # must not raise
        self.assertFalse(c.running)

    def test_monitor_loop_records_error_and_continues(self):
        c = _make_controller()
        c.running = True

        def _boom():
            c.running = False  # exit loop after the failure iteration
            raise RuntimeError("poll exploded")

        c._check_participants = _boom
        with patch("jamulus_controller.time.sleep"):
            c._monitor_loop()
        self.assertIn("poll exploded", c.last_error)


class TestUnconfiguredHostFallback(unittest.TestCase):
    """Fresh installs ship with jamulus_server = "" — the controller must
    still construct (the app has to start so the wizard can run).
    Regression: empty host used to propagate into JamulusProtocolAdapter,
    which raises ValueError, crashing the whole app at startup."""

    def test_empty_host_falls_back_to_loopback(self):
        c = JamulusController(host="", port=22124, rpc_port=22222)
        self.assertEqual(c.host, "127.0.0.1")

    def test_whitespace_host_falls_back_to_loopback(self):
        c = JamulusController(host="   ", port=22124, rpc_port=22222)
        self.assertEqual(c.host, "127.0.0.1")

    def test_real_host_is_kept(self):
        c = JamulusController(host="band.example.com", port=22124, rpc_port=22222)
        self.assertEqual(c.host, "band.example.com")

    def test_legacy_udp_monitor_is_dormant_in_product(self):
        """The monitor registers as a phantom musician when enabled."""
        c = JamulusController(host="127.0.0.1", port=22124, rpc_port=22222)
        self.assertFalse(c.protocol.enabled)
        c.protocol.start_receiving()
        self.assertFalse(c.protocol._running)
        self.assertIsNone(c.protocol._sock)
        self.assertIsNone(c.protocol._rx_thread)


class TestRpcCallbackRouting(unittest.TestCase):
    def test_rpc_participants_accepts_objects_and_dicts(self):
        c = _make_controller()
        c._on_rpc_participants([
            SimpleNamespace(channel_id=0, name="Jeff", instrument="guitar",
                            is_local=True),
            {"channel_id": 1, "name": "Ann", "instrument": "bass",
             "is_local": False},
            {"channel_id": -1, "name": "ignored"},   # invalid id dropped
        ])
        self.assertEqual(set(c.participants), {0, 1})
        self.assertEqual(c.participants[0].name, "Jeff")
        self.assertEqual(c.participants[0].instrument, "guitar")
        self.assertTrue(c.participants[0].is_local)
        self.assertEqual(c.participants[1].name, "Ann")
        self.assertEqual(c.participants[1].instrument, "bass")
        self.assertFalse(c.participants[1].is_local)

    def test_rpc_participants_fills_blank_names(self):
        c = _make_controller()
        c._on_rpc_participants([{"channel_id": 3, "name": ""}])
        self.assertEqual(c.participants[3].name, "Participant 3")

    def test_empty_rpc_participants_clears_cache_and_notifies_when_changed(self):
        c = _make_controller()
        callback = MagicMock()
        c.register_callback(callback)
        c._on_rpc_participants([{"channel_id": 3, "name": "Ann"}])
        callback.reset_mock()

        c._on_rpc_participants([])

        self.assertEqual(c.participants, {})
        callback.assert_called_once_with([])

    def test_empty_rpc_participants_does_not_notify_when_already_empty(self):
        c = _make_controller()
        callback = MagicMock()
        c.register_callback(callback)

        c._on_rpc_participants([])

        callback.assert_not_called()

    def test_rpc_levels_set_engine_overrides(self):
        c = _make_controller()
        c._on_rpc_levels({0: 0.5, 1: 0.9})
        c.audio_engine.set_level_override.assert_any_call(0, 0.5)
        c.audio_engine.set_level_override.assert_any_call(1, 0.9)

    def test_udp_participants_ignored_when_rpc_available(self):
        c = _make_controller()
        c.rpc_client.available = True
        c._on_udp_participants({0: "Someone"})
        self.assertEqual(len(c.participants), 0)

    def test_udp_participants_used_when_rpc_unavailable(self):
        c = _make_controller()
        c.rpc_client.available = False
        c._on_udp_participants({0: "Someone"})
        self.assertEqual(c.participants[0].name, "Someone")

    def test_udp_levels_gated_by_rpc_availability(self):
        c = _make_controller()
        c.rpc_client.available = True
        c._on_udp_levels({0: 0.7})
        c.audio_engine.set_level_override.assert_not_called()
        c.rpc_client.available = False
        c._on_udp_levels({0: 0.7})
        c.audio_engine.set_level_override.assert_called_once_with(0, 0.7)


class TestCheckParticipantsNormalization(unittest.TestCase):
    def test_non_dict_payload_is_ignored(self):
        c = _make_controller()
        c.protocol.request_clients.return_value = ["not", "a", "dict"]
        c._check_participants()
        self.assertEqual(len(c.participants), 0)

    def test_payload_keys_and_names_are_normalized(self):
        c = _make_controller()
        c.protocol.request_clients.return_value = {
            "1": "  Ann  ",     # string key + padded name
            "x": "bad",         # unparseable key dropped
            -2: "negative",     # negative id dropped
            2: 42,              # non-string name → placeholder
        }
        c._check_participants()
        self.assertEqual(set(c.participants), {1, 2})
        self.assertEqual(c.participants[1].name, "Ann")
        self.assertEqual(c.participants[2].name, "Participant 2")
        self.assertEqual(c.last_error, "")

    def test_noop_when_rpc_available(self):
        c = _make_controller()
        c.rpc_client.available = True
        c._check_participants()
        c.protocol.request_clients.assert_not_called()

    def test_hosted_server_roster_is_truth_when_client_rpc_stalls(self):
        c = _make_controller()
        c.settings.host_server_enabled = True
        c.settings.server_rpc_secret_file = "/tmp/host.secret"
        c.settings.server_rpc_port = 22240
        c.settings.musician_name = "Jeff"
        rpc = MagicMock()
        rpc.get_clients.return_value = {
            "connections": 2,
            "clients": [
                {"id": 0, "name": "", "address": "127.0.0.1:50000"},
                {"id": 4, "name": "Ann", "address": "192.0.2.4:50001"},
            ],
        }
        rpc_class = MagicMock()
        rpc_class.return_value.__enter__.return_value = rpc
        with (
            patch("core.jamulus_server_rpc.read_secret_file", return_value="secret"),
            patch("core.jamulus_server_rpc.JamulusServerRpc", rpc_class),
        ):
            c._check_participants()

        self.assertEqual(set(c.participants), {0, 4})
        self.assertEqual(c.participants[0].name, "Jeff")
        self.assertTrue(c.participants[0].is_local)
        self.assertEqual(c.participants[4].name, "Ann")
        self.assertFalse(c.participants[4].is_local)
        c.protocol.request_clients.assert_not_called()
        rpc_class.assert_called_once_with(port=22240, secret="secret")

    def test_successful_empty_hosted_roster_clears_stale_participants(self):
        c = _make_controller()
        c.settings.host_server_enabled = True
        c.settings.server_rpc_secret_file = "/tmp/host.secret"
        c.add_participant("Gone", 7)
        rpc = MagicMock()
        rpc.get_clients.return_value = {"connections": 0, "clients": []}
        rpc_class = MagicMock()
        rpc_class.return_value.__enter__.return_value = rpc
        with (
            patch("core.jamulus_server_rpc.read_secret_file", return_value="secret"),
            patch("core.jamulus_server_rpc.JamulusServerRpc", rpc_class),
        ):
            c._check_participants()
        self.assertEqual(c.participants, {})

    def test_hosted_roster_failure_falls_back_to_udp(self):
        c = _make_controller()
        c.settings.host_server_enabled = True
        c.settings.server_rpc_secret_file = "/tmp/missing.secret"
        c.protocol.request_clients.return_value = {3: "UDP Musician"}
        with patch(
            "core.jamulus_server_rpc.read_secret_file",
            side_effect=OSError("missing"),
        ):
            c._check_participants()
        self.assertEqual(c.participants[3].name, "UDP Musician")


class TestRpcConvenienceWrappers(unittest.TestCase):
    def test_set_self_muted_false_when_rpc_unavailable(self):
        c = _make_controller()
        self.assertFalse(c.set_self_muted(True))
        c.rpc_client.set_self_muted.assert_not_called()

    def test_set_self_muted_success(self):
        c = _make_controller()
        c.rpc_client.available = True
        c.rpc_client.set_self_muted.return_value = True
        self.assertTrue(c.set_self_muted(True))

    def test_set_self_muted_swallows_rpc_errors(self):
        c = _make_controller()
        c.rpc_client.available = True
        c.rpc_client.set_self_muted.side_effect = RuntimeError("boom")
        self.assertFalse(c.set_self_muted(True))

    def test_send_chat_rejects_empty_and_unavailable(self):
        c = _make_controller()
        c.rpc_client.available = True
        self.assertFalse(c.send_chat(""))
        c.rpc_client.available = False
        self.assertFalse(c.send_chat("hello"))

    def test_send_chat_success_and_error(self):
        c = _make_controller()
        c.rpc_client.available = True
        c.rpc_client.send_chat_text.return_value = True
        self.assertTrue(c.send_chat("hello band"))
        c.rpc_client.send_chat_text.side_effect = RuntimeError("boom")
        self.assertFalse(c.send_chat("hello band"))

    def test_set_name_rejects_empty_and_unavailable(self):
        c = _make_controller()
        c.rpc_client.available = True
        self.assertFalse(c.set_name(""))
        c.rpc_client.available = False
        self.assertFalse(c.set_name("Jeff"))

    def test_set_name_success_and_error(self):
        c = _make_controller()
        c.rpc_client.available = True
        c.rpc_client.set_name.return_value = True
        self.assertTrue(c.set_name("Jeff"))
        c.rpc_client.set_name.side_effect = RuntimeError("boom")
        self.assertFalse(c.set_name("Jeff"))


class TestChatForwarding(unittest.TestCase):
    def test_chat_without_callback_is_noop(self):
        c = _make_controller()
        c.chat_callback = None
        c._on_rpc_chat("hello")  # must not raise

    def test_chat_forwarded_to_callback(self):
        c = _make_controller()
        received = []
        c.chat_callback = received.append
        c._on_rpc_chat("hello band")
        self.assertEqual(received, ["hello band"])

    def test_failing_chat_callback_is_swallowed(self):
        c = _make_controller()
        c.chat_callback = MagicMock(side_effect=RuntimeError("boom"))
        c._on_rpc_chat("hello")  # must not raise


class TestCallbackBookkeeping(unittest.TestCase):
    def test_unregister_missing_callback_is_silent(self):
        c = _make_controller()
        c.unregister_callback(lambda participants: None)  # never registered

    def test_notify_survives_failing_callback(self):
        c = _make_controller()
        failing = MagicMock(side_effect=RuntimeError("boom"))
        healthy = MagicMock()
        c.register_callback(failing)
        c.register_callback(healthy)
        c._notify_callbacks()
        healthy.assert_called_once()

    def test_unregister_stops_future_notifications(self):
        c = _make_controller()
        cb = MagicMock()
        c.register_callback(cb)
        c.unregister_callback(cb)
        c._notify_callbacks()
        cb.assert_not_called()


class TestDiagnosticsAndSaveErrors(unittest.TestCase):
    def test_get_audio_diagnostics_shape(self):
        c = _make_controller()
        c.audio_engine.diagnostics.return_value = SimpleNamespace(
            backend="synthetic", samplerate=48000, blocksize=64,
            latency_mode="low", active=True, message="ok",
        )
        c.last_error = ""
        diag = c.get_audio_diagnostics()
        self.assertEqual(diag["backend"], "synthetic")
        self.assertEqual(diag["samplerate"], "48000")
        self.assertEqual(diag["last_error"], "none")

    def test_save_mix_raises_and_cleans_temp_on_write_failure(self):
        import tempfile
        from pathlib import Path
        c = _make_controller()
        c.add_participant("Jeff", 0)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "mix.json"
            with patch(
                "jamulus_controller.os.fdopen",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(OSError):
                    c.save_mix(str(target))
            self.assertFalse(target.exists())
            leftovers = [p for p in Path(tmp).iterdir()]
            self.assertEqual(leftovers, [], f"temp files leaked: {leftovers}")


if __name__ == "__main__":
    unittest.main()
