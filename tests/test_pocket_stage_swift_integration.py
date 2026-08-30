"""Opt-in live Swift URLSession ↔ Python pinned-WSS interoperability gate."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.pocket_stage import (
    MobileRecordingState,
    MobileSessionProjection,
    PairingScope,
)
from core.session_conductor import (
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
)
from services.pocket_stage_gateway import PocketStageGateway


pytestmark = pytest.mark.requires_local_socket


ROOT = Path(__file__).resolve().parents[1]


def _projection() -> MobileSessionProjection:
    """Keep the live gate independent from FastAPI's optional test client."""

    return MobileSessionProjection(
        generation=3,
        revision=9,
        role=SessionRole.HOST,
        phase=SessionConductorPhase.CONNECTED,
        primary_action=SessionPrimaryAction.NONE,
        primary_enabled=False,
        recording_state=MobileRecordingState.IDLE,
    )


@pytest.mark.skipif(
    sys.platform != "darwin"
    or os.environ.get("WEBJAM_RUN_SWIFT_POCKET_STAGE_INTEGRATION") != "1",
    reason="requires explicit macOS Swift ↔ WSS integration gate",
)
def test_swift_urlsession_pairs_with_live_pinned_gateway() -> None:
    gateway = PocketStageGateway(
        snapshot_provider=_projection,
        command_handler=lambda _request, _scopes, _epoch, _lease: (_ for _ in ()).throw(
            AssertionError("no command expected")
        ),
    )
    try:
        assert gateway.start() is True
        offer = gateway.issue_pairing_offer(
            scopes=(PairingScope.OBSERVE,),
            ttl_seconds=60,
        )
        environment = os.environ.copy()
        environment["WEBJAM_POCKET_STAGE_PAIRING_CODE"] = offer.qr_code_text
        completed = subprocess.run(
            [
                "swift",
                "test",
                "--package-path",
                str(ROOT / "ios"),
                "--filter",
                "livePinnedGatewayPairing",
            ],
            env=environment,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        assert completed.returncode == 0, (
            "Swift pinned-WSS probe failed.\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    finally:
        gateway.stop()
