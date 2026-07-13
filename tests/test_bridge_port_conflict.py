"""Regression tests for BridgeService port-conflict detection (v0.4.5).

Verifies ``_is_rpc_port_in_use()`` correctly detects an occupied JSON-RPC
port and that ``launch_jamulus(manual=True)`` short-circuits with an
actionable error instead of spawning Jamulus when the port is taken.
"""
from __future__ import annotations

import socket
import unittest
from unittest.mock import MagicMock, patch


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
    )


def _free_port() -> int:
    """Bind ephemeral, capture the port, close — then assume it stays free."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestRpcPortDetection(unittest.TestCase):
    def test_returns_false_when_port_is_free(self):
        port = _free_port()
        bridge = _make_bridge(rpc_port=port)
        self.assertFalse(bridge._is_rpc_port_in_use())

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
