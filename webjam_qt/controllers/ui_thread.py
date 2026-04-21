"""
UiThreadInvoker — marshal callables from background threads onto the Qt main thread.

Services like BridgeService run subprocess launches on daemon threads and then
invoke ``schedule_ui_callback(f)``; we must ensure ``f`` runs on the GUI thread.
This object bridges the gap via a queued signal.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot


class UiThreadInvoker(QObject):
    _invoke = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Queued connection guarantees the slot runs on this object's thread
        # (which is the main thread when it was created there).
        self._invoke.connect(self._run_on_main, Qt.ConnectionType.QueuedConnection)

    def invoke(self, callable_: Callable[[], Any]) -> None:
        """Call from any thread — ``callable_`` will run on the UI thread."""
        self._invoke.emit(callable_)

    @Slot(object)
    def _run_on_main(self, callable_: Callable[[], Any]) -> None:
        try:
            callable_()
        except Exception:  # noqa: BLE001
            # A misbehaving UI callback should not crash the app.
            import logging
            logging.getLogger("webjam.ui_thread").exception("UI callback failed")
