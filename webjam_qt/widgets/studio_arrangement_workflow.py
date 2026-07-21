"""Arrangement-document workflow for the integrated Studio widget.

This module owns Arrange signal binding, immutable edit/history coordination,
take-lane comping, mixer-state synchronization, and durable Studio sidecar
persistence. RecordingStudio retains Qt layout, live capture, take switching,
waveform scheduling, export, and playback lifecycle ownership; this mixin
reaches those concerns only through the host's existing private gateways.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QMenu

from core.studio_comping import (
    StudioCompingError,
    add_take_lane,
    audition_lane_document,
    compatible_source_tracks,
    remove_take_lane,
    select_lane_range,
)
from core.studio_controller import StudioControllerError
from core.studio_project import (
    MarkerKind,
    SnapMode,
    StudioDocument,
    StudioMaster,
    StudioProjectError,
    StudioRegion,
)
from core.studio_sections import reorder_section
from core.studio_source_catalog import (
    StudioSourceCatalog,
    StudioSourceCatalogError,
)
from core.take_library import TakeInfo
from core.take_player import PlaybackError
from core.take_project import TakeProject, TakeProjectError, load_take_project
from webjam_qt.widgets.studio_arrange import _format_frame_time
from webjam_qt.widgets.studio_editing import (
    StudioCrossfadeTarget,
    StudioEditingContext,
)
from webjam_qt.widgets.studio_messages import arrange_edit_failure_message
from webjam_qt.widgets.studio_review import (
    _fmt_db,
    _fmt_time,
    _timeline_gaps_for_track,
)

LOGGER = logging.getLogger("webjam.qt.recording_studio")


def _selectable_track_export_track_ids(take: TakeInfo) -> tuple[str, ...]:
    """Return stable IDs only when Studio can safely narrow a project export.

    Schema-v1 takes have no durable per-track IDs, so their export remains the
    existing all-tracks behavior.  A partial ID set would be misleading: the
    export boundary would not know how to represent every visible lane.
    """
    identifiers = tuple(
        str(getattr(track, "track_id", "") or "").strip() for track in take.tracks
    )
    if not identifiers or any(not identifier for identifier in identifiers):
        return ()
    if len(set(identifiers)) != len(identifiers):
        return ()
    return identifiers


class StudioArrangementWorkflowMixin:
    """Own non-destructive arrangement editing and sidecar persistence."""

    def _connect_studio_arrange(self) -> None:
        """Bind semantic Arrange requests to the controller's immutable history."""

        arrange = self._studio_arrange
        arrange.scrub_requested.connect(self._seek_from_arrange)
        arrange.track_selected.connect(self._select_arrange_track)
        arrange.region_selected.connect(self._select_arrange_region)
        arrange.region_move_requested.connect(self._move_arrange_region)
        arrange.section_move_requested.connect(self._move_arrange_section)
        arrange.region_trim_requested.connect(self._trim_arrange_region)
        arrange.split_region_requested.connect(self._split_arrange_region)
        arrange.duplicate_region_requested.connect(self._duplicate_arrange_region)
        arrange.region_enabled_requested.connect(self._enable_arrange_region)
        arrange.delete_region_requested.connect(self._delete_arrange_region)
        arrange.lane_selected.connect(self._select_take_lane)
        arrange.lane_audition_requested.connect(self._toggle_take_lane_audition)
        arrange.comp_range_requested.connect(self._select_comp_range)
        arrange.viewport_changed.connect(self._queue_studio_waveforms)
        arrange.snap_mode_requested.connect(self._set_arrange_snap_mode)
        arrange.undo_requested.connect(self._undo_arrange_edit)
        arrange.redo_requested.connect(self._redo_arrange_edit)

    def _set_master_controls(self, master: StudioMaster) -> None:
        for control in (self._master_gain, self._master_limiter):
            control.blockSignals(True)
        self._master_gain.setValue(round(master.gain * 100))
        self._master_limiter.setChecked(master.limiter_enabled)
        for control in (self._master_gain, self._master_limiter):
            control.blockSignals(False)
        self._master_gain_value.setText(_fmt_db(master.gain))

    def _on_master_gain_changed(self, value: int) -> None:
        gain = value / 100.0
        self._master_gain_value.setText(_fmt_db(gain))
        self._player.set_master_gain(gain)
        if self._studio_state is None:
            return
        self._perform_arrange_edit(
            "Adjust master gain",
            lambda document: document.set_master(
                StudioMaster(
                    gain=gain,
                    limiter_enabled=document.master.limiter_enabled,
                )
            ),
            reload_audio=False,
            merge_key=self._master_gain_merge_key,
        )

    def _next_mix_merge_key(self, stem: str) -> str:
        self._studio_mix_gesture_serial += 1
        return f"mix:{self._studio_mix_gesture_serial}:{stem}"

    def _begin_master_gain_gesture(self) -> None:
        self._master_gain_merge_key = self._next_mix_merge_key("master:gain")

    def _end_master_gain_gesture(self) -> None:
        self._master_gain_merge_key = None

    def _begin_track_mix_gesture(self, channel_id: int, field_name: str) -> None:
        key = (int(channel_id), str(field_name))
        self._studio_mix_merge_keys[key] = self._next_mix_merge_key(
            f"track:{key[0]}:{key[1]}"
        )

    def _end_track_mix_gesture(self, channel_id: int, field_name: str) -> None:
        self._studio_mix_merge_keys.pop((int(channel_id), str(field_name)), None)

    def _on_master_limiter_changed(self, enabled: bool) -> None:
        self._player.set_limiter_enabled(enabled)
        if self._studio_state is None:
            return
        self._perform_arrange_edit(
            "Toggle master limiter",
            lambda document: document.set_master(
                StudioMaster(
                    gain=document.master.gain,
                    limiter_enabled=enabled,
                )
            ),
            reload_audio=False,
        )

    def _arrange_editing_context(self) -> StudioEditingContext:
        arrange = getattr(self, "_studio_arrange", None)
        return StudioEditingContext(
            document=self._studio_state,
            selected_region_id=(
                arrange.selected_region_id if arrange is not None else None
            ),
            playhead_frame=(arrange.playhead_frame if arrange is not None else 0),
            studio_visible=self._studio_state is not None and not self._viewing_live,
        )

    # Compatibility delegates keep the existing RecordingStudio editing
    # surface while the focused component owns toolbar behavior.
    def _selected_arrange_region(self) -> StudioRegion | None:
        return self._arrange_toolbar.selected_region()

    def _next_arrangement_label(self, kind: MarkerKind) -> str:
        return self._arrange_toolbar.next_label(kind)

    def _prompt_arrangement_name(self, title: str, default: str) -> str | None:
        return self._arrange_toolbar.prompt_name(title, default)

    def _add_arrange_marker(self, label: str | None = None) -> None:
        self._arrange_toolbar.add_marker(label)

    def _add_arrange_section(self, label: str | None = None) -> None:
        self._arrange_toolbar.add_section(label)

    def _toggle_selected_cycle(self) -> None:
        self._arrange_toolbar.toggle_cycle()

    def _toggle_selected_region_fades(self) -> None:
        self._arrange_toolbar.toggle_region_fades()

    def _selected_crossfade_target(self) -> StudioCrossfadeTarget | None:
        return self._arrange_toolbar.selected_crossfade_target()

    def _toggle_selected_crossfade(self) -> None:
        self._arrange_toolbar.toggle_crossfade()

    def _channel_for_track_id(self, track_id: str) -> int | None:
        for channel_id, track in self._track_info_by_channel.items():
            if self._state_track_id(track) == track_id:
                return channel_id
        return None

    def _select_arrange_track(self, track_id: str) -> None:
        try:
            self._studio_controller.select_track(track_id)
        except (StudioControllerError, StudioProjectError):
            return
        channel_id = self._channel_for_track_id(track_id)
        if channel_id is not None:
            self._select_track(channel_id, sync_controller=False)

    def _select_arrange_region(self, region_id: str) -> None:
        try:
            self._studio_controller.select_region(region_id)
        except (StudioControllerError, StudioProjectError):
            return
        track_id = self._studio_controller.selected_track_id
        channel_id = self._channel_for_track_id(track_id or "")
        if channel_id is not None:
            self._select_track(channel_id, sync_controller=False)

    def _source_catalog_for_document(
        self,
        document: StudioDocument,
        project: TakeProject,
        take_path: Path,
    ) -> StudioSourceCatalog | None:
        """Load repeated takes retained by the non-deleted region inventory."""

        source_take_ids = sorted(
            {
                item.source_take_id
                for item in document.regions
                if not item.deleted and item.source_take_id != project.take_id
            }
        )
        roots: list[Path] = []
        for source_take_id in source_take_ids:
            matches = [
                item
                for item in self._takes
                if str(getattr(item, "take_id", "") or "") == source_take_id
                and str(getattr(item, "session_id", "") or "") == project.session_id
                and int(getattr(item, "project_samplerate", 0) or 0)
                == project.project_sample_rate
            ]
            if len(matches) != 1:
                raise StudioSourceCatalogError(
                    "A referenced repeated take is missing or ambiguous in the Takes library."
                )
            roots.append(matches[0].path)
        return StudioSourceCatalog.load(
            project,
            take_path,
            additional_take_roots=tuple(roots),
        )

    def _refresh_studio_source_catalog(self) -> bool:
        """Match the trusted catalog to the current document's source inventory."""

        document = self._studio_state
        project = self._studio_project
        take_path = self._studio_state_take_path
        if document is None or project is None or take_path is None:
            self._studio_source_catalog = None
            return False
        desired_take_ids = (
            project.take_id,
            *sorted(
                {
                    region.source_take_id
                    for region in document.regions
                    if not region.deleted and region.source_take_id != project.take_id
                }
            ),
        )
        current = self._studio_source_catalog
        if current is not None and current.take_ids == desired_take_ids:
            return True
        try:
            self._studio_source_catalog = self._source_catalog_for_document(
                document,
                project,
                take_path,
            )
        except StudioSourceCatalogError as exc:
            LOGGER.warning("Could not refresh Studio source inventory: %s", exc)
            self._studio_source_catalog = None
            self._cancel_studio_waveforms(clear=True)
            self._studio_state_error = (
                "Studio couldn't verify every take used by this arrangement. "
                "The recordings are unchanged."
            )
            self._hint.setText(self._studio_state_error)
            return False
        return True

    def _available_comp_sources(
        self,
    ) -> tuple[tuple[Path, TakeProject, object], ...]:
        """Return trusted, same-musician repeated tracks for the current selection."""

        document = self._studio_state
        primary = self._studio_project
        current = self._studio_state_take_path
        if document is None or primary is None or current is None:
            return ()
        destination_id = (
            self._studio_controller.selected_track_id
            or self._studio_arrange.selected_track_id
        )
        if not destination_id:
            return ()
        destination = next(
            (item for item in primary.tracks if item.track_id == destination_id),
            None,
        )
        if destination is None:
            return ()
        existing = {
            (item.source_take_id, item.source_track_id)
            for item in document.take_lanes
            if item.track_id == destination_id and not item.deleted
        }
        values: list[tuple[Path, TakeProject, object]] = []
        for take in self._takes:
            try:
                path = take.path.expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if path == current:
                continue
            if (
                str(getattr(take, "session_id", "") or "") != primary.session_id
                or int(getattr(take, "project_samplerate", 0) or 0)
                != primary.project_sample_rate
            ):
                continue
            try:
                source_project = load_take_project(path)
                matches = compatible_source_tracks(destination, source_project)
            except (TakeProjectError, StudioCompingError):
                continue
            for source_track in matches:
                if (source_project.take_id, source_track.track_id) in existing:
                    continue
                values.append((path, source_project, source_track))
        return tuple(
            sorted(
                values,
                key=lambda item: (
                    item[1].take_name.casefold(),
                    item[1].take_id,
                    item[2].order,
                    item[2].track_id,
                ),
            )
        )

    def _show_add_take_lane_menu(self) -> None:
        sources = self._available_comp_sources()
        if not sources:
            self._hint.setText(
                "No other complete take in this session has a safe match for the "
                "selected musician."
            )
            return
        menu = QMenu(self)
        menu.setAccessibleName("Matching repeated takes")
        for path, project, source_track in sources:
            label = f"{project.take_name} — {source_track.name}"
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, source_path=path, track_id=source_track.track_id: (
                    self._add_take_lane_from_source(source_path, track_id)
                )
            )
        menu.exec(
            self._add_take_lane_btn.mapToGlobal(
                self._add_take_lane_btn.rect().bottomLeft()
            )
        )

    def _add_take_lane_from_source(
        self,
        source_path: Path,
        source_track_id: str,
    ) -> None:
        primary = self._studio_project
        take_path = self._studio_state_take_path
        destination_id = self._studio_controller.selected_track_id
        if primary is None or take_path is None or not destination_id:
            return
        try:
            source_project = load_take_project(source_path)
            before = self._studio_controller.document
            after = add_take_lane(
                before,
                primary,
                source_project,
                destination_track_id=destination_id,
                source_track_id=source_track_id,
            )
            catalog = self._source_catalog_for_document(after, primary, take_path)
            updated = self._studio_controller.perform(
                "Add take lane",
                lambda current: after if current == before else current,
            )
            if updated is before:
                return
        except (
            StudioCompingError,
            StudioControllerError,
            StudioSourceCatalogError,
            TakeProjectError,
        ) as exc:
            LOGGER.warning("Could not add Studio take lane: %s", exc)
            self._hint.setText(
                "Studio couldn't add that take safely. The recordings are unchanged."
            )
            return
        self._studio_source_catalog = catalog
        self._sync_studio_controller_snapshot()
        self._reload_arranged_playback()
        self._refresh_comp_controls()
        self._hint.setText(
            "Take lane added. Option/Alt-drag across it to select a comp range; "
            "double-click the lane name to audition it."
        )

    def _select_take_lane(self, _lane_id: str) -> None:
        self._refresh_comp_controls()

    def _select_comp_range(
        self,
        lane_id: str,
        start_frame: object,
        end_frame: object,
    ) -> None:
        if (
            isinstance(start_frame, bool)
            or not isinstance(start_frame, int)
            or isinstance(end_frame, bool)
            or not isinstance(end_frame, int)
        ):
            return
        changed = self._perform_arrange_edit(
            "Select comp range",
            lambda document: select_lane_range(
                document,
                lane_id,
                start_frame,
                end_frame,
            ),
            reload_audio=True,
        )
        if changed:
            self._hint.setText(
                "Comp range selected with equal-power boundaries. Every original "
                "take remains unchanged."
            )

    def _toggle_selected_take_lane_audition(self) -> None:
        lane_id = self._studio_arrange.selected_lane_id
        if lane_id:
            self._toggle_take_lane_audition(lane_id)

    def _toggle_take_lane_audition(self, lane_id: str) -> None:
        project = self._studio_project
        document = self._studio_state
        take_path = self._studio_state_take_path
        if project is None or document is None or take_path is None:
            return
        if self._studio_audition_lane_id == lane_id:
            self._reload_arranged_playback()
            self._hint.setText("Returned to the saved comp.")
            return
        try:
            audition = audition_lane_document(document, lane_id)
            position_frame = self._player.position_frame
            resume_playback = self._player.is_playing or (
                self._playback_preparing and self._playback_prepare_autoplay
            )
            self._cancel_playback_preparation(restore_controls=False)
            self._player.load_studio(
                project,
                audition,
                take_path,
                source_catalog=self._studio_source_catalog,
            )
            self._player.seek_frame(position_frame)
            if resume_playback:
                self._begin_playback_preparation(autoplay=True)
        except (PlaybackError, StudioCompingError) as exc:
            LOGGER.warning("Could not audition Studio take lane: %s", exc)
            self._hint.setText(
                "Studio couldn't audition that lane safely. The recordings are unchanged."
            )
            return
        self._studio_audition_lane_id = lane_id
        self._studio_arrange.set_audition_lane(lane_id)
        self._audition_take_lane_btn.setText("Return to Comp")
        self._hint.setText(
            "Auditioning this take lane only where it has recorded media. "
            "Double-click it again to return to the saved comp."
        )

    def _remove_selected_take_lane(self) -> None:
        lane_id = self._studio_arrange.selected_lane_id
        if not lane_id:
            return
        changed = self._perform_arrange_edit(
            "Remove take lane",
            lambda document: remove_take_lane(document, lane_id),
            reload_audio=True,
        )
        self._refresh_comp_controls()
        if changed:
            self._hint.setText(
                "Take lane removed non-destructively. The repeated recording remains "
                "in Takes."
            )

    def _refresh_comp_controls(self) -> None:
        if not hasattr(self, "_comp_toolbar"):
            return
        studio_visible = self._studio_state is not None and not self._viewing_live
        self._comp_toolbar.setVisible(studio_visible)
        selected_track = bool(
            self._studio_controller.selected_track_id if studio_visible else False
        )
        selected_lane = bool(self._studio_arrange.selected_lane_id)
        has_other_take = False
        if studio_visible and self._studio_project is not None:
            has_other_take = any(
                str(getattr(item, "session_id", "") or "")
                == self._studio_project.session_id
                and int(getattr(item, "project_samplerate", 0) or 0)
                == self._studio_project.project_sample_rate
                and item.path.expanduser().resolve() != self._studio_state_take_path
                for item in self._takes
            )
        self._add_take_lane_btn.setEnabled(selected_track and has_other_take)
        self._audition_take_lane_btn.setEnabled(selected_lane)
        self._remove_take_lane_btn.setEnabled(selected_lane)
        self._audition_take_lane_btn.setText(
            "Return to Comp" if self._studio_audition_lane_id else "Audition"
        )
        self._refresh_arrange_edit_controls()

    def _refresh_arrange_edit_controls(self) -> None:
        if not hasattr(self, "_arrange_toolbar"):
            return
        self._arrange_toolbar.refresh()

    def _perform_arrange_edit(
        self,
        label: str,
        edit,
        *,
        reload_audio: bool,
        merge_key: str | None = None,
    ) -> bool:
        try:
            before = self._studio_controller.document
            after = self._studio_controller.perform(
                label,
                edit,
                merge_key=merge_key,
            )
        except (StudioControllerError, StudioProjectError, ValueError) as exc:
            LOGGER.warning(
                "Could not apply Studio Arrange edit: %s",
                exc,
                exc_info=True,
            )
            self._hint.setText(arrange_edit_failure_message(exc))
            return False
        if after is before:
            return False
        self._sync_studio_controller_snapshot()
        if reload_audio:
            self._reload_arranged_playback()
        return True

    def _reload_arranged_playback(self) -> None:
        project = self._studio_project
        document = self._studio_state
        take_path = self._studio_state_take_path
        if project is None or document is None or take_path is None:
            return
        position_frame = self._player.position_frame
        resume_playback = self._player.is_playing or (
            self._playback_preparing and self._playback_prepare_autoplay
        )
        self._studio_audition_lane_id = None
        self._studio_arrange.set_audition_lane(None)
        self._audition_take_lane_btn.setText("Audition")
        try:
            self._cancel_playback_preparation(restore_controls=False)
            self._player.load_studio(
                project,
                document,
                take_path,
                source_catalog=self._studio_source_catalog,
            )
            self._player.seek_frame(position_frame)
            if resume_playback:
                self._begin_playback_preparation(autoplay=True)
        except PlaybackError as exc:
            LOGGER.warning("Could not refresh arranged playback: %s", exc)
            self._play_btn.setText("▶ Play")
            self._hint.setText(
                "The edit was saved, but Studio couldn't refresh playback safely. "
                "The recorded take is unchanged."
            )
        self._refresh_comp_controls()

    def _move_arrange_region(self, region_id: str, target_frame: object) -> None:
        if isinstance(target_frame, bool) or not isinstance(target_frame, int):
            return
        self._perform_arrange_edit(
            "Move region",
            lambda document: document.move_region(region_id, target_frame),
            reload_audio=True,
        )

    def _move_arrange_section(
        self, section_marker_id: str, target_frame: object
    ) -> None:
        """Ripple one named song section across every arrangement track."""

        if isinstance(target_frame, bool) or not isinstance(target_frame, int):
            return
        document = self._studio_state
        section = (
            next(
                (
                    marker
                    for marker in document.markers
                    if marker.marker_id == section_marker_id and not marker.deleted
                ),
                None,
            )
            if document is not None
            else None
        )
        changed = self._perform_arrange_edit(
            "Move song section",
            lambda document: reorder_section(
                document,
                section_marker_id,
                target_frame,
            ),
            reload_audio=True,
        )
        if changed:
            label = section.label if section is not None else "Section"
            sample_rate = document.project_sample_rate if document is not None else 0
            position = _format_frame_time(target_frame, sample_rate)
            self._hint.setText(
                f'Moved "{label}" to {position} across every track. '
                "Undo is available."
            )

    def _trim_arrange_region(
        self,
        region_id: str,
        edge: str,
        target_frame: object,
    ) -> None:
        if (
            edge not in {"start", "end"}
            or isinstance(target_frame, bool)
            or not isinstance(target_frame, int)
        ):
            return

        def edit(document: StudioDocument) -> StudioDocument:
            region = document.region_for(region_id)
            if edge == "start":
                return document.trim_region(
                    region_id,
                    timeline_start_frame=target_frame,
                    timeline_frame_count=region.timeline_end_frame - target_frame,
                )
            return document.trim_region(
                region_id,
                timeline_frame_count=target_frame - region.timeline_start_frame,
            )

        self._perform_arrange_edit("Trim region", edit, reload_audio=True)

    def _split_arrange_region(self, region_id: str, at_frame: object) -> None:
        if isinstance(at_frame, bool) or not isinstance(at_frame, int):
            return
        self._perform_arrange_edit(
            "Split region",
            lambda document: document.split_region(region_id, at_frame),
            reload_audio=True,
        )

    def _duplicate_arrange_region(self, region_id: str, at_frame: object) -> None:
        if isinstance(at_frame, bool) or not isinstance(at_frame, int):
            return
        self._perform_arrange_edit(
            "Duplicate region",
            lambda document: document.duplicate_region(
                region_id,
                timeline_start_frame=at_frame,
            ),
            reload_audio=True,
        )

    def _enable_arrange_region(self, region_id: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            return
        self._perform_arrange_edit(
            "Enable region" if enabled else "Disable region",
            lambda document: document.set_region_enabled(region_id, enabled),
            reload_audio=True,
        )

    def _delete_arrange_region(self, region_id: str) -> None:
        self._perform_arrange_edit(
            "Delete region",
            lambda document: document.delete_region(region_id),
            reload_audio=True,
        )

    def _set_arrange_snap_mode(self, mode: str) -> None:
        try:
            snap_mode = SnapMode(mode)
        except ValueError:
            return
        self._perform_arrange_edit(
            "Change snap mode",
            lambda document: document.set_snap_mode(snap_mode),
            reload_audio=False,
        )

    def _undo_arrange_edit(self) -> None:
        try:
            before = self._studio_controller.document
            after = self._studio_controller.undo()
        except StudioControllerError:
            return
        if after is before:
            return
        self._sync_studio_controller_snapshot()
        self._reload_arranged_playback()

    def _redo_arrange_edit(self) -> None:
        try:
            before = self._studio_controller.document
            after = self._studio_controller.redo()
        except StudioControllerError:
            return
        if after is before:
            return
        self._sync_studio_controller_snapshot()
        self._reload_arranged_playback()

    def _seek_from_arrange(self, frame: object) -> None:
        document = self._studio_state
        if (
            document is None
            or self._viewing_live
            or self._current is None
            or self._player.duration_s <= 0
            or isinstance(frame, bool)
            or not isinstance(frame, int)
        ):
            return
        try:
            self._player.seek_frame(max(0, frame))
        except PlaybackError as exc:
            self._handle_playback_error(exc)
            return
        self._sync_seek_ui(
            self._player.position_s,
            arrange_frame=self._player.position_frame,
        )

    def _track_info_for_channel(self, channel_id: int):
        return self._track_info_by_channel.get(int(channel_id))

    @staticmethod
    def _state_track_id(track) -> str:
        return str(getattr(track, "track_id", "") or "").strip()

    def _select_track(
        self,
        channel_id: int,
        *,
        sync_controller: bool = True,
    ) -> None:
        """Focus one lane and expose only its review/export facts."""
        channel_id = int(channel_id)
        if channel_id not in self._lanes:
            return
        if sync_controller and self._studio_state is not None:
            track_id = self._state_track_id(self._track_info_for_channel(channel_id))
            if track_id:
                try:
                    self._studio_controller.select_region(None)
                    self._studio_controller.select_track(track_id)
                    self._studio_arrange.set_selection(track_id=track_id)
                except (StudioControllerError, StudioProjectError, ValueError):
                    pass
        self._selected_channel_id = channel_id
        for lane_id, lane in self._lanes.items():
            lane.set_selected(lane_id == channel_id)

        source_info = self._track_info_for_channel(channel_id)
        if self._viewing_live or source_info is None:
            lane = self._lanes[channel_id]
            self._set_inspector_values(
                status="RECORDING" if self._recording else "ARMED",
                source="Live musician input",
                timeline=(
                    f"REC {_fmt_time(self._recording_elapsed)}"
                    if self._recording
                    else "Waiting for recording"
                ),
                alignment="Not applicable during live capture",
                gaps="No completed timeline yet",
                export=(
                    "Available after this take is saved" if lane is not None else "—"
                ),
            )
            return

        source = str(getattr(source_info, "source", "") or "")
        media_status = str(
            getattr(source_info, "media_status", "available") or "available"
        )
        status = {
            "available": "READY TO REVIEW",
            "recovered": "RECOVERED · REVIEW",
            "partial": "PARTIAL · REVIEW",
            "missing": "MISSING MEDIA",
            "damaged": "DAMAGED MEDIA",
            "transfer_failed": "TRANSFER FAILED",
            "transferring": "TRANSFER IN PROGRESS",
        }.get(media_status, "NEEDS REVIEW")
        offset = float(getattr(source_info, "offset_s", 0.0) or 0.0)
        duration = float(getattr(source_info, "duration_s", 0.0) or 0.0)
        if offset < 0:
            timeline = f"Begins before 0:00 · {_fmt_time(duration)} long"
        else:
            timeline = f"Starts {_fmt_time(offset)} · {_fmt_time(duration)} long"
        confidence = float(getattr(source_info, "alignment_confidence", 0.0) or 0.0)
        method = str(
            getattr(source_info, "alignment_method", "unverified") or "unverified"
        )
        if source == "jamulus_server":
            alignment = "Band server timeline reference"
        elif confidence > 0.0 and method != "unverified":
            alignment = f"Evidence {confidence:.2f} · {method}"
        else:
            alignment = "Needs verified timeline alignment"
        gaps = _timeline_gaps_for_track(source_info, self._current)
        track_id = self._state_track_id(source_info)
        selected = self._selected_track_export_track_ids(self._current)
        export = (
            "Included in next track export"
            if selected is None or track_id in selected
            else "Left out of next track export"
        )
        self._set_inspector_values(
            status=status,
            source=(
                "Band server track" if source == "jamulus_server" else "Local original"
            ),
            timeline=timeline,
            alignment=alignment,
            gaps=(
                "No recorded gaps"
                if not gaps
                else f"{len(gaps)} recorded gap{'s' if len(gaps) != 1 else ''}"
            ),
            export=export,
        )
        self._refresh_comp_controls()

    def _load_studio_state(self, take: TakeInfo) -> None:
        """Activate one controller-owned schema-v2 arrangement snapshot."""
        self._cancel_studio_waveforms(clear=True)
        self._studio_state = None
        self._studio_state_take_path = None
        self._studio_state_token = None
        self._studio_state_dirty = False
        self._studio_state_error = ""
        self._studio_project = None
        self._studio_source_catalog = None
        self._studio_audition_lane_id = None
        if hasattr(self, "_studio_arrange"):
            self._studio_arrange.set_document(None)
        if not _selectable_track_export_track_ids(take):
            return
        controller_loaded = False
        try:
            document = self._studio_controller.load(take.path)
            controller_loaded = True
            project = load_take_project(take.path)
            source_catalog = self._source_catalog_for_document(
                document,
                project,
                take.path.expanduser().resolve(),
            )
        except (
            StudioControllerError,
            StudioSourceCatalogError,
            TakeProjectError,
        ) as exc:
            if controller_loaded:
                try:
                    self._studio_controller.unload(discard_dirty=True)
                except StudioControllerError as unload_exc:
                    LOGGER.warning(
                        "Could not release failed Studio activation for %s: %s",
                        take.path,
                        unload_exc,
                    )
            LOGGER.warning("Ignoring Studio state for %s: %s", take.path, exc)
            self._studio_state_error = (
                "Saved Studio choices couldn't be used. Default review settings are "
                "shown; the recorded take is safe."
            )
            return
        self._studio_state = document
        self._studio_project = project
        self._studio_source_catalog = source_catalog
        self._studio_state_token = self._studio_controller.store_token
        self._studio_state_take_path = take.path.expanduser().resolve()
        self._studio_state_dirty = self._studio_controller.dirty
        if self._studio_controller.recovery_notice:
            self._studio_state_error = self._studio_controller.recovery_notice
        if hasattr(self, "_studio_arrange"):
            self._studio_arrange.set_document(document)
            self._set_master_controls(document.master)
            self._studio_arrange.set_track_names(
                {item.track_id: item.name for item in project.tracks}
            )
            self._activate_studio_waveforms()
        if self._studio_controller.dirty:
            self._schedule_studio_autosave(self._studio_controller.generation)
        self._refresh_comp_controls()

    def _schedule_studio_autosave(self, generation: int) -> None:
        """Coalesce controller edits onto the Qt event loop."""

        self._studio_autosave_generation = int(generation)
        timer = getattr(self, "_studio_state_save_timer", None)
        if timer is not None:
            timer.start()

    def _sync_studio_controller_snapshot(self) -> None:
        """Mirror controller state for existing UI and compatibility callers."""

        try:
            self._studio_state = self._studio_controller.document
        except StudioControllerError:
            self._studio_state = None
        self._studio_state_take_path = self._studio_controller.take_path
        self._studio_state_token = self._studio_controller.store_token
        self._studio_state_dirty = self._studio_controller.dirty
        if self._studio_state is not None and hasattr(self, "_studio_arrange"):
            self._refresh_studio_source_catalog()
            self._studio_arrange.set_document(self._studio_state)
            self._set_master_controls(self._studio_state.master)
            self._studio_arrange.set_selection(
                track_id=self._studio_controller.selected_track_id,
                region_id=self._studio_controller.selected_region_id,
            )
            self._sync_mixer_controls_from_document()
            self._activate_studio_waveforms()
        self._refresh_comp_controls()

    def _sync_mixer_controls_from_document(self) -> None:
        """Reflect undo/redo snapshots without emitting another mixer edit."""

        document = self._studio_state
        if document is None:
            return
        for channel_id, lane in self._lanes.items():
            track_id = self._state_track_id(self._track_info_for_channel(channel_id))
            if not track_id:
                continue
            try:
                state = document.state_for(track_id)
            except StudioProjectError:
                continue
            lane.set_mix_state(
                gain=state.fader_gain,
                trim_gain=state.trim_gain,
                pan=state.pan,
                muted=state.muted,
                solo=state.solo,
            )
            lane.set_track_export_included(state.export_included)

    def _saved_state_for_track(self, track_id: str):
        if self._studio_state is None or not track_id:
            return None
        try:
            return self._studio_state.state_for(track_id)
        except StudioProjectError:
            return None

    def _update_studio_state(self, channel_id: int, **changes: object) -> None:
        """Stage a user edit and coalesce sidecar writes while a slider moves."""
        track = self._track_info_for_channel(channel_id)
        track_id = self._state_track_id(track)
        if self._studio_state is None or not track_id:
            return
        try:
            self._studio_controller.perform(
                "Adjust track mix",
                lambda document: document.update_track(track_id, **changes),
                merge_key=self._studio_mix_merge_keys.get(
                    (int(channel_id), ",".join(sorted(changes)))
                ),
            )
            self._sync_studio_controller_snapshot()
        except (StudioControllerError, StudioProjectError) as exc:
            LOGGER.warning("Could not update Studio state: %s", exc)
            self._studio_state_error = (
                "Studio couldn't save that review choice. The recorded take is safe."
            )
            self._hint.setText(self._studio_state_error)
            return
        if self._selected_channel_id == int(channel_id):
            self._select_track(channel_id, sync_controller=False)

    def _flush_studio_state(self) -> bool:
        """Persist staged settings before changing takes or closing Studio."""
        if hasattr(self, "_studio_state_save_timer"):
            self._studio_state_save_timer.stop()
        if self._studio_state is None or self._studio_state_take_path is None:
            return True
        if not self._studio_controller.dirty:
            self._sync_studio_controller_snapshot()
            return True
        if not self._studio_controller.save():
            LOGGER.warning(
                "Could not save Studio state: %s",
                self._studio_controller.last_error,
            )
            self._studio_state_error = (
                "Studio couldn't save those review choices. The recorded take is safe."
            )
            self._hint.setText(self._studio_state_error)
            # Keep the edit dirty. A later edit, hide, export, or shutdown
            # retries instead of silently forgetting a low-disk/fsync failure.
            return False
        self._sync_studio_controller_snapshot()
        return True
