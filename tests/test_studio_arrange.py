"""Headless interaction tests for the frame-domain Studio Arrange surface."""

from __future__ import annotations

import os
import uuid
from dataclasses import replace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.studio_project import (  # noqa: E402
    FadeCurve,
    MarkerKind,
    SnapMode,
    StudioCompRange,
    StudioCrossfade,
    StudioCycleRange,
    StudioDocument,
    StudioMarker,
    StudioRegion,
    StudioTakeLane,
    StudioTrack,
)
from core.studio_waveform import (  # noqa: E402
    WaveformSourceIdentity,
    WaveformTile,
    WaveformTileKey,
)
from webjam_qt.widgets.studio_arrange import (  # noqa: E402
    MAX_PIXELS_PER_SECOND,
    MIN_PIXELS_PER_SECOND,
    RULER_HEIGHT,
    STUDIO_ARRANGE_TRACK_HEADER_WIDTH,
    TAKE_LANE_HEIGHT,
    TRACK_HEIGHT,
    StudioArrange,
)
from webjam_qt.widgets import studio_arrange as studio_arrange_module  # noqa: E402


_NAMESPACE = uuid.UUID("536dc696-44ee-4996-8798-293e134d98b5")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _region(
    label: str,
    *,
    track_id: str,
    take_id: str,
    start: int,
    count: int,
    fade_in: int = 0,
    fade_out: int = 0,
    enabled: bool = True,
) -> StudioRegion:
    return StudioRegion(
        region_id=_id(f"{label}:region"),
        track_id=track_id,
        source_take_id=take_id,
        source_track_id=track_id,
        source_segment_id=_id(f"{label}:segment"),
        source_start_frame=0,
        source_frame_count=count,
        timeline_start_frame=start,
        timeline_frame_count=count,
        enabled=enabled,
        fade_in_frames=fade_in,
        fade_out_frames=fade_out,
        fade_in_curve=FadeCurve.S_CURVE,
        fade_out_curve=FadeCurve.EQUAL_POWER,
    )


def _document(snap_mode: SnapMode = SnapMode.OFF) -> StudioDocument:
    take_id = _id("take")
    first_track = _id("track:one")
    second_track = _id("track:two")
    first = _region(
        "first",
        track_id=first_track,
        take_id=take_id,
        start=0,
        count=48_000,
        fade_in=4_800,
        fade_out=4_800,
    )
    overlap = _region(
        "overlap",
        track_id=first_track,
        take_id=take_id,
        start=36_000,
        count=48_000,
    )
    alternate = _region(
        "alternate",
        track_id=first_track,
        take_id=take_id,
        start=0,
        count=48_000,
    )
    far = _region(
        "far",
        track_id=second_track,
        take_id=take_id,
        start=432_000,
        count=48_000,
        enabled=False,
    )
    lane_id = _id("lane")
    return StudioDocument(
        session_id=_id("session"),
        take_id=take_id,
        project_sample_rate=48_000,
        tracks=(
            StudioTrack(track_id=first_track, order=0),
            StudioTrack(track_id=second_track, order=1),
        ),
        regions=(first, overlap, alternate, far),
        take_lanes=(
            StudioTakeLane(
                lane_id=lane_id,
                track_id=first_track,
                source_take_id=take_id,
                source_track_id=first_track,
                name="Chorus alternate",
                region_ids=(alternate.region_id,),
            ),
        ),
        comp_ranges=(
            StudioCompRange(
                comp_range_id=_id("comp"),
                track_id=first_track,
                lane_id=lane_id,
                timeline_start_frame=0,
                frame_count=24_000,
                fade_out_frames=1_200,
            ),
        ),
        markers=(
            StudioMarker(
                marker_id=_id("marker:verse"),
                start_frame=24_000,
                label="Verse pickup",
            ),
            StudioMarker(
                marker_id=_id("marker:chorus"),
                start_frame=36_000,
                end_frame=96_000,
                label="Chorus",
                kind=MarkerKind.SECTION,
            ),
            StudioMarker(
                marker_id=_id("marker:far"),
                start_frame=480_000,
                label="Outro",
            ),
        ),
        crossfades=(
            StudioCrossfade(
                crossfade_id=_id("crossfade"),
                left_region_id=first.region_id,
                right_region_id=overlap.region_id,
                start_frame=36_000,
                frame_count=12_000,
            ),
        ),
        cycle_range=StudioCycleRange(start_frame=12_000, end_frame=60_000),
        snap_mode=snap_mode,
    )


