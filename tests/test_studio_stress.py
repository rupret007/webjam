"""Bounded 12-track/60-minute acceptance gate for the Studio workspace."""

from __future__ import annotations

import hashlib
import os
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import core.studio_export as studio_export  # noqa: E402
from core.studio_export import (  # noqa: E402
    StudioExportCancelled,
    export_studio_arrangement,
)
from core.studio_renderer import StudioRenderer, StudioRenderStream  # noqa: E402
from core.studio_source_catalog import StudioSourceCatalog  # noqa: E402
from core.studio_store import (  # noqa: E402
    load_studio_document,
    save_studio_document,
)
from core.take_project import (  # noqa: E402
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
    write_take_project,
)
from core.studio_waveform import WaveformTileCache  # noqa: E402
from webjam_qt.widgets.studio_arrange import (  # noqa: E402
    MAX_ARRANGE_WAVEFORM_BINDINGS,
    MAX_ARRANGE_WAVEFORM_BYTES,
    MAX_PIXELS_PER_SECOND,
    MIN_PIXELS_PER_SECOND,
    StudioArrange,
)
from webjam_qt.widgets.studio_waveforms import (  # noqa: E402
    StudioWaveformCoordinator,
)


RATE = 48_000
TRACKS = 12
DURATION_FRAMES = RATE * 60 * 60
SOURCE_FRAMES = RATE // 10
_NAMESPACE = uuid.UUID("7aa84138-5cb4-453a-86bf-85d9752fa011")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1_048_576):
            digest.update(block)
    return digest.hexdigest()


def _write_sparse_pcm16_wav(
    path: Path,
    *,
    sample_rate: int,
    frame_count: int,
) -> None:
    """Write a valid zero-filled mono WAV whose payload can remain sparse."""

    bytes_per_sample = 2
    data_size = frame_count * bytes_per_sample
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * bytes_per_sample,
        bytes_per_sample,
        bytes_per_sample * 8,
        b"data",
        data_size,
    )
    with path.open("wb") as handle:
        handle.write(header)
        handle.seek(data_size - 1, os.SEEK_CUR)
        handle.write(b"\0")


