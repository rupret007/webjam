"""Application orchestration for standalone Reference Studio song projects.

The core project, sidecar, renderer, playback, and waveform components are
deliberately Qt-neutral.  This controller is their sole desktop owner.  It
translates semantic widget requests into immutable edits, keeps file/audio
work off the callback thread, and never mutates Jamulus configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import math
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QProgressDialog,
)

from core.project_playback import (
    ProjectPlaybackEngine,
    ProjectPlaybackError,
    ProjectPlaybackState,
    SoundDeviceProjectOutputBackend,
)
from core.project_audio import (
    PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
    PROJECT_AUDIO_SAMPLE_RATE,
)
from core.creative_modes import get_creator_profile_by_key_or_default
from core.meeting_link import STUDIO_MEETING_CAPTURE_NOTICE
from core.project_recording import (
    ArmedProjectTrack,
    ProjectMultitrackRecorder,
    ProjectRecorderState,
    ProjectRecordingError,
    ProjectRecordingSchedule,
    SoundDeviceProjectInputBackend,
)
from core.project_recording_commit import (
    ProjectRecordingCommitError,
    ProjectRecordingCommitRecoveryRequired,
    ProjectRecordingCommitResult,
    ProjectRecordingCommitState,
    commit_project_recording,
    inspect_project_recording_recovery,
    recover_project_recording_commit,
)
from core.project_tempo_analysis import (
    ProjectTempoAnalysis,
    ProjectTempoAnalysisError,
    analyze_project_tempo,
)
from core.song_bounce import (
    BounceFormat,
    SongBounceCancelled,
    SongBounceEngine,
    SongBounceError,
    SongBounceRequest,
    SongBounceResult,
    SongBounceStale,
)
from core.song_media_catalog import SongMediaCatalog, SongMediaCatalogError
from core.song_project import InputMapping, SongProject, SongProjectError
from core.song_project_controller import (
    SongProjectController,
    SongProjectControllerError,
)
from core.song_project_store import (
    SongProjectStoreError,
    load_project_bundle,
)
from core.song_studio_controller import (
    SongStudioController,
    SongStudioControllerError,
)
from core.song_studio_clone import (
    SongStudioSaveAsConflict,
    SongStudioSaveAsError,
    SongStudioSaveAsResult,
    save_song_studio_project_as,
)
from core.song_studio_reconcile import (
    SongStudioReconcileError,
    reconcile_song_studio_document,
)
from core.song_studio_store import (
    SongStudioStoreError,
    load_song_studio_document,
)
from core.studio_comping import StudioCompingError, select_lane_range
from core.studio_project import (
    MarkerKind,
    SnapMode,
    StudioAutomationInterpolation,
    StudioAutomationLane,
    StudioAutomationParameter,
    StudioAutomationPoint,
    StudioCycleRange,
    StudioDocument,
    StudioEffect,
    StudioEffectKind,
    StudioMarker,
    StudioMaster,
    StudioProjectError,
    StudioRegion,
    StudioSend,
    StudioTrack,
    StudioTrackKind,
)
from core.studio_renderer import StudioRenderError, StudioRenderer
from core.studio_sections import (
    StudioSectionError,
    duplicate_section,
    remove_section,
    reorder_section,
)
from core.studio_tempo import (
    MICRO_BPM_PER_BPM,
    TempoAnalysisCancelled,
    TempoAnalysisGuard,
    TempoAnalysisToken,
    TempoMap,
    TempoPoint,
    TimeSignaturePoint,
)
from webjam_qt.widgets.reference_studio_shell import ReferenceStudioShell
from webjam_qt.widgets.reference_studio_workspace import (
    ReferenceStudioPresentation,
)
from webjam_qt.widgets.studio_project_home import RecentStudioProject
from webjam_qt.widgets.studio_waveforms import (
    StudioWaveformCoordinator,
    StudioWaveformCoordinatorError,
    StudioWaveformRegionError,
    StudioWaveformRegionTile,
)
from webjam_qt.windows.reference_studio_tools import (
    ReferenceStudioBounceDialog,
    ReferenceStudioTempoReviewDialog,
)
from webjam_qt.windows.reference_studio_mixer import (
    ReferenceStudioAutomationDialog,
    ReferenceStudioMixerDialog,
)


_AUDIO_FILTER = (
    "Audio files (*.wav *.aif *.aiff *.flac *.ogg *.mp3);;"
    "WAV (*.wav);;AIFF (*.aif *.aiff);;FLAC (*.flac);;"
    "Ogg Vorbis (*.ogg);;MP3 (*.mp3)"
)
_BUNDLE_SUFFIX = ".webjam"
_PREPARE_WORKERS = 3
_NAMESPACE = uuid.UUID("43144e08-821a-448e-bf93-a5d36d3c7f12")
_FIRST_TAKE_DEFAULT_MINUTES = 5
# Overdub loop-records without a pass-count dialog; the musician presses Stop
# when done. This ceiling matches the maximum the cycle dialog offers.
_OVERDUB_MAX_PASSES = 20
_FIRST_TAKE_MAX_MINUTES = 120


class ReferenceStudioApplicationError(RuntimeError):
    """Bounded, musician-facing Reference Studio application failure."""


class _MediaPreparationCancelled(RuntimeError):
    pass


class ReferenceStudioApplicationController(QObject):
    """Own project workflow, transport, Arrange edits, and media preparation."""

    _media_prepared = Signal(int, object)
    _bounce_completed = Signal(int, str, object)
    _tempo_completed = Signal(int, str, object)
    _save_as_completed = Signal(int, str, object)
    _recording_completed = Signal(int, str, object)

    def __init__(
        self,
        shell: ReferenceStudioShell,
        *,
        config_file: str | Path | None = None,
        creator_profile_key: str = "music",
        profile_applied: Callable[[str], None] | None = None,
        output_backend=None,
        input_backend_factory=None,
        executor=None,
        parent: QObject | None = None,
    ) -> None:
        if not isinstance(shell, ReferenceStudioShell):
            raise TypeError("shell must be a ReferenceStudioShell.")
        if input_backend_factory is not None and not callable(input_backend_factory):
            raise TypeError("input_backend_factory must be callable.")
        if profile_applied is not None and not callable(profile_applied):
            raise TypeError("profile_applied must be callable.")
        super().__init__(parent or shell)
        self.shell = shell
        self.workspace = shell.workspace
        self._creator_profile = get_creator_profile_by_key_or_default(
            creator_profile_key
        )
        self._studio_preset = self._creator_profile.default_studio_preset
        self._profile_applied = profile_applied
        self.shell.set_creator_profile(self._creator_profile)
        config = Path(config_file or (Path.home() / ".webjam_config.json")).expanduser()
        recent_index = config.parent / ".webjam-reference-studio-recents.json"
        self.project_controller = SongProjectController(
            recent_index_path=recent_index,
            autosave_scheduler=self._schedule_project_autosave,
        )
        self.studio_controller = SongStudioController(
            autosave_requested=self._schedule_studio_autosave,
        )
        self._owns_executor = executor is None
        self._executor = executor or ThreadPoolExecutor(
            max_workers=_PREPARE_WORKERS,
            thread_name_prefix="reference-studio",
        )
        backend = output_backend or SoundDeviceProjectOutputBackend()
        self.playback = ProjectPlaybackEngine(backend)
        self._input_backend_factory = input_backend_factory or (
            lambda *, input_channels, device: SoundDeviceProjectInputBackend(
                input_channels=input_channels,
                block_frames=512,
                device=device,
            )
        )
        self._waveforms = StudioWaveformCoordinator(
            self._executor,
            publish_tile=self._publish_waveform_tile,
            publish_error=self._publish_waveform_error,
        )
        self._catalog: SongMediaCatalog | None = None
        self._renderer: StudioRenderer | None = None
        self._prepare_future: Future | None = None
        self._prepare_cancel: threading.Event | None = None
        self._prepare_generation = 0
        self._bounce_engine = SongBounceEngine()
        self._bounce_generation = 0
        self._bounce_cancel: threading.Event | None = None
        self._bounce_future: Future | None = None
        self._bounce_progress: QProgressDialog | None = None
        self._tempo_guard = TempoAnalysisGuard()
        self._tempo_token: TempoAnalysisToken | None = None
        self._tempo_future: Future | None = None
        self._tempo_progress: QProgressDialog | None = None
        self._save_as_generation = 0
        self._save_as_future: Future | None = None
        self._save_as_progress: QProgressDialog | None = None
        self._recorder: ProjectMultitrackRecorder | None = None
        self._recording_backend = None
        self._recording_generation = 0
        self._recording_commit_future: Future | None = None
        self._recording_progress: QProgressDialog | None = None
        self._recording_project_id = ""
        self._recording_bundle: Path | None = None
        self._recording_temp: Path | None = None
        self._recording_source_project: SongProject | None = None
        self._recording_source_document: StudioDocument | None = None
        self._recording_project_token: str | None = None
        self._recording_studio_token: str | None = None
        self._recording_recovery_pending = False
        self._recording_auto_stop = QTimer(self)
        self._recording_auto_stop.setSingleShot(True)
        self._recording_auto_stop.timeout.connect(self._stop_recording_async)
        self._latency_compensation_frames = 0
        self._closed = False
        self._status = self._profile_text(
            "Choose Play Along / Record, New Project, or Open Project.",
            "Choose New Recording, New Episode Project, or Open Project.",
            "Review & Rehearsal Preview supports take review; local multitrack projects are unavailable.",
        )
        self._metronome = bool(
            self._studio_preset is not None and self._studio_preset.metronome_enabled
        )
        self._count_in = bool(
            self._studio_preset is not None and self._studio_preset.count_in_enabled
        )
        self._overdub = False
        self._clipboard_regions: tuple[StudioRegion, ...] = ()
        self._last_waveform_error_generation = -1
        self._mixer_dialog: ReferenceStudioMixerDialog | None = None
        self._automation_dialog: ReferenceStudioAutomationDialog | None = None

        self._transport_timer = QTimer(self)
        self._transport_timer.setInterval(50)
        self._transport_timer.timeout.connect(self._poll_transport)
        self._transport_timer.start()
        self._waveform_timer = QTimer(self)
        self._waveform_timer.setInterval(50)
        self._waveform_timer.timeout.connect(self._drain_waveforms)
        self._waveform_timer.start()

        self._media_prepared.connect(self._accept_media_preparation)
        self._bounce_completed.connect(self._accept_bounce)
        self._tempo_completed.connect(self._accept_tempo_analysis)
        self._save_as_completed.connect(self._accept_save_as)
        self._recording_completed.connect(self._accept_recording_completion)
        self._connect_signals()
        self._refresh_recents()
        self._refresh()
        self._notify_profile_applied()

    # ------------------------------------------------------------------
    # Public lifecycle used by the containing application and tests
    # ------------------------------------------------------------------
    @property
    def project_open(self) -> bool:
        return self.project_controller.snapshot.is_open

    @property
    def creator_profile_key(self) -> str:
        return self._creator_profile.key

    def _profile_text(
        self,
        music: str,
        podcast_voice: str,
        review_rehearsal: str | None = None,
    ) -> str:
        if self._creator_profile.key == "podcast_voice":
            return podcast_voice
        if (
            self._creator_profile.key == "review_rehearsal"
            and review_rehearsal is not None
        ):
            return review_rehearsal
        return music

    def _notify_profile_applied(self) -> None:
        callback = self._profile_applied
        if callback is not None:
            callback(self._creator_profile.key)

    def _require_local_multitrack(self, action: str) -> None:
        if self._creator_profile.capabilities.local_multitrack:
            return
        raise ReferenceStudioApplicationError(
            f"{self._creator_profile.label} Preview cannot {action} local "
            "multitrack projects. Use its live review and completed-take "
            "workflow instead."
        )

    def _reject_studio_edit(self, action: str) -> bool:
        """Enforce creator capabilities below the disabled-command UI.

        Reference Studio normally cannot own a Review Preview project, but
        controller helpers are also called directly by tests and integration
        surfaces.  Keep that lower-level boundary explicit so a future caller
        cannot turn a disabled action into an editing path by bypassing the
        workspace command state.
        """

        capabilities = self._creator_profile.capabilities
        if capabilities.local_multitrack and capabilities.take_editing:
            return False
        self._status = (
            f"{self._creator_profile.label} Preview is playback and source "
            f"inspection only; it cannot {action}."
        )
        self._refresh()
        return True

    def _reject_studio_export(self, action: str) -> bool:
        """Enforce the profile export boundary at the bounce entry point."""

        capabilities = self._creator_profile.capabilities
        if capabilities.local_multitrack and capabilities.track_export:
            return False
        self._status = (
            f"{self._creator_profile.label} Preview is playback and source "
            f"inspection only; it cannot {action}."
        )
        self._refresh()
        return True

    @property
    def _is_recording(self) -> bool:
        recorder = self._recorder
        return bool(
            recorder is not None and recorder.state is ProjectRecorderState.RECORDING
        )

    @property
    def _recording_busy(self) -> bool:
        return bool(
            self._is_recording
            or self._recording_progress is not None
            or self._recording_commit_future is not None
        )

    def _reject_recording_change(self, action: str = "changing the project") -> bool:
        """Keep the saved commit tokens stable until recording is resolved."""

        if self._recording_busy:
            self._status = f"Finish the protected Studio recording before {action}."
            self._refresh()
            return True
        if self._recording_recovery_pending:
            self._status = (
                "This protected recording must be resolved before more project "
                "work. Close and reopen the project to resume recovery."
            )
            self._refresh()
            return True
        return False

    def activate(self) -> None:
        if self.project_open:
            self.shell.show_project()
        else:
            self.shell.show_home()
            self._refresh_recents()

    def _apply_project_creator_profile(self, project: SongProject) -> None:
        """Restore persisted workflow defaults without inferring from media."""

        profile = get_creator_profile_by_key_or_default(project.creator_profile_key)
        if not profile.capabilities.local_multitrack:
            raise ReferenceStudioApplicationError(
                f"{profile.label} Preview does not support local multitrack "
                "Reference Studio projects."
            )
        self._creator_profile = profile
        self._studio_preset = profile.default_studio_preset
        self.shell.set_creator_profile(profile)
        preset = self._studio_preset
        self._metronome = bool(preset and preset.metronome_enabled)
        self._count_in = bool(preset and preset.count_in_enabled)
        if preset is not None:
            self.workspace.arrange.set_ruler_mode(
                preset.ruler_mode,
                tempo_bpm=project.tempo_bpm,
                beats_per_bar=project.time_signature.numerator,
                beat_denominator=project.time_signature.denominator,
            )

    def create_project(
        self,
        bundle_path: str | Path,
        name: str,
        *,
        add_default_track: bool = True,
    ) -> SongProject:
        self._require_running()
        if self.project_open:
            raise ReferenceStudioApplicationError(
                "Close the current project before creating another."
            )
        self._require_local_multitrack("create")
        destination = self._bundle_destination(bundle_path)
        try:
            preset = self._studio_preset
            snapshot = self.project_controller.create_project(
                destination,
                name,
                project_sample_rate=(
                    preset.sample_rate_hz if preset is not None else 48_000
                ),
                creator_profile_key=self._creator_profile.key,
            )
            if add_default_track:
                track_names = preset.track_names if preset is not None else ("Audio 1",)
                for track_name in track_names:
                    snapshot = self.project_controller.add_track(track_name)
                self.project_controller.save_project()
                snapshot = self.project_controller.snapshot
            assert snapshot.project is not None and snapshot.bundle_path is not None
            self.studio_controller.load(snapshot.bundle_path, snapshot.project)
            self._apply_project_creator_profile(snapshot.project)
        except (SongProjectControllerError, SongStudioControllerError) as exc:
            self._reset_failed_open()
            raise ReferenceStudioApplicationError(str(exc)) from None
        self.shell.show_project()
        self._status = self._profile_text(
            "Project ready. Import a backing track or arm an input.",
            "Episode project ready. Import reference audio or arm a voice input.",
        )
        self._prepare_media_async()
        self._refresh_recents()
        self._refresh()
        self._notify_profile_applied()
        return snapshot.project

    def open_project(
        self,
        bundle_path: str | Path,
        *,
        project_recovery: str = "discard",
        studio_recovery: str = "discard",
        recording_recovery: str = "cancel",
    ) -> SongProject:
        self._require_running()
        if self.project_open:
            raise ReferenceStudioApplicationError(
                "Close the current project before opening another."
            )
        self._require_local_multitrack("open")
        try:
            snapshot = self.project_controller.open_project(bundle_path)
            if snapshot.recovery is not None:
                if project_recovery == "recover":
                    snapshot = self.project_controller.recover_autosave()
                elif project_recovery == "discard":
                    snapshot = self.project_controller.discard_recovery()
                else:
                    raise ReferenceStudioApplicationError(
                        "Project recovery requires an explicit Recover or Discard choice."
                    )
            assert snapshot.project is not None and snapshot.bundle_path is not None
            self._apply_project_creator_profile(snapshot.project)
            document = self.studio_controller.load(
                snapshot.bundle_path,
                snapshot.project,
            )
            if self.studio_controller.recovery_candidate is not None:
                if studio_recovery == "recover":
                    document = self.studio_controller.recover_autosave()
                elif studio_recovery == "discard":
                    self.studio_controller.discard_recovery()
                    document = self.studio_controller.document
                else:
                    raise ReferenceStudioApplicationError(
                        "Studio recovery requires an explicit Recover or Discard choice."
                    )
            recording_candidate = inspect_project_recording_recovery(
                snapshot.bundle_path
            )
            if recording_candidate is not None:
                if recording_recovery != "recover":
                    raise ReferenceStudioApplicationError(
                        "Recording recovery requires an explicit Resolve choice."
                    )
                recover_project_recording_commit(snapshot.bundle_path)
                self.studio_controller.unload(discard_dirty=True)
                closed = self.project_controller.close_project(discard_unsaved=True)
                if not closed.closed:
                    raise ReferenceStudioApplicationError(
                        "Recording recovery completed, but the project could not reload."
                    )
                snapshot = self.project_controller.open_project(bundle_path)
                assert snapshot.project is not None and snapshot.bundle_path is not None
                self._apply_project_creator_profile(snapshot.project)
                document = self.studio_controller.load(
                    snapshot.bundle_path,
                    snapshot.project,
                )
            self._recording_recovery_pending = False
            self.workspace.set_document(document)
        except ReferenceStudioApplicationError:
            self._reset_failed_open()
            raise
        except (
            ProjectRecordingCommitError,
            SongProjectControllerError,
            SongStudioControllerError,
        ) as exc:
            self._reset_failed_open()
            raise ReferenceStudioApplicationError(str(exc)) from None
        self.shell.show_project()
        self._status = "Verifying collected project media…"
        self._prepare_media_async()
        self._refresh_recents()
        self._refresh()
        assert snapshot.project is not None
        self._notify_profile_applied()
        return snapshot.project

    def import_backing(self, source_path: str | Path) -> None:
        if self._reject_recording_change("importing audio"):
            raise ReferenceStudioApplicationError(self._status)
        project, _bundle = self._open_identity()
        reference_audio = self._creator_profile.vocabulary.reference_audio_noun
        try:
            result = self.project_controller.import_backing_media(source_path)
            if not result.applied or result.stale or result.media is None:
                raise SongProjectControllerError(
                    self._profile_text(
                        "The backing-track import was superseded.",
                        "The reference audio import was superseded.",
                    )
                )
            saved = self.project_controller.save_project()
            project = saved.snapshot.project
            assert project is not None
            self._apply_studio_edit(
                f"Import {reference_audio}",
                lambda document: reconcile_song_studio_document(project, document),
                rebuild=False,
            )
            if not self.studio_controller.save():
                raise SongStudioControllerError(
                    self.studio_controller.last_error
                    or self._profile_text(
                        "WebJam couldn't save the backing arrangement.",
                        "WebJam couldn't save the reference audio arrangement.",
                    )
                )
        except (
            SongProjectControllerError,
            SongStudioControllerError,
            SongStudioReconcileError,
        ) as exc:
            self._status = str(exc)
            self._refresh()
            raise ReferenceStudioApplicationError(str(exc)) from None
        self._status = f"Imported {reference_audio} “{result.media.original_basename}”."
        self._prepare_media_async()
        self._refresh()

    def import_media(self, source_path: str | Path) -> None:
        if self._reject_recording_change("importing audio"):
            raise ReferenceStudioApplicationError(self._status)
        self._open_identity()
        try:
            result = self.project_controller.import_media(source_path)
            if not result.applied or result.stale or result.media is None:
                raise SongProjectControllerError("The media import was superseded.")
            self.project_controller.save_project()
        except SongProjectControllerError as exc:
            self._status = str(exc)
            self._refresh()
            raise ReferenceStudioApplicationError(str(exc)) from None
        self._status = (
            f"Collected “{result.media.original_basename}” in the project media bin."
        )
        self._prepare_media_async()
        self._refresh()

    def save(self, *, prepare_media: bool = True) -> bool:
        if self._reject_recording_change("saving"):
            return False
        self._open_identity()
        try:
            project_result = self.project_controller.save_project()
            studio_saved = self.studio_controller.save()
        except (SongProjectControllerError, SongStudioControllerError) as exc:
            self._status = str(exc)
            self._refresh()
            return False
        if not studio_saved:
            self._status = (
                self.studio_controller.last_error
                or "WebJam couldn't safely save the Studio arrangement."
            )
            self._refresh()
            return False
        self._status = "Project saved."
        if project_result.saved and prepare_media:
            self._prepare_media_async()
        self._refresh()
        return True

    def close_project(self, *, choice: str = "cancel") -> bool:
        if not self.project_open:
            self.shell.show_home()
            return True
        if self._recording_busy:
            self._status = (
                "Stop and finish the protected Studio recording before closing "
                "this project."
            )
            self._refresh()
            return False
        if self._save_as_future is not None:
            self._status = (
                "Save As is finishing its verified copy. Keep WebJam open until "
                "the result appears."
            )
            self._refresh()
            return False
        dirty = (
            self.project_controller.snapshot.dirty
            or self.studio_controller.dirty
            or self.studio_controller.recovery_candidate is not None
            or self.studio_controller.recovery_requires_discard
        )
        if dirty and choice == "save":
            if not self.save():
                return False
        elif dirty and choice != "discard":
            return False
        self._cancel_offline_tools()
        self._cancel_media_preparation()
        self._stop_playback()
        self._close_studio_dialogs()
        self._waveforms.cancel()
        self.workspace.arrange.clear_waveforms()
        try:
            self.studio_controller.unload(discard_dirty=(choice == "discard"))
            result = self.project_controller.close_project(
                discard_unsaved=(choice == "discard"),
            )
        except (SongProjectControllerError, SongStudioControllerError):
            return False
        if not result.closed:
            return False
        self._catalog = None
        self._renderer = None
        self._clear_recording_context()
        self.workspace.set_document(None)
        self._status = "Reference Studio home."
        self.shell.show_home()
        self._refresh_recents()
        self._refresh()
        return True

    def prepare_close(self) -> bool:
        """Synchronous quit gate; never silently discards a dirty project."""

        if not self.project_open:
            return True
        if self._recording_busy:
            self._status = (
                "Stop and finish the protected Studio recording before quitting WebJam."
            )
            self._refresh()
            return False
        if self._save_as_future is not None:
            self._status = (
                "Save As is still finishing. Wait for its verified result before "
                "quitting WebJam."
            )
            self._refresh()
            return False
        dirty = self.project_controller.snapshot.dirty or self.studio_controller.dirty
        if not dirty:
            return True
        choice = self._ask_unsaved_choice("Quit WebJam")
        if choice == "save":
            return self.save()
        return choice == "discard" and self.close_project(choice="discard")

    def shutdown(self) -> bool:
        if self._closed:
            return True
        if self.project_open and not self.prepare_close():
            return False
        self._closed = True
        self._cancel_offline_tools()
        self._tempo_guard.shutdown()
        self._cancel_media_preparation()
        self._transport_timer.stop()
        self._waveform_timer.stop()
        self._stop_playback()
        self._close_studio_dialogs()
        self.playback.close()
        self._waveforms.shutdown()
        self.studio_controller.shutdown()
        if self._owns_executor:
            # Cancellation is cooperative for media verification and waveform
            # jobs that are already running.  Join the owned pool before this
            # QObject and its Qt signal receivers can be destroyed; otherwise
            # a late native audio read or done-callback can race application
            # teardown after shutdown() has reported success.
            self._executor.shutdown(wait=True, cancel_futures=True)
        return True

    # ------------------------------------------------------------------
    # Signal wiring and file-dialog entry points
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self.shell.new_project_requested.connect(self._new_project_dialog)
        self.shell.open_project_requested.connect(self._open_project_dialog)
        self.shell.play_along_requested.connect(self._play_along_dialog)
        self.shell.recent_project_requested.connect(self._open_recent)
        self.workspace.command_requested.connect(self._dispatch_command)
        self.workspace.tempo_changed.connect(self._set_tempo)
        self.workspace.time_signature_changed.connect(self._set_time_signature)
        self.workspace.snap_changed.connect(self._set_musical_snap)
        self.workspace.track_selected.connect(self._select_track_row)
        self.workspace.files_dropped.connect(self._files_dropped)
        arrange = self.workspace.arrange
        arrange.scrub_requested.connect(self._seek)
        arrange.track_selected.connect(self._select_arrange_track)
        arrange.region_move_requested.connect(self._move_region)
        arrange.region_trim_requested.connect(self._trim_region)
        arrange.split_region_requested.connect(self._split_region)
        arrange.duplicate_region_requested.connect(self._duplicate_region)
        arrange.region_enabled_requested.connect(self._enable_region)
        arrange.delete_region_requested.connect(self._delete_region)
        arrange.comp_range_requested.connect(self._select_comp_range)
        arrange.section_move_requested.connect(self._move_section)
        arrange.section_duplicate_requested.connect(self._duplicate_section)
        arrange.section_remove_requested.connect(self._remove_section)
        arrange.viewport_changed.connect(self._schedule_waveforms)
        arrange.snap_mode_requested.connect(self._set_arrange_snap)
        arrange.undo_requested.connect(self._undo)
        arrange.redo_requested.connect(self._redo)

    def _new_project_dialog(self) -> None:
        if not self._creator_profile.capabilities.local_multitrack:
            self._status = (
                "Local multitrack projects are unavailable in Review & "
                "Rehearsal Preview."
            )
            self._refresh()
            return
        if not self._close_from_ui():
            return
        is_voice = self._creator_profile.key == "podcast_voice"
        name, accepted = QInputDialog.getText(
            self.shell,
            "New Episode Project" if is_voice else "New Reference Studio Project",
            "Episode name:" if is_voice else "Project name:",
            text="Untitled Episode" if is_voice else "Untitled Song",
        )
        if not accepted or not " ".join(name.split()):
            return
        path, _selected = QFileDialog.getSaveFileName(
            self.shell,
            "Create Episode Project" if is_voice else "Create Reference Studio Project",
            str(
                Path.home()
                / ("Documents" if is_voice else "Music")
                / f"{' '.join(name.split())}.webjam"
            ),
            "WebJam Project (*.webjam)",
        )
        if not path:
            return
        self._run_ui_action(lambda: self.create_project(path, name))

    def _open_project_dialog(self) -> None:
        if not self._creator_profile.capabilities.local_multitrack:
            self._status = (
                "Opening local multitrack projects is unavailable in Review & "
                "Rehearsal Preview."
            )
            self._refresh()
            return
        if not self._close_from_ui():
            return
        is_voice = self._creator_profile.key == "podcast_voice"
        path = QFileDialog.getExistingDirectory(
            self.shell,
            "Open Podcast or Voice Project"
            if is_voice
            else "Open Reference Studio Project",
            str(Path.home() / ("Documents" if is_voice else "Music")),
        )
        if path:
            self._open_with_recovery_ui(path)

    def _play_along_dialog(self) -> None:
        if self._creator_profile.key == "podcast_voice":
            self._new_project_dialog()
            return
        if not self._creator_profile.capabilities.local_multitrack:
            self._status = (
                "Local recording projects are unavailable in Review & Rehearsal "
                "Preview."
            )
            self._refresh()
            return
        if not self._close_from_ui():
            return
        audio, _selected = QFileDialog.getOpenFileName(
            self.shell,
            "Choose a Backing Track You Own or May Use",
            str(Path.home() / "Music"),
            _AUDIO_FILTER,
        )
        if not audio:
            return
        suggested_name = Path(audio).stem.strip() or "Play Along"
        destination, _selected = QFileDialog.getSaveFileName(
            self.shell,
            "Create the Reference Studio Project",
            str(Path.home() / "Music" / f"{suggested_name}.webjam"),
            "WebJam Project (*.webjam)",
        )
        if not destination:
            return

        def create_and_import() -> None:
            self.create_project(destination, suggested_name)
            self.import_backing(audio)

        self._run_ui_action(create_and_import)

    def _open_recent(self, path: str) -> None:
        if self._close_from_ui():
            self._open_with_recovery_ui(path)

    def _open_with_recovery_ui(self, path: str | Path) -> None:
        project_choice = "discard"
        studio_choice = "discard"
        recording_choice = "none"
        try:
            inspected = load_project_bundle(path)
            inspected_project = inspected.project
            if inspected.recovery_candidate is not None:
                project_choice = self._ask_recovery_choice(
                    "Recover Project Changes?",
                    "WebJam found project changes from an interrupted session. "
                    "Recover them, or discard that recovery copy and open the "
                    "last saved project?",
                )
                if project_choice == "cancel":
                    return
                if project_choice == "recover":
                    inspected_project = inspected.recovery_candidate.project
            studio = load_song_studio_document(path, inspected_project)
            if studio.recovery_candidate is not None:
                studio_choice = self._ask_recovery_choice(
                    "Recover Studio Edits?",
                    "WebJam found arrangement or mix edits from an interrupted "
                    "session. Recover them, or discard that recovery copy and "
                    "open the last saved Studio state?",
                )
                if studio_choice == "cancel":
                    return
            recording_candidate = inspect_project_recording_recovery(path)
            if recording_candidate is not None:
                recording_choice = self._ask_recording_recovery_choice(
                    recording_candidate.notice
                )
                if recording_choice == "cancel":
                    return
        except (
            ProjectRecordingCommitError,
            SongProjectStoreError,
            SongStudioStoreError,
        ):
            QMessageBox.warning(
                self.shell,
                "Reference Studio",
                "WebJam couldn't safely inspect that project.",
            )
            return
        self._run_ui_action(
            lambda: self.open_project(
                path,
                project_recovery=project_choice,
                studio_recovery=studio_choice,
                recording_recovery=recording_choice,
            )
        )

    def _ask_recovery_choice(self, title: str, message: str) -> str:
        box = QMessageBox(self.shell)
        box.setWindowTitle(title)
        box.setText(message)
        box.setInformativeText(
            "Imported originals remain unchanged whichever option you choose."
        )
        recover = box.addButton("Recover", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(
            "Discard Recovery",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is recover:
            return "recover"
        if clicked is discard:
            return "discard"
        return "cancel"

    def _ask_recording_recovery_choice(self, notice: str) -> str:
        box = QMessageBox(self.shell)
        box.setWindowTitle("Resolve Protected Recording?")
        box.setText("WebJam found a recording transaction from an interrupted session.")
        box.setInformativeText(
            " ".join(str(notice).split())[:400]
            + " Resolve it before editing this project. WebJam will either "
            "finish the verified commit or safely roll back an unpublished prepare."
        )
        resolve = box.addButton(
            "Resolve Recording",
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return "recover" if box.clickedButton() is resolve else "cancel"

    def _close_from_ui(self) -> bool:
        if not self.project_open:
            return True
        dirty = self.project_controller.snapshot.dirty or self.studio_controller.dirty
        choice = self._ask_unsaved_choice("Close Project") if dirty else "discard"
        return self.close_project(choice=choice)

    def _ask_unsaved_choice(self, title: str) -> str:
        box = QMessageBox(self.shell)
        box.setWindowTitle(title)
        box.setText("Save your Reference Studio project before continuing?")
        box.setInformativeText(
            "Imported originals remain unchanged. Unsaved arrangement and mix "
            "edits will be lost if you choose Discard."
        )
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        return "cancel"

    def _run_ui_action(self, callback) -> None:
        try:
            callback()
        except ReferenceStudioApplicationError as exc:
            QMessageBox.warning(self.shell, "Reference Studio", str(exc))

    # ------------------------------------------------------------------
    # Media catalog, renderer, waveform, and transport ownership
    # ------------------------------------------------------------------
    def _prepare_media_async(self) -> None:
        project, bundle = self._open_identity()
        document = self.studio_controller.document
        if self.project_controller.snapshot.dirty:
            self._status = "Save the project before verifying its collected media."
            self._refresh()
            return
        self._cancel_media_preparation()
        self._prepare_generation += 1
        generation = self._prepare_generation
        cancelled = threading.Event()
        self._prepare_cancel = cancelled
        self._status = "Verifying collected project media…"
        self._refresh()

        def check_cancelled() -> None:
            if cancelled.is_set():
                raise _MediaPreparationCancelled

        def worker():
            try:
                catalog = SongMediaCatalog.load(
                    project,
                    bundle,
                    cancel_check=check_cancelled,
                )
                check_cancelled()
                renderer = StudioRenderer(
                    project,
                    document,
                    bundle,
                    source_catalog=catalog,
                )
                return catalog, renderer, ""
            except _MediaPreparationCancelled:
                return None, None, "cancelled"
            except (SongMediaCatalogError, StudioRenderError):
                return (
                    None,
                    None,
                    "WebJam couldn't verify the collected project media.",
                )

        future = self._executor.submit(worker)
        self._prepare_future = future
        future.add_done_callback(
            lambda completed, item=generation: self._media_prepared.emit(
                item,
                completed,
            )
        )

    def _accept_media_preparation(self, generation: int, future: object) -> None:
        if self._closed or generation != self._prepare_generation:
            return
        if not isinstance(future, Future):
            return
        try:
            catalog, renderer, error = future.result()
        except Exception:
            catalog, renderer, error = (
                None,
                None,
                "WebJam couldn't prepare project playback.",
            )
        if error == "cancelled":
            return
        if error:
            self._catalog = None
            self._renderer = None
            self._status = error
            self.workspace.arrange.clear_waveforms()
            self._refresh()
            return
        if not isinstance(catalog, SongMediaCatalog) or not isinstance(
            renderer, StudioRenderer
        ):
            return
        self._catalog = catalog
        self._renderer = renderer
        try:
            self._stop_playback()
            self.playback.set_renderer(renderer, tempo_map=self._tempo_map())
            self._waveforms.activate(self.studio_controller.document, catalog)
            self._schedule_waveforms()
        except (
            ProjectPlaybackError,
            StudioWaveformCoordinatorError,
            StudioRenderError,
        ):
            self._status = "Project media verified, but playback preparation failed."
            self._refresh()
            return
        self._status = (
            "Ready to play."
            if renderer.timeline_end_frame > 0
            else self._profile_text(
                "Project ready. Import a backing track or recording.",
                "Episode ready. Import reference audio or record a voice track.",
            )
        )
        self._refresh()

    def _cancel_media_preparation(self) -> None:
        if self._prepare_cancel is not None:
            self._prepare_cancel.set()
        if self._prepare_future is not None:
            self._prepare_future.cancel()
        self._prepare_cancel = None
        self._prepare_future = None
        self._prepare_generation += 1

    def _rebuild_renderer(self) -> None:
        catalog = self._catalog
        if catalog is None or not self.project_open:
            return
        document = self.studio_controller.document
        try:
            renderer = StudioRenderer(
                catalog.project,
                document,
                catalog.bundle_root,
                source_catalog=catalog,
            )
            self._stop_playback()
            self.playback.set_renderer(renderer, tempo_map=self._tempo_map())
            self._renderer = renderer
            self._waveforms.activate(document, catalog)
            self._schedule_waveforms()
        except (
            ProjectPlaybackError,
            SongMediaCatalogError,
            StudioRenderError,
            StudioWaveformCoordinatorError,
        ):
            self._renderer = None
            self._status = (
                "The edit is safe, but playback needs media verification. Save "
                "and reopen the project if this continues."
            )

    def _schedule_waveforms(self) -> None:
        if self._catalog is None or self._renderer is None:
            return
        start, end = self.workspace.arrange.visible_frame_range()
        if end <= start:
            end = start + max(1, self.studio_controller.document.project_sample_rate)
        try:
            generation = self._waveforms.schedule(
                start,
                end,
                self.workspace.arrange.pixels_per_frame,
                self.workspace.arrange.visible_region_ids(),
            )
        except StudioWaveformCoordinatorError:
            return
        self.workspace.arrange.begin_waveform_generation(generation)

    def _publish_waveform_tile(self, item: StudioWaveformRegionTile) -> None:
        self.workspace.arrange.add_region_waveform_tile(
            item.region_id,
            item.tile,
            generation=item.generation,
        )

    def _publish_waveform_error(self, item: StudioWaveformRegionError) -> None:
        if item.generation != self._last_waveform_error_generation:
            self._last_waveform_error_generation = item.generation
            self._status = (
                "A waveform could not be drawn safely; playback media is unchanged."
            )
            self._refresh()

    def _drain_waveforms(self) -> None:
        if not self._closed:
            self._waveforms.drain(64)

    def _play_pause(self) -> None:
        if self._reject_recording_change("changing playback"):
            return
        if self._renderer is None or self._renderer.timeline_end_frame <= 0:
            self._status = self._profile_text(
                "Import a backing track or recording before playback.",
                "Import reference audio or record a voice track before playback.",
            )
            self._refresh()
            return
        try:
            state = self.playback.state
            if state is ProjectPlaybackState.PLAYING:
                self.playback.pause()
            else:
                self.playback.play()
        except ProjectPlaybackError as exc:
            self._status = str(exc)
        self._refresh()

    def _stop_playback(self) -> None:
        try:
            if self.playback.state not in {
                ProjectPlaybackState.EMPTY,
                ProjectPlaybackState.READY,
                ProjectPlaybackState.CLOSED,
            }:
                self.playback.stop()
        except ProjectPlaybackError:
            pass

    def _seek(self, frame: object) -> None:
        if self._reject_recording_change("moving the playhead"):
            return
        if (
            self._renderer is None
            or isinstance(frame, bool)
            or not isinstance(frame, int)
        ):
            return
        try:
            position = self.playback.seek(
                max(0, min(frame, self._renderer.timeline_end_frame))
            )
        except ProjectPlaybackError as exc:
            self._status = str(exc)
            return
        self.workspace.arrange.set_playhead(position)
        self._refresh()

    def _poll_transport(self) -> None:
        if self._closed:
            return
        try:
            snapshot = self.playback.poll()
        except ProjectPlaybackError:
            return
        self.workspace.arrange.set_playhead(snapshot.position_frame)
        if snapshot.error:
            self._status = snapshot.error
        self._refresh()

    # ------------------------------------------------------------------
    # Immutable edits and workspace commands
    # ------------------------------------------------------------------
    def _apply_studio_edit(self, label: str, edit, *, rebuild: bool = True) -> bool:
        if self._reject_studio_edit("edit the Studio arrangement"):
            return False
        if self._reject_recording_change("editing the arrangement"):
            return False
        try:
            before = self.studio_controller.document
            after = self.studio_controller.perform(label, edit)
        except (
            SongStudioControllerError,
            SongStudioReconcileError,
            StudioCompingError,
            StudioProjectError,
            StudioSectionError,
            ValueError,
        ) as exc:
            self._status = " ".join(str(exc).split())[:600]
            self._refresh()
            return False
        if after == before:
            return False
        self.workspace.set_document(after)
        if rebuild:
            self._rebuild_renderer()
        self._status = f"{label}. Undo is available."
        self._refresh()
        return True

    def _show_mixer(self) -> None:
        if self._reject_studio_edit("open Studio mixing controls"):
            return
        if self._reject_recording_change("opening the mixer"):
            return
        if not self.project_open:
            self._status = "Open a Reference Studio project before opening the mixer."
            self._refresh()
            return
        existing = self._mixer_dialog
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        if existing is not None:
            existing.deleteLater()
        dialog = ReferenceStudioMixerDialog(
            self.studio_controller.document,
            parent=self.shell,
        )
        dialog.track_fader_changed.connect(self._set_track_fader)
        dialog.track_pan_changed.connect(self._set_track_pan)
        dialog.track_mute_changed.connect(self._set_track_mute)
        dialog.track_solo_changed.connect(self._set_track_solo)
        dialog.track_reverb_send_changed.connect(self._set_track_reverb_send)
        dialog.track_effect_changed.connect(self._set_track_effect_enabled)
        dialog.master_changed.connect(self._set_master_mix)
        self._mixer_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._status = (
            "Mixer open. Channel-strip changes affect project playback and bounce."
        )
        self._refresh()

    def _show_automation(self) -> None:
        if self._reject_studio_edit("open Studio automation controls"):
            return
        if self._reject_recording_change("opening automation"):
            return
        if not self.project_open:
            self._status = "Open a Reference Studio project before editing automation."
            self._refresh()
            return
        track = self._selected_studio_track()
        if track is None:
            self._status = "Select a Studio track before editing automation."
            self._refresh()
            return
        existing = self._automation_dialog
        if existing is not None:
            existing.close()
            existing.deleteLater()
        dialog = ReferenceStudioAutomationDialog(
            track,
            playhead_frame=self.workspace.arrange.playhead_frame,
            parent=self.shell,
        )
        dialog.point_requested.connect(self._set_automation_point)
        dialog.clear_requested.connect(self._clear_automation_lane)
        self._automation_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._status = (
            f"Automation open for “{track.name}” at frame "
            f"{self.workspace.arrange.playhead_frame}."
        )
        self._refresh()

    def _close_studio_dialogs(self) -> None:
        dialogs = (self._mixer_dialog, self._automation_dialog)
        self._mixer_dialog = None
        self._automation_dialog = None
        for dialog in dialogs:
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()

    def _set_track_fader(self, track_id: str, gain: float) -> None:
        self._apply_studio_edit(
            "Changed track fader",
            lambda document: document.update_track(
                track_id,
                fader_gain=float(gain),
            ),
        )

    def _set_track_pan(self, track_id: str, pan: float) -> None:
        self._apply_studio_edit(
            "Changed track pan",
            lambda document: document.update_track(track_id, pan=float(pan)),
        )

    def _set_track_mute(self, track_id: str, muted: bool) -> None:
        if not isinstance(muted, bool):
            return
        self._apply_studio_edit(
            "Muted track" if muted else "Unmuted track",
            lambda document: document.update_track(track_id, muted=muted),
        )

    def _set_track_solo(self, track_id: str, solo: bool) -> None:
        if not isinstance(solo, bool):
            return
        self._apply_studio_edit(
            "Soloed track" if solo else "Cleared track solo",
            lambda document: document.update_track(track_id, solo=solo),
        )

    def _set_master_mix(self, gain: float, limiter_enabled: bool) -> None:
        if not isinstance(limiter_enabled, bool):
            return
        self._apply_studio_edit(
            "Changed master output",
            lambda document: document.set_master(
                StudioMaster(
                    gain=float(gain),
                    limiter_enabled=limiter_enabled,
                )
            ),
        )

    @staticmethod
    def _mixer_id(document: StudioDocument, role: str) -> str:
        return str(uuid.uuid5(_NAMESPACE, f"{document.project_id}:mixer:{role}"))

    def _set_track_reverb_send(self, track_id: str, gain: float) -> None:
        try:
            value = float(gain)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            self._status = "Shared reverb send must be between 0 and 100 percent."
            self._refresh()
            return

        def edit(document: StudioDocument) -> StudioDocument:
            source = document.state_for(track_id)
            if source.kind not in {
                StudioTrackKind.AUDIO,
                StudioTrackKind.BACKING,
            }:
                raise StudioProjectError(
                    "Shared reverb sends are available only on source tracks."
                )
            bus_id = self._mixer_id(document, "shared-reverb-bus")
            effect_id = self._mixer_id(document, "shared-reverb-effect")
            send_id = self._mixer_id(
                document,
                f"shared-reverb-send:{source.track_id}",
            )
            tracks = list(document.tracks)
            bus_index = next(
                (index for index, item in enumerate(tracks) if item.track_id == bus_id),
                -1,
            )
            if value > 0.0:
                if bus_index < 0:
                    next_order = max((item.order for item in tracks), default=-1) + 1
                    tracks.append(
                        StudioTrack(
                            track_id=bus_id,
                            order=next_order,
                            name="Shared Reverb",
                            kind=StudioTrackKind.BUS,
                            channel_count=2,
                            effects=(
                                StudioEffect(
                                    effect_id=effect_id,
                                    kind=StudioEffectKind.REVERB,
                                    reverb_mix=1.0,
                                ),
                            ),
                        )
                    )
                else:
                    bus = tracks[bus_index]
                    if bus.kind is not StudioTrackKind.BUS:
                        raise StudioProjectError(
                            "The deterministic shared-reverb ID is already in use."
                        )
                    reverb = next(
                        (
                            item
                            for item in bus.effects
                            if item.kind is StudioEffectKind.REVERB
                        ),
                        None,
                    )
                    effects = (
                        tuple(
                            replace(item, enabled=True, reverb_mix=1.0)
                            if item.kind is StudioEffectKind.REVERB
                            else item
                            for item in bus.effects
                        )
                        if reverb is not None
                        else bus.effects
                        + (
                            StudioEffect(
                                effect_id=effect_id,
                                kind=StudioEffectKind.REVERB,
                                reverb_mix=1.0,
                            ),
                        )
                    )
                    tracks[bus_index] = replace(bus, effects=effects)

            source_index = next(
                index
                for index, item in enumerate(tracks)
                if item.track_id == source.track_id
            )
            sends = tuple(
                item
                for item in source.sends
                if item.send_id != send_id and item.target_bus_id != bus_id
            )
            if value > 0.0:
                sends += (
                    StudioSend(
                        send_id=send_id,
                        target_bus_id=bus_id,
                        gain=value,
                        pre_fader=False,
                        enabled=True,
                    ),
                )
            updated_source = replace(source, sends=sends)
            tracks[source_index] = updated_source
            updated_tracks = tuple(tracks)
            if updated_tracks == document.tracks:
                return document
            return replace(
                document,
                tracks=updated_tracks,
                revision=document.revision + 1,
            )

        label = (
            "Disabled shared reverb send"
            if value == 0.0
            else "Changed shared reverb send"
        )
        self._apply_studio_edit(label, edit)

    def _set_track_effect_enabled(
        self,
        track_id: str,
        effect_kind: str,
        enabled: bool,
    ) -> None:
        if not isinstance(enabled, bool):
            return
        try:
            kind = StudioEffectKind(str(effect_kind))
        except ValueError:
            self._status = "That Studio effect is not supported."
            self._refresh()
            return
        if kind is StudioEffectKind.REVERB:
            self._status = "Use the shared reverb send to control Studio reverb."
            self._refresh()
            return

        def edit(document: StudioDocument) -> StudioDocument:
            track = document.state_for(track_id)
            if track.kind not in {
                StudioTrackKind.AUDIO,
                StudioTrackKind.BACKING,
                StudioTrackKind.BUS,
            }:
                raise StudioProjectError(
                    "Built-in effects are unavailable on that track."
                )
            current = next(
                (item for item in track.effects if item.kind is kind),
                None,
            )
            if current is None:
                if not enabled:
                    return document
                effect = StudioEffect(
                    effect_id=self._mixer_id(
                        document,
                        f"effect:{track.track_id}:{kind.value}",
                    ),
                    kind=kind,
                    enabled=enabled,
                )
                effects = track.effects + (effect,)
            else:
                effects = tuple(
                    replace(item, enabled=enabled) if item.kind is kind else item
                    for item in track.effects
                )
            return document.update_track(track.track_id, effects=effects)

        self._apply_studio_edit(
            f"{'Enabled' if enabled else 'Disabled'} {kind.value.upper()}",
            edit,
        )

    def _set_automation_point(
        self,
        track_id: str,
        parameter_value: str,
        frame: int,
        value: float,
    ) -> None:
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            return
        try:
            parameter = StudioAutomationParameter(str(parameter_value))
            point = StudioAutomationPoint(frame=frame, value=float(value))
        except (StudioProjectError, TypeError, ValueError):
            self._status = "That automation point is outside the supported range."
            self._refresh()
            return

        def edit(document: StudioDocument) -> StudioDocument:
            track = document.state_for(track_id)
            current = next(
                (item for item in track.automation if item.parameter is parameter),
                None,
            )
            points = {
                item.frame: item for item in (() if current is None else current.points)
            }
            points[point.frame] = point
            interpolation = (
                StudioAutomationInterpolation.HOLD
                if parameter is StudioAutomationParameter.MUTE
                else StudioAutomationInterpolation.LINEAR
            )
            lane = StudioAutomationLane(
                lane_id=(
                    current.lane_id
                    if current is not None
                    else self._mixer_id(
                        document,
                        f"automation:{track.track_id}:{parameter.value}",
                    )
                ),
                parameter=parameter,
                points=tuple(points[key] for key in sorted(points)),
                interpolation=interpolation,
                enabled=True,
            )
            automation = tuple(
                item for item in track.automation if item.parameter is not parameter
            ) + (lane,)
            automation = tuple(
                sorted(automation, key=lambda item: item.parameter.value)
            )
            return document.update_track(track.track_id, automation=automation)

        if self._apply_studio_edit(
            f"Added {parameter.value} automation at frame {frame}",
            edit,
        ):
            self._refresh_automation_dialog(track_id)

    def _clear_automation_lane(
        self,
        track_id: str,
        parameter_value: str,
    ) -> None:
        try:
            parameter = StudioAutomationParameter(str(parameter_value))
        except ValueError:
            self._status = "That automation parameter is not supported."
            self._refresh()
            return
        try:
            current_track = self.studio_controller.document.state_for(track_id)
        except StudioProjectError as exc:
            self._status = " ".join(str(exc).split())[:600]
            self._refresh()
            return
        if not any(item.parameter is parameter for item in current_track.automation):
            self._status = f"No {parameter.value} automation lane to clear."
            self._refresh()
            return

        def edit(document: StudioDocument) -> StudioDocument:
            track = document.state_for(track_id)
            automation = tuple(
                item for item in track.automation if item.parameter is not parameter
            )
            return document.update_track(track.track_id, automation=automation)

        if self._apply_studio_edit(
            f"Cleared {parameter.value} automation",
            edit,
        ):
            self._refresh_automation_dialog(track_id)

    def _refresh_automation_dialog(self, track_id: str) -> None:
        dialog = self._automation_dialog
        if dialog is None:
            return
        try:
            dialog.refresh_track(self.studio_controller.document.state_for(track_id))
        except (RuntimeError, StudioProjectError, ValueError):
            dialog.close()
            dialog.deleteLater()
            self._automation_dialog = None

    def _undo(self) -> None:
        if self._reject_studio_edit("undo Studio edits"):
            return
        if self._reject_recording_change("undoing an edit"):
            return
        try:
            before = self.studio_controller.document
            after = self.studio_controller.undo()
        except SongStudioControllerError:
            return
        if after != before:
            self.workspace.set_document(after)
            self._rebuild_renderer()
            self._status = "Undid the last Studio edit."
            self._refresh()

    def _redo(self) -> None:
        if self._reject_studio_edit("redo Studio edits"):
            return
        if self._reject_recording_change("redoing an edit"):
            return
        try:
            before = self.studio_controller.document
            after = self.studio_controller.redo()
        except SongStudioControllerError:
            return
        if after != before:
            self.workspace.set_document(after)
            self._rebuild_renderer()
            self._status = "Redid the Studio edit."
            self._refresh()

    def _move_region(self, region_id: str, target: object) -> None:
        if isinstance(target, bool) or not isinstance(target, int):
            return
        self._apply_studio_edit(
            "Moved region",
            lambda document: document.move_region(region_id, target),
        )

    def _trim_region(self, region_id: str, edge: str, target: object) -> None:
        if (
            edge not in {"start", "end"}
            or isinstance(target, bool)
            or not isinstance(target, int)
        ):
            return

        def edit(document: StudioDocument) -> StudioDocument:
            region = document.region_for(region_id)
            if edge == "start":
                return document.trim_region(
                    region_id,
                    timeline_start_frame=target,
                    timeline_frame_count=region.timeline_end_frame - target,
                )
            return document.trim_region(
                region_id,
                timeline_frame_count=target - region.timeline_start_frame,
            )

        self._apply_studio_edit("Trimmed region", edit)

    def _split_region(self, region_id: str, frame: object) -> None:
        if not isinstance(frame, bool) and isinstance(frame, int):
            self._apply_studio_edit(
                "Split region",
                lambda document: document.split_region(region_id, frame),
            )

    def _duplicate_region(self, region_id: str, frame: object) -> None:
        if not isinstance(frame, bool) and isinstance(frame, int):
            self._apply_studio_edit(
                "Duplicated region",
                lambda document: document.duplicate_region(
                    region_id,
                    timeline_start_frame=frame,
                ),
            )

    def _enable_region(self, region_id: str, enabled: bool) -> None:
        if isinstance(enabled, bool):
            self._apply_studio_edit(
                "Enabled region" if enabled else "Disabled region",
                lambda document: document.set_region_enabled(region_id, enabled),
            )

    def _delete_region(self, region_id: str) -> None:
        self._apply_studio_edit(
            "Deleted region non-destructively",
            lambda document: document.delete_region(region_id),
        )

    def _select_comp_range(self, lane_id: str, start: object, end: object) -> None:
        if any(
            isinstance(item, bool) or not isinstance(item, int) for item in (start, end)
        ):
            return
        self._apply_studio_edit(
            "Selected comp range",
            lambda document: select_lane_range(document, lane_id, start, end),
        )

    def _move_section(self, marker_id: str, target: object) -> None:
        if isinstance(target, bool) or not isinstance(target, int):
            return
        section = self._creator_profile.vocabulary.section_noun
        self._apply_studio_edit(
            f"Moved {section} across every track",
            lambda document: reorder_section(document, marker_id, target),
        )

    def _duplicate_section(self, marker_id: str) -> None:
        section = self._creator_profile.vocabulary.section_noun
        self._apply_studio_edit(
            f"Duplicated {section} across every track",
            lambda document: duplicate_section(document, marker_id),
        )

    def _remove_section(self, marker_id: str) -> None:
        section = self._creator_profile.vocabulary.section_noun
        self._apply_studio_edit(
            f"Removed {section} and closed the gap across every track",
            lambda document: remove_section(document, marker_id),
        )

    def _set_arrange_snap(self, mode: str) -> None:
        try:
            snap = SnapMode(mode)
        except ValueError:
            return
        self._apply_studio_edit(
            "Changed Arrange snap",
            lambda document: document.set_snap_mode(snap),
            rebuild=False,
        )

    def _select_arrange_track(self, track_id: str) -> None:
        ordered = self._ordered_studio_tracks()
        row = next(
            (index for index, item in enumerate(ordered) if item.track_id == track_id),
            -1,
        )
        if row >= 0:
            self.workspace.track_list.setCurrentRow(row)

    def _select_track_row(self, row: int) -> None:
        tracks = self._ordered_studio_tracks()
        if 0 <= row < len(tracks):
            self.workspace.arrange.set_selection(track_id=tracks[row].track_id)

    def _dispatch_command(self, command: str) -> None:
        if self._recording_busy and command not in {
            "record",
            "stop",
            "open_guide",
        }:
            self._status = (
                "Finish the protected Studio recording before changing the project."
            )
            self._refresh()
            return
        if self._recording_recovery_pending and command not in {
            "close_project",
            "open_guide",
        }:
            self._reject_recording_change()
            return
        handlers = {
            "new_project": self._new_project_dialog,
            "open_project": self._open_project_dialog,
            "save_project": lambda: self._run_ui_action(
                lambda: (
                    self.save()
                    or (_ for _ in ()).throw(
                        ReferenceStudioApplicationError(self._status)
                    )
                )
            ),
            "import_backing": self._import_backing_dialog,
            "import_media": self._import_media_dialog,
            "bounce": self._bounce_dialog,
            "close_project": lambda: self._close_from_ui(),
            "undo": self._undo,
            "redo": self._redo,
            "play_pause": self._play_pause,
            "stop": self._stop_and_refresh,
            "record": self._record_command,
            "return_to_start": lambda: self._seek(0),
            "toggle_metronome": self._toggle_metronome,
            "toggle_cycle": self._toggle_cycle,
            "toggle_count_in": self._toggle_count_in,
            "toggle_overdub": self._toggle_overdub,
            "new_audio_track": self._new_track_dialog,
            "rename_track": self._rename_track_dialog,
            "duplicate_track": self._duplicate_track,
            "remove_track": self._remove_track,
            "arm_selected_track": self._arm_selected_track,
            "map_track_input": self._map_track_input_dialog,
            "split_region": self._split_selected,
            "join_regions": self._join_selected_regions,
            "delete": self._delete_selected,
            "cut": self._cut_selected,
            "copy": self._copy_selected,
            "paste": self._paste_region,
            "loop_region": self._loop_selected_region,
            "quick_swipe_comp": self._quick_swipe_selected_region,
            "create_take_lane": self._create_take_lane_truth,
            "add_marker": lambda: self._add_marker(MarkerKind.MARKER),
            "add_section": lambda: self._add_marker(MarkerKind.SECTION),
            "project_settings": self._project_settings_dialog,
            "analyze_tempo": self._analyze_tempo,
            "latency_calibration": self._latency_calibration_dialog,
            "open_guide": self._show_guide,
            "select_all": self._select_all_regions,
            "show_media_bin": self._show_media_bin,
            "save_project_as": self._save_as_dialog,
            "relink_media": self._relink_backing_dialog,
            "collect_media": self._show_collect_truth,
            "toggle_ruler": self._toggle_ruler_truth,
            "show_mixer": self._show_mixer,
            "show_automation": self._show_automation,
        }
        handler = handlers.get(command)
        if handler is None:
            self._status = (
                f"{command.replace('_', ' ').title()} is not available in this "
                "Reference Studio build yet."
            )
            self._refresh()
            return
        handler()

    # ------------------------------------------------------------------
    # Bounded offline analysis and bounce workers
    # ------------------------------------------------------------------
    def _bounce_dialog(self) -> None:
        if self._reject_studio_export("bounce or export Studio audio"):
            return
        if self._bounce_future is not None and not self._bounce_future.done():
            self._status = "A project bounce is already running."
            self._refresh()
            return
        project, _bundle = self._open_identity()
        if self._renderer is None or self._renderer.timeline_end_frame <= 0:
            self._status = "Import or record audio before bouncing the project."
            self._refresh()
            return
        selected = self._selected_project_track()
        cycle = self.studio_controller.document.cycle_range
        dialog = ReferenceStudioBounceDialog(
            backing_available=project.backing_media_id is not None,
            selected_audio_track_available=selected is not None,
            cycle_available=cycle is not None and cycle.enabled,
            parent=self.shell,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        options = dialog.options
        suffix = f".{options.audio_format.value}"
        filter_text = (
            "24-bit WAV (*.wav)"
            if options.audio_format is BounceFormat.WAV
            else "24-bit FLAC (*.flac)"
        )
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self.shell,
            "Choose Bounce Destination",
            str(Path.home() / "Music" / f"{project.name} Mix{suffix}"),
            filter_text,
        )
        if not destination:
            return
        path = Path(destination).expanduser()
        if path.suffix.casefold() != suffix:
            path = path.with_name(path.name + suffix)
        track_ids = (
            (selected.track_id,)
            if options.selected_track_only and selected is not None
            else None
        )
        try:
            request = SongBounceRequest(
                destination=path,
                audio_format=options.audio_format,
                track_ids=track_ids,
                include_backing=options.include_backing,
                create_stems=options.create_stems,
                use_cycle_range=options.use_cycle_range,
            )
        except SongBounceError as exc:
            self._status = str(exc)
            self._refresh()
            return
        self._start_bounce(request)

    def _start_bounce(self, request: SongBounceRequest) -> None:
        if self._reject_studio_export("bounce or export Studio audio"):
            return
        if not self.save():
            QMessageBox.warning(
                self.shell,
                "Reference Studio Bounce",
                "Save the project successfully before starting a bounce.",
            )
            return
        project, bundle = self._open_identity()
        document = self.studio_controller.document
        generation = self._bounce_engine.begin()
        cancelled = threading.Event()
        self._bounce_generation = generation
        self._bounce_cancel = cancelled

        progress = QProgressDialog(
            "Rendering and verifying the project…",
            "Cancel Bounce",
            0,
            0,
            self.shell,
        )
        progress.setObjectName("ReferenceStudioBounceProgress")
        progress.setWindowTitle("Bouncing Reference Studio Project")
        progress.setAccessibleName("Project bounce progress")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self._cancel_bounce)
        self._bounce_progress = progress
        progress.show()
        self._status = "Bouncing a verified project mix…"
        self._refresh()

        def worker():
            try:
                catalog = SongMediaCatalog.load(project, bundle)
                renderer = StudioRenderer(
                    project,
                    document,
                    bundle,
                    source_catalog=catalog,
                )
                result = self._bounce_engine.bounce(
                    renderer,
                    request,
                    generation=generation,
                    cancel_event=cancelled,
                )
                return result, ""
            except (SongBounceCancelled, SongBounceStale):
                return None, "cancelled"
            except (SongBounceError, SongMediaCatalogError, StudioRenderError) as exc:
                return None, " ".join(str(exc).split())[:600]
            except Exception:
                return None, "The project bounce failed safely before publication."

        future = self._executor.submit(worker)
        self._bounce_future = future
        future.add_done_callback(
            lambda completed, item=generation, identity=project.project_id: (
                self._bounce_completed.emit(item, identity, completed)
            )
        )

    def _cancel_bounce(self) -> None:
        active = bool(
            self._bounce_cancel is not None
            or self._bounce_progress is not None
            or (self._bounce_future is not None and not self._bounce_future.done())
        )
        if not active:
            return
        if self._bounce_cancel is not None:
            self._bounce_cancel.set()
        if self._bounce_generation:
            self._bounce_engine.cancel(self._bounce_generation)
        if self._bounce_progress is not None:
            self._bounce_progress.setLabelText(
                "Cancelling and removing unpublished bounce files…"
            )
            self._bounce_progress.setCancelButton(None)
        if not self._closed:
            self._status = "Cancelling project bounce…"
            self._refresh()

    def _accept_bounce(
        self,
        generation: int,
        project_id: str,
        future: object,
    ) -> None:
        if generation != self._bounce_generation:
            return
        progress = self._bounce_progress
        self._bounce_progress = None
        if progress is not None:
            try:
                progress.canceled.disconnect(self._cancel_bounce)
            except (RuntimeError, TypeError):
                pass
            progress.close()
            progress.deleteLater()
        self._bounce_cancel = None
        self._bounce_future = None
        if self._closed:
            return
        current = self.project_controller.snapshot.project
        if current is None or current.project_id != project_id:
            return
        if not isinstance(future, Future):
            return
        try:
            result, error = future.result()
        except Exception:
            result, error = (
                None,
                "The project bounce failed safely before publication.",
            )
        if error == "cancelled":
            self._status = "Project bounce cancelled; no partial files were published."
            self._refresh()
            return
        if error or not isinstance(result, SongBounceResult):
            self._status = (
                error or "The project bounce did not produce a verified file."
            )
            QMessageBox.warning(
                self.shell,
                "Reference Studio Bounce",
                self._status,
            )
            self._refresh()
            return
        artifact_lines: list[str] = []
        for artifact in result.artifacts:
            peak = (
                "−∞"
                if artifact.analysis.peak_dbfs is None
                else f"{artifact.analysis.peak_dbfs:.2f}"
            )
            loudness = (
                "−∞"
                if artifact.analysis.loudness_dbfs is None
                else f"{artifact.analysis.loudness_dbfs:.2f}"
            )
            artifact_lines.append(
                "\n".join(
                    (
                        artifact.path.name,
                        f"SHA-256: {artifact.sha256}",
                        f"Peak: {peak} dBFS",
                        f"RMS loudness: {loudness} dBFS",
                        f"Clipped samples: {artifact.analysis.clipped_sample_count}",
                    )
                )
            )
        self._status = (
            f"Published {len(result.artifacts)} verified bounce "
            f"{'file' if len(result.artifacts) == 1 else 'files'}."
        )
        QMessageBox.information(
            self.shell,
            "Reference Studio Bounce Complete",
            self._status + "\n\n" + "\n\n".join(artifact_lines),
        )
        self._refresh()

    def _analyze_tempo(self) -> None:
        reference_audio = self._creator_profile.vocabulary.reference_audio_noun
        if self._tempo_future is not None and not self._tempo_future.done():
            self._status = self._profile_text(
                "Backing-track tempo analysis is already running.",
                "Reference audio tempo analysis is already running.",
            )
            self._refresh()
            return
        project, bundle = self._open_identity()
        media_id = project.backing_media_id
        if media_id is None:
            selected = self._selected_region()
            media_id = selected.source_media_id if selected is not None else None
        if media_id is None:
            self._status = (
                f"Import {reference_audio} or select a collected-audio region "
                "before analyzing tempo."
            )
            self._refresh()
            return
        if self.project_controller.snapshot.dirty and not self.save():
            return
        project, bundle = self._open_identity()
        token = self._tempo_guard.begin_generation()
        self._tempo_token = token

        progress = QProgressDialog(
            self._profile_text(
                "Analyzing a bounded set of backing-track windows…",
                "Analyzing a bounded set of reference audio windows…",
            ),
            "Cancel Analysis",
            0,
            0,
            self.shell,
        )
        progress.setObjectName("ReferenceStudioTempoProgress")
        progress.setWindowTitle(
            self._profile_text(
                "Analyzing Backing Tempo",
                "Analyzing Reference Audio Tempo",
            )
        )
        progress.setAccessibleName(
            self._profile_text(
                "Backing-track tempo analysis progress",
                "Reference audio tempo analysis progress",
            )
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self._cancel_tempo_analysis)
        self._tempo_progress = progress
        progress.show()
        self._status = self._profile_text(
            "Analyzing backing-track tempo…",
            "Analyzing reference audio tempo…",
        )
        self._refresh()

        def worker():
            try:
                catalog = SongMediaCatalog.load(project, bundle)
                report = analyze_project_tempo(catalog, media_id, token)
                return report, ""
            except TempoAnalysisCancelled:
                return None, "cancelled"
            except (
                ProjectTempoAnalysisError,
                SongMediaCatalogError,
            ) as exc:
                return None, " ".join(str(exc).split())[:600]
            except Exception:
                return (
                    None,
                    "Tempo analysis couldn't finish safely. Enter the tempo manually.",
                )

        future = self._executor.submit(worker)
        self._tempo_future = future
        future.add_done_callback(
            lambda completed, item=token.generation, identity=project.project_id: (
                self._tempo_completed.emit(item, identity, completed)
            )
        )

    def _cancel_tempo_analysis(self) -> None:
        active = bool(
            self._tempo_token is not None
            or self._tempo_progress is not None
            or (self._tempo_future is not None and not self._tempo_future.done())
        )
        if not active:
            return
        self._tempo_guard.cancel_current()
        if self._tempo_progress is not None:
            self._tempo_progress.setLabelText("Cancelling tempo analysis…")
            self._tempo_progress.setCancelButton(None)
        if not self._closed:
            self._status = "Cancelling tempo analysis…"
            self._refresh()

    def _accept_tempo_analysis(
        self,
        generation: int,
        project_id: str,
        future: object,
    ) -> None:
        token = self._tempo_token
        if token is None or token.generation != generation:
            return
        progress = self._tempo_progress
        self._tempo_progress = None
        if progress is not None:
            try:
                progress.canceled.disconnect(self._cancel_tempo_analysis)
            except (RuntimeError, TypeError):
                pass
            progress.close()
            progress.deleteLater()
        self._tempo_future = None
        self._tempo_token = None
        if self._closed:
            return
        current = self.project_controller.snapshot.project
        if current is None or current.project_id != project_id:
            return
        if not isinstance(future, Future):
            return
        try:
            report, error = future.result()
        except Exception:
            report, error = (
                None,
                "Tempo analysis couldn't finish safely. Enter the tempo manually.",
            )
        if error == "cancelled":
            self._status = "Tempo analysis cancelled; the project was unchanged."
            self._refresh()
            return
        if error or not isinstance(report, ProjectTempoAnalysis):
            self._status = error or "Tempo analysis did not return a usable result."
            QMessageBox.warning(
                self.shell,
                self._profile_text(
                    "Backing Tempo Analysis",
                    "Reference Audio Tempo Analysis",
                ),
                self._status,
            )
            self._refresh()
            return
        try:
            self._tempo_guard.accept(token, report.result)
        except TempoAnalysisCancelled:
            self._status = "A newer tempo analysis superseded that result."
            self._refresh()
            return
        detected = report.result.detected_bpm_micros / MICRO_BPM_PER_BPM
        numerator, denominator = report.result.effective_time_signature
        dialog = ReferenceStudioTempoReviewDialog(
            detected_bpm=detected,
            confidence_percent=(report.result.confidence_millionths / 10_000),
            numerator=numerator,
            denominator=denominator,
            manual_review_recommended=report.manual_correction_recommended,
            parent=self.shell,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status = "Tempo analysis reviewed; the project was unchanged."
            self._refresh()
            return
        choice = dialog.choice
        try:
            reviewed = report.with_manual_correction(
                bpm=choice.bpm,
                numerator=choice.numerator,
                denominator=choice.denominator,
            )
            self.project_controller.set_tempo(
                reviewed.result.effective_bpm_micros / MICRO_BPM_PER_BPM
            )
            top, bottom = reviewed.result.effective_time_signature
            self.project_controller.set_time_signature(top, bottom)
            if self._renderer is not None:
                self._stop_playback()
                self.playback.set_renderer(
                    self._renderer,
                    tempo_map=self._tempo_map(),
                )
        except (
            ProjectTempoAnalysisError,
            ProjectPlaybackError,
            SongProjectControllerError,
            ValueError,
        ) as exc:
            self._status = " ".join(str(exc).split())[:600]
            self._refresh()
            return
        self._status = (
            f"Applied {choice.bpm:.2f} BPM and "
            f"{choice.numerator}/{choice.denominator}; project audio was unchanged."
        )
        self._refresh()

    def _cancel_offline_tools(self) -> None:
        self._cancel_bounce()
        self._cancel_tempo_analysis()

    # ------------------------------------------------------------------
    # Presentation and small helpers
    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        snapshot = self.project_controller.snapshot
        if not snapshot.is_open or snapshot.project is None:
            self.workspace.set_presentation(
                ReferenceStudioPresentation(status=self._status)
            )
            return
        project = snapshot.project
        document = self.studio_controller.document
        playback = self.playback.snapshot()
        tempo_map = self._tempo_map(project)
        position = tempo_map.frame_to_bar_position(playback.position_frame)
        tick = math.floor(position.tick_in_beat)
        track_names = tuple(item.name for item in self._ordered_studio_tracks())
        backing = (
            project.media_by_id(project.backing_media_id).original_basename
            if project.backing_media_id is not None
            else self._profile_text(
                "No backing track",
                "No reference audio",
            )
        )
        dirty = snapshot.dirty or self.studio_controller.dirty
        playing = playback.state is ProjectPlaybackState.PLAYING
        duration = self._format_elapsed(
            playback.position_frame, project.project_sample_rate
        )
        renderer_ready = self._renderer is not None
        can_play = bool(
            self._renderer is not None
            and self._renderer.timeline_end_frame > 0
            and playback.state is not ProjectPlaybackState.CLOSED
        )
        armed = tuple(item for item in project.tracks if item.armed)
        ready_to_record = bool(
            renderer_ready
            and armed
            and all(item.input_mapping is not None for item in armed)
            and len(
                {
                    item.input_mapping.device_key
                    for item in armed
                    if item.input_mapping is not None
                }
            )
            == 1
            and self._recording_commit_future is None
            and self._recording_progress is None
            and not self._recording_recovery_pending
        )
        recording = self._is_recording and self._recording_progress is None
        bounce_running = bool(
            self._bounce_future is not None and not self._bounce_future.done()
        )
        self.workspace.set_presentation(
            ReferenceStudioPresentation(
                project_name=project.name,
                save_state="Unsaved changes" if dirty else "Saved",
                status=self._status,
                backing_track=backing,
                position_text=(
                    f"{position.bar_number} {position.beat_number} {tick:03d}"
                ),
                duration_text=duration,
                track_names=track_names,
                dirty=dirty,
                playing=playing,
                recording=recording,
                can_save=(
                    dirty
                    and not self._recording_busy
                    and not self._recording_recovery_pending
                ),
                can_play=(
                    can_play
                    and not self._recording_busy
                    and not self._recording_recovery_pending
                ),
                can_record=recording or (ready_to_record and not self._recording_busy),
                can_bounce=(
                    can_play
                    and not bounce_running
                    and not self._recording_busy
                    and not self._recording_recovery_pending
                ),
            )
        )
        self.workspace.set_project_controls(
            tempo_bpm=project.tempo_bpm,
            numerator=project.time_signature.numerator,
            denominator=project.time_signature.denominator,
            snap_mode=self._musical_snap_value(document.snap_mode),
            metronome=self._metronome,
            cycle=document.cycle_range is not None and document.cycle_range.enabled,
            count_in=self._count_in,
            overdub=self._overdub,
        )
        if self.workspace.arrange.ruler_mode == "bars":
            self.workspace.arrange.set_ruler_mode(
                "bars",
                tempo_bpm=project.tempo_bpm,
                beats_per_bar=project.time_signature.numerator,
                beat_denominator=project.time_signature.denominator,
            )
        self.workspace.actions["undo"].setEnabled(self.studio_controller.can_undo)
        self.workspace.actions["redo"].setEnabled(self.studio_controller.can_redo)

    def _refresh_recents(self) -> None:
        records = tuple(
            RecentStudioProject(
                path=str(path),
                title=path.stem.removesuffix(_BUNDLE_SUFFIX).strip()
                or "Reference Studio Project",
                detail="Reference Studio project",
            )
            for path in self.project_controller.snapshot.recent_projects
        )
        self.shell.home.set_recent_projects(records)

    def _ordered_studio_tracks(self):
        if not self.project_open:
            return ()
        return tuple(
            sorted(
                self.studio_controller.document.tracks,
                key=lambda item: (item.order, item.track_id),
            )
        )

    def _selected_studio_track(self):
        tracks = self._ordered_studio_tracks()
        row = self.workspace.selected_track_index
        if 0 <= row < len(tracks):
            return tracks[row]
        selected = self.workspace.arrange.selected_track_id
        return next((item for item in tracks if item.track_id == selected), None)

    def _selected_project_track(self):
        selected = self._selected_studio_track()
        if selected is None or selected.kind is not StudioTrackKind.AUDIO:
            return None
        project, _bundle = self._open_identity()
        return next(
            (item for item in project.tracks if item.track_id == selected.track_id),
            None,
        )

    def _open_identity(self) -> tuple[SongProject, Path]:
        snapshot = self.project_controller.snapshot
        if snapshot.project is None or snapshot.bundle_path is None:
            raise ReferenceStudioApplicationError(
                "Open a Reference Studio project first."
            )
        return snapshot.project, snapshot.bundle_path

    def _tempo_map(self, project: SongProject | None = None) -> TempoMap:
        project = project or self._open_identity()[0]
        tempo_id = str(uuid.uuid5(_NAMESPACE, f"{project.project_id}:tempo"))
        signature_id = str(
            uuid.uuid5(_NAMESPACE, f"{project.project_id}:time-signature")
        )
        return TempoMap(
            sample_rate=project.project_sample_rate,
            tempo_points=(
                TempoPoint(
                    point_id=tempo_id,
                    frame=0,
                    bpm_micros=round(project.tempo_bpm * MICRO_BPM_PER_BPM),
                ),
            ),
            time_signature_points=(
                TimeSignaturePoint(
                    point_id=signature_id,
                    frame=0,
                    numerator=project.time_signature.numerator,
                    denominator=project.time_signature.denominator,
                ),
            ),
        )

    @staticmethod
    def _format_elapsed(frame: int, sample_rate: int) -> str:
        seconds = max(0, frame) / max(1, sample_rate)
        minutes = int(seconds // 60)
        return f"{minutes}:{seconds - minutes * 60:06.3f}"

    @staticmethod
    def _bundle_destination(value: str | Path) -> Path:
        path = Path(value).expanduser()
        if path.suffix.casefold() != _BUNDLE_SUFFIX:
            path = path.with_name(path.name + _BUNDLE_SUFFIX)
        return path

    @staticmethod
    def _musical_snap_value(value: SnapMode) -> str:
        if value is SnapMode.OFF:
            return "off"
        if value is SnapMode.TIME:
            return "eighth"
        return "bar"

    def _require_running(self) -> None:
        if self._closed:
            raise ReferenceStudioApplicationError(
                "Reference Studio has already shut down."
            )

    def _reset_failed_open(self) -> None:
        try:
            if self.project_controller.snapshot.is_open:
                self.project_controller.close_project(discard_unsaved=True)
        except Exception:
            pass
        try:
            if self.studio_controller.bundle_path is not None:
                self.studio_controller.unload(discard_dirty=True)
        except Exception:
            pass
        self._catalog = None
        self._renderer = None
        self.workspace.set_document(None)
        self.shell.show_home()

    def _schedule_project_autosave(self, callback) -> None:
        def flush() -> None:
            if self._closed or not self.project_open:
                return
            try:
                callback()
            except SongProjectControllerError:
                return

        QTimer.singleShot(1_500, flush)

    def _schedule_studio_autosave(self, generation: int) -> None:
        def flush(item: int = generation) -> None:
            if self._closed or not self.project_open:
                return
            try:
                self.studio_controller.flush_autosave(item)
            except SongStudioControllerError:
                return

        QTimer.singleShot(1_500, flush)

    # Remaining concrete menu handlers are kept below so the command surface
    # remains auditable as one finite vocabulary.
    def _stop_and_refresh(self) -> None:
        if self._is_recording:
            self._stop_recording_async()
            return
        if self._recording_busy:
            self._status = "The protected Studio recording is still finishing."
            self._refresh()
            return
        self._stop_playback()
        self._refresh()

    def _record_command(self) -> None:
        if self._is_recording:
            self._stop_recording_async()
            return
        if self._recording_busy:
            self._status = "The previous Studio recording is still finishing."
            self._refresh()
            return
        if self._recording_recovery_pending:
            self._reject_recording_change("starting another recording")
            return
        self._start_recording()

    def _start_recording(self) -> None:
        project, bundle = self._open_identity()
        if self._recording_recovery_pending:
            self._reject_recording_change("starting another recording")
            return
        renderer = self._renderer
        if renderer is None:
            self._status = (
                "Wait for Reference Studio to finish verifying the project "
                "before recording."
            )
            self._refresh()
            return
        first_take = renderer.timeline_end_frame <= 0
        armed_project_tracks = tuple(item for item in project.tracks if item.armed)
        if not armed_project_tracks:
            self._status = "Arm at least one audio track before recording."
            self._refresh()
            return
        if any(item.input_mapping is None for item in armed_project_tracks):
            self._status = "Map every armed track to an input before recording."
            self._refresh()
            return
        device_keys = {
            item.input_mapping.device_key
            for item in armed_project_tracks
            if item.input_mapping is not None
        }
        if len(device_keys) != 1:
            self._status = (
                "All armed tracks must use the same input device for synchronized "
                "multitrack recording."
            )
            self._refresh()
            return
        if not self.save(prepare_media=False):
            return
        # A previously queued catalog refresh must never replace the playback
        # renderer while its saved tokens are protecting an active capture.
        self._cancel_media_preparation()
        project, bundle = self._open_identity()
        document = self.studio_controller.document
        project_token = self.project_controller.snapshot.token
        studio_token = self.studio_controller.store_token
        if project_token is None:
            self._status = "Save the project before recording."
            self._refresh()
            return

        cycle = document.cycle_range
        cycle_enabled = not first_take and cycle is not None and cycle.enabled
        if cycle_enabled:
            assert cycle is not None
            if self._overdub:
                # Overdub is dialog-free: loop until the musician presses
                # Stop, bounded by the same ceiling the cycle dialog offers.
                cycle_count = _OVERDUB_MAX_PASSES
            else:
                cycle_count, accepted = QInputDialog.getInt(
                    self.shell,
                    "Cycle Recording Passes",
                    "Number of complete takes:",
                    3,
                    2,
                    20,
                    1,
                )
                if not accepted:
                    return
            punch_in = cycle.start_frame
            punch_out = cycle.end_frame
            cycle_start = cycle.start_frame
            cycle_end = cycle.end_frame
        elif first_take:
            maximum_minutes, accepted = QInputDialog.getInt(
                self.shell,
                "First Take Maximum Duration",
                "Maximum recording length in minutes (you can press Stop sooner):",
                _FIRST_TAKE_DEFAULT_MINUTES,
                1,
                _FIRST_TAKE_MAX_MINUTES,
                1,
            )
            if not accepted:
                return
            cycle_count = 1
            punch_in = 0
            punch_out = min(
                maximum_minutes * 60 * PROJECT_AUDIO_SAMPLE_RATE,
                PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
            )
            cycle_start = None
            cycle_end = None
        else:
            if self._overdub:
                self._status = (
                    "Overdub records over a loop. Select a region and use "
                    "Region > Loop Selected Region (or drag a cycle range), "
                    "then press Record — or turn Overdub off for a straight "
                    "punch from the playhead."
                )
                self._refresh()
                return
            cycle_count = 1
            punch_in = self.workspace.arrange.playhead_frame
            punch_out = renderer.timeline_end_frame
            cycle_start = None
            cycle_end = None
        if punch_out <= punch_in:
            self._status = "Move the playhead before the project end to record."
            self._refresh()
            return
        tempo_map = self._tempo_map(project)
        count_in_frames = (
            tempo_map.bar_position_to_frame(2)
            if self._count_in and not first_take
            else 0
        )
        if count_in_frames > punch_in:
            self._status = (
                "This count-in would begin before the project start. Move the "
                "playhead or cycle after the first bar, or turn Count-in off."
            )
            self._refresh()
            return
        try:
            schedule = ProjectRecordingSchedule(
                punch_in_frame=punch_in,
                punch_out_frame=min(
                    punch_out,
                    PROJECT_AUDIO_MAX_OUTPUT_FRAMES,
                ),
                count_in_frames=count_in_frames,
                pre_roll_frames=0,
                cycle_start_frame=cycle_start,
                cycle_end_frame=cycle_end,
                cycle_count=cycle_count,
            )
            armed = tuple(
                ArmedProjectTrack(
                    track_id=item.track_id,
                    channel_map=tuple(
                        channel - 1 for channel in item.input_mapping.channels
                    ),
                    latency_compensation_frames=(self._latency_compensation_frames),
                )
                for item in armed_project_tracks
                if item.input_mapping is not None
            )
            input_channels = max(
                channel + 1 for item in armed for channel in item.channel_map
            )
            device = self._runtime_input_device(next(iter(device_keys)))
            backend = self._input_backend_factory(
                input_channels=input_channels,
                device=device,
            )
            recorder = ProjectMultitrackRecorder(backend)
        except (
            ProjectRecordingError,
            ReferenceStudioApplicationError,
            SongProjectError,
            TypeError,
            ValueError,
        ) as exc:
            self._status = " ".join(str(exc).split())[:600]
            self._refresh()
            return

        capture = (
            Path(tempfile.gettempdir())
            / "WebJam Reference Studio Captures"
            / str(uuid.uuid4())
        )
        self._stop_playback()
        try:
            if first_take:
                self.playback.set_loop(None, None)
                self.playback.set_metronome(False)
            elif cycle_enabled:
                assert cycle is not None
                self.playback.set_loop(cycle.start_frame, cycle.end_frame)
            else:
                self.playback.set_loop(None, None)
            if not first_take:
                self.playback.set_metronome(self._metronome or self._count_in)
            generation = recorder.start(
                capture,
                schedule=schedule,
                tracks=armed,
            )
            if not first_take:
                self.playback.play(
                    start_frame=schedule.cue_start_frame,
                    allow_loop_lead_in=bool(cycle_enabled and schedule.lead_in_frames),
                )
        except (ProjectPlaybackError, ProjectRecordingError, ValueError) as exc:
            try:
                recorder.cancel()
            except ProjectRecordingError:
                pass
            self._remove_recording_capture(capture)
            self._status = " ".join(str(exc).split())[:600]
            self._refresh()
            return

        self._recorder = recorder
        self._recording_backend = backend
        self._recording_generation = generation
        self._recording_project_id = project.project_id
        self._recording_bundle = bundle
        self._recording_temp = capture
        self._recording_source_project = project
        self._recording_source_document = document
        self._recording_project_token = project_token
        self._recording_studio_token = studio_token
        duration_ms = max(
            1,
            math.ceil(
                schedule.scheduled_input_frames * 1_000 / PROJECT_AUDIO_SAMPLE_RATE
            ),
        )
        self._recording_auto_stop.start(duration_ms)
        if not first_take and self._count_in and not self._metronome:
            count_in_ms = math.ceil(count_in_frames * 1_000 / PROJECT_AUDIO_SAMPLE_RATE)

            def end_count_in(item=generation) -> None:
                if self._recording_generation == item and self._is_recording:
                    try:
                        self.playback.set_metronome(False)
                    except ProjectPlaybackError:
                        pass

            QTimer.singleShot(count_in_ms, end_count_in)
        self._status = (
            f"Recording {len(armed)} armed "
            f"{'track' if len(armed) == 1 else 'tracks'}"
            + (
                f" for {cycle_count} cycle passes."
                if cycle_enabled
                else (
                    " as the first take; "
                    + (
                        "it begins immediately because count-in needs an "
                        "existing playable timeline. "
                        if self._count_in
                        else ""
                    )
                    + "Press Stop when finished."
                    if first_take
                    else " until Stop or project end."
                )
            )
        )
        self._refresh()

    @staticmethod
    def _runtime_input_device(device_key: str):
        if device_key == "system-default-input":
            return None
        prefix = "sounddevice-index:"
        if device_key.startswith(prefix):
            value = device_key.removeprefix(prefix)
            try:
                index = int(value)
            except ValueError:
                index = -1
            if index >= 0:
                return index
        raise ReferenceStudioApplicationError(
            "That saved input device is unavailable. Remap the armed tracks."
        )

    def _stop_recording_async(self) -> None:
        if self._recording_commit_future is not None:
            self._status = "The Studio recording is already being verified."
            self._refresh()
            return
        recorder = self._recorder
        if recorder is None or recorder.state is not ProjectRecorderState.RECORDING:
            return
        project = self._recording_source_project
        document = self._recording_source_document
        bundle = self._recording_bundle
        if project is None or document is None or bundle is None:
            self._status = (
                "Recording ownership is incomplete; captured audio was retained."
            )
            self._refresh()
            return
        self._recording_auto_stop.stop()
        self._stop_playback()
        progress = QProgressDialog(
            "Closing WAV files and committing the recording…",
            "",
            0,
            0,
            self.shell,
        )
        progress.setObjectName("ReferenceStudioRecordingProgress")
        progress.setWindowTitle("Finishing Studio Recording")
        progress.setAccessibleName("Studio recording commit progress")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setCancelButton(None)
        self._recording_progress = progress
        progress.show()
        generation = self._recording_generation
        self._status = "Verifying and committing the Studio recording…"
        self._refresh()

        def worker():
            try:
                recorded = recorder.stop()
                if not recorded.published:
                    raise ProjectRecordingError(
                        "The recording did not publish complete temporary WAVs."
                    )
                committed = commit_project_recording(
                    bundle,
                    project,
                    document,
                    recorded,
                    expected_project_token=self._recording_project_token,
                    expected_studio_token=self._recording_studio_token,
                )
                cleaned = self._remove_recording_capture(recorded.output_dir)
                return committed, "", cleaned
            except ProjectRecordingCommitRecoveryRequired:
                cleaned = self._remove_recording_capture(recorded.output_dir)
                return None, "recovery_required", cleaned
            except (
                ProjectRecordingCommitError,
                ProjectRecordingError,
            ) as exc:
                return None, " ".join(str(exc).split())[:600], False
            except Exception:
                return (
                    None,
                    "The recording could not finish safely; temporary audio was retained.",
                    False,
                )

        try:
            future = self._executor.submit(worker)
        except RuntimeError:
            try:
                recorder.cancel()
            except ProjectRecordingError:
                pass
            self._recording_progress = None
            progress.close()
            progress.deleteLater()
            self._recorder = None
            self._recording_backend = None
            self._status = (
                "WebJam couldn't start recording verification. The input was "
                "stopped and temporary capture data was retained."
            )
            self._clear_recording_context()
            self._refresh()
            return
        self._recording_commit_future = future
        future.add_done_callback(
            lambda completed, item=generation, identity=project.project_id: (
                self._recording_completed.emit(item, identity, completed)
            )
        )

    def _accept_recording_completion(
        self,
        generation: int,
        project_id: str,
        future: object,
    ) -> None:
        if generation != self._recording_generation:
            return
        progress = self._recording_progress
        self._recording_progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()
        self._recording_commit_future = None
        self._recorder = None
        self._recording_backend = None
        if self._closed:
            return
        current = self.project_controller.snapshot.project
        if current is None or current.project_id != project_id:
            return
        if not isinstance(future, Future):
            return
        try:
            committed, error, cleaned = future.result()
        except Exception:
            committed, error, cleaned = (
                None,
                "The recording could not finish safely; temporary audio was retained.",
                False,
            )
        if error == "recovery_required":
            self._recording_recovery_pending = True
            self._status = (
                "The recording is protected but needs recovery before more edits."
                + ("" if cleaned else " Temporary capture cleanup needs review.")
            )
            answer = QMessageBox.question(
                self.shell,
                "Resolve Protected Recording",
                self._status + "\n\nResolve it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            self._refresh()
            if answer == QMessageBox.StandardButton.Yes:
                self._recover_recording_async()
            else:
                self._status = (
                    "The protected recording is waiting. Close and reopen this "
                    "project when you are ready to resolve it."
                )
                self._refresh()
            return
        if isinstance(error, str) and error.startswith("recovery_failed:"):
            self._recording_recovery_pending = True
            detail = error.removeprefix("recovery_failed:").strip()
            self._status = detail or ("The protected recording still needs recovery.")
            QMessageBox.warning(
                self.shell,
                "Reference Studio Recording Recovery",
                self._status
                + "\n\nClose and reopen the project to retry without editing it.",
            )
            self._refresh()
            return
        if error or not isinstance(committed, ProjectRecordingCommitResult):
            self._status = error or "The recording did not produce a verified commit."
            QMessageBox.warning(
                self.shell,
                "Reference Studio Recording",
                self._status,
            )
            self._clear_recording_context()
            self._refresh()
            return
        bundle = self._recording_bundle
        if bundle is None or not self._reload_recorded_project(bundle):
            self._status = (
                "The recording was committed, but WebJam couldn't reload its "
                "updated project. Reopen the project from Reference Studio Home."
            )
            self._clear_recording_context()
            self._refresh()
            return
        if committed.state is ProjectRecordingCommitState.ROLLED_BACK:
            self._status = (
                "The interrupted recording was safely rolled back before it "
                "changed the project. No take was added."
            )
            self._clear_recording_context()
            self._refresh()
            return
        dropout_count = (
            sum(len(track.dropouts) for track in committed.evidence.tracks)
            if committed.evidence is not None
            else 0
        )
        action = (
            "Recovered and committed"
            if committed.state is ProjectRecordingCommitState.RECOVERED
            else "Committed"
        )
        self._status = (
            f"{action} {len(committed.imported_media_ids)} recorded "
            f"{'track' if len(committed.imported_media_ids) == 1 else 'tracks'}"
            f" and {len(committed.lane_ids)} alternate take "
            f"{'lane' if len(committed.lane_ids) == 1 else 'lanes'}"
            + (
                f"; {dropout_count} dropout intervals are documented."
                if dropout_count
                else "; no capture dropout intervals were reported."
            )
            + ("" if cleaned else " Temporary capture cleanup needs review.")
            + (
                " Overdub passes are stacked as take lanes: Option-drag a "
                "lane to comp, or use Region > Quick-Swipe Comp."
                if self._overdub and committed.lane_ids
                else ""
            )
        )
        self._clear_recording_context()
        self._refresh()

    def _recover_recording_async(self) -> None:
        bundle = self._recording_bundle
        if bundle is None:
            return
        self._recording_generation += 1
        generation = self._recording_generation
        project_id = self._recording_project_id
        progress = QProgressDialog(
            "Resolving the protected recording transaction…",
            "",
            0,
            0,
            self.shell,
        )
        progress.setWindowTitle("Resolving Studio Recording")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        self._recording_progress = progress

        def worker():
            try:
                result = recover_project_recording_commit(bundle)
                capture = self._recording_temp
                cleaned = bool(
                    capture is None
                    or not capture.exists()
                    or self._remove_recording_capture(capture)
                )
                return result, "", cleaned
            except ProjectRecordingCommitError as exc:
                detail = " ".join(str(exc).split())[:560]
                return None, f"recovery_failed: {detail}", False

        try:
            future = self._executor.submit(worker)
        except RuntimeError:
            self._recording_progress = None
            progress.close()
            progress.deleteLater()
            self._recording_recovery_pending = True
            self._status = (
                "Recording recovery could not start. Close and reopen the project "
                "to retry."
            )
            self._refresh()
            return
        self._recording_commit_future = future
        future.add_done_callback(
            lambda completed, item=generation, identity=project_id: (
                self._recording_completed.emit(item, identity, completed)
            )
        )

    def _reload_recorded_project(self, bundle: Path) -> bool:
        self._cancel_media_preparation()
        self._waveforms.cancel()
        self.workspace.arrange.clear_waveforms()
        try:
            self.studio_controller.unload(discard_dirty=True)
            closed = self.project_controller.close_project(discard_unsaved=True)
            if not closed.closed:
                return False
            self.open_project(bundle, recording_recovery="none")
        except (
            ReferenceStudioApplicationError,
            SongProjectControllerError,
            SongStudioControllerError,
        ):
            self._reset_failed_open()
            return False
        return True

    @staticmethod
    def _remove_recording_capture(path: Path | None) -> bool:
        if path is None:
            return False
        candidate = Path(path)
        expected_parent = Path(tempfile.gettempdir()) / (
            "WebJam Reference Studio Captures"
        )
        try:
            if (
                candidate.parent.resolve() != expected_parent.resolve()
                or str(uuid.UUID(candidate.name)) != candidate.name
            ):
                return False
            shutil.rmtree(candidate)
            try:
                expected_parent.rmdir()
            except OSError:
                pass
            return True
        except (OSError, ValueError):
            return False

    def _clear_recording_context(self) -> None:
        self._recording_auto_stop.stop()
        self._recording_recovery_pending = False
        self._recording_project_id = ""
        self._recording_bundle = None
        self._recording_temp = None
        self._recording_source_project = None
        self._recording_source_document = None
        self._recording_project_token = None
        self._recording_studio_token = None

    def _latency_calibration_dialog(self) -> None:
        if self._reject_recording_change("changing recording calibration"):
            return
        milliseconds = (
            self._latency_compensation_frames * 1_000 / PROJECT_AUDIO_SAMPLE_RATE
        )
        value, accepted = QInputDialog.getDouble(
            self.shell,
            "Studio Latency Compensation",
            "Measured round-trip latency in milliseconds "
            "(positive places recordings earlier):",
            milliseconds,
            -10_000.0,
            10_000.0,
            2,
        )
        if not accepted:
            return
        self._latency_compensation_frames = round(
            value * PROJECT_AUDIO_SAMPLE_RATE / 1_000
        )
        self._status = (
            f"Recording latency compensation set to {value:.2f} ms. "
            "This does not change Jamulus latency or device settings."
        )
        self._refresh()

    def _create_take_lane_truth(self) -> None:
        self._status = (
            "Enable Cycle and press Record to create repeated-take lanes "
            "automatically. This avoids empty or source-less lanes."
        )
        self._refresh()

    def _toggle_metronome(self) -> None:
        if self._reject_recording_change("changing the metronome"):
            return
        self._metronome = not self._metronome
        self.playback.set_metronome(self._metronome)
        self._status = "Metronome on." if self._metronome else "Metronome off."
        self._refresh()

    def _toggle_count_in(self) -> None:
        if self._reject_recording_change("changing count-in"):
            return
        self._count_in = not self._count_in
        self._status = "Count-in on." if self._count_in else "Count-in off."
        self._refresh()

    def _toggle_overdub(self) -> None:
        if self._reject_recording_change("changing overdub"):
            return
        self._overdub = not self._overdub
        if not self._overdub:
            self._status = "Overdub off."
        else:
            document = self.studio_controller.document
            cycle = document.cycle_range
            cycle_ready = cycle is not None and cycle.enabled
            self._status = (
                "Overdub on. Record loops the cycle range and lands each pass "
                "in a new take lane; press Stop when you have enough."
                if cycle_ready
                else "Overdub on. Set a loop first: select a region, use "
                "Region > Loop Selected Region (or drag a cycle range), then "
                "press Record."
            )
        self._refresh()

    def _toggle_cycle(self) -> None:
        if self._reject_recording_change("changing the cycle range"):
            return
        document = self.studio_controller.document
        current = document.cycle_range
        if current is not None and current.enabled:
            updated = None
        else:
            start, end = self.workspace.arrange.visible_frame_range()
            if end <= start:
                return
            updated = StudioCycleRange(start_frame=max(0, start), end_frame=end)
        if self._apply_studio_edit(
            "Changed cycle range",
            lambda item: item.set_cycle_range(updated),
            rebuild=False,
        ):
            try:
                self.playback.set_loop(
                    updated.start_frame if updated is not None else None,
                    updated.end_frame if updated is not None else None,
                )
            except (ProjectPlaybackError, ValueError):
                pass

    def _set_tempo(self, value: float) -> None:
        if self._reject_recording_change("changing tempo"):
            return
        try:
            self.project_controller.set_tempo(value)
            if self._renderer is not None:
                self._stop_playback()
                self.playback.set_renderer(self._renderer, tempo_map=self._tempo_map())
        except (SongProjectControllerError, ProjectPlaybackError) as exc:
            self._status = str(exc)
        else:
            self._status = f"Tempo set to {value:g} BPM."
        self._refresh()

    def _set_time_signature(self, numerator: int, denominator: int) -> None:
        if self._reject_recording_change("changing the time signature"):
            return
        try:
            self.project_controller.set_time_signature(numerator, denominator)
            if self._renderer is not None:
                self._stop_playback()
                self.playback.set_renderer(self._renderer, tempo_map=self._tempo_map())
        except (SongProjectControllerError, ProjectPlaybackError) as exc:
            self._status = str(exc)
        else:
            self._status = f"Time signature set to {numerator}/{denominator}."
        self._refresh()

    def _set_musical_snap(self, value: str) -> None:
        mapping = {
            "off": SnapMode.OFF,
            "bar": SnapMode.MARKERS,
            "beat": SnapMode.MARKERS,
            "eighth": SnapMode.TIME,
            "sixteenth": SnapMode.TIME,
        }
        mode = mapping.get(value)
        if mode is not None:
            self._apply_studio_edit(
                "Changed snap resolution",
                lambda document: document.set_snap_mode(mode),
                rebuild=False,
            )

    def _import_backing_dialog(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self.shell,
            self._profile_text(
                "Import Reference / Backing Track",
                "Import Reference Audio",
            ),
            str(
                Path.home()
                / (
                    "Documents"
                    if self._creator_profile.key == "podcast_voice"
                    else "Music"
                )
            ),
            _AUDIO_FILTER,
        )
        if path:
            self._run_ui_action(lambda: self.import_backing(path))

    def _import_media_dialog(self) -> None:
        paths, _selected = QFileDialog.getOpenFileNames(
            self.shell,
            "Collect Project Media",
            str(Path.home() / "Music"),
            _AUDIO_FILTER,
        )
        for path in paths:
            if path:
                self._run_ui_action(lambda item=path: self.import_media(item))

    def _files_dropped(self, paths: object) -> None:
        if self._reject_recording_change("importing dropped audio"):
            return
        try:
            values = tuple(str(item) for item in paths)
        except TypeError:
            return
        project, _bundle = self._open_identity()
        for index, path in enumerate(values):
            if index == 0 and project.backing_media_id is None:
                self._run_ui_action(lambda item=path: self.import_backing(item))
                project, _bundle = self._open_identity()
            else:
                self._run_ui_action(lambda item=path: self.import_media(item))

    def _new_track_dialog(self) -> None:
        if self._reject_recording_change("adding a track"):
            return
        name, accepted = QInputDialog.getText(
            self.shell,
            "New Audio Track",
            "Track name:",
            text=f"Audio {len(self._open_identity()[0].tracks) + 1}",
        )
        if not accepted:
            return
        try:
            snapshot = self.project_controller.add_track(name)
            assert snapshot.project is not None
            self._apply_studio_edit(
                "Added audio track",
                lambda document: reconcile_song_studio_document(
                    snapshot.project,
                    document,
                ),
            )
        except SongProjectControllerError as exc:
            self._status = str(exc)
            self._refresh()

    def _rename_track_dialog(self) -> None:
        if self._reject_recording_change("renaming a track"):
            return
        track = self._selected_project_track()
        if track is None:
            self._status = "Select an audio track to rename."
            self._refresh()
            return
        name, accepted = QInputDialog.getText(
            self.shell,
            "Rename Track",
            "Track name:",
            text=track.name,
        )
        if not accepted:
            return
        try:
            snapshot = self.project_controller.rename_track(track.track_id, name)
            assert snapshot.project is not None
            self._apply_studio_edit(
                "Renamed track",
                lambda document: reconcile_song_studio_document(
                    snapshot.project,
                    document,
                ),
            )
        except SongProjectControllerError as exc:
            self._status = str(exc)
            self._refresh()

    def _duplicate_track(self) -> None:
        if self._reject_recording_change("duplicating a track"):
            return
        track = self._selected_project_track()
        if track is None:
            self._status = "Select an audio track to duplicate."
            self._refresh()
            return
        try:
            snapshot = self.project_controller.add_track(
                f"{track.name} Copy",
                input_mapping=track.input_mapping,
            )
            assert snapshot.project is not None
            self._apply_studio_edit(
                "Duplicated track settings",
                lambda document: reconcile_song_studio_document(
                    snapshot.project,
                    document,
                ),
            )
        except SongProjectControllerError as exc:
            self._status = str(exc)
            self._refresh()

    def _remove_track(self) -> None:
        if self._reject_recording_change("removing a track"):
            return
        track = self._selected_project_track()
        if track is None:
            self._status = "Select an audio track to remove."
            self._refresh()
            return
        project = self.project_controller.snapshot.project
        assert project is not None
        try:
            candidate = project.remove_track(track.track_id)
            document = reconcile_song_studio_document(
                candidate,
                self.studio_controller.document,
            )
            self.project_controller.replace_project(candidate)
            self._apply_studio_edit(
                "Removed empty audio track",
                lambda _current: document,
            )
        except (SongProjectError, SongStudioReconcileError) as exc:
            self._status = str(exc)
            self._refresh()

    def _arm_selected_track(self) -> None:
        if self._reject_recording_change("changing track arming"):
            return
        track = self._selected_project_track()
        if track is None:
            self._status = "Select an audio track to arm."
            self._refresh()
            return
        try:
            snapshot = self.project_controller.set_track_armed(
                track.track_id,
                not track.armed,
            )
            assert snapshot.project is not None
            self._apply_studio_edit(
                "Armed track" if not track.armed else "Disarmed track",
                lambda document: reconcile_song_studio_document(
                    snapshot.project,
                    document,
                ),
                rebuild=False,
            )
        except SongProjectControllerError as exc:
            self._status = str(exc)
            self._refresh()

    def _map_track_input_dialog(self) -> None:
        if self._reject_recording_change("changing input mapping"):
            return
        track = self._selected_project_track()
        if track is None:
            self._status = "Select an audio track to map."
            self._refresh()
            return
        choices = self._available_input_devices()
        current_key = (
            track.input_mapping.device_key
            if track.input_mapping is not None
            else "system-default-input"
        )
        current_index = next(
            (
                index
                for index, (_label, device_key, _channels) in enumerate(choices)
                if device_key == current_key
            ),
            0,
        )
        labels = [item[0] for item in choices]
        selected_label, selected = QInputDialog.getItem(
            self.shell,
            "Studio Input Device",
            "Recording input device:",
            labels,
            current_index,
            False,
        )
        if not selected:
            return
        selected_choice = next(
            (item for item in choices if item[0] == selected_label),
            choices[0],
        )
        _label, device_key, maximum_channels = selected_choice
        default = (
            ",".join(str(item) for item in track.input_mapping.channels)
            if track.input_mapping is not None
            else "1"
        )
        text, accepted = QInputDialog.getText(
            self.shell,
            "Input Mapping",
            "Interface channel numbers (for example 1 or 1,2):",
            text=default,
        )
        if not accepted:
            return
        try:
            channels = tuple(
                int(item.strip()) for item in text.split(",") if item.strip()
            )
            if len(channels) not in (1, 2):
                raise ValueError(
                    "A Studio track must map one mono channel or two stereo channels."
                )
            if maximum_channels is not None and any(
                channel > maximum_channels for channel in channels
            ):
                raise ValueError(
                    "That input device does not provide every mapped channel."
                )
            mapping = InputMapping(device_key=device_key, channels=channels)
            snapshot = self.project_controller.set_track_input_mapping(
                track.track_id,
                mapping,
            )
            assert snapshot.project is not None
            self._apply_studio_edit(
                "Mapped track input",
                lambda document: reconcile_song_studio_document(
                    snapshot.project,
                    document,
                ),
                rebuild=False,
            )
        except (ValueError, SongProjectError, SongProjectControllerError) as exc:
            self._status = " ".join(str(exc).split())[:600]
            self._refresh()

    @staticmethod
    def _available_input_devices() -> tuple[tuple[str, str, int | None], ...]:
        """Return bounded display labels and stable-enough local runtime keys."""

        default_channels: int | None = None
        discovered: list[tuple[str, str, int | None]] = []
        try:
            import sounddevice as sounddevice_module  # type: ignore

            devices = tuple(sounddevice_module.query_devices())
            try:
                host_apis = tuple(sounddevice_module.query_hostapis())
            except Exception:
                host_apis = ()
            default_device = getattr(sounddevice_module.default, "device", None)
            try:
                default_index = int(default_device[0])
            except (IndexError, TypeError, ValueError):
                try:
                    default_index = int(default_device)
                except (TypeError, ValueError):
                    default_index = -1
            for index, info in enumerate(devices):
                try:
                    input_channels = int(info["max_input_channels"])
                except (KeyError, TypeError, ValueError):
                    continue
                if input_channels <= 0:
                    continue
                if index == default_index:
                    default_channels = input_channels
                name = " ".join(str(info.get("name", "Audio input")).split())
                host_name = ""
                try:
                    host_index = int(info.get("hostapi", -1))
                    if 0 <= host_index < len(host_apis):
                        host_name = " ".join(
                            str(host_apis[host_index].get("name", "")).split()
                        )
                except (AttributeError, TypeError, ValueError):
                    host_name = ""
                detail = f" — {host_name}" if host_name else ""
                label = (
                    f"{name[:120]}{detail[:80]} · device {index} "
                    f"({input_channels} input "
                    f"{'channel' if input_channels == 1 else 'channels'})"
                )
                discovered.append((label, f"sounddevice-index:{index}", input_channels))
        except Exception:
            pass
        default_label = "System default input" + (
            f" ({default_channels} input "
            f"{'channel' if default_channels == 1 else 'channels'})"
            if default_channels is not None
            else ""
        )
        return (
            (default_label, "system-default-input", default_channels),
            *discovered,
        )

    def _selected_region(self) -> StudioRegion | None:
        region_id = self.workspace.arrange.selected_region_id
        if not region_id:
            return None
        try:
            region = self.studio_controller.document.region_for(region_id)
        except StudioProjectError:
            return None
        return region if not region.deleted else None

    def _selected_regions(self) -> tuple[StudioRegion, ...]:
        """Every selected active region in timeline order."""

        document = self.studio_controller.document
        regions = []
        for region_id in self.workspace.arrange.selected_region_ids:
            try:
                region = document.region_for(region_id)
            except StudioProjectError:
                continue
            if not region.deleted:
                regions.append(region)
        if not regions:
            single = self._selected_region()
            if single is not None:
                regions.append(single)
        return tuple(
            sorted(
                regions,
                key=lambda item: (item.timeline_start_frame, item.region_id),
            )
        )

    def _split_selected(self) -> None:
        region = self._selected_region()
        if region is not None:
            self._split_region(region.region_id, self.workspace.arrange.playhead_frame)

    def _join_selected_regions(self) -> None:
        left = self._selected_region()
        if left is None:
            self._status = "Select the first of two adjacent regions to join."
            self._refresh()
            return

        def join(document: StudioDocument) -> StudioDocument:
            first = document.region_for(left.region_id)
            candidates = tuple(
                item
                for item in document.regions
                if item.track_id == first.track_id
                and item.enabled
                and not item.deleted
                and item.region_id != first.region_id
                and item.timeline_start_frame == first.timeline_end_frame
            )
            if len(candidates) != 1:
                raise StudioProjectError(
                    "The selected region needs exactly one adjacent region after it."
                )
            second = candidates[0]
            lane_region_ids = {
                region_id
                for lane in document.take_lanes
                if lane.enabled and not lane.deleted
                for region_id in lane.region_ids
            }
            if (
                first.region_id in lane_region_ids
                or second.region_id in lane_region_ids
            ):
                raise StudioProjectError(
                    "Comp-lane regions cannot be joined; comp or bounce them first."
                )
            if any(
                not item.deleted
                and (
                    item.left_region_id in {first.region_id, second.region_id}
                    or item.right_region_id in {first.region_id, second.region_id}
                )
                for item in document.crossfades
            ):
                raise StudioProjectError(
                    "Remove the regions' crossfade before joining them."
                )
            if (
                first.source_media_id != second.source_media_id
                or first.source_take_id != second.source_take_id
                or first.source_track_id != second.source_track_id
                or first.source_segment_id != second.source_segment_id
                or first.source_end_frame != second.source_start_frame
                or first.fade_out_frames
                or second.fade_in_frames
            ):
                raise StudioProjectError(
                    "Adjacent regions can join only when they are contiguous "
                    "pieces of the same source with no seam fade."
                )
            if (
                first.source_boundary_for_timeline(second.timeline_start_frame)
                != second.source_start_frame
                or first.source_boundary_for_timeline(second.timeline_end_frame)
                != second.source_end_frame
            ):
                raise StudioProjectError(
                    "Those regions use different timing maps and cannot be joined."
                )
            joined = replace(
                first,
                source_frame_count=first.source_frame_count + second.source_frame_count,
                timeline_frame_count=first.timeline_frame_count
                + second.timeline_frame_count,
                mapping_source_start_frame=first.source_start_frame,
                mapping_timeline_start_frame=first.timeline_start_frame,
                mapping_source_frame_count=first.source_frame_count
                + second.source_frame_count,
                mapping_timeline_frame_count=first.timeline_frame_count
                + second.timeline_frame_count,
                fade_out_frames=second.fade_out_frames,
                fade_out_curve=second.fade_out_curve,
            )
            removed = replace(second, enabled=False, deleted=True)
            return replace(
                document,
                revision=document.revision + 1,
                regions=tuple(
                    joined
                    if item.region_id == first.region_id
                    else removed
                    if item.region_id == second.region_id
                    else item
                    for item in document.regions
                ),
            )

        self._apply_studio_edit("Joined adjacent source regions", join)

    def _delete_selected(self) -> None:
        regions = self._selected_regions()
        if not regions:
            return
        if len(regions) == 1:
            self._delete_region(regions[0].region_id)
            return
        region_ids = tuple(item.region_id for item in regions)

        def delete(document: StudioDocument) -> StudioDocument:
            for region_id in region_ids:
                document = document.delete_region(region_id)
            return document

        self._apply_studio_edit(
            f"Deleted {len(region_ids)} regions non-destructively",
            delete,
        )

    def _copy_selected(self) -> None:
        self._clipboard_regions = self._selected_regions()
        count = len(self._clipboard_regions)
        self._status = (
            "Select a region to copy."
            if not count
            else "Region copied."
            if count == 1
            else f"Copied {count} regions."
        )
        self._refresh()

    def _cut_selected(self) -> None:
        regions = self._selected_regions()
        if not regions:
            return
        self._clipboard_regions = regions
        if len(regions) == 1:
            self._delete_region(regions[0].region_id)
            return
        region_ids = tuple(item.region_id for item in regions)

        def cut(document: StudioDocument) -> StudioDocument:
            for region_id in region_ids:
                document = document.delete_region(region_id)
            return document

        self._apply_studio_edit(
            f"Cut {len(region_ids)} regions to the Studio clipboard",
            cut,
        )

    def _paste_region(self) -> None:
        copied = self._clipboard_regions
        if not copied:
            self._status = "Copy a region before pasting."
            self._refresh()
            return
        target = self.workspace.arrange.playhead_frame
        # The earliest copied region lands at the playhead; every other copy
        # keeps its exact relative offset, so a multi-track phrase pastes as
        # one phrase.
        anchor = min(item.timeline_start_frame for item in copied)

        def paste(document: StudioDocument) -> StudioDocument:
            track_ids = {item.track_id for item in document.tracks}
            missing = tuple(item for item in copied if item.track_id not in track_ids)
            if missing:
                raise StudioProjectError(
                    "The copied region's destination track no longer exists."
                    if len(copied) == 1
                    else "A copied region's destination track no longer exists."
                )
            duplicates = []
            for item in copied:
                start = target + (item.timeline_start_frame - anchor)
                delta = start - item.timeline_start_frame
                duplicates.append(
                    replace(
                        item,
                        region_id=str(uuid.uuid4()),
                        timeline_start_frame=start,
                        mapping_timeline_start_frame=(
                            int(item.mapping_timeline_start_frame) + delta
                        ),
                        enabled=True,
                        deleted=False,
                    )
                )
            return replace(
                document,
                revision=document.revision + 1,
                regions=(*document.regions, *duplicates),
            )

        self._apply_studio_edit(
            "Pasted region" if len(copied) == 1 else f"Pasted {len(copied)} regions",
            paste,
        )

    def _loop_selected_region(self) -> None:
        region = self._selected_region()
        if region is None:
            return
        cycle = StudioCycleRange(
            start_frame=region.timeline_start_frame,
            end_frame=region.timeline_end_frame,
        )
        self._apply_studio_edit(
            "Set cycle to selected region",
            lambda document: document.set_cycle_range(cycle),
            rebuild=False,
        )
        try:
            self.playback.set_loop(cycle.start_frame, cycle.end_frame)
        except (ProjectPlaybackError, ValueError):
            pass

    def _quick_swipe_selected_region(self) -> None:
        region = self._selected_region()
        if region is None:
            self._status = "Select a region in an alternate take lane to comp it."
            self._refresh()
            return
        lane = next(
            (
                item
                for item in self.studio_controller.document.take_lanes
                if item.enabled
                and not item.deleted
                and region.region_id in item.region_ids
            ),
            None,
        )
        if lane is None:
            self._status = (
                "That region is not in an alternate take lane. Cycle recording "
                "creates repeated-take lanes automatically."
            )
            self._refresh()
            return
        self._select_comp_range(
            lane.lane_id,
            region.timeline_start_frame,
            region.timeline_end_frame,
        )

    def _add_marker(self, kind: MarkerKind) -> None:
        section = self._creator_profile.vocabulary.section_noun
        default = (
            ("Chapter" if self._creator_profile.key == "podcast_voice" else "Verse")
            if kind is MarkerKind.SECTION
            else "Marker"
        )
        label, accepted = QInputDialog.getText(
            self.shell,
            f"Add {section.title()}" if kind is MarkerKind.SECTION else "Add Marker",
            "Name:",
            text=default,
        )
        if not accepted:
            return
        start = self.workspace.arrange.playhead_frame
        end = (
            start + self.studio_controller.document.project_sample_rate * 8
            if kind is MarkerKind.SECTION
            else None
        )
        marker = StudioMarker(
            marker_id=str(uuid.uuid4()),
            start_frame=start,
            end_frame=end,
            label=label,
            kind=kind,
        )
        self._apply_studio_edit(
            f"Added {section}" if kind is MarkerKind.SECTION else "Added marker",
            lambda document: document.upsert_marker(marker),
            rebuild=False,
        )

    def _select_all_regions(self) -> None:
        self.workspace.arrange.select_all_regions()
        count = len(self.workspace.arrange.selected_region_ids)
        self._status = (
            "There are no regions to select yet."
            if not count
            else "Selected the only region."
            if count == 1
            else f"Selected all {count} regions."
        )
        self._refresh()

    def _project_settings_dialog(self) -> None:
        project, _bundle = self._open_identity()
        name, accepted = QInputDialog.getText(
            self.shell,
            "Project Settings",
            "Project name:",
            text=project.name,
        )
        if not accepted:
            return
        try:
            self.project_controller.rename_project(name)
        except SongProjectControllerError as exc:
            self._status = str(exc)
        else:
            self._status = "Project name updated."
        self._refresh()

    def _show_media_bin(self) -> None:
        project, _bundle = self._open_identity()
        if not project.media:
            body = "No collected media yet."
        else:
            body = "\n".join(
                f"• {item.original_basename} — {item.format}, "
                f"{item.channels} ch, {item.sample_rate} Hz"
                for item in project.media[:200]
            )
        QMessageBox.information(self.shell, "Project Media", body)

    def _show_guide(self) -> None:
        if self._creator_profile.key == "podcast_voice":
            title = "Podcast & Voice Studio Guide"
            body = (
                "1. Create an episode and optionally import reference audio you own or may use.\n"
                "2. Add voice tracks, map interface channels, and arm them.\n"
                "3. Record isolated voices; use Play, Cycle, markers, and chapters.\n"
                "4. Drag regions to edit; split, duplicate, comp, and undo changes.\n"
                "5. Save the episode project, then Bounce when the edit is ready.\n\n"
                f"{STUDIO_MEETING_CAPTURE_NOTICE}"
            )
        elif self._creator_profile.key == "review_rehearsal":
            title = "Review & Rehearsal Preview Guide"
            body = (
                "This Preview supports live review and completed session takes. "
                "Creating or opening local multitrack projects is not available."
            )
        else:
            title = "Reference Studio Guide"
            body = (
                "1. Import a backing track you own or may use.\n"
                "2. Add audio tracks, map interface channels, and arm them.\n"
                "3. Use Play, Click, Count-in, Cycle, markers, and sections.\n"
                "4. Drag regions to arrange; split, duplicate, comp, and undo edits.\n"
                "5. Save the project, then Bounce when your mix is ready.\n\n"
                "Reference Studio audio is separate from Jamulus live settings."
            )
        QMessageBox.information(
            self.shell,
            title,
            body,
        )

    def _show_collect_truth(self) -> None:
        self._status = (
            "Imported project media is already collected inside this portable "
            ".webjam project. Originals remain read-only."
        )
        self._refresh()

    def _toggle_ruler_truth(self) -> None:
        project, _bundle = self._open_identity()
        mode = "bars" if self.workspace.arrange.ruler_mode == "time" else "time"
        self.workspace.arrange.set_ruler_mode(
            mode,
            tempo_bpm=project.tempo_bpm,
            beats_per_bar=project.time_signature.numerator,
            beat_denominator=project.time_signature.denominator,
        )
        self._status = (
            "Arrange ruler now shows bars and beats."
            if mode == "bars"
            else "Arrange ruler now shows elapsed time."
        )
        self._refresh()

    def _save_as_dialog(self) -> None:
        if self._save_as_future is not None:
            self._status = "Save As is already creating a verified project copy."
            self._refresh()
            return
        project, source_bundle = self._open_identity()
        destination, _selected = QFileDialog.getSaveFileName(
            self.shell,
            "Save Reference Studio Project As",
            str(source_bundle.parent / f"{project.name} Copy.webjam"),
            "WebJam Project (*.webjam)",
        )
        if not destination:
            return
        destination_path = self._bundle_destination(destination)
        if not self.save():
            return
        snapshot = self.project_controller.snapshot
        if (
            snapshot.project is None
            or snapshot.bundle_path is None
            or snapshot.token is None
        ):
            self._status = "Save As requires a clean, verified source project."
            self._refresh()
            return
        studio_token = self.studio_controller.store_token
        if studio_token is None:
            self._status = "Save As requires a saved Studio arrangement."
            self._refresh()
            return
        source_project = snapshot.project
        source_document = self.studio_controller.document
        self._save_as_generation += 1
        generation = self._save_as_generation

        progress = QProgressDialog(
            "Copying collected media and verifying the new project…",
            "",
            0,
            0,
            self.shell,
        )
        progress.setObjectName("ReferenceStudioSaveAsProgress")
        progress.setWindowTitle("Saving Reference Studio Project As")
        progress.setAccessibleName("Save As project-copy progress")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setCancelButton(None)
        self._save_as_progress = progress
        progress.show()
        self._status = (
            "Creating a complete verified project copy. Keep WebJam open until "
            "this finishes."
        )
        self._refresh()

        def worker():
            try:
                result = save_song_studio_project_as(
                    snapshot.bundle_path,
                    destination_path,
                    source_project,
                    source_document,
                    expected_project_token=snapshot.token,
                    expected_studio_token=studio_token,
                )
                return result, ""
            except (SongStudioSaveAsConflict, SongStudioSaveAsError) as exc:
                return None, " ".join(str(exc).split())[:600]
            except Exception:
                return (
                    None,
                    "WebJam couldn't create a complete verified project copy.",
                )

        future = self._executor.submit(worker)
        self._save_as_future = future
        future.add_done_callback(
            lambda completed, item=generation, identity=source_project.project_id: (
                self._save_as_completed.emit(item, identity, completed)
            )
        )

    def _accept_save_as(
        self,
        generation: int,
        source_project_id: str,
        future: object,
    ) -> None:
        if generation != self._save_as_generation:
            return
        progress = self._save_as_progress
        self._save_as_progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()
        self._save_as_future = None
        if self._closed:
            return
        current = self.project_controller.snapshot.project
        if current is None or current.project_id != source_project_id:
            return
        if not isinstance(future, Future):
            return
        try:
            result, error = future.result()
        except Exception:
            result, error = (
                None,
                "WebJam couldn't create a complete verified project copy.",
            )
        if error or not isinstance(result, SongStudioSaveAsResult):
            self._status = error or "Save As did not produce a verified project copy."
            QMessageBox.warning(self.shell, "Reference Studio Save As", self._status)
            self._refresh()
            return

        source_bundle = self.project_controller.snapshot.bundle_path
        self._cancel_offline_tools()
        self._cancel_media_preparation()
        self._stop_playback()
        self._waveforms.cancel()
        self.workspace.arrange.clear_waveforms()
        try:
            self.studio_controller.unload()
            closed = self.project_controller.close_project()
            if not closed.closed:
                raise ReferenceStudioApplicationError(
                    "The source project could not close after Save As."
                )
            self.open_project(result.bundle_path)
        except (
            ReferenceStudioApplicationError,
            SongProjectControllerError,
            SongStudioControllerError,
        ):
            reopened = False
            if source_bundle is not None:
                try:
                    if self.project_controller.snapshot.is_open:
                        self.project_controller.close_project(discard_unsaved=True)
                    if self.studio_controller.bundle_path is not None:
                        self.studio_controller.unload(discard_dirty=True)
                    self.open_project(source_bundle)
                    reopened = True
                except (
                    ReferenceStudioApplicationError,
                    SongProjectControllerError,
                    SongStudioControllerError,
                ):
                    self._reset_failed_open()
            self._status = (
                "The verified project copy was created, but WebJam couldn't switch "
                + (
                    "to it; the source project was reopened."
                    if reopened
                    else "projects. Open the new copy from Reference Studio Home."
                )
            )
            QMessageBox.warning(
                self.shell,
                "Reference Studio Save As",
                self._status,
            )
            self._refresh()
            return
        self._status = (
            f"Saved and switched to project copy “{result.bundle_path.stem}”."
        )
        self._refresh_recents()
        self._refresh()

    def _relink_backing_dialog(self) -> None:
        project, _bundle = self._open_identity()
        if project.backing_media_id is None:
            self._status = self._profile_text(
                "This project has no backing track to relink.",
                "This episode has no reference audio to relink.",
            )
            self._refresh()
            return
        path, _selected = QFileDialog.getOpenFileName(
            self.shell,
            self._profile_text(
                "Relink Missing Backing Track",
                "Relink Missing Reference Audio",
            ),
            str(
                Path.home()
                / (
                    "Documents"
                    if self._creator_profile.key == "podcast_voice"
                    else "Music"
                )
            ),
            _AUDIO_FILTER,
        )
        if not path:
            return
        try:
            self.project_controller.relink_backing_media(path)
            self._status = self._profile_text(
                "Backing track relinked and verified.",
                "Reference audio relinked and verified.",
            )
            self._prepare_media_async()
        except SongProjectControllerError as exc:
            self._status = str(exc)
        self._refresh()


__all__ = [
    "ReferenceStudioApplicationController",
    "ReferenceStudioApplicationError",
]