def _shown_arrange(qapp: QApplication, document: StudioDocument) -> StudioArrange:
    arrange = StudioArrange()
    arrange.resize(760, 600)
    arrange.set_track_names(
        {
            document.tracks[0].track_id: "Lead vocal",
            document.tracks[1].track_id: "Guitar",
        }
    )
    arrange.set_document(document)
    arrange.show()
    qapp.processEvents()
    return arrange


def _render(arrange: StudioArrange, qapp: QApplication) -> None:
    viewport = arrange._canvas.viewport()
    image = QPixmap(viewport.size())
    viewport.render(image)
    qapp.processEvents()


def _main_row_y(arrange: StudioArrange, track_id: str) -> int:
    layout = arrange._canvas._layout_by_track[track_id]
    return round(
        RULER_HEIGHT
        + layout.top
        - arrange._canvas.verticalScrollBar().value()
        + TRACK_HEIGHT / 2
    )


def _lane_row_y(arrange: StudioArrange, lane_id: str) -> int:
    lane = arrange._canvas._lane_layouts[lane_id]
    return round(
        RULER_HEIGHT
        + lane.top
        - arrange._canvas.verticalScrollBar().value()
        + TAKE_LANE_HEIGHT / 2
    )


def test_responsive_fixed_headers_accessibility_and_public_view_controls(
    qapp: QApplication,
) -> None:
    document = _document()
    arrange = _shown_arrange(qapp, document)

    assert arrange.sizeHint().width() == 760
    assert arrange.sizeHint().height() == 600
    assert arrange._headers.width() == STUDIO_ARRANGE_TRACK_HEADER_WIDTH
    assert arrange._canvas.viewport().width() >= 560
    assert arrange._canvas.viewport().height() >= 520
    assert arrange.accessibleName() == "Studio Arrange editor"
    assert arrange._headers.accessibleName() == "Arrange track headers"
    assert "arrow keys or pointer" in arrange._headers.accessibleDescription().lower()
    assert "with the mouse" not in arrange._headers.accessibleDescription().lower()
    assert arrange._canvas.viewport().accessibleName() == "Arrange timeline canvas"
    assert "off snapping" in arrange._canvas.viewport().accessibleDescription()
    assert "drag an edge" in arrange._canvas.viewport().toolTip().lower()
    assert (
        'inside the "chorus" section'
        not in arrange._canvas.viewport().accessibleDescription().lower()
    )

    arrange.set_playhead(60_000)
    description = arrange._canvas.viewport().accessibleDescription().lower()
    assert 'inside the "chorus" section' in description

    arrange.set_playhead(0)
    assert (
        'inside the "chorus" section'
        not in arrange._canvas.viewport().accessibleDescription().lower()
    )

    arrange.set_zoom(400)
    assert arrange.pixels_per_second == 400
    arrange.set_zoom(0)
    assert arrange.pixels_per_second == MIN_PIXELS_PER_SECOND
    arrange.set_zoom(50_000)
    assert arrange.pixels_per_second == MAX_PIXELS_PER_SECOND
    arrange.set_zoom(72)
    arrange.scroll_to_frame(480_000)
    start, end = arrange.visible_frame_range()
    assert start <= 480_000 <= end

    arrange.close()


def test_viewport_paints_only_visible_rows_regions_markers_and_overlays(
    qapp: QApplication,
) -> None:
    document = _document()
    arrange = _shown_arrange(qapp, document)

    _render(arrange, qapp)
    initial = arrange._canvas._last_paint_stats
    assert initial.tracks == 2
    assert initial.lanes == 1
    assert initial.regions == 3
    assert initial.markers == 2
    assert initial.comp_ranges == 1
    assert initial.crossfades == 1

    arrange.scroll_to_frame(480_000)
    _render(arrange, qapp)
    distant = arrange._canvas._last_paint_stats
    assert distant.regions == 1
    assert distant.markers == 1
    assert distant.comp_ranges == 0
    assert distant.crossfades == 0
    arrange.close()


def test_worker_tiles_paint_progressively_and_survive_mix_only_snapshots(
    qapp: QApplication,
) -> None:
    document = _document()
    region = document.regions[0]
    arrange = _shown_arrange(qapp, document)
    identity = WaveformSourceIdentity(
        source_id=region.source_segment_id,
        catalog_key=(
            region.source_take_id,
            region.source_track_id,
            region.source_segment_id,
        ),
        frame_count=region.source_frame_count,
        sample_rate=document.project_sample_rate,
        channels=1,
        sample_format="PCM_16",
        size_bytes=100,
        sha256="a" * 64,
        gap_signature="b" * 64,
    )
    key = WaveformTileKey(
        source=identity,
        start_frame=0,
        frame_count=region.source_frame_count,
        frames_per_peak=12_000,
    )
    tile = WaveformTile(
        key,
        np.array([[-0.2], [-0.5], [-0.8], [-0.3]], dtype=np.float32),
        np.array([[0.2], [0.5], [0.8], [0.3]], dtype=np.float32),
    )
    arrange.add_region_waveform_tile(region.region_id, tile)
    assert arrange._waveform_tiles[region.region_id][key] is tile
    _render(arrange, qapp)

    mix_only = document.update_track(document.tracks[0].track_id, pan=0.25)
    arrange.set_document(mix_only)
    assert arrange._waveform_tiles[region.region_id][key] is tile

    changed_source = replace(
        mix_only.regions[0],
        source_segment_id=_id("replacement-segment"),
    )
    arrange.set_document(
        replace(
            mix_only,
            regions=(changed_source, *mix_only.regions[1:]),
            revision=mix_only.revision + 1,
        )
    )
    assert region.region_id not in arrange._waveform_tiles
    arrange.close()


