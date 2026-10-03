"""Editable saved Art context. Only explicit Open invokes an external tool."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QBoxLayout, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from core.art_workspace import make_reference, normalize_art_workspace
from core.youtube_lesson import YouTubeLesson, parse_youtube_lesson_url


class ArtWorkspacePanel(QWidget):
    changed = Signal()
    lesson_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._action_rows = []
        self._value = normalize_art_workspace({})
        self._defer_reference_checks = False
        self._reference_checks = {}
        self._reference_evidence = {}
        self.reference_action_handler = None
        layout = QVBoxLayout(self)
        hint = QLabel("Keep making with your own tools. References and lesson positions stay local.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.brief = QPlainTextEdit()
        self.progress = QPlainTextEdit()
        self.next_steps = QPlainTextEdit()
        for label, editor in (("Project brief", self.brief), ("Progress", self.progress),
                              ("Next steps", self.next_steps)):
            editor.setAccessibleName(label)
            editor.setTabChangesFocus(True)
            editor.setMinimumHeight(72)
            editor.setMaximumHeight(140)
            editor.textChanged.connect(self._changed)
            form.addRow(label, editor)
        layout.addLayout(form)
        self.references = QListWidget()
        self.references.setAccessibleName("Project references")
        self.references.setMinimumHeight(100)
        layout.addWidget(self.references)
        row = QHBoxLayout()
        for label, callback in (("Add file…", self._add_file), ("Add link…", self._add_link),
                                ("Relink…", self._relink)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self._action_rows.append(row)
        row = QHBoxLayout()
        self.verify_button = QPushButton("Verify reference")
        self.verify_button.clicked.connect(self._verify)
        self.verify_button.setEnabled(False)
        row.addWidget(self.verify_button)
        for label, callback in (("Open reference", self._open), ("Remove reference", self._remove)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self._action_rows.append(row)
        self.use_lesson_button = QPushButton("Use saved lesson")
        self.use_lesson_button.setAccessibleDescription(
            "Set up a shared lesson from this YouTube reference. The browser opens only when you choose."
        )
        self.use_lesson_button.setEnabled(False)
        self.use_lesson_button.clicked.connect(self._use_lesson)
        layout.addWidget(self.use_lesson_button)
        self.bookmarks = QListWidget()
        self.bookmarks.setAccessibleName("Saved lesson bookmarks")
        self.bookmarks.setMinimumHeight(80)
        layout.addWidget(self.bookmarks)
        mark_form = QFormLayout()
        mark_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.position = QDoubleSpinBox()
        self.position.setRange(0, 86400)
        self.position.setDecimals(1)
        self.position.setSuffix(" seconds")
        self.position.setAccessibleName("Lesson position in seconds")
        self.bookmark_note = QLineEdit()
        self.bookmark_note.setMaxLength(4000)
        self.bookmark_note.setAccessibleName("Lesson bookmark note")
        mark_form.addRow("Lesson position", self.position)
        mark_form.addRow("Bookmark note", self.bookmark_note)
        layout.addLayout(mark_form)
        row = QHBoxLayout()
        for label, callback in (("Save lesson bookmark", self._add_bookmark),
                                ("Remove bookmark", self._remove_bookmark)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self._action_rows.append(row)
        # Start narrow so a scroll area's initial minimum width cannot prevent
        # its child from ever receiving a compact resize event.
        for row in self._action_rows:
            row.setDirection(QBoxLayout.Direction.TopToBottom)
        self.status = QLabel("Select a reference and enter the position you want to return to.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.references.currentRowChanged.connect(self._reference_status)
        self.bookmarks.currentRowChanged.connect(self._bookmark_selected)

    def _sync_action_rows(self):
        margins = self.layout().contentsMargins()
        parent = self.parentWidget()
        available = min(self.width(), parent.width()) if parent is not None else self.width()
        width = available - margins.left() - margins.right()
        for row in self._action_rows:
            needed = sum(row.itemAt(index).minimumSize().width()
                         for index in range(row.count())) + row.spacing() * (row.count() - 1)
            direction = (QBoxLayout.Direction.TopToBottom if needed > width
                         else QBoxLayout.Direction.LeftToRight)
            if row.direction() != direction:
                row.setDirection(direction)
        # Reflow can shrink the scroll child during the same layout pass.
        # Lay out against its final width so hidden-tab activation cannot leave
        # wider child controls clipped inside an otherwise correct viewport.
        if self.layout().geometry() != self.rect():
            self.layout().setGeometry(self.rect())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_action_rows()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and getattr(self, "_action_rows", None):
            self._sync_action_rows()
        elif event.type() == QEvent.Type.ParentChange and self.parentWidget() is not None:
            self.parentWidget().installEventFilter(self)
        return result

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._sync_action_rows()
        return super().eventFilter(watched, event)

    def load_payload(self, value: dict, *, defer_reference_checks: bool = False) -> None:
        normalized = normalize_art_workspace(value)
        self._loading = True
        try:
            self._value = normalized
            # Imported locators may be network paths. Even a passive stat can
            # connect to a remote volume, so provenance defers all such reads.
            self._defer_reference_checks = defer_reference_checks
            self._reference_checks = {}
            self._reference_evidence = {}
            self.brief.setPlainText(normalized["brief"])
            self.progress.setPlainText(normalized["progress"])
            self.next_steps.setPlainText(normalized["next_steps"])
            self._render_lists()
        finally:
            self._loading = False

    def payload(self) -> dict:
        return normalize_art_workspace(dict(self._value, brief=self.brief.toPlainText(),
            progress=self.progress.toPlainText(), next_steps=self.next_steps.toPlainText()))

    def _changed(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _render_lists(self) -> None:
        selected_reference = self.references.currentItem()
        reference_id = selected_reference.data(Qt.ItemDataRole.UserRole) if selected_reference else None
        selected_bookmark = self.bookmarks.currentItem()
        bookmark_id = selected_bookmark.data(Qt.ItemDataRole.UserRole) if selected_bookmark else None
        self.references.blockSignals(True)
        self.bookmarks.blockSignals(True)
        self.references.clear()
        for ref in self._value["references"]:
            evidence = self._reference_evidence.get((ref["id"], ref["locator"]))
            if evidence is not None:
                suffix = " — " + evidence
            elif self._defer_reference_checks:
                suffix = " — stored link — not checked"
                checked = self._reference_checks.get((ref["id"], ref["locator"]))
                if checked is not None:
                    suffix = " — file available; content not verified" if checked else " — missing; Relink…"
            else:
                missing = ref["kind"] == "file" and not Path(ref["locator"]).is_file()
                suffix = " — missing; Relink…" if missing else ""
            item = QListWidgetItem(ref["title"] + suffix)
            item.setData(Qt.ItemDataRole.UserRole, ref["id"])
            self.references.addItem(item)
            if ref["id"] == reference_id:
                self.references.setCurrentItem(item)
        self.bookmarks.clear()
        titles = {ref["id"]: ref["title"] for ref in self._value["references"]}
        for mark in self._value["bookmarks"]:
            seconds = int(mark["seconds"])
            item = QListWidgetItem(f"{titles[mark['reference_id']]} · {seconds // 60}:{seconds % 60:02d} · {mark['note']}")
            item.setData(Qt.ItemDataRole.UserRole, mark["id"])
            self.bookmarks.addItem(item)
            if mark["id"] == bookmark_id:
                self.bookmarks.setCurrentItem(item)
        self.references.blockSignals(False)
        self.bookmarks.blockSignals(False)
        self._reference_status(self.references.currentRow())

    def add_reference(self, locator: str, *, kind: str, title: str = "") -> None:
        entry = make_reference(locator, kind=kind, title=title)
        self._value = normalize_art_workspace(dict(self._value,
            references=[*self._value["references"], entry]))
        self._render_lists()
        self.references.setCurrentRow(len(self._value["references"]) - 1)
        self._changed()

    def _add_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Remember a local project reference")
        if path:
            try:
                self.add_reference(path, kind="file")
            except ValueError as error:
                self.status.setText(str(error))

    def _add_link(self) -> None:
        url, accepted = QInputDialog.getText(self, "Remember a reference", "http/https link")
        if accepted and url:
            try:
                self.add_reference(url.strip(), kind="url")
            except ValueError as error:
                self.status.setText(str(error))

    def _selected_reference(self):
        index = self.references.currentRow()
        return self._value["references"][index] if 0 <= index < len(self._value["references"]) else None

    def _reference_status(self, _index: int) -> None:
        ref = self._selected_reference()
        self.use_lesson_button.setEnabled(self.selected_lesson() is not None)
        self.verify_button.setEnabled(bool(ref and ref["kind"] == "file" and self.reference_action_handler is not None))
        evidence = self._reference_evidence.get((ref["id"], ref["locator"])) if ref else None
        if evidence is not None:
            self.status.setText(evidence + ". Verify again after changing the file.")
        elif ref and self._defer_reference_checks:
            checked = self._reference_checks.get((ref["id"], ref["locator"]))
            if checked is False:
                self.status.setText("This reference moved or is unavailable. Choose Relink… to locate it.")
            elif checked is True:
                self.status.setText("File was available when checked; its content is not verified against the original.")
            else:
                self.status.setText("Stored link — not checked. Choose Open reference or Relink… to access it.")
        elif ref and ref["kind"] == "file" and not Path(ref["locator"]).is_file():
            self.status.setText("This reference moved or is unavailable. Choose Relink… to locate it.")
        else:
            self.status.setText("Open reference launches its usual app only when you choose it.")

    def _relink(self) -> None:
        ref = self._selected_reference()
        if not ref or ref["kind"] != "file":
            self.status.setText("Select a local file reference to relink.")
            return
        if self.reference_action_handler is not None and self.reference_action_handler("relink", ref):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Locate this project reference")
        if path:
            if self._defer_reference_checks:
                try:
                    if not Path(path).is_file():
                        raise ValueError("Choose an available local file.")
                except (OSError, ValueError) as error:
                    self.status.setText(f"Reference was not relinked: {error}")
                    return
            ref["locator"] = path
            self._reference_checks[(ref["id"], path)] = True
            self._render_lists()
            self._changed()

    def _open(self) -> None:
        ref = self._selected_reference()
        if not ref:
            return
        if self.reference_action_handler is not None and self.reference_action_handler("open", ref):
            return
        if ref["kind"] == "file":
            try:
                available = Path(ref["locator"]).is_file()
            except (OSError, ValueError):
                available = False
            self._reference_checks[(ref["id"], ref["locator"])] = available
            if self._defer_reference_checks:
                self._render_lists()
            if not available:
                self._reference_status(self.references.currentRow())
                return
            url = QUrl.fromLocalFile(ref["locator"])
        else:
            url = QUrl(ref["locator"])
        if not QDesktopServices.openUrl(url):
            self.status.setText("The reference could not be opened. Check its file or link.")

    def _verify(self):
        ref = self._selected_reference()
        if ref is not None and self.reference_action_handler is not None:
            self.reference_action_handler("verify", ref)

    def set_reference_evidence(self, reference_id, locator, message):
        if any(ref["id"] == reference_id and ref["locator"] == locator for ref in self._value["references"]):
            self._reference_evidence[(reference_id, locator)] = message
            self._render_lists()

    def replace_reference_locator(self, reference_id, old_locator, new_locator):
        ref = next((r for r in self._value["references"] if r["id"] == reference_id and r["locator"] == old_locator), None)
        if ref is None:
            raise ValueError("Reference changed before relinking.")
        ref["locator"] = new_locator
        self._render_lists()
        self._changed()

    def open_verified_reference(self, reference_id, expected_locator, verified_locator):
        ref = self._selected_reference()
        if ref is None or ref["id"] != reference_id or ref["locator"] != expected_locator:
            return False
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(verified_locator))
        if not opened:
            self.status.setText("The checked reference could not be opened by its usual app.")
        return opened

    def _remove(self) -> None:
        ref = self._selected_reference()
        if ref:
            self._value["references"].remove(ref)
            self._value["bookmarks"] = [item for item in self._value["bookmarks"] if item["reference_id"] != ref["id"]]
            self._render_lists()
            self._changed()

    def _add_bookmark(self) -> None:
        ref = self._selected_reference()
        if not ref:
            self.status.setText("Select the lesson reference before saving a position.")
            return
        mark = {"id": uuid4().hex, "reference_id": ref["id"],
                "seconds": self.position.value(), "note": self.bookmark_note.text()}
        try:
            self._value = normalize_art_workspace(dict(self._value,
                bookmarks=[*self._value["bookmarks"], mark]))
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._render_lists()
        self.bookmark_note.clear()
        self._changed()

    def _remove_bookmark(self) -> None:
        index = self.bookmarks.currentRow()
        if 0 <= index < len(self._value["bookmarks"]):
            self._value["bookmarks"].pop(index)
            self._render_lists()
            self._changed()

    def selected_lesson(self):
        ref = self._selected_reference()
        if ref is None or ref["kind"] != "url":
            return None
        try:
            lesson = parse_youtube_lesson_url(ref["locator"])
        except ValueError:
            return None
        index = self.bookmarks.currentRow()
        if 0 <= index < len(self._value["bookmarks"]):
            mark = self._value["bookmarks"][index]
            if mark["reference_id"] == ref["id"]:
                lesson = YouTubeLesson(lesson.video_id, int(mark["seconds"]))
        return ref["id"], lesson.playback_url

    def _use_lesson(self):
        selected = self.selected_lesson()
        if selected is not None and self.use_lesson_button.isEnabled() and self.use_lesson_button.isVisible():
            self.lesson_requested.emit(*selected)

    def _bookmark_selected(self, index: int) -> None:
        if not 0 <= index < len(self._value["bookmarks"]):
            return
        mark = self._value["bookmarks"][index]
        for row, ref in enumerate(self._value["references"]):
            if ref["id"] == mark["reference_id"]:
                self.references.setCurrentRow(row)
        self.position.setValue(mark["seconds"])
        self.bookmark_note.setText(mark["note"])
        self.status.setText("Saved lesson position shown below. Open reference, then return to this position in your player.")
