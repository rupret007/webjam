"""Unsigned 0.27.0 Shared Track play path: no catalog pin, a doable next step.

The live jamulus-components catalog's webjam_version is not the Shared Track
play gate. A host can play a local file once the isolated BlackHole route is
on this Mac. If that route is missing, the product names a step a person can
do without a signed catalog. A mute 'Needs attention' badge is not that step.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from services.bridge_service import (
    BridgeService,
    _bundled_reference_track_jamulus_candidate,
)
from services.reference_track_backend import (
    MacOSBlackHoleReferenceBackend,
    create_reference_audio_backend,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.widgets.session_strip import (
    shared_track_next_step_label,
    shared_track_play_is_locked,
    shared_track_status_label,
)


def _play_path_sources() -> str:
    return "\n".join(
        inspect.getsource(item)
        for item in (
            _bundled_reference_track_jamulus_candidate,
            BridgeService.find_reference_track_jamulus,
            create_reference_audio_backend,
            MacOSBlackHoleReferenceBackend.capability,
            MacOSBlackHoleReferenceBackend._route_certification,
            ApplicationController._play_reference_track,
        )
    )


def test_shared_track_play_path_does_not_consult_catalog_webjam_version() -> None:
    source = _play_path_sources()
    assert "webjam_version" not in source
    assert "supports_webjam" not in source
    assert "jamulus-components" not in source
    assert "exact-compatibility" not in source


def test_local_load_without_blackhole_never_says_needs_attention() -> None:
    snapshot = SimpleNamespace(
        state="ready",
        source_name="Taylor Swift - The Fate of Ophelia.mp3",
        cleanup_pending=False,
        count_in_active=False,
        error="",
        can_play=False,
        capability=SimpleNamespace(
            available=False,
            reason_code="physical_certification_required",
        ),
    )
    assert shared_track_play_is_locked(snapshot) is True
    assert shared_track_status_label(snapshot) == "Set up the audio device"
    assert shared_track_next_step_label(snapshot) == "Set up the audio device"
    for forbidden in ("Needs attention", "Ready", "Paused"):
        assert forbidden not in shared_track_status_label(snapshot)
