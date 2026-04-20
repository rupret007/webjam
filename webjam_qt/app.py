"""QApplication bootstrap for the Conductor UI."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from core.logging_config import configure_logging
from core.settings import load_settings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow


def _configure_qt_attributes() -> None:
    # Qt attributes must be set before QApplication is constructed.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)


def _configure_default_font(app: QApplication) -> None:
    font = QFont("Inter")
    # Fall back to the platform default font family if Inter isn't installed;
    # Qt will quietly substitute, so we set a reasonable pixel size instead
    # of relying on families that may not be present.
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

    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Band Rehearsal",
    )
    controller = ApplicationController(window, settings=settings)
    window.show()

    exit_code = app.exec()
    controller.shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
