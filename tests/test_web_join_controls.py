import os
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow

from webjam_qt.controllers.web_join import WebJoinControls
from webjam_qt.widgets.web_join_panel import WebJoinPanel


@pytest.fixture
def controls():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.web_guest_panel = WebJoinPanel(window)
    window.setCentralWidget(window.web_guest_panel)
    window.flash_message = lambda *args, **kwargs: None
    bridge = SimpleNamespace(
        hosted_server_process=object(), hosted_server_owned=lambda: True
    )
    controller = SimpleNamespace(
        window=window,
        bridge=bridge,
        settings=SimpleNamespace(host_server_enabled=True, jamulus_port=22124),
        audio=SimpleNamespace(stopping=False, cleanup_retry_required=False),
    )
    controls = WebJoinControls(controller)
    yield controls, controller, app
    controls.stop()
    controls._timer.stop()
    window.close()


def test_web_permission_starts_off_and_requires_owned_host(controls):
    controls, controller, _ = controls
    assert not controls.allow_action.isChecked()
    assert controls._host is None
    assert controls.allow_action.isEnabled()
    assert not controls.copy_action.isEnabled()
    assert not controller.window.web_guest_panel.isHidden()
    controller.settings.host_server_enabled = False
    controls._refresh()
    assert not controls.allow_action.isEnabled()
    assert controller.window.web_guest_panel.isHidden()
    controller.settings.host_server_enabled = True
    controller.bridge.hosted_server_owned = lambda: False
    controls._refresh()
    assert not controls.allow_action.isEnabled()


@pytest.mark.parametrize(
    "boundary", ["server_replaced", "stopping", "shutdown", "host_closed"]
)
def test_session_boundaries_revoke_web_guest(controls, boundary):
    controls, controller, _ = controls
    stopped = []
    host = SimpleNamespace(
        state="waiting",
        stop=lambda: stopped.append(True),
        invite="https://invalid/#test",
    )
    controls._host = host
    controls._server = controller.bridge.hosted_server_process
    controls.allow_action.blockSignals(True)
    controls.allow_action.setChecked(True)
    controls.allow_action.blockSignals(False)
    if boundary == "server_replaced":
        controller.bridge.hosted_server_process = object()
    elif boundary == "stopping":
        controller.audio.stopping = True
    elif boundary == "shutdown":
        controller._shutdown = True
    else:
        host.state = "closed"
    controls._refresh()
    assert stopped == [True]
    assert controls._host is None
    assert not controls.allow_action.isChecked()
    assert not controls.copy_action.isEnabled()


def test_late_invite_after_kick_is_retired(controls):
    controls, _, _ = controls
    stopped = []
    stale = SimpleNamespace(
        stop=lambda: stopped.append(True), invite="https://invalid/#secret"
    )
    controls._prepared(stale, True)
    assert stopped == [True]
    assert not controls.copy_action.isEnabled()


def test_failed_stop_keeps_owner_and_disables_allow_until_retry(controls):
    controls, controller, _ = controls
    failures = [True]
    host = SimpleNamespace(state="waiting", invite="https://invalid/#secret")

    def stop():
        host.invite = ""
        if failures:
            host.state = "cleanup_pending"
            raise RuntimeError("private stop details")
        host.state = "closed"

    host.stop = stop
    controls._host = host
    controls._server = controller.bridge.hosted_server_process
    assert controls.stop() is False
    assert controls._host is host
    assert not controls.allow_action.isEnabled() and not controls.copy_action.isEnabled()
    assert controls.kick_action.isEnabled()
    controls._refresh()  # Timer never raises or re-enables an unclean owner.
    controls._prepared(host, True)  # Late successful preparation cannot restore invitation.
    assert controls._host is host and not controls.copy_action.isEnabled()
    failures.clear()
    assert controls.stop() is True
    assert controls._host is None and controls.allow_action.isEnabled()


