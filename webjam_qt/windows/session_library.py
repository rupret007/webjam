"""A searchable local library and editor, available before joining a room."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from core.art_workspace import art_summary
from core.session_intelligence import build_session_pulse
from core.session_library import SessionLibrary, validate_session_record
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
        self.new_button = QPushButton("New workspace…")
        self.new_button.clicked.connect(self._new)
        create_row.addWidget(self.new_button)
        self.copy_button = QPushButton("Save as copy…")
        self.copy_button.clicked.connect(self._copy)
        create_row.addWidget(self.copy_button)
        outer.addLayout(create_row)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        details = QWidget()
        form = QFormLayout(details)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title = QLineEdit()
        self.title.setMaxLength(200)
        self.title.setAccessibleName("Workspace title")
        self.notes = QPlainTextEdit()
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
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_current)
        row.addWidget(self.save_button)
        self.export_button = QPushButton("Export summary…")
        self.export_button.clicked.connect(self._export)
        row.addWidget(self.export_button)
        self.continue_button = QPushButton("Continue this work")
        self.continue_button.clicked.connect(self._continue)
        row.addWidget(self.continue_button)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
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
        self.rehearsal.bookmark_open_requested.connect(self.bookmark_open_requested)
        self.search.textChanged.connect(self.refresh)
        self.history.currentRowChanged.connect(self._select)
        self.refresh()
        self._initial_current_id = ""
        if current_id:
            self.select_id(current_id)

    def _add_tab(self, widget, label):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, label)

    def refresh(self, _query=None):
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
        if self.record is None and records:
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
            self.art.load_payload(art)
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
        for ref in self.record.take_links:
            path = str(ref.get("take_path", "") or "").strip()
            label = ref.get("title") or ref.get("take_id") or "Take"
            if not path:
                suffix = " — requested or finalizing; no completed take yet"
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

    def save_current(self) -> bool:
        self.timer.stop()
        if not self._dirty or self.record is None:
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
                "recaps", "take_links", "rehearsal", "art", "mode_key")}
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
            if self.save_current():
                self.take_open_requested.emit(selected)

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
            take = load_take(Path(path))
            if take is None or not ref.get("take_id") or take.take_id != ref["take_id"]:
                raise ValueError("Choose the original take with the same identity.")
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

    def reject(self):
        if self.save_current():
            super().reject()

    def closeEvent(self, event):
        if self.save_current():
            event.accept()
        else:
            event.ignore()