def test_waveform_generations_reject_stale_tiles_and_bound_ui_retention(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    region = document.regions[0]
    arrange = _shown_arrange(qapp, document)
    identity = WaveformSourceIdentity(
        source_id=region.source_segment_id,
        catalog_key=(
            region.source_take_id,
            region.source_track_id,
            region.source_segment_id,
        ),
        frame_count=region.source_frame_count,
        sample_rate=document.project_sample_rate,
        channels=1,
        sample_format="PCM_16",
        size_bytes=100,
        sha256="a" * 64,
        gap_signature="b" * 64,
    )

    def tile_at(start_frame: int) -> WaveformTile:
        key = WaveformTileKey(
            source=identity,
            start_frame=start_frame,
            frame_count=2,
            frames_per_peak=1,
        )
        return WaveformTile(
            key,
            np.array([[-0.25], [-0.5]], dtype=np.float32),
            np.array([[0.25], [0.5]], dtype=np.float32),
        )

    arrange.begin_waveform_generation(10)
    first = tile_at(0)
    arrange.add_region_waveform_tile(region.region_id, first, generation=10)
    assert arrange._waveform_tiles[region.region_id][first.key] is first

    arrange.begin_waveform_generation(11)
    assert arrange._waveform_tiles == {}
    arrange.add_region_waveform_tile(region.region_id, first, generation=10)
    assert arrange._waveform_tiles == {}

    monkeypatch.setattr(
        studio_arrange_module,
        "MAX_ARRANGE_WAVEFORM_BINDINGS",
        2,
    )
    monkeypatch.setattr(
        studio_arrange_module,
        "MAX_ARRANGE_WAVEFORM_BYTES",
        1_024,
    )
    values = [tile_at(start) for start in (0, 2, 4)]
    for tile in values:
        arrange.add_region_waveform_tile(region.region_id, tile, generation=11)
    assert len(arrange._waveform_bindings) == 2
    assert values[0].key not in arrange._waveform_tiles[region.region_id]

    monkeypatch.setattr(
        studio_arrange_module,
        "MAX_ARRANGE_WAVEFORM_BYTES",
        values[-1].byte_size,
    )
    arrange.add_region_waveform_tile(
        region.region_id,
        tile_at(6),
        generation=11,
    )
    assert len(arrange._waveform_bindings) == 1
    assert arrange._waveform_retained_bytes <= values[-1].byte_size
    assert arrange._region_by_id[region.region_id] is region

    with pytest.raises(TypeError, match="non-negative integer"):
        arrange.begin_waveform_generation(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative integer"):
        arrange.begin_waveform_generation(-1)
    arrange.close()


def test_same_song_document_edits_preserve_horizontal_scroll(
    qapp: QApplication,
) -> None:
    document = _document()
    arrange = _shown_arrange(qapp, document)
    arrange.scroll_to_frame(240_000, center=False)
    qapp.processEvents()
    before = arrange.horizontal_scroll_frame
    assert before > 0

    arrange.set_document(document.update_track(document.tracks[0].track_id, pan=0.25))
    qapp.processEvents()
    assert arrange.horizontal_scroll_frame == before

    different_take = replace(
        document,
        session_id=_id("different-session"),
        revision=document.revision + 1,
    )
    arrange.set_document(different_take)
    qapp.processEvents()
    assert arrange.horizontal_scroll_frame == 0
    arrange.close()


def test_vertical_viewport_culls_offscreen_track_rows(qapp: QApplication) -> None:
    document = _document()
    extra_tracks = tuple(
        StudioTrack(track_id=_id(f"track:extra:{index}"), order=index + 2)
        for index in range(12)
    )
    many_tracks = replace(
        document,
        tracks=(*document.tracks, *extra_tracks),
        revision=document.revision + 1,
    )
    arrange = _shown_arrange(qapp, many_tracks)
    _render(arrange, qapp)
    assert arrange._canvas.verticalScrollBar().maximum() > 0
    assert 0 < arrange._canvas._last_paint_stats.tracks < len(many_tracks.tracks)

    arrange._canvas.verticalScrollBar().setValue(
        arrange._canvas.verticalScrollBar().maximum()
    )
    _render(arrange, qapp)
    assert 0 < arrange._canvas._last_paint_stats.tracks < len(many_tracks.tracks)
    assert arrange._canvas._last_paint_stats.regions == 0
    arrange.close()


def test_track_region_selection_and_integer_scrub_signal(qapp: QApplication) -> None:
    document = _document()
    arrange = _shown_arrange(qapp, document)
    arrange.set_zoom(480)
    arrange.scroll_to_frame(0, center=False)
    viewport = arrange._canvas.viewport()
    first_region = document.regions[0]
    region_events: list[str] = []
    track_events: list[str] = []
    scrub_events: list[int] = []
    arrange.region_selected.connect(region_events.append)
    arrange.track_selected.connect(track_events.append)
    arrange.scrub_requested.connect(scrub_events.append)

    region_x = round(arrange._canvas.x_for_frame(12_000))
    region_y = _main_row_y(arrange, first_region.track_id)
    QTest.mouseClick(
        viewport,
        Qt.MouseButton.LeftButton,
        pos=QPoint(region_x, region_y),
    )
    assert arrange.selected_track_id == first_region.track_id
    assert arrange.selected_region_id == first_region.region_id
    assert region_events == [first_region.region_id]
    assert track_events[-1] == first_region.track_id

    second_layout = arrange._canvas._layout_by_track[document.tracks[1].track_id]
    header_y = RULER_HEIGHT + second_layout.top + TRACK_HEIGHT // 2
    QTest.mouseClick(
        arrange._headers,
        Qt.MouseButton.LeftButton,
        pos=QPoint(30, header_y),
    )
    assert arrange.selected_track_id == document.tracks[1].track_id
    assert arrange.selected_region_id is None

    ruler_x = round(arrange._canvas.x_for_frame(18_000))
    QTest.mouseClick(
        viewport,
        Qt.MouseButton.LeftButton,
        pos=QPoint(ruler_x, RULER_HEIGHT // 2),
    )
    assert scrub_events
    assert isinstance(scrub_events[-1], int)
    assert abs(scrub_events[-1] - 18_000) <= 100
    arrange.close()


def test_move_and_edge_trim_gestures_emit_semantic_integer_frames(
    qapp: QApplication,
) -> None:
    document = _document()
    arrange = _shown_arrange(qapp, document)
    arrange.set_zoom(480)
    arrange.scroll_to_frame(0, center=False)
    viewport = arrange._canvas.viewport()
    region = document.regions[0]
    row_y = _main_row_y(arrange, region.track_id)
    moves: list[tuple[str, int]] = []
    trims: list[tuple[str, str, int]] = []
    arrange.region_move_requested.connect(
        lambda region_id, frame: moves.append((region_id, frame))
    )
    arrange.region_trim_requested.connect(
        lambda region_id, edge, frame: trims.append((region_id, edge, frame))
    )

    body_x = round(arrange._canvas.x_for_frame(12_000))
    QTest.mouseClick(
        viewport,
        Qt.MouseButton.LeftButton,
        pos=QPoint(body_x, row_y),
    )
    assert moves == []
    assert trims == []

    QTest.mousePress(
        viewport,
        Qt.MouseButton.LeftButton,
        pos=QPoint(body_x, row_y),
    )
    QTest.mouseMove(viewport, QPoint(body_x + 37, row_y))
    assert moves == []
    QTest.mouseRelease(
        viewport,
        Qt.MouseButton.LeftButton,
        pos=QPoint(body_x + 37, row_y),
    )
    assert moves
    assert moves[-1][0] == region.region_id
    assert isinstance(moves[-1][1], int)
    assert 3_500 <= moves[-1][1] <= 3_900

    left_x = round(arrange._canvas.x_for_frame(region.timeline_start_frame)) + 2
    target_x = round(arrange._canvas.x_for_frame(5_000))
    QTest.mousePress(
        viewport,
        Qt.MouseButton.LeftButton,
        pos=QPoint(left_x, row_y),
    )
    QTest.mouseMove(viewport, QPoint(target_x, row_y))
    QTest.mouseRelease(
        viewport,
        Qt.MouseButton.LeftButton,
        pos=QPoint(target_x, row_y),
    )
    assert trims
    assert trims[-1][0] == region.region_id
    assert trims[-1][1] == "start"
    assert isinstance(trims[-1][2], int)
    assert abs(trims[-1][2] - 4_800) <= 100
    assert document.revision == 1
    assert document.regions[0].timeline_start_frame == 0
    arrange.close()


def test_named_section_bar_drag_requests_whole_song_block_reorder(
    qapp: QApplication,
) -> None:
    document = _document()
    section = next(
        marker for marker in document.markers if marker.kind is MarkerKind.SECTION
    )
    arrange = _shown_arrange(qapp, document)
    arrange.set_zoom(240)
    arrange.scroll_to_frame(0, center=False)
    viewport = arrange._canvas.viewport()
    requests: list[tuple[str, int]] = []
    arrange.section_move_requested.connect(
        lambda marker_id, frame: requests.append((marker_id, frame))
    )

    grab_frame = section.start_frame + 2_000
    target_start = 12_000
    press = QPoint(round(arrange._canvas.x_for_frame(grab_frame)), 22)
    release = QPoint(
        round(arrange._canvas.x_for_frame(target_start + 2_000)),
        22,
    )
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=press)
    QTest.mouseMove(viewport, release)
    assert requests == []
    assert arrange._canvas._section_preview is not None
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=release)

    assert requests
    marker_id, frame = requests[-1]
    assert marker_id == section.marker_id
    assert isinstance(frame, int)
    assert abs(frame - target_start) <= 100
    assert arrange._canvas._section_preview is None
    assert document.markers[1] == section
    arrange.close()


def test_playhead_only_refreshes_accessible_state_at_section_boundaries(
    qapp: QApplication,
) -> None:
    """Playback advances the playhead ~16x/second.  Rebuilding the whole
    accessible description that often floods a screen reader with identical
    text, so only a named-section boundary crossing may refresh it."""

    document = _document()
    section = next(
        marker for marker in document.markers if marker.kind is MarkerKind.SECTION
    )
    arrange = _shown_arrange(qapp, document)
    refreshes = 0
    original = arrange._refresh_accessible_state

    def _counting_refresh() -> None:
        nonlocal refreshes
        refreshes += 1
        original()

    arrange._refresh_accessible_state = _counting_refresh

    # Outside every section: repeated movement stays silent.
    arrange.set_playhead(0)
    for frame in range(1_000, 9_000, 1_000):
        arrange.set_playhead(frame)
    assert refreshes == 0
    assert arrange._active_section_span is None

    # Entering the section announces once.
    arrange.set_playhead(section.start_frame)
    assert refreshes == 1
    assert arrange._active_section_span == (
        section.marker_id,
        section.start_frame,
        int(section.end_frame),
    )
    assert (
        'inside the "chorus" section'
        in arrange._canvas.viewport().accessibleDescription().lower()
    )

    # Ordinary movement inside the same section stays silent.
    for offset in range(1_000, 20_000, 1_000):
        arrange.set_playhead(section.start_frame + offset)
    assert refreshes == 1

    # Leaving the section announces exactly once more.
    arrange.set_playhead(int(section.end_frame))
    assert refreshes == 2
    assert arrange._active_section_span is None
    assert (
        'inside the "chorus" section'
        not in arrange._canvas.viewport().accessibleDescription().lower()
    )

    # An explicit seek back inside announces again.
    arrange.set_playhead(section.start_frame + 5_000)
    assert refreshes == 3

    arrange.close()


def test_moving_between_adjacent_sections_refreshes_each_boundary(
    qapp: QApplication,
) -> None:
    document = _document()
    chorus = next(
        marker for marker in document.markers if marker.kind is MarkerKind.SECTION
    )
    bridge = StudioMarker(
        marker_id=_id("marker:bridge"),
        start_frame=int(chorus.end_frame),
        end_frame=int(chorus.end_frame) + 48_000,
        label="Bridge",
        kind=MarkerKind.SECTION,
    )
    document = replace(document, markers=(*document.markers, bridge))
    arrange = _shown_arrange(qapp, document)
    refreshes = 0
    original = arrange._refresh_accessible_state

    def _counting_refresh() -> None:
        nonlocal refreshes
        refreshes += 1
        original()

    arrange._refresh_accessible_state = _counting_refresh

    arrange.set_playhead(chorus.start_frame + 1_000)
    assert refreshes == 1
    description = arrange._canvas.viewport().accessibleDescription().lower()
    assert 'inside the "chorus" section' in description

    # Crossing directly from one section into the next must announce the new
    # one rather than keeping the previous name.
    arrange.set_playhead(bridge.start_frame + 1_000)
    assert refreshes == 2
    description = arrange._canvas.viewport().accessibleDescription().lower()
    assert 'inside the "bridge" section' in description
    assert "chorus" not in description
    assert arrange._active_section_span == (
        bridge.marker_id,
        bridge.start_frame,
        int(bridge.end_frame),
    )

    arrange.close()


def test_replacing_the_document_invalidates_the_cached_section(
    qapp: QApplication,
) -> None:
    """A cached section identity must never outlive the document it came
    from, or Arrange would announce a section the take no longer has."""

    document = _document()
    section = next(
        marker for marker in document.markers if marker.kind is MarkerKind.SECTION
    )
    arrange = _shown_arrange(qapp, document)
    arrange.set_playhead(section.start_frame + 1_000)
    assert arrange._active_section_span is not None

    without_sections = replace(
        document,
        markers=tuple(
            marker
            for marker in document.markers
            if marker.kind is not MarkerKind.SECTION
        ),
    )
    arrange.set_document(without_sections)

    assert arrange._active_section_span is None
    assert (
        'inside the "chorus" section'
        not in arrange._canvas.viewport().accessibleDescription().lower()
    )

    # A later playhead move must not resurrect the removed section.
    arrange.set_playhead(section.start_frame + 2_000)
    assert arrange._active_section_span is None
    assert "chorus" not in arrange._canvas.viewport().accessibleDescription().lower()

    arrange.set_document(None)
    assert arrange._active_section_span is None
    assert (
        arrange._canvas.viewport().accessibleDescription()
        == "No Studio arrangement is open."
    )

    arrange.close()


def test_named_section_drag_paints_full_height_destination_preview(
    qapp: QApplication,
) -> None:
    """A section drag must show the destination across every track, not just
    the ruler strip, so the whole-song-block scope is visually unambiguous."""

    document = _document()
    section = next(
        marker for marker in document.markers if marker.kind is MarkerKind.SECTION
    )
    arrange = _shown_arrange(qapp, document)
    arrange.set_zoom(240)
    arrange.scroll_to_frame(0, center=False)
    viewport = arrange._canvas.viewport()

    grab_frame = section.start_frame + 2_000
    target_start = 12_000
    press = QPoint(round(arrange._canvas.x_for_frame(grab_frame)), 22)
    move_to = QPoint(round(arrange._canvas.x_for_frame(target_start + 2_000)), 22)
    body_y = RULER_HEIGHT + TRACK_HEIGHT // 2
    preview_x = round(arrange._canvas.x_for_frame(target_start) + 5)

    def _sample(x: int):
        image = QPixmap(viewport.size())
        viewport.render(image)
        qapp.processEvents()
        return image.toImage().pixelColor(x, body_y)

    before = _sample(preview_x)

    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=press)
    QTest.mouseMove(viewport, move_to)
    assert arrange._canvas._section_preview is not None

    during = _sample(preview_x)
    assert during != before

    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=move_to)
    assert arrange._canvas._section_preview is None
    after = _sample(preview_x)
    assert after == before

    arrange.close()


