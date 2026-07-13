"""QApplication bootstrap for the Conductor UI."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox

from core.logging_config import configure_logging
from core.settings import load_settings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet, make_brand_icon
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog, apply_host_defaults


class WebJamApplication(QApplication):
    """QApplication that receives macOS ``webjam://`` open events."""

    invite_url_received = Signal(str)

    def __init__(self, arguments: list[str]) -> None:
        # A QFileOpen event may arrive during QApplication construction,
        # before the launch dialog or live controller has connected a slot.
        # Retain one pending invite so a cold deep link can never disappear.
        self._pending_invite_url = ""
        super().__init__(arguments)

    def event(self, event) -> bool:  # noqa: A003
        if event.type() == QEvent.Type.FileOpen:
            try:
                url = event.url().toString()
            except AttributeError:
                url = ""
            if str(url).lower().startswith("webjam://"):
                self._pending_invite_url = str(url)
                self.invite_url_received.emit(str(url))
                return True
        return super().event(event)

    def take_pending_invite(self) -> str:
        """Return and clear an invite received before a consumer was ready."""
        value = str(getattr(self, "_pending_invite_url", "") or "")
        self._pending_invite_url = ""
        return value

    def acknowledge_invite(self, value: str) -> None:
        """Clear a just-delivered signal without dropping a newer invite."""
        if self._pending_invite_url == str(value or ""):
            self._pending_invite_url = ""


def _invite_from_arguments(arguments: list[str]) -> str:
    return next(
        (
            str(item)
            for item in arguments[1:]
            if str(item).lower().startswith("webjam://")
        ),
        "",
    )


def _configure_qt_attributes() -> None:
    # Qt 6 enables high-DPI scaling by default; the old AA_EnableHighDpiScaling /
    # AA_UseHighDpiPixmaps attributes are deprecated no-ops since Qt 6.0 and
    # generate DeprecationWarnings in PySide6 6.x — nothing to set here.
    pass


def _configure_default_font(app: QApplication) -> None:
    # Inter ships in webjam_qt/theme/fonts (OFL — see THIRD_PARTY_NOTICES).
    # A missing or corrupt bundle must never block launch: fall back to the
    # platform UI font (SF Pro on macOS, Segoe UI on Windows).
    fonts_dir = Path(__file__).resolve().parent / "theme" / "fonts"
    loaded = False
    if fonts_dir.is_dir():
        for ttf in sorted(fonts_dir.glob("Inter-*.ttf")):
            if QFontDatabase.addApplicationFont(str(ttf)) != -1:
                loaded = True
    if loaded and "Inter" in QFontDatabase.families():
        font = QFont("Inter")
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(13)
    app.setFont(font)


def _report_unhandled_exception(exception_type, exception, traceback) -> None:
    """Turn an uncaught Qt callback error into one safe restart instruction."""
    logging.getLogger("webjam.qt").critical(
        "Unhandled WebJam UI error",
        exc_info=(exception_type, exception, traceback),
    )
    app = QApplication.instance()
    try:
        QMessageBox.critical(
            QApplication.activeWindow(),
            "WebJam needs to restart",
            "Your current session could not continue safely. Close WebJam, "
            "then open it again.",
        )
    except Exception:  # noqa: BLE001 - the process log is the final fallback
        logging.getLogger("webjam.qt").exception(
            "WebJam could not show the runtime failure screen"
        )
    if app is not None:
        app.quit()


def _request_smoke_quit(window: ConductorWindow) -> None:
    """Quit a frozen Host smoke after representing an affirmative close choice."""
    logging.getLogger("webjam.qt").info(
        "Frozen Host smoke timer confirming close and requesting Qt quit"
    )
    # A live Host normally asks before ending the jam.  The headless smoke has
    # no operator to click Yes, so bypass only that already-tested prompt.  Qt
    # still closes the real window, emits close_requested, and runs the same
    # controller shutdown used after an affirmative musician choice.
    window.confirm_close = lambda: True
    window.close()


