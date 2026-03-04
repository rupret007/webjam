"""Tests for ui.services.RetryService."""

import unittest
from unittest.mock import MagicMock

from ui.services import RetryService


class TestRetryServiceImmediateSuccess(unittest.TestCase):
    def test_returns_value_on_first_call(self):
        action = MagicMock(return_value="ok")
        result = RetryService.retry_action(action, attempts=3, base_delay=0.0)
        self.assertEqual(result, "ok")
        action.assert_called_once()


class TestRetryServiceSuccessOnRetry(unittest.TestCase):
    def test_succeeds_on_second_attempt(self):
        action = MagicMock(side_effect=[ValueError("fail"), "recovered"])
        result = RetryService.retry_action(action, attempts=3, base_delay=0.0)
        self.assertEqual(result, "recovered")
        self.assertEqual(action.call_count, 2)

    def test_succeeds_on_last_attempt(self):
        action = MagicMock(side_effect=[ValueError("1"), ValueError("2"), "final"])
        result = RetryService.retry_action(action, attempts=3, base_delay=0.0)
        self.assertEqual(result, "final")
        self.assertEqual(action.call_count, 3)


class TestRetryServicePermanentFailure(unittest.TestCase):
    def test_raises_last_exception(self):
        action = MagicMock(side_effect=RuntimeError("permanent"))
        with self.assertRaises(RuntimeError) as ctx:
            RetryService.retry_action(action, attempts=3, base_delay=0.0)
        self.assertIn("permanent", str(ctx.exception))
        self.assertEqual(action.call_count, 3)

    def test_single_attempt_raises(self):
        action = MagicMock(side_effect=IOError("boom"))
        with self.assertRaises(IOError):
            RetryService.retry_action(action, attempts=1, base_delay=0.0)
        action.assert_called_once()


class TestRetryServiceEdge(unittest.TestCase):
    def test_none_return_value(self):
        action = MagicMock(return_value=None)
        result = RetryService.retry_action(action, attempts=1, base_delay=0.0)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
