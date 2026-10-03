"""Editable local setlist, per-song drafts and honest moment bookmarks."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from core.file_io import atomic_write_text
from core.rehearsal_plan import (
    MAX_BOOKMARKS, MAX_PLAN_FILE_BYTES, MAX_SONGS, RehearsalPlan, make_bookmark,
)
from core.song_clock import MAX_TEMPO_BPM, MIN_TEMPO_BPM
from webjam_qt.theme.tokens import Space


class RehearsalPlanPanel(QFrame):
    changed = Signal()
    song_selected = Signal(dict)
    bookmark_requested = Signal(str)
    bookmark_open_requested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RehearsalPlan")
        self.setAccessibleName("Rehearsal plan")
        self._plan = RehearsalPlan()
        self._loading = False
        self._removed: tuple[int, dict] | None = None
        self._action_rows: list[QBoxLayout] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Space.XS, Space.XS, Space.XS, Space.XS)
        layout.setSpacing(Space.SM)
        self._scroll.setWidget(content)
        root.addWidget(self._scroll)

        self._title = QLineEdit()
        self._title.setAccessibleName("Plan name")
        self._title.setPlaceholderText("Rehearsal plan")
        layout.addWidget(self._title)
        self._progress = QLabel()
        self._progress.setWordWrap(True)
        self._progress.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._progress)
        self._song_choice = QComboBox()
        self._song_choice.setAccessibleName("Current song in rehearsal order")
        self._song_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._song_choice.setMinimumContentsLength(8)
        layout.addWidget(self._song_choice)
        self._first_song = self._button("Add your first song")
        self._first_song.clicked.connect(self.add_song)
        layout.addWidget(self._first_song)
        self._previous, self._next = self._row(layout, ("Previous", "Next"))
        self._undo = self._button("Undo remove")
        self._undo.hide()

        self._editor = QWidget()
        form = QFormLayout(self._editor)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self._song_title = QLineEdit()
        self._song_title.setAccessibleName("Song title")
        self._key = QLineEdit()
        self._key.setAccessibleName("Song key")
        self._key.setPlaceholderText("For example, A minor")
        self._tempo = QSpinBox()
        self._tempo.setRange(int(MIN_TEMPO_BPM) - 1, int(MAX_TEMPO_BPM))
        self._tempo.setSpecialValueText("Not set")
        self._tempo.setSuffix(" BPM")
        self._tempo.setAccessibleName("Planned tempo")
        self._tempo.setToolTip("A stated rehearsal tempo; changing it does not change the song clock.")
        self._goals = self._text_editor("Goals for this song", 65)
        self._notes = self._text_editor("Notes for this song", 100)
        self._next_steps = self._text_editor("Next steps for this song", 65)
        for label, field in (("Song", self._song_title), ("Key", self._key),
                             ("Tempo", self._tempo), ("Goals", self._goals),
                             ("Notes", self._notes), ("Next steps", self._next_steps)):
            form.addRow(label, field)
        self._completed = QCheckBox("Song complete")
        self._completed.setAccessibleName("Song marked complete for this rehearsal")
        form.addRow(self._completed)
        layout.addWidget(self._editor)

        self._moment_note = QLineEdit()
        self._moment_note.setAccessibleName("Moment note")
        self._moment_note.setPlaceholderText("What should we remember?")
        layout.addWidget(self._moment_note)
        self._mark = self._button("Mark moment")
        shortcut = QKeySequence("Ctrl+M")
        self._mark.setToolTip(f"Mark moment ({shortcut.toString(QKeySequence.SequenceFormat.NativeText)})")
        layout.addWidget(self._mark)
        self._moment_help = QLabel(
            "A moment is saved as a note unless a recording position can be confirmed."
        )
        self._moment_help.setWordWrap(True)
        self._moment_help.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._moment_help)
        self._bookmarks = QListWidget()
        self._bookmarks.setAccessibleName("Moments for this song")
        self._bookmarks.setWordWrap(True)
        self._bookmarks.setMinimumHeight(95)
        self._bookmarks.setMaximumHeight(180)
        self._bookmarks.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._bookmarks)
        self._open_moment, self._remove_moment = self._row(layout, ("Open in take", "Remove moment"))
        self._add, self._remove = self._row(layout, ("Add song", "Remove song"))
        self._earlier, self._later = self._row(layout, ("Move earlier", "Move later"))
        layout.addWidget(self._undo)
        self._save, self._load = self._row(layout, ("Save plan…", "Add saved plan…"))
        help_text = QLabel("Reuse the song order and goals. Notes and moments stay with this session.")
        help_text.setTextFormat(Qt.TextFormat.PlainText)
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self._feedback = QLabel()
        self._feedback.setWordWrap(True)
        self._feedback.setTextFormat(Qt.TextFormat.PlainText)
        self._feedback.setAccessibleName("Plan status")
        layout.addWidget(self._feedback)
        layout.addStretch(1)

        self._title.textChanged.connect(self._edit_title)
        for editor in (self._song_title, self._key):
            editor.textChanged.connect(self._edit_song)
        self._tempo.valueChanged.connect(self._edit_song)
        for editor in (self._goals, self._notes, self._next_steps):
            editor.textChanged.connect(self._edit_song)
        self._completed.toggled.connect(self._edit_song)
        self._song_choice.currentIndexChanged.connect(self._choose_song)
        self._previous.clicked.connect(lambda: self._advance(-1))
        self._next.clicked.connect(lambda: self._advance(1))
        self._add.clicked.connect(self.add_song)
        self._remove.clicked.connect(self._remove_song)
        self._earlier.clicked.connect(lambda: self._move(-1))
        self._later.clicked.connect(lambda: self._move(1))
        self._undo.clicked.connect(self._undo_remove)
        self._mark.clicked.connect(self._request_bookmark)
        self._moment_note.textChanged.connect(self._edit_moment_draft)
        self._moment_note.returnPressed.connect(self._request_bookmark)
        self._bookmark_shortcut = QShortcut(shortcut, self)
        self._bookmark_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._bookmark_shortcut.activated.connect(self._request_bookmark)
        self._bookmarks.currentRowChanged.connect(self._sync_bookmark_actions)
        self._bookmarks.itemActivated.connect(lambda _item: self._open_bookmark())
        self._open_moment.clicked.connect(self._open_bookmark)
        self._remove_moment.clicked.connect(self._delete_bookmark)
        self._save.clicked.connect(self._save_template)
        self._load.clicked.connect(self._load_template)
        self._render()

    @staticmethod
    def _button(text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("GhostButton")
        button.setAccessibleName(text.rstrip("…"))
        return button

    def _row(self, layout: QVBoxLayout, labels: tuple[str, str]) -> tuple[QPushButton, QPushButton]:
        row = QHBoxLayout()
        row.setSpacing(Space.XS)
        buttons = tuple(self._button(label) for label in labels)
        for button in buttons:
            row.addWidget(button)
        layout.addLayout(row)
        self._action_rows.append(row)
        return buttons

    @staticmethod
    def _text_editor(name: str, height: int) -> QTextEdit:
        editor = QTextEdit()
        editor.setAcceptRichText(False)
        editor.setTabChangesFocus(True)
        editor.setAccessibleName(name)
        editor.setMinimumHeight(height)
        editor.setMaximumHeight(height + 80)
        return editor

    def load_payload(self, payload: dict) -> None:
        plan = RehearsalPlan.from_payload(payload)
        self._plan = plan
        self._removed = None
        self._undo.hide()
        self._feedback.clear()
        self._render()

    def payload(self) -> dict:
        return self._plan.payload()

    def summary(self) -> str:
        return self._plan.summary()

    def add_song(self, title: str = "New song") -> None:
        if isinstance(title, bool):  # QPushButton.clicked supplies checked.
            title = "New song"
        try:
            self._plan.add_song(title)
        except ValueError as exc:
            self._feedback.setText(str(exc))
            return
        self._render()
        self.changed.emit()
        self._emit_selected()
        self._song_title.setFocus()
        self._song_title.selectAll()

    def add_bookmark(self, note: str, **evidence) -> None:
        song = self._plan.current
        if song is None:
            self._feedback.setText("Add a song before marking a moment.")
            return
        if len(song["bookmarks"]) >= MAX_BOOKMARKS:
            self._feedback.setText("This song already has 500 moments.")
            return
        bookmark = make_bookmark(note, **evidence)
        song["bookmarks"].append(bookmark)
        self._moment_note.clear()
        self._render_bookmarks()
        self._bookmarks.setCurrentRow(len(song["bookmarks"]) - 1)
        self._feedback.setText("Moment saved with a take position." if bookmark["position_seconds"] is not None
                               else "Moment saved as a note; no recording position was confirmed.")
        self.changed.emit()

    def _request_bookmark(self) -> None:
        if self._plan.current is not None:
            self.bookmark_requested.emit(self._moment_note.text().strip() or "Moment")

    def _edit_title(self) -> None:
        if not self._loading:
            self._plan.title = self._title.text()
            self.changed.emit()

    def _edit_moment_draft(self) -> None:
        if not self._loading and self._plan.current is not None:
            self._plan.current["moment_draft"] = self._moment_note.text()
            self.changed.emit()

    def _edit_song(self) -> None:
        song = self._plan.current
        if self._loading or song is None:
            return
        song.update(title=self._song_title.text(), key=self._key.text(),
                    tempo=self._tempo.value() if self._tempo.value() >= MIN_TEMPO_BPM else None,
                    goals=self._goals.toPlainText(), notes=self._notes.toPlainText(),
                    next_steps=self._next_steps.toPlainText(), completed=self._completed.isChecked())
        self._song_choice.setItemText(self._plan.index, self._song_label(self._plan.index, song))
        self._render_progress()
        self.changed.emit()

    def _choose_song(self, index: int) -> None:
        if not self._loading and 0 <= index < len(self._plan.songs):
            if self._plan.select(self._plan.songs[index]["id"]):
                self._render()
                self.changed.emit()
                self._emit_selected()

    def _advance(self, delta: int) -> None:
        if self._plan.advance(delta):
            self._render()
            self.changed.emit()
            self._emit_selected()

    def _move(self, delta: int) -> None:
        if self._plan.move(delta):
            self._render()
            self.changed.emit()

    def _remove_song(self) -> None:
        index = self._plan.index
        song = self._plan.remove_current()
        if song is not None:
            self._removed = index, song
            self._undo.show()
            self._render()
            self._feedback.setText("Song removed. Undo remove restores its notes and moments.")
            self.changed.emit()
            self._emit_selected()

    def _undo_remove(self) -> None:
        if self._removed is None or len(self._plan.songs) >= MAX_SONGS:
            return
        index, song = self._removed
        self._plan.songs.insert(min(index, len(self._plan.songs)), song)
        self._plan.active_song_id = song["id"]
        self._removed = None
        self._undo.hide()
        self._render()
        self.changed.emit()
        self._emit_selected()

    def _emit_selected(self) -> None:
        if self._plan.current is not None:
            self.song_selected.emit(self.payload()["songs"][self._plan.index])

    @staticmethod
    def _song_label(index: int, song: dict) -> str:
        return f"{index + 1}. {song['title'].strip() or 'Untitled song'}" + (" · complete" if song["completed"] else "")

    def _render_progress(self) -> None:
        total = len(self._plan.songs)
        complete = sum(song["completed"] for song in self._plan.songs)
        self._progress.setText(f"{complete} of {total} songs marked complete" if total
                               else "Add the songs you want to work on.")

    def _render(self) -> None:
        self._loading = True
        try:
            self._title.setText(self._plan.title)
            self._song_choice.clear()
            for index, song in enumerate(self._plan.songs):
                self._song_choice.addItem(self._song_label(index, song), song["id"])
            self._song_choice.setCurrentIndex(self._plan.index)
            song = self._plan.current or {}
            self._first_song.setVisible(not self._plan.songs)
            self._song_title.setText(song.get("title", ""))
            self._key.setText(song.get("key", ""))
            self._tempo.setValue(song.get("tempo") or int(MIN_TEMPO_BPM) - 1)
            self._goals.setPlainText(song.get("goals", ""))
            self._notes.setPlainText(song.get("notes", ""))
            self._next_steps.setPlainText(song.get("next_steps", ""))
            self._moment_note.setText(song.get("moment_draft", ""))
            self._completed.setChecked(song.get("completed", False))
            self._editor.setEnabled(bool(song))
            self._mark.setEnabled(bool(song))
            self._moment_note.setEnabled(bool(song))
            self._remove.setEnabled(bool(song))
            self._save.setEnabled(bool(self._plan.songs))
            self._add.setEnabled(len(self._plan.songs) < MAX_SONGS)
            for button in (self._previous, self._earlier):
                button.setEnabled(self._plan.index > 0)
            for button in (self._next, self._later):
                button.setEnabled(0 <= self._plan.index < len(self._plan.songs) - 1)
            self._render_progress()
            self._render_bookmarks()
        finally:
            self._loading = False

    def _render_bookmarks(self) -> None:
        self._bookmarks.clear()
        song = self._plan.current
        for bookmark in song["bookmarks"] if song else []:
            seconds = bookmark["position_seconds"]
            prefix = f"Take {int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds is not None else "Note"
            item = QListWidgetItem(f"{prefix} · {bookmark['note']}")
            item.setData(Qt.ItemDataRole.UserRole, bookmark["id"])
            self._bookmarks.addItem(item)
        self._sync_bookmark_actions()

    def relink_take(self, take_id, source_identity, locator):
        """Update only matching locations, retaining every open song/moment draft."""
        for song in self._plan.songs:
            for bookmark in song["bookmarks"]:
                if bookmark.get("take_id") == take_id and bookmark.get("source_identity") == source_identity:
                    bookmark["take_path"] = locator
        # Titles/positions do not change, so leave the current selection and
        # editing widgets alone instead of rebuilding this panel.
        self.changed.emit()

    def _selected_bookmark(self) -> dict | None:
        song, row = self._plan.current, self._bookmarks.currentRow()
        return song["bookmarks"][row] if song and 0 <= row < len(song["bookmarks"]) else None

    def _sync_bookmark_actions(self) -> None:
        bookmark = self._selected_bookmark()
        self._open_moment.setEnabled(bookmark is not None and bookmark["position_seconds"] is not None)
        self._remove_moment.setEnabled(bookmark is not None)

    def _open_bookmark(self) -> None:
        bookmark = self._selected_bookmark()
        if bookmark is not None and bookmark["position_seconds"] is not None:
            self.bookmark_open_requested.emit(dict(bookmark))

    def _delete_bookmark(self) -> None:
        if self._selected_bookmark() is not None:
            del self._plan.current["bookmarks"][self._bookmarks.currentRow()]
            self._render_bookmarks()
            self.changed.emit()

    def _save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save rehearsal plan", "rehearsal-plan.json", "Rehearsal plans (*.json)")
        if not path:
            return
        try:
            atomic_write_text(path, json.dumps(self._plan.template_payload(), ensure_ascii=False, indent=2), mode=0o600)
        except OSError:
            QMessageBox.warning(self, "Plan not saved", "Choose another folder and try again.")
            return
        self._feedback.setText("Song order and goals saved for another rehearsal.")

    def _load_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Add a saved rehearsal plan", "", "Rehearsal plans (*.json)")
        if not path:
            return
        try:
            with Path(path).open("rb") as stream:
                data = stream.read(MAX_PLAN_FILE_BYTES + 1)
            if len(data) > MAX_PLAN_FILE_BYTES:
                raise ValueError("This plan file is too large.")
            payload = json.loads(data)
            count = self._plan.append_template(payload)
        except (OSError, UnicodeError, ValueError, RecursionError):
            QMessageBox.warning(self, "Plan not added", "Choose a valid WebJam rehearsal plan with up to 200 songs.")
            return
        self._render()
        self._feedback.setText(f"Added {count} songs. Existing notes and moments were kept.")
        self.changed.emit()
        self._emit_selected()

    def _sync_action_rows(self) -> None:
        if not hasattr(self, "_scroll"):
            return
        width = self._scroll.viewport().width() - 2 * Space.XS
        for row in self._action_rows:
            needed = sum(row.itemAt(index).widget().minimumSizeHint().width()
                         for index in range(row.count())) + row.spacing()
            direction = (QBoxLayout.Direction.TopToBottom if needed > width
                         else QBoxLayout.Direction.LeftToRight)
            if row.direction() != direction:
                row.setDirection(direction)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_action_rows()

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, "_action_rows"):
            self._sync_action_rows()
        return result
