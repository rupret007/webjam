from __future__ import annotations

from unittest.mock import MagicMock, patch

from webjam_qt import app as app_module


def test_run_turns_startup_failure_into_plain_restart_message() -> None:
    qt_app = MagicMock()
    with (
        patch.object(app_module, "_run_app", side_effect=RuntimeError("secret detail")),
        patch.object(app_module.QApplication, "instance", return_value=qt_app),
        patch.object(app_module.QMessageBox, "critical") as critical,
    ):
        assert app_module.run() == 1

    title, message = critical.call_args.args[1:3]
    assert title == "WebJam couldn’t open"
    assert "secret detail" not in message
    assert "open it again" in message


def test_unhandled_ui_error_is_logged_hidden_and_quits() -> None:
    qt_app = MagicMock()
    error = RuntimeError("private traceback detail")
    with (
        patch.object(app_module.QApplication, "instance", return_value=qt_app),
        patch.object(app_module.QApplication, "activeWindow", return_value=None),
        patch.object(app_module.QMessageBox, "critical") as critical,
    ):
        app_module._report_unhandled_exception(type(error), error, error.__traceback__)

    title, message = critical.call_args.args[1:3]
    assert title == "WebJam needs to restart"
    assert "private traceback detail" not in message
    qt_app.quit.assert_called_once_with()
