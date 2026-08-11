"""Editor for configurable Record Session input maps.

A self-contained dialog that turns ``AppSettings.input_maps`` into an
editable list of named local input tracks — the musician-facing front end
for the capture-truth resolver in ``core.session_recording_plan``. Each row
carries a name, a mono/stereo choice, an enable switch, and a Local Original
opt-in. The dialog validates through the same rules the settings loader
uses, so a saved map is always one the recorder can act on.

Deliberately scoped for this phase: rows allocate device channels
sequentially (the resolver's documented default), matching what the live
capture layer records today. Explicit per-track device-channel selection is
a later addition; the model here is forward-compatible with it.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space

_MAX_ROWS = 32
_MAX_CAPTURE_CHANNELS = 32
_MAX_NAME_CHARS = 128


class _InputMapRow(QWidget):
    """One editable input-track row: name, channels, enable, Local Original."""

    def __init__(
        self,
        entry: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        entry = entry or {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM)

        self._name = QLineEdit(str(entry.get("name", "") or ""))
        self._name.setMaxLength(_MAX_NAME_CHARS)
        self._name.setPlaceholderText("Track name (e.g. Guitar DI)")
        self._name.setAccessibleName("Input track name")

        self._channels = QComboBox()
        self._channels.addItem("Mono", 1)
        self._channels.addItem("Stereo", 2)
        self._channels.setCurrentIndex(
            1 if int(entry.get("channels", 1) or 1) == 2 else 0
        )
        self._channels.setAccessibleName("Mono or stereo")

        self._enabled = QCheckBox("On")
        self._enabled.setChecked(bool(entry.get("enabled", True)))
        self._enabled.setAccessibleName("Enable this input track")

        self._local_original = QCheckBox("Local Original")
        self._local_original.setChecked(
            bool(entry.get("local_original_enabled", True))
        )
        self._local_original.setAccessibleName(
            "Keep this track as an isolated Local Original"
        )

        self._remove = QPushButton("Remove")
        self._remove.setObjectName("GhostButton")
        self._remove.setAccessibleName("Remove this input track")

        layout.addWidget(self._name, stretch=1)
        layout.addWidget(self._channels)
        layout.addWidget(self._enabled)
        layout.addWidget(self._local_original)
        layout.addWidget(self._remove)

    def to_entry(self) -> dict:
        return {
            "name": self._name.text().strip(),
            "channels": int(self._channels.currentData()),
            "enabled": self._enabled.isChecked(),
            "local_original_enabled": self._local_original.isChecked(),
        }


class InputMapEditorDialog(QDialog):
    """Add, edit, and remove the local input tracks Record Session captures."""

    def __init__(
        self,
        input_maps: Optional[list] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Input Tracks")
        self.setModal(True)
        self._rows: list[_InputMapRow] = []
        self._result_maps: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        root.setSpacing(Space.SM)

        title = QLabel("Input Tracks")
        title.setObjectName("SimpleSettingsTitle")
        subtitle = QLabel(
            "Name the local inputs Record Session captures as isolated Local "
            "Originals. Tracks record on your interface's inputs in this "
            "order; a stereo track uses two inputs. Up to 32 enabled Local "
            "Original input channels are supported. Leave this empty to keep "
            "the default two isolated stems."
        )
        subtitle.setObjectName("SimpleSettingsSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self._rows_widget = QWidget()
        self._rows_container = QVBoxLayout(self._rows_widget)
        self._rows_container.setContentsMargins(0, 0, 0, 0)
        self._rows_container.setSpacing(Space.XS)
        self._rows_scroll = QScrollArea()
        self._rows_scroll.setObjectName("InputTrackScroll")
        self._rows_scroll.setAccessibleName("Configured input tracks")
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._rows_scroll.setMinimumHeight(160)
        self._rows_scroll.setWidget(self._rows_widget)
        root.addWidget(self._rows_scroll, stretch=1)

        for entry in list(input_maps or [])[:_MAX_ROWS]:
            if isinstance(entry, dict):
                self._add_row(entry)

        self._add_btn = QPushButton("Add Track")
        self._add_btn.setObjectName("GhostButton")
        self._add_btn.setAccessibleName("Add an input track")
        self._add_btn.clicked.connect(lambda: self._add_row())
        root.addWidget(self._add_btn)
        self._sync_add_enabled()

        self._error = QLabel("")
        self._error.setObjectName("SimpleSettingsError")
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._error.setVisible(False)
        root.addWidget(self._error)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("GhostButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save Input Tracks")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)
        self.resize(760, 560)

    def _add_row(self, entry: Optional[dict] = None) -> None:
        if len(self._rows) >= _MAX_ROWS:
            self._show_error("Up to 32 input tracks are supported.")
            return
        row = _InputMapRow(entry)
        row._remove.clicked.connect(lambda: self._remove_row(row))
        self._rows.append(row)
        self._rows_container.addWidget(row)
        self._sync_add_enabled()

    def _remove_row(self, row: _InputMapRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
            self._rows_container.removeWidget(row)
            row.deleteLater()
        self._sync_add_enabled()

    def _sync_add_enabled(self) -> None:
        # Called after the Add button exists; the initial population loop
        # runs before it, so this is a no-op guard during construction.
        button = getattr(self, "_add_btn", None)
        if button is not None:
            button.setEnabled(len(self._rows) < _MAX_ROWS)

    def collect(self) -> tuple[bool, str, list]:
        """Validate the rows. Returns (ok, error, maps).

        The same rules the settings loader enforces: every row needs a
        non-empty, control-free, unique, bounded name and a 1/2 channel
        count. An empty editor is valid and clears the configuration.
        """

        maps: list[dict] = []
        seen: set[str] = set()
        selected_channels = 0
        for index, row in enumerate(self._rows):
            entry = row.to_entry()
            name = entry["name"]
            if not name:
                return False, f"Track {index + 1} needs a name.", []
            if len(name) > _MAX_NAME_CHARS:
                return False, f"Track {index + 1}'s name is too long.", []
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
                return (
                    False,
                    f"Track {index + 1}'s name has invalid characters.",
                    [],
                )
            if name.casefold() in seen:
                return False, f"Two tracks are both named '{name}'.", []
            seen.add(name.casefold())
            maps.append(entry)
            if entry["enabled"] and entry["local_original_enabled"]:
                selected_channels += entry["channels"]
                if selected_channels > _MAX_CAPTURE_CHANNELS:
                    return (
                        False,
                        "Enabled Local Originals use more than 32 input "
                        "channels. Disable a track or change a stereo track "
                        "to mono.",
                        [],
                    )
        return True, "", maps

    def _save(self) -> None:
        ok, error, maps = self.collect()
        if not ok:
            self._show_error(error)
            return
        self._result_maps = maps
        self.accept()

    def result_maps(self) -> list:
        """The validated maps chosen on Save (empty until accepted)."""

        return list(self._result_maps)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)
