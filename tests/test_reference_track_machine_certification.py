"""Opt-in hardware proof that route authority is earned on this machine.

The rest of the suite injects a fake CoreAudio scanner, which is what keeps
it deterministic on CI.  That also means it cannot catch the class of bug
this file exists for: a build in which every synthetic path passes while the
production factory still refuses to certify real, capable hardware.

Run on a Mac that has an official BlackHole 16ch/64ch device installed:

    WEBJAM_RUN_MACHINE_AUDIO=1 .venv/bin/pytest -q \
        tests/test_reference_track_machine_certification.py

Nothing here launches Jamulus, opens PortAudio, or writes to disk --
``capability()`` is the read-only inspection boundary.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from services.reference_track_backend import create_reference_audio_backend


pytestmark = [
    pytest.mark.skipif(
        os.environ.get("WEBJAM_RUN_MACHINE_AUDIO") != "1",
        reason="real-hardware certification proof is opt-in",
    ),
    pytest.mark.skipif(
        sys.platform != "darwin",
        reason="the BlackHole route backend is macOS-only",
    ),
]


def _machine_capability():
    capability = create_reference_audio_backend().capability()
    if not capability.available:
        pytest.skip(
            "this Mac has no certifiable BlackHole route: " f"{capability.detail}"
        )
    return capability


def test_capable_hardware_earns_route_authority_without_a_flag() -> None:
    capability = _machine_capability()

    assert capability.reason_code == "ready"
    assert capability.backend == "blackhole"
    assert "BlackHole" in (capability.route_name or "")
    # The musician must never be shown an internal release milestone.
    assert "physical macOS pilot" not in capability.detail
    assert "private test candidate" not in capability.detail


def test_certified_machine_enables_play_in_the_dialog() -> None:
    """The whole chain: real hardware -> capability -> an enabled Play button.

    v0.22.2 could not reach this state on any machine, because the backend
    refused to certify before these probes ever ran.
    """

    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from webjam_qt.windows.reference_track import (
        ReferenceTrackDialog,
        ReferenceTrackPrimaryGate,
    )

    capability = _machine_capability()
    app = QApplication.instance() or QApplication([])
    assert app is not None

    dialog = ReferenceTrackDialog()
    try:
        # Session lifecycle is a separate, legitimate gate; stand in for a
        # live hosted and connected jam so this test isolates certification.
        dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
        dialog.set_snapshot(_loaded_snapshot(capability))

        assert dialog._play.isEnabled() is True
        assert "Playback route ready" in dialog._route.text()
        assert "physical macOS pilot" not in dialog._route.text()
    finally:
        dialog.deleteLater()


def _loaded_snapshot(capability):
    """A song-loaded snapshot carrying this machine's real capability."""

    return SimpleNamespace(
        # The dialog compares snapshot.state against plain state strings.
        state="ready",
        capability=capability,
        source_name="Rehearsal Reference.flac",
        source_format="FLAC",
        source_samplerate=48_000,
        source_channels=2,
        duration_s=120.0,
        position_s=0.0,
        loop_start_s=0.0,
        loop_end_s=None,
        trim_db=0.0,
        count_in_beats=0,
        count_in_bpm=120.0,
        error="",
    )
