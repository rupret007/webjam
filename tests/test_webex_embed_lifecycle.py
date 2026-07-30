"""External-only Webex launch-card contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from services.webex_app import WebexAppState  # noqa: E402
from webjam_qt.widgets.webex_embed import WebexEmbed  # noqa: E402


def test_card_constructs_without_webengine_or_token_state():
    embed = WebexEmbed()

    assert not hasattr(embed, "_view")
    assert not hasattr(embed, "_profile")
    assert not hasattr(embed, "_pending_token")
    assert not hasattr(embed, "load_meeting_with_guest_token")


def test_compatibility_load_refuses_to_embed_even_a_valid_webex_link():
    embed = WebexEmbed()
    states: list[str] = []
    embed.meeting_state_changed.connect(states.append)

    assert embed.load_meeting("https://team.webex.com/meet/example") is False
    assert states == ["error"]
    assert not hasattr(embed, "_view")


def test_leave_and_shutdown_are_safe_idempotent_no_ops():
    embed = WebexEmbed()

    embed.leave_meeting()
    embed.shutdown()
    embed.shutdown()

    assert embed.fallback_button().text() == "Join / Open Meeting"


def test_external_success_copy_never_claims_join_or_connection():
    embed = WebexEmbed()

    embed.set_launch_status("Opened externally")

    status = embed._status_label.text()
    assert status == "Opened externally—finish joining in Webex."
    assert "joined" not in status.lower()
    assert embed.fallback_button().text() == "Open Again"


def test_install_action_stays_hidden_until_native_app_needs_attention():
    embed = WebexEmbed()
    requested = []
    rechecks = []
    embed.install_webex_requested.connect(lambda: requested.append(True))
    embed.recheck_webex_requested.connect(lambda: rechecks.append(True))

    assert embed.install_button().isHidden()
    assert embed.recheck_button().isHidden()
    assert embed._app_status_label.isHidden()

    embed.set_app_status(WebexAppState.NOT_INSTALLED)
    assert not embed.install_button().isHidden()
    assert embed.install_button().text() == "Get Webex"
    assert not embed.recheck_button().isHidden()
    assert embed._app_status_label.text() == "Webex app not installed"
    assert embed._app_status_label.accessibleName() == "Webex app status"
    assert "official Webex download" in (
        embed.install_button().accessibleDescription()
    )

    embed.install_button().click()
    embed.recheck_button().click()
    assert requested == [True]
    assert rechecks == [True]

    embed.set_app_status(WebexAppState.INVALID)
    assert not embed.install_button().isHidden()
    assert embed.install_button().text() == "Get Webex"
    assert "unverified installation" in (
        embed.install_button().accessibleDescription()
    )


def test_native_app_rescan_and_activation_busy_states_are_truthful():
    embed = WebexEmbed()

    embed.set_app_checking()
    assert embed._app_status_label.text() == "Checking for the Webex app…"
    assert not embed.recheck_button().isHidden()
    assert not embed.recheck_button().isEnabled()
    assert not embed.bring_forward_button().isEnabled()
    assert embed.bring_forward_button().text() == "Verifying…"

    embed.set_app_status(
        WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
    )
    assert embed.recheck_button().isHidden()
    assert embed.bring_forward_button().isEnabled()
    assert embed.bring_forward_button().text() == "Show Webex App"

    embed.set_native_action_busy(True)
    assert not embed.bring_forward_button().isEnabled()
    assert not embed.mute_button().isEnabled()
    assert embed.bring_forward_button().text() == "Verifying…"

    embed.set_native_action_busy(False)
    assert embed.bring_forward_button().isEnabled()
    assert embed.mute_button().isEnabled()


def test_app_status_never_overwrites_external_meeting_launch_truth():
    embed = WebexEmbed()
    embed.set_launch_status("Opened externally")
    launch_text = embed._status_label.text()
    launch_description = embed.fallback_button().accessibleDescription()

    embed.set_app_status(
        WebexAppState.INSTALLED,
        version="45.7.0",
        publisher_verified=True,
    )

    assert embed._app_status_label.text() == "Webex app verified • 45.7.0"
    assert embed.install_button().isHidden()
    assert embed._status_label.text() == launch_text
    assert embed.fallback_button().text() == "Open Again"
    assert (
        embed.fallback_button().accessibleDescription()
        == launch_description
    )
    assert "joining" in launch_text
    assert "installed" not in launch_text.lower()


def test_unverified_platform_reports_app_found_without_publisher_claim():
    embed = WebexEmbed()

    embed.set_app_status(
        WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=False,
    )

    assert embed._app_status_label.text() == "Webex app found • 46.7.0"
    description = embed._app_status_label.accessibleDescription()
    assert "publisher verification is not available" in description
    assert "publisher is verified" not in description
    assert embed.install_button().isHidden()
    assert not embed.bring_forward_button().isEnabled()
    assert not embed.mute_button().isEnabled()


def test_unsupported_app_check_keeps_browser_meeting_action_available():
    embed = WebexEmbed()
    embed.set_meeting_configured(True)
    embed.set_app_status("unsupported")

    assert embed.install_button().isHidden()
    assert embed.fallback_button().isEnabled()
    assert embed.fallback_button().text() == "Join / Open Meeting"
    assert embed._app_status_label.text() == "Webex app check unavailable"
    assert "supported browser" in (
        embed._app_status_label.accessibleDescription()
    )
    assert embed.maximumHeight() <= 152


def test_transient_app_check_failure_keeps_explicit_retry_available():
    embed = WebexEmbed()
    rechecks: list[bool] = []
    embed.recheck_webex_requested.connect(lambda: rechecks.append(True))
    embed.set_meeting_configured(True)

    embed.set_app_status(
        WebexAppState.UNSUPPORTED,
        reason_code="detection-failed",
    )

    assert embed._app_status_label.text() == "Webex app check failed"
    assert not embed.recheck_button().isHidden()
    assert embed.recheck_button().isEnabled()
    assert embed.fallback_button().isEnabled()
    assert "Check Again" in embed._app_status_label.accessibleDescription()
    embed.recheck_button().click()
    assert rechecks == [True]


def test_conversation_actions_are_distinct_and_truthful():
    embed = WebexEmbed()
    events: list[str] = []
    embed.bring_forward_requested.connect(lambda: events.append("bring"))
    embed.mute_in_webex_requested.connect(lambda: events.append("mute"))
    embed.open_meeting_requested.connect(lambda: events.append("open"))
    embed.change_link_requested.connect(lambda: events.append("settings"))
    embed.set_meeting_configured(True)
    embed.set_app_status(
        WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
    )

    embed.bring_forward_button().click()
    embed.mute_button().click()
    embed.fallback_button().click()
    embed.change_link_button().click()

    assert events == ["bring", "mute", "open", "settings"]
    assert "without opening the meeting link or a browser" in (
        embed.bring_forward_button().accessibleDescription()
    )
    assert "never starts Webex" in (
        embed.bring_forward_button().accessibleDescription()
    )
    assert "already running" in embed.bring_forward_button().toolTip()
    mute_description = embed.mute_button().accessibleDescription()
    assert "cannot verify or change mute" in mute_description
    assert "Jamulus" not in embed.mute_button().text()


def test_join_open_requires_a_configured_link_and_is_single_flight():
    embed = WebexEmbed()
    assert not embed.fallback_button().isEnabled()
    assert embed.change_link_button().text() == "Add Link"

    embed.set_meeting_configured(True)
    assert embed.fallback_button().isEnabled()
    assert embed.change_link_button().text() == "Change Link"

    embed.set_launch_status("Opening…")
    assert not embed.fallback_button().isEnabled()
    assert embed.bring_forward_button().text() == "Show Webex App"

    embed.set_launch_status("Opened externally")
    assert embed.fallback_button().isEnabled()
    assert embed.fallback_button().text() == "Open Again"


def test_keyboard_show_app_and_join_meeting_remain_distinct():
    embed = WebexEmbed()
    events: list[str] = []
    embed.bring_forward_requested.connect(lambda: events.append("show-app"))
    embed.open_meeting_requested.connect(lambda: events.append("join-meeting"))
    embed.set_meeting_configured(True)
    embed.set_app_status(
        WebexAppState.INSTALLED,
        publisher_verified=True,
    )
    embed.show()
    _app.processEvents()

    embed.show_app_button().setFocus()
    QTest.keyClick(embed.show_app_button(), Qt.Key.Key_Space)
    _app.processEvents()
    assert events == ["show-app"]

    embed.fallback_button().setFocus()
    QTest.keyClick(embed.fallback_button(), Qt.Key.Key_Space)
    _app.processEvents()
    assert events == ["show-app", "join-meeting"]
    embed.close()


def test_explicit_webex_actions_remain_legible_at_760_pixels():
    embed = WebexEmbed()
    embed.resize(760, embed.maximumHeight())
    embed.set_meeting_configured(True)
    embed.set_app_status(
        WebexAppState.INSTALLED,
        version="46.7.0.35472",
        publisher_verified=True,
    )
    embed.show()
    _app.processEvents()

    assert embed.minimumSizeHint().width() <= 760
    for button in (
        embed.show_app_button(),
        embed.mute_button(),
        embed.fallback_button(),
        embed.change_link_button(),
    ):
        assert button.width() >= button.sizeHint().width()
    embed.close()


def test_native_app_controls_fail_closed_until_detection_is_installed():
    embed = WebexEmbed()
    assert not embed.bring_forward_button().isEnabled()
    assert not embed.mute_button().isEnabled()

    embed.set_app_status(WebexAppState.NOT_INSTALLED)
    assert not embed.bring_forward_button().isEnabled()
    assert not embed.mute_button().isEnabled()

    embed.set_app_status(
        WebexAppState.INSTALLED,
        publisher_verified=True,
    )
    assert embed.bring_forward_button().isEnabled()
    assert embed.mute_button().isEnabled()


@pytest.mark.parametrize("action_name", ("bring", "mute", "recheck"))
def test_native_busy_state_never_moves_focus_to_join_open(action_name):
    embed = WebexEmbed()
    embed.set_meeting_configured(True)
    if action_name == "recheck":
        embed.set_app_status(WebexAppState.NOT_INSTALLED)
        action = embed.recheck_button()
    else:
        embed.set_app_status(
            WebexAppState.INSTALLED,
            publisher_verified=True,
        )
        action = (
            embed.bring_forward_button()
            if action_name == "bring"
            else embed.mute_button()
        )
    embed.show()
    _app.processEvents()
    action.setFocus()
    _app.processEvents()

    if action_name == "recheck":
        embed.set_app_checking()
    else:
        embed.set_native_action_busy(True)
    _app.processEvents()

    assert QApplication.focusWidget() is embed._app_status_label
    assert QApplication.focusWidget() is not embed.fallback_button()

    if action_name == "recheck":
        embed.set_app_status(
            WebexAppState.NOT_INSTALLED,
            reason_code="detection-failed",
        )
    else:
        embed.set_native_action_busy(False)
    _app.processEvents()
    assert QApplication.focusWidget() is action
    embed.close()


def test_app_status_rejects_unknown_state_and_drops_unsafe_version_copy():
    embed = WebexEmbed()

    with pytest.raises(ValueError, match="unsupported Webex app status"):
        embed.set_app_status("connected")

    embed.set_app_status(
        "installed",
        version="/Users/private\nsecret",
        publisher_verified=True,
    )
    assert embed._app_status_label.text() == "Webex app verified"
