"""Settings rows for the optional keys a musician brings, and only those.

Shared on purpose. Any creator profile that later wants a musician's own key
embeds this panel with the provider ids it cares about and inherits the same
schema, the same storage rule, and the same copy — rather than growing a second
place where a credential can be typed.

Three properties this widget exists to hold:

* **A key never reaches the settings file.** Save writes straight to the OS
  credential store through :class:`~core.provider_credentials.ProviderCredentials`.
  It is not part of the dialog's Save, so the JSON WebJam writes cannot contain
  one even if this panel is left half-filled.
* **A key is never shown back.** Fields are password fields, they are cleared
  after a successful save, and nothing here reads a stored value to display it.
* **Nothing is required.** The heading says so, because a musician opening
  Settings mid-jam should not conclude the jam needs a key.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.provider_credentials import (
    SOURCE_ENVIRONMENT,
    SOURCE_LEGACY_SETTINGS,
    SOURCE_STORE,
    ProviderCredentials,
    ProviderSpec,
    no_store_reason,
    provider_spec,
    storage_note,
)
from webjam_qt.theme.tokens import Space

HEADING = "Optional keys"
INTRO = (
    "WebJam plays without any of these. Jamulus, the Shared Track, the "
    "conductor, and the mix all work with no key at all."
)


class ProviderKeyPanel(QFrame):
    """One row per provider: paste, save to the OS store, or remove."""

    keys_changed = Signal()

    def __init__(
        self,
        provider_ids,
        *,
        credentials: Optional[ProviderCredentials] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ProviderKeyPanel")
        self._credentials = credentials or ProviderCredentials()
        self._specs: list[ProviderSpec] = [
            spec
            for spec in (provider_spec(key) for key in provider_ids)
            if spec is not None
        ]
        self._fields: dict[str, QLineEdit] = {}
        self._status: dict[str, QLabel] = {}
        self._save_buttons: dict[str, QPushButton] = {}
        self._remove_buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)

        intro = _hint(INTRO)
        intro.setObjectName("ProviderKeyIntro")
        layout.addWidget(intro)

        self._storage_line = _hint(storage_note())
        layout.addWidget(self._storage_line)

        for spec in self._specs:
            layout.addWidget(self._build_row(spec))

        self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_row(self, spec: ProviderSpec) -> QWidget:
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, Space.XS, 0, 0)
        column.setSpacing(2)

        title = QLabel(f"{spec.label} — {spec.purpose}")
        title.setObjectName("SimpleSettingsFieldLabel")
        title.setWordWrap(True)
        column.addWidget(title)

        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText(spec.placeholder or "Paste your key")
        field.setAccessibleName(f"{spec.label} API key")
        field.setAccessibleDescription(
            f"Saved in this computer's credential store. Create a key at "
            f"{spec.console_url}."
        )
        self._fields[spec.id] = field

        save = QPushButton("Save key")
        save.setObjectName("QuietButton")
        save.setAccessibleName(f"Save the {spec.label} key")
        save.clicked.connect(lambda _checked=False, key=spec.id: self._save(key))
        self._save_buttons[spec.id] = save

        remove = QPushButton("Remove")
        remove.setObjectName("GhostButton")
        remove.setAccessibleName(f"Remove the saved {spec.label} key")
        remove.clicked.connect(lambda _checked=False, key=spec.id: self._remove(key))
        self._remove_buttons[spec.id] = remove

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(Space.SM)
        controls.addWidget(field, 1)
        controls.addWidget(save)
        controls.addWidget(remove)
        column.addLayout(controls)

        status = _hint("")
        status.setAccessibleName(f"{spec.label} key status")
        self._status[spec.id] = status
        column.addWidget(status)
        return row

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Re-read where each key currently comes from. Values stay hidden."""

        store_usable = self._credentials.store.usable()
        if not store_usable:
            self._storage_line.setText(
                f"{no_store_reason(self._credentials.store)} Set the "
                "environment variable for a provider instead — WebJam will "
                "not write a key to disk in plain text."
            )
        else:
            self._storage_line.setText(storage_note())

        for spec in self._specs:
            resolved = self._credentials.resolve(spec.id)
            field = self._fields[spec.id]
            from_environment = resolved.source == SOURCE_ENVIRONMENT
            field.setEnabled(store_usable and not from_environment)
            self._save_buttons[spec.id].setEnabled(
                store_usable and not from_environment
            )
            self._remove_buttons[spec.id].setEnabled(
                store_usable and resolved.source == SOURCE_STORE
            )
            self._status[spec.id].setText(self._status_text(spec, resolved))

    def _status_text(self, spec: ProviderSpec, resolved) -> str:
        if resolved.source == SOURCE_ENVIRONMENT:
            variable = next(
                (name for name in spec.env_vars if name), spec.env_vars[0]
            )
            return (
                f"Set in your environment ({variable}). WebJam uses that and "
                "will not overwrite it."
            )
        if resolved.source == SOURCE_STORE:
            return "Saved in this computer's credential store."
        if resolved.source == SOURCE_LEGACY_SETTINGS:
            return (
                "Found in the older settings file. Save it here to move it "
                "into the credential store."
            )
        return f"Not set. Create one at {spec.console_url}."

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _save(self, provider_id: str) -> None:
        field = self._fields.get(provider_id)
        if field is None:
            return
        result = self._credentials.save(provider_id, field.text())
        if not result.saved:
            self._status[provider_id].setText(result.reason)
            return
        # Never keep the typed value on screen once it has somewhere to live.
        field.clear()
        self.refresh()
        self.keys_changed.emit()

    def _remove(self, provider_id: str) -> None:
        removed = self._credentials.clear(provider_id)
        self.refresh()
        if not removed:
            self._status[provider_id].setText(
                "There was no saved key to remove."
            )
            return
        self.keys_changed.emit()

    # ------------------------------------------------------------------
    # For tests and callers
    # ------------------------------------------------------------------
    def status_text(self, provider_id: str) -> str:
        label = self._status.get(provider_id)
        return label.text() if label is not None else ""

    def field(self, provider_id: str) -> Optional[QLineEdit]:
        return self._fields.get(provider_id)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SimpleSettingsHint")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


__all__ = ["HEADING", "INTRO", "ProviderKeyPanel"]
