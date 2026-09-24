"""Scrollable workflow help with an always-visible dismissal control."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
)

from webjam_qt.controllers.window_layout import centered_window_rect
from webjam_qt.theme.brand import render_brand_pixmap


class HelpDialog(QDialog):
    """Keep long or enlarged help inside the chosen display, not a native sheet."""

    def __init__(self, body: str, available_geometry: QRect | None = None) -> None:
        super().__init__()
        self.setWindowTitle("WebJam Help")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._available_geometry = QRect(available_geometry) if available_geometry else QRect()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        content = QHBoxLayout()
        content.setSpacing(16)
        self._brand = QLabel()
        self._brand.setObjectName("HelpBrand")
        self._brand.setAccessibleName("WebJam")
        self._brand.setPixmap(render_brand_pixmap(64))
        content.addWidget(self._brand, 0, Qt.AlignmentFlag.AlignTop)

        self._body = QTextBrowser()
        self._body.setAccessibleName("Workflow help")
        self._body.setAccessibleDescription(
            "Read-only help. Use the arrow or Page Down keys to scroll."
        )
        self._body.setOpenLinks(False)
        self._body.setOpenExternalLinks(False)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setHtml(body)
        content.addWidget(self._body, 1)
        layout.addLayout(content, 1)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self._buttons.accepted.connect(self.accept)
        self._ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setAccessibleName("Close help")
        # Cocoa otherwise skips this button when tabbing out of the text.
        self._ok.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._ok.setDefault(True)
        layout.addWidget(self._buttons)
        self.setTabOrder(self._body, self._ok)
        self.resize(640, 520)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_to_screen()
        # Native frame margins settle when the window is first shown. The
        # text browser keeps wrapping/scrolling independent of dialog height.
        QTimer.singleShot(0, self._fit_to_screen)
        self._ok.setFocus(Qt.FocusReason.OtherFocusReason)

    def _fit_to_screen(self) -> None:
        available = self._available_geometry
        if available.isEmpty():
            return
        frame, inner = self.frameGeometry(), self.geometry()
        extra_width = frame.width() - inner.width()
        extra_height = frame.height() - inner.height()
        target = centered_window_rect(
            available.adjusted(12, 12, -12, -12),
            QSize(640 + extra_width, 520 + extra_height),
        )
        if target.isEmpty():
            return
        self.setGeometry(
            target.x() + inner.x() - frame.x(),
            target.y() + inner.y() - frame.y(),
            max(1, target.width() - extra_width),
            max(1, target.height() - extra_height),
        )
