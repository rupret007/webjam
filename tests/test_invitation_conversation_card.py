"""Conversation editor guidance survives provider/configuration rerendering."""

import pytest
from PySide6.QtWidgets import QApplication

from core.creative_modes import get_creator_profile_by_key
from webjam_qt.widgets.webex_embed import WebexEmbed


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _assert_editor_guidance(panel):
    button = panel.change_link_button()
    for text in (button.accessibleDescription(), button.toolTip()):
        assert "Conversation" in text
        assert "meeting link" in text
        # Invited rooms edit a temporary room link; a personal setup may open
        # Settings. The shared card must not promise a particular editor.
        assert "settings" not in text.casefold()
        assert "saved" not in text.casefold()
    assert button.text() == ("Change Link" if panel._meeting_configured else "Add Link")


@pytest.mark.parametrize("profile", ("art", "music"))
def test_card_editor_guidance_survives_constructor_and_provider_changes(qapp, profile):
    panel = WebexEmbed()
    events = []
    panel.change_link_requested.connect(lambda: events.append("edit"))
    panel.open_meeting_requested.connect(lambda: events.append("open"))
    panel.bring_forward_requested.connect(lambda: events.append("native"))
    try:
        # The constructor itself renders accessibility after initial labels.
        _assert_editor_guidance(panel)
        panel.set_creator_profile(get_creator_profile_by_key(profile))
        for service, configured in (
            ("Webex", True), ("Google Meet", True), ("", True),
            ("Zoom", False), ("Zoom", True), ("", False),
        ):
            panel.set_service_label(service)
            panel.set_meeting_configured(configured)
            _assert_editor_guidance(panel)
            if service:
                assert service in panel.change_link_button().accessibleName()
            assert events == []
        panel.change_link_button().click()
        assert events == ["edit"]
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()
