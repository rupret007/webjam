"""Offline proof that the frozen lesson view loads HTML and returns JavaScript."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

SUCCESS_MARKER = "WebJam lesson player frozen-runtime smoke passed"


def run_frozen_youtube_smoke(*, result_path: Path) -> int:
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from webjam_qt.widgets.youtube_video_player import (
        APP_ORIGIN, NativeYouTubeBridge, initialize_youtube_webview,
    )

    path = result_path.resolve()
    if (path.parent.parent != Path(tempfile.gettempdir()).resolve()
            or not path.parent.name.startswith("webjam-lesson-smoke-")
            or path.name != "result.txt" or path.exists()):
        raise RuntimeError("Invalid lesson smoke result path.")
    initialize_youtube_webview()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    bridge = NativeYouTubeBridge()
    bridge.surface.resize(640, 360)
    bridge.surface.show()
    success = False
    pending = False
    bridge._view.loadHtml("<!doctype html><html><script>window.webjamSmoke=42;</script></html>",
                          QUrl(APP_ORIGIN + "/"))

    def observed(value):
        nonlocal success, pending
        pending = False
        if str(value) == "42":
            success = True
            timer.stop()
            bridge.close()
            QTimer.singleShot(100, app.quit)

    def poll():
        nonlocal pending
        if pending:
            return
        pending = True
        bridge._view.runJavaScript("JSON.stringify(window.webjamSmoke)", observed)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    QTimer.singleShot(10_000, app.quit)
    app.exec()
    timer.stop()
    bridge.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    if not success:
        return 1
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(SUCCESS_MARKER + "\n")
    return 0
