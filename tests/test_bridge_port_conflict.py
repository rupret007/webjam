"""Regression tests for BridgeService port-conflict detection (v0.4.5).

Verifies ``_is_rpc_port_in_use()`` correctly detects an occupied JSON-RPC
port and that ``launch_jamulus(manual=True)`` short-circuits with an
actionable error instead of spawning Jamulus when the port is taken.
"""
from __future__ import annotations

import socket
import sys
import unittest
from unittest.mock import MagicMock, patch

from tests.support.component_store import isolated_component_store_root


def _make_settings(jamulus_rpc_port: int = 22222) -> MagicMock:
    s = MagicMock()
    s.jamulus_server = "jam.example.com"
    s.jamulus_port = 22124
    s.jamulus_rpc_port = jamulus_rpc_port
    s.jamulus_candidates = ["C:/Jamulus.exe"]
    s.webex_url = "https://example.webex.com/meet/test"
    return s


def _make_bridge(rpc_port: int = 22222):
    from services.bridge_service import BridgeService

    settings = _make_settings(jamulus_rpc_port=rpc_port)
    repository = MagicMock()
    repository.get_setting.return_value = "1"

    ui_callbacks = {
        "set_status_banner": MagicMock(),
        "refresh_readiness": MagicMock(),
        "show_actionable_error": MagicMock(),
        "show_message": MagicMock(),
        "shutdown_requested": lambda: False,
        "schedule_ui_callback": lambda f: f(),
    }
    return BridgeService(
        jamulus_controller=MagicMock(),
        webex_controller=MagicMock(),
        metrics_service=MagicMock(),
        repository=repository,
        settings=settings,
        ui_callbacks=ui_callbacks,
        component_store_root=isolated_component_store_root(),
    )


def _free_port() -> int:
    """Bind ephemeral, capture the port, close — then assume it stays free."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _leave_loopback_port_in_time_wait() -> int:
    """Close the accepted side first so its local port enters TIME_WAIT."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(1)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    accepted, _ = listener.accept()
    accepted.shutdown(socket.SHUT_WR)
    assert client.recv(1) == b""

    client.close()
    accepted.close()
    listener.close()
    return port


class TestRpcPortDetection(unittest.TestCase):
    def test_returns_false_when_port_is_free(self):
        port = _free_port()
        bridge = _make_bridge(rpc_port=port)
        self.assertFalse(bridge._is_rpc_port_in_use())

    def test_returns_true_when_wildcard_listener_owns_port(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
        sock.listen(1)
        try:
            bridge = _make_bridge(rpc_port=port)
            self.assertTrue(bridge._is_rpc_port_in_use())
        finally:
            sock.close()

    @unittest.skipUnless(sys.platform == "darwin", "macOS TCP semantics")
    def test_returns_false_when_only_time_wait_blocks_strict_bind(self):
        port = _leave_loopback_port_in_time_wait()

        strict = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(OSError):
                strict.bind(("127.0.0.1", port))
        finally:
            strict.close()

        bridge = _make_bridge(rpc_port=port)
        self.assertFalse(bridge._is_rpc_port_in_use())

    def test_non_macos_keeps_strict_fail_closed_behavior(self):
        bridge = _make_bridge()
        with patch("services.bridge_service.sys.platform", "win32"), patch.object(
            bridge,
            "_macos_rpc_port_is_rebindable",
        ) as fallback:
            with patch("socket.socket") as socket_cls:
                socket_cls.return_value.bind.side_effect = OSError("occupied")
                self.assertTrue(bridge._is_rpc_port_in_use())
        fallback.assert_not_called()

    def test_macos_fails_closed_when_reuse_probe_is_not_conclusive(self):
        bridge = _make_bridge()
        with patch("services.bridge_service.sys.platform", "darwin"), patch.object(
            bridge,
            "_macos_rpc_port_is_rebindable",
            return_value=False,
        ) as fallback:
            with patch("socket.socket") as socket_cls:
                socket_cls.return_value.bind.side_effect = OSError("occupied")
                self.assertTrue(bridge._is_rpc_port_in_use())
        fallback.assert_called_once_with(22222)

    def test_macos_reuse_probe_requires_loopback_and_wildcard_binds(self):
        bridge = _make_bridge()
        loopback_probe = MagicMock()
        wildcard_probe = MagicMock()
        with patch(
            "socket.socket",
            side_effect=(loopback_probe, wildcard_probe),
        ):
            self.assertTrue(bridge._macos_rpc_port_is_rebindable(22222))

        loopback_probe.bind.assert_called_once_with(("127.0.0.1", 22222))
        wildcard_probe.bind.assert_called_once_with(("0.0.0.0", 22222))
        for probe in (loopback_probe, wildcard_probe):
            probe.setsockopt.assert_called_once_with(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1,
            )
            probe.close.assert_called_once_with()

    def test_macos_reuse_probe_fails_closed_if_either_bind_fails(self):
        bridge = _make_bridge()
        loopback_probe = MagicMock()
        wildcard_probe = MagicMock()
        wildcard_probe.bind.side_effect = OSError("occupied")
        with patch(
            "socket.socket",
            side_effect=(loopback_probe, wildcard_probe),
        ):
            self.assertFalse(bridge._macos_rpc_port_is_rebindable(22222))

        loopback_probe.close.assert_called_once_with()
        wildcard_probe.close.assert_called_once_with()

    def test_returns_true_when_port_is_bound(self):
        # Bind a socket on a real ephemeral port and verify detection.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        try:
            bridge = _make_bridge(rpc_port=port)
            self.assertTrue(bridge._is_rpc_port_in_use())
        finally:
            sock.close()

        # After close, the port should once again read as free.
        bridge = _make_bridge(rpc_port=port)
        self.assertFalse(bridge._is_rpc_port_in_use())


class TestLaunchAbortsOnPortConflict(unittest.TestCase):
    def test_manual_launch_calls_show_actionable_error_when_port_in_use(self):
        # Occupy a real port to force the conflict path.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        try:
            bridge = _make_bridge(rpc_port=port)
            retry = MagicMock()
            bridge.retry_audio_launch = retry
            # Make find_jamulus succeed so we reach the port check.
            with patch.object(bridge, "find_jamulus", return_value="C:/Jamulus.exe"), \
                 patch("services.bridge_service.threading.Thread") as thread_cls:
                thread_cls.return_value = MagicMock()
                bridge.launch_jamulus(manual=True, reconnect=False)

            # Thread for _do_launch must NOT have been started.
            thread_cls.assert_not_called()
            # The musician sees the cause without RPC/port vocabulary.
            bridge.show_actionable_error.assert_called_once()
            args, kwargs = bridge.show_actionable_error.call_args
            self.assertEqual(args[0], "Another audio session is open")
            self.assertNotIn("port", kwargs["what_failed"].lower())
            self.assertEqual(bridge.jamulus_state, "Port in use")
            bridge.metrics_service.increment.assert_any_call(
                "metric_jamulus_port_conflict"
            )
            # The callback captured by the dialog must re-enter the controller
            # path rather than launching a client behind its timer/peer state.
            kwargs["retry_callback"]()
            retry.assert_called_once_with()
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