def _long_project(tmp_path: Path) -> tuple[Path, TakeProject, tuple[Path, ...]]:
    take_root = tmp_path / "take"
    media_root = take_root / "media"
    media_root.mkdir(parents=True)
    tracks: list[ProjectTrack] = []
    sources: list[Path] = []
    for index in range(TRACKS):
        source = media_root / f"track-{index + 1:02d}.wav"
        _write_sparse_pcm16_wav(
            source,
            sample_rate=RATE,
            frame_count=SOURCE_FRAMES,
        )
        sources.append(source)
        if index == 0:
            project_start_frame = 0
        elif index == TRACKS - 1:
            project_start_frame = DURATION_FRAMES - SOURCE_FRAMES
        else:
            project_start_frame = (
                DURATION_FRAMES // 2 + (index - TRACKS // 2) * SOURCE_FRAMES
            )
        segment = MediaSegment(
            segment_id=_id(f"segment:{index}"),
            path=str(source.relative_to(take_root)),
            project_start_frame=project_start_frame,
            frame_count=SOURCE_FRAMES,
            sample_rate=RATE,
            channels=1,
            sample_format="PCM_16",
            media_status=MediaStatus.AVAILABLE,
            sha256=_sha256(source),
            size_bytes=source.stat().st_size,
            has_signal=True,
        )
        tracks.append(
            ProjectTrack(
                track_id=_id(f"track:{index}"),
                source_id=_id(f"source:{index}"),
                participant_id=None,
                name=f"Musician {index + 1}",
                instrument="",
                source_type=SourceType.JAMULUS_SERVER,
                quality=SourceQuality.NETWORK_TRACK,
                media_status=MediaStatus.AVAILABLE,
                order=index,
                segments=(segment,),
            )
        )
    project = TakeProject(
        session_id=_id("session"),
        take_id=_id("take"),
        session_title="Synthetic long rehearsal",
        take_name="60 minute stress take",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=RATE,
        participants=(),
        tracks=tuple(tracks),
    )
    write_take_project(take_root, project)
    return take_root, project, tuple(sources)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_twelve_track_sixty_minute_workspace_and_cancelled_export_stay_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    take_root, project, sources = _long_project(tmp_path)
    manifest = take_root / "webjam-take.json"
    evidence_before = (_sha256(manifest), *(_sha256(path) for path in sources))

    initial = load_studio_document(take_root)
    assert len(initial.document.tracks) == TRACKS
    assert len(initial.document.regions) == TRACKS
    first_region = initial.document.regions[0]
    edited = initial.document.move_region(first_region.region_id, RATE)
    saved = save_studio_document(take_root, edited, expected_token=initial.token)
    reopened = load_studio_document(take_root)
    assert reopened.token == saved.token
    assert reopened.document.to_dict() == edited.to_dict()
    assert DURATION_FRAMES == 172_800_000
    assert max(item.timeline_end_frame for item in reopened.document.regions) == (
        DURATION_FRAMES
    )
    assert sum(
        item.frame_count for track in project.tracks for item in track.segments
    ) == TRACKS * SOURCE_FRAMES
    assert sum(path.stat().st_size for path in sources) < 256 * 1024

    arrange = StudioArrange()
    arrange.resize(760, 600)
    arrange.set_document(reopened.document)
    arrange.show()
    qapp.processEvents()
    arrange.set_zoom(MAX_PIXELS_PER_SECOND)
    arrange.scroll_to_frame(30 * 60 * RATE)
    qapp.processEvents()
    zoomed_ids = arrange.visible_region_ids()
    assert 0 < len(zoomed_ids) < TRACKS
    arrange.set_zoom(MIN_PIXELS_PER_SECOND)
    qapp.processEvents()

    catalog = StudioSourceCatalog.load(project, take_root)
    published: list[object] = []
    errors: list[object] = []
    executor = ThreadPoolExecutor(max_workers=2)

    def publish_tile(delivery) -> None:
        published.append(delivery)
        arrange.add_region_waveform_tile(
            delivery.region_id,
            delivery.tile,
            generation=delivery.generation,
        )

    cache = WaveformTileCache(
        max_entries=32,
        max_bytes=1024 * 1024,
        read_chunk_frames=256,
        tile_peaks=64,
        max_viewport_tiles=16,
    )
    coordinator = StudioWaveformCoordinator(
        executor,
        publish_tile=publish_tile,
        publish_error=errors.append,
        cache=cache,
    )
    try:
        coordinator.activate(reopened.document, catalog)
        view_start, view_end = arrange.visible_frame_range()
        generation = coordinator.schedule(
            view_start,
            view_end,
            arrange.pixels_per_frame,
            arrange.visible_region_ids(),
        )
        arrange.begin_waveform_generation(generation)
        stats = coordinator.stats
        assert stats.in_flight <= 2
        assert stats.pending + stats.in_flight <= 256
        deadline = time.monotonic() + 5.0
        while not published and not errors and time.monotonic() < deadline:
            coordinator.drain()
            qapp.processEvents()
            if published or errors:
                break
            time.sleep(0.005)

        assert errors == []
        assert published
        cache_stats = cache.stats
        assert 0 < cache_stats.entries <= cache_stats.max_entries
        assert 0 < cache_stats.bytes <= cache_stats.max_bytes
        assert 0 < cache_stats.max_read_frames <= cache.read_chunk_frames
        assert cache_stats.read_calls > 0
        assert 0 < arrange.waveform_retained_entries <= (
            MAX_ARRANGE_WAVEFORM_BINDINGS
        )
        assert 0 < arrange.waveform_retained_bytes <= MAX_ARRANGE_WAVEFORM_BYTES
        coordinator.cancel()
        coordinator.drain()
    finally:
        coordinator.shutdown(shutdown_executor=True, wait=True)
        arrange.close()

    render_block_bytes: list[tuple[int, int]] = []
    renderer = StudioRenderer(
        project,
        reopened.document,
        take_root,
        source_catalog=catalog,
        block_frames=127,
    )
    render_start = DURATION_FRAMES // 2
    with renderer.open(
        start_frame=render_start,
        end_frame=render_start + 127 * 3,
    ) as stream:
        while stream.remaining_frames:
            mix, tracks = stream.read_with_tracks(127)
            render_block_bytes.append(
                (mix.nbytes, sum(item.nbytes for item in tracks.values()))
            )
    assert len(render_block_bytes) == 3
    assert max(item[0] for item in render_block_bytes) <= 127 * 2 * 4
    assert max(item[1] for item in render_block_bytes) <= TRACKS * 127 * 2 * 4

    observed_blocks: list[tuple[int, int, int]] = []
    cancelled = threading.Event()
    original_read = StudioRenderStream.read_with_tracks

    def cancel_after_one_block(self, frame_count):
        result = original_read(self, frame_count)
        mix, tracks = result
        observed_blocks.append(
            (
                frame_count,
                mix.nbytes,
                sum(item.nbytes for item in tracks.values()),
            )
        )
        cancelled.set()
        return result

    monkeypatch.setattr(
        StudioRenderStream,
        "read_with_tracks",
        cancel_after_one_block,
    )

    def accept_sparse_timeline_preflight(
        _destination_root,
        *,
        frames,
        audio_file_count,
        reserve_bytes,
        metadata_bytes,
    ):
        # The export is deliberately cancelled after one bounded render block;
        # reserving the full logical hour would make this memory/I/O gate depend
        # on whichever test volume happens to host the checkout.
        assert frames == DURATION_FRAMES
        assert reserve_bytes == 0
        return frames * 2 * 3 * audio_file_count + metadata_bytes

    monkeypatch.setattr(
        studio_export,
        "_preflight_disk",
        accept_sparse_timeline_preflight,
    )
    destination = tmp_path / "exports"
    with pytest.raises(StudioExportCancelled, match="cancelled"):
        export_studio_arrangement(
            project,
            reopened.document,
            take_root,
            destination_root=destination,
            source_catalog=catalog,
            block_frames=127,
            disk_reserve_bytes=0,
            cancel_event=cancelled,
        )

    assert observed_blocks
    assert max(item[0] for item in observed_blocks) <= 127
    assert max(item[1] for item in observed_blocks) <= 127 * 2 * 4
    assert max(item[2] for item in observed_blocks) <= TRACKS * 127 * 2 * 4
    assert not destination.exists() or not tuple(destination.iterdir())
    assert (_sha256(manifest), *(_sha256(path) for path in sources)) == evidence_before
