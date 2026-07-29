"""External-only Webex launch-card contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
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

    assert embed.fallback_button().text() == "Open Webex"


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
    embed.install_webex_requested.connect(lambda: requested.append(True))

    assert embed.install_button().isHidden()
    assert embed._app_status_label.isHidden()

    embed.set_app_status(WebexAppState.NOT_INSTALLED)
    assert not embed.install_button().isHidden()
    assert embed.install_button().text() == "Get Webex"
    assert embed._app_status_label.text() == "Webex app not installed"
    assert embed._app_status_label.accessibleName() == "Webex app status"
    assert "official Webex download" in (
        embed.install_button().accessibleDescription()
    )

    embed.install_button().click()
    assert requested == [True]

    embed.set_app_status(WebexAppState.INVALID)
    assert not embed.install_button().isHidden()
    assert embed.install_button().text() == "Get Webex"
    assert "unverified installation" in (
        embed.install_button().accessibleDescription()
    )


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


def test_unsupported_app_check_keeps_browser_meeting_action_available():
    embed = WebexEmbed()
    embed.set_app_status("unsupported")

    assert embed.install_button().isHidden()
    assert embed.fallback_button().isEnabled()
    assert embed.fallback_button().text() == "Open Webex"
    assert embed._app_status_label.text() == "Webex app check unavailable"
    assert "supported browser" in (
        embed._app_status_label.accessibleDescription()
    )
    assert embed.maximumHeight() <= 96


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
