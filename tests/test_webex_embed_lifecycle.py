"""
WebexEmbed lifecycle edge cases: leaving before any load, and guest-token
exchange failure falling back to the direct-URL path.
"""
from __future__ import annotations

import os
import json
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from webjam_qt.widgets.webex_embed import WebexEmbed, _HTML_TEMPLATE  # noqa: E402


class TestLeaveBeforeLoad(unittest.TestCase):
    def test_untrusted_url_is_refused_before_webengine_init(self):
        embed = WebexEmbed()
        states = []
        embed.meeting_state_changed.connect(states.append)

        embed.load_meeting("https://example.com/meet/not-webex")

        self.assertIsNone(embed._view)
        self.assertIn("error", states)

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

    def test_page_ready_uses_json_encoded_token_and_url(self):
        embed = WebexEmbed()
        embed._page = unittest.mock.MagicMock()
        embed._pending_token = "tok'en\nvalue"
        embed._pending_url = "https://x.webex.com/meet/a'b"

        embed._on_page_ready()

        js = embed._page.runJavaScript.call_args.args[0]
        token_json = json.dumps("tok'en\nvalue")
        url_json = json.dumps("https://x.webex.com/meet/a'b")
        expected = (
            "startWebexMeeting("
            f"{token_json}, "
            f"{url_json});"
        )
        self.assertEqual(js, expected)
        self.assertIsNone(embed._pending_token)

    def test_leave_meeting_does_not_purge_cookies_or_cache(self):
        """Regression: leave_meeting() must preserve the persistent profile.

        The whole point of the named persistent profile (``webjam_webex``)
        is that Webex session state survives across joins/leaves so users
        aren't forced to re-authenticate every time. A prior version wiped
        cookies/cache on every leave, defeating that design.
        """
        embed = WebexEmbed()
        embed.load_meeting("https://x.webex.com/y")
        self.assertIsNotNone(embed._profile)

        embed._profile.cookieStore = unittest.mock.MagicMock()
        embed._profile.clearHttpCache = unittest.mock.MagicMock()

        embed.leave_meeting()

        embed._profile.cookieStore.assert_not_called()
        embed._profile.clearHttpCache.assert_not_called()
        self.assertEqual(embed._stack.currentIndex(), 0)

    def test_shutdown_tears_down_view_and_is_idempotent(self):
        embed = WebexEmbed()
        # Safe no-op before any load.
        try:
            embed.shutdown()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"shutdown raised before any load: {exc!r}")
        self.assertIsNone(embed._view)

        embed.load_meeting("https://x.webex.com/y")
        self.assertIsNotNone(embed._view)

        embed.shutdown()
        self.assertIsNone(embed._view)

        # Calling shutdown again must not raise.
        try:
            embed.shutdown()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"shutdown raised on second call: {exc!r}")

    def test_local_widget_permission_url_allowed_only_for_trusted_meeting(self):
        embed = WebexEmbed()
        widget_url = QUrl.fromLocalFile(str(_HTML_TEMPLATE.resolve()))

        self.assertFalse(embed._is_trusted_widget_permission_url(widget_url))

        embed._pending_token = "token"
        embed._pending_url = "https://x.webex.com/meet/band"
        self.assertTrue(embed._is_trusted_widget_permission_url(widget_url))

        embed._pending_url = "https://example.com/meet/not-webex"
        self.assertFalse(embed._is_trusted_widget_permission_url(widget_url))


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
