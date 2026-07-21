"""Repeated-take source catalog and cross-take comp rendering coverage."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.studio_project import (
    StudioCompRange,
    StudioRegion,
    StudioTakeLane,
    default_studio_document,
)
import core.studio_renderer as studio_renderer
from core.studio_renderer import (
    StudioRenderError,
    StudioRenderer,
    iter_studio_blocks,
)
from core.studio_source_catalog import (
    MAX_STUDIO_SOURCE_TAKES,
    StudioSourceCatalog,
    StudioSourceCatalogError,
)
from core.take_player import TakePlayer
from core.take_project import (
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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(
    root: Path,
    samples: np.ndarray,
    *,
    segment_id: str = _id(20),
    rate: int = 8_000,
) -> MediaSegment:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "source.wav"
    sf.write(path, np.asarray(samples, dtype=np.float32), rate, subtype="FLOAT")
    info = sf.info(path)
    return MediaSegment(
        segment_id=segment_id,
        path=path.name,
        project_start_frame=0,
        frame_count=int(info.frames),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        sample_format=str(info.subtype),
        media_status=MediaStatus.AVAILABLE,
        sha256=_digest(path),
        size_bytes=path.stat().st_size,
        has_signal=True,
    )


def _project(
    segment: MediaSegment,
    *,
    take_id: str,
    session_id: str = _id(1),
    track_id: str = _id(10),
    rate: int = 8_000,
) -> TakeProject:
    track = ProjectTrack(
        track_id=track_id,
        source_id=_id(110),
        participant_id=None,
        name="Vocal",
        instrument="Voice",
        source_type=SourceType.LOCAL_ISOLATED,
        quality=SourceQuality.VERIFIED_ISOLATED,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(segment,),
        alignment=AlignmentState(confidence=0.91, method="test-verified-alignment"),
    )
    return TakeProject(
        session_id=session_id,
        take_id=take_id,
        session_title="Catalog fixture",
        take_name=f"Take {take_id[-2:]}",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=rate,
        participants=(),
        tracks=(track,),
    )


def _write_manifest(root: Path, project: TakeProject) -> None:
    (root / "webjam-take.json").write_text(
        json.dumps(project.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )


def _cross_take_comp(primary: TakeProject, alternate: TakeProject):
    document = default_studio_document(primary)
    base_region = document.regions[0]
    alternate_segment = alternate.tracks[0].segments[0]
    alternate_region = StudioRegion(
        region_id=_id(30),
        track_id=base_region.track_id,
        source_take_id=alternate.take_id,
        source_track_id=alternate.tracks[0].track_id,
        source_segment_id=alternate_segment.segment_id,
        source_start_frame=0,
        source_frame_count=alternate_segment.frame_count,
        timeline_start_frame=0,
        timeline_frame_count=alternate_segment.frame_count,
    )
    lane = StudioTakeLane(
        lane_id=_id(31),
        track_id=base_region.track_id,
        source_take_id=alternate.take_id,
        source_track_id=alternate.tracks[0].track_id,
        name="Take 2",
        region_ids=(alternate_region.region_id,),
    )
    comp = StudioCompRange(
        comp_range_id=_id(32),
        track_id=base_region.track_id,
        lane_id=lane.lane_id,
        timeline_start_frame=3,
        frame_count=6,
        fade_in_frames=3,
        fade_out_frames=3,
    )
    return replace(
        document,
        regions=(*document.regions, alternate_region),
        take_lanes=(lane,),
        comp_ranges=(comp,),
    )


def test_cross_take_comp_uses_full_source_key_and_equal_power_boundaries(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    alternate_root = tmp_path / "take-2"
    # Repeated recordings may legitimately reuse track and segment IDs.  The
    # take ID is therefore a required part of every catalog/read-cache key.
    primary = _project(
        _source(primary_root, np.full(12, 0.2, dtype=np.float32)),
        take_id=_id(2),
    )
    alternate = _project(
        _source(alternate_root, np.full(12, 0.8, dtype=np.float32)),
        take_id=_id(3),
    )
    _write_manifest(primary_root, primary)
    _write_manifest(alternate_root, alternate)
    before = {
        primary_root / "source.wav": (primary_root / "source.wav").read_bytes(),
        alternate_root / "source.wav": (alternate_root / "source.wav").read_bytes(),
    }
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root,),
    )
    assert len(catalog) == 2
    assert catalog.project_for_take(primary.take_id) == primary
    assert catalog.project_for_take(alternate.take_id) == alternate
    assert catalog.root_for_take(primary.take_id) == primary_root.resolve()
    assert catalog.root_for_take(alternate.take_id) == alternate_root.resolve()
    assert catalog.source_keys == (
        (primary.take_id, _id(10), _id(20)),
        (alternate.take_id, _id(10), _id(20)),
    )
    document = _cross_take_comp(primary, alternate)

    renderer = StudioRenderer(
        primary,
        document,
        primary_root,
        block_frames=2,
        source_catalog=catalog,
    )
    small_blocks = np.concatenate(tuple(renderer.iter_blocks(block_frames=2)))
    large_blocks = np.concatenate(tuple(renderer.iter_blocks(block_frames=7)))
    convenience_blocks = np.concatenate(
        tuple(
            iter_studio_blocks(
                primary,
                document,
                primary_root,
                block_frames=5,
                source_catalog=catalog,
            )
        )
    )

    midpoint = np.float32(1.0 / np.sqrt(2.0))
    expected = np.array(
        [
            0.2,
            0.2,
            0.2,
            0.2,
            (0.2 + 0.8) * midpoint,
            0.8,
            0.8,
            (0.2 + 0.8) * midpoint,
            0.2,
            0.2,
            0.2,
            0.2,
        ],
        dtype=np.float32,
    )
    np.testing.assert_array_equal(small_blocks, large_blocks)
    np.testing.assert_array_equal(small_blocks, convenience_blocks)
    np.testing.assert_allclose(small_blocks[:, 0], expected, atol=1e-6)
    np.testing.assert_array_equal(small_blocks[:, 0], small_blocks[:, 1])
    assert all(path.read_bytes() == data for path, data in before.items())


def test_take_player_accepts_the_same_cross_take_catalog(tmp_path: Path) -> None:
    class PullSink:
        pull = None

        def start(self, _rate, _blocksize, pull) -> None:
            self.pull = pull

        def stop(self) -> None:
            pass

    primary_root = tmp_path / "take-1"
    alternate_root = tmp_path / "take-2"
    primary = _project(
        _source(primary_root, np.full(12, 0.2, dtype=np.float32)),
        take_id=_id(2),
    )
    alternate = _project(
        _source(alternate_root, np.full(12, 0.8, dtype=np.float32)),
        take_id=_id(3),
    )
    _write_manifest(primary_root, primary)
    _write_manifest(alternate_root, alternate)
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root,),
    )
    document = _cross_take_comp(primary, alternate)
    expected = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    ).render_block(0, 12)

    sink = PullSink()
    player = TakePlayer(samplerate=8_000, blocksize=12, sink=sink)
    player.load_studio(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    )
    player.play()
    actual = sink.pull(12)
    player.stop()

    np.testing.assert_array_equal(actual, expected)


def test_cross_take_regions_fail_closed_without_the_exact_catalog(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    alternate_root = tmp_path / "take-2"
    primary = _project(
        _source(primary_root, np.full(12, 0.2, dtype=np.float32)),
        take_id=_id(2),
    )
    alternate = _project(
        _source(alternate_root, np.full(12, 0.8, dtype=np.float32)),
        take_id=_id(3),
    )
    _write_manifest(primary_root, primary)
    _write_manifest(alternate_root, alternate)
    document = _cross_take_comp(primary, alternate)

    with pytest.raises(StudioRenderError, match="trusted source catalog"):
        StudioRenderer(primary, document, primary_root)

    primary_only = StudioSourceCatalog.load(primary, primary_root)
    with pytest.raises(StudioRenderError, match="not present"):
        StudioRenderer(
            primary,
            document,
            primary_root,
            source_catalog=primary_only,
        )


def test_renderer_revalidates_cross_take_project_status_and_musician_match(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    alternate_root = tmp_path / "take-2"
    primary = _project(
        _source(primary_root, np.full(12, 0.2, dtype=np.float32)),
        take_id=_id(2),
    )
    alternate = _project(
        _source(alternate_root, np.full(12, 0.8, dtype=np.float32)),
        take_id=_id(3),
    )
    primary_participant = Participant(_id(201), "Primary musician")
    alternate_participant = Participant(_id(202), "Different musician")
    primary = replace(
        primary,
        participants=(primary_participant,),
        tracks=(
            replace(
                primary.tracks[0],
                participant_id=primary_participant.participant_id,
            ),
        ),
    )
    wrong_musician = replace(
        alternate,
        participants=(alternate_participant,),
        tracks=(
            replace(
                alternate.tracks[0],
                participant_id=alternate_participant.participant_id,
            ),
        ),
    )
    _write_manifest(primary_root, primary)
    _write_manifest(alternate_root, wrong_musician)
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root,),
    )

    with pytest.raises(StudioRenderError, match="safe match for this musician"):
        StudioRenderer(
            primary,
            _cross_take_comp(primary, wrong_musician),
            primary_root,
            source_catalog=catalog,
        )

    needs_review = replace(alternate, status=ProjectStatus.NEEDS_ATTENTION)
    _write_manifest(alternate_root, needs_review)
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root,),
    )
    with pytest.raises(StudioRenderError, match="complete or explicitly recovered"):
        StudioRenderer(
            primary,
            _cross_take_comp(primary, needs_review),
            primary_root,
            source_catalog=catalog,
        )


def test_catalog_rejects_forged_identity_and_post_load_manifest_change(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "take-1"
    alternate_root = tmp_path / "take-2"
    primary = _project(
        _source(primary_root, np.full(12, 0.2, dtype=np.float32)),
        take_id=_id(2),
    )
    forged = _project(
        _source(alternate_root, np.full(12, 0.8, dtype=np.float32)),
        take_id=_id(3),
        session_id=_id(999),
    )
    _write_manifest(primary_root, primary)
    _write_manifest(alternate_root, forged)
    with pytest.raises(StudioSourceCatalogError, match="same session"):
        StudioSourceCatalog.load(
            primary,
            primary_root,
            additional_take_roots=(alternate_root,),
        )

    valid_alternate = replace(forged, session_id=primary.session_id)
    _write_manifest(alternate_root, valid_alternate)
    catalog = StudioSourceCatalog.load(
        primary,
        primary_root,
        additional_take_roots=(alternate_root,),
    )
    document = _cross_take_comp(primary, valid_alternate)
    manifest = alternate_root / "webjam-take.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")

    renderer = StudioRenderer(
        primary,
        document,
        primary_root,
        source_catalog=catalog,
    )
    with pytest.raises(StudioRenderError, match="manifest changed"):
        renderer.render_block(0, 12)


def test_catalog_requires_strict_schema_v2_regular_manifest(tmp_path: Path) -> None:
    root = tmp_path / "take"
    project = _project(
        _source(root, np.full(4, 0.2, dtype=np.float32)),
        take_id=_id(2),
    )
    manifest = root / "webjam-take.json"
    manifest.write_text('{"schema_version": 1}\n', encoding="utf-8")
    with pytest.raises(StudioSourceCatalogError, match="schema-v2"):
        StudioSourceCatalog.load(project, root)

    target = root / "manifest-target.json"
    target.write_text(json.dumps(project.to_dict()), encoding="utf-8")
    manifest.unlink()
    try:
        manifest.symlink_to(target.name)
    except OSError as exc:  # pragma: no cover - privilege-limited Windows
        pytest.skip(f"symlinks are unavailable: {exc}")
    with pytest.raises(StudioSourceCatalogError, match="symbolic link"):
        StudioSourceCatalog.load(project, root)


def test_catalog_take_limit_consumes_only_the_first_disallowed_root(
    tmp_path: Path,
) -> None:
    primary_root = tmp_path / "primary"
    primary = _project(
        _source(primary_root, np.full(4, 0.2, dtype=np.float32)),
        take_id=_id(2),
    )
    _write_manifest(primary_root, primary)
    consumed = 0

    def excessive_roots():
        nonlocal consumed
        for number in range(MAX_STUDIO_SOURCE_TAKES + 100):
            consumed += 1
            yield tmp_path / f"never-opened-{number}"

    with pytest.raises(StudioSourceCatalogError, match="at most 128 takes"):
        StudioSourceCatalog.load(
            primary,
            primary_root,
            additional_take_roots=excessive_roots(),
        )

    # The primary take consumes one slot. Loading stops as soon as the 128th
    # alternate proves the input is too large; the arbitrary iterable is not
    # materialized and no yielded path is opened.
    assert consumed == MAX_STUDIO_SOURCE_TAKES


@pytest.mark.parametrize("portable_fallback", [False, True])
def test_bound_reader_rejects_intermediate_directory_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    portable_fallback: bool,
) -> None:
    if not portable_fallback and not studio_renderer._SECURE_DIRFD_AVAILABLE:
        pytest.skip("dirfd no-follow traversal is unavailable")
    if portable_fallback:
        monkeypatch.setattr(studio_renderer, "_SECURE_DIRFD_AVAILABLE", False)

    root = tmp_path / "take"
    segment = _source(root, np.full(4, 0.2, dtype=np.float32))
    media = root / "media"
    media.mkdir()
    source = media / "source.wav"
    os.replace(root / "source.wav", source)
    segment = replace(segment, path="media/source.wav")
    project = _project(segment, take_id=_id(2))
    _write_manifest(root, project)
    catalog = StudioSourceCatalog.load(project, root)
    renderer = StudioRenderer(
        project,
        default_studio_document(project),
        root,
        source_catalog=catalog,
    )

    outside = tmp_path / "outside-media"
    outside.mkdir()
    shutil.copyfile(source, outside / source.name)
    parked = root / "media-original"

    with renderer.open(end_frame=4) as stream:
        assert stream.read(1).shape == (1, 2)
        os.replace(media, parked)
        try:
            media.symlink_to(outside, target_is_directory=True)
        except OSError as exc:  # pragma: no cover - privilege-limited Windows
            os.replace(parked, media)
            pytest.skip(f"symlinks are unavailable: {exc}")
        try:
            with pytest.raises(StudioSourceCatalogError, match="symbolic link"):
                _ = catalog.resolve(
                    project.take_id,
                    project.tracks[0].track_id,
                    segment.segment_id,
                ).path
            with pytest.raises(StudioRenderError, match="symbolic link"):
                stream.read(1)
        finally:
            if media.is_symlink():
                media.unlink()
            if parked.exists():
                os.replace(parked, media)


def test_bound_reader_rejects_take_root_symlink_swap(tmp_path: Path) -> None:
    root = tmp_path / "take"
    segment = _source(root, np.full(4, 0.2, dtype=np.float32))
    project = _project(segment, take_id=_id(2))
    _write_manifest(root, project)
    catalog = StudioSourceCatalog.load(project, root)
    renderer = StudioRenderer(
        project,
        default_studio_document(project),
        root,
        source_catalog=catalog,
    )

    outside = tmp_path / "outside-take"
    outside.mkdir()
    shutil.copyfile(root / "source.wav", outside / "source.wav")
    shutil.copyfile(root / "webjam-take.json", outside / "webjam-take.json")
    parked = tmp_path / "take-original"

    with renderer.open(end_frame=4) as stream:
        assert stream.read(1).shape == (1, 2)
        os.replace(root, parked)
        try:
            root.symlink_to(outside, target_is_directory=True)
        except OSError as exc:  # pragma: no cover - privilege-limited Windows
            os.replace(parked, root)
            pytest.skip(f"symlinks are unavailable: {exc}")
        try:
            with pytest.raises(StudioSourceCatalogError, match="symbolic link"):
                _ = catalog.resolve(
                    project.take_id,
                    project.tracks[0].track_id,
                    segment.segment_id,
                ).path
            with pytest.raises(StudioRenderError, match="symbolic link"):
                stream.read(1)
        finally:
            if root.is_symlink():
                root.unlink()
            if parked.exists():
                os.replace(parked, root)
