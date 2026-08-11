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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.audio_routing import list_input_devices
from core.settings import AppSettings, save_settings
from webjam_qt.theme.tokens import Space


LOGGER = logging.getLogger("webjam.qt.recording_setup")


class LocalOriginalsChoiceDialog(QDialog):
    """Ask the one first-recording question without touching live audio.

    The shared Jamulus take is always available on the host.  Keeping this
    Mac's first two interface inputs is a separate, explicit recording choice
    and therefore belongs here—not in Host, Join, or Jamulus setup.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.choice = ""
        self.setObjectName("LocalOriginalsChoiceDialog")
        self.setWindowTitle("Keep a local original?")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.resize(600, 310)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        title = QLabel("Keep a local original?")
        title.setObjectName("SimpleSettingsTitle")
        root.addWidget(title)

        detail = QLabel(
            "WebJam will record the shared Jamulus take either way. You can "
            "also keep this Mac’s first two interface inputs as separate "
            "Local Originals for Studio later. This does not change Jamulus "
            "audio settings."
        )
        detail.setObjectName("SimpleSettingsSubtitle")
        detail.setWordWrap(True)
        root.addWidget(detail)
        root.addStretch(1)

        shared = QPushButton("Record Shared Jam Only")
        shared.setObjectName("PrimaryButton")
        shared.setAccessibleName("Record shared Jamulus take only")
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
    ) -> None:
        super().__init__(parent)
        # Edit a draft. A failed atomic save must never leave the running
        # controller believing that unsaved preferences are active.
        self._settings = deepcopy(settings)
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

        title = QLabel("Recording setup")
        title.setObjectName("SimpleSettingsTitle")
        subtitle = QLabel(
            (
                "The host records the synchronized Jamulus take. Choose whether "
                "this Mac also keeps its first two interface inputs as Local "
                "Originals. Studio chooses its own playback output when you review a take."
            )
            if self._local_originals_available
            else (
                "The host records the synchronized Jamulus take. Studio chooses "
                "its playback output when you review a take."
            )
        )
        subtitle.setObjectName("SimpleSettingsSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self._capture = QCheckBox(
            "Keep interface inputs 1 and 2 as isolated local originals"
        )
        self._capture.setAccessibleName("Record two isolated local input stems")
        self._capture.setChecked(
            self._local_originals_available
            and bool(settings.local_capture_enabled)
        )
        self._capture.setEnabled(self._local_originals_available)
        root.addWidget(self._capture)

        self._capture_unavailable = QLabel(
            "Local originals are unavailable for this session. You can "
            "still play normally, and the host's synchronized server track is kept."
        )
        self._capture_unavailable.setObjectName("SimpleSettingsSubtitle")
        self._capture_unavailable.setWordWrap(True)
        self._capture_unavailable.setVisible(
            not self._local_originals_available
        )
        root.addWidget(self._capture_unavailable)

        self._capture_help = QLabel(
            "Use this when one interface carries two distinct sources, such as "
            "guitar on input 1 and vocal on input 2. The device must support two "
            "input channels at 48 kHz and be shareable with Jamulus. WebJam records "
            "only after the host confirms a take, keeps the originals on this Mac, "
            "and transfers verified copies to the host when available."
        )
        self._capture_help.setObjectName("SimpleSettingsSubtitle")
        self._capture_help.setWordWrap(True)
        root.addWidget(self._capture_help)

        self._input_label = QLabel("Two-channel recording input")
        self._input_label.setObjectName("SimpleSettingsFieldLabel")
        self._input = QComboBox()
        self._input.setAccessibleName("Two-channel isolated recording input")
        for device in list_input_devices():
            if int(device.get("channels", 0) or 0) < 2:
                continue
            name = str(device.get("name") or "").strip()
            index = int(device.get("index", -1))
            if name and index >= 0:
                self._input.addItem(f"{name} · {device['channels']} inputs", index)
        saved_input = int(settings.audio_input_device_index)
        input_index = self._input.findData(saved_input)
        if input_index >= 0:
            self._input.setCurrentIndex(input_index)
        root.addWidget(self._input_label)
        root.addWidget(self._input)

        # Working copy of the configured input maps; edited through the
        # dedicated editor and persisted on Save alongside the capture flag.
        self._input_maps = [
            dict(entry)
            for entry in (getattr(settings, "input_maps", None) or [])
            if isinstance(entry, dict)
        ]
        self._edit_tracks_btn = QPushButton("Edit Input Tracks…")
        self._edit_tracks_btn.setObjectName("GhostButton")
        self._edit_tracks_btn.setAccessibleName(
            "Edit the named local input tracks Record Session captures"
        )
        self._edit_tracks_btn.clicked.connect(self._edit_input_tracks)
        self._tracks_summary = QLabel("")
        self._tracks_summary.setObjectName("SimpleSettingsSubtitle")
        self._tracks_summary.setWordWrap(True)
        root.addWidget(self._edit_tracks_btn)
        root.addWidget(self._tracks_summary)
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
            choose_folder.setToolTip(
                "End or restart the current jam before changing its Takes folder."
            )
        choose_folder.clicked.connect(self._choose_folder)
        folder_row.addWidget(choose_folder)
        show_folder = QPushButton("Show Folder")
        show_folder.setObjectName("GhostButton")
        self._show_folder_button = show_folder
        show_folder.setEnabled(bool(settings.takes_directory))
        show_folder.clicked.connect(self._show_folder)
        folder_row.addWidget(show_folder)
        root.addLayout(folder_row)

        self._error = QLabel("")
        self._error.setObjectName("SimpleSettingsError")
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._error.setVisible(False)
        root.addWidget(self._error)
        root.addStretch(1)

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
            self._tracks_summary.setText(
                "Using the default two isolated stems (host-guitar, host-vocal)."
            )
        else:
            names = ", ".join(
                str(entry.get("name", "") or "?") for entry in self._input_maps
            )
            self._tracks_summary.setText(f"{count} configured: {names}")

    def _edit_input_tracks(self) -> None:
        from webjam_qt.windows.input_map_editor import InputMapEditorDialog

        dialog = InputMapEditorDialog(self._input_maps, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._input_maps = dialog.result_maps()
            self._refresh_tracks_summary()

    def _save(self) -> None:
        capture = self._capture.isEnabled() and self._capture.isChecked()
        input_index = self._input.currentData()
        if capture and input_index is None:
            self._show_error(
                "Connect a two-channel input interface, then reopen Recording Setup."
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