def test_failed_stale_completion_retains_cleanup_owner(controls):
    controls, _, _ = controls
    failures = [True]
    stale = SimpleNamespace(invite="", state="cleanup_pending")

    def stop():
        if failures:
            raise RuntimeError("stop failed")
        stale.state = "closed"

    stale.stop = stop
    controls._prepared(stale, True)
    assert controls._retired_hosts == [stale]
    assert not controls.allow_action.isEnabled() and controls.kick_action.isEnabled()
    controls._refresh()
    assert not controls.allow_action.isEnabled()
    failures.clear()
    assert controls.stop() is True and controls._retired_hosts == []


def test_remote_session_cleanup_refuses_failed_web_guest_stop():
    from webjam_qt.controllers.application_controller import ApplicationController

    calls = []
    controller = SimpleNamespace(
        _web_join_controls=SimpleNamespace(stop=lambda: calls.append(True) or False)
    )
    assert ApplicationController._stop_remote_transport(controller) is False
    assert calls == [True]


def test_explicit_false_stop_result_keeps_owner_and_timer_latched(controls):
    controls, controller, _ = controls
    host = SimpleNamespace(state="waiting", invite="", stop=lambda: False)
    controls._host = host
    controls._server = controller.bridge.hosted_server_process
    assert controls.stop() is False and controls._host is host
    controls._refresh()
    assert not controls.allow_action.isEnabled() and controls.kick_action.isEnabled()
    host.stop = lambda: True
    assert controls.stop() is True and controls._host is None


def test_visible_browser_button_copy_and_end_use_the_existing_owner(controls):
    controls, controller, app = controls
    panel = controller.window.web_guest_panel
    controller.window.show()
    app.processEvents()
    assert panel.primary.isVisible() and panel.primary.text() == "Copy browser invite"
    host = SimpleNamespace(state="preparing", invite="", stop=lambda: True)
    controls._host, controls._server = host, controller.bridge.hosted_server_process
    controls._refresh()
    assert not panel.primary.isEnabled() and "Preparing" in panel.status.text()
    host.state, host.invite = "waiting", "https://invalid/#synthetic-test-only"
    controls._prepared(host, True)
    assert panel.primary.text() == "Copy browser invite" and panel.primary.isEnabled()
    panel.primary.click()
    assert QApplication.clipboard().text() == host.invite
    host.state = "connected"
    controls._refresh()
    assert not panel.primary.isEnabled() and "connected" in panel.status.text()
    panel.stop_button.click()
    assert controls._host is None and not controls.copy_action.isEnabled()
    QApplication.clipboard().clear()


def test_expired_browser_invite_keeps_music_open_and_offers_a_fresh_copy(controls):
    controls, controller, app = controls
    panel = controller.window.web_guest_panel
    clipboard = app.clipboard()
    clipboard.setText("clipboard already in use")
    host = SimpleNamespace(state="waiting", invite="", stop=lambda: True)
    controls._host, controls._server = host, controller.bridge.hosted_server_process
    controls.copy_action.setEnabled(True)
    try:
        controls._copy()
        assert clipboard.text() == "clipboard already in use"
        assert not panel.primary.isEnabled()
        assert "expired" in panel.status.text()

        host.state = "expired"
        controls._refresh()
        assert controls._host is None
        assert panel.primary.isEnabled()
        assert panel.primary.text() == "Copy fresh browser invite"
        assert "same Music session" in panel.status.text()

        controller.bridge.hosted_server_process = object()
        controls._refresh()
        assert panel.primary.text() == "Copy browser invite"
    finally:
        clipboard.clear()


