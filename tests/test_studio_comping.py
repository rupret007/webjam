"""Repeated-take lane import, quick-swipe, audition, and removal coverage."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.studio_comping import (
    StudioCompingError,
    add_take_lane,
    audition_lane_document,
    compatible_source_tracks,
    remove_take_lane,
    select_lane_range,
)
from core.studio_project import StudioProjectError, default_studio_document
from core.studio_renderer import StudioRenderError, StudioRenderer
from core.studio_source_catalog import StudioSourceCatalog
from core.take_export import _reference_fingerprint
from core.take_project import (
    AlignmentAnchor,
    AlignmentState,
    MediaSegment,
    MediaStatus,
    Participant,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _project(
    root: Path,
    *,
    take_id: str,
    value: float,
    participant_id: str = _id(50),
    track_id: str = _id(10),
    segment_id: str = _id(20),
    name: str = "Vocal",
    status: ProjectStatus = ProjectStatus.COMPLETE,
    source_type: SourceType = SourceType.LOCAL_ISOLATED,
    quality: SourceQuality = SourceQuality.VERIFIED_ISOLATED,
    alignment: AlignmentState | None = None,
) -> TakeProject:
    root.mkdir(parents=True, exist_ok=True)
    media = root / "vocal.wav"
    sf.write(media, np.full(16, value, dtype=np.float32), 8_000, subtype="FLOAT")
    info = sf.info(media)
    segment = MediaSegment(
        segment_id=segment_id,
        path=media.name,
        project_start_frame=0,
        frame_count=int(info.frames),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        sample_format=str(info.subtype),
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(media.read_bytes()).hexdigest(),
        size_bytes=media.stat().st_size,
        has_signal=True,
    )
    track = ProjectTrack(
        track_id=track_id,
        source_id=_id(int(take_id[-3:]) + 100),
        participant_id=participant_id,
        name=name,
        instrument="Voice",
        source_type=source_type,
        quality=quality,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(segment,),
        alignment=(
            alignment
            if alignment is not None
            else AlignmentState(confidence=0.91, method="test-verified-alignment")
        ),
    )
    project = TakeProject(
        session_id=_id(1),
        take_id=take_id,
        session_title="Comp fixture",
        take_name=f"Take {take_id[-2:]}",
        status=status,
        project_sample_rate=8_000,
        participants=(Participant(participant_id, name, "Voice"),),
        tracks=(track,),
    )
    (root / "webjam-take.json").write_text(
        json.dumps(project.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    return project


def _peer_project(
    root: Path,
    *,
    take_id: str,
    value: float,
    verified: bool,
) -> TakeProject:
    project = _project(root, take_id=take_id, value=value)
    participant_id = project.tracks[0].participant_id
    reference_media = root / "vocal-server.wav"
    sf.write(
        reference_media,
        np.full(16, value / 2.0, dtype=np.float32),
        8_000,
        subtype="FLOAT",
    )
    info = sf.info(reference_media)
    reference_segment = MediaSegment(
        segment_id=_id(21),
        path=reference_media.name,
        project_start_frame=0,
        frame_count=int(info.frames),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        sample_format=str(info.subtype),
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(reference_media.read_bytes()).hexdigest(),
        size_bytes=reference_media.stat().st_size,
        has_signal=True,
    )
    reference = ProjectTrack(
        track_id=_id(11),
        source_id=_id(111),
        participant_id=participant_id,
        name="Vocal server reference",
        instrument="Voice",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(reference_segment,),
        alignment=AlignmentState(confidence=1.0, method="server-origin"),
    )
    peer = replace(
        project.tracks[0],
        order=1,
        alignment=AlignmentState(
            automatic_offset_s=0.001,
            confidence=0.93,
            method=(
                "peer-local-original-verified-alignment/test-v1"
                if verified
                else "peer-local-original-awaiting-reference/test-v1"
            ),
            residual_ms=0.4,
            anchors=(
                AlignmentAnchor(0.0, 0.001, 0.2),
                AlignmentAnchor(0.001, 0.002, 0.3),
                AlignmentAnchor(0.002, 0.003, 0.2),
            ),
            reference_track_id=reference.track_id if verified else "",
            reference_fingerprint_sha256=(
                _reference_fingerprint(reference) if verified else ""
            ),
        ),
    )
    project = replace(project, tracks=(reference, peer))
    (root / "webjam-take.json").write_text(
        json.dumps(project.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    return project


def test_add_quick_swipe_audition_and_remove_lane_are_authoritative(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    alternate_root = tmp_path / "take-2"
    primary = _project(primary_root, take_id=_id(2), value=0.2)
    alternate = _project(alternate_root, take_id=_id(3), value=0.8)
    primary_manifest = (primary_root / "webjam-take.json").read_bytes()
    alternate_manifest = (alternate_root / "webjam-take.json").read_bytes()
    document = default_studio_document(primary)

    with_lane = add_take_lane(
        document,
        primary,
        alternate,
        destination_track_id=primary.tracks[0].track_id,
    )
    assert with_lane.revision == document.revision + 1
    assert len(with_lane.take_lanes) == 1
    assert len(with_lane.regions) == 2
    assert (
        add_take_lane(
            with_lane,
            primary,
            alternate,
            destination_track_id=primary.tracks[0].track_id,
        )
        is with_lane
    )

    lane = with_lane.take_lanes[0]
    comped = select_lane_range(
        with_lane,
        lane.lane_id,
        4,
        12,
        boundary_ms=0,
    )
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root,),
    )
    rendered = StudioRenderer(
        primary,
        comped,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 16)
    expected = np.array([0.2] * 4 + [0.8] * 8 + [0.2] * 4, dtype=np.float32)
    np.testing.assert_allclose(rendered[:, 0], expected, atol=1e-6)

    audition = audition_lane_document(comped, lane.lane_id)
    auditioned = StudioRenderer(
        primary,
        audition,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 16)
    np.testing.assert_allclose(auditioned[:, 0], 0.8, atol=1e-6)

    removed = remove_take_lane(comped, lane.lane_id)
    assert removed.lane_for(lane.lane_id).deleted
    assert all(removed.region_for(region_id).deleted for region_id in lane.region_ids)
    assert all(item.deleted for item in removed.comp_ranges)
    base_only = StudioRenderer(
        primary,
        removed,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 16)
    np.testing.assert_allclose(base_only[:, 0], 0.2, atol=1e-6)

    assert (primary_root / "webjam-take.json").read_bytes() == primary_manifest
    assert (alternate_root / "webjam-take.json").read_bytes() == alternate_manifest


def test_quick_swipe_splits_prior_lane_selection_without_overlap(
    tmp_path: Path,
) -> None:
    primary = _project(tmp_path / "take-1", take_id=_id(2), value=0.2)
    first = _project(tmp_path / "take-2", take_id=_id(3), value=0.5)
    second = _project(tmp_path / "take-3", take_id=_id(4), value=0.8)
    document = default_studio_document(primary)
    document = add_take_lane(
        document,
        primary,
        first,
        destination_track_id=primary.tracks[0].track_id,
    )
    first_lane = document.take_lanes[-1]
    document = add_take_lane(
        document,
        primary,
        second,
        destination_track_id=primary.tracks[0].track_id,
    )
    second_lane = document.take_lanes[-1]
    document = select_lane_range(document, first_lane.lane_id, 2, 14, boundary_ms=0)
    document = select_lane_range(document, second_lane.lane_id, 6, 10, boundary_ms=0)

    active = sorted(
        (item for item in document.comp_ranges if not item.deleted and item.enabled),
        key=lambda item: item.timeline_start_frame,
    )
    assert [
        (item.timeline_start_frame, item.timeline_end_frame, item.lane_id)
        for item in active
    ] == [
        (2, 6, first_lane.lane_id),
        (6, 10, second_lane.lane_id),
        (10, 14, first_lane.lane_id),
    ]
    assert all(
        right.timeline_start_frame >= left.timeline_end_frame
        for left, right in zip(active, active[1:])
    )


def test_quick_swipe_rejects_cut_inside_existing_comp_fade_atomically(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    first_root = tmp_path / "take-2"
    second_root = tmp_path / "take-3"
    primary = _project(primary_root, take_id=_id(2), value=0.2)
    first = _project(first_root, take_id=_id(3), value=0.5)
    second = _project(second_root, take_id=_id(4), value=0.8)
    document = add_take_lane(
        default_studio_document(primary),
        primary,
        first,
        destination_track_id=primary.tracks[0].track_id,
    )
    first_lane = document.take_lanes[-1]
    document = add_take_lane(
        document,
        primary,
        second,
        destination_track_id=primary.tracks[0].track_id,
    )
    second_lane = document.take_lanes[-1]
    document = select_lane_range(
        document,
        first_lane.lane_id,
        0,
        16,
        boundary_ms=0.5,
    )
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(first_root, second_root),
    )
    before = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 16)

    with pytest.raises(StudioCompingError, match="existing comp fade"):
        select_lane_range(
            document,
            second_lane.lane_id,
            2,
            10,
            boundary_ms=0,
        )

    after = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 16)
    np.testing.assert_array_equal(after, before)
    assert document.comp_ranges[0].fade_in_frames == 4
    assert document.comp_ranges[0].fade_out_frames == 4


def test_quick_swipe_preserves_outer_fade_at_fade_out_seam(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    first_root = tmp_path / "take-2"
    second_root = tmp_path / "take-3"
    primary = _project(primary_root, take_id=_id(2), value=0.2)
    first = _project(first_root, take_id=_id(3), value=0.5)
    second = _project(second_root, take_id=_id(4), value=0.8)
    document = add_take_lane(
        default_studio_document(primary),
        primary,
        first,
        destination_track_id=primary.tracks[0].track_id,
    )
    first_lane = document.take_lanes[-1]
    document = add_take_lane(
        document,
        primary,
        second,
        destination_track_id=primary.tracks[0].track_id,
    )
    second_lane = document.take_lanes[-1]
    document = select_lane_range(
        document,
        first_lane.lane_id,
        0,
        12,
        boundary_ms=0.5,
    )
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(first_root, second_root),
    )
    before = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 12)

    changed = select_lane_range(
        document,
        second_lane.lane_id,
        4,
        8,
        boundary_ms=0.5,
    )
    after = StudioRenderer(
        primary,
        changed,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 12)

    active = sorted(
        (item for item in changed.comp_ranges if item.enabled and not item.deleted),
        key=lambda item: item.timeline_start_frame,
    )
    assert [
        (
            item.timeline_start_frame,
            item.timeline_end_frame,
            item.fade_in_frames,
            item.fade_out_frames,
        )
        for item in active
    ] == [(0, 4, 4, 0), (4, 8, 2, 2), (8, 12, 0, 4)]
    np.testing.assert_array_equal(after[:4], before[:4])
    np.testing.assert_array_equal(after[8:], before[8:])


def test_nested_same_lane_swipe_is_render_equivalent_and_preserves_outer_fades(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    first_root = tmp_path / "take-2"
    second_root = tmp_path / "take-3"
    primary = _project(primary_root, take_id=_id(2), value=0.2)
    first = _project(first_root, take_id=_id(3), value=0.5)
    second = _project(second_root, take_id=_id(4), value=0.8)
    document = add_take_lane(
        default_studio_document(primary),
        primary,
        first,
        destination_track_id=primary.tracks[0].track_id,
    )
    first_lane = document.take_lanes[-1]
    document = add_take_lane(
        document,
        primary,
        second,
        destination_track_id=primary.tracks[0].track_id,
    )
    second_lane = document.take_lanes[-1]
    document = select_lane_range(
        document,
        first_lane.lane_id,
        0,
        12,
        boundary_ms=0.5,
    )
    document = select_lane_range(
        document,
        second_lane.lane_id,
        4,
        8,
        boundary_ms=0.5,
    )
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(first_root, second_root),
    )
    before = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 12)

    nested = select_lane_range(
        document,
        first_lane.lane_id,
        1,
        3,
        boundary_ms=0.5,
    )
    after = StudioRenderer(
        primary,
        nested,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 12)

    assert nested is document
    assert [
        (
            item.timeline_start_frame,
            item.timeline_end_frame,
            item.fade_in_frames,
            item.fade_out_frames,
        )
        for item in document.comp_ranges
        if item.enabled and not item.deleted
    ] == [(0, 4, 4, 0), (4, 8, 2, 2), (8, 12, 0, 4)]
    np.testing.assert_array_equal(after, before)


def test_comp_lane_rejects_unverified_local_but_allows_server_timeline(
    tmp_path: Path,
) -> None:
    primary = _project(tmp_path / "take-1", take_id=_id(2), value=0.2)
    document = default_studio_document(primary)
    unverified = _project(
        tmp_path / "take-2",
        take_id=_id(3),
        value=0.8,
        alignment=AlignmentState(confidence=0.0, method="unverified"),
    )

    assert compatible_source_tracks(primary.tracks[0], unverified) == ()
    with pytest.raises(StudioCompingError, match="no verified timeline alignment"):
        add_take_lane(
            document,
            primary,
            unverified,
            destination_track_id=primary.tracks[0].track_id,
        )

    # A persisted lane cannot bypass the same check if its cataloged source
    # later presents an unverified COMPLETE manifest at the renderer boundary.
    forged_root = tmp_path / "take-3"
    initially_verified = _project(
        forged_root,
        take_id=_id(4),
        value=0.7,
    )
    forged_document = add_take_lane(
        document,
        primary,
        initially_verified,
        destination_track_id=primary.tracks[0].track_id,
    )
    now_unverified = replace(
        initially_verified,
        tracks=(
            replace(
                initially_verified.tracks[0],
                alignment=AlignmentState(confidence=0.0, method="unverified"),
            ),
        ),
    )
    (forged_root / "webjam-take.json").write_text(
        json.dumps(now_unverified.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    forged_catalog = StudioSourceCatalog.load(
        primary,
        tmp_path / "take-1",
        additional_take_roots=(forged_root,),
    )
    with pytest.raises(StudioRenderError, match="no verified timeline alignment"):
        StudioRenderer(
            primary,
            forged_document,
            tmp_path / "take-1",
            source_catalog=forged_catalog,
        )

    server_alignment = AlignmentState(confidence=0.0, method="server-origin")
    server_primary = _project(
        tmp_path / "server-1",
        take_id=_id(102),
        value=0.2,
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        alignment=server_alignment,
    )
    server_alternate = _project(
        tmp_path / "server-2",
        take_id=_id(103),
        value=0.8,
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        alignment=server_alignment,
    )
    compatible = compatible_source_tracks(
        server_primary.tracks[0],
        server_alternate,
    )
    assert compatible == (server_alternate.tracks[0],)
    with_server_lane = add_take_lane(
        default_studio_document(server_primary),
        server_primary,
        server_alternate,
        destination_track_id=server_primary.tracks[0].track_id,
    )
    assert len(with_server_lane.take_lanes) == 1


def test_peer_original_requires_verified_provenance_and_current_reference(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    waiting_root = tmp_path / "take-2"
    verified_root = tmp_path / "take-3"
    primary = _project(primary_root, take_id=_id(2), value=0.2)
    waiting = _peer_project(
        waiting_root,
        take_id=_id(3),
        value=0.6,
        verified=False,
    )
    waiting_peer = next(
        item for item in waiting.tracks if item.source_type is SourceType.LOCAL_ISOLATED
    )
    assert compatible_source_tracks(primary.tracks[0], waiting) == ()
    with pytest.raises(StudioCompingError, match="no verified timeline alignment"):
        add_take_lane(
            default_studio_document(primary),
            primary,
            waiting,
            destination_track_id=primary.tracks[0].track_id,
            source_track_id=waiting_peer.track_id,
        )

    verified = _peer_project(
        verified_root,
        take_id=_id(4),
        value=0.8,
        verified=True,
    )
    verified_peer = next(
        item
        for item in verified.tracks
        if item.source_type is SourceType.LOCAL_ISOLATED
    )
    document = add_take_lane(
        default_studio_document(primary),
        primary,
        verified,
        destination_track_id=primary.tracks[0].track_id,
        source_track_id=verified_peer.track_id,
    )
    lane = document.take_lanes[-1]
    document = select_lane_range(document, lane.lane_id, 8, 16, boundary_ms=0)
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(verified_root,),
    )
    rendered = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 16)
    np.testing.assert_allclose(
        rendered[:, 0],
        np.array([0.2] * 8 + [0.8] * 8, dtype=np.float32),
        atol=1e-6,
    )

    # The lane was built from valid metadata, but the renderer must bind peer
    # timing provenance to the still-current server reference bytes again.
    reference_media = verified_root / "vocal-server.wav"
    sf.write(
        reference_media,
        np.full(16, 0.1, dtype=np.float32),
        8_000,
        subtype="FLOAT",
    )
    with pytest.raises(StudioRenderError, match="no verified timeline alignment"):
        StudioRenderer(
            primary,
            document,
            primary_root,
            source_catalog=catalog,
        )


def test_comp_lane_requires_same_session_rate_status_and_musician(
    tmp_path: Path,
) -> None:
    primary = _project(tmp_path / "take-1", take_id=_id(2), value=0.2)
    document = default_studio_document(primary)
    wrong_musician = _project(
        tmp_path / "take-2",
        take_id=_id(3),
        value=0.8,
        participant_id=_id(999),
        name="Other",
    )
    assert compatible_source_tracks(primary.tracks[0], wrong_musician) == ()
    with pytest.raises(StudioCompingError, match="unambiguous"):
        add_take_lane(
            document,
            primary,
            wrong_musician,
            destination_track_id=primary.tracks[0].track_id,
        )

    bad_status = replace(wrong_musician, status=ProjectStatus.NEEDS_ATTENTION)
    with pytest.raises(StudioCompingError, match="complete or explicitly recovered"):
        add_take_lane(
            document,
            primary,
            bad_status,
            destination_track_id=primary.tracks[0].track_id,
        )

    wrong_session = replace(wrong_musician, session_id=_id(888))
    with pytest.raises(StudioCompingError, match="different session"):
        add_take_lane(
            document,
            primary,
            wrong_session,
            destination_track_id=primary.tracks[0].track_id,
        )


def test_active_cross_take_region_cannot_escape_its_lane(tmp_path: Path) -> None:
    primary = _project(tmp_path / "take-1", take_id=_id(2), value=0.2)
    alternate = _project(tmp_path / "take-2", take_id=_id(3), value=0.8)
    document = add_take_lane(
        default_studio_document(primary),
        primary,
        alternate,
        destination_track_id=primary.tracks[0].track_id,
    )
    with pytest.raises(StudioProjectError, match="cross-take region"):
        replace(document, take_lanes=())
