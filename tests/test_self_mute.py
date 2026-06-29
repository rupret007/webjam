"""Self-mute uses Jamulus's real global mute (jamulusclient/setMuted), not a
local fader-to-zero (which would only mute you in your own monitor)."""
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

    def test_delegates_to_rpc_when_available(self):
        c = self._controller(available=True)
        self.assertTrue(c.set_self_muted(True))
        c.rpc_client.set_self_muted.assert_called_once_with(True)

    def test_noop_when_rpc_unavailable(self):
        c = self._controller(available=False)
        self.assertFalse(c.set_self_muted(True))
        c.rpc_client.set_self_muted.assert_not_called()

    def test_swallows_rpc_errors(self):
        c = self._controller(available=True)
        c.rpc_client.set_self_muted.side_effect = RuntimeError("boom")
        self.assertFalse(c.set_self_muted(True))  # must not raise


if __name__ == "__main__":
    unittest.main()
