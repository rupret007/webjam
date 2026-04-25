"""
WebexEmbed lifecycle edge cases: leaving before any load, and guest-token
exchange failure falling back to the direct-URL path.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from webjam_qt.widgets.webex_embed import WebexEmbed  # noqa: E402


class TestLeaveBeforeLoad(unittest.TestCase):
    def test_leave_meeting_before_load_completes_no_crash(self):
        embed = WebexEmbed()
        # Sanity: no WebEngine objects yet, view is on placeholder (idx 0).
        self.assertIsNone(embed._view)
        self.assertEqual(embed._stack.currentIndex(), 0)

        # leave_meeting() must be a safe no-op even before load_meeting().
        try:
            embed.leave_meeting()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"leave_meeting raised before any load: {exc!r}")

        # Still on placeholder, no view yet.
        self.assertEqual(embed._stack.currentIndex(), 0)
        self.assertIsNone(embed._view)

        # Now a real load_meeting should bring the WebEngine up and switch
        # the stack to the webview slot.
        embed.load_meeting("https://x.webex.com/y")
        self.assertIsNotNone(embed._view)
        self.assertEqual(embed._stack.currentIndex(), 1)


class TestGuestTokenFallback(unittest.TestCase):
    def test_guest_token_failure_falls_back_to_direct_url(self):
        embed = WebexEmbed()

        # Capture the _load_ready signal so we can assert it arrived with an
        # empty token and the original meeting URL.
        received: list[tuple[str, str]] = []
        embed._load_ready.connect(lambda tok, url: received.append((tok, url)))

        # We don't want load_meeting to actually spin up WebEngine here —
        # patching the slot lets us assert the direct-URL fallback decision.
        load_calls: list[dict] = []

        def _fake_load(url, *, access_token=None):
            load_calls.append({"url": url, "access_token": access_token})

        with patch.object(embed, "load_meeting", side_effect=_fake_load):
            with patch(
                "core.webex_guest_token.exchange_guest_jwt",
                side_effect=RuntimeError("boom"),
            ):
                embed.load_meeting_with_guest_token(
                    "https://x.webex.com/m",
                    issuer_id="iss",
                    secret_b64="aGVsbG8=",
                    display_name="Tester",
                )

                # Run the Qt event loop briefly so the queued
                # _load_ready -> _on_load_ready connection fires on the main
                # thread (the worker thread emits, the main thread dispatches).
                deadline = 50  # ~50 * 50 ms = 2.5 s budget
                while deadline > 0 and not load_calls:
                    _app.processEvents()
                    deadline -= 1
                    if not load_calls:
                        import time
                        time.sleep(0.05)
                        _app.processEvents()

        self.assertTrue(received, "_load_ready should have been emitted")
        token, url = received[-1]
        self.assertEqual(token, "")
        self.assertEqual(url, "https://x.webex.com/m")

        self.assertTrue(load_calls, "load_meeting should be called via _on_load_ready")
        # Empty token => direct-URL mode (access_token=None).
        self.assertIsNone(load_calls[-1]["access_token"])
        self.assertEqual(load_calls[-1]["url"], "https://x.webex.com/m")


if __name__ == "__main__":
    unittest.main()
