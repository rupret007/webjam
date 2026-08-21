"""Bounded frozen-runtime proof for Pocket Stage's lazy dependencies.

This module is reachable only through the frozen CI smoke hook in
``webjam_qt.app``. It deliberately uses loopback so packaging verification is
deterministic and doesn't open a production LAN sharing session. The ordinary
musician flow still requires an explicit private-interface gateway.
"""

from __future__ import annotations

import hashlib
import os
import ssl
import tempfile
import time
import uuid
from pathlib import Path

from core.pocket_stage import (
    MobileRecordingState,
    MobileSessionProjection,
    PairingClaim,
    PairingScope,
    PocketStageEnvelope,
    PocketStageMessageKind,
)
from core.session_conductor import (
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
)
from services.pocket_stage_gateway import PocketStageGateway

SUCCESS_MARKER = "WebJam Pocket Stage frozen-runtime smoke passed"


def _write_success_marker(result_path: Path) -> None:
    """Write one CI result only inside a securely created temp directory."""

    path = result_path.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    parent = path.parent
    if (
        parent.parent != temporary_root
        or not parent.name.startswith("webjam-pocket-smoke-")
        or not parent.is_dir()
        or path.name != "result.txt"
        or path.exists()
    ):
        raise RuntimeError("Pocket Stage runtime smoke result path is invalid.")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(SUCCESS_MARKER + "\n")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _projection() -> MobileSessionProjection:
    return MobileSessionProjection(
        generation=1,
        revision=1,
        role=SessionRole.HOST,
        phase=SessionConductorPhase.CONNECTED,
        primary_action=SessionPrimaryAction.NONE,
        primary_enabled=False,
        recording_state=MobileRecordingState.IDLE,
    )


def run_frozen_pocket_stage_smoke(*, result_path: Path) -> int:
    """Exercise the packaged QR, TLS server, client, pair, and teardown path."""

    # These imports are intentionally inside the hook: they are the lazily
    # loaded packages this frozen-runtime smoke exists to prove were bundled.
    import segno
    from websockets.sync.client import connect

    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda *_args: (_ for _ in ()).throw(
            AssertionError("The runtime smoke sends no commands.")
        ),
        host="127.0.0.1",
        port=0,
        allow_loopback_for_tests=True,
    )
    connection = None
    try:
        if not gateway.start():
            raise RuntimeError("Pocket Stage runtime smoke gateway did not start.")
        offer = gateway.issue_pairing_offer(
            scopes=(PairingScope.OBSERVE,),
            ttl_seconds=30,
            display_name="WebJam package smoke",
        )

        # Force the packaged QR encoder to build the complete symbol, not just
        # import successfully. No QR bytes or bearer material leave this process.
        qr = segno.make(offer.qr_code_text, error="m")
        if int(qr.symbol_size(scale=1, border=4)[0]) <= 0:
            raise RuntimeError("Pocket Stage runtime smoke QR was empty.")

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = connect(
            offer.endpoint,
            ssl=context,
            proxy=None,
            open_timeout=3,
            close_timeout=2,
            ping_interval=None,
            max_size=65_536,
        )
        peer_der = connection.socket.getpeercert(binary_form=True)
        if hashlib.sha256(peer_der).hexdigest() != (
            offer.certificate_fingerprint_sha256
        ):
            raise RuntimeError("Pocket Stage runtime smoke certificate mismatch.")

        pair = PocketStageEnvelope(
            kind=PocketStageMessageKind.PAIR,
            message_id=str(uuid.uuid4()),
            generation=0,
            sequence=0,
            sent_at_unix_ms=int(time.time() * 1000),
            body=PairingClaim(
                capability_token=offer.capability_token,
                claim_id=str(uuid.uuid4()),
            ),
        )
        connection.send(pair.to_json())
        snapshot = PocketStageEnvelope.from_json(connection.recv(timeout=3))
        if snapshot.kind is not PocketStageMessageKind.SNAPSHOT:
            raise RuntimeError("Pocket Stage runtime smoke did not receive a snapshot.")
    finally:
        if connection is not None:
            connection.close()
        gateway.stop()

    _write_success_marker(result_path)
    return 0
