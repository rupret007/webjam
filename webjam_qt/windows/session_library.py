"""A searchable local library and editor, available before joining a room."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QBoxLayout, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from core.art_workspace import art_summary
from core.session_intelligence import build_session_pulse
from core.session_library import (
    SessionLibrary, SessionLibraryError, SessionLibraryImportUnconfirmed,
    validate_session_record,
)
from webjam_qt.widgets.art_workspace import ArtWorkspacePanel
from webjam_qt.widgets.rehearsal_plan import RehearsalPlanPanel


def workspace_summary(record) -> str:
    if record.profile == "art":
        text = art_summary(record.title, record.art)
    else:
        from core.rehearsal_plan import summarize_plan
        pulse = build_session_pulse(creator_profile_key=record.profile,
            title=record.title, notes=record.notes)
        text = pulse.to_markdown() + "\n\n" + summarize_plan(record.rehearsal)
    if record.notes.strip():
        text += "\n\n## Notes\n" + record.notes
    if record.recaps:
        text += "\n\n## Session history\n"
        for recap in record.recaps:
            text += f"\n{recap.get('ended_at', '')}\n{recap.get('summary', '')}\n"
    return text.rstrip() + "\n"


class _TitleByteLimit(QValidator):
    """Bound new typing to the core's 512 UTF-8 byte title limit.

    ``setText`` (used when loading a saved record) does not consult a
    validator, so an imported title already within the core limit is never
    silently truncated here even if it exceeds 512 plain characters.
    """

    def __init__(self, limit: int, parent=None) -> None:
        super().__init__(parent)
        self._limit = limit

    def validate(self, text, pos):
        if len(text.encode("utf-8", "surrogatepass")) > self._limit:
            return QValidator.State.Invalid, text, pos
        return QValidator.State.Acceptable, text, pos


class WorkspaceBackupPreviewDialog(QDialog):
    """Preview validated backup bytes; never touches the library until accepted."""

    def __init__(self, library: SessionLibrary, preview, parent=None):
        super().__init__(parent)
        from core.workspace_backup import WorkspaceBackupError

        self.setWindowTitle("Import backup")
        self.setMinimumSize(420, 360)
        record = preview.record
        layout = QVBoxLayout(self)
        info = QLabel(
            f"Title: {record.title}\n"
            f"Profile: {record.profile.replace('_', ' ')}\n"
            f"Saved: {record.updated_at}\n"
            + self._counts(record)
        )
        info.setTextFormat(Qt.TextFormat.PlainText)
        info.setWordWrap(True)
        info.setAccessibleName("Backup preview details")
        layout.addWidget(info)
        try:
            duplicates = preview.matching_import_ids(library)
        except (WorkspaceBackupError, SessionLibraryError, OSError) as error:
            duplicate_text = f"Could not check for prior imports here: {error}"
        else:
            duplicate_text = (f"Already imported here as {len(duplicates)} separate workspace(s)."
                               if duplicates else "No prior import of this exact backup found here.")
        if record.source_key:
            duplicate_text += f"\nOriginal import source: {record.source_key}"
        duplicate_label = QLabel(duplicate_text)
        duplicate_label.setTextFormat(Qt.TextFormat.PlainText)
        duplicate_label.setWordWrap(True)
        duplicate_label.setAccessibleName("Backup source and duplicate information")
        layout.addWidget(duplicate_label)
        limits = QLabel("\n".join(preview.limitations))
        limits.setTextFormat(Qt.TextFormat.PlainText)
        limits.setWordWrap(True)
        limits.setAccessibleName("Backup limitations")
        layout.addWidget(limits)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        self.confirm_button = QPushButton("Import as new workspace")
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    @staticmethod
    def _counts(record) -> str:
        lines = [
            f"Notes: {'present' if record.notes.strip() else 'none'}",
            f"Take links: {len(record.take_links)}",
            f"Session history entries: {len(record.recaps)}",
        ]
        if record.profile == "music":
            lines.append(f"Rehearsal songs: {len(record.rehearsal.get('songs', []) or [])}")
        if record.profile == "art":
            lines.append(f"Art references: {len(record.art.get('references', []) or [])}")
        return "\n".join(lines) + "\n"


class SessionLibraryDialog(QDialog):
    record_saved = Signal(object)
    copy_saved = Signal(object, object)
    continue_requested = Signal(object)
    take_open_requested = Signal(dict)
    bookmark_requested = Signal(str)
    bookmark_open_requested = Signal(dict)
    song_selected = Signal(dict)

    def __init__(self, library: SessionLibrary, parent=None, *, profile="music", current_id="", pending_records=None,
                 save_record=None):
        super().__init__(parent)
        self.library = library
        # The coordinator remains the owner of its recovery map. Only the
        # success signals may settle that map; this dialog edits a snapshot.
        self.pending_records = deepcopy(dict(pending_records or {}))
        self._save_record = save_record
        self._base_record = None
        self._initial_current_id = current_id
        self.default_profile = profile
        self.record = None
        self.selected_record = None
        self._loading = False
        self._dirty = False
        self._import_in_progress = False
        self._retry_import = None
        self._action_rows = []
        self.setWindowTitle("Session library")
        self.resize(760, 680)
        self.setMinimumSize(480, 400)
        outer = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sessions, workspaces, and notes")
        self.search.setAccessibleName("Search session library")
        outer.addWidget(self.search)
        self.history = QListWidget()
        self.history.setAccessibleName("Saved sessions and Art workspaces")
        self.history.setMaximumHeight(100)
        outer.addWidget(self.history)
        create_row = QHBoxLayout()
        self.profile = QComboBox()
        for label, key in (("Music", "music"), ("Art", "art"),
                           ("Podcast & Voice", "podcast_voice"), ("Review & Rehearsal", "review_rehearsal")):
            self.profile.addItem(label, key)
        self.profile.setAccessibleName("New workspace profile")
        self.profile.setCurrentIndex(max(0, self.profile.findData(profile)))
        create_row.addWidget(self.profile)
        create_buttons = QHBoxLayout()
        self.new_button = QPushButton("New workspace…")
        self.new_button.clicked.connect(self._new)
        create_buttons.addWidget(self.new_button)
        self.copy_button = QPushButton("Save as copy…")
        self.copy_button.clicked.connect(self._copy)
        create_buttons.addWidget(self.copy_button)
        create_row.addLayout(create_buttons)
        self._action_rows.extend((create_buttons, create_row))
        outer.addLayout(create_row)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        details = QWidget()
        form = QFormLayout(details)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title = QLineEdit()
        self.title.setValidator(_TitleByteLimit(512, self.title))
        self.title.setAccessibleName("Workspace title")
        self.notes = QPlainTextEdit()
        self.notes.setTabChangesFocus(True)
        self.notes.setAccessibleName("Workspace notes")
        self.notes.setPlaceholderText("Keep notes here. Use Decision: and Action: for the recap.")
        self.notes.setMinimumHeight(150)
        form.addRow("Title", self.title)
        form.addRow("Notes", self.notes)
        self._add_tab(details, "Notes")
        self.rehearsal = RehearsalPlanPanel()
        self.tabs.addTab(self.rehearsal, "Rehearsal plan")
        self.art = ArtWorkspacePanel()
        self._add_tab(self.art, "Art project")
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setAccessibleName("Workspace summary and session history")
        self.tabs.addTab(self.summary, "Summary")
        take_page = QWidget()
        take_layout = QVBoxLayout(take_page)
        self.takes = QListWidget()
        self.takes.setAccessibleName("Takes linked to this workspace")
        take_layout.addWidget(self.takes)
        self.open_take_button = QPushButton("Open selected take in Studio")
        self.open_take_button.clicked.connect(self._open_take)
        take_layout.addWidget(self.open_take_button)
        self.relink_take_button = QPushButton("Locate moved take…")
        self.relink_take_button.clicked.connect(self._relink_take)
        take_layout.addWidget(self.relink_take_button)
        self.takes.currentRowChanged.connect(self._update_take_actions)
        self._update_take_actions()
        self.tabs.addTab(take_page, "Takes")
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Library save status")
        outer.addWidget(self.status)
        row = QHBoxLayout()
        save_actions = QHBoxLayout()
        continue_actions = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_current)
        save_actions.addWidget(self.save_button)
        self.export_button = QPushButton("Export summary…")
        self.export_button.clicked.connect(self._export)
        save_actions.addWidget(self.export_button)
        self.backup_button = QPushButton("Back up workspace…")
        self.backup_button.clicked.connect(self._backup)
        save_actions.addWidget(self.backup_button)
        self.import_backup_button = QPushButton("Import backup…")
        self.import_backup_button.clicked.connect(self._import_backup)
        create_buttons.addWidget(self.import_backup_button)
        self.continue_button = QPushButton("Continue this work")
        self.continue_button.clicked.connect(self._continue)
        continue_actions.addWidget(self.continue_button)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        continue_actions.addWidget(close)
        row.addLayout(save_actions)
        row.addLayout(continue_actions)
        self._action_rows.extend((save_actions, continue_actions, row))
        outer.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(650)
        self.timer.timeout.connect(self.save_current)
        self.title.textChanged.connect(self._changed)
        self.notes.textChanged.connect(self._changed)
        self.rehearsal.changed.connect(self._changed)
        self.art.changed.connect(self._changed)
        self.rehearsal.song_selected.connect(self.song_selected)
        self.rehearsal.bookmark_requested.connect(self.bookmark_requested)
        self.rehearsal.bookmark_open_requested.connect(self._open_bookmark)
        self.search.textChanged.connect(self.refresh)
        self.history.currentRowChanged.connect(self._select)
        self.refresh()
        self._initial_current_id = ""
        if current_id:
            self.select_id(current_id)
        self._sync_import_recovery()

    def _sync_action_rows(self):
        margins = self.layout().contentsMargins()
        width = self.width() - margins.left() - margins.right()
        for row in self._action_rows:
            needed = sum(row.itemAt(index).minimumSize().width()
                         for index in range(row.count())) + row.spacing() * (row.count() - 1)
            direction = (QBoxLayout.Direction.TopToBottom if needed > width
                         else QBoxLayout.Direction.LeftToRight)
            if row.direction() != direction:
                row.setDirection(direction)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_action_rows()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and getattr(self, "_action_rows", None):
            self._sync_action_rows()
        return result

    def _add_tab(self, widget, label):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, label)

    def refresh(self, _query=None, *, select_first=True):
        read_error = ""
        try:
            records = self.library.list(query=self.search.text())
        except (OSError, ValueError) as error:
            read_error = f"Library could not be read: {error}"
            if not self.pending_records:
                self.status.setText(read_error)
                return
            records = []
        # Failed saves in another profile must remain discoverable even if
        # their original was removed or the on-disk library is unavailable.
        indexed = {record.id: record for record in records}
        query = self.search.text().casefold().strip()
        for pending in self.pending_records.values():
            content = json.dumps({name: getattr(pending, name) for name in (
                "title", "notes", "profile", "decisions", "actions", "blockers",
                "recaps", "take_links", "art", "rehearsal",
            )}, ensure_ascii=False).casefold()
            if not query or query in content:
                indexed[pending.id] = pending
            else:
                indexed.pop(pending.id, None)
        records = sorted(indexed.values(), key=lambda record: (record.updated_at, record.id), reverse=True)
        current_id = self.record.id if self.record else ""
        self.history.blockSignals(True)
        self.history.clear()
        for record in records:
            suffix = " · unsaved changes" if record.id in self.pending_records else ""
            item = QListWidgetItem(f"{record.title} · {record.profile.replace('_', ' ')} · {record.updated_at[:10]}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, record.id)
            self.history.addItem(item)
            if record.id == current_id:
                self.history.setCurrentItem(item)
        self.history.blockSignals(False)
        if select_first and self.record is None and records:
            initial_row = next((row for row, record in enumerate(records)
                                if record.id == self._initial_current_id), 0)
            self.history.setCurrentRow(initial_row)
        self.tabs.setEnabled(self.record is not None)
        self.continue_button.setEnabled(self.record is not None)
        if read_error:
            self.status.setText(read_error + " Retained drafts are still available; retry saving or save a separate copy.")
        elif self.library.warnings:
            self.status.setText("Some records need recovery. " + " ".join(self.library.warnings))
        elif not records and self.record is None:
            self.status.setText("Create a workspace to keep your next session's work.")

    def select_id(self, key):
        for row in range(self.history.count()):
            if self.history.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.history.setCurrentRow(row)
                return

    def _select(self, row):
        item = self.history.item(row)
        if item is None or self._loading:
            return
        # Saving refreshes the history and destroys its old QListWidgetItems.
        # Keep the identifier before asking the previous workspace to save.
        requested_id = item.data(Qt.ItemDataRole.UserRole)
        if not self.save_current():
            self.history.blockSignals(True)
            self.select_id(self.record.id)
            self.history.blockSignals(False)
            return
        try:
            pending = self.pending_records.get(requested_id)
            record = pending if pending is not None else self.library.load(requested_id)
            # Validate the whole workspace before replacing any current draft.
            validate_session_record(record)
            from core.art_workspace import normalize_art_workspace
            from core.rehearsal_plan import RehearsalPlan
            rehearsal = RehearsalPlan.from_payload(record.rehearsal).payload()
            art = normalize_art_workspace(record.art)
            summary = workspace_summary(record)
            self._loading = True
            self.rehearsal.load_payload(rehearsal)
            self.art.load_payload(art, defer_reference_checks=bool(record.import_provenance))
            self.title.setText(record.title)
            self.notes.setPlainText(record.notes)
            self.summary.setPlainText(summary)
            self.record = record
            self._base_record = deepcopy(record)
            self.history.blockSignals(True)
            self.select_id(record.id)
            self.history.blockSignals(False)
            self.tabs.setTabVisible(1, record.profile == "music")
            self.tabs.setTabVisible(2, record.profile == "art")
            self.tabs.setTabVisible(4, record.profile != "art")
            self.tabs.setEnabled(True)
            self.continue_button.setEnabled(True)
            self._render_takes()
            self._dirty = pending is not None
            self.status.setText(
                "Retained changes are not saved. Use Save to retry or Save as copy."
                if pending is not None else (
                    "Recovered copy loaded. Save as copy to keep it." if record.recovered else "Saved on this computer."
                )
            )
        except (OSError, ValueError) as error:
            self.status.setText(f"Workspace could not be opened: {error}")
            if self.record is not None:
                self.history.blockSignals(True)
                self.select_id(self.record.id)
                self.history.blockSignals(False)
        finally:
            self._loading = False

    def _render_takes(self):
        previous = self.takes.currentItem()
        selected = (previous.data(Qt.ItemDataRole.UserRole)
                    if previous is not None and getattr(self, "_takes_record_id", None) == self.record.id else None)
        self._takes_record_id = self.record.id
        self.takes.clear()
        # Imported history never owns a pending recording, but this workspace
        # can acquire a new recording reservation after import.
        imported = bool(self.record.import_provenance)
        for ref in self.record.take_links:
            path = str(ref.get("take_path", "") or "").strip()
            label = ref.get("title") or ref.get("take_id") or "Take"
            if not path:
                suffix = (" — historical reference; no completed take here" if imported and not ref.get("recording_session_id")
                          else " — requested or finalizing; no completed take yet")
            elif imported:
                suffix = " — stored link — not checked"
            else:
                suffix = " — missing; locate it" if not Path(path).is_dir() else ""
            item = QListWidgetItem(label + suffix)
            item.setData(Qt.ItemDataRole.UserRole, ref.get("take_id"))
            self.takes.addItem(item)
            if selected and ref.get("take_id") == selected:
                self.takes.setCurrentItem(item)
        self._update_take_actions()

    def _update_take_actions(self, _row=None):
        row = self.takes.currentRow()
        available = bool(self.record and self.record.profile != "art"
                         and 0 <= row < len(self.record.take_links)
                         and str(self.record.take_links[row].get("take_path", "") or "").strip())
        self.open_take_button.setEnabled(available)
        self.relink_take_button.setEnabled(available)

    def _changed(self, *_args):
        if not self._loading and self.record is not None:
            self._dirty = True
            self.status.setText("Saving changes…")
            self.timer.start()

    def _edited_record(self):
        pulse = build_session_pulse(creator_profile_key=self.record.profile,
            title=self.title.text(), notes=self.notes.toPlainText())
        return replace(self.record, title=self.title.text().strip() or "Untitled workspace",
            notes=self.notes.toPlainText(), decisions=pulse.decisions,
            actions=tuple((f"@{a.owner} " if a.owner else "") + a.text for a in pulse.actions),
            blockers=pulse.blockers, rehearsal=self.rehearsal.payload(), art=self.art.payload())

    def save_current(self, *, force: bool = False) -> bool:
        """Persist editor changes; ``force`` reconciles even a clean editor.

        A backup must reflect current Notes and any late take/recap facts
        reconciled through the owning coordinator, not a stale cached record.
        """
        self.timer.stop()
        if self._import_in_progress:
            return False
        if self.record is None:
            return True
        if not force and not self._dirty:
            return True
        try:
            edited = self._edited_record()
            self.record = (self._save_record(deepcopy(self._base_record), edited)
                           if self._save_record is not None else self.library.save(edited))
        except (OSError, ValueError) as error:
            self.status.setText(f"Changes are still here but not saved: {error} Use Save to retry or Save as copy.")
            return False
        self._dirty = False
        self._base_record = deepcopy(self.record)
        self.pending_records.pop(self.record.id, None)
        self.summary.setPlainText(workspace_summary(self.record))
        self._render_takes()
        self.status.setText("Saved on this computer.")
        self.record_saved.emit(self.record)
        self.refresh()
        return True

    def _new(self):
        if not self.save_current():
            return
        title, accepted = QInputDialog.getText(self, "New workspace", "Name")
        if accepted and title.strip():
            try:
                record = self.library.create(self.profile.currentData(), title.strip())
                self.search.clear()
                self.refresh()
                self.select_id(record.id)
            except (OSError, ValueError) as error:
                self.status.setText(f"Workspace could not be saved: {error}")

    def _copy(self):
        if self.record is None:
            return
        title, accepted = QInputDialog.getText(self, "Save a separate workspace", "Name",
                                             text=self.title.text() + " copy")
        if not accepted or not title.strip():
            return
        try:
            original = deepcopy(self._base_record)
            edited = self._edited_record()
            fields = {key: getattr(edited, key) for key in ("notes", "decisions", "actions", "blockers",
                "recaps", "take_links", "rehearsal", "art", "mode_key", "source_key", "import_provenance")}
            record = self.library.create(edited.profile, title.strip(), **fields)
            self._dirty = False
            self.pending_records.pop(edited.id, None)
            self.search.clear()
            self.refresh()
            self.select_id(record.id)
            # This is the original loaded snapshot, before editor changes or
            # normalization. Only that exact pending draft may be settled.
            self.copy_saved.emit(original, record)
        except (OSError, ValueError) as error:
            self.status.setText(f"The separate copy could not be saved: {error}")

    def _continue(self):
        if self.record is not None and self.save_current():
            self.selected_record = self.record
            self.continue_requested.emit(self.record)
            self.accept()

    def _open_take(self):
        row = self.takes.currentRow()
        if (self.record and 0 <= row < len(self.record.take_links)
                and str(self.record.take_links[row].get("take_path", "") or "").strip()):
            # Saving may reconcile new take facts and reorder the record's
            # links. Keep the exact reference chosen before that happens.
            selected = deepcopy(self.record.take_links[row])
            if self.record.import_provenance:
                if not selected.get("take_id") or not selected.get("source_identity"):
                    self.status.setText("This imported link has no complete take identity. Open the original recording separately in Studio; this link cannot identify a substitute.")
                    return
                selected["_imported_link"] = True
            if self.save_current():
                self.take_open_requested.emit(selected)

    def _open_bookmark(self, bookmark):
        selected = deepcopy(bookmark)
        if self.record is not None and self.record.import_provenance:
            if not selected.get("take_id") or not selected.get("source_identity"):
                self.status.setText("This imported moment has no complete take identity. Its linked recording cannot be verified.")
                return
            selected["_imported_link"] = True
        self.bookmark_open_requested.emit(selected)

    def _relink_take(self):
        row = self.takes.currentRow()
        if not self.record or not 0 <= row < len(self.record.take_links):
            return
        if not str(self.record.take_links[row].get("take_path", "") or "").strip():
            self.status.setText("This recording has no completed take yet. Wait for it to finish before opening or locating it.")
            return
        path = QFileDialog.getExistingDirectory(self, "Locate the same take")
        if not path:
            return
        from core.take_library import load_take
        ref = self.record.take_links[row]
        try:
            if self.record.import_provenance and (not ref.get("take_id") or not ref.get("source_identity")):
                raise ValueError("This imported link has no complete take identity; its original cannot be proven.")
            take = load_take(Path(path))
            if take is None or not ref.get("take_id") or take.take_id != ref["take_id"]:
                raise ValueError("Choose the original take with the same identity.")
            if self.record.import_provenance:
                from core.take_review import take_source_identity
                if take_source_identity(take) != ref["source_identity"]:
                    raise ValueError("The selected take's source changed; its original identity does not match.")
            links = [dict(item) for item in self.record.take_links]
            links[row]["take_path"] = path
            self.record = replace(self.record, take_links=tuple(links))
            self._dirty = True
            self.save_current()
            self._render_takes()
        except (OSError, ValueError) as error:
            self.status.setText(f"Take was not relinked: {error}")

    def _export(self):
        if self.record is None or not self.save_current():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export workspace summary", "workspace-summary.md", "Markdown (*.md)")
        if not path:
            return
        try:
            # A summary must never replace notes, references, or take evidence.
            # Exclusive creation also rejects existing symlinks and hardlinks.
            import os
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(workspace_summary(self.record))
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                Path(path).unlink(missing_ok=True)
                raise
            self.status.setText("Summary exported. Original work is unchanged.")
        except (OSError, ValueError):
            self.status.setText("Summary was not exported. Choose a new filename in a writable folder.")

    def _backup(self):
        if self.record is None:
            return
        if not self.save_current(force=True):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Back up workspace…", "workspace-backup.json",
                                              "WebJam workspace backup (*.json)")
        if not path:
            return
        from core.workspace_backup import WorkspaceBackupError, export_workspace_backup
        try:
            export_workspace_backup(self.record, path)
            self.status.setText("Workspace backed up on this computer. Metadata only; "
                                "media files are not included.")
        except WorkspaceBackupError as error:
            self.status.setText(f"Workspace was not backed up: {error}")

    def _import_backup(self):
        # Modal file/preview dialogs pump Qt events. Stop this editor's pending
        # autosave and reject coordinator flushes while a preview is open.
        self.timer.stop()
        self._import_in_progress = True
        try:
            self._choose_import_backup()
        finally:
            self._import_in_progress = False
            self.timer.stop()
            if self._dirty and self.status.text() == "Saving changes…":
                self.status.setText("Your draft is still here and remains unsaved. Use Save to retry or Save as copy.")

    def _choose_import_backup(self):
        if not self._sync_import_recovery():
            self._check_import()
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import backup…", "", "WebJam workspace backup (*.json)")
        if not path:
            return
        from core.workspace_backup import import_workspace_backup, preview_workspace_backup
        try:
            preview = preview_workspace_backup(path)
        except (SessionLibraryError, OSError) as error:
            self.status.setText(f"Backup could not be opened: {error}")
            return
        dialog = WorkspaceBackupPreviewDialog(self.library, preview, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            record = import_workspace_backup(self.library, preview)
        except SessionLibraryImportUnconfirmed as error:
            self._import_unconfirmed(error)
            return
        except (SessionLibraryError, OSError) as error:
            self.status.setText(f"Backup was not imported: {error}")
            self._sync_import_recovery()
            return
        self._import_finished(record)

    def _import_finished(self, record):
        self._retry_import = None
        self.refresh(select_first=False)
        self._sync_import_recovery()
        self.status.setText("Backup imported. Draft kept. Metadata only; media files are not included.")

    def _sync_import_recovery(self):
        try:
            pending = self.library.pending_import()
        except (SessionLibraryError, OSError):
            self.import_backup_button.setText("Check previous import")
            return False
        self.import_backup_button.setText("Import backup…" if pending is None else (
            "Retry same import" if self._retry_import == pending else "Check previous import"))
        if pending is None:
            self.import_backup_button.setToolTip("")
        return pending is None

    def _import_unconfirmed(self, error):
        self._retry_import = None
        self.import_backup_button.setText("Check previous import")
        self.import_backup_button.setEnabled(True)
        self.import_backup_button.setToolTip(f"Intended workspace: {error.workspace_id}\nChecksum: {error.expected_sha256}")
        self.status.setText("Import needs checking. Choose Check previous import. Current draft kept.")

    def _check_import(self):
        self.timer.stop()
        try:
            pending = self.library.pending_import()
            if pending is None:
                self._retry_import = None
                self.import_backup_button.setText("Import backup…")
                self.status.setText("No unresolved import is stored here.")
                return
            saved = self.library.reconcile_import(pending)
            if saved is not None:
                self.library.acknowledge_import(pending)
                self.import_backup_button.setText("Check previous import")
                self._import_finished(saved)
                return
            if self._retry_import == pending:
                # A second explicit click retries the SAME prepared identity.
                # Preparation restores a journal whose initial write failed;
                # publication rechecks absence under the library lock.
                self.library.prepare_import(pending)
                saved = self.library.publish_import(pending)
                self.library.acknowledge_import(pending)
                self.import_backup_button.setText("Check previous import")
                self._import_finished(saved)
                return
            self._retry_import = pending
            self.import_backup_button.setText("Retry same import")
            self.status.setText("Import was not published. Choose Retry same import. Current draft kept.")
        except SessionLibraryImportUnconfirmed as error:
            self._import_unconfirmed(error)
        except (SessionLibraryError, OSError) as error:
            self._retry_import = None
            self.import_backup_button.setText("Check previous import")
            self.status.setText(f"Import could not be reconciled; retry is blocked and its evidence is retained: {error}")

    def reject(self):
        if self.save_current():
            super().reject()

    def closeEvent(self, event):
        if self.save_current():
            event.accept()
        else:
            event.ignore()
