"""Session-scoped host permission for a single higher-delay browser guest."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from services.web_join_host import WebJoinHost


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

    def _toggle(self, allowed):
        if not allowed:
            self.stop()
            return
        server = self._owned_server()
        if server is None or self._host is not None or self._retired_hosts:
            self.stop()
            return
        self._generation += 1
        host = WebJoinHost()
        self._host, self._server = host, server
        self.kick_action.setEnabled(True)
        self.allow_action.setText("Preparing web invite…")
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
            if not self.stop():
                return
            self._controller.window.flash_message(
                "Web Join is unavailable. Check the host audio bridge and free relay service.",
                ms=7000,
            )
            return
        self.allow_action.setText("Allow a web guest")
        self.copy_action.setEnabled(True)
        self._controller.window.flash_message(
            "Web invite ready. Choose Web Join → Copy web invite. Expect about 60–120 ms delay; wear headphones.",
            ms=9000,
        )

    def _copy(self):
        if (
            self._host is not None
            and self._host.invite
            and self._owned_server() is self._server
        ):
            QApplication.clipboard().setText(self._host.invite)
            self._controller.window.flash_message(
                "Web invite copied. Keep this session open.", ms=5000
            )

    def _show_cleanup_pending(self):
        self._cleanup_pending = True
        self.allow_action.setEnabled(False)
        self.allow_action.setText("Web guest cleanup pending")
        self.copy_action.setEnabled(False)
        self.kick_action.setEnabled(True)

    def stop(self) -> bool:
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
        return True