@pytest.mark.parametrize("cancel", [False, True])
def test_copy_button_prepares_and_copies_only_for_the_current_invitation(controls, monkeypatch, cancel):
    controls, controller, app = controls
    panel = controller.window.web_guest_panel
    finish = threading.Event()
    returned = threading.Event()

    class Host:
        state = "preparing"
        invite = ""

        def allow(self, port, *, generation):
            assert port == 22124 and generation == 1
            assert finish.wait(2)
            self.state, self.invite = "waiting", "https://invalid/#synthetic-test-only"
            returned.set()

        def stop(self):
            self.state, self.invite = "closed", ""
            return True

    host = Host()
    monkeypatch.setattr("webjam_qt.controllers.web_join.WebJoinHost", lambda: host)
    clipboard = QApplication.clipboard()
    clipboard.setText("clipboard already in use")
    try:
        controller.window.show()
        app.processEvents()
        panel.primary.click()
        assert panel.primary.text() == "Preparing link…"
        assert panel.stop_button.text() == "Cancel"
        assert clipboard.text() == "clipboard already in use"
        if cancel:
            panel.stop_button.click()
        finish.set()
        assert returned.wait(1)
        for _ in range(50):
            app.processEvents()
            if (cancel and host.state == "closed") or (not cancel and "Link copied" in panel.status.text()):
                break
            QTest.qWait(10)
        if cancel:
            assert host.state == "closed"
            assert clipboard.text() == "clipboard already in use"
        else:
            assert clipboard.text() == host.invite
            assert "Share it in your Jam room or a private message" in panel.status.text()
    finally:
        finish.set()
        clipboard.clear()


def test_hangout_invite_waits_for_native_confirmation_and_leaves_clipboard_alone(controls, monkeypatch):
    from core import web_join_target
    from webjam_qt.controllers import web_join

    controls, _, app = controls
    private_page = "https://stalemate-hangout-beta.jeffstory007.workers.dev/join/"
    fragment = f"k={'A' * 43}&s={'0' * 64}&g=1&e=1800000000"
    request = "b" * 32
    opened = []

    class Host:
        state = "preparing"
        invite = ""

        def allow(self, port, *, generation):
            self.state = "waiting"
            self.invite = f"{private_page}#{fragment}"

        def stop(self):
            self.state = "closed"
            self.invite = ""
            return True

    class InlineThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(web_join, "threading", SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(web_join, "WebJoinHost", Host)
    monkeypatch.setattr(web_join, "web_join_page", lambda: private_page)
    monkeypatch.setattr(web_join_target, "web_join_page", lambda: private_page)
    monkeypatch.setattr(web_join.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    answers = iter([web_join.QMessageBox.StandardButton.No, web_join.QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(web_join.QMessageBox, "question", lambda *args: next(answers))
    clipboard = app.clipboard()
    clipboard.setText("existing text")
    try:
        controls._prepare_for_hangout(request)
        assert not controls.allow_action.isChecked()
        assert opened == []
        controls._prepare_for_hangout(request)
        assert opened == [f"{private_page}handoff.html#{request}&{fragment}"]
        assert clipboard.text() == "existing text"
    finally:
        clipboard.clear()


def test_pressed_copy_cannot_become_invite_after_server_replacement(controls, monkeypatch):
    controls, controller, _ = controls
    host = SimpleNamespace(state="waiting", invite="https://invalid/#synthetic", stop=lambda: True)
    controls._host, controls._server = host, controller.bridge.hosted_server_process
    controls._prepared(host, True)
    panel = controller.window.web_guest_panel
    panel.primary.pressed.emit()
    controller.bridge.hosted_server_process = object()
    controls._refresh()
    monkeypatch.setattr("webjam_qt.controllers.web_join.WebJoinHost",
                        lambda: pytest.fail("Stale copy allocated a new invitation"))
    panel.primary.clicked.emit()
    assert controls._host is None and not controls.allow_action.isChecked()


def test_preparation_error_is_persistent_and_private_details_are_not_rendered(controls):
    controls, controller, _ = controls
    host = SimpleNamespace(state="closed", invite="", stop=lambda: True,
                           failure_reason="private credential and invitation details")
    controls._host, controls._server = host, controller.bridge.hosted_server_process
    controls._prepared(host, False)
    panel = controller.window.web_guest_panel
    assert panel.primary.text() == "Retry copying invite"
    assert "private credential" not in panel.status.text()
    previous = panel.status.text()
    controls._refresh()
    assert panel.status.text() == previous
    controller.settings.host_server_enabled = False
    controls._refresh()
    assert not panel.primary.isEnabled()
