"""Run the initial profile chooser before the main application event loop."""

from __future__ import annotations

import sys

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QDialog


def exec_startup_dialog(app: QApplication, dialog: QDialog) -> int:
    """Keep macOS native menus usable at the standalone startup door.

    Cocoa disables the merged application Quit item for this modal startup
    dialog, even though its QAction is enabled. There is no workspace to block
    yet, so show only that standalone door nonmodally and wait for its result.
    Parented dialogs, an existing visible workspace and other platforms retain
    QDialog.exec(). The main application loop and shutdown ownership stay with
    the bootstrap; the dialog's normal close/accept guards still decide exit.
    """
    if (
        sys.platform != "darwin"
        or dialog.parent() is not None
        or any(
            widget is not dialog and widget.isVisible()
            for widget in app.topLevelWidgets()
        )
    ):
        return dialog.exec()

    original_modality = dialog.windowModality()
    loop = QEventLoop()
    result = int(QDialog.DialogCode.Rejected)
    finished = False

    def finish(code: int) -> None:
        nonlocal result, finished
        result = code
        finished = True
        loop.quit()

    def destroyed() -> None:
        nonlocal finished
        finished = True
        loop.quit()

    dialog.finished.connect(finish)
    dialog.destroyed.connect(destroyed)
    try:
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.show()
        # A show handler may synchronously accept/reject the dialog.
        if not finished:
            loop.exec()
        return result
    finally:
        try:
            dialog.finished.disconnect(finish)
            dialog.destroyed.disconnect(destroyed)
            dialog.setWindowModality(original_modality)
        except (RuntimeError, TypeError):
            # A dialog deleted during the nested loop has no signals or state
            # left to restore. Do not hide an original setup/teardown error.
            pass
