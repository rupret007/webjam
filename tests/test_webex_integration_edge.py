from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from webex_integration import (
    WebexController,
    WebexLaunchState,
    create_webex_controller,
    open_webex_meeting,
)


class TestExternalWebexLauncher(unittest.TestCase):
    def test_open_webex_meeting_returns_false_when_browser_refuses(self):
        with patch("webex_integration.webbrowser.open", return_value=False):
            self.assertFalse(open_webex_meeting("https://meet.jit.si/WebJamBand"))

    def test_open_webex_meeting_returns_false_on_exception(self):
        with patch(
            "webex_integration.webbrowser.open", side_effect=RuntimeError("boom")
        ):
            self.assertFalse(open_webex_meeting("https://meet.jit.si/WebJamBand"))

    def test_join_opens_browser_without_claiming_connected(self):
        controller = WebexController("https://example.webex.com/meet/test")
        with patch("webex_integration.webbrowser.open", return_value=True):
            self.assertTrue(controller.join_meeting())
        self.assertTrue(controller.browser_opened)
        self.assertFalse(controller.is_connected)
        self.assertEqual(
            controller.launch_state, WebexLaunchState.OPENED_EXTERNALLY
        )

    def test_explicit_launch_uses_immutable_argument_not_mutable_setting(self):
        controller = WebexController(
            "https://old.webex.com/meet/original"
        )
        requested = "https://new.webex.com/meet/authorized"
        with patch(
            "webex_integration.webbrowser.open",
            return_value=True,
        ) as opener:
            self.assertTrue(controller.join_meeting_url(requested))

        opener.assert_called_once_with(requested)
        self.assertEqual(
            controller.meeting_url,
            "https://old.webex.com/meet/original",
        )

    def test_generic_provider_hands_the_validated_url_to_the_os_exactly_once(self):
        requested = "https://meet.jit.si/WebJamBand?private=1#join"
        controller = WebexController(requested)
        controller.logger = MagicMock()
        with patch(
            "webex_integration.webbrowser.open",
            return_value=True,
        ) as opener:
            self.assertTrue(controller.join_meeting_url(requested))

        opener.assert_called_once_with(requested)
        rendered = " ".join(
            str(value) for value in controller.logger.info.call_args.args
        )
        self.assertIn("generic", rendered)
        self.assertNotIn("meet.jit.si", rendered)
        self.assertNotIn("WebJamBand", rendered)

    def test_factory_preserves_external_only_contract(self):
        controller = create_webex_controller("https://example.webex.com/meet/test")
        self.assertEqual(controller.launch_state, WebexLaunchState.NOT_OPENED)
        self.assertFalse(hasattr(controller, "get_participants"))
        self.assertFalse(hasattr(controller, "mute_audio"))
        self.assertFalse(hasattr(controller, "enable_video"))
        self.assertFalse(hasattr(controller, "start_screen_share"))
        self.assertFalse(hasattr(controller, "leave_meeting"))

    def test_stop_never_claims_to_close_an_external_meeting(self):
        controller = WebexController("https://example.webex.com/meet/test")
        with patch("webex_integration.webbrowser.open", return_value=True):
            self.assertTrue(controller.join_meeting())

        controller.stop()

        self.assertEqual(
            controller.launch_state, WebexLaunchState.OPENED_EXTERNALLY
        )
        self.assertTrue(controller.browser_opened)
        self.assertFalse(controller.is_connected)

    def test_launcher_logs_hostname_without_meeting_secret(self):
        controller = WebexController(
            "https://team.webex.com/meet/private-room?token=super-secret#frag"
        )
        controller.logger = MagicMock()
        with patch("webex_integration.webbrowser.open", return_value=True):
            self.assertTrue(controller.join_meeting())
        rendered = " ".join(
            str(value) for value in controller.logger.info.call_args.args
        )
        self.assertIn("team.webex.com", rendered)
        self.assertNotIn("private-room", rendered)
        self.assertNotIn("super-secret", rendered)

    def test_failure_log_does_not_include_browser_exception_text(self):
        controller = WebexController(
            "https://team.webex.com/meet/private-room?token=super-secret"
        )
        controller.logger = MagicMock()
        with patch(
            "webex_integration.webbrowser.open",
            side_effect=RuntimeError(
                "leaked https://team.webex.com/meet/private-room"
            ),
        ):
            self.assertFalse(controller.join_meeting())
        rendered = " ".join(
            str(value) for value in controller.logger.warning.call_args.args
        )
        self.assertIn("team.webex.com", rendered)
        self.assertNotIn("private-room", rendered)


if __name__ == "__main__":
    unittest.main()
