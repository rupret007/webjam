"""Focused recording preferences for the integrated Studio."""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.audio_routing import list_input_devices
from core.creative_modes import CreatorProfile, get_creator_profile_by_key
from core.meeting_link import (
    COMPACT_MEETING_CAPTURE_NOTICE,
    RECORD_SESSION_MEETING_CAPTURE_NOTICE,
)
from core.settings import AppSettings, save_settings
from webjam_qt.theme.tokens import Space


LOGGER = logging.getLogger("webjam.qt.recording_setup")


def _resolve_creator_profile(
    creator_profile: CreatorProfile | str | None,
) -> CreatorProfile:
    if creator_profile is None:
        profile = get_creator_profile_by_key("music")
    elif isinstance(creator_profile, CreatorProfile):
        profile = get_creator_profile_by_key(creator_profile.key)
    elif isinstance(creator_profile, str):
        profile = get_creator_profile_by_key(creator_profile)
    else:
        raise TypeError("creator_profile must be a CreatorProfile or profile key.")
    if profile is None:
        raise ValueError("creator profile is unsupported.")
    return profile


class LocalOriginalsChoiceDialog(QDialog):
    """Ask the one first-recording question without touching live audio.

    The shared WebJam take is always available on the host. Keeping this Mac's
    configured interface inputs is a separate, explicit recording choice and
    therefore belongs here—not in Host, Join, or live-audio setup.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        creator_profile: CreatorProfile | str | None = None,
    ) -> None:
        super().__init__(parent)
        profile = _resolve_creator_profile(creator_profile)
        self._creator_profile = profile
        self.choice = ""
        self.setObjectName("LocalOriginalsChoiceDialog")

        if profile.key == "podcast_voice":
            title_text = "Keep a local voice original?"
            detail_text = (
                "WebJam records the synchronized voice take either way. Optionally "
                "keep selected Local Original inputs for Podcast & Voice Studio. "
                f"{COMPACT_MEETING_CAPTURE_NOTICE}"
            )
            accessible_detail_text = (
                "WebJam records the shared synchronized voice take from this "
                "session either way. You can also keep selected inputs from this "
                "Mac as Local Originals for Podcast & Voice Studio. "
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
            )
            shared_text = "Record Shared Voice Take Only"
            shared_accessible_name = "Record shared WebJam voice take only"
        elif profile.key == "review_rehearsal":
            title_text = "Keep optional Local Originals?"
            detail_text = (
                "WebJam records synchronized audio either way. Optionally keep "
                "selected Local Original inputs for source review; editing/export "
                f"are unavailable. {COMPACT_MEETING_CAPTURE_NOTICE}"
            )
            accessible_detail_text = (
                "WebJam records the shared synchronized WebJam audio take either "
                "way. You can also keep selected inputs from this Mac as Local "
                "Originals for playback and source review. Completed-take review "
                "is playback-only: take editing and track export are unavailable. "
                f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
            )
            shared_text = "Record Shared WebJam Audio Only"
            shared_accessible_name = "Record shared WebJam audio take only"
        else:
            # Preserve the shipped Music wording exactly.
            title_text = "Keep a local original?"
            detail_text = (
                "WebJam will record the shared Jamulus take either way. You can "
                "also keep configured inputs from this Mac as separate Local "
                "Originals for Studio later. This does not change Jamulus "
                "audio settings."
            )
            accessible_detail_text = detail_text
            shared_text = "Record Shared Jam Only"
            shared_accessible_name = "Record shared Jamulus take only"

        self.setWindowTitle(title_text)
        self.setAccessibleName(title_text)
        self.setAccessibleDescription(accessible_detail_text)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.resize(600, 310)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.SM)

        title = QLabel(title_text)
        title.setObjectName("SimpleSettingsTitle")
        root.addWidget(title)

        detail = QLabel(detail_text)
        detail.setObjectName("SimpleSettingsSubtitle")
        detail.setWordWrap(True)
        detail.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        root.addWidget(detail)
        root.addStretch(1)

        shared = QPushButton(shared_text)
        shared.setObjectName("PrimaryButton")
        shared.setAccessibleName(shared_accessible_name)
        shared.clicked.connect(self._record_shared)
        root.addWidget(shared)

        local = QPushButton("Also Keep This Mac’s Inputs")
        local.setObjectName("GhostButton")
        local.setAccessibleName("Configure Local Originals from this Mac")
        local.clicked.connect(self._configure_local)
        root.addWidget(local)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("GhostButton")
        cancel.clicked.connect(self.reject)
        root.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignRight)

    def _record_shared(self) -> None:
        self.choice = "shared"
        self.accept()

    def _configure_local(self) -> None:
        self.choice = "local"
        self.accept()


class RecordingSetupDialog(QDialog):
    """Configure explicit local-original recording consent and storage."""

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
        *,
        local_originals_available: bool = True,
        takes_folder_editable: bool = True,
        creator_profile: CreatorProfile | str | None = None,
    ) -> None:
        super().__init__(parent)
        profile = _resolve_creator_profile(creator_profile)
        # Edit a draft. A failed atomic save must never leave the running
        # controller believing that unsaved preferences are active.
        self._settings = deepcopy(settings)
        self._creator_profile = profile
        self._local_originals_available = bool(local_originals_available)
        self._takes_folder_editable = bool(takes_folder_editable)
        self.setObjectName("RecordingSetupDialog")
        self.setWindowTitle("WebJam Recording Setup")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.resize(620, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        scroll = QScrollArea()
        scroll.setObjectName("RecordingSetupScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setAccessibleName("Recording setup options")
        body = QWidget()
        content = QVBoxLayout(body)
        content.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        content.setContentsMargins(0, 0, Space.SM, 0)
        content.setSpacing(Space.MD)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        if profile.key == "podcast_voice":
            subtitle_text = (
                (
                    "The host records the synchronized WebJam voice take. Choose "
                    "whether this Mac also keeps its configured interface inputs "
                    "as Local Originals. Podcast & Voice Studio chooses its own "
                    "playback output when you review a take. "
                    f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
                )
                if self._local_originals_available
                else (
                    "The host records the synchronized WebJam voice take. Podcast "
                    "& Voice Studio chooses its own playback output when you "
                    f"review a take. {RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
                )
            )
            unavailable_text = (
                "Local originals are unavailable for this recording session. You "
                "can still participate normally, and the host's synchronized "
                "WebJam server track is kept."
            )
            capture_help_text = (
                "Name mono or stereo voice tracks with Edit Voice Tracks. Enabled "
                "Local Originals use device inputs sequentially, up to 32 channels "
                "total. An empty track list keeps the compatible input 1–2 default. "
                "The device must run at 48 kHz and be shareable with WebJam’s "
                "Jamulus audio path. WebJam records only after the host confirms a "
                "take."
            )
            edit_tracks_text = "Edit Voice Tracks…"
            edit_tracks_accessible_name = (
                "Edit the named local voice tracks Record Session captures"
            )
            locked_folder_tooltip = (
                "End or restart the current recording session before changing its "
                "Takes folder."
            )
        elif profile.key == "review_rehearsal":
            subtitle_text = (
                (
                    "The host records the synchronized WebJam audio take. Choose "
                    "whether this Mac also keeps its configured interface inputs "
                    "as Local Originals. Completed-take review is playback-only; "
                    "take editing and track export are unavailable. "
                    f"{RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
                )
                if self._local_originals_available
                else (
                    "The host records the synchronized WebJam audio take. "
                    "Completed-take review is playback-only; take editing and track "
                    f"export are unavailable. {RECORD_SESSION_MEETING_CAPTURE_NOTICE}"
                )
            )
            unavailable_text = (
                "Local originals are unavailable for this review session. You can "
                "still participate normally, and the host's synchronized WebJam "
                "server track is kept."
            )
            capture_help_text = (
                "Name mono or stereo sources with Configure Input Sources. Enabled "
                "Local Originals use device inputs sequentially, up to 32 channels "
                "total. An empty source list keeps the compatible input 1–2 default. "
                "The device must run at 48 kHz and be shareable with WebJam’s "
                "Jamulus audio path. WebJam records only after the host confirms a "
                "take."
            )
            edit_tracks_text = "Configure Input Sources…"
            edit_tracks_accessible_name = (
                "Configure the named local input sources Record Session captures"
            )
            locked_folder_tooltip = (
                "End or restart the current review session before changing its "
                "Takes folder."
            )
        else:
            # Preserve the shipped Music wording exactly.
            subtitle_text = (
                (
                    "The host records the synchronized Jamulus take. Choose whether "
                    "this Mac also keeps its configured interface inputs as Local "
                    "Originals. Studio chooses its own playback output when you review a take."
                )
                if self._local_originals_available
                else (
                    "The host records the synchronized Jamulus take. Studio chooses "
                    "its playback output when you review a take."
                )
            )
            unavailable_text = (
                "Local originals are unavailable for this session. You can "
                "still play normally, and the host's synchronized server track is kept."
            )
            capture_help_text = (
                "Name mono or stereo tracks with Edit Input Tracks. Enabled Local "
                "Originals use device inputs sequentially, up to 32 channels total. "
                "An empty track list keeps the compatible input 1–2 default. The "
                "device must run at 48 kHz and be shareable with Jamulus. WebJam "
                "records only after the host confirms a take."
            )
            edit_tracks_text = "Edit Input Tracks…"
            edit_tracks_accessible_name = (
                "Edit the named local input tracks Record Session captures"
            )
            locked_folder_tooltip = (
                "End or restart the current jam before changing its Takes folder."
            )

        self.setAccessibleName("WebJam Recording Setup")
        self.setAccessibleDescription(subtitle_text)
        title = QLabel("Recording setup")
        title.setObjectName("SimpleSettingsTitle")
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("SimpleSettingsSubtitle")
        subtitle.setWordWrap(True)
        content.addWidget(title)
        content.addWidget(subtitle)

        self._capture = QCheckBox(
            "Keep configured interface inputs as isolated Local Originals"
        )
        self._capture.setAccessibleName("Record configured isolated local inputs")
        self._capture.setChecked(
            self._local_originals_available
            and bool(settings.local_capture_enabled)
        )
        self._capture.setEnabled(self._local_originals_available)
        content.addWidget(self._capture)

        self._capture_unavailable = QLabel(unavailable_text)
        self._capture_unavailable.setObjectName("SimpleSettingsSubtitle")
        self._capture_unavailable.setWordWrap(True)
        self._capture_unavailable.setVisible(
            not self._local_originals_available
        )
        content.addWidget(self._capture_unavailable)

        self._capture_help = QLabel(capture_help_text)
        self._capture_help.setObjectName("SimpleSettingsSubtitle")
        self._capture_help.setWordWrap(True)
        content.addWidget(self._capture_help)

        self._input_label = QLabel("Local Original recording input")
        self._input_label.setObjectName("SimpleSettingsFieldLabel")
        self._input = QComboBox()
        self._input.setAccessibleName("Local Original recording input device")
        self._input_device_channels: dict[int, int] = {}
        for device in list_input_devices():
            channels = int(device.get("channels", 0) or 0)
            if channels < 1:
                continue
            name = str(device.get("name") or "").strip()
            index = int(device.get("index", -1))
            if name and index >= 0:
                self._input_device_channels[index] = channels
                self._input.addItem(f"{name} · {channels} inputs", index)
        saved_input = int(settings.audio_input_device_index)
        input_index = self._input.findData(saved_input)
        if input_index >= 0:
            self._input.setCurrentIndex(input_index)
        else:
            for combo_index in range(self._input.count()):
                device_index = int(self._input.itemData(combo_index))
                if self._input_device_channels.get(device_index, 0) >= 2:
                    self._input.setCurrentIndex(combo_index)
                    break
        content.addWidget(self._input_label)
        content.addWidget(self._input)

        # Working copy of the configured input maps; edited through the
        # dedicated editor and persisted on Save alongside the capture flag.
        self._input_maps = [
            dict(entry)
            for entry in (getattr(settings, "input_maps", None) or [])
            if isinstance(entry, dict)
        ]
        self._edit_tracks_btn = QPushButton(edit_tracks_text)
        self._edit_tracks_btn.setObjectName("GhostButton")
        self._edit_tracks_btn.setAccessibleName(edit_tracks_accessible_name)
        self._edit_tracks_btn.clicked.connect(self._edit_input_tracks)
        self._tracks_summary = QLabel("")
        self._tracks_summary.setObjectName("SimpleSettingsSubtitle")
        self._tracks_summary.setWordWrap(True)
        content.addWidget(self._edit_tracks_btn)
        content.addWidget(self._tracks_summary)
        self._refresh_tracks_summary()

        folder_row = QHBoxLayout()
        self._folder = QLabel(
            "Takes: " + (str(settings.takes_directory or "Not configured"))
        )
        self._folder.setObjectName("SimpleSettingsSubtitle")
        self._folder.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        folder_row.addWidget(self._folder, 1)
        choose_folder = QPushButton("Choose Folder")
        choose_folder.setObjectName("GhostButton")
        choose_folder.setEnabled(self._takes_folder_editable)
        if not self._takes_folder_editable:
            choose_folder.setToolTip(locked_folder_tooltip)
        choose_folder.clicked.connect(self._choose_folder)
        folder_row.addWidget(choose_folder)
        show_folder = QPushButton("Show Folder")
        show_folder.setObjectName("GhostButton")
        self._show_folder_button = show_folder
        show_folder.setEnabled(bool(settings.takes_directory))
        show_folder.clicked.connect(self._show_folder)
        folder_row.addWidget(show_folder)
        content.addLayout(folder_row)

        self._error = QLabel("")
        self._error.setObjectName("SimpleSettingsError")
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._error.setVisible(False)
        content.addWidget(self._error)
        content.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("GhostButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save Recording Setup")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self._capture.toggled.connect(self._sync_capture_fields)
        self._sync_capture_fields()

    def _sync_capture_fields(self) -> None:
        visible = self._capture.isEnabled() and self._capture.isChecked()
        self._input_label.setVisible(visible)
        self._input.setVisible(visible)
        if hasattr(self, "_edit_tracks_btn"):
            self._edit_tracks_btn.setVisible(visible)
            self._tracks_summary.setVisible(visible)
        self._capture_help.setVisible(self._capture.isEnabled())
        self._error.clear()
        self._error.setVisible(False)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)

    def _show_folder(self) -> None:
        path = str(self._settings.takes_directory or "")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _choose_folder(self) -> None:
        start = str(self._settings.takes_directory or "")
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose WebJam Takes Folder",
            start,
        )
        if not path:
            return
        self._settings.takes_directory = str(path)
        self._folder.setText(f"Takes: {path}")
        self._show_folder_button.setEnabled(True)
        self._error.clear()
        self._error.setVisible(False)

    def _refresh_tracks_summary(self) -> None:
        count = len(self._input_maps)
        if count == 0:
            if self._creator_profile.key == "podcast_voice":
                summary = (
                    "No input map is configured. WebJam will keep the legacy "
                    "two-input Local Original fallback (inputs 1–2) until you "
                    "name voice tracks."
                )
            elif self._creator_profile.key == "review_rehearsal":
                summary = (
                    "No input map is configured. WebJam will keep the legacy "
                    "two-input Local Original fallback (inputs 1–2) until you "
                    "name the sources to keep."
                )
            else:
                summary = (
                    "Using the default two isolated stems "
                    "(host-guitar, host-vocal)."
                )
            self._tracks_summary.setText(summary)
        else:
            active_channels = sum(
                int(entry.get("channels", 0) or 0)
                for entry in self._input_maps
                if bool(entry.get("enabled", True))
                and bool(entry.get("local_original_enabled", False))
            )
            names = ", ".join(
                str(entry.get("name", "") or "?") for entry in self._input_maps
            )
            self._tracks_summary.setText(
                f"{count} configured · {active_channels}/32 active input "
                f"channels: {names}"
            )

    def _edit_input_tracks(self) -> None:
        from webjam_qt.windows.input_map_editor import InputMapEditorDialog

        dialog = InputMapEditorDialog(
            self._input_maps,
            parent=self,
            creator_profile=self._creator_profile,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._input_maps = dialog.result_maps()
            self._refresh_tracks_summary()

    def _save(self) -> None:
        capture = self._capture.isEnabled() and self._capture.isChecked()
        input_index = self._input.currentData()
        if capture and input_index is None:
            self._show_error(
                "Connect an input interface, then reopen Recording Setup."
            )
            return
        active_channels = (
            2
            if not self._input_maps
            else sum(
                int(entry.get("channels", 0) or 0)
                for entry in self._input_maps
                if bool(entry.get("enabled", True))
                and bool(entry.get("local_original_enabled", False))
            )
        )
        if capture and active_channels == 0:
            self._show_error(
                "Turn on at least one Local Original track, or turn off local "
                "input capture."
            )
            return
        available_channels = self._input_device_channels.get(
            int(input_index) if input_index is not None else -1,
            0,
        )
        if capture and available_channels < active_channels:
            self._show_error(
                f"This map needs {active_channels} input channels, but the "
                f"selected interface provides {available_channels}."
            )
            return
        if self._local_originals_available:
            self._settings.local_capture_enabled = capture
            self._settings.local_capture_choice_made = True
            self._settings.input_maps = [dict(e) for e in self._input_maps]
        if capture:
            self._settings.audio_input_device_index = int(input_index)
        try:
            save_settings(self._settings)
        except Exception:  # noqa: BLE001 - settings errors can carry local paths
            LOGGER.error("Could not save recording setup")
            self._show_error(
                "WebJam couldn't save recording setup. Check folder access and "
                "try again."
            )
            return
        self.accept()
