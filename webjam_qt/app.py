"""QApplication bootstrap for the Conductor UI."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from core.logging_config import configure_logging
from core.settings import load_settings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.first_run_setup import FirstRunSetupDialog


def _configure_qt_attributes() -> None:
    # Qt 6 enables high-DPI scaling by default; the old AA_EnableHighDpiScaling /
    # AA_UseHighDpiPixmaps attributes are deprecated no-ops since Qt 6.0 and
    # generate DeprecationWarnings in PySide6 6.x — nothing to set here.
    pass


def _configure_default_font(app: QApplication) -> None:
    font = QFont("Inter")
    font.setPixelSize(13)
    app.setFont(font)


def run() -> int:
    """Entry point — called by webjam_qt_main.py."""
    settings = load_settings()
    configure_logging(settings)
    logging.getLogger("webjam.qt").info("Starting Conductor UI")

    _configure_qt_attributes()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("WebJam")
    app.setApplicationDisplayName("WebJam")
    app.setOrganizationName("WebJam")

    _configure_default_font(app)
    app.setStyleSheet(load_stylesheet())

    # Show setup wizard on first run (no config file yet)
    run_ready_check_after_startup = False
    if FirstRunSetupDialog.should_show_on_startup(settings):
        wizard = FirstRunSetupDialog(settings)
        if wizard.exec() == FirstRunSetupDialog.DialogCode.Rejected:
            return 0  # User cancelled — exit cleanly
        # Reload settings that the wizard just saved
        settings = load_settings()
        run_ready_check_after_startup = True

    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Band Rehearsal",
    )
    controller = ApplicationController(window, settings=settings)
    controller.start_companion_api()  # optional localhost bridge for DAWs/editors
    window.show()
    if run_ready_check_after_startup:
        QTimer.singleShot(0, controller._on_ready_check)

    exit_code = app.exec()
    controller.shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
