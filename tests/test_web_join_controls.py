import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow

from webjam_qt.controllers.web_join import WebJoinControls


@pytest.fixture
def controls():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
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
    controller.settings.host_server_enabled = False
    controls._refresh()
    assert not controls.allow_action.isEnabled()
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
