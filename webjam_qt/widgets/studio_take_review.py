"""A compact, non-modal place to keep take notes and compare two takes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QLabel,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.take_review import TakeReview


class StudioTakeReviewDialog(QDialog):
    save_requested = Signal()
    assign_requested = Signal(str)
    audition_requested = Signal(str)
    export_requested = Signal()

    def __init__(self, parent, flush):
        super().__init__(parent)
        self.setWindowTitle("Review and compare takes")
        self.setModal(False)
        self.resize(520, 550)
        self._flush = flush
        self.dirty = False
        self.review: TakeReview | None = None
        self.title = QLabel("Choose a completed take")
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.favorite = QCheckBox("Favorite take")
        self.favorite.setAccessibleName("Favorite selected take")
        self.notes = QPlainTextEdit()
        self.notes.setAccessibleName("Private review notes for selected take")
        self.notes.setPlaceholderText("What worked? What should we try next?")
        self.notes.setMaximumHeight(160)
        self.save_button = QPushButton("Save review")
        self.save_button.clicked.connect(self.save_requested.emit)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.favorite.toggled.connect(self._changed)
        self.notes.textChanged.connect(self._changed)
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAccessibleName("Take review controls")
        content = QWidget()
        layout = QVBoxLayout(content)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        layout.addWidget(self.title)
        layout.addWidget(self.favorite)
        layout.addWidget(self.notes)
        layout.addWidget(self.save_button)
        self.slots = {}
        grid = QGridLayout()
        for row, slot in enumerate(("A", "B")):
            assign = QPushButton(f"Set {slot}")
            assign.setAccessibleName(f"Use selected take as comparison {slot}")
            assign.clicked.connect(lambda _checked=False, key=slot: self.assign_requested.emit(key))
            listen = QPushButton(f"Listen {slot}")
            listen.setAccessibleName(f"Audition comparison take {slot}")
            listen.clicked.connect(lambda _checked=False, key=slot: self.audition_requested.emit(key))
            listen.setEnabled(False)
            label = QLabel("No take selected")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            grid.addWidget(assign, row, 0)
            grid.addWidget(listen, row, 1)
            grid.addWidget(label, row, 2)
            self.slots[slot] = (listen, label)
        layout.addLayout(grid)
        explanation = QLabel("A/B plays each take with its saved mix. Pending edits must save before switching; auditioning makes no arrangement edits.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.export_button = QPushButton("Export reviewed take")
        self.export_button.clicked.connect(self.export_requested.emit)
        layout.addWidget(self.export_button)
        self.receipt_button = QPushButton("Export receipt…")
        self.receipt_button.setEnabled(False)
        self.receipt_button.clicked.connect(self._show_receipt)
        layout.addWidget(self.receipt_button)
        layout.addWidget(self.status)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.close)
        outer.addWidget(close)
        self._receipt_details = ""

    def _changed(self, *_args):
        self.dirty = True
        self.status.setText("Review changes are not saved yet.")

    def apply_review(self, title: str, review: TakeReview | None):
        self.title.setText(title)
        self.review = review
        self.favorite.blockSignals(True)
        self.notes.blockSignals(True)
        self.favorite.setChecked(bool(review and review.favorite))
        self.notes.setPlainText(review.notes if review else "")
        self.favorite.blockSignals(False)
        self.notes.blockSignals(False)
        self.favorite.setEnabled(review is not None)
        self.notes.setEnabled(review is not None)
        self.save_button.setEnabled(review is not None)
        self.dirty = False
        self.status.setText("")

    def set_receipt(self, details: str = ""):
        self._receipt_details = details
        self.receipt_button.setEnabled(bool(details))

    def _show_receipt(self):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("Verified export receipt")
        dialog.resize(640, 500)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setAccessibleName("Exact export sources settings destination and checksums")
        text.setPlainText(self._receipt_details)
        layout.addWidget(text)
        dialog.show()

    def reject(self):
        if self._flush():
            super().reject()

    def closeEvent(self, event):
        if self._flush():
            event.accept()
        else:
            event.ignore()
