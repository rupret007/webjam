"""Pinned Jamulus 3.12.2 exposes no live network-send mute."""
from __future__ import annotations

import unittest
from unittest import mock

from jamulus_controller import JamulusController


class TestControllerSelfMute(unittest.TestCase):
    def _controller(self, available: bool):
        c = JamulusController.__new__(JamulusController)  # bypass full init
        c.rpc_client = mock.MagicMock()
        c.rpc_client.available = available
        c.rpc_client.set_self_muted.return_value = True
        return c

    def test_fails_closed_without_delegating_when_rpc_available(self):
        c = self._controller(available=True)
        self.assertFalse(c.set_self_muted(True))
        c.rpc_client.set_self_muted.assert_not_called()
        self.assertFalse(c.live_send_mute)

    def test_noop_when_rpc_unavailable(self):
        c = self._controller(available=False)
        self.assertFalse(c.set_self_muted(True))
        c.rpc_client.set_self_muted.assert_not_called()

class TestControllerSetName(unittest.TestCase):
    def _controller(self, available: bool):
        c = JamulusController.__new__(JamulusController)
        c.rpc_client = mock.MagicMock()
        c.rpc_client.available = available
        c.rpc_client.set_name.return_value = True
        return c

    def test_delegates_when_available(self):
        c = self._controller(available=True)
        self.assertTrue(c.set_name("Drummer Dan"))
        c.rpc_client.set_name.assert_called_once_with("Drummer Dan")

    def test_noop_when_unavailable_or_empty(self):
        c = self._controller(available=False)
        self.assertFalse(c.set_name("X"))
        c.rpc_client.set_name.assert_not_called()
        c2 = self._controller(available=True)
        self.assertFalse(c2.set_name(""))
        c2.rpc_client.set_name.assert_not_called()


if __name__ == "__main__":
    unittest.main()
