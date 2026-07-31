"""QApplication bootstrap for the Conductor UI."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox

from core.logging_config import configure_logging
from core.network_invite import BandInvite
from core.settings import load_settings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet, make_brand_icon
from webjam_qt.invitation_ingress import (
    Invitation,
    InvitationIngressError,
    InvitationSource,
    invitation_from_arguments,
    parse_invitation_at_ingress,
)
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog, apply_host_defaults


TEST_NIGHT_ARGUMENT = "--test-night"


def test_night_mode_from_arguments(arguments: Sequence[object]) -> bool:
    """Return whether the explicit, local operator flag was supplied.

    This reads the user's argument list without changing it.  The flag is
    removed only from the private list handed to Qt because it is WebJam's
    option, not a Qt option.
    """

    return any(str(argument) == TEST_NIGHT_ARGUMENT for argument in arguments[1:])


def qt_arguments_without_test_night(arguments: Sequence[object]) -> list[str]:
    """Make a Qt-only argument list without mutating ``sys.argv``.

    Qt retains its construction arguments for process lifetime and platform
    tooling may expose them. WebJam URLs can carry invitation credentials, so
    remove every such URL before QApplication is constructed. The bootstrap
    still inspects the original list through the strict ingress policy, where
    only endpoint-only v1 links may be accepted from argv.
    """

    return [
        str(argument)
        for index, argument in enumerate(arguments)
        if index == 0
        or (
            str(argument) != TEST_NIGHT_ARGUMENT
            and "webjam://" not in str(argument).lower()
        )
    ]


class WebJamApplication(QApplication):
    """QApplication that receives macOS ``webjam://`` open events."""

    invitation_received = Signal(object)
    invitation_error = Signal(str)

    def __init__(self, arguments: list[str]) -> None:
        # A QFileOpen event may arrive during QApplication construction,
        # before the launch dialog or live controller has connected a slot.
        # Retain one pending invite so a cold deep link can never disappear.
        self._pending_invitation: Invitation | None = None
        self._pending_invitation_error = ""
        super().__init__(arguments)

    def event(self, event) -> bool:  # noqa: A003
        if event.type() == QEvent.Type.FileOpen:
            try:
                url = event.url().toString()
            except AttributeError:
                url = ""
            if str(url).lower().startswith("webjam://"):
                try:
                    invitation = parse_invitation_at_ingress(
                        url,
                        source=InvitationSource.MAC_FILE_OPEN,
                        platform=sys.platform,
                    )
                except InvitationIngressError as exc:
                    # The newest file-open event wins even when malformed.
                    # Otherwise an older queued capability could launch after
                    # WebJam has already told the musician the newer link was
                    # refused.
                    self._pending_invitation = None
                    self._pending_invitation_error = str(exc)
                    self.invitation_error.emit(str(exc))
                    return True
                self._pending_invitation = invitation
                self._pending_invitation_error = ""
                self.invitation_received.emit(invitation)
                return True
        return super().event(event)

    def take_pending_invitation(self) -> Invitation | None:
        """Return and clear a typed invite received before a consumer was ready."""

        value = self._pending_invitation
        self._pending_invitation = None
        return value

    def pending_invitation(self) -> Invitation | None:
        """Return the newest typed invite without transferring its ownership."""

        return self._pending_invitation

    def invitation_is_pending(self, value: Invitation) -> bool:
        """Return whether ``value`` is still the newest unacknowledged invite."""

        return self._pending_invitation is value

    def acknowledge_invitation(self, value: Invitation) -> None:
        """Clear a just-delivered object without dropping a newer invite."""

        if self._pending_invitation is value:
            self._pending_invitation = None

    def take_pending_invitation_error(self) -> str:
        message = self._pending_invitation_error
        self._pending_invitation_error = ""
        return message


def _invite_from_arguments(arguments: list[str]) -> BandInvite | None:
    """Preserve endpoint-only v1 links while refusing argv bearers."""

    return invitation_from_arguments(arguments)


def _deliver_current_invitation(
    app: WebJamApplication,
    invitation: Invitation,
    accept: Callable[[Invitation], bool],
) -> bool:
    """Deliver only the newest invite and acknowledge it only after acceptance.

    File-open events can race a queued handoff between the launch dialog and
    the main window.  Keeping the application slot until the destination has
    explicitly accepted the typed object prevents both stale replay and silent
    loss when a destination must refuse the invitation.
    """

    if not app.invitation_is_pending(invitation):
        return False
    if not bool(accept(invitation)):
        return False
    app.acknowledge_invitation(invitation)
    return True


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
    """Quit a frozen lifecycle smoke after representing an affirmative close."""
    logging.getLogger("webjam.qt").info(
        "Frozen desktop smoke timer confirming close and requesting Qt quit"
    )
    # A live session normally asks before ending the jam. The headless smoke has
    # no operator to click Yes, so bypass only that already-tested prompt.  Qt
    # still closes the real window, emits close_requested, and runs the same
    # controller shutdown used after an affirmative musician choice.
    window.confirm_close = lambda: True
    window.close()