def _run_app() -> int:
    """Build and run the application after the fatal-error boundary."""
    settings = load_settings()
    configure_logging(settings)
    logging.getLogger("webjam.qt").info("Starting Conductor UI")

    _configure_qt_attributes()
    app = QApplication.instance() or WebJamApplication(sys.argv)
    app.setApplicationName("WebJam")
    app.setApplicationDisplayName("WebJam")
    app.setOrganizationName("WebJam")
    app.setWindowIcon(make_brand_icon())

    _configure_default_font(app)
    app.setStyleSheet(load_stylesheet())

    # Every ordinary launch begins with the same two choices. Existing config
    # supplies invisible defaults; it never skips the Host/Join decision.
    smoke_autostart = os.environ.get("WEBJAM_SMOKE_AUTOSTART_AUDIO") == "1"
    launch = None
    if not smoke_autostart:
        argument_invite = _invite_from_arguments(sys.argv)
        pending_invite = (
            app.take_pending_invite() if isinstance(app, WebJamApplication) else ""
        )
        launch = LaunchDialog(
            settings,
            initial_invite_url=argument_invite or pending_invite,
        )
        launch_invite_handler = None
        if isinstance(app, WebJamApplication):
            def _deliver_launch_invite(value: str) -> None:
                app.acknowledge_invite(value)
                launch.accept_invite(value)

            launch_invite_handler = _deliver_launch_invite
            app.invite_url_received.connect(launch_invite_handler)
            late_invite = app.take_pending_invite()
            if late_invite:
                QTimer.singleShot(
                    0, lambda value=late_invite: _deliver_launch_invite(value)
                )
        result = launch.exec()
        if isinstance(app, WebJamApplication) and launch_invite_handler is not None:
            try:
                app.invite_url_received.disconnect(launch_invite_handler)
            except (RuntimeError, TypeError):
                pass
        if result == LaunchDialog.DialogCode.Rejected:
            return 0
        settings = load_settings(settings.config_file)
    elif not Path(settings.config_file).expanduser().exists():
        # Deterministic frozen-build smoke path only. Normal users never enter
        # here because the environment variable is absent.
        apply_host_defaults(settings)
        from core.settings import save_settings

        save_settings(settings)
        settings = load_settings(settings.config_file)

    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Band Rehearsal",
    )
    controller = ApplicationController(
        window,
        settings=settings,
        session_invite=(
            getattr(launch, "band_invite", None)
            if launch is not None and launch.selected_role == "join"
            else None
        ),
    )
    # Qt may terminate the native event loop without returning from exec() on
    # some platform shutdown paths.  Keep the finally block below as a second,
    # idempotent guard, but also tie cleanup to Qt's guaranteed quit signal so
    # Jamulus and the local companion service cannot be orphaned.
    app.aboutToQuit.connect(controller.shutdown)
    controller.start_companion_api()  # optional localhost bridge for DAWs/editors
    if launch is not None and launch.selected_role == "join":
        window.session_strip.set_session_title(launch.session_name)
        controller._save_session_title()
    if isinstance(app, WebJamApplication):
        def _deliver_live_invite(value: str) -> None:
            app.acknowledge_invite(value)
            controller.accept_invite_url(value)

        app.invite_url_received.connect(_deliver_live_invite)
        late_invite = app.take_pending_invite()
        if late_invite:
            QTimer.singleShot(
                0, lambda value=late_invite: _deliver_live_invite(value)
            )
    window.show()
    # A matching Band Check verification keeps returning musicians on the
    # one-click path. New or changed audio setups are checked before WebJam
    # starts the production music engine. Frozen smoke validation deliberately
    # bypasses this human confirmation gate.
    QTimer.singleShot(
        0,
        controller._on_launch_audio
        if smoke_autostart
        else controller.start_session_or_band_check,
    )
    if smoke_autostart:
        # Frozen-build validation needs to exercise the real Host lifecycle
        # and then leave through Qt's ordinary quit path.  A process signal
        # only tests the bootloader and can bypass ``aboutToQuit`` entirely.
        # Keep this hook unavailable to normal launches and bounded so a bad
        # environment value can never close an interactive session.
        try:
            smoke_exit_ms = int(os.environ.get("WEBJAM_SMOKE_EXIT_MS", "0"))
        except ValueError:
            smoke_exit_ms = 0
        if 1_000 <= smoke_exit_ms <= 60_000:
            QTimer.singleShot(
                smoke_exit_ms, lambda: _request_smoke_quit(window)
            )

    previous_exception_hook = sys.excepthook
    sys.excepthook = _report_unhandled_exception
    try:
        exit_code = app.exec()
    finally:
        sys.excepthook = previous_exception_hook
        controller.shutdown()
    return exit_code


def run() -> int:
    """Run WebJam with one plain-language last-resort failure screen."""
    try:
        return _run_app()
    except Exception:  # noqa: BLE001 - this is the process-level safety net
        logging.getLogger("webjam.qt").exception("WebJam failed during startup")
        try:
            app = QApplication.instance()
            if app is None:
                _configure_qt_attributes()
                app = WebJamApplication(sys.argv)
            QMessageBox.critical(
                None,
                "WebJam couldn’t open",
                "Quit WebJam and open it again. If this keeps happening, "
                "reinstall the latest WebJam build.",
            )
        except Exception:  # noqa: BLE001 - logging is the only fallback left
            logging.getLogger("webjam.qt").exception(
                "WebJam could not show the startup failure screen"
            )
        return 1


if __name__ == "__main__":
    sys.exit(run())
