"""Replace fully retired workspaces while retaining one application lifetime.

The session and project controllers remain the cleanup authorities. This owner
only routes application-wide events to the current destination and creates a
fresh controller after the old owner has confirmed teardown.
"""
from __future__ import annotations

from collections.abc import Callable
import logging

from PySide6.QtCore import QTimer

LOGGER = logging.getLogger("webjam.qt.workspace_navigation")


class WorkspaceCreationFailure(RuntimeError):
    """A partially opened workspace still owns cleanup and must stay visible."""
    def __init__(self, window, controller):
        super().__init__("The workspace still needs cleanup.")
        self.window = window
        self.controller = controller


class WorkspaceNavigator:
    def __init__(self, app, *, create_launch: Callable, create_workspace: Callable,
                 invitation_mailbox: bool = False) -> None:
        self.app = app
        self._create_launch = create_launch
        self._create_workspace = create_workspace
        self._invitation_mailbox = invitation_mailbox
        self.window = None
        self.controller = None
        self.launch = None
        self._generation = 0
        self._transitioning = False
        self._closed = False
        self._return_slot = None
        self._previous_quit_policy = app.quitOnLastWindowClosed()
        app.aboutToQuit.connect(self.shutdown)
        if invitation_mailbox:
            app.invitation_received.connect(self._receive_invitation)
            app.invitation_error.connect(self._receive_error)

    def adopt(self, window, controller, *, deliver_pending: bool = True) -> None:
        if self.controller is not None or self._closed:
            raise RuntimeError("The previous workspace has not retired.")
        self._generation += 1
        self.window, self.controller = window, controller
        generation = self._generation
        self._return_slot = lambda: self.return_to_launch(generation=generation)
        window.return_to_launch_requested.connect(self._return_slot)
        self.app.setQuitOnLastWindowClosed(self._previous_quit_policy)
        if self._invitation_mailbox and deliver_pending:
            invitation = self.app.pending_invitation()
            if invitation is not None:
                QTimer.singleShot(0, lambda: self._receive_invitation(
                    invitation, generation=generation,
                ))

    def _discard_pending_invitation(self) -> None:
        if self._invitation_mailbox:
            self.app.take_pending_invitation()
            self.app.take_pending_invitation_error()

    def _receive_invitation(self, invitation, *, generation=None) -> None:
        if (self._closed or self._transitioning
                or (generation is not None and generation != self._generation)
                or not self.app.invitation_is_pending(invitation)):
            return
        destination = self.launch if self.launch is not None else self.controller
        if destination is not None and destination.accept_invitation(invitation):
            self.app.acknowledge_invitation(invitation)

    def _receive_error(self, message) -> None:
        if self._closed or self._transitioning:
            return
        if self.launch is not None:
            self.launch.show_ingress_error(str(message))
        elif self.window is not None:
            self.window.flash_message(str(message), ms=5000)

    def return_to_launch(self, *, generation=None) -> bool:
        if (self._closed or self._transitioning or self.controller is None
                or (generation is not None and generation != self._generation)):
            return False
        controller, window = self.controller, self.window
        self._transitioning = True
        controller._workspace_transition_pending = True
        next_launch = None
        try:
            if not controller.prepare_return_to_launch():
                return False
            # Allocate the destination before terminal cleanup so an unreadable
            # settings file or a failed launch view leaves the current owner.
            try:
                next_launch = self._create_launch()
            except Exception:
                window.flash_message(
                    "The launch screen could not be opened. Your workspace is "
                    "still here; choose Return to launch to try again.", ms=0,
                )
                return False
            # Shutdown is deliberately terminal for this controller. A failed
            # cleanup keeps it and its retry controls; no replacement exists.
            controller._returning_to_launch = True
            if not controller.shutdown():
                return False
            self.app.setQuitOnLastWindowClosed(False)
            self._generation += 1
            self._discard_pending_invitation()
            window.return_to_launch_requested.disconnect(self._return_slot)
            self._return_slot = None
            self.controller = self.window = None
            # Closing succeeds only after shutdown's ownership proof above.
            # Never reuse hidden Studio engines or an old controller's flags.
            window.close()
            window.deleteLater()
            self._show_launch(next_launch)
            next_launch = None
            return True
        finally:
            if next_launch is not None:
                next_launch.deleteLater()
            controller._workspace_transition_pending = False
            self._transitioning = False

    def _show_launch(self, launch=None) -> None:
        self._generation += 1
        generation = self._generation
        if launch is None:
            launch = self._create_launch()
        self.launch = launch
        launch.finished.connect(
            lambda result: self._launch_finished(launch, generation, result)
        )
        launch.show()
        launch.raise_()
        launch.activateWindow()

    def _launch_finished(self, launch, generation, result) -> None:
        if (self._closed or self._transitioning or self.launch is not launch
                or generation != self._generation):
            return
        if result != launch.DialogCode.Accepted:
            self.launch = None
            self._discard_pending_invitation()
            launch.deleteLater()
            self.app.quit()
            return
        self._transitioning = True
        try:
            try:
                window, controller = self._create_workspace(launch)
            except WorkspaceCreationFailure as failure:
                self._discard_pending_invitation()
                self.launch = None
                self.adopt(failure.window, failure.controller, deliver_pending=False)
                launch.deleteLater()
                failure.window.show()
                failure.window.flash_message(
                    "The workspace could not finish opening. Choose File → "
                    "Return to launch to finish its cleanup safely.", ms=0,
                )
                return
            except Exception:
                LOGGER.error("Could not open the selected workspace")
                self._discard_pending_invitation()
                # Reuse the already constructed launch screen. Another
                # allocation can fail for the same reason and hide all UI.
                launch.restore_after_workspace_failure()
                launch.show()
                return
            self.launch = None
            library_launch = launch.selected_role == "library"
            if library_launch:
                self._discard_pending_invitation()
            self.adopt(window, controller, deliver_pending=not library_launch)
            launch.deleteLater()
        finally:
            self._transitioning = False

    def shutdown(self) -> bool:
        return self.controller is None or self.controller.shutdown()

    def dispose(self) -> None:
        """Remove application-wide callbacks after the event loop ends."""
        self._closed = True
        self._generation += 1
        self.app.aboutToQuit.disconnect(self.shutdown)
        if self._invitation_mailbox:
            self.app.invitation_received.disconnect(self._receive_invitation)
            self.app.invitation_error.disconnect(self._receive_error)
        self.app.setQuitOnLastWindowClosed(self._previous_quit_policy)
