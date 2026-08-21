"""
UiThreadInvoker — marshal callables from background threads onto the Qt main thread.

Services like BridgeService run subprocess launches on daemon threads and then
invoke ``schedule_ui_callback(f)``; we must ensure ``f`` runs on the GUI thread.
This object bridges the gap via a queued signal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot


class UiThreadInvoker(QObject):
    _invoke = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Queued connection guarantees the slot runs on this object's thread
        # (which is the main thread when it was created there).
        self._invoke.connect(self._run_on_main, Qt.ConnectionType.QueuedConnection)

    def invoke(self, callable_: Callable[[], Any]) -> None:
        """Call from any thread — ``callable_`` will run on the UI thread.

        A worker (RPC reader, record-toggle, routing-scan, subprocess launch)
        can still call this after the window/controller C++ objects are torn
        down, at which point ``emit`` raises "Internal C++ object already
        deleted". That's a benign shutdown race — drop the callback rather
        than let it surface on a daemon thread.
        """
        try:
            self._invoke.emit(callable_)
        except RuntimeError:
            import logging
            logging.getLogger("webjam.ui_thread").debug(
                "UI callback dropped — invoker already destroyed"
            )

    @Slot(object)
    def _run_on_main(self, callable_: Callable[[], Any]) -> None:
        try:
            callable_()
        except Exception:
            # A misbehaving UI callback should not crash the app.
            import logging
            logging.getLogger("webjam.ui_thread").exception("UI callback failed")
