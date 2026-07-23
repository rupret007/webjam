"""Musician-facing pairing surface for the Pocket Stage iPhone companion."""

from __future__ import annotations

import io
import sys
import time
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Color, Space

if TYPE_CHECKING:
    from services.pocket_stage_gateway import PocketStageGateway, PocketStagePairingOffer


class PocketStagePairingDialog(QDialog):
    """Show one expiring QR without taking ownership of the gateway."""

    refresh_requested = Signal()
    stop_requested = Signal()

    def __init__(
        self,
        gateway: "PocketStageGateway",
        offer: "PocketStagePairingOffer",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._expires_at_unix = 0.0
        self._has_connected = False
        self.setObjectName("PocketStagePairingDialog")
        self.setWindowTitle("Use iPhone as Pocket Stage")
        self.setModal(False)
        self.setMinimumSize(420, 480)
        self.setMaximumHeight(560)
        self.resize(460, 560)

        title = QLabel("Pocket Stage")
        title.setObjectName("DialogTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        intro = QLabel(
            "Keep WebJam at the computer while your iPhone becomes the "
            "instrument-side mixer, cue, marker, and recording remote."
        )
        intro.setWordWrap(True)
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        intro.setTextFormat(Qt.TextFormat.PlainText)

        self._qr = QLabel()
        self._qr.setObjectName("PocketStageQrCode")
        self._qr.setAccessibleName("Pocket Stage pairing QR code")
        self._qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr.setMinimumSize(250, 250)

        if sys.platform == "darwin":
            permission_help = (
                "Allow Local Network access if asked. If pairing is blocked, "
                "check System Settings → Privacy & Security → Local Network and "
                "Network → Firewall → Options."
            )
        elif sys.platform == "win32":
            permission_help = (
                "If Windows Security asks, allow WebJam on Private networks only. "
                "Do not allow it on public networks."
            )
        else:
            permission_help = (
                "If a firewall blocks pairing, allow WebJam only on this trusted "
                "private network."
            )
        self._instruction = QLabel(
            "On iPhone, open Pocket Stage and scan this code. Both devices "
            "must be on the same private Wi-Fi network. " + permission_help
        )
        self._instruction.setWordWrap(True)
        self._instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._instruction.setTextFormat(Qt.TextFormat.PlainText)

        self._controls = QLabel()
        self._controls.setWordWrap(True)
        self._controls.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._controls.setTextFormat(Qt.TextFormat.PlainText)
        self._controls.setAccessibleName("Pocket Stage granted controls")

        self._status = QLabel()
        self._status.setObjectName("PocketStageStatus")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setAccessibleName("Pocket Stage connection status")

        self._security = QLabel(
            "The code is one-use and short-lived. The phone pins this "
            "computer's temporary certificate; WebJam never opens the "
            "companion API or the live audio path to the network."
        )
        self._security.setWordWrap(True)
        self._security.setTextFormat(Qt.TextFormat.PlainText)
        self._security.setObjectName("DialogHint")

        self._refresh_button = QPushButton("New Code")
        self._refresh_button.setObjectName("GhostButton")
        self._refresh_button.clicked.connect(self.refresh_requested.emit)

        self._stop_button = QPushButton("Stop iPhone Sharing")
        self._stop_button.setProperty("destructive", "true")
        self._stop_button.clicked.connect(self.stop_requested.emit)

        done_button = QPushButton("Done")
        done_button.setObjectName("PrimaryButton")
        done_button.setToolTip("Pairing stays active after this window closes")
        done_button.clicked.connect(self.accept)

        actions = QHBoxLayout()
        actions.addWidget(self._refresh_button)
        actions.addStretch(1)
        actions.addWidget(self._stop_button)
        actions.addWidget(done_button)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(Space.MD)
        content_layout.addWidget(title)
        content_layout.addWidget(intro)
        content_layout.addWidget(self._qr)
        content_layout.addWidget(self._instruction)
        content_layout.addWidget(self._controls)
        content_layout.addWidget(self._status)
        content_layout.addWidget(self._security)

        scroll = QScrollArea()
        scroll.setObjectName("PocketStagePairingScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.SM)
        layout.addWidget(scroll, 1)
        layout.addLayout(actions)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start()
        self.set_offer(offer)

    def set_offer(self, offer: "PocketStagePairingOffer") -> None:
        """Replace the displayed one-use capability with a newly issued one."""

        self._expires_at_unix = float(offer.expires_at_unix)
        self._has_connected = False
        self._render_qr(offer.qr_code_text)
        scope_values = {getattr(scope, "value", str(scope)) for scope in offer.scopes}
        labels = ["View session"]
        if "mix" in scope_values:
            labels.append("Your monitor mix")
        if "markers" in scope_values:
            labels.append("Mark moments")
        if "record" in scope_values:
            labels.append("Host recording")
        self._controls.setText("Phone controls: " + " · ".join(labels))
        self._refresh_status()

    def _render_qr(self, text: str) -> None:
        try:
            import segno

            qr = segno.make(text, error="m")
            output = io.BytesIO()
            # Four modules is the QR-standard quiet zone. Scale 3 keeps a
            # realistic long pairing payload usable at WebJam's 760×600 floor.
            qr.save(output, kind="png", scale=3, border=4, dark=Color.BG_PANEL)
            pixmap = QPixmap()
            if not pixmap.loadFromData(output.getvalue(), "PNG"):
                raise ValueError("Qt could not decode the generated QR code")
            self._qr.setPixmap(pixmap)
            self._qr.setText("")
        except Exception:  # noqa: BLE001 - typed-link fallback remains usable
            self._qr.setPixmap(QPixmap())
            self._qr.setText(
                "QR rendering is unavailable in this build.\n"
                "Reinstall this WebJam build before pairing an iPhone."
            )
            self._qr.setWordWrap(True)

    def set_stop_unresolved(self) -> None:
        """Freeze controls when the listener cannot prove it stopped."""

        self._timer.stop()
        self._refresh_button.setEnabled(False)
        self._stop_button.setEnabled(False)
        self._qr.setPixmap(QPixmap())
        self._qr.setText("Sharing stop unresolved")
        self._status.setText(
            "Quit WebJam before leaving this network. Do not create another code."
        )

    def _refresh_status(self) -> None:
        connected = int(getattr(self._gateway, "connected_clients", 0))
        if connected:
            self._has_connected = True
            self._qr.setPixmap(QPixmap())
            self._qr.setText("Paired securely")
            self._status.setText(
                "iPhone connected securely. You can close this window; sharing stays on."
            )
            return
        if self._has_connected:
            self._qr.setPixmap(QPixmap())
            self._qr.setText("iPhone disconnected")
            self._status.setText(
                "The one-use code was consumed. Choose New Code to reconnect."
            )
            return
        remaining = max(0, int(self._expires_at_unix - time.time()))
        if remaining <= 0:
            self._qr.setPixmap(QPixmap())
            self._qr.setText("Pairing code expired")
            self._status.setText("This pairing code expired. Choose New Code.")
        else:
            self._status.setText(f"Waiting for iPhone · code expires in {remaining}s")