def test_take_lane_quick_swipe_and_header_audition_emit_semantic_requests(
    qapp: QApplication,
) -> None:
    document = _document()
    lane = document.take_lanes[0]
    arrange = _shown_arrange(qapp, document)
    arrange.set_zoom(480)
    arrange.scroll_to_frame(0, center=False)
    viewport = arrange._canvas.viewport()
    row_y = _lane_row_y(arrange, lane.lane_id)
    selections: list[tuple[str, int, int]] = []
    auditions: list[str] = []
    lane_selections: list[str] = []
    arrange.comp_range_requested.connect(
        lambda lane_id, start, end: selections.append((lane_id, start, end))
    )
    arrange.lane_audition_requested.connect(auditions.append)
    arrange.lane_selected.connect(lane_selections.append)

    start_x = round(arrange._canvas.x_for_frame(12_000))
    end_x = round(arrange._canvas.x_for_frame(30_000))
    QTest.mousePress(
        viewport,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier,
        QPoint(start_x, row_y),
    )
    QTest.mouseMove(viewport, QPoint(end_x, row_y))
    assert selections == []
    assert arrange._canvas._comp_preview is not None
    QTest.mouseRelease(
        viewport,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier,
        QPoint(end_x, row_y),
    )
    assert selections
    lane_id, start, end = selections[-1]
    assert lane_id == lane.lane_id
    assert isinstance(start, int) and isinstance(end, int)
    assert abs(start - 12_000) <= 100
    assert abs(end - 30_000) <= 100
    assert arrange._canvas._comp_preview is None

    header_y = row_y
    QTest.mouseDClick(
        arrange._headers,
        Qt.MouseButton.LeftButton,
        pos=QPoint(40, header_y),
    )
    assert lane_selections[-1] == lane.lane_id
    assert auditions[-1] == lane.lane_id
    assert arrange.selected_lane_id == lane.lane_id
    arrange.set_audition_lane(lane.lane_id)
    assert arrange.audition_lane_id == lane.lane_id
    arrange.set_audition_lane(None)
    assert arrange.audition_lane_id is None
    arrange.close()


