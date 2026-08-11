"""Integrated multitrack recording and playback workspace.

The server recorder already captures one synchronized file per musician.  This
widget makes that capability feel like part of WebJam instead of an external
server feature: live armed lanes become recorded waveform lanes, and the same
screen provides transport plus per-track gain/mute/solo controls.
"""

from __future__ import annotations

from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
import logging
import queue
import threading
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.audio_routing import list_output_devices
from core.musician_guidance import (
    MusicianGuidanceSnapshot,
    StudioGuidanceFacts,
)
from core.studio_controller import StudioProjectController
from core.studio_export import (
    StudioExportPublishedError,
    StudioExportResult,
    export_studio_arrangement,
    studio_export_supported,
)
from core.take_export import (
    TrackExportResult,
    TrackMixSettings,
    export_track_package,
)
from core.take_library import TakeInfo, TakeValidationResult, discover_takes
from core.take_player import (
    PlaybackDeviceError,
    PlaybackError,
    StudioPlaybackPreparation,
    StudioPlaybackSourceError,
    SoundDeviceSink,
    TakePlayer,
)
from core.take_project import TakeProject, TakeProjectError, load_take_project
from core.studio_project import StudioDocument, default_studio_document
from core.studio_source_catalog import StudioSourceCatalog
from webjam_qt.widgets.studio_arrangement_workflow import (
    StudioArrangementWorkflowMixin,
    _selectable_track_export_track_ids,
    _take_requires_studio_document,
)
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.accessible import set_labeled_action
from webjam_qt.widgets.studio_arrange import StudioArrange
from webjam_qt.widgets.studio_editing import StudioEditingToolbar
from webjam_qt.widgets.studio_review import (
    TRACK_LANE_HEADER_WIDTH,
    StudioTimelineRuler,
    TrackLane,
    TrackLevelMeter,
    _CompactComboBox,
    _CompositeWaveformSpec,
    _WaveformBuildCancelled,
    _WaveformPeakCache,
    _WaveformSegmentSpec,
    _WaveformSourceKey,
    _composite_waveform_key,
    _composite_waveform_peaks,
    _fmt_db,
    _fmt_time,
    _is_synchronized_source,
    _timeline_gaps_for_track,
    _waveform_peaks,
    _waveform_source_key,
    _waveform_spec_for_track,
)
from webjam_qt.widgets.studio_waveforms import (
    StudioWaveformCoordinator,
    StudioWaveformCoordinatorError,
    StudioWaveformRegionError,
    StudioWaveformRegionTile,
)

LOGGER = logging.getLogger("webjam.qt.recording_studio")

__all__ = [
    "RecordingStudio",
    "_CompositeWaveformSpec",
    "_WaveformPeakCache",
    "_WaveformSegmentSpec",
    "_composite_waveform_peaks",
    "_waveform_peaks",
    "_waveform_source_key",
]


def _take_review_message(*, has_errors: bool, has_warnings: bool) -> str:
    """Return fixed musician-facing copy; findings stay in the take manifest."""
    if has_errors:
        return (
            "This take needs review. Listen to each track before export, then "
            "record a short test take."
        )
    if has_warnings:
        return (
            "Take saved with something to review. Listen to each track before export."
        )
    return "Take verified and ready to mix or export."


def _track_export_failure_message(error: str) -> str:
    """Return safe, musician-facing copy for a failed track export.

    Export workers can surface implementation exceptions containing local paths
    or other diagnostic details.  A small, fixed allowlist preserves the two
    recording-safety actions that a musician can resolve in Studio while every
    other failure remains a general retry message.
    """
    message = (error or "").strip()
    if message.startswith(
        "WebJam found explicitly silent segments in selected performance tracks:"
    ):
        return (
            "Track export paused: a selected performance track has an explicitly "
            "silent segment. Review the take, or intentionally deselect each "
            "affected track and export again. The original take is safe."
        )
    if message.startswith(
        "WebJam cannot create a timing-ready track export because these "
        "local originals have no verified timeline alignment:"
    ):
        return (
            "Track export paused: selected local originals have no verified "
            "timeline alignment. Keep the Jamulus server track for this take, "
            "or align and verify each local original before exporting. The "
            "original take is safe."
        )
    return (
        "Track export couldn't be completed. The original take is safe. "
        "Check available disk space and folder access, then try again."
    )


def _studio_export_failure_message(error: str) -> str:
    """Explain a Studio-export failure without implying a fallback exists."""

    message = (error or "").strip()
    if message.startswith(
        "WebJam found explicitly silent segments in selected performance tracks:"
    ) or message.startswith(
        "WebJam cannot create a timing-ready track export because these "
        "local originals have no verified timeline alignment:"
    ):
        return _track_export_failure_message(message)
    return (
        "Studio export couldn't be completed, and WebJam did not create an "
        "aligned-originals fallback. The original take and saved Studio choices "
        "are safe. Review the take, then retry the Studio export."
    )


def _studio_document_differs_from_default(
    document: StudioDocument,
    project: TakeProject,
) -> bool:
    """Compare semantic Studio state while ignoring bookkeeping revisions."""

    current = document.to_dict()
    expected = default_studio_document(project).to_dict()
    current.pop("revision", None)
    expected.pop("revision", None)
    return current != expected


@dataclass(frozen=True)
class _ExportWorkerOutcome:
    """One worker result tagged so stale take callbacks can be discarded."""

    generation: int
    take_path: Path
    result: TrackExportResult | StudioExportResult | None
    error: str = ""
    published_folder: Path | None = None
    aligned_originals_only: bool = False
    studio_export_attempted: bool = False


@dataclass(frozen=True)
class _PlaybackPreparationOutcome:
    """One descriptor-bound playback preparation tagged to its take view."""

    generation: int
    take_path: Path
    preparation: StudioPlaybackPreparation | None
    error: PlaybackError | None = None


