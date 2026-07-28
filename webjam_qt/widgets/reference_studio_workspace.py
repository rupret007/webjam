"""Project-first Reference Studio workspace and command surface.

The widget owns presentation and semantic commands, not files or audio.  A
controller supplies immutable project/document snapshots and handles every
emitted command.  Keeping that boundary explicit lets headless tests exercise
the full menu and keyboard surface without opening devices or mutating a song.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMenuBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.studio_project import StudioDocument
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.studio_arrange import StudioArrange


@dataclass(frozen=True, slots=True)
class ReferenceStudioPresentation:
    """Path-free, bounded values rendered by the project workspace."""

    project_name: str = "Untitled Song"
    save_state: str = "Not saved"
    status: str = "Ready"
    backing_track: str = "No backing track"
    position_text: str = "1 1 1 000"
    duration_text: str = "0:00.000"
    track_names: tuple[str, ...] = ()
    dirty: bool = False
    playing: bool = False
    recording: bool = False
    can_save: bool = False
    can_play: bool = False
    can_record: bool = False
    can_bounce: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "project_name",
            "save_state",
            "status",
            "backing_track",
            "position_text",
            "duration_text",
        ):
            value = " ".join(str(getattr(self, field_name) or "").split())
            if not value or len(value.encode("utf-8")) > 1_024:
                raise ValueError(f"{field_name} is invalid.")
            object.__setattr__(self, field_name, value)
        names = tuple(" ".join(str(item or "").split()) for item in self.track_names)
        if len(names) > 512 or any(
            not item or len(item.encode("utf-8")) > 512 for item in names
        ):
            raise ValueError("track_names are invalid.")
        object.__setattr__(self, "track_names", names)
        for field_name in (
            "dirty",
            "playing",
            "recording",
            "can_save",
            "can_play",
            "can_record",
            "can_bounce",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be true or false.")


class ReferenceStudioWorkspace(QWidget):
    """Accessible arrangement workspace with one complete command vocabulary."""

    command_requested = Signal(str)
    tempo_changed = Signal(float)
    time_signature_changed = Signal(int, int)
    snap_changed = Signal(str)
    track_selected = Signal(int)
    files_dropped = Signal(object)

    COMMAND_IDS = frozenset(
        {
            "new_project",
            "open_project",
            "save_project",
            "save_project_as",
            "import_backing",
            "import_media",
            "collect_media",
            "relink_media",
            "bounce",
            "close_project",
            "undo",
            "redo",
            "cut",
            "copy",
            "paste",
            "delete",
            "select_all",
            "new_audio_track",
            "rename_track",
            "duplicate_track",
            "remove_track",
            "arm_selected_track",
            "map_track_input",
            "split_region",
            "join_regions",
            "loop_region",
            "add_marker",
            "add_section",
            "create_take_lane",
            "quick_swipe_comp",
            "show_mixer",
            "show_media_bin",
            "show_automation",
            "play_pause",
            "stop",
            "record",
            "return_to_start",
            "toggle_cycle",
            "toggle_metronome",
            "toggle_count_in",
            "toggle_ruler",
            "latency_calibration",
            "analyze_tempo",
            "project_settings",
            "open_guide",
        }
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReferenceStudioWorkspace")
        self.setAccessibleName("Reference Studio project workspace")
        self.setAccessibleDescription(
            "Arrange a backing track and recordings, control transport, mix, and bounce."
        )
        # The workspace is embedded below WebJam's live header/HUD/control
        # chrome. Keep its own floor compact enough for the application's
        # supported 760×600 window; the Arrange pane still receives the
        # remaining stretch.
        self.setMinimumSize(640, 360)
        self.setAcceptDrops(True)
        self._presentation = ReferenceStudioPresentation()
        self._actions: dict[str, QAction] = {}
        self._document: StudioDocument | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setObjectName("ReferenceStudioMenuBar")
        self.menu_bar.setAccessibleName("Reference Studio menus")
        root.setMenuBar(self.menu_bar)
        self._build_menus()

        header = QFrame()
        header.setObjectName("ReferenceStudioHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        header_layout.setSpacing(Space.SM)
        self.home_button = QToolButton()
        self.home_button.setObjectName("ReferenceStudioHomeButton")
        self.home_button.setAccessibleName("Return to Reference Studio home")
        self.home_button.setToolTip("Reference Studio Home")
        self.home_button.setIcon(BrandMarkIcon.icon())
        self.home_button.clicked.connect(
            lambda: self._emit_command("close_project")
        )
        header_layout.addWidget(self.home_button)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.project_title = QLabel("Untitled Song")
        self.project_title.setObjectName("ReferenceStudioProjectTitle")
        self.project_title.setAccessibleName("Project name")
        self.project_state = QLabel("Not saved")
        self.project_state.setObjectName("ReferenceStudioProjectState")
        self.project_state.setAccessibleName("Project save status")
        titles.addWidget(self.project_title)
        titles.addWidget(self.project_state)
        header_layout.addLayout(titles)
        header_layout.addStretch(1)
        for command, text, accessible in (
            ("import_backing", "Import Backing…", "Import a local backing track"),
            ("save_project", "Save", "Save project"),
            ("bounce", "Bounce…", "Bounce project audio"),
        ):
            button = QPushButton(text)
            button.setObjectName(
                "ReferenceStudioBounce"
                if command == "bounce"
                else "ReferenceStudioHeaderButton"
            )
            button.setAccessibleName(accessible)
            button.clicked.connect(
                lambda _checked=False, item=command: self._emit_command(item)
            )
            header_layout.addWidget(button)
            if command == "save_project":
                self.save_button = button
            elif command == "bounce":
                self.bounce_button = button
            else:
                self.import_backing_button = button
        root.addWidget(header)

        transport = QFrame()
        transport.setObjectName("ReferenceStudioTransport")
        transport_layout = QHBoxLayout(transport)
        transport_layout.setContentsMargins(Space.MD, Space.XS, Space.MD, Space.XS)
        transport_layout.setSpacing(Space.XS)
        self.return_button = self._transport_button(
            "↤", "Return to project start", "return_to_start"
        )
        self.stop_button = self._transport_button("■", "Stop", "stop")
        self.play_button = self._transport_button("▶", "Play or pause", "play_pause")
        self.record_button = self._transport_button("●", "Record armed tracks", "record")
        self.record_button.setObjectName("ReferenceStudioRecordButton")
        for button in (
            self.return_button,
            self.stop_button,
            self.play_button,
            self.record_button,
        ):
            transport_layout.addWidget(button)
        self.cycle_box = self._transport_check(
            "Cycle", "Loop the selected cycle range", "toggle_cycle"
        )
        self.metronome_box = self._transport_check(
            "Click", "Play the project metronome", "toggle_metronome"
        )
        self.count_in_box = self._transport_check(
            "Count-in", "Play a count-in before recording", "toggle_count_in"
        )
        transport_layout.addWidget(self.cycle_box)
        transport_layout.addWidget(self.metronome_box)
        transport_layout.addWidget(self.count_in_box)
        transport_layout.addSpacing(Space.SM)
        self.position = QLabel("1 1 1 000")
        self.position.setObjectName("ReferenceStudioPosition")
        self.position.setAccessibleName("Playhead musical position")
        self.elapsed = QLabel("0:00.000")
        self.elapsed.setObjectName("ReferenceStudioElapsed")
        self.elapsed.setAccessibleName("Playhead elapsed time")
        transport_layout.addWidget(self.position)
        transport_layout.addWidget(self.elapsed)
        transport_layout.addStretch(1)
        tempo_label = QLabel("BPM")
        tempo_label.setObjectName("ReferenceStudioTransportLabel")
        transport_layout.addWidget(tempo_label)
        self.tempo = QDoubleSpinBox()
        self.tempo.setObjectName("ReferenceStudioTempo")
        self.tempo.setAccessibleName("Project tempo in beats per minute")
        self.tempo.setRange(20.0, 400.0)
        self.tempo.setDecimals(2)
        self.tempo.setSingleStep(0.5)
        self.tempo.setValue(120.0)
        self.tempo.editingFinished.connect(
            lambda: self.tempo_changed.emit(float(self.tempo.value()))
        )
        transport_layout.addWidget(self.tempo)
        self.time_signature = QComboBox()
        self.time_signature.setObjectName("ReferenceStudioTimeSignature")
        self.time_signature.setAccessibleName("Project time signature")
        self.time_signature.addItems(("4/4", "3/4", "6/8", "2/4", "5/4", "7/8"))
        self.time_signature.currentTextChanged.connect(self._time_signature_edited)
        transport_layout.addWidget(self.time_signature)
        self.snap_label = QLabel("Snap")
        self.snap_label.setObjectName("ReferenceStudioTransportLabel")
        transport_layout.addWidget(self.snap_label)
        self.snap = QComboBox()
        self.snap.setObjectName("ReferenceStudioSnap")
        self.snap.setAccessibleName("Arrange snap resolution")
        self.snap.addItem("Bar", "bar")
        self.snap.addItem("Beat", "beat")
        self.snap.addItem("1/8", "eighth")
        self.snap.addItem("1/16", "sixteenth")
        self.snap.addItem("Off", "off")
        self.snap.currentIndexChanged.connect(
            lambda _index: self.snap_changed.emit(str(self.snap.currentData()))
        )
        transport_layout.addWidget(self.snap)
        root.addWidget(transport)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("ReferenceStudioSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        library = QFrame()
        library.setObjectName("ReferenceStudioTrackLibrary")
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        library_title = QLabel("TRACKS")
        library_title.setObjectName("ReferenceStudioSectionTitle")
        library_layout.addWidget(library_title)
        self.track_list = QListWidget()
        self.track_list.setObjectName("ReferenceStudioTrackList")
        self.track_list.setAccessibleName("Project tracks")
        self.track_list.currentRowChanged.connect(self.track_selected.emit)
        library_layout.addWidget(self.track_list, 1)
        add_track = QPushButton("＋ Audio Track")
        add_track.setObjectName("ReferenceStudioAddTrack")
        add_track.setAccessibleName("Add audio track")
        add_track.clicked.connect(lambda: self._emit_command("new_audio_track"))
        library_layout.addWidget(add_track)
        self.splitter.addWidget(library)

        editor = QFrame()
        editor.setObjectName("ReferenceStudioEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        editor_layout.setSpacing(Space.XS)
        backing_row = QHBoxLayout()
        backing_label = QLabel("REFERENCE / BACKING")
        backing_label.setObjectName("ReferenceStudioSectionTitle")
        self.backing_name = QLabel("No backing track")
        self.backing_name.setObjectName("ReferenceStudioBackingName")
        self.backing_name.setAccessibleName("Backing track")
        backing_row.addWidget(backing_label)
        backing_row.addWidget(self.backing_name, 1)
        editor_layout.addLayout(backing_row)
        self.arrange = StudioArrange()
        self.arrange.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        editor_layout.addWidget(self.arrange, 1)
        self.splitter.addWidget(editor)

        inspector = QFrame()
        inspector.setObjectName("ReferenceStudioInspector")
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        inspector_layout.setSpacing(Space.SM)
        inspector_title = QLabel("INSPECTOR / MIX")
        inspector_title.setObjectName("ReferenceStudioSectionTitle")
        inspector_layout.addWidget(inspector_title)
        self.inspector_summary = QLabel(
            "Select a track or region to edit gain, pan, fades, takes, and automation."
        )
        self.inspector_summary.setObjectName("ReferenceStudioInspectorSummary")
        self.inspector_summary.setWordWrap(True)
        self.inspector_summary.setAccessibleName("Track and region inspector")
        inspector_layout.addWidget(self.inspector_summary)
        for command, text in (
            ("show_mixer", "Open Mixer"),
            ("show_automation", "Show Automation"),
            ("map_track_input", "Input Mapping…"),
            ("latency_calibration", "Latency Calibration…"),
        ):
            button = QPushButton(text)
            button.setObjectName("ReferenceStudioInspectorButton")
            button.setAccessibleName(text.replace("…", ""))
            button.clicked.connect(
                lambda _checked=False, item=command: self._emit_command(item)
            )
            inspector_layout.addWidget(button)
        inspector_layout.addStretch(1)
        self.splitter.addWidget(inspector)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([190, 760, 230])
        root.addWidget(self.splitter, 1)

        footer = QFrame()
        footer.setObjectName("ReferenceStudioFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(Space.MD, Space.XS, Space.MD, Space.XS)
        self.status = QLabel("Ready")
        self.status.setObjectName("ReferenceStudioStatus")
        self.status.setAccessibleName("Reference Studio status")
        self.status.setWordWrap(True)
        footer_layout.addWidget(self.status, 1)
        self.audio_truth = QLabel("Studio audio is separate from Jamulus")
        self.audio_truth.setObjectName("ReferenceStudioAudioTruth")
        self.audio_truth.setAccessibleName("Live audio isolation status")
        footer_layout.addWidget(self.audio_truth)
        root.addWidget(footer)

        QWidget.setTabOrder(self.home_button, self.import_backing_button)
        QWidget.setTabOrder(self.import_backing_button, self.save_button)
        QWidget.setTabOrder(self.save_button, self.bounce_button)
        QWidget.setTabOrder(self.bounce_button, self.return_button)
        QWidget.setTabOrder(self.return_button, self.stop_button)
        QWidget.setTabOrder(self.stop_button, self.play_button)
        QWidget.setTabOrder(self.play_button, self.record_button)
        QWidget.setTabOrder(self.record_button, self.cycle_box)
        QWidget.setTabOrder(self.cycle_box, self.metronome_box)
        QWidget.setTabOrder(self.metronome_box, self.count_in_box)
        QWidget.setTabOrder(self.count_in_box, self.tempo)
        QWidget.setTabOrder(self.tempo, self.time_signature)
        QWidget.setTabOrder(self.time_signature, self.snap)
        QWidget.setTabOrder(self.snap, self.track_list)
        self.set_presentation(self._presentation)

    def _action(
        self,
        menu: QMenu,
        command: str,
        text: str,
        shortcut: str | QKeySequence | None = None,
        *,
        checkable: bool = False,
    ) -> QAction:
        if command not in self.COMMAND_IDS or command in self._actions:
            raise ValueError("Reference Studio command registration is invalid.")
        action = QAction(text, self)
        action.setObjectName(f"ReferenceStudioAction_{command}")
        action.setData(command)
        action.setCheckable(checkable)
        if shortcut:
            sequence = QKeySequence(shortcut)
            # Qt maps "Ctrl" to Command on macOS. Command+M is the native
            # Minimize Window shortcut, so use the physical Control key for
            # the Studio mixer there. The live-session Control+M shortcut is
            # scoped to the live workspace and cannot compete here.
            if command == "show_mixer" and sys.platform == "darwin":
                sequence = QKeySequence(
                    Qt.KeyboardModifier.MetaModifier.value
                    | Qt.Key.Key_M.value
                )
            action.setShortcut(sequence)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        action.triggered.connect(
            lambda _checked=False, item=command: self._emit_command(item)
        )
        menu.addAction(action)
        self._actions[command] = action
        return action

    def _build_menus(self) -> None:
        file_menu = self.menu_bar.addMenu("&File")
        self._action(file_menu, "new_project", "&New Project", QKeySequence.StandardKey.New)
        self._action(file_menu, "open_project", "&Open Project…", QKeySequence.StandardKey.Open)
        file_menu.addSeparator()
        self._action(file_menu, "save_project", "&Save", QKeySequence.StandardKey.Save)
        self._action(
            file_menu,
            "save_project_as",
            "Save &As…",
            QKeySequence.StandardKey.SaveAs,
        )
        file_menu.addSeparator()
        self._action(file_menu, "import_backing", "Import &Backing Track…")
        self._action(file_menu, "import_media", "Import &Media…")
        self._action(
            file_menu,
            "collect_media",
            "About &Collected Project Media…",
        )
        self._action(file_menu, "relink_media", "&Relink Backing Track…")
        file_menu.addSeparator()
        self._action(file_menu, "bounce", "&Bounce…", "Ctrl+B")
        self._action(file_menu, "close_project", "&Close Project", QKeySequence.StandardKey.Close)

        edit_menu = self.menu_bar.addMenu("&Edit")
        self._action(edit_menu, "undo", "&Undo", QKeySequence.StandardKey.Undo)
        self._action(edit_menu, "redo", "&Redo", QKeySequence.StandardKey.Redo)
        edit_menu.addSeparator()
        self._action(edit_menu, "cut", "Cu&t", QKeySequence.StandardKey.Cut)
        self._action(edit_menu, "copy", "&Copy", QKeySequence.StandardKey.Copy)
        self._action(edit_menu, "paste", "&Paste", QKeySequence.StandardKey.Paste)
        self._action(edit_menu, "delete", "&Delete", QKeySequence.StandardKey.Delete)
        select_all = self._action(
            edit_menu,
            "select_all",
            "Select &All",
            QKeySequence.StandardKey.SelectAll,
        )
        select_all.setEnabled(False)
        select_all.setToolTip(
            "Reference Studio currently edits one selected region at a time."
        )

        track_menu = self.menu_bar.addMenu("&Track")
        self._action(track_menu, "new_audio_track", "New &Audio Track", "Ctrl+Shift+N")
        self._action(track_menu, "rename_track", "&Rename Track…")
        self._action(track_menu, "duplicate_track", "D&uplicate Track")
        self._action(track_menu, "remove_track", "Remove Track")
        track_menu.addSeparator()
        self._action(track_menu, "arm_selected_track", "&Arm Selected Track", "R")
        self._action(track_menu, "map_track_input", "Input &Mapping…")
        self._action(track_menu, "latency_calibration", "&Latency Calibration…")

        region_menu = self.menu_bar.addMenu("&Region")
        self._action(region_menu, "split_region", "&Split at Playhead", "Ctrl+T")
        self._action(region_menu, "join_regions", "&Join Selected Regions")
        self._action(region_menu, "loop_region", "&Loop Selected Region")
        region_menu.addSeparator()
        self._action(region_menu, "create_take_lane", "About &Take Lanes…")
        self._action(region_menu, "quick_swipe_comp", "&Quick-Swipe Comp")
        region_menu.addSeparator()
        self._action(region_menu, "add_marker", "Add &Marker")
        self._action(region_menu, "add_section", "Add Song &Section")

        mix_menu = self.menu_bar.addMenu("&Mix")
        self._action(mix_menu, "show_mixer", "Show &Mixer", "Ctrl+M")
        self._action(mix_menu, "show_automation", "Show &Automation", "A")

        transport_menu = self.menu_bar.addMenu("&Transport")
        self._action(transport_menu, "play_pause", "&Play / Pause", "Space")
        self._action(transport_menu, "stop", "&Stop", "Shift+Space")
        self._action(transport_menu, "record", "&Record", "Ctrl+R")
        self._action(transport_menu, "return_to_start", "Return to &Start", "Home")
        transport_menu.addSeparator()
        self._action(
            transport_menu, "toggle_cycle", "&Cycle", "C", checkable=True
        )
        self._action(
            transport_menu,
            "toggle_metronome",
            "&Metronome",
            "K",
            checkable=True,
        )
        self._action(
            transport_menu,
            "toggle_count_in",
            "Count-&in",
            checkable=True,
        )

        view_menu = self.menu_bar.addMenu("&View")
        self._action(view_menu, "show_media_bin", "Show Media &Bin")
        self._action(view_menu, "toggle_ruler", "Toggle Time / &Bars and Beats")

        project_menu = self.menu_bar.addMenu("&Project")
        self._action(project_menu, "analyze_tempo", "Analyze Backing &Tempo…")
        project_menu.addSeparator()
        self._action(project_menu, "project_settings", "Project &Settings…")

        help_menu = self.menu_bar.addMenu("&Help")
        self._action(help_menu, "open_guide", "Reference Studio &Guide")

    def _transport_button(
        self,
        text: str,
        accessible_name: str,
        command: str,
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("ReferenceStudioTransportButton")
        button.setText(text)
        button.setAccessibleName(accessible_name)
        button.setToolTip(accessible_name)
        button.clicked.connect(lambda: self._emit_command(command))
        return button

    def _transport_check(
        self,
        text: str,
        accessible_name: str,
        command: str,
    ) -> QCheckBox:
        control = QCheckBox(text)
        control.setObjectName("ReferenceStudioTransportCheck")
        control.setAccessibleName(accessible_name)
        control.clicked.connect(lambda: self._emit_command(command))
        return control

    def _time_signature_edited(self, value: str) -> None:
        try:
            numerator_text, denominator_text = value.split("/", 1)
            numerator = int(numerator_text)
            denominator = int(denominator_text)
        except (AttributeError, TypeError, ValueError):
            return
        self.time_signature_changed.emit(numerator, denominator)

    def _emit_command(self, command: str) -> None:
        if command not in self.COMMAND_IDS:
            raise ValueError("Unknown Reference Studio command.")
        self.command_requested.emit(command)

    @property
    def actions(self) -> dict[str, QAction]:
        return dict(self._actions)

    @property
    def presentation(self) -> ReferenceStudioPresentation:
        return self._presentation

    def set_presentation(self, value: ReferenceStudioPresentation) -> None:
        if not isinstance(value, ReferenceStudioPresentation):
            raise TypeError("value must be a ReferenceStudioPresentation.")
        self._presentation = value
        title = value.project_name + (" •" if value.dirty else "")
        self.project_title.setText(title)
        self.project_state.setText(value.save_state)
        self.status.setText(value.status)
        self.backing_name.setText(value.backing_track)
        self.position.setText(value.position_text)
        self.elapsed.setText(value.duration_text)
        self.play_button.setText("❚❚" if value.playing else "▶")
        self.play_button.setAccessibleName("Pause" if value.playing else "Play")
        self.record_button.setText("■" if value.recording else "●")
        self.record_button.setAccessibleName(
            "Stop recording" if value.recording else "Record armed tracks"
        )
        self.record_button.setAccessibleDescription(
            "Stop and safely commit the current Studio recording."
            if value.recording
            else "Record every armed track using its mapped input."
        )
        self.record_button.setProperty("recording", value.recording)
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.save_button.setEnabled(value.can_save)
        self.bounce_button.setEnabled(value.can_bounce)
        self.play_button.setEnabled(value.can_play)
        self.stop_button.setEnabled(value.can_play or value.recording)
        self.record_button.setEnabled(value.can_record)
        self._actions["save_project"].setEnabled(value.can_save)
        self._actions["bounce"].setEnabled(value.can_bounce)
        for command in ("play_pause", "stop", "return_to_start"):
            self._actions[command].setEnabled(value.can_play or value.recording)
        self._actions["record"].setEnabled(value.can_record)
        existing_names = tuple(
            str(self.track_list.item(index).data(Qt.ItemDataRole.UserRole + 1) or "")
            for index in range(self.track_list.count())
        )
        if existing_names != value.track_names:
            selected = self.track_list.currentRow()
            self.track_list.blockSignals(True)
            try:
                self.track_list.clear()
                for index, name in enumerate(value.track_names, start=1):
                    item = QListWidgetItem(f"{index:02d}  {name}")
                    item.setData(Qt.ItemDataRole.UserRole, index - 1)
                    item.setData(Qt.ItemDataRole.UserRole + 1, name)
                    item.setData(
                        Qt.ItemDataRole.AccessibleTextRole,
                        f"Track {index}, {name}",
                    )
                    self.track_list.addItem(item)
                if self.track_list.count():
                    self.track_list.setCurrentRow(
                        min(max(0, selected), self.track_list.count() - 1)
                    )
            finally:
                self.track_list.blockSignals(False)

    def set_document(self, document: StudioDocument | None) -> None:
        if document is not None and not isinstance(document, StudioDocument):
            raise TypeError("document must be a StudioDocument or null.")
        self._document = document
        self.arrange.set_document(document)

    def set_project_controls(
        self,
        *,
        tempo_bpm: float,
        numerator: int,
        denominator: int,
        snap_mode: str,
        metronome: bool,
        cycle: bool,
        count_in: bool,
    ) -> None:
        """Render controller-owned settings without emitting edit requests."""

        controls = (
            self.tempo,
            self.time_signature,
            self.snap,
            self.metronome_box,
            self.cycle_box,
            self.count_in_box,
        )
        for control in controls:
            control.blockSignals(True)
        try:
            self.tempo.setValue(float(tempo_bpm))
            signature = f"{int(numerator)}/{int(denominator)}"
            index = self.time_signature.findText(signature)
            if index < 0:
                self.time_signature.addItem(signature)
                index = self.time_signature.findText(signature)
            self.time_signature.setCurrentIndex(index)
            snap_index = self.snap.findData(str(snap_mode))
            if snap_index >= 0:
                self.snap.setCurrentIndex(snap_index)
            self.metronome_box.setChecked(bool(metronome))
            self.cycle_box.setChecked(bool(cycle))
            self.count_in_box.setChecked(bool(count_in))
            for command, checked in (
                ("toggle_metronome", metronome),
                ("toggle_cycle", cycle),
                ("toggle_count_in", count_in),
            ):
                self._actions[command].setChecked(bool(checked))
        finally:
            for control in controls:
                control.blockSignals(False)

    @property
    def selected_track_index(self) -> int:
        return int(self.track_list.currentRow())

    def set_compact(self, compact: bool) -> None:
        """Keep the Arrange surface useful at the supported 760px floor."""

        if not isinstance(compact, bool):
            raise TypeError("compact must be true or false.")
        inspector = self.splitter.widget(2)
        inspector.setVisible(not compact)
        # These three toggles remain fully available (and checked truthfully)
        # in the Transport menu. Hiding only their duplicate toolbar controls
        # preserves tempo, meter, signature, and snap without horizontally
        # clipping them at the supported compact width.
        for control in (
            self.cycle_box,
            self.metronome_box,
            self.count_in_box,
        ):
            control.setVisible(not compact)
        self.splitter.setSizes([150, 610, 0] if compact else [190, 760, 230])

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.set_compact(self.width() < 1_080)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        urls = event.mimeData().urls()
        if urls and all(item.isLocalFile() for item in urls):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        urls = event.mimeData().urls()
        if urls and all(item.isLocalFile() for item in urls):
            self.files_dropped.emit(tuple(item.toLocalFile() for item in urls))
            event.acceptProposedAction()


class BrandMarkIcon:
    """Tiny lazy adapter that avoids manufacturing an icon before QApplication."""

    @staticmethod
    def icon():
        from webjam_qt.theme.brand import make_brand_icon

        return make_brand_icon()


__all__ = [
    "ReferenceStudioPresentation",
    "ReferenceStudioWorkspace",
]
