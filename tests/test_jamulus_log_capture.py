"""Regression tests for BridgeService._close_jamulus_log_file (v0.4.5).

The helper must be safe to call:
    - With no log file ever opened (handle is None).
    - Multiple times in a row (idempotent).
    - On a handle that's already been closed externally.
"""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock

from tests.support.component_store import isolated_component_store_root


def _make_bridge():
    from services.bridge_service import BridgeService

    settings = MagicMock()
    settings.jamulus_server = "jam.example.com"
    settings.jamulus_port = 22124
    settings.jamulus_rpc_port = 22222
    settings.jamulus_candidates = ["C:/Jamulus.exe"]
    settings.webex_url = "https://example.webex.com/meet/test"

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


class TestCloseJamulusLogFileIdempotent(unittest.TestCase):
    def test_close_with_none_handle_is_noop(self):
        bridge = _make_bridge()
        self.assertIsNone(bridge._jamulus_log_file)
        # Should not raise.
        bridge._close_jamulus_log_file()
        self.assertIsNone(bridge._jamulus_log_file)

    def test_close_called_twice_in_a_row(self):
        bridge = _make_bridge()
        # Two calls back-to-back: the second must see None and no-op.
        bridge._close_jamulus_log_file()
        bridge._close_jamulus_log_file()
        self.assertIsNone(bridge._jamulus_log_file)

    def test_close_handles_already_closed_file_handle(self):
        bridge = _make_bridge()
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        )
        # Close the underlying file ourselves so the bridge's close()
        # call would normally raise ValueError ("I/O on closed file").
        tmp.close()
        bridge._jamulus_log_file = tmp

        # Must swallow the exception silently and clear the attribute.
        bridge._close_jamulus_log_file()
        self.assertIsNone(bridge._jamulus_log_file)

        # And calling again must remain a no-op.
        bridge._close_jamulus_log_file()
        self.assertIsNone(bridge._jamulus_log_file)


if __name__ == "__main__":
    unittest.main()