class RecordingStudio(StudioArrangementWorkflowMixin, QWidget):
    """A single in-app home for recording, takes, waveforms, and rough mixes."""

    record_requested = Signal()
    return_live_requested = Signal()
    live_fader_changed = Signal(int, int)
    live_mute_toggled = Signal(int, bool)
    live_solo_toggled = Signal(int, bool)
    output_device_changed = Signal(str)
    recording_setup_requested = Signal()
    # The controller owns the session conductor and pilot evidence.  Studio
    # only reports its actual export worker boundary; it never declares a
    # external-editor import, alignment, or musician review successful on its own.
    export_started = Signal()
    export_finished = Signal(bool)
    guidance_changed = Signal()

    def __init__(
        self,
        takes_dir: str = "",
        *,
        player: Optional[TakePlayer] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RecordingStudio")
        self.setAccessibleName("Multitrack Studio")
        self._takes_dir = str(takes_dir or "")
        self._takes: list[TakeInfo] = []
        self._current: Optional[TakeInfo] = None
        self._live_participants: list = []
        self._live_signature: tuple = ()
        self._lanes: dict[int, TrackLane] = {}
        self._track_info_by_channel: dict[int, object] = {}
        self._selected_channel_id: int | None = None
        self._excluded_track_export_track_ids: dict[Path, set[str]] = {}
        # Schema-v2 mix choices live in a separate, durable sidecar.  They are
        # deliberately never written into recording evidence or source media.
        self._studio_state: StudioDocument | None = None
        self._studio_state_take_path: Path | None = None
        self._studio_state_token: str | None = None
        self._studio_state_dirty = False
        self._studio_state_error = ""
        self._studio_persistence_failed = False
        self._studio_project: TakeProject | None = None
        self._studio_source_catalog: StudioSourceCatalog | None = None
        self._studio_audition_lane_id: str | None = None
        self._studio_autosave_generation = 0
        self._studio_mix_gesture_serial = 0
        self._studio_mix_merge_keys: dict[tuple[int, str], str] = {}
        self._master_gain_merge_key: str | None = None
        self._studio_controller = StudioProjectController(
            autosave_requested=self._schedule_studio_autosave,
        )
        self._pending_levels: dict[int, tuple[int, float]] = {}
        self._pending_stereo_levels: dict[int, tuple[int, float, float, bool]] = {}
        self._pending_master_level: tuple[int, float, float, bool] | None = None
        self._level_lock = threading.Lock()
        # Sticky overload latch: once a lane or the master clips during one
        # playback epoch, it stays lit until the epoch changes (transport
        # start or seek), so a single mid-take clip is not lost to the next
        # UI tick. Reset per epoch so a fresh pass reads clean.
        self._overload_epoch = -1
        self._overloaded_lanes: set[int] = set()
        self._master_overloaded = False
        self._pending_finished_epoch: int | None = None
        self._pending_playback_error: tuple[int, PlaybackError] | None = None
        self._playback_prepare_results: queue.SimpleQueue[
            _PlaybackPreparationOutcome
        ] = queue.SimpleQueue()
        self._playback_prepare_generation = 0
        self._playback_prepare_cancel = threading.Event()
        self._playback_prepare_future: Future | None = None
        self._playback_preparing = False
        self._playback_prepare_autoplay = False
        self._playback_prepare_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="webjam-playback-prepare",
        )
        self._export_results: queue.SimpleQueue[_ExportWorkerOutcome] = (
            queue.SimpleQueue()
        )
        self._export_generation = 0
        self._export_cancel = threading.Event()
        self._export_thread: threading.Thread | None = None
        self._exporting = False
        self._reveal_path: Optional[Path] = None
        self._local_originals_path: Optional[Path] = None
        self._recording_elapsed = 0.0
        self._recording = False
        self._can_record = True
        self._phase_name = "idle"
        self._phase_label = "READY"
        self._shared_guidance_text = ""
        self._viewing_live = True
        self._guidance_take_revision = 0
        self._last_guidance_facts: StudioGuidanceFacts | None = None
        self._compact_inspector_open = False
        self._compact_inspector_library_was_visible = True
        self._waveform_cache = _WaveformPeakCache()
        self._waveform_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="webjam-waveform",
        )
        self._studio_waveforms = StudioWaveformCoordinator(
            self._waveform_executor,
            publish_tile=self._publish_studio_waveform_tile,
            publish_error=self._publish_studio_waveform_error,
        )
        self._studio_waveform_document_key: object | None = None
        self._studio_waveform_error_generation: int | None = None
        self._waveform_generation = 0
        self._waveform_cancel = threading.Event()
        self._waveform_futures: set[Future] = set()
        self._waveform_futures_lock = threading.Lock()
        self._waveform_results: queue.SimpleQueue[
            tuple[
                int,
                int,
                Path,
                _WaveformSourceKey,
                tuple[float, ...],
            ]
        ] = queue.SimpleQueue()
        self._waveform_shutdown = False
        self._player = player or TakePlayer(sink=SoundDeviceSink())
        # Studio consumes epoch-tagged notifications so a callback from a
        # stopped or replaced take can never mutate the next playback run.
        self._player._on_levels = None
        self._player._on_stereo_levels = None
        self._player._on_master_level = None
        self._player._on_finished = None
        self._player._on_error = None
        self._player._on_levels_epoch = self._on_levels_bg
        self._player._on_stereo_levels_epoch = self._on_stereo_levels_bg
        self._player._on_master_level_epoch = self._on_master_level_bg
        self._player._on_finished_epoch = self._on_finished_bg
        self._player._on_error_epoch = self._on_playback_error_bg

        self._build_ui()
        self._studio_waveform_schedule_timer = QTimer(self)
        self._studio_waveform_schedule_timer.setSingleShot(True)
        self._studio_waveform_schedule_timer.setInterval(30)
        self._studio_waveform_schedule_timer.timeout.connect(
            self._schedule_studio_waveforms
        )
        self._studio_state_save_timer = QTimer(self)
        self._studio_state_save_timer.setSingleShot(True)
        self._studio_state_save_timer.setInterval(350)
        self._studio_state_save_timer.timeout.connect(self._flush_studio_state)
        self.reload()
        self._show_live_session()

        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        # The Inspector is removed below 1080 px.  Without this dynamic
        # constraint Qt caches the wider three-panel minimum on a top-level
        # Studio and refuses to shrink it back to the supported compact size.
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.LG)
        root.setSpacing(Space.MD)

        top = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        eyebrow = QLabel("MULTITRACK STUDIO")
        eyebrow.setObjectName("StudioEyebrow")
        self._title = QLabel("Record the whole band")
        self._title.setObjectName("StudioTitle")
        self._subtitle = QLabel(
            "Every connected musician lands on a separate synchronized track."
        )
        self._subtitle.setObjectName("StudioSubtitle")
        self._subtitle.setWordWrap(True)
        self._subtitle.setMinimumWidth(0)
        title_block.addWidget(eyebrow)
        title_block.addWidget(self._title)
        title_block.addWidget(self._subtitle)
        top.addLayout(title_block, 1)
        self._live_btn = QPushButton("Back to Live")
        self._live_btn.setObjectName("GhostButton")
        self._live_btn.setAccessibleName("Return to live room")
        self._live_btn.clicked.connect(self.return_live_requested.emit)
        top.addWidget(self._live_btn)
        self._setup_btn = QPushButton("Setup")
        self._setup_btn.setObjectName("GhostButton")
        self._setup_btn.setAccessibleName("Open recording setup")
        self._setup_btn.clicked.connect(self.recording_setup_requested.emit)
        top.addWidget(self._setup_btn)
        self._record_btn = QPushButton("● Record Session")
        self._record_btn.setObjectName("StudioRecordButton")
        self._record_btn.setAccessibleName("Record Session")
        self._record_btn.clicked.connect(self.record_requested.emit)
        top.addWidget(self._record_btn)
        root.addLayout(top)

        self._phase = QLabel("READY")
        self._phase.setObjectName("StudioPhase")
        self._phase.setAccessibleName("Multitrack recorder status")
        root.addWidget(self._phase)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        library = QFrame()
        self._library = library
        library.setObjectName("StudioLibrary")
        library.setMinimumWidth(160)
        library.setMaximumWidth(240)
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        library_layout.setSpacing(Space.SM)
        library_title = QLabel("Takes")
        library_title.setObjectName("StudioSectionTitle")
        library_layout.addWidget(library_title)
        self._take_list = QListWidget()
        self._take_list.setObjectName("StudioTakeList")
        self._take_list.setAccessibleName("Studio take library")
        self._take_list.currentRowChanged.connect(self._on_take_selected)
        library_layout.addWidget(self._take_list, 1)
        self._new_take_btn = QPushButton("＋ New live take")
        self._new_take_btn.setObjectName("GhostButton")
        self._new_take_btn.setAccessibleName("Start a new live take")
        self._new_take_btn.clicked.connect(self._show_live_session)
        library_layout.addWidget(self._new_take_btn)
        splitter.addWidget(library)

        editor = QFrame()
        editor.setObjectName("StudioEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        editor_layout.setSpacing(Space.SM)

        transport = QHBoxLayout()
        self._play_btn = QPushButton("▶ Play")
        self._play_btn.setObjectName("AudioButton")
        self._play_btn.setAccessibleName("Play or pause the selected take")
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setObjectName("GhostButton")
        self._stop_btn.setAccessibleName("Stop Studio playback")
        self._stop_btn.clicked.connect(self._stop_playback)
        self._inspector_btn = QPushButton("Track Details")
        self._inspector_btn.setObjectName("GhostButton")
        self._inspector_btn.setCheckable(True)
        self._inspector_btn.setAccessibleName("Show track details")
        self._inspector_btn.setAccessibleDescription(
            "At compact window sizes, open track details in place of the take "
            "library. Activate again to restore the library."
        )
        self._inspector_btn.setToolTip(
            "Show track details while keeping the Arrange editor usable."
        )
        self._inspector_btn.toggled.connect(self._set_compact_inspector_open)
        self._inspector_btn.setVisible(False)
        self._position = QLabel("0:00 / 0:00")
        self._position.setObjectName("StudioPosition")
        self._scrub = QSlider(Qt.Orientation.Horizontal)
        self._scrub.setRange(0, 1000)
        self._scrub.setAccessibleName("Selected take playhead")
        self._scrub.sliderPressed.connect(lambda: setattr(self, "_scrubbing", True))
        self._scrub.sliderReleased.connect(self._seek_from_scrub)
        self._scrubbing = False
        transport.addWidget(self._play_btn)
        transport.addWidget(self._stop_btn)
        transport.addWidget(self._inspector_btn)
        transport.addWidget(self._position)
        transport.addWidget(self._scrub, 1)
        editor_layout.addLayout(transport)

        master = QHBoxLayout()
        master.setContentsMargins(0, 0, 0, 0)
        master.setSpacing(Space.SM)
        self._master_label = QLabel("MASTER")
        self._master_label.setObjectName("StudioSectionTitle")
        self._master_meter = TrackLevelMeter()
        self._master_meter.setAccessibleName("Stereo master peak level")
        self._master_gain = QSlider(Qt.Orientation.Horizontal)
        self._master_gain.setRange(0, 400)
        self._master_gain.setValue(100)
        self._master_gain.setMaximumWidth(120)
        self._master_gain.setAccessibleName("Studio master gain")
        self._master_gain.setToolTip("Master output gain")
        self._master_gain_value = QLabel(_fmt_db(1.0))
        self._master_gain_value.setObjectName("StudioGainValue")
        self._master_gain_value.setFixedWidth(46)
        self._master_limiter = QCheckBox("Limiter")
        self._master_limiter.setChecked(True)
        self._master_limiter.setAccessibleName("Enable Studio master limiter")
        self._master_limiter.setToolTip(
            "Keep the playback and rough-mix bus at full scale."
        )
        self._master_gain.valueChanged.connect(self._on_master_gain_changed)
        self._master_gain.sliderPressed.connect(self._begin_master_gain_gesture)
        self._master_gain.sliderReleased.connect(self._end_master_gain_gesture)
        self._master_limiter.toggled.connect(self._on_master_limiter_changed)
        self._reset_mix_btn = QPushButton("Reset Mix")
        self._reset_mix_btn.setObjectName("GhostButton")
        self._reset_mix_btn.setAccessibleName(
            "Reset every track and master mix control to default"
        )
        self._reset_mix_btn.setToolTip(
            "Return trim, volume, pan, mute, solo, and master to defaults.\n"
            "Export choices are kept. One undo restores the mix."
        )
        self._reset_mix_btn.clicked.connect(self._on_reset_mix)
        master.addWidget(self._master_label)
        master.addWidget(self._master_meter)
        master.addWidget(self._master_gain)
        master.addWidget(self._master_gain_value)
        master.addWidget(self._master_limiter)
        master.addWidget(self._reset_mix_btn)
        master.addStretch(1)
        editor_layout.addLayout(master)

        self._arrange_toolbar = StudioEditingToolbar(
            context_provider=self._arrange_editing_context,
            apply_edit=lambda label, edit, *, reload_audio: self._perform_arrange_edit(
                label,
                edit,
                reload_audio=reload_audio,
            ),
            name_prompt=lambda title, default: self._prompt_arrangement_name(
                title,
                default,
            ),
        )
        # Preserve the established private widget names used by focused UI
        # tests and downstream desktop automation.
        self._add_marker_btn = self._arrange_toolbar.add_marker_button
        self._add_section_btn = self._arrange_toolbar.add_section_button
        self._cycle_region_btn = self._arrange_toolbar.cycle_region_button
        self._region_fades_btn = self._arrange_toolbar.region_fades_button
        self._crossfade_btn = self._arrange_toolbar.crossfade_button
        editor_layout.addWidget(self._arrange_toolbar)

        self._comp_toolbar = QWidget()
        self._comp_toolbar.setObjectName("StudioCompToolbar")
        self._comp_toolbar.setAccessibleName("Studio take lane and comp controls")
        comp_actions = QHBoxLayout(self._comp_toolbar)
        comp_actions.setContentsMargins(0, 0, 0, 0)
        comp_actions.setSpacing(Space.SM)
        comp_label = QLabel("TAKE LANES")
        comp_label.setObjectName("StudioSectionTitle")
        comp_actions.addWidget(comp_label)
        self._add_take_lane_btn = QPushButton("＋ Add Take")
        self._add_take_lane_btn.setObjectName("GhostButton")
        self._add_take_lane_btn.setAccessibleName(
            "Add a matching repeated take to the selected track"
        )
        self._add_take_lane_btn.setToolTip(
            "Add another recording from this session as a non-destructive take lane."
        )
        self._add_take_lane_btn.clicked.connect(self._show_add_take_lane_menu)
        comp_actions.addWidget(self._add_take_lane_btn)
        self._audition_take_lane_btn = QPushButton("Audition")
        self._audition_take_lane_btn.setObjectName("GhostButton")
        self._audition_take_lane_btn.setAccessibleName("Audition selected take lane")
        self._audition_take_lane_btn.setToolTip(
            "Hear the selected take lane across all of its recorded ranges."
        )
        self._audition_take_lane_btn.clicked.connect(
            self._toggle_selected_take_lane_audition
        )
        comp_actions.addWidget(self._audition_take_lane_btn)
        self._remove_take_lane_btn = QPushButton("Remove Lane")
        self._remove_take_lane_btn.setObjectName("GhostButton")
        self._remove_take_lane_btn.setAccessibleName("Remove selected take lane")
        self._remove_take_lane_btn.setToolTip(
            "Remove this lane and its comp selections without changing recordings."
        )
        self._remove_take_lane_btn.clicked.connect(self._remove_selected_take_lane)
        comp_actions.addWidget(self._remove_take_lane_btn)
        self._comp_help = QLabel(
            "Option/Alt-drag a lane to comp · double-click its name to audition"
        )
        self._comp_help.setObjectName("StudioHint")
        self._comp_help.setWordWrap(True)
        comp_actions.addWidget(self._comp_help, 1)
        self._comp_toolbar.setVisible(False)
        editor_layout.addWidget(self._comp_toolbar)

        actions = QHBoxLayout()
        self._output_label = QLabel("Playback output")
        actions.addWidget(self._output_label)
        self._output_picker = _CompactComboBox()
        self._output_picker.setObjectName("StudioOutputPicker")
        self._output_picker.setAccessibleName("Studio playback output")
        self._output_picker.setMaximumWidth(220)
        self._populate_output_devices()
        self._output_picker.currentIndexChanged.connect(self._on_output_changed)
        actions.addWidget(self._output_picker, 1)
        actions.addStretch(1)
        self._export_btn = QPushButton("Export Tracks")
        self._export_btn.setObjectName("PrimaryButton")
        self._export_btn.setAccessibleName("Export aligned tracks")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_tracks)
        actions.addWidget(self._export_btn)
        self._reveal_btn = QPushButton("Show Take")
        self._reveal_btn.setObjectName("GhostButton")
        self._reveal_btn.setAccessibleName("Show selected take folder")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal_current)
        actions.addWidget(self._reveal_btn)
        self._originals_btn = QPushButton("Show My Originals")
        self._originals_btn.setObjectName("GhostButton")
        self._originals_btn.setAccessibleName("Show preserved Local Originals folder")
        self._originals_btn.setToolTip(
            "Open the folder containing this Mac's preserved, unchanged recordings."
        )
        self._originals_btn.setVisible(False)
        self._originals_btn.clicked.connect(self._reveal_local_originals)
        actions.addWidget(self._originals_btn)
        editor_layout.addLayout(actions)

        self._legacy_timeline = QWidget()
        timeline = QHBoxLayout(self._legacy_timeline)
        timeline.setContentsMargins(0, 0, 0, 0)
        timeline.setSpacing(Space.SM)
        self._timeline_gutter = QLabel("TRACKS")
        self._timeline_gutter.setObjectName("StudioTimelineGutter")
        self._timeline_gutter.setFixedWidth(TRACK_LANE_HEADER_WIDTH + Space.SM)
        self._timeline_ruler = StudioTimelineRuler()
        self._timeline_ruler.seek_requested.connect(self._seek_from_ruler)
        timeline.addWidget(self._timeline_gutter)
        timeline.addWidget(self._timeline_ruler, 1)
        editor_layout.addWidget(self._legacy_timeline)

        self._playback_controls = (
            self._play_btn,
            self._stop_btn,
            self._inspector_btn,
            self._position,
            self._scrub,
            self._output_label,
            self._output_picker,
            self._master_label,
            self._master_meter,
            self._master_gain,
            self._master_gain_value,
            self._master_limiter,
            self._export_btn,
            self._reveal_btn,
        )
        self._master_controls = (
            self._master_label,
            self._master_meter,
            self._master_gain,
            self._master_gain_value,
            self._master_limiter,
            self._reset_mix_btn,
        )
        self._comp_controls = (
            self._comp_toolbar,
            self._add_take_lane_btn,
            self._audition_take_lane_btn,
            self._remove_take_lane_btn,
        )

        self._track_scroll = QScrollArea()
        self._track_scroll.setObjectName("StudioTrackScroll")
        self._track_scroll.setWidgetResizable(True)
        self._track_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Keep the one-lane mixer usefully visible alongside the compact
        # embedded Arrange canvas.  QSplitter otherwise permits it to shrink
        # below the controls needed to review a selected track.
        self._track_scroll.setMinimumHeight(88)
        self._track_container = QWidget()
        self._track_layout = QVBoxLayout(self._track_container)
        self._track_layout.setContentsMargins(0, 0, 0, 0)
        self._track_layout.setSpacing(Space.XS)
        self._track_layout.addStretch(1)
        self._track_scroll.setWidget(self._track_container)
        self._studio_arrange = StudioArrange()
        # The embedded Studio also keeps a one-lane mixer visible.  A 150 px
        # canvas still exposes the ruler plus multiple Arrange rows at the
        # supported 760x600 floor without forcing the mixer or status hint to
        # overlap it.  Standalone Arrange surfaces retain their larger hint.
        self._studio_arrange.setMinimumSize(480, 150)
        self._studio_arrange.setVisible(False)
        self._connect_studio_arrange()
        self._arrange_mixer_splitter = QSplitter(Qt.Orientation.Vertical)
        self._arrange_mixer_splitter.setChildrenCollapsible(False)
        self._arrange_mixer_splitter.setHandleWidth(2)
        self._arrange_mixer_splitter.addWidget(self._studio_arrange)
        self._arrange_mixer_splitter.addWidget(self._track_scroll)
        self._arrange_mixer_splitter.setStretchFactor(0, 3)
        self._arrange_mixer_splitter.setStretchFactor(1, 1)
        self._arrange_mixer_splitter.setSizes([520, 180])
        editor_layout.addWidget(self._arrange_mixer_splitter, 1)

        self._hint = QLabel("")
        self._hint.setObjectName("StudioHint")
        self._hint.setWordWrap(True)
        self._arrange_toolbar.hint_requested.connect(self._hint.setText)
        editor_layout.addWidget(self._hint)
        splitter.addWidget(editor)

        inspector = QFrame()
        self._inspector = inspector
        inspector.setObjectName("StudioInspector")
        # Compact mode swaps this drawer for the take library.  Its narrower
        # floor leaves the editor's 480 px canvas usable at 760 px overall.
        inspector.setMinimumWidth(176)
        inspector.setMaximumWidth(260)
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        inspector_layout.setSpacing(Space.SM)
        inspector_title = QLabel("Track details")
        inspector_title.setObjectName("StudioInspectorTitle")
        inspector_layout.addWidget(inspector_title)
        self._inspector_values: dict[str, QLabel] = {}
        for key, label in (
            ("status", "STATUS"),
            ("source", "SOURCE"),
            ("timeline", "TIMELINE"),
            ("alignment", "ALIGNMENT"),
            ("gaps", "GAPS"),
            ("export", "EXPORT"),
        ):
            field = QLabel(label)
            field.setObjectName("StudioInspectorField")
            value = QLabel("—")
            value.setObjectName("StudioInspectorValue")
            value.setWordWrap(True)
            inspector_layout.addWidget(field)
            inspector_layout.addWidget(value)
            self._inspector_values[key] = value
        inspector_layout.addStretch(1)
        splitter.addWidget(inspector)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([220, 900, 230])
        root.addWidget(splitter, 1)
        self._set_empty_inspector()
        self._update_inspector_visibility()

        scrollbar = self._track_scroll.verticalScrollBar()
        scrollbar.rangeChanged.connect(
            lambda _minimum, _maximum: self._sync_timeline_ruler_inset()
        )
        self._setup_tab_order()

    def _studio_tab_order(self) -> tuple[QWidget, ...]:
        """Return keyboard traversal in the musician's Arrange workflow order."""

        order: list[QWidget] = [
            self._take_list,
            self._new_take_btn,
            self._live_btn,
            self._setup_btn,
            self._record_btn,
            self._output_picker,
            self._play_btn,
            self._stop_btn,
            self._inspector_btn,
            self._scrub,
            self._master_gain,
            self._master_limiter,
            self._add_marker_btn,
            self._add_section_btn,
            self._cycle_region_btn,
            self._region_fades_btn,
            self._crossfade_btn,
            self._add_take_lane_btn,
            self._audition_take_lane_btn,
            self._remove_take_lane_btn,
            self._studio_arrange._canvas.viewport(),
        ]
        for lane in self._lanes.values():
            order.extend(
                [
                    lane._track_export_include,
                    lane._mute,
                    lane._solo,
                    lane._trim,
                    lane._gain,
                    lane._pan,
                ]
            )
        order.extend([self._export_btn, self._reveal_btn, self._originals_btn])
        return tuple(order)

    def _setup_tab_order(self) -> None:
        """Keep keyboard focus aligned with library, editing, mix, then export."""

        order = self._studio_tab_order()
        for current, following in zip(order, order[1:]):
            QWidget.setTabOrder(current, following)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_inspector_visibility()
        self._sync_timeline_ruler_inset()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the supported compact workspace floor when details are a drawer."""

        hint = super().minimumSizeHint()
        if self.width() < 1080:
            return hint.boundedTo(QSize(760, 600))
        return hint

    def _update_inspector_visibility(self) -> None:
        """Expose details as a library-swapping drawer at compact widths."""

        if not hasattr(self, "_inspector"):
            return
        wide = self.width() >= 1080
        self._set_compact_chrome(not wide)
        if wide:
            self._inspector.setMinimumWidth(176)
            self._inspector.setMaximumWidth(260)
            self._library.setMinimumWidth(160)
            self._library.setMaximumWidth(240)
            if self._compact_inspector_open:
                self._library.setVisible(self._compact_inspector_library_was_visible)
            self._compact_inspector_open = False
            self._inspector_btn.blockSignals(True)
            self._inspector_btn.setChecked(False)
            self._inspector_btn.blockSignals(False)
            self._inspector_btn.setText("Track Details")
            self._inspector_btn.setAccessibleName("Show track details")
            self._inspector_btn.setVisible(False)
            self._inspector.setVisible(True)
            return

        compact_available = not self._viewing_live and self._current is not None
        if not compact_available:
            if self._compact_inspector_open:
                self._library.setVisible(self._compact_inspector_library_was_visible)
            self._compact_inspector_open = False
            self._inspector.setMinimumWidth(0)
            self._inspector.setMaximumWidth(0)
            self._inspector_btn.blockSignals(True)
            self._inspector_btn.setChecked(False)
            self._inspector_btn.blockSignals(False)
            self._inspector_btn.setVisible(False)
            self._inspector.setVisible(False)
            return

        self._inspector_btn.setVisible(True)
        # A hidden QSplitter child can retain its old minimum in a pending
        # layout pass (most visibly after other top-level Qt windows have been
        # exercised).  Remove that constraint until the details drawer is
        # explicitly opened so 760 px remains a real compact-workspace floor.
        if not self._compact_inspector_open:
            self._inspector.setMinimumWidth(0)
            self._inspector.setMaximumWidth(0)
        self._inspector.setVisible(self._compact_inspector_open)
        if self._compact_inspector_open:
            self._library.setVisible(False)

    def _set_compact_chrome(self, compact: bool) -> None:
        """Keep Arrange and its one-lane mixer usable at the 760 px floor."""

        self._arrange_toolbar.set_compact(compact)
        button_height = 44
        controls = (
            self._live_btn,
            self._setup_btn,
            self._record_btn,
            self._play_btn,
            self._stop_btn,
            self._inspector_btn,
            self._scrub,
            self._output_picker,
            self._export_btn,
            self._reveal_btn,
        )
        for widget in controls:
            if compact:
                widget.setFixedHeight(button_height)
            else:
                widget.setMinimumHeight(0)
                widget.setMaximumHeight(16_777_215)
        for widget in (
            self._add_take_lane_btn,
            self._audition_take_lane_btn,
            self._remove_take_lane_btn,
        ):
            if compact:
                widget.setFixedHeight(button_height)
            else:
                widget.setMinimumHeight(0)
                widget.setMaximumHeight(16_777_215)
        if compact:
            self._output_picker.setFixedHeight(40)
            self._arrange_toolbar.setFixedHeight(40)
            self._comp_toolbar.setFixedHeight(button_height)
            self._hint.setFixedHeight(18)
        else:
            for widget in (
                self._output_picker,
                self._arrange_toolbar,
                self._comp_toolbar,
                self._hint,
            ):
                widget.setMinimumHeight(0)
                widget.setMaximumHeight(16_777_215)
        if self._hint.property("compact") != compact:
            self._hint.setProperty("compact", compact)
            self._hint.setWordWrap(not compact)
            self._hint.style().unpolish(self._hint)
            self._hint.style().polish(self._hint)
        self._phase.setVisible(not compact)
        self._comp_help.setVisible(not compact)

    def _set_compact_inspector_open(self, opened: bool) -> None:
        """Swap compact library/details panels without shrinking Arrange."""

        if self.width() >= 1080:
            return
        opened = bool(opened)
        if opened and not self._compact_inspector_open:
            self._compact_inspector_library_was_visible = not self._library.isHidden()
        self._compact_inspector_open = opened
        self._inspector.setMinimumWidth(176 if opened else 0)
        self._inspector.setMaximumWidth(260 if opened else 0)
        self._library.setMinimumWidth(0 if opened else 160)
        self._library.setMaximumWidth(0 if opened else 240)
        self._inspector.setVisible(opened)
        self._library.setVisible(
            False if opened else self._compact_inspector_library_was_visible
        )
        self._inspector_btn.setText("Hide Details" if opened else "Track Details")
        self._inspector_btn.setAccessibleName(
            "Hide track details" if opened else "Show track details"
        )

    def _sync_timeline_ruler_inset(self) -> None:
        """Keep ruler ticks aligned with the visible waveform viewport."""
        if not hasattr(self, "_timeline_ruler"):
            return
        scrollbar = self._track_scroll.verticalScrollBar()
        trailing = 8 + (scrollbar.width() if scrollbar.isVisible() else 0)
        self._timeline_ruler.set_trailing_inset(trailing)

    def _align_legacy_ruler_origin(self) -> None:
        """Align the legacy ruler after Qt has applied stylesheet frame widths."""

        if not self._lanes or not self.isVisible():
            return
        lane = next(iter(self._lanes.values()))
        if not (lane.waveform.isVisible() and self._timeline_ruler.isVisible()):
            return
        waveform_x = lane.waveform.mapTo(self, lane.waveform.rect().topLeft()).x()
        ruler_x = self._timeline_ruler.mapTo(
            self, self._timeline_ruler.rect().topLeft()
        ).x()
        delta = waveform_x - ruler_x
        if delta:
            self._timeline_gutter.setFixedWidth(
                max(0, self._timeline_gutter.width() + delta)
            )
            layout = self._legacy_timeline.layout()
            if layout is not None:
                layout.activate()

    def _set_inspector_values(self, **values: str) -> None:
        for key, value in values.items():
            label = self._inspector_values.get(key)
            if label is not None:
                label.setText(value)

    def _set_empty_inspector(self) -> None:
        self._set_inspector_values(
            status="Select a track to review it.",
            source="—",
            timeline="—",
            alignment="—",
            gaps="—",
            export="—",
        )

    def set_takes_directory(self, path: str) -> None:
        normalized = str(path or "")
        if normalized == self._takes_dir:
            return
        self._takes_dir = normalized
        self.reload()

    def set_local_originals_directory(self, path: str | Path | None) -> None:
        """Expose preserved guest media without importing or modifying it."""

        self._local_originals_path = Path(path).expanduser().resolve() if path else None
        available = bool(
            self._local_originals_path is not None
            and self._local_originals_path.is_dir()
        )
        self._originals_btn.setVisible(available)
        self._originals_btn.setEnabled(available)

    def refresh_take(self, path: str | Path) -> None:
        """Reload manifest truth while preserving the user's Studio context."""

        target = Path(path).resolve()
        selected = self._current.path if self._current is not None else None
        viewing_live = self._viewing_live
        if not viewing_live and selected is None:
            selected = target
        self.reload(select_path=selected)
        if viewing_live:
            self._show_live_session()

    def set_output_device(self, name: str) -> None:
        """Apply the saved Studio playback output without starting audio."""
        self._stop_playback()
        value = str(name or "")
        index = self._output_picker.findData(value)
        if index < 0 and value:
            self._output_picker.addItem(f"{value} (unavailable)", value)
            index = self._output_picker.count() - 1
        self._output_picker.blockSignals(True)
        self._output_picker.setCurrentIndex(max(0, index))
        self._output_picker.blockSignals(False)
        self._player.set_output_device(value)

    def _populate_output_devices(self) -> None:
        self._output_picker.addItem("System Default", "")
        for device in list_output_devices():
            name = str(device.get("name") or "").strip()
            if name and self._output_picker.findData(name) < 0:
                self._output_picker.addItem(name, name)

    def _on_output_changed(self, _index: int) -> None:
        self._stop_playback()
        name = str(self._output_picker.currentData() or "")
        self._player.set_output_device(name)
        self.output_device_changed.emit(name)

    def set_can_record(self, enabled: bool, reason: str = "") -> None:
        self._can_record = bool(enabled)
        if not self._recording:
            self._refresh_record_button_enabled()
        if reason and not enabled:
            self._hint.setText(reason)

    def guidance_facts(self) -> StudioGuidanceFacts:
        """Return path-free semantic Studio facts for the session conductor."""

        take = self._current if not self._viewing_live else None
        selected = take is not None
        validated = bool(
            selected
            and take.validation_status == "complete"
            and not take.manifest_errors
        )
        needs_attention = bool(
            selected
            and (
                take.manifest_errors
                or take.review_only
                or take.export_block_reason
            )
        )
        return StudioGuidanceFacts(
            take_revision=self._guidance_take_revision,
            take_available=bool(self._takes),
            take_selected=selected,
            take_validated=validated,
            take_needs_attention=needs_attention,
            arrangement_available=bool(selected and self._studio_state is not None),
            dirty=bool(
                selected
                and (
                    self._studio_state_dirty
                    or self._studio_controller.dirty
                )
            ),
            save_failed=bool(selected and self._studio_persistence_failed),
            can_export=bool(selected and self._can_export_current_take()),
        )

    def set_musician_guidance(
        self,
        guidance: MusicianGuidanceSnapshot,
    ) -> None:
        """Render the shared next step without adding another primary button."""

        studio = guidance.output("studio")
        next_step = guidance.next_step
        text = f"{studio.detail} · Next: {next_step}"
        if not self._viewing_live and self._studio_state is not None:
            text = f"Non-destructive · {text}"
        self._shared_guidance_text = text
        self._render_studio_phase()
        self._phase.setAccessibleDescription(guidance.accessible_description)
        self._phase.setToolTip(guidance.accessible_description)
        self.setAccessibleDescription(guidance.accessible_description)

    def _render_studio_phase(self) -> None:
        text = self._phase_label
        if self._shared_guidance_text:
            text = f"{text} · {self._shared_guidance_text}"
        self._phase.setText(text)

    def _emit_guidance_changed(self) -> None:
        """Publish semantic changes once; timers and playheads never call this."""

        facts = self.guidance_facts()
        if facts == self._last_guidance_facts:
            return
        self._last_guidance_facts = facts
        self.guidance_changed.emit()

    def set_live_participants(self, participants: Iterable) -> None:
        incoming = list(participants)
        signature = tuple(
            (
                int(getattr(item, "channel_id", -1)),
                str(getattr(item, "name", "")),
                bool(getattr(item, "is_local", False)),
            )
            for item in incoming
        )
        changed = signature != self._live_signature
        self._live_participants = incoming
        self._live_signature = signature
        if self._phase_name not in {"preflight", "starting", "stopping", "validating"}:
            self._refresh_record_button_enabled()
        if self._viewing_live and changed:
            self._populate_live_lanes()

    def set_live_levels(self, levels: dict[int, float]) -> None:
        if not self._viewing_live:
            return
        for channel_id, value in levels.items():
            lane = self._lanes.get(int(channel_id))
            if lane is not None:
                lane.set_level(value)

    def set_recording_phase(self, phase: str, detail: str = "") -> None:
        phase = str(phase or "idle")
        previous_phase = self._phase_name
        self._phase_name = phase
        if self._exporting and phase in {
            "preflight",
            "starting",
            "count_in",
            "recording",
            "stop_failed",
        }:
            self._cancel_export_for_recording()
        labels = {
            "idle": "READY · start audio, then Record Session",
            "preflight": "PREPARING THE SESSION…",
            "starting": "PREPARING TRACKS…",
            "count_in": "COUNT-IN · recording is already active",
            "recording": "● RECORDING · one track per musician",
            "stopping": "STOPPING RECORDING…",
            "validating": detail or "FINALIZING THE TAKE…",
            "complete": "READY · TAKE SAVED",
            "needs_attention": "TAKE SAVED · review recommended",
            "stop_failed": "CLEANUP PENDING · recording stop not confirmed",
            "error": "RECORDING NEEDS ATTENTION",
        }
        self._phase_label = labels.get(phase, phase.upper())
        self._render_studio_phase()
        self._recording = phase in {"count_in", "recording", "stop_failed"}
        if self._recording:
            if phase == "recording" and previous_phase not in {
                "count_in",
                "recording",
            }:
                self._recording_elapsed = 0.0
            self._show_live_session()
            self._record_btn.setText(
                "■ Finish Stop"
                if phase == "stop_failed"
                else "■ Stop Recording"
            )
            self._refresh_record_button_enabled()
        elif phase in {"preflight", "starting", "stopping", "validating"}:
            self._record_btn.setText("Working…")
            self._record_btn.setEnabled(False)
        else:
            self._record_btn.setText("● Record Session")
            self._refresh_record_button_enabled()
        for lane in self._lanes.values():
            lane.waveform.set_recording(self._recording)
        self._refresh_export_button()

    def _refresh_record_button_enabled(self) -> None:
        """Keep record/stop availability consistent with export and recorder state."""

        busy = self._phase_name in {
            "preflight",
            "starting",
            "stopping",
            "validating",
        }
        self._record_btn.setEnabled(
            self._recording
            or (
                not self._exporting
                and not busy
                and self._can_record
                and bool(self._live_participants)
            )
        )

    def _cancel_export_for_recording(self) -> None:
        """Give an already-starting recorder priority over a stale export race."""

        if not self._exporting:
            return
        self._export_cancel.set()
        self._export_generation += 1
        self._restore_export_controls()
        self._hint.setText(
            "Studio stopped the previous export because recording is starting. "
            "The original take is unchanged."
        )
        self.export_finished.emit(False)

    def reload(self, select_path: Optional[Path] = None) -> None:
        self._takes = discover_takes(self._takes_dir) if self._takes_dir else []
        self._library.setVisible(bool(self._takes))
        self._take_list.blockSignals(True)
        self._take_list.clear()
        for take in self._takes:
            status = "✓" if take.validation_status == "complete" else "•"
            label = (
                f"{status} {take.display_name}\n"
                f"   {take.track_count} tracks · {_fmt_time(take.duration_s)}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(take.path))
            self._take_list.addItem(item)
        self._take_list.blockSignals(False)
        if select_path is not None:
            wanted = str(Path(select_path))
            for row in range(self._take_list.count()):
                if self._take_list.item(row).data(Qt.ItemDataRole.UserRole) == wanted:
                    self._take_list.setCurrentRow(row)
                    self._on_take_selected(row)
                    return
        if not self._takes and not self._live_participants:
            self._hint.setText(
                "Start Session to add musicians, then press Record for synchronized tracks."
            )
        if self._current is None:
            self._export_btn.setEnabled(False)
            self._reveal_btn.setEnabled(False)

    def on_take_completed(
        self,
        path: Optional[Path],
        validation: Optional[TakeValidationResult] = None,
    ) -> None:
        self.reload(select_path=path)
        if validation is not None:
            if path is None:
                self._hint.setText(
                    "No completed take was found. Run Band Check, then record "
                    "a short test take."
                )
            else:
                self._hint.setText(
                    _take_review_message(
                        has_errors=bool(validation.errors),
                        has_warnings=bool(validation.warnings),
                    )
                )

    @staticmethod
    def _studio_waveform_key(
        document: StudioDocument,
        catalog: StudioSourceCatalog,
    ) -> tuple[object, ...]:
        """Describe only source/timeline facts that affect Arrange waveforms."""

        regions = tuple(
            (
                item.region_id,
                item.enabled,
                item.deleted,
                item.source_take_id,
                item.source_track_id,
                item.source_segment_id,
                item.source_start_frame,
                item.source_frame_count,
                item.timeline_start_frame,
                item.timeline_frame_count,
                item.mapping_source_start_frame,
                item.mapping_source_frame_count,
                item.mapping_timeline_start_frame,
                item.mapping_timeline_frame_count,
            )
            for item in document.regions
        )
        return id(catalog), regions

    def _activate_studio_waveforms(self) -> None:
        """Bind the current immutable arrangement to its trusted source catalog."""

        document = self._studio_state
        catalog = self._studio_source_catalog
        if document is None or catalog is None or self._viewing_live:
            self._cancel_studio_waveforms(clear=document is None)
            return
        key = self._studio_waveform_key(document, catalog)
        if key == self._studio_waveform_document_key:
            self._queue_studio_waveforms()
            return
        try:
            self._studio_waveforms.activate(document, catalog)
        except StudioWaveformCoordinatorError as exc:
            LOGGER.warning("Could not bind Studio waveform sources: %s", exc)
            self._studio_waveforms.cancel()
            self._studio_waveform_document_key = None
            self._studio_arrange.clear_waveforms()
            self._hint.setText(
                "Some waveform previews couldn't be prepared. Playback and export "
                "will still verify the source media; every recording is unchanged."
            )
            return
        self._studio_waveform_document_key = key
        self._studio_waveform_error_generation = None
        self._queue_studio_waveforms()

    def _queue_studio_waveforms(self) -> None:
        """Debounce pure viewport planning after zoom, scroll, or document changes."""

        timer = getattr(self, "_studio_waveform_schedule_timer", None)
        if (
            timer is not None
            and not self._waveform_shutdown
            and not self._viewing_live
            and self._studio_state is not None
            and self._studio_source_catalog is not None
            and self._studio_waveform_document_key is not None
        ):
            timer.start()

    def _schedule_studio_waveforms(self) -> None:
        """Plan bounded visible tiles without opening media on the UI thread."""

        if (
            self._waveform_shutdown
            or self._viewing_live
            or self._studio_waveform_document_key is None
        ):
            return
        start, end = self._studio_arrange.visible_frame_range()
        try:
            generation = self._studio_waveforms.schedule(
                start,
                end,
                self._studio_arrange.pixels_per_frame,
                self._studio_arrange.visible_region_ids(),
            )
            self._studio_arrange.begin_waveform_generation(generation)
        except StudioWaveformCoordinatorError as exc:
            LOGGER.warning("Could not plan Studio waveforms: %s", exc)
            self._hint.setText(
                "Some waveform previews couldn't be drawn. The arrangement and "
                "recorded audio are unchanged."
            )

    def _publish_studio_waveform_tile(
        self,
        result: StudioWaveformRegionTile,
    ) -> None:
        """Publish one current worker result on the timer/UI thread."""

        if self._viewing_live or self._studio_state is None:
            return
        self._studio_arrange.add_region_waveform_tile(
            result.region_id,
            result.tile,
            generation=result.generation,
        )

    def _publish_studio_waveform_error(
        self,
        result: StudioWaveformRegionError,
    ) -> None:
        """Surface one safe fixed error per generation; log details for diagnosis."""

        LOGGER.warning(
            "Studio waveform worker failed for region %s: %s",
            result.region_id,
            result.error,
        )
        if (
            self._viewing_live
            or result.generation == self._studio_waveform_error_generation
        ):
            return
        self._studio_waveform_error_generation = result.generation
        self._hint.setText(
            "Some waveform previews couldn't be drawn. Playback and export still "
            "verify the source media; every recording is unchanged."
        )

    def _cancel_studio_waveforms(self, *, clear: bool) -> None:
        timer = getattr(self, "_studio_waveform_schedule_timer", None)
        if timer is not None:
            timer.stop()
        coordinator = getattr(self, "_studio_waveforms", None)
        if coordinator is not None:
            coordinator.cancel()
        self._studio_waveform_document_key = None
        self._studio_waveform_error_generation = None
        if clear and hasattr(self, "_studio_arrange"):
            self._studio_arrange.clear_waveforms()

    def _cancel_waveform_jobs(self) -> None:
        """Cancel current work and discard results for lanes being replaced."""
        self._waveform_cancel.set()
        with self._waveform_futures_lock:
            futures = tuple(self._waveform_futures)
            self._waveform_futures.clear()
        for future in futures:
            future.cancel()
        while True:
            try:
                self._waveform_results.get_nowait()
            except queue.Empty:
                break

    def _begin_waveform_batch(self) -> tuple[int, threading.Event]:
        self._waveform_generation += 1
        self._waveform_cancel = threading.Event()
        return self._waveform_generation, self._waveform_cancel

    def _schedule_waveform(
        self,
        *,
        generation: int,
        cancel_event: threading.Event,
        channel_id: int,
        path: Path,
    ) -> None:
        """Apply a cached envelope or build one away from the Qt thread."""
        if self._waveform_shutdown or cancel_event.is_set():
            return
        source = Path(path)
        try:
            key = _waveform_source_key(source)
        except OSError as exc:
            LOGGER.debug("Could not identify waveform source %s: %s", source, exc)
            return

        cached = self._waveform_cache.get(key)
        if cached is not None:
            if generation == self._waveform_generation and not self._viewing_live:
                lane = self._lanes.get(int(channel_id))
                if lane is not None:
                    lane.waveform.set_peaks(cached)
            return

        def build() -> tuple[float, ...]:
            if cancel_event.is_set():
                raise _WaveformBuildCancelled
            existing = self._waveform_cache.get(key)
            if existing is not None:
                return existing
            peaks = _waveform_peaks(source, cancel_event=cancel_event)
            if cancel_event.is_set():
                raise _WaveformBuildCancelled
            self._waveform_cache.put(key, peaks)
            return peaks

        try:
            future = self._waveform_executor.submit(build)
        except RuntimeError:
            # Executor shutdown raced an application/window close.
            return
        with self._waveform_futures_lock:
            self._waveform_futures.add(future)

        def completed(done: Future) -> None:
            with self._waveform_futures_lock:
                self._waveform_futures.discard(done)
            try:
                peaks = done.result()
            except (CancelledError, _WaveformBuildCancelled):
                return
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Waveform worker failed for %s: %s", source, exc)
                return
            if self._waveform_shutdown or cancel_event.is_set():
                return
            self._waveform_results.put(
                (generation, int(channel_id), source, key, tuple(peaks))
            )

        future.add_done_callback(completed)

    def _schedule_composite_waveform(
        self,
        *,
        generation: int,
        cancel_event: threading.Event,
        channel_id: int,
        spec: _CompositeWaveformSpec,
    ) -> None:
        if self._waveform_shutdown or cancel_event.is_set():
            return
        try:
            key = _composite_waveform_key(spec)
        except OSError as exc:
            LOGGER.debug("Could not identify composite waveform: %s", exc)
            return
        cached = self._waveform_cache.get(key)
        if cached is not None:
            if generation == self._waveform_generation and not self._viewing_live:
                lane = self._lanes.get(int(channel_id))
                if lane is not None:
                    lane.waveform.set_peaks(cached)
            return

        def build() -> tuple[float, ...]:
            existing = self._waveform_cache.get(key)
            if existing is not None:
                return existing
            peaks = _composite_waveform_peaks(spec, cancel_event=cancel_event)
            if cancel_event.is_set():
                raise _WaveformBuildCancelled
            self._waveform_cache.put(key, peaks)
            return peaks

        try:
            future = self._waveform_executor.submit(build)
        except RuntimeError:
            return
        with self._waveform_futures_lock:
            self._waveform_futures.add(future)

        def completed(done: Future) -> None:
            with self._waveform_futures_lock:
                self._waveform_futures.discard(done)
            try:
                peaks = done.result()
            except (CancelledError, _WaveformBuildCancelled):
                return
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Composite waveform worker failed: %s", exc)
                return
            if self._waveform_shutdown or cancel_event.is_set():
                return
            self._waveform_results.put(
                (generation, int(channel_id), spec, key, tuple(peaks))
            )

        future.add_done_callback(completed)

    def _drain_waveform_results(self) -> None:
        """Apply current worker results; stale take/source results are ignored."""
        while True:
            try:
                generation, channel_id, source, key, peaks = (
                    self._waveform_results.get_nowait()
                )
            except queue.Empty:
                return
            if generation != self._waveform_generation or self._viewing_live:
                continue
            lane = self._lanes.get(channel_id)
            if lane is None:
                continue
            try:
                current_key = (
                    _composite_waveform_key(source)
                    if isinstance(source, _CompositeWaveformSpec)
                    else _waveform_source_key(source)
                )
            except OSError:
                continue
            if current_key != key:
                # The file changed while it was being scanned.  Never paint
                # stale peaks; queue one build for the new source identity.
                if isinstance(source, _CompositeWaveformSpec):
                    self._schedule_composite_waveform(
                        generation=generation,
                        cancel_event=self._waveform_cancel,
                        channel_id=channel_id,
                        spec=source,
                    )
                else:
                    self._schedule_waveform(
                        generation=generation,
                        cancel_event=self._waveform_cancel,
                        channel_id=channel_id,
                        path=source,
                    )
                continue
            lane.waveform.set_peaks(peaks)

    def _clear_lanes(self) -> None:
        self._cancel_waveform_jobs()
        self._studio_mix_merge_keys.clear()
        self._master_gain_merge_key = None
        self._lanes.clear()
        self._track_info_by_channel.clear()
        self._selected_channel_id = None
        self._set_empty_inspector()
        while self._track_layout.count() > 1:
            item = self._track_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._setup_tab_order()

    def _add_lane(self, lane: TrackLane, *, live: bool) -> None:
        self._lanes[lane.channel_id] = lane
        lane.set_live_mode(live)
        lane.track_selected.connect(self._select_track)
        lane.mix_gesture_started.connect(self._begin_track_mix_gesture)
        lane.mix_gesture_finished.connect(self._end_track_mix_gesture)
        if live:
            lane.gain_changed.connect(self.live_fader_changed.emit)
            lane.mute_changed.connect(self.live_mute_toggled.emit)
            lane.solo_changed.connect(self.live_solo_toggled.emit)
        else:
            lane.trim_changed.connect(
                lambda cid, value: self._player.set_trim_gain(cid, value / 100.0)
            )
            lane.gain_changed.connect(
                lambda cid, value: self._player.set_gain(cid, value / 100.0)
            )
            lane.mute_changed.connect(self._player.set_muted)
            lane.solo_changed.connect(self._player.set_solo)
            lane.pan_changed.connect(
                lambda cid, value: self._player.set_pan(cid, value / 100.0)
            )
            lane.gain_changed.connect(
                lambda cid, value: self._update_studio_state(cid, gain=value / 100.0)
            )
            lane.trim_changed.connect(
                lambda cid, value: self._update_studio_state(
                    cid, trim_gain=value / 100.0
                )
            )
            lane.mute_changed.connect(
                lambda cid, muted: self._update_studio_state(cid, muted=muted)
            )
            lane.solo_changed.connect(
                lambda cid, solo: self._update_studio_state(cid, solo=solo)
            )
            lane.pan_changed.connect(
                lambda cid, value: self._update_studio_state(cid, pan=value / 100.0)
            )
        self._track_layout.insertWidget(self._track_layout.count() - 1, lane)
        self._setup_tab_order()

    def _set_playback_controls_visible(self, visible: bool) -> None:
        for widget in self._playback_controls:
            widget.setVisible(visible)
        self._update_inspector_visibility()

    def _set_master_controls_visible(self, visible: bool) -> None:
        for widget in self._master_controls:
            widget.setVisible(visible and self.width() >= 1080)

    def _populate_live_lanes(self) -> None:
        self._clear_lanes()
        self._studio_arrange.setVisible(False)
        self._arrange_toolbar.setVisible(False)
        self._comp_toolbar.setVisible(False)
        self._legacy_timeline.setVisible(True)
        self._set_playback_controls_visible(False)
        self._set_master_controls_visible(False)
        self._live_btn.setVisible(False)
        self._new_take_btn.setVisible(False)
        self._title.setText("Live multitrack session")
        self._subtitle.setText(
            "Connected musicians are mapped automatically after recorder proof."
        )
        self._play_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._scrub.setEnabled(False)
        self._position.setText(
            f"REC {_fmt_time(self._recording_elapsed)}" if self._recording else "0:00"
        )
        self._timeline_ruler.set_timeline(
            duration=max(30.0, self._recording_elapsed),
            playhead=self._recording_elapsed if self._recording else 0.0,
            seek_enabled=False,
        )
        for track_number, participant in enumerate(self._live_participants, start=1):
            channel_id = int(getattr(participant, "channel_id", -1))
            if channel_id < 0:
                continue
            recording_proven = self._phase_name in {
                "count_in",
                "recording",
                "stop_failed",
            }
            detail = (
                "RECORDING · live musician"
                if recording_proven
                else "CONNECTED · awaiting recorder proof"
            )
            if getattr(participant, "is_local", False):
                detail = (
                    "RECORDING · you"
                    if recording_proven
                    else "YOU · awaiting recorder proof"
                )
            lane = TrackLane(
                channel_id,
                getattr(participant, "name", "Musician"),
                detail,
                track_number=track_number,
                source="jamulus_server",
            )
            lane.waveform.set_live(self._recording)
            self._add_lane(lane, live=True)
        if self._live_participants:
            if self._phase_name in {"count_in", "recording", "stop_failed"}:
                self._hint.setText(
                    f"Recording {len(self._live_participants)} proven musician "
                    f"source{'s' if len(self._live_participants) != 1 else ''}."
                )
            else:
                self._hint.setText(
                    f"{len(self._live_participants)} connected musician"
                    f"{'s' if len(self._live_participants) != 1 else ''}. "
                    "Press Record Session to verify and map recorder tracks."
                )
        else:
            self._hint.setText(
                "Start Session to add musicians, then press Record for synchronized tracks."
            )
        self._sync_timeline_ruler_inset()
        if self._lanes:
            self._select_track(next(iter(self._lanes)))

    def _show_live_session(self) -> None:
        if self._exporting:
            self._hint.setText(
                "Finish the current export before returning to the live session."
            )
            return
        saved = self._flush_studio_state()
        if not saved:
            if self._studio_state_error:
                self._hint.setText(self._studio_state_error)
            self._emit_guidance_changed()
            return
        self._cancel_playback_preparation(restore_controls=False)
        self._player.stop()
        self._cancel_studio_waveforms(clear=True)
        changed_take = self._current is not None or not self._viewing_live
        self._current = None
        self._studio_state = None
        self._studio_state_take_path = None
        self._studio_state_token = None
        self._studio_state_dirty = False
        self._studio_state_error = ""
        self._studio_persistence_failed = False
        self._studio_project = None
        self._studio_source_catalog = None
        self._studio_audition_lane_id = None
        self._studio_arrange.set_document(None)
        self._reveal_path = None
        self._reveal_btn.setEnabled(False)
        set_labeled_action(self._reveal_btn, "Show Take")
        self._export_btn.setEnabled(False)
        self._viewing_live = True
        if changed_take:
            self._guidance_take_revision += 1
        self._take_list.clearSelection()
        self._populate_live_lanes()
        self._emit_guidance_changed()

    @staticmethod
    def _track_export_selection_key(take: TakeInfo) -> Path:
        """Return a stable in-memory key for a take's temporary export choices."""
        return take.path.expanduser().resolve()

    def _selected_track_export_track_ids(self, take: TakeInfo) -> set[str] | None:
        """Return durable inclusion choices, or ``None`` for schema-v1 takes."""
        available = set(_selectable_track_export_track_ids(take))
        if not available:
            return None
        if (
            self._studio_state is not None
            and self._studio_state_take_path == self._track_export_selection_key(take)
        ):
            return {
                track_id
                for track_id in available
                if (
                    (saved := self._saved_state_for_track(track_id)) is None
                    or saved.export_included
                )
            }
        excluded = self._excluded_track_export_track_ids.setdefault(
            self._track_export_selection_key(take), set()
        )
        excluded.intersection_update(available)
        return available - excluded

    def _studio_export_context_ready(self, take: TakeInfo) -> bool:
        """Require one fully activated source inventory for schema-v2 export."""

        if not _take_requires_studio_document(take):
            return True
        take_path = self._track_export_selection_key(take)
        document = self._studio_state
        project = self._studio_project
        catalog = self._studio_source_catalog
        if (
            document is None
            or project is None
            or catalog is None
            or self._studio_state_take_path != take_path
            or self._studio_controller.take_path != take_path
            or document.take_id != project.take_id
            or document.take_id != take.take_id
        ):
            return False
        expected_take_ids = (
            project.take_id,
            *sorted(
                {
                    region.source_take_id
                    for region in document.regions
                    if not region.deleted
                    and region.source_take_id != project.take_id
                }
            ),
        )
        return catalog.take_ids == expected_take_ids

    def _show_studio_export_context_error(self) -> None:
        self._hint.setText(
            "Studio export stays locked because its arrangement sources could "
            "not be verified. Reopen the take and try again. Every recording "
            "is unchanged."
        )
        self._emit_guidance_changed()

    def _can_export_current_take(self) -> bool:
        take = self._current
        if (
            take is None
            or self._exporting
            or self._recording
            or self._phase_name not in {"idle", "complete", "needs_attention", "error"}
            or take.validation_status != "complete"
            or bool(take.manifest_errors)
            or not self._player.tracks
            or not self._studio_export_context_ready(take)
        ):
            return False
        selected = self._selected_track_export_track_ids(take)
        return selected is None or bool(selected)

    def _refresh_export_button(self) -> None:
        if not self._exporting:
            self._refresh_export_presentation()
            self._export_btn.setEnabled(self._can_export_current_take())

    def _refresh_export_presentation(self) -> None:
        """Label the unsupported-platform path as originals-only."""

        aligned_originals_only = (
            self._studio_state is not None and not studio_export_supported()
        )
        if aligned_originals_only:
            self._export_btn.setText("Export Aligned Originals")
            self._export_btn.setAccessibleName(
                "Export aligned originals without Studio edits"
            )
            self._export_btn.setToolTip(
                "Exports unity aligned originals and a reference rough mix. "
                "Arrangement edits, fades, comps, sections, and master controls "
                "are not included on this platform. Attached or repeated "
                "take-lane recordings are not included; export each from its "
                "own take."
            )
            return
        self._export_btn.setText("Export Tracks")
        self._export_btn.setAccessibleName("Export aligned tracks")
        self._export_btn.setToolTip("")

    def _set_track_export_included(
        self,
        take_path: Path,
        track_id: str,
        included: bool,
    ) -> None:
        """Store one non-destructive choice for the current track export."""
        take = self._current
        if (
            take is None
            or self._track_export_selection_key(take)
            != take_path.expanduser().resolve()
        ):
            return
        available = set(_selectable_track_export_track_ids(take))
        if track_id not in available:
            return
        excluded = self._excluded_track_export_track_ids.setdefault(
            self._track_export_selection_key(take), set()
        )
        if included:
            excluded.discard(track_id)
            self._hint.setText(
                "Track included in future exports. The recorded take is unchanged."
            )
        else:
            excluded.add(track_id)
            self._hint.setText(
                "Track left out of future exports. The recorded take is unchanged."
            )
        for channel_id, track in self._track_info_by_channel.items():
            if self._state_track_id(track) == track_id:
                self._update_studio_state(
                    channel_id,
                    export_included=bool(included),
                )
                break
        if not self._selected_track_export_track_ids(take):
            self._hint.setText(
                "Choose at least one track to export. The recorded take is unchanged."
            )
        self._refresh_export_button()

    def _on_take_selected(self, row: int) -> None:
        if self._exporting:
            if self._current is not None:
                current_path = str(self._current.path)
                self._take_list.blockSignals(True)
                for current_row in range(self._take_list.count()):
                    if (
                        self._take_list.item(current_row).data(Qt.ItemDataRole.UserRole)
                        == current_path
                    ):
                        self._take_list.setCurrentRow(current_row)
                        break
                self._take_list.blockSignals(False)
            self._hint.setText("Finish the current export before changing takes.")
            return
        if row < 0 or row >= len(self._takes):
            return
        if not self._flush_studio_state():
            if self._current is not None:
                current_path = str(self._current.path)
                self._take_list.blockSignals(True)
                for previous_row in range(self._take_list.count()):
                    if (
                        self._take_list.item(previous_row).data(
                            Qt.ItemDataRole.UserRole
                        )
                        == current_path
                    ):
                        self._take_list.setCurrentRow(previous_row)
                        break
                self._take_list.blockSignals(False)
            return
        self._cancel_playback_preparation(restore_controls=False)
        self._viewing_live = False
        take = self._takes[row]
        self._current = take
        self._guidance_take_revision += 1
        self._load_studio_state(take)
        if self._studio_state is not None and self._studio_project is not None:
            try:
                self._player.load_studio(
                    self._studio_project,
                    self._studio_state,
                    take.path,
                    source_catalog=self._studio_source_catalog,
                )
            except (PlaybackError, TakeProjectError) as exc:
                LOGGER.warning("Could not load Studio arrangement: %s", exc)
                self._studio_state_error = (
                    "Studio couldn't open this arrangement safely. The recorded "
                    "take is unchanged; review its media before playback."
                )
        else:
            self._player.load(take)
        self._studio_arrange.setVisible(self._studio_state is not None)
        self._legacy_timeline.setVisible(self._studio_state is None)
        self._refresh_comp_controls()
        self._clear_lanes()
        waveform_generation, waveform_cancel = self._begin_waveform_batch()
        self._set_playback_controls_visible(True)
        self._set_master_controls_visible(self._studio_state is not None)
        self._live_btn.setVisible(True)
        self._new_take_btn.setVisible(True)
        self._title.setText(take.display_name)
        blocked_statuses = {"missing", "damaged", "transfer_failed", "transferring"}
        missing_count = sum(
            getattr(track, "media_status", "available") in blocked_statuses
            for track in take.tracks
        )
        if missing_count:
            self._subtitle.setText(
                f"{take.track_count} tracks · {missing_count} missing · "
                f"{_fmt_time(take.duration_s)}"
            )
        else:
            self._subtitle.setText(
                f"{take.track_count} synchronized tracks · {_fmt_time(take.duration_s)}"
            )
        playable = any(
            getattr(track, "media_status", "available") not in blocked_statuses
            and float(getattr(track, "duration_s", 0.0) or 0.0) > 0.0
            for track in take.tracks
        )
        self._play_btn.setEnabled(playable)
        self._stop_btn.setEnabled(playable)
        self._scrub.setEnabled(playable)
        verified = take.validation_status == "complete" and not take.manifest_errors
        self._refresh_export_button()
        self._reveal_path = take.path
        set_labeled_action(self._reveal_btn, "Show Take")
        self._reveal_btn.setEnabled(True)
        self._scrub.setValue(0)
        self._position.setText(f"0:00 / {_fmt_time(self._player.duration_s)}")
        info_by_path = {Path(track.path): track for track in take.tracks}
        info_by_channel = {index: track for index, track in enumerate(take.tracks)}
        self._track_info_by_channel = dict(info_by_channel)
        selectable_track_ids = set(_selectable_track_export_track_ids(take))
        selected_track_ids = self._selected_track_export_track_ids(take)
        for track in self._player.tracks:
            source_info = info_by_channel.get(
                track.channel_id,
                info_by_path.get(Path(track.path)),
            )
            duration = float(getattr(source_info, "duration_s", 0.0) or 0.0)
            media_status = str(
                getattr(source_info, "media_status", "available") or "available"
            )
            if media_status in blocked_statuses:
                label = {
                    "missing": "MISSING MEDIA",
                    "damaged": "DAMAGED MEDIA",
                    "transfer_failed": "TRANSFER FAILED",
                    "transferring": "TRANSFER IN PROGRESS",
                }.get(media_status, "MEDIA NEEDS ATTENTION")
                detail = f"{label} · restore or finish this track to continue"
            elif media_status == "partial":
                detail = "PARTIAL TRACK · listen and review before export"
            elif media_status == "recovered":
                detail = "RECOVERED TRACK · listen and review before export"
            else:
                detail = (
                    "SYNCHRONIZED"
                    if _is_synchronized_source(track.source)
                    else "ORIGINAL"
                )
            export_track_id = str(getattr(source_info, "track_id", "") or "").strip()
            lane = TrackLane(
                track.channel_id,
                track.name,
                detail,
                export_track_id=(
                    export_track_id if export_track_id in selectable_track_ids else ""
                ),
                track_number=track.channel_id + 1,
                source=track.source,
            )
            if export_track_id in selectable_track_ids:
                lane.set_track_export_included(
                    selected_track_ids is not None
                    and export_track_id in selected_track_ids
                )
                lane.export_included_changed.connect(
                    lambda track_id, included, take_path=take.path: (
                        self._set_track_export_included(
                            take_path,
                            track_id,
                            included,
                        )
                    )
                )
            composite_spec = (
                _waveform_spec_for_track(source_info, take)
                if source_info is not None
                else None
            )
            lane.waveform.set_recorded_clip(
                peaks=(),
                offset=0.0 if composite_spec is not None else track.offset_s,
                duration=(
                    max(0.001, float(take.duration_s))
                    if composite_spec is not None
                    else duration
                ),
                timeline_duration=max(1.0, take.duration_s),
                source=track.source,
                gaps=(
                    _timeline_gaps_for_track(source_info, take)
                    if source_info is not None
                    else ()
                ),
            )
            self._add_lane(lane, live=False)
            lane.set_trim_available(self._studio_state is not None)
            saved_state = self._saved_state_for_track(export_track_id)
            if saved_state is not None:
                lane.set_mix_state(
                    gain=saved_state.gain,
                    trim_gain=saved_state.trim_gain,
                    pan=saved_state.pan,
                    muted=saved_state.muted,
                    solo=saved_state.solo,
                )
                self._player.set_gain(track.channel_id, saved_state.gain)
                self._player.set_trim_gain(
                    track.channel_id,
                    saved_state.trim_gain,
                )
                self._player.set_pan(track.channel_id, saved_state.pan)
                self._player.set_muted(track.channel_id, saved_state.muted)
                self._player.set_solo(track.channel_id, saved_state.solo)
            if media_status not in blocked_statuses:
                if composite_spec is not None:
                    self._schedule_composite_waveform(
                        generation=waveform_generation,
                        cancel_event=waveform_cancel,
                        channel_id=track.channel_id,
                        spec=composite_spec,
                    )
                else:
                    self._schedule_waveform(
                        generation=waveform_generation,
                        cancel_event=waveform_cancel,
                        channel_id=track.channel_id,
                        path=track.path,
                    )
        self._timeline_ruler.set_timeline(
            duration=max(1.0, take.duration_s),
            playhead=0.0,
            seek_enabled=playable,
        )
        self._sync_timeline_ruler_inset()
        self._align_legacy_ruler_origin()
        QTimer.singleShot(0, self._align_legacy_ruler_origin)
        if self._lanes:
            self._select_track(next(iter(self._lanes)))
        if self._studio_state_error:
            self._hint.setText(self._studio_state_error)
        elif take.manifest_errors or take.manifest_warnings:
            self._hint.setText(
                _take_review_message(
                    has_errors=bool(take.manifest_errors),
                    has_warnings=bool(take.manifest_warnings),
                )
            )
        elif not verified:
            self._hint.setText(
                "Unverified take. Playback is available, but track export stays "
                "locked until WebJam verifies the recording."
            )
        else:
            if self._studio_state is not None and not studio_export_supported():
                self._hint.setText(
                    "This platform can export aligned originals and a reference "
                    "rough mix. Arrangement edits, fades, comps, sections, and "
                    "master controls are excluded. Attached or repeated take-lane "
                    "recordings must be exported from their own take."
                )
            else:
                self._hint.setText("Take verified and ready to mix or export.")
        self._emit_guidance_changed()

    def _export_tracks(self) -> None:
        """Publish a portable track package without creating an editor project."""
        take = self._current
        if take is None:
            return
        if not self._can_export_current_take():
            if not self._studio_export_context_ready(take):
                self._show_studio_export_context_error()
            return
        if not self._flush_studio_state():
            return
        # Saving can refresh the controller snapshot and its cross-take source
        # inventory. Recheck the exact schema-v2 activation before choosing the
        # Studio or legacy worker so a failed refresh cannot become a fallback.
        if not self._studio_export_context_ready(take):
            self._show_studio_export_context_error()
            self._refresh_export_button()
            return
        self._stop_playback()
        take_path = take.path.expanduser().resolve()
        studio_document = (
            self._studio_state
            if self._studio_state is not None
            and self._studio_state_take_path == take_path
            else None
        )
        studio_export_enabled = (
            studio_document is not None and studio_export_supported()
        )
        aligned_originals_only = (
            studio_document is not None and not studio_export_enabled
        )
        if aligned_originals_only:
            project = self._studio_project
            if project is None:
                self._hint.setText(
                    "Aligned originals couldn't be prepared because the take "
                    "project is unavailable. Reopen the take and try again. The "
                    "recording is unchanged."
                )
                return
            if _studio_document_differs_from_default(studio_document, project):
                answer = QMessageBox.question(
                    self,
                    "Export aligned originals?",
                    "Studio arrangement export is unavailable on this platform.\n\n"
                    "Export Aligned Originals creates unity aligned WAV files and "
                    "a reference rough mix using the current track trim, gain, "
                    "pan, mute, and solo controls. Arrangement edits, region "
                    "fades, comp choices, song sections, and master gain or "
                    "limiter settings are not included. Attached or repeated "
                    "take-lane recordings are also not included; export each "
                    "from its own take.\n\nContinue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._hint.setText(
                        "Aligned-originals export canceled. Your Studio choices "
                        "remain saved and every recording is unchanged."
                    )
                    return
        studio_source_catalog = self._studio_source_catalog
        selectable_track_ids = set(_selectable_track_export_track_ids(take))
        states: dict[int | str, TrackMixSettings] = {}
        if aligned_originals_only:
            for state in studio_document.tracks:
                states[state.track_id] = TrackMixSettings(
                    gain=state.trim_gain * state.fader_gain,
                    pan=state.pan,
                    muted=state.muted,
                    solo=state.solo,
                )
        elif studio_document is None:
            for track in self._player.tracks:
                source_info = self._track_info_for_channel(track.channel_id)
                track_id = self._state_track_id(source_info)
                state_key: int | str = (
                    track_id if track_id in selectable_track_ids else track.channel_id
                )
                states[state_key] = TrackMixSettings(
                    gain=track.gain,
                    pan=track.pan,
                    muted=track.muted,
                    solo=track.solo,
                )
        selected_track_ids = self._selected_track_export_track_ids(take)
        self._export_cancel.set()
        self._export_cancel = threading.Event()
        cancel_event = self._export_cancel
        self._export_generation += 1
        generation = self._export_generation
        self._exporting = True
        self.export_started.emit()
        self._take_list.setEnabled(False)
        self._live_btn.setEnabled(False)
        self._new_take_btn.setEnabled(False)
        self._setup_btn.setEnabled(False)
        self._record_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._export_btn.setText("Exporting…")
        for lane in self._lanes.values():
            lane.set_track_export_enabled(False)
        if aligned_originals_only:
            self._hint.setText(
                "Preparing unity aligned originals and a reference rough mix. "
                "Arrangement edits, fades, comps, sections, and master controls "
                "are excluded. Attached or repeated take-lane recordings must be "
                "exported from their own take."
            )
        else:
            self._hint.setText(
                "Preparing aligned 24-bit stems and a stereo rough mix. "
                "The original take will not be changed."
            )

        def worker() -> None:
            try:
                if studio_export_enabled:
                    project = load_take_project(take_path)
                    studio_export_options = {"cancel_event": cancel_event}
                    if studio_source_catalog is not None:
                        studio_export_options["source_catalog"] = studio_source_catalog
                    result: TrackExportResult | StudioExportResult = (
                        export_studio_arrangement(
                            project,
                            studio_document,
                            take_path,
                            **studio_export_options,
                        )
                    )
                else:
                    result = export_track_package(
                        take,
                        mix_settings=states,
                        selected_track_ids=selected_track_ids,
                    )
                outcome = _ExportWorkerOutcome(
                    generation=generation,
                    take_path=take_path,
                    result=result,
                    aligned_originals_only=aligned_originals_only,
                    studio_export_attempted=studio_export_enabled,
                )
            except Exception as exc:  # noqa: BLE001
                if not cancel_event.is_set():
                    LOGGER.exception("Track export failed for %s", take.path)
                outcome = _ExportWorkerOutcome(
                    generation=generation,
                    take_path=take_path,
                    result=None,
                    error=str(exc),
                    published_folder=(
                        exc.folder
                        if isinstance(exc, StudioExportPublishedError)
                        else None
                    ),
                    aligned_originals_only=aligned_originals_only,
                    studio_export_attempted=studio_export_enabled,
                )
            self._export_results.put(outcome)

        self._export_thread = threading.Thread(
            target=worker,
            daemon=True,
            name="studio-export" if studio_export_enabled else "track-export",
        )
        self._export_thread.start()

    def _restore_export_controls(self) -> None:
        self._exporting = False
        self._export_thread = None
        self._take_list.setEnabled(True)
        self._live_btn.setEnabled(True)
        self._new_take_btn.setEnabled(True)
        self._setup_btn.setEnabled(True)
        self._refresh_record_button_enabled()
        for lane in self._lanes.values():
            lane.set_track_export_enabled(True)
        self._refresh_export_button()

    def _finish_export(
        self,
        result: TrackExportResult | StudioExportResult | None,
        error: str,
        published_folder: Path | None = None,
        *,
        aligned_originals_only: bool = False,
        studio_export_attempted: bool = False,
    ) -> None:
        self._restore_export_controls()
        if result is None:
            LOGGER.error("Track export did not complete: %s", error or "unknown error")
            if published_folder is not None:
                self._reveal_path = published_folder
                set_labeled_action(self._reveal_btn, "Show Unverified Export")
                self._reveal_btn.setEnabled(True)
                self._hint.setText(
                    "Studio created the export folder, but storage durability could "
                    "not be confirmed. Verify SHA256SUMS.txt before relying on it. "
                    "The original take is safe."
                )
                self.export_finished.emit(False)
                return
            self._hint.setText(
                _studio_export_failure_message(error)
                if studio_export_attempted
                else _track_export_failure_message(error)
            )
            self.export_finished.emit(False)
            return
        self._reveal_path = result.folder
        self._reveal_btn.setEnabled(True)
        if isinstance(result, StudioExportResult):
            set_labeled_action(self._reveal_btn, "Show Studio Export")
            self._hint.setText(
                f"Studio export ready · {len(result.edited_stems)} edited stems, "
                f"{len(result.original_stems)} aligned originals, and a rough mix · "
                f"{result.sample_rate / 1000:g} kHz. Import the desired WAVs "
                "together at 0:00 in your editor."
            )
        else:
            if aligned_originals_only:
                set_labeled_action(self._reveal_btn, "Show Aligned Originals")
                self._hint.setText(
                    f"Aligned originals ready · {len(result.stems)} unity 24-bit "
                    f"stems · {result.samplerate / 1000:g} kHz. The reference "
                    "rough mix uses current track controls; arrangement edits, "
                    "fades, comps, sections, and master controls are excluded. "
                    "Attached or repeated take-lane recordings must be exported "
                    "from their own take."
                )
            else:
                set_labeled_action(self._reveal_btn, "Show Track Export")
                self._hint.setText(
                    f"Track export ready · {len(result.stems)} aligned 24-bit "
                    f"stems · {result.samplerate / 1000:g} kHz. Import the "
                    "numbered WAVs together at 0:00 in your editor."
                )
        self.export_finished.emit(True)

    def _drain_export_results(self) -> None:
        while True:
            try:
                outcome = self._export_results.get_nowait()
            except queue.Empty:
                return
            if self._waveform_shutdown or outcome.generation != self._export_generation:
                continue
            current_path = (
                None
                if self._current is None
                else self._current.path.expanduser().resolve()
            )
            if current_path != outcome.take_path:
                # A programmatic refresh can replace a take despite the disabled
                # list. Never attach the old result or error to the new take.
                self._restore_export_controls()
                self._export_generation += 1
                self._hint.setText(
                    "The export finished after Studio changed takes, so its result "
                    "was not attached here. The recorded takes are unchanged."
                )
                self.export_finished.emit(False)
                continue
            self._finish_export(
                outcome.result,
                outcome.error,
                outcome.published_folder,
                aligned_originals_only=getattr(
                    outcome, "aligned_originals_only", False
                ),
                studio_export_attempted=getattr(
                    outcome, "studio_export_attempted", False
                ),
            )

    def _reveal_current(self) -> None:
        if self._reveal_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._reveal_path)))

    def _reveal_local_originals(self) -> None:
        path = self._local_originals_path
        if path is not None and path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _cancel_playback_preparation(self, *, restore_controls: bool = True) -> None:
        """Invalidate background source work before the Studio context changes."""

        was_preparing = self._playback_preparing
        self._playback_prepare_generation += 1
        self._playback_prepare_cancel.set()
        future = self._playback_prepare_future
        if future is not None:
            future.cancel()
        self._playback_prepare_future = None
        self._playback_preparing = False
        self._playback_prepare_autoplay = False
        if was_preparing:
            set_labeled_action(self._play_btn, "▶ Play")
        if restore_controls and was_preparing:
            self._play_btn.setEnabled(
                self._current is not None
                and bool(self._player.tracks)
                and not self._exporting
                and not self._recording
            )

    def _begin_playback_preparation(self, *, autoplay: bool) -> None:
        take = self._current
        if (
            take is None
            or not self._player.has_studio_arrangement
            or self._waveform_shutdown
        ):
            return
        self._cancel_playback_preparation(restore_controls=False)
        cancel_event = threading.Event()
        self._playback_prepare_cancel = cancel_event
        generation = self._playback_prepare_generation
        take_path = take.path.expanduser().resolve()
        self._playback_preparing = True
        self._playback_prepare_autoplay = bool(autoplay)
        set_labeled_action(self._play_btn, "Preparing…")
        self._play_btn.setEnabled(False)
        self._hint.setText(
            "Preparing verified source media for playback. The original take "
            "will not be changed."
        )

        def cancel_check() -> None:
            if cancel_event.is_set():
                raise CancelledError()

        def worker() -> None:
            preparation: StudioPlaybackPreparation | None = None
            error: PlaybackError | None = None
            try:
                preparation = self._player.prepare_studio_playback(cancel_check)
                cancel_check()
            except CancelledError:
                if preparation is not None:
                    preparation.close()
                return
            except PlaybackError as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Unexpected Studio playback preparation failure")
                error = PlaybackError(str(exc))
            if cancel_event.is_set():
                if preparation is not None:
                    preparation.close()
                return
            self._playback_prepare_results.put(
                _PlaybackPreparationOutcome(
                    generation=generation,
                    take_path=take_path,
                    preparation=preparation,
                    error=error,
                )
            )

        self._playback_prepare_future = self._playback_prepare_executor.submit(worker)

    def _drain_playback_preparation_results(self) -> None:
        while True:
            try:
                outcome = self._playback_prepare_results.get_nowait()
            except queue.Empty:
                return
            current_path = (
                None
                if self._current is None
                else self._current.path.expanduser().resolve()
            )
            if (
                self._waveform_shutdown
                or outcome.generation != self._playback_prepare_generation
                or outcome.take_path != current_path
            ):
                if outcome.preparation is not None:
                    outcome.preparation.close()
                continue

            autoplay = self._playback_prepare_autoplay
            self._playback_prepare_future = None
            self._playback_preparing = False
            self._playback_prepare_autoplay = False
            set_labeled_action(self._play_btn, "▶ Play")
            self._play_btn.setEnabled(
                self._current is not None
                and bool(self._player.tracks)
                and not self._exporting
                and not self._recording
            )
            if outcome.error is not None:
                self._handle_playback_error(outcome.error)
                continue
            preparation = outcome.preparation
            if preparation is None:
                self._handle_playback_error(
                    PlaybackError("Studio playback preparation returned no stream.")
                )
                continue
            try:
                installed = self._player.install_studio_preparation(preparation)
            except PlaybackError as exc:
                self._handle_playback_error(exc)
                continue
            if not installed:
                continue
            if not autoplay:
                continue
            try:
                self._player.play()
                set_labeled_action(self._play_btn, "⏸ Pause")
                self._hint.setText(
                    "Playing the verified Studio arrangement. Every source "
                    "recording remains unchanged."
                )
            except PlaybackError as exc:
                self._handle_playback_error(exc)

    def _toggle_play(self) -> None:
        if self._current is None:
            return
        if self._playback_preparing:
            return
        if self._player.is_playing:
            self._player.pause()
            self._clear_playback_meters()
            set_labeled_action(self._play_btn, "▶ Play")
            return
        if (
            self._player.has_studio_arrangement
            and not self._player.studio_playback_prepared
        ):
            self._begin_playback_preparation(autoplay=True)
            return
        try:
            self._player.play()
            set_labeled_action(self._play_btn, "⏸ Pause")
        except PlaybackError as exc:
            self._handle_playback_error(exc)

    def _stop_playback(self) -> None:
        self._cancel_playback_preparation()
        self._player.stop()
        self._reset_playback_ui()

    def _reset_playback_ui(self) -> None:
        """Return transport and meters to their stopped presentation."""

        self._clear_playback_meters()
        set_labeled_action(self._play_btn, "▶ Play")
        self._scrub.setValue(0)
        if hasattr(self, "_studio_arrange"):
            self._studio_arrange.set_playhead(0)
        if not self._viewing_live:
            duration = max(1.0, self._player.duration_s)
            self._timeline_ruler.set_timeline(
                duration=duration,
                playhead=0.0,
                seek_enabled=bool(self._current is not None),
            )
            for lane in self._lanes.values():
                lane.waveform.set_playhead(0.0, duration)

    def overloaded_sources(self) -> tuple[bool, tuple[int, ...]]:
        """Return (master_overloaded, clipped_channel_ids) for this pass.

        Sticky within one playback epoch: a single clip stays reported
        until transport restarts or seeks, so a musician can act on a take
        that overloaded once mid-playback instead of missing the flash.
        """

        return self._master_overloaded, tuple(sorted(self._overloaded_lanes))

    def _clear_playback_meters(self) -> None:
        """Clear queued and visible levels without moving the transport."""

        with self._level_lock:
            self._pending_levels.clear()
            self._pending_stereo_levels.clear()
            self._pending_master_level = None
        self._overload_epoch = -1
        self._overloaded_lanes.clear()
        self._master_overloaded = False
        self._master_meter.set_stereo_levels(0.0, 0.0, clipped=False)
        for lane in self._lanes.values():
            lane.set_stereo_levels(0.0, 0.0, clipped=False)

    def _handle_playback_error(self, error: PlaybackError) -> None:
        """Drain a terminal player failure on the UI thread and explain it safely."""

        with self._level_lock:
            self._pending_playback_error = None
            self._pending_finished_epoch = None
        drained = self._player.drain_terminal_error()
        if drained is not None:
            error = drained
        else:
            # Startup failures already release their readers, but stop keeps
            # custom sinks and the exact transport position deterministic.
            self._player.stop()
        self._reset_playback_ui()
        if isinstance(error, StudioPlaybackSourceError):
            self._hint.setText(
                "Studio couldn't verify the arrangement's source media. Review or "
                "restore the affected take, then try again. Every recording is "
                "unchanged."
            )
        elif isinstance(error, PlaybackDeviceError):
            self._hint.setText(
                "Studio couldn't open the selected playback output. Choose "
                "another output in Recording Setup, then try again."
            )
        else:
            self._hint.setText(
                "Studio couldn't continue playback safely. Reopen the take, then "
                "try again. Every recording is unchanged."
            )

    def _seek_from_scrub(self) -> None:
        self._scrubbing = False
        if self._player.duration_s > 0:
            self._seek_from_ruler(
                self._scrub.value() / 1000.0 * self._player.duration_s
            )

    def _seek_from_ruler(self, seconds: float) -> None:
        """Seek the open recorded take from the common elapsed-time ruler."""
        if self._viewing_live or self._current is None or self._player.duration_s <= 0:
            return
        position = max(0.0, min(float(seconds), self._player.duration_s))
        try:
            self._player.seek(position)
        except PlaybackError as exc:
            self._handle_playback_error(exc)
            return
        self._sync_seek_ui(self._player.position_s)

    def _sync_seek_ui(
        self,
        position: float,
        *,
        arrange_frame: int | None = None,
    ) -> None:
        """Reflect one successful player seek without another unit conversion."""

        self._scrubbing = False
        self._scrub.setValue(int(position / self._player.duration_s * 1000))
        self._position.setText(
            f"{_fmt_time(position)} / {_fmt_time(self._player.duration_s)}"
        )
        self._timeline_ruler.set_timeline(
            duration=self._player.duration_s,
            playhead=position,
            seek_enabled=True,
        )
        for lane in self._lanes.values():
            lane.waveform.set_playhead(position, self._player.duration_s)
        if self._studio_state is not None:
            self._studio_arrange.set_playhead(
                arrange_frame
                if arrange_frame is not None
                else round(position * self._studio_state.project_sample_rate)
            )

    def _on_levels_bg(self, epoch: int, levels: dict[int, float]) -> None:
        with self._level_lock:
            for channel_id, level in levels.items():
                self._pending_levels[int(channel_id)] = (int(epoch), float(level))

    def _on_stereo_levels_bg(
        self,
        epoch: int,
        levels: dict[int, tuple[float, float, bool]],
    ) -> None:
        with self._level_lock:
            for channel_id, (left, right, clipped) in levels.items():
                key = int(channel_id)
                previous = self._pending_stereo_levels.get(key)
                self._pending_stereo_levels[key] = (
                    int(epoch),
                    left,
                    right,
                    bool(
                        clipped
                        or (
                            previous is not None
                            and previous[0] == int(epoch)
                            and previous[3]
                        )
                    ),
                )

    def _on_master_level_bg(
        self,
        epoch: int,
        level: tuple[float, float, bool],
    ) -> None:
        with self._level_lock:
            previous = self._pending_master_level
            self._pending_master_level = (
                int(epoch),
                level[0],
                level[1],
                bool(
                    level[2]
                    or (
                        previous is not None
                        and previous[0] == int(epoch)
                        and previous[3]
                    )
                ),
            )

    def _on_finished_bg(self, epoch: int) -> None:
        with self._level_lock:
            if (
                self._pending_finished_epoch is None
                or int(epoch) > self._pending_finished_epoch
            ):
                self._pending_finished_epoch = int(epoch)

    def _on_playback_error_bg(self, epoch: int, error: PlaybackError) -> None:
        with self._level_lock:
            pending = self._pending_playback_error
            if pending is None or int(epoch) > pending[0]:
                self._pending_playback_error = (int(epoch), error)

    def _tick(self) -> None:
        if self._recording:
            self._recording_elapsed += self._timer.interval() / 1000.0
            self._position.setText(f"REC {_fmt_time(self._recording_elapsed)}")
            timeline_duration = max(30.0, self._recording_elapsed)
            self._timeline_ruler.set_timeline(
                duration=timeline_duration,
                playhead=self._recording_elapsed,
                seek_enabled=False,
            )
            for lane in self._lanes.values():
                lane.waveform.set_playhead(self._recording_elapsed, timeline_duration)
        elif not self._viewing_live:
            pos = self._player.position_s
            duration = self._player.duration_s
            if self._studio_state is not None:
                self._studio_arrange.set_playhead(
                    round(pos * self._studio_state.project_sample_rate)
                )
            if not self._scrubbing and duration > 0:
                self._scrub.setValue(int(pos / duration * 1000))
            self._position.setText(f"{_fmt_time(pos)} / {_fmt_time(duration)}")
            self._timeline_ruler.set_timeline(
                duration=max(1.0, duration),
                playhead=pos,
                seek_enabled=bool(self._current is not None and duration > 0),
            )
            for lane in self._lanes.values():
                lane.waveform.set_playhead(pos, duration)
        self._player.drain_studio_notifications()
        with self._level_lock:
            pending, self._pending_levels = self._pending_levels, {}
            stereo, self._pending_stereo_levels = self._pending_stereo_levels, {}
            master = self._pending_master_level
            if master is not None:
                self._pending_master_level = (
                    master[0],
                    master[1],
                    master[2],
                    False,
                )
            playback_error = self._pending_playback_error
            self._pending_playback_error = None
            finished_epoch = self._pending_finished_epoch
            self._pending_finished_epoch = None
        current_epoch = self._player.playback_epoch
        if current_epoch != self._overload_epoch:
            # A new transport pass or seek: clear the sticky overload latch.
            self._overload_epoch = current_epoch
            self._overloaded_lanes = set()
            self._master_overloaded = False
        for channel_id, (epoch, level) in pending.items():
            if epoch != current_epoch:
                continue
            lane = self._lanes.get(int(channel_id))
            if lane is not None:
                lane.set_level(level)
        for channel_id, values in stereo.items():
            epoch, left, right, clipped = values
            if epoch != current_epoch:
                continue
            lane = self._lanes.get(int(channel_id))
            if lane is not None:
                if clipped:
                    self._overloaded_lanes.add(int(channel_id))
                lane.set_stereo_levels(
                    left,
                    right,
                    clipped=int(channel_id) in self._overloaded_lanes,
                )
        if master is not None and master[0] == current_epoch:
            _epoch, master_left, master_right, master_clipped = master
        else:
            master_left, master_right, master_clipped = 0.0, 0.0, False
        if master_clipped:
            self._master_overloaded = True
        self._master_meter.set_stereo_levels(
            master_left,
            master_right,
            clipped=self._master_overloaded,
        )
        self._drain_waveform_results()
        self._studio_waveforms.drain()
        self._drain_playback_preparation_results()
        # Preparation draining may start a new run in this same UI tick. Check
        # terminal notifications against the post-drain epoch, not the one that
        # was current when meter values were detached above.
        terminal_epoch = self._player.playback_epoch
        if playback_error is not None and playback_error[0] == terminal_epoch:
            self._handle_playback_error(playback_error[1])
        elif finished_epoch == terminal_epoch:
            self._player.stop()
            set_labeled_action(self._play_btn, "▶ Play")
            self._scrub.setValue(0)
            if not self._viewing_live:
                self._timeline_ruler.set_timeline(
                    duration=max(1.0, self._player.duration_s),
                    playhead=0.0,
                    seek_enabled=bool(self._current is not None),
                )
        self._drain_export_results()

    def prepare_close(self) -> bool:
        """Synchronously persist Studio edits without making the UI unusable."""

        if self._waveform_shutdown:
            return True
        return self._flush_studio_state()

    def shutdown(self) -> bool:
        if self._waveform_shutdown:
            return True
        if not self.prepare_close():
            return False
        self._waveform_shutdown = True
        self._studio_controller.shutdown()
        self._export_generation += 1
        self._export_cancel.set()
        self._exporting = False
        self._cancel_playback_preparation(restore_controls=False)
        self._studio_waveform_schedule_timer.stop()
        self._studio_waveforms.shutdown()
        self._cancel_waveform_jobs()
        self._waveform_executor.shutdown(wait=False, cancel_futures=True)
        self._playback_prepare_executor.shutdown(wait=True, cancel_futures=True)
        while True:
            try:
                outcome = self._playback_prepare_results.get_nowait()
            except queue.Empty:
                break
            if outcome.preparation is not None:
                outcome.preparation.close()
        self._timer.stop()
        self._player.stop()
        return True

    def hideEvent(self, event) -> None:  # noqa: N802
        """Release playback when the integrated Studio workspace is left.

        Studio lives in a stacked workspace rather than a separate closeable
        window.  A stack switch emits a hide event, so this is the lifecycle
        boundary that must stop the output stream and close source readers.
        """
        self._flush_studio_state()
        self._stop_playback()
        super().hideEvent(event)

    @property
    def export_in_progress(self) -> bool:
        return self._exporting
