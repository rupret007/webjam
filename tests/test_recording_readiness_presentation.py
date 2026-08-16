from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from core.recording_readiness_presentation import (
    MAX_READINESS_BLOCKERS,
    MAX_READINESS_SOURCES,
    RecordingChannelTopology,
    RecordingReadinessModelError,
    RecordingReadinessPresentation,
    RecordingReadinessSource,
    RecordingSourceKind,
    RecordingSourceReadiness,
    RecordingStoragePresentation,
    RecordingStorageReadiness,
    SharedTrackPresentation,
    SharedTrackReadiness,
)


def _source(
    source_id: str = "server:alice",
    *,
    kind: RecordingSourceKind = RecordingSourceKind.SERVER,
    topology: RecordingChannelTopology = RecordingChannelTopology.MONO,
    required: bool = True,
    readiness: RecordingSourceReadiness = RecordingSourceReadiness.READY,
    detail: str = "Signal is present.",
    meter_percent: int | None = 62,
) -> RecordingReadinessSource:
    return RecordingReadinessSource(
        source_id=source_id,
        participant_label="Alice",
        source_label="Vocal",
        kind=kind,
        topology=topology,
        required=required,
        readiness=readiness,
        detail=detail,
        meter_percent=meter_percent,
    )


def _presentation(
    *,
    sources: tuple[RecordingReadinessSource, ...] | None = None,
    storage_readiness: RecordingStorageReadiness = RecordingStorageReadiness.READY,
    shared_readiness: SharedTrackReadiness = SharedTrackReadiness.READY,
    shared_required: bool = True,
    blockers: tuple[str, ...] = (),
) -> RecordingReadinessPresentation:
    return RecordingReadinessPresentation(
        profile_label="Music",
        sources=sources if sources is not None else (_source(),),
        storage=RecordingStoragePresentation(
            readiness=storage_readiness,
            summary="48.2 GB available",
            detail="Enough space for the expected take.",
        ),
        shared_track=SharedTrackPresentation(
            readiness=shared_readiness,
            required=shared_required,
            summary="Reference mix · stereo",
            detail="Shared Track route is verified.",
        ),
        blockers=blockers,
    )


def test_ready_snapshot_preserves_exact_source_semantics_and_is_immutable() -> None:
    sources = (
        _source(),
        _source(
            "local:host:1",
            kind=RecordingSourceKind.LOCAL_ORIGINAL,
            topology=RecordingChannelTopology.STEREO,
            required=False,
            meter_percent=41,
        ),
        _source(
            "shared:reference",
            kind=RecordingSourceKind.SHARED_TRACK,
            topology=RecordingChannelTopology.STEREO,
            meter_percent=None,
        ),
    )
    snapshot = _presentation(sources=sources)

    assert snapshot.can_start
    assert snapshot.ready_source_count == 3
    assert [source.kind.label for source in snapshot.sources] == [
        "Server track",
        "Local Original",
        "Shared Track",
    ]
    assert [source.topology.channels for source in snapshot.sources] == [1, 2, 2]
    assert snapshot.sources[1].obligation_label == "Optional"
    assert "Meter unavailable" in snapshot.sources[2].accessible_description
    with pytest.raises(FrozenInstanceError):
        snapshot.profile_label = "Podcast & Voice"  # type: ignore[misc]


def test_required_non_ready_sources_and_storage_derive_aggregate_blockers() -> None:
    required = _source(
        readiness=RecordingSourceReadiness.ACTION_NEEDED,
        detail="No signal is present.",
    )
    optional = _source(
        "local:optional",
        kind=RecordingSourceKind.LOCAL_ORIGINAL,
        required=False,
        readiness=RecordingSourceReadiness.ACTION_NEEDED,
        detail="Optional input is disconnected.",
    )
    snapshot = _presentation(
        sources=(required, optional),
        storage_readiness=RecordingStorageReadiness.ACTION_NEEDED,
        blockers=("Confirm the session title.",),
    )

    assert not snapshot.can_start
    assert snapshot.effective_blockers == (
        "Confirm the session title.",
        "Alice · Vocal: No signal is present.",
        "Enough space for the expected take.",
    )
    assert "Optional input is disconnected" not in " ".join(snapshot.effective_blockers)


def test_required_shared_track_must_be_ready_but_optional_can_be_absent() -> None:
    blocked = _presentation(shared_readiness=SharedTrackReadiness.CHECKING)
    optional = _presentation(
        shared_readiness=SharedTrackReadiness.NOT_INCLUDED,
        shared_required=False,
    )

    assert not blocked.can_start
    assert blocked.shared_track.detail in blocked.effective_blockers
    assert optional.can_start


@pytest.mark.parametrize("meter", [-1, 101, True, 2.5])
def test_meter_is_strictly_bounded(meter: object) -> None:
    with pytest.raises(RecordingReadinessModelError):
        _source(meter_percent=meter)  # type: ignore[arg-type]


def test_snapshot_rejects_ambiguous_or_unbounded_inputs() -> None:
    duplicate = (_source(), _source())
    with pytest.raises(RecordingReadinessModelError, match="unique"):
        _presentation(sources=duplicate)
    with pytest.raises(RecordingReadinessModelError, match="too many readiness"):
        _presentation(
            sources=tuple(
                _source(f"server:{index}") for index in range(MAX_READINESS_SOURCES + 1)
            )
        )
    with pytest.raises(RecordingReadinessModelError, match="too many readiness"):
        _presentation(
            blockers=tuple(
                f"Blocker {index}" for index in range(MAX_READINESS_BLOCKERS + 1)
            )
        )

    with pytest.raises(TypeError, match="source_id"):
        _source(7)  # type: ignore[arg-type]
    with pytest.raises(RecordingReadinessModelError, match="opaque identifier"):
        _source("/Users/creator/input-one")


def test_free_text_is_path_redacted_and_repr_never_discloses_labels() -> None:
    source = RecordingReadinessSource(
        source_id="local:1",
        participant_label="/Users/creator/private",
        source_label=r"C:\\Users\\creator\\take.wav",
        kind=RecordingSourceKind.LOCAL_ORIGINAL,
        topology=RecordingChannelTopology.STEREO,
        required=True,
        readiness=RecordingSourceReadiness.ACTION_NEEDED,
        detail="Could not open /Users/creator/private/take.wav",
    )
    snapshot = _presentation(
        sources=(source,),
        blockers=("Retry /Users/creator/private/take.wav",),
    )

    rendered = " ".join(
        (
            source.participant_label,
            source.source_label,
            source.detail,
            *snapshot.effective_blockers,
        )
    )
    assert "/Users/creator" not in rendered
    assert r"C:\\Users" not in rendered
    assert "creator" not in repr(source)
    assert "creator" not in repr(snapshot)
