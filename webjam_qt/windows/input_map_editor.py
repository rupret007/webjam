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

from core.creative_modes import CreatorProfile, get_creator_profile_by_key
from webjam_qt.theme.tokens import Space

_MAX_ROWS = 32
_MAX_CAPTURE_CHANNELS = 32
_MAX_NAME_CHARS = 128


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


def _profile_copy(profile: CreatorProfile) -> dict[str, str]:
    if profile.key == "podcast_voice":
        return {
            "title": "Voice Input Tracks",
            "subtitle": (
                "Name the local voice inputs Record Session captures as isolated "
                "Local Originals. Voice tracks record on your interface's inputs "
                "in this order; a stereo voice track uses two inputs. Up to 32 "
                "enabled Local Original input channels are supported. Leave this "
                "empty to keep the legacy two-input Local Original fallback "
                "(inputs 1–2)."
            ),
            "placeholder": "Voice track name (e.g. Host Mic)",
            "name_accessible": "Voice input track name",
            "enable_accessible": "Enable this voice input track",
            "local_accessible": (
                "Keep this voice track as an isolated Local Original"
            ),
            "remove_accessible": "Remove this voice input track",
            "scroll_accessible": "Configured voice input tracks",
            "add_text": "Add Voice Track",
            "add_accessible": "Add a voice input track",
            "save_text": "Save Voice Tracks",
            "row_limit": "Up to 32 voice input tracks are supported.",
            "item_title": "Voice track",
            "item_plural": "voice tracks",
            "channel_limit": (
                "Enabled Local Originals use more than 32 input channels. "
                "Disable a voice track or change a stereo voice track to mono."
            ),
        }
    if profile.key == "review_rehearsal":
        return {
            "title": "Input Sources",
            "subtitle": (
                "Name the local audio sources Record Session captures as isolated "
                "Local Originals. Sources record on your interface's inputs in "
                "this order; a stereo source uses two inputs. Up to 32 enabled "
                "Local Original input channels are supported. Leave this empty "
                "to keep the legacy two-input Local Original fallback "
                "(inputs 1–2)."
            ),
            "placeholder": "Source name (e.g. Room Mic)",
            "name_accessible": "Input source name",
            "enable_accessible": "Enable this input source",
            "local_accessible": "Keep this source as an isolated Local Original",
            "remove_accessible": "Remove this input source",
            "scroll_accessible": "Configured input sources",
            "add_text": "Add Source",
            "add_accessible": "Add an input source",
            "save_text": "Save Sources",
            "row_limit": "Up to 32 input sources are supported.",
            "item_title": "Source",
            "item_plural": "sources",
            "channel_limit": (
                "Enabled Local Originals use more than 32 input channels. "
                "Disable a source or change a stereo source to mono."
            ),
        }
    # Preserve the shipped Music wording exactly.
    return {
        "title": "Input Tracks",
        "subtitle": (
            "Name the local inputs Record Session captures as isolated Local "
            "Originals. Tracks record on your interface's inputs in this "
            "order; a stereo track uses two inputs. Up to 32 enabled Local "
            "Original input channels are supported. Leave this empty to keep "
            "the default two isolated stems."
        ),
        "placeholder": "Track name (e.g. Guitar DI)",
        "name_accessible": "Input track name",
        "enable_accessible": "Enable this input track",
        "local_accessible": "Keep this track as an isolated Local Original",
        "remove_accessible": "Remove this input track",
        "scroll_accessible": "Configured input tracks",
        "add_text": "Add Track",
        "add_accessible": "Add an input track",
        "save_text": "Save Input Tracks",
        "row_limit": "Up to 32 input tracks are supported.",
        "item_title": "Track",
        "item_plural": "tracks",
        "channel_limit": (
            "Enabled Local Originals use more than 32 input channels. Disable "
            "a track or change a stereo track to mono."
        ),
    }


class _InputMapRow(QWidget):
    """One editable input-track row: name, channels, enable, Local Original."""

    def __init__(
        self,
        entry: dict | None = None,
        parent: QWidget | None = None,
        *,
        creator_profile: CreatorProfile | str | None = None,
    ) -> None:
        super().__init__(parent)
        profile = _resolve_creator_profile(creator_profile)
        profile_copy = _profile_copy(profile)
        entry = entry or {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM)

        self._name = QLineEdit(str(entry.get("name", "") or ""))
        self._name.setMaxLength(_MAX_NAME_CHARS)
        self._name.setPlaceholderText(profile_copy["placeholder"])
        self._name.setAccessibleName(profile_copy["name_accessible"])

        self._channels = QComboBox()
        self._channels.addItem("Mono", 1)
        self._channels.addItem("Stereo", 2)
        self._channels.setCurrentIndex(
            1 if int(entry.get("channels", 1) or 1) == 2 else 0
        )
        self._channels.setAccessibleName("Mono or stereo")

        self._enabled = QCheckBox("On")
        self._enabled.setChecked(bool(entry.get("enabled", True)))
        self._enabled.setAccessibleName(profile_copy["enable_accessible"])

        self._local_original = QCheckBox("Local Original")
        self._local_original.setChecked(
            bool(entry.get("local_original_enabled", True))
        )
        self._local_original.setAccessibleName(profile_copy["local_accessible"])

        self._remove = QPushButton("Remove")
        self._remove.setObjectName("GhostButton")
        self._remove.setAccessibleName(profile_copy["remove_accessible"])

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
        input_maps: list | None = None,
        parent: QWidget | None = None,
        *,
        creator_profile: CreatorProfile | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._creator_profile = _resolve_creator_profile(creator_profile)
        self._profile_copy = _profile_copy(self._creator_profile)
        self.setWindowTitle(self._profile_copy["title"])
        self.setAccessibleName(self._profile_copy["title"])
        self.setAccessibleDescription(self._profile_copy["subtitle"])
        self.setModal(True)
        self._rows: list[_InputMapRow] = []
        self._result_maps: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        root.setSpacing(Space.SM)

        title = QLabel(self._profile_copy["title"])
        title.setObjectName("SimpleSettingsTitle")
        subtitle = QLabel(self._profile_copy["subtitle"])
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
        self._rows_scroll.setAccessibleName(
            self._profile_copy["scroll_accessible"]
        )
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

        self._add_btn = QPushButton(self._profile_copy["add_text"])
        self._add_btn.setObjectName("GhostButton")
        self._add_btn.setAccessibleName(self._profile_copy["add_accessible"])
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
        save = QPushButton(self._profile_copy["save_text"])
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)
        self.resize(760, 560)

    def _add_row(self, entry: dict | None = None) -> None:
        if len(self._rows) >= _MAX_ROWS:
            self._show_error(self._profile_copy["row_limit"])
            return
        row = _InputMapRow(entry, creator_profile=self._creator_profile)
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
            item_title = self._profile_copy["item_title"]
            if not name:
                return False, f"{item_title} {index + 1} needs a name.", []
            if len(name) > _MAX_NAME_CHARS:
                return False, f"{item_title} {index + 1}'s name is too long.", []
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
                return (
                    False,
                    f"{item_title} {index + 1}'s name has invalid characters.",
                    [],
                )
            if name.casefold() in seen:
                item_plural = self._profile_copy["item_plural"]
                return False, f"Two {item_plural} are both named '{name}'.", []
            seen.add(name.casefold())
            maps.append(entry)
            if entry["enabled"] and entry["local_original_enabled"]:
                selected_channels += entry["channels"]
                if selected_channels > _MAX_CAPTURE_CHANNELS:
                    return (
                        False,
                        self._profile_copy["channel_limit"],
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
