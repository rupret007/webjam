"""Focused non-destructive editing controls for the Studio Arrange surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol
from uuid import uuid4

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QWidget,
)

from core.studio_project import (
    FadeCurve,
    MarkerKind,
    StudioCrossfade,
    StudioCycleRange,
    StudioDocument,
    StudioMarker,
    StudioProjectError,
    StudioRegion,
)
from webjam_qt.theme.tokens import Space


StudioEdit = Callable[[StudioDocument], StudioDocument]
StudioCrossfadeTarget = tuple[StudioRegion, StudioRegion, StudioCrossfade | None]


class StudioEditApplier(Protocol):
    """RecordingStudio-owned history and playback boundary."""

    def __call__(
        self,
        label: str,
        edit: StudioEdit,
        *,
        reload_audio: bool,
    ) -> bool: ...


@dataclass(frozen=True)
class StudioEditingContext:
    """Current immutable facts needed to derive toolbar edit requests."""

    document: StudioDocument | None
    selected_region_id: str | None
    playhead_frame: int
    studio_visible: bool


class StudioEditingToolbar(QWidget):
    """Named markers, cycle, fades, and crossfades for Arrange.

    This widget derives edits from an immutable document snapshot. It never
    stores Studio history, writes a sidecar, or reloads audio: those effects
    remain behind ``apply_edit`` in :class:`RecordingStudio`.
    """

    hint_requested = Signal(str)

    def __init__(
        self,
        *,
        context_provider: Callable[[], StudioEditingContext],
        apply_edit: StudioEditApplier,
        name_prompt: Callable[[str, str], str | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context_provider = context_provider
        self._apply_edit = apply_edit
        self._name_prompt = name_prompt or self.prompt_name
        self.setObjectName("StudioArrangeToolbar")
        self.setAccessibleName("Studio arrangement controls")

        actions = QHBoxLayout(self)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(Space.SM)
        label = QLabel("ARRANGE")
        label.setObjectName("StudioSectionTitle")
        actions.addWidget(label)

        self.add_marker_button = QPushButton("＋ Marker")
        self.add_marker_button.setObjectName("GhostButton")
        self.add_marker_button.setAccessibleName("Add named marker at playhead")
        self.add_marker_button.setToolTip("Add a named marker at the playhead.")
        self.add_marker_button.clicked.connect(lambda _checked=False: self.add_marker())
        actions.addWidget(self.add_marker_button)

        self.add_section_button = QPushButton("＋ Section")
        self.add_section_button.setObjectName("GhostButton")
        self.add_section_button.setAccessibleName("Name selected region as a section")
        self.add_section_button.setToolTip(
            "Add a named section spanning the selected region."
        )
        self.add_section_button.clicked.connect(
            lambda _checked=False: self.add_section()
        )
        actions.addWidget(self.add_section_button)

        self.cycle_region_button = QPushButton("Cycle Region")
        self.cycle_region_button.setObjectName("GhostButton")
        self.cycle_region_button.setAccessibleName("Cycle selected region")
        self.cycle_region_button.setToolTip(
            "Set or clear the cycle range using the selected region."
        )
        self.cycle_region_button.clicked.connect(self.toggle_cycle)
        actions.addWidget(self.cycle_region_button)

        self.region_fades_button = QPushButton("5 ms Fades")
        self.region_fades_button.setObjectName("GhostButton")
        self.region_fades_button.setAccessibleName(
            "Add equal-power fades to selected region"
        )
        self.region_fades_button.setToolTip(
            "Add or remove short click-safe equal-power fades."
        )
        self.region_fades_button.clicked.connect(self.toggle_region_fades)
        actions.addWidget(self.region_fades_button)

        self.crossfade_button = QPushButton("Crossfade")
        self.crossfade_button.setObjectName("GhostButton")
        self.crossfade_button.setAccessibleName(
            "Crossfade selected overlapping regions"
        )
        self.crossfade_button.setToolTip(
            "Add or remove an equal-power crossfade across the nearest overlap."
        )
        self.crossfade_button.clicked.connect(self.toggle_crossfade)
        actions.addWidget(self.crossfade_button)
        actions.addStretch(1)
        self.setVisible(False)

    def _context(self) -> StudioEditingContext:
        return self._context_provider()

    def selected_region(self) -> StudioRegion | None:
        context = self._context()
        if context.document is None or not context.selected_region_id:
            return None
        try:
            region = context.document.region_for(context.selected_region_id)
        except StudioProjectError:
            return None
        return None if region.deleted else region

    def next_label(self, kind: MarkerKind) -> str:
        document = self._context().document
        stem = "Marker" if kind is MarkerKind.MARKER else "Section"
        count = (
            sum(
                1
                for marker in document.markers
                if not marker.deleted and marker.kind is kind
            )
            if document is not None
            else 0
        )
        return f"{stem} {count + 1}"

    def prompt_name(self, title: str, default: str) -> str | None:
        value, accepted = QInputDialog.getText(
            self,
            title,
            "Name:",
            text=default,
        )
        if not accepted:
            return None
        cleaned = value.strip()
        return cleaned or None

    def add_marker(self, label: str | None = None) -> None:
        context = self._context()
        if context.document is None:
            return
        name = label
        if name is None:
            name = self._name_prompt(
                "Add marker",
                self.next_label(MarkerKind.MARKER),
            )
        if not isinstance(name, str) or not name.strip():
            return
        marker = StudioMarker(
            marker_id=str(uuid4()),
            start_frame=context.playhead_frame,
            label=name.strip(),
            kind=MarkerKind.MARKER,
        )
        if self._apply_edit(
            "Add marker",
            lambda document: document.upsert_marker(marker),
            reload_audio=False,
        ):
            self.hint_requested.emit(
                f"Marker “{marker.label}” added at the playhead. "
                "The recording is unchanged."
            )

    def add_section(self, label: str | None = None) -> None:
        region = self.selected_region()
        if region is None:
            return
        name = label
        if name is None:
            name = self._name_prompt(
                "Add section",
                self.next_label(MarkerKind.SECTION),
            )
        if not isinstance(name, str) or not name.strip():
            return
        section = StudioMarker(
            marker_id=str(uuid4()),
            start_frame=region.timeline_start_frame,
            end_frame=region.timeline_end_frame,
            label=name.strip(),
            kind=MarkerKind.SECTION,
        )
        if self._apply_edit(
            "Add section",
            lambda document: document.upsert_marker(section),
            reload_audio=False,
        ):
            self.hint_requested.emit(
                f"Section “{section.label}” now spans the selected region."
            )

    def toggle_cycle(self) -> None:
        context = self._context()
        region = self.selected_region()
        document = context.document
        if region is None or document is None:
            return
        current = document.cycle_range
        exact = (
            current is not None
            and current.start_frame == region.timeline_start_frame
            and current.end_frame == region.timeline_end_frame
        )
        cycle = (
            None
            if exact
            else StudioCycleRange(
                region.timeline_start_frame,
                region.timeline_end_frame,
            )
        )
        if self._apply_edit(
            "Clear cycle range" if exact else "Set cycle range",
            lambda value: value.set_cycle_range(cycle),
            reload_audio=True,
        ):
            self.hint_requested.emit(
                "Cycle range cleared."
                if exact
                else "Cycle range set to the selected region."
            )

    def toggle_region_fades(self) -> None:
        context = self._context()
        region = self.selected_region()
        document = context.document
        if region is None or document is None:
            return
        removing = bool(region.fade_in_frames or region.fade_out_frames)
        fade_frames = (
            0
            if removing
            else min(
                max(1, round(document.project_sample_rate * 0.005)),
                region.timeline_frame_count // 2,
            )
        )
        if self._apply_edit(
            "Remove region fades" if removing else "Add region fades",
            lambda value: value.set_region_fades(
                region.region_id,
                fade_in_frames=fade_frames,
                fade_out_frames=fade_frames,
                fade_in_curve=FadeCurve.EQUAL_POWER,
                fade_out_curve=FadeCurve.EQUAL_POWER,
            ),
            reload_audio=True,
        ):
            self.hint_requested.emit(
                "Region fades removed."
                if removing
                else "Added short equal-power fades without changing the source audio."
            )

    def selected_crossfade_target(self) -> StudioCrossfadeTarget | None:
        selected = self.selected_region()
        document = self._context().document
        if selected is None or document is None or not selected.enabled:
            return None
        lane_by_region = {
            region_id: lane.lane_id
            for lane in document.take_lanes
            if not lane.deleted
            for region_id in lane.region_ids
        }
        selected_lane = lane_by_region.get(selected.region_id)
        candidates: list[tuple[int, int, str, StudioRegion]] = []
        for region in document.regions:
            if (
                region.region_id == selected.region_id
                or region.deleted
                or not region.enabled
                or region.track_id != selected.track_id
                or lane_by_region.get(region.region_id) != selected_lane
            ):
                continue
            overlap_start = max(
                selected.timeline_start_frame,
                region.timeline_start_frame,
            )
            overlap_end = min(
                selected.timeline_end_frame,
                region.timeline_end_frame,
            )
            if overlap_end <= overlap_start:
                continue
            candidates.append(
                (
                    -(overlap_end - overlap_start),
                    abs(region.timeline_start_frame - selected.timeline_start_frame),
                    region.region_id,
                    region,
                )
            )
        if not candidates:
            return None
        other = min(candidates)[3]
        pair = {selected.region_id, other.region_id}
        existing = next(
            (
                crossfade
                for crossfade in document.crossfades
                if not crossfade.deleted
                and {crossfade.left_region_id, crossfade.right_region_id} == pair
            ),
            None,
        )
        return selected, other, existing

    def toggle_crossfade(self) -> None:
        target = self.selected_crossfade_target()
        if target is None:
            return
        selected, other, existing = target
        if existing is not None:
            if self._apply_edit(
                "Remove crossfade",
                lambda document: document.remove_crossfade(existing.crossfade_id),
                reload_audio=True,
            ):
                self.hint_requested.emit(
                    "Crossfade removed. Both regions remain intact."
                )
            return
        overlap_start = max(
            selected.timeline_start_frame,
            other.timeline_start_frame,
        )
        overlap_end = min(
            selected.timeline_end_frame,
            other.timeline_end_frame,
        )
        left, right = sorted(
            (selected, other),
            key=lambda region: (region.timeline_start_frame, region.region_id),
        )
        if self._apply_edit(
            "Add crossfade",
            lambda document: document.set_crossfade(
                left.region_id,
                right.region_id,
                start_frame=overlap_start,
                frame_count=overlap_end - overlap_start,
                curve=FadeCurve.EQUAL_POWER,
            ),
            reload_audio=True,
        ):
            self.hint_requested.emit(
                "Equal-power crossfade added across the overlap. Sources are unchanged."
            )

    def refresh(self) -> None:
        context = self._context()
        visible = context.studio_visible
        self.setVisible(visible)
        region = self.selected_region() if visible else None
        self.add_marker_button.setEnabled(visible)
        for control in (
            self.add_section_button,
            self.cycle_region_button,
            self.region_fades_button,
        ):
            control.setEnabled(region is not None)
        if region is None or context.document is None:
            self.cycle_region_button.setText("Cycle Region")
            self.region_fades_button.setText("5 ms Fades")
        else:
            cycle = context.document.cycle_range
            self.cycle_region_button.setText(
                "Clear Cycle"
                if cycle is not None
                and cycle.start_frame == region.timeline_start_frame
                and cycle.end_frame == region.timeline_end_frame
                else "Cycle Region"
            )
            self.region_fades_button.setText(
                "Remove Fades"
                if region.fade_in_frames or region.fade_out_frames
                else "5 ms Fades"
            )
        crossfade = self.selected_crossfade_target() if visible else None
        self.crossfade_button.setEnabled(crossfade is not None)
        self.crossfade_button.setText(
            "Remove Crossfade"
            if crossfade is not None and crossfade[2] is not None
            else "Crossfade"
        )


__all__ = [
    "StudioCrossfadeTarget",
    "StudioEditingContext",
    "StudioEditingToolbar",
]
