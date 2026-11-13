"""The host invite is offered for sharing at the moment copy becomes available."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow

from core.network_invite import invite_message_title
from core.settings import AppSettings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.web_join import WebJoinControls
from webjam_qt.widgets.web_join_panel import WebJoinPanel
from webjam_qt.widgets.webex_embed import WebexEmbed
from webjam_qt.windows.conductor_window import ConductorWindow

LINK = "https://example.com/join/#invite"


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _authorize_microphone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "webjam_qt.platform_permissions.microphone_permission_status",
        lambda: "authorized",
    )


def test_controller_offers_person_invite_when_copy_becomes_available(qapp, tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
        takes_directory=str(tmp_path / "takes"),
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Practice",
    )
    controller = ApplicationController(window, settings=settings)
    controller.bridge.hosted_server_alive = MagicMock(return_value=False)
    calls: list[tuple[str, str]] = []
    embed = window.webex_embed
    real_offer = embed.offer_person_invite

    def spy(link: str, title: str = "") -> bool:
        calls.append((str(link), str(title)))
        return real_offer(link, title)

    embed.offer_person_invite = spy
    clipboard = QApplication.clipboard()
    clipboard.setText("untouched")
    try:
        window.show()
        qapp.processEvents()
        controller.bridge.jamulus_launch_intended = True
        controller.bridge.jamulus_state = "Running"
        controller._update_session_hud()
        assert calls == []
        controller.bridge.hosted_server_alive.return_value = True
        controller._current_invite_url = MagicMock(return_value=LINK)
        controller._update_session_hud()
        qapp.processEvents()

        assert calls == [(LINK, invite_message_title("Practice"))]
        assert invite_message_title("Practice") == "Join me for Practice"
        assert not embed.isHidden()
        assert embed._share_invite_btn.isHidden()
        assert not embed._email_invite_btn.isHidden()
        assert embed._email_invite_btn.objectName() == "PrimaryButton"
        assert embed._copy_invite_btn.objectName() == "GhostButton"
        assert embed._copy_invite_btn.isEnabled()
        assert window.session_hud._action_kind == "copy_invite"
        assert not window.session_hud._action.isHidden()

        window.session_hud._action.click()
        qapp.processEvents()
        assert clipboard.text() == "untouched"
        assert qapp.focusWidget() is embed._email_invite_btn

        embed._copy_invite_btn.click()
        assert clipboard.text() == LINK
        assert "deliver" not in clipboard.text()
    finally:
        controller.bridge.hosted_server_alive.return_value = False
        controller.shutdown()
        clipboard.clear()
        window.close()


def test_web_join_prepare_offers_person_invite(qapp):
    window = QMainWindow()
    window.web_guest_panel = WebJoinPanel(window)
    embed = WebexEmbed(window)
    window.webex_embed = embed
    window.session_strip = SimpleNamespace(current_title=lambda: "Practice")
    window.flash_message = lambda *args, **kwargs: None
    bridge = SimpleNamespace(
        hosted_server_process=object(),
        hosted_server_owned=lambda: True,
    )
    controller = SimpleNamespace(
        window=window,
        bridge=bridge,
        settings=SimpleNamespace(host_server_enabled=True, jamulus_port=22124),
        audio=SimpleNamespace(stopping=False, cleanup_retry_required=False),
    )
    controls = WebJoinControls(controller)
    controller._web_join_controls = controls
    calls: list[tuple[str, str]] = []
    real_offer = embed.offer_person_invite

    def spy(link: str, title: str = "") -> bool:
        calls.append((str(link), str(title)))
        return real_offer(link, title)

    embed.offer_person_invite = spy
    host = SimpleNamespace(state="waiting", invite=LINK, stop=lambda: True)
    controls._host = host
    controls._server = bridge.hosted_server_process
    try:
        controls._prepared(host, True)
        assert calls == [(LINK, invite_message_title("Practice"))]
        assert embed._email_invite_btn.objectName() == "PrimaryButton"
        assert embed._copy_invite_btn.objectName() == "GhostButton"
        assert controls.copy_action.isEnabled()
        assert not embed._invite_delivery.isHidden()
        # The desktop HUD and browser controls share one card. A HUD tick
        # during a pending browser invite must not replace or hide its link.
        controller._hud_copy_invite_enabled = MagicMock(return_value=False)
        controller._retire_prepared_person_invite = MagicMock()
        ApplicationController._sync_prepared_person_invite(controller)
        controller._retire_prepared_person_invite.assert_not_called()
        assert embed._invite_link == LINK
        controls.stop()
        assert embed._invite_delivery.isHidden()
        assert embed._invite_link == ""
    finally:
        controls.stop()
        controls._timer.stop()
        window.close()
