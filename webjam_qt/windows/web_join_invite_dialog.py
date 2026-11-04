"""Host dialog: copy Web Join link and show an offline QR for phone guests."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.web_join_invite_share import copy_text_to_qt_clipboard, web_join_invite_qr_png
from webjam_qt.theme.tokens import Color, Space


class WebJoinInviteDialog(QDialog):
    """One obvious copy action, a read-only link field, and a phone QR."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WebJoinInviteDialog")
        self.setWindowTitle("Invite a browser guest")
        self.setMinimumWidth(375)
        self.resize(420, 520)

        intro = QLabel(
            "Send this link to one bandmate on a phone or laptop browser. "
            "Web Join has higher delay — headphones required."
        )
        intro.setWordWrap(True)

        self._link = QLineEdit()
        self._link.setObjectName("WebJoinInviteLink")
        self._link.setReadOnly(True)
        self._link.setAccessibleName("Web Join invite link")
        self._link.setAccessibleDescription(
            "Full link to open in a browser. Select and copy if clipboard is blocked."
        )

        self._copy = QPushButton("Copy invite link")
        self._copy.setObjectName("PrimaryButton")
        self._copy.setAccessibleName("Copy invite link")
        self._copy.setAccessibleDescription(
            "Copies the Web Join link to your clipboard."
        )
        self._copy.clicked.connect(self._copy_invite)

        self._notice = QLabel()
        self._notice.setObjectName("WebJoinInviteNotice")
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)
        self._notice.setAccessibleName("Copy confirmation")

        scan_label = QLabel("Scan to join on a phone")
        scan_label.setObjectName("WebJoinInviteQrLabel")
        scan_label.setAccessibleName("Scan to join on a phone")

        self._qr = QLabel()
        self._qr.setObjectName("WebJoinInviteQr")
        self._qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr.setMinimumSize(220, 220)
        self._qr.setAccessibleName("Web Join invite QR code")
        self._qr.setAccessibleDescription(
            "QR code encoding the same Web Join link shown above."
        )

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.MD)
        layout.addWidget(intro)
        layout.addWidget(self._link)
        layout.addWidget(self._copy)
        layout.addWidget(self._notice)
        layout.addWidget(scan_label)
        layout.addWidget(self._qr)

        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        outer.addWidget(scroll, 1)

        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._clear_notice)

        self.setTabOrder(self._link, self._copy)

    def set_invite_url(self, url: str) -> None:
        value = str(url or "")
        self._link.setText(value)
        self._render_qr(value)
        self._clear_notice()

    def _render_qr(self, url: str) -> None:
        if not url:
            self._qr.clear()
            self._qr.setText("No invite link yet.")
            return
        try:
            png = web_join_invite_qr_png(url, dark=Color.BG_PANEL)
            pixmap = QPixmap()
            if not pixmap.loadFromData(png, "PNG"):
                raise ValueError("invalid qr png")
            self._qr.setPixmap(pixmap)
            self._qr.setText("")
        except Exception:  # noqa: BLE001 - typed-link fallback remains usable
            self._qr.setPixmap(QPixmap())
            self._qr.setText(
                "QR code could not be drawn. Copy the link above instead."
            )
            self._qr.setWordWrap(True)

    def copy_invite_link(self) -> None:
        self._copy_invite()

    def _copy_invite(self) -> None:
        from PySide6.QtWidgets import QApplication

        result = copy_text_to_qt_clipboard(
            QApplication.clipboard(), self._link.text()
        )
        if not result.ok:
            self._link.selectAll()
            self._link.setFocus(Qt.FocusReason.OtherFocusReason)
        self._show_notice(result.notice)

    def _show_notice(self, text: str) -> None:
        notice = str(text or "").strip()
        self._notice.setText(notice)
        self._notice.setVisible(bool(notice))
        self._notice_timer.stop()
        if notice:
            self._notice_timer.start(4000)

    def _clear_notice(self) -> None:
        self._notice.clear()
        self._notice.setVisible(False)
        self._notice_timer.stop()
