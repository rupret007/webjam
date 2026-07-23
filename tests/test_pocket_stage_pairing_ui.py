from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from core.pocket_stage import PairingScope
from services.pocket_stage_gateway import PocketStagePairingOffer
from webjam_qt.windows.pocket_stage_pairing import PocketStagePairingDialog


_APP = QApplication.instance() or QApplication([])


def _offer() -> PocketStagePairingOffer:
    return PocketStagePairingOffer(
        session_id="66666666-6666-4666-8666-666666666666",
        endpoint="wss://192.168.1.10:18443/v1/pocket",
        certificate_fingerprint_sha256="ab" * 32,
        capability_id="55555555-5555-4555-8555-555555555555",
        capability_token="S" * 43,
        expires_at_unix=4_000_000_000,
        display_name="Band Rehearsal",
        scopes=(
            PairingScope.OBSERVE,
            PairingScope.MIX,
            PairingScope.MARKERS,
            PairingScope.RECORD,
        ),
    )


def test_pairing_dialog_renders_qr_without_exposing_bearer_as_text() -> None:
    offer = _offer()
    dialog = PocketStagePairingDialog(
        SimpleNamespace(connected_clients=0),  # type: ignore[arg-type]
        offer,
    )
    try:
        assert dialog._qr.pixmap().isNull() is False
        visible_and_accessible = " ".join(
            label.text() + " " + label.accessibleName()
            for label in dialog.findChildren(QLabel)
        )
        assert offer.capability_token not in visible_and_accessible
        assert offer.endpoint not in visible_and_accessible
        assert "Your monitor mix" in dialog._controls.text()
        assert "Host recording" in dialog._controls.text()
        dialog.show()
        _APP.processEvents()
        assert dialog.height() <= 560
        assert dialog._qr.pixmap().width() <= 300
    finally:
        dialog.close()


def test_pairing_dialog_reports_connected_without_claiming_audio_state() -> None:
    dialog = PocketStagePairingDialog(
        SimpleNamespace(connected_clients=1),  # type: ignore[arg-type]
        _offer(),
    )
    try:
        dialog._refresh_status()
        assert "iPhone connected securely" in dialog._status.text()
        assert "audio" not in dialog._status.text().lower()
    finally:
        dialog.close()


def test_pairing_dialog_requires_new_code_after_connected_phone_leaves() -> None:
    gateway = SimpleNamespace(connected_clients=0)
    dialog = PocketStagePairingDialog(gateway, _offer())  # type: ignore[arg-type]
    try:
        gateway.connected_clients = 1
        dialog._refresh_status()
        gateway.connected_clients = 0
        dialog._refresh_status()

        assert dialog._qr.text() == "iPhone disconnected"
        assert "one-use code was consumed" in dialog._status.text()
        assert "Waiting" not in dialog._status.text()
    finally:
        dialog.close()