def _show_live_invitation_error(
    window: ConductorWindow,
    message: object,
) -> None:
    """Render a late macOS invitation error without breaking Qt dispatch."""

    window.flash_message(str(message), ms=5_000)


def _bounded_smoke_exit_ms(default: int = 0) -> int:
    """Return a test-only bounded exit delay without affecting normal users."""

    try:
        value = int(os.environ.get("WEBJAM_SMOKE_EXIT_MS", "0"))
    except ValueError:
        return default
    return value if 1_000 <= value <= 60_000 else default


def _run_app() -> int:
    """Build and run the application after the fatal-error boundary."""
    arguments = tuple(sys.argv)
    operator_mode = test_night_mode_from_arguments(arguments)
    settings = load_settings()
    configure_logging(settings)
    logging.getLogger("webjam.qt").info("Starting Conductor UI")

    _configure_qt_attributes()
    app = QApplication.instance() or WebJamApplication(
        qt_arguments_without_test_night(arguments)
    )
    app.setApplicationName("WebJam")
    app.setApplicationDisplayName("WebJam")
    app.setOrganizationName("WebJam")
    app.setWindowIcon(make_brand_icon())

    _configure_default_font(app)
    app.setStyleSheet(load_stylesheet())

    # Every ordinary launch begins with the same three musician choices.
    # Existing config supplies invisible live-session defaults; it never skips
    # the Host / Join / offline Reference Studio decision.
    smoke_autostart = os.environ.get("WEBJAM_SMOKE_AUTOSTART_AUDIO") == "1"
    smoke_launch_only = (
        getattr(sys, "frozen", False)
        and not smoke_autostart
        and os.environ.get("WEBJAM_SMOKE_LAUNCH_ONLY") == "1"
    )
    launch = None
    if not smoke_autostart:
        argument_invitation = _invite_from_arguments(arguments)
        pending_invitation = (
            app.take_pending_invitation()
            if isinstance(app, WebJamApplication)
            else None
        )
        initial_invitation = pending_invitation or argument_invitation
        launch = LaunchDialog(
            settings,
            initial_invitation=initial_invitation,
        )
        # The dialog owns the typed object now; do not keep extra capability
        # references in this long-lived bootstrap frame.
        initial_invitation = None
        pending_invitation = None
        argument_invitation = None
        launch_invite_handler = None
        launch_error_handler = None
        if isinstance(app, WebJamApplication):

            def _deliver_launch_invite(invitation: Invitation) -> None:
                _deliver_current_invitation(
                    app,
                    invitation,
                    launch.accept_invitation,
                )

            launch_invite_handler = _deliver_launch_invite
            launch_error_handler = launch.show_ingress_error
            app.invitation_received.connect(launch_invite_handler)
            app.invitation_error.connect(launch_error_handler)
            late_invitation = app.pending_invitation()
            if late_invitation is not None:
                QTimer.singleShot(
                    0,
                    lambda invitation=late_invitation: _deliver_launch_invite(
                        invitation
                    ),
                )
            late_error = app.take_pending_invitation_error()
            if late_error:
                QTimer.singleShot(
                    0, lambda message=late_error: launch.show_ingress_error(message)
                )
        if smoke_launch_only:
            # Native release runners need a clean, bounded way to prove the
            # frozen GUI reaches its real launch surface. Rejecting the
            # modal launch dialog follows the ordinary no-session exit path
            # and never starts Jamulus or mutates saved settings.
            QTimer.singleShot(_bounded_smoke_exit_ms(default=5_000), launch.reject)
        result = launch.exec()
        if isinstance(app, WebJamApplication) and launch_invite_handler is not None:
            try:
                app.invitation_received.disconnect(launch_invite_handler)
            except (RuntimeError, TypeError):
                pass
            if launch_error_handler is not None:
                try:
                    app.invitation_error.disconnect(launch_error_handler)
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

    reference_studio_launch = bool(
        launch is not None and launch.selected_role == "studio"
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title=(
            "Reference Studio" if reference_studio_launch else "Band Rehearsal"
        ),
        operator_mode=operator_mode,
    )
    remote_invitation = (
        launch.take_remote_invitation()
        if launch is not None
        and launch.selected_role == "join"
        and hasattr(launch, "take_remote_invitation")
        else None
    )
    controller = ApplicationController(
        window,
        settings=settings,
        session_invite=(
            getattr(launch, "band_invite", None)
            if launch is not None and launch.selected_role == "join"
            else None
        ),
        remote_invitation=remote_invitation,
        operator_mode=operator_mode,
        offline_reference_studio=reference_studio_launch,
    )
    remote_invitation = None
    # Qt may terminate the native event loop without returning from exec() on
    # some platform shutdown paths.  Keep the finally block below as a second,
    # idempotent guard, but also tie cleanup to Qt's guaranteed quit signal so
    # Jamulus and the local companion service cannot be orphaned.
    app.aboutToQuit.connect(controller.shutdown)
    if not reference_studio_launch:
        # The localhost companion belongs to live-session integrations.
        controller.start_companion_api()
    if launch is not None and launch.selected_role == "join":
        window.session_strip.set_session_title(launch.session_name)
        controller._save_session_title()
    if isinstance(app, WebJamApplication):

        def _deliver_live_invite(invitation: Invitation) -> None:
            _deliver_current_invitation(
                app,
                invitation,
                controller.accept_invitation,
            )

        app.invitation_received.connect(_deliver_live_invite)
        app.invitation_error.connect(
            lambda message: _show_live_invitation_error(window, message)
        )
        late_invitation = app.pending_invitation()
        if late_invitation is not None:
            QTimer.singleShot(
                0,
                lambda invitation=late_invitation: _deliver_live_invite(invitation),
            )
    window.show()
    # Open on the display's own terms instead of a fixed 1440x900 that leaves
    # the desktop showing around a floating window. Cmd+Shift+F re-snaps.
    window.fit_to_screen()
    controller.start_desktop_integrations(
        enable_update_check=not smoke_autostart,
    )
    # Host/Join is authorization to begin the non-modal Jamulus-native journey.
    # Reference Studio instead opens offline and does not start Jamulus.
    QTimer.singleShot(
        0,
        controller._on_launch_audio
        if smoke_autostart
        else (
            controller.begin_reference_studio_journey
            if reference_studio_launch
            else controller.begin_startup_journey
        ),
    )
    if smoke_autostart:
        # Frozen-build validation needs to exercise the real desktop lifecycle
        # and then leave through Qt's ordinary quit path. A process signal
        # only tests the bootloader and can bypass ``aboutToQuit`` entirely.
        # Keep this hook unavailable to normal launches and bounded so a bad
        # environment value can never close an interactive session.
        smoke_exit_ms = _bounded_smoke_exit_ms()
        if 1_000 <= smoke_exit_ms <= 60_000:
            QTimer.singleShot(smoke_exit_ms, lambda: _request_smoke_quit(window))

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
    if (
        getattr(sys, "frozen", False)
        and os.environ.get("WEBJAM_SMOKE_COMPONENT_CATALOG_RUNTIME") == "1"
    ):
        try:
            from services.jamulus_component_packaged_smoke import (
                run_frozen_component_catalog_smoke,
            )

            result_path = Path(
                os.environ.get("WEBJAM_SMOKE_COMPONENT_CATALOG_RESULT", "")
            )
            return run_frozen_component_catalog_smoke(result_path=result_path)
        except Exception as exc:  # noqa: BLE001 - release-only frozen proof
            logging.getLogger("webjam.qt").error(
                "Frozen Jamulus component catalog smoke failed; "
                "exception_type=%s",
                type(exc).__name__,
            )
            return 1
    if (
        getattr(sys, "frozen", False)
        and os.environ.get("WEBJAM_SMOKE_REFERENCE_STUDIO_RUNTIME") == "1"
    ):
        try:
            from services.reference_studio_packaged_smoke import (
                run_frozen_reference_studio_smoke,
            )

            result_path = Path(
                os.environ.get("WEBJAM_SMOKE_REFERENCE_STUDIO_RESULT", "")
            )
            return run_frozen_reference_studio_smoke(result_path=result_path)
        except Exception:  # noqa: BLE001 - bounded CI-only frozen proof
            logging.getLogger("webjam.qt").exception(
                "Frozen Reference Studio runtime smoke failed"
            )
            return 1
    if (
        getattr(sys, "frozen", False)
        and os.environ.get("WEBJAM_SMOKE_POCKET_STAGE_RUNTIME") == "1"
    ):
        try:
            from services.pocket_stage_packaged_smoke import (
                run_frozen_pocket_stage_smoke,
            )

            result_path = Path(os.environ.get("WEBJAM_SMOKE_POCKET_STAGE_RESULT", ""))
            return run_frozen_pocket_stage_smoke(result_path=result_path)
        except Exception:  # noqa: BLE001 - bounded CI-only frozen proof
            logging.getLogger("webjam.qt").exception(
                "Frozen Pocket Stage runtime smoke failed"
            )
            return 1
    try:
        return _run_app()
    except Exception:  # noqa: BLE001 - this is the process-level safety net
        logging.getLogger("webjam.qt").exception("WebJam failed during startup")
        try:
            app = QApplication.instance()
            if app is None:
                _configure_qt_attributes()
                app = WebJamApplication(qt_arguments_without_test_night(sys.argv))
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
