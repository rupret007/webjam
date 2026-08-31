"""Regression tests for the deep-dive audit remediation plan."""
from __future__ import annotations

import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.jamulus_protocol import (
    _map_level_list_to_channels,
    _parse_level_list,
)
from tests.support.component_store import isolated_component_store_root


pytestmark = pytest.mark.requires_local_socket


class TestUdpLevelChannelMapping(unittest.TestCase):
    def test_maps_by_participant_order_not_raw_index_as_channel_id(self):
        index_levels = _parse_level_list(b"\xff\xff\x00\x00")
        mapped = _map_level_list_to_channels(index_levels, [10, 20])
        self.assertIn(10, mapped)
        self.assertIn(20, mapped)
        self.assertNotIn(0, mapped)
        self.assertNotIn(1, mapped)


class TestBridgeReconnectPortGuard(unittest.TestCase):
    def test_reconnect_aborts_when_rpc_port_in_use(self):
        from services.bridge_service import BridgeService

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        try:
            settings = MagicMock()
            settings.jamulus_server = "jam.example.com"
            settings.jamulus_port = 22124
            settings.jamulus_rpc_port = port
            settings.jamulus_candidates = ["/bin/echo"]
            bridge = BridgeService(
                jamulus_controller=MagicMock(),
                webex_controller=MagicMock(),
                metrics_service=MagicMock(),
                repository=MagicMock(),
                settings=settings,
                ui_callbacks={
                    "set_status_banner": MagicMock(),
                    "refresh_readiness": MagicMock(),
                    "show_actionable_error": MagicMock(),
                    "show_message": MagicMock(),
                    "shutdown_requested": lambda: False,
                    "schedule_ui_callback": lambda f: f(),
                },
                component_store_root=isolated_component_store_root(),
            )
            bridge.jamulus_launch_intended = True
            with patch.object(bridge, "find_jamulus", return_value="/bin/echo"), \
                 patch("services.bridge_service.threading.Thread") as thread_cls:
                thread_cls.return_value = MagicMock()
                bridge.launch_jamulus(manual=False, reconnect=True)
            thread_cls.assert_not_called()
            bridge.metrics_service.increment.assert_any_call(
                "metric_jamulus_reconnect_failed"
            )
        finally:
            sock.close()


class TestEffectiveServerHostPort(unittest.TestCase):
    def test_does_not_double_append_port(self):
        from services.bridge_service import BridgeService

        settings = MagicMock()
        settings.jamulus_server = "jam.example.com:22124"
        settings.jamulus_port = 22124
        bridge = BridgeService(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), settings,
            ui_callbacks={"schedule_ui_callback": lambda f: f()},
            component_store_root=isolated_component_store_root(),
        )
        self.assertEqual(bridge.effective_server(), "jam.example.com:22124")


class TestFaderClampRpc(unittest.TestCase):
    def test_sends_clamped_value(self):
        from jamulus_state_manager import JamulusParticipant, ParticipantStateManager

        sent = []

        mgr = ParticipantStateManager(
            apply_mixer_setting=lambda *_: None,
            set_cached_participants=lambda *_: None,
            send_rpc_gain=lambda cid, level: sent.append((cid, level)),
            notify_callbacks=lambda: None,
        )
        mgr.participants[0] = JamulusParticipant(channel_id=0, name="Me")
        mgr.set_fader_level(0, 999)
        self.assertEqual(sent, [(0, 127)])


class TestLocalBridgeLoopbackGuard(unittest.TestCase):
    def test_rejects_non_loopback_bind(self):
        from api.local_bridge import LocalApiBridge

        bridge = LocalApiBridge(lambda: [], lambda: {}, host="0.0.0.0", port=8765)
        self.assertFalse(bridge.start())


class TestRepositoryDbPermissions(unittest.TestCase):
    def test_db_file_mode_0600_after_create(self):
        from storage.repository import WebJamRepository

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            WebJamRepository(str(db))
            mode = stat.S_IMODE(db.stat().st_mode)
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
