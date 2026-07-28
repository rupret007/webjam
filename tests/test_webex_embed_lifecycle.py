"""External-only Webex launch-card contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

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
