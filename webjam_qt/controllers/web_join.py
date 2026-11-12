"""Session-scoped host permission for a single higher-delay browser guest."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from services.web_join_host import PREPARE_UNAVAILABLE, PREPARATION_FAILURE_MESSAGES, WebJoinHost


class WebJoinControls(QObject):
    prepared = Signal(object, bool)

    def __init__(self, controller):
        super().__init__(controller.window)
        self._controller = controller
        self._host = None
        self._retired_hosts = []
        self._cleanup_pending = False
        self._server = None
        self._generation = 0
        self._copy_when_ready = False
        self._invite_copied = False
        self._error = ""
        self._error_server = None
        self._panel = getattr(controller.window, "web_guest_panel", None)
        self._panel_context = None
        self._panel_server = None
        self._panel_host = None
        if self._panel is not None:
            self._panel.primary_requested.connect(self._primary)
            self._panel.stop_requested.connect(self._stop_from_panel)
        menu = controller.window.menuBar().addMenu("Web Join")
        self.allow_action = menu.addAction("Allow a web guest")
        self.allow_action.setCheckable(True)
        self.allow_action.setStatusTip(
            "Web Join, higher delay (about 60–120 ms). Headphones required."
        )
        self.copy_action = menu.addAction("Copy web invite")
        self.kick_action = menu.addAction("Kick web guest")
        self.copy_action.setEnabled(False)
        self.kick_action.setEnabled(False)
        self.allow_action.toggled.connect(self._toggle)
        self.copy_action.triggered.connect(self._copy)
        self.kick_action.triggered.connect(self.stop)
        self.prepared.connect(self._prepared)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _owned_server(self):
        c = self._controller
        bridge = c.bridge
        audio = getattr(c, "audio", None)
        if (
            getattr(c, "_shutdown", False)
            or getattr(c, "_shutdown_cleanup_pending", False)
            or getattr(audio, "stopping", False)
            or getattr(audio, "cleanup_retry_required", False)
            or not getattr(c.settings, "host_server_enabled", False)
            or not bridge.hosted_server_owned()
        ):
            return None
        return bridge.hosted_server_process

    def _refresh(self):
        server = self._owned_server()
        if server is not self._error_server:
            self._error = ""
            self._error_server = None
        if self._cleanup_pending or self._retired_hosts or (self._host is not None and self._host.state == "cleanup_pending"):
            self._show_cleanup_pending()
            return
        if self._host is not None and (
            server is not self._server or self._host.state == "closed"
        ):
            if not self.stop():
                return
        self.allow_action.setEnabled(server is not None)
        if self._host is not None and self._host.state == "connected":
            self.allow_action.setText("Web guest connected (higher delay)")
            self.copy_action.setEnabled(False)
        self._render_panel()

    def _current_panel_context(self, context):
        return (context is self._panel_context and self._owned_server() is self._panel_server
                and self._host is self._panel_host)

    def _stop_from_panel(self, context):
        if self._current_panel_context(context):
            self.stop()

    def _primary(self, action, context):
        # Recheck current authority even for queued button events.
        if (not self._current_panel_context(context) or self._owned_server() is None
                or self._cleanup_pending or self._retired_hosts):
            return
        if action == "copy" and self._host is not None and self.copy_action.isEnabled():
            self._copy()
        elif action == "invite" and self._host is None and self.allow_action.isEnabled():
            self._copy_when_ready = True
            self.allow_action.setChecked(True)

    def _render_panel(self):
        if self._panel is None:
            return
        server = self._owned_server()
        if (self._panel_context is None or server is not self._panel_server
                or self._host is not self._panel_host):
            self._panel_context = object()
            self._panel_server, self._panel_host = server, self._host
        def render(state, message):
            self._panel.render(state, message, self._panel_context)
        if self._cleanup_pending or self._retired_hosts:
            render("cleanup", "The browser guest has not stopped completely. Retry ending it.")
        elif self._owned_server() is None:
            render("unavailable", "Start a hosted Music session to invite a browser guest.")
        elif self._host is not None:
            state = self._host.state
            if state == "waiting" and self.copy_action.isEnabled():
                if self._invite_copied:
                    render(
                        "ready",
                        "Link copied. Share it in your Jam room or a private message. Keep this Music session open.",
                    )
                else:
                    render("ready", "The link is ready to copy. Headphones required.")
            elif state == "connected":
                render("connected", "One browser guest is connected. End the invite to disconnect them.")
            else:
                render("preparing", "Preparing a private link for one browser guest…")
        elif self._error:
            render("error", self._error)
        else:
            render("available", "Send a private link to one browser guest. Headphones required.")

    def _toggle(self, allowed):
        if not allowed:
            self.stop()
            return
        server = self._owned_server()
        if server is None or self._host is not None or self._retired_hosts:
            self.stop()
            return
        self._generation += 1
        self._error = ""
        self._error_server = None
        host = WebJoinHost()
        self._host, self._server = host, server
        self.kick_action.setEnabled(True)
        self.allow_action.setText("Preparing web invite…")
        self._render_panel()
        port = int(self._controller.settings.jamulus_port)
        generation = self._generation

        def worker():
            success = False
            try:
                host.allow(port, generation=generation)
                success = True
            except Exception:
                try:
                    host.stop()
                except Exception:
                    pass  # The controller retains this owner for cleanup retry.
            try:
                self.prepared.emit(host, success)
            except RuntimeError:
                try:
                    host.stop()
                except Exception:
                    pass

        threading.Thread(
            target=worker, name="webjam-web-join-prepare", daemon=True
        ).start()

    def _prepared(self, host, success):
        if host is not self._host:
            # A retired preparation can finish after Kick. Its host was already
            # confirmed stopped before another invitation could be enabled.
            try:
                host.stop()
            except Exception:
                # Keep even an unexpected failed stale owner available for retry.
                if not any(owner is host for owner in self._retired_hosts):
                    self._retired_hosts.append(host)
                self._show_cleanup_pending()
            return
        if self._cleanup_pending or host.state == "cleanup_pending":
            self._show_cleanup_pending()
            return
        if not success or not host.invite or self._owned_server() is not self._server:
            reason = getattr(host, "failure_reason", PREPARE_UNAVAILABLE)
            if reason not in PREPARATION_FAILURE_MESSAGES.values():
                reason = PREPARE_UNAVAILABLE
            if not self.stop():
                return
            self._error, self._error_server = reason, self._owned_server()
            self._render_panel()
            self._controller.window.flash_message(
                reason,
                ms=7000,
            )
            return
        self.allow_action.setText("Allow a web guest")
        self.copy_action.setEnabled(True)
        if self._copy_when_ready:
            self._copy_when_ready = False
            self._copy()
        else:
            self._render_panel()

    def _copy(self):
        if (
            self._host is not None
            and self._host.state == "waiting"
            and self.copy_action.isEnabled()
            and self._owned_server() is self._server
        ):
            invite = self._host.invite
            if not invite:
                return
            QApplication.clipboard().setText(invite)
            self._invite_copied = True
            self._render_panel()
            self._controller.window.flash_message(
                "Browser invite copied. Share it in your Jam room or a private message.", ms=5000
            )

    def _show_cleanup_pending(self):
        self._cleanup_pending = True
        self.allow_action.setEnabled(False)
        self.allow_action.setText("Web guest cleanup pending")
        self.copy_action.setEnabled(False)
        self.kick_action.setEnabled(True)
        self._render_panel()

    def stop(self) -> bool:
        self._error = ""
        self._error_server = None
        self._copy_when_ready = False
        self._invite_copied = False
        owners = ([self._host] if self._host is not None else []) + self._retired_hosts
        self.allow_action.blockSignals(True)
        self.allow_action.setChecked(False)
        self.allow_action.setText("Allow a web guest")
        self.allow_action.blockSignals(False)
        self.copy_action.setEnabled(False)
        self.kick_action.setEnabled(False)
        pending = []
        for host in owners:
            try:
                if host.stop() is False:
                    pending.append(host)
                    continue
            except Exception:
                pending.append(host)
            else:
                if self._host is host:
                    self._host = None
        self._retired_hosts = [host for host in pending if host is not self._host]
        if pending:
            self._show_cleanup_pending()
            self._controller.window.flash_message(
                "Web Join could not stop completely. Choose Web Join → Kick web guest to retry.",
                ms=7000,
            )
            return False
        self._server = None
        self._cleanup_pending = False
        self.allow_action.setEnabled(self._owned_server() is not None)
        self._render_panel()
        return True