@pytest.mark.parametrize(
    ("mode", "raw", "expected"),
    [
        (SnapMode.OFF, 3_700, 3_700),
        (SnapMode.TIME, 3_700, 4_800),
        (SnapMode.MARKERS, 23_200, 24_000),
    ],
)
def test_snap_modes_use_integer_time_or_nearby_marker_boundaries(
    qapp: QApplication,
    mode: SnapMode,
    raw: int,
    expected: int,
) -> None:
    arrange = _shown_arrange(qapp, _document(mode))
    arrange.set_zoom(480)
    assert arrange._canvas._snap_frame(raw) == expected
    arrange.close()


def test_operation_undo_redo_and_snap_shortcuts_emit_requests(
    qapp: QApplication,
) -> None:
    document = _document()
    arrange = _shown_arrange(qapp, document)
    arrange.set_zoom(480)
    arrange.scroll_to_frame(0, center=False)
    viewport = arrange._canvas.viewport()
    viewport.setFocus()
    region = document.regions[0]
    region_x = round(arrange._canvas.x_for_frame(12_000))
    region_y = _main_row_y(arrange, region.track_id)
    QTest.mouseClick(
        viewport,
        Qt.MouseButton.LeftButton,
        pos=QPoint(region_x, region_y),
    )
    arrange.set_playhead(24_000)

    undo: list[bool] = []
    redo: list[bool] = []
    splits: list[tuple[str, int]] = []
    duplicates: list[tuple[str, int]] = []
    enabled: list[tuple[str, bool]] = []
    deleted: list[str] = []
    snaps: list[str] = []
    arrange.undo_requested.connect(lambda: undo.append(True))
    arrange.redo_requested.connect(lambda: redo.append(True))
    arrange.split_region_requested.connect(
        lambda region_id, frame: splits.append((region_id, frame))
    )
    arrange.duplicate_region_requested.connect(
        lambda region_id, frame: duplicates.append((region_id, frame))
    )
    arrange.region_enabled_requested.connect(
        lambda region_id, value: enabled.append((region_id, value))
    )
    arrange.delete_region_requested.connect(deleted.append)
    arrange.snap_mode_requested.connect(snaps.append)

    QTest.keyClick(viewport, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(
        viewport,
        Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    QTest.keyClick(viewport, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(viewport, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(viewport, Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(viewport, Qt.Key.Key_Delete)
    QTest.keyClick(viewport, Qt.Key.Key_2, Qt.KeyboardModifier.AltModifier)
    qapp.processEvents()

    assert undo == [True]
    assert redo == [True]
    assert splits == [(region.region_id, 24_000)]
    assert duplicates == [(region.region_id, region.timeline_end_frame)]
    assert enabled == [(region.region_id, False)]
    assert deleted == [region.region_id]
    assert snaps == [SnapMode.TIME.value]
    arrange.close()


def test_keyboard_selection_nudge_trim_comp_audition_and_section_reorder(
    qapp: QApplication,
) -> None:
    document = _document()
    verse = StudioMarker(
        marker_id=_id("section:verse"),
        start_frame=0,
        end_frame=36_000,
        label="Verse",
        kind=MarkerKind.SECTION,
    )
    document = replace(
        document,
        markers=(verse, *document.markers[1:]),
        revision=document.revision + 1,
    )
    chorus = document.markers[1]
    lane = document.take_lanes[0]
    alternate = document.regions[2]
    arrange = _shown_arrange(qapp, document)
    viewport = arrange._canvas.viewport()
    viewport.setFocus()

    moves: list[tuple[str, int]] = []
    trims: list[tuple[str, str, int]] = []
    comps: list[tuple[str, int, int]] = []
    auditions: list[str] = []
    sections: list[tuple[str, int]] = []
    arrange.region_move_requested.connect(
        lambda region_id, frame: moves.append((region_id, frame))
    )
    arrange.region_trim_requested.connect(
        lambda region_id, edge, frame: trims.append((region_id, edge, frame))
    )
    arrange.comp_range_requested.connect(
        lambda lane_id, start, end: comps.append((lane_id, start, end))
    )
    arrange.lane_audition_requested.connect(auditions.append)
    arrange.section_move_requested.connect(
        lambda marker_id, frame: sections.append((marker_id, frame))
    )

    # No mouse prerequisite: arrows enter the first row and select its first region.
    QTest.keyClick(viewport, Qt.Key.Key_Down)
    QTest.keyClick(viewport, Qt.Key.Key_Right)
    qapp.processEvents()
    first = document.regions[0]
    assert arrange.selected_track_id == first.track_id
    assert arrange.selected_region_id == first.region_id

    QTest.keyClick(viewport, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    arrange.set_playhead(24_000)
    QTest.keyClick(
        viewport, Qt.Key.Key_BracketLeft, Qt.KeyboardModifier.ControlModifier
    )
    QTest.keyClick(
        viewport,
        Qt.Key.Key_BracketRight,
        Qt.KeyboardModifier.ControlModifier,
    )
    qapp.processEvents()
    assert moves == [(first.region_id, first.timeline_start_frame + 1)]
    assert trims == [
        (first.region_id, "start", 24_000),
        (first.region_id, "end", 24_000),
    ]

    # Down enters the alternate take lane; Right selects its first region.
    QTest.keyClick(viewport, Qt.Key.Key_Down)
    QTest.keyClick(viewport, Qt.Key.Key_Right)
    QTest.keyClick(
        viewport,
        Qt.Key.Key_C,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
    )
    QTest.keyClick(
        viewport,
        Qt.Key.Key_A,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
    )
    qapp.processEvents()
    assert arrange.selected_lane_id == lane.lane_id
    assert arrange.selected_region_id == alternate.region_id
    assert comps == [
        (
            lane.lane_id,
            alternate.timeline_start_frame,
            alternate.timeline_end_frame,
        )
    ]
    assert auditions == [lane.lane_id]
    assert "selected region" in viewport.accessibleDescription().lower()
    assert "alternate take lane" in viewport.accessibleDescription().lower()

    arrange.set_playhead(48_000)
    QTest.keyClick(
        viewport,
        Qt.Key.Key_Left,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
    )
    arrange.set_playhead(12_000)
    QTest.keyClick(
        viewport,
        Qt.Key.Key_Right,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
    )
    qapp.processEvents()
    assert sections == [
        (chorus.marker_id, verse.start_frame),
        (verse.marker_id, chorus.start_frame),
    ]
    arrange.close()


def test_document_replacement_reconciles_selection_and_validates_inputs(
    qapp: QApplication,
) -> None:
    document = _document()
    arrange = _shown_arrange(qapp, document)
    arrange.set_selection(
        track_id=document.tracks[0].track_id,
        region_id=document.regions[0].region_id,
    )
    assert arrange.selected_region_id == document.regions[0].region_id
    arrange._user_select_region(document.regions[0])
    deleted = replace(
        document.regions[0],
        enabled=False,
        deleted=True,
    )
    replacement = replace(
        document,
        regions=(deleted, *document.regions[1:]),
        crossfades=(replace(document.crossfades[0], deleted=True),),
        revision=document.revision + 1,
    )
    arrange.set_document(replacement)
    assert arrange.selected_region_id is None
    assert arrange.selected_track_id == document.tracks[0].track_id

    with pytest.raises(TypeError, match="StudioDocument"):
        arrange.set_document(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="integer frame"):
        arrange.set_playhead(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="mapping"):
        arrange.set_track_names([])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="160"):
        arrange.set_track_names({document.tracks[0].track_id: "x" * 161})
    with pytest.raises(TypeError, match="true or false"):
        arrange.scroll_to_frame(0, center=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not part"):
        arrange.set_selection(track_id=_id("missing-track"))
    arrange.close()


def test_scroll_and_scrub_preserve_frames_beyond_qt_int_range(
    qapp: QApplication,
) -> None:
    document = _document()
    distant_frame = 5_000_000_000
    far_region = replace(
        document.regions[-1],
        timeline_start_frame=distant_frame,
        mapping_timeline_start_frame=distant_frame,
    )
    far_marker = replace(document.markers[-1], start_frame=distant_frame)
    long_document = replace(
        document,
        regions=(*document.regions[:-1], far_region),
        markers=(*document.markers[:-1], far_marker),
        revision=document.revision + 1,
    )
    arrange = _shown_arrange(qapp, long_document)
    arrange.set_zoom(480)
    arrange.scroll_to_frame(distant_frame)
    start, end = arrange.visible_frame_range()
    assert start <= distant_frame <= end

    scrubbed: list[int] = []
    arrange.scrub_requested.connect(scrubbed.append)
    x = round(arrange._canvas.x_for_frame(distant_frame))
    QTest.mouseClick(
        arrange._canvas.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(x, RULER_HEIGHT // 2),
    )
    assert scrubbed
    assert isinstance(scrubbed[-1], int)
    assert scrubbed[-1] > 2**31
    assert abs(scrubbed[-1] - distant_frame) <= 8
    arrange.close()
