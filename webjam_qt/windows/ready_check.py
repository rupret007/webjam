"""Non-blocking, rerunnable pre-session readiness report."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class ReadyCheckDialog(QDialog):
    settings_requested = Signal()
    practice_requested = Signal()
    _report_ready = Signal(object)

    def __init__(
        self,
        settings_provider: Callable[[], object],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("WebJam — Ready Check")
        self.resize(620, 470)
        self.setModal(False)
        self._settings_provider = settings_provider
        self._scan_id = 0

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Automated checks cover WebJam configuration and devices. "
            "Practice remains the final proof of real input, headphones, and Jamulus routing."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._summary = QLabel("Checking your setup…")
        self._summary.setObjectName("ReadySummary")
        self._summary.setWordWrap(True)
        self._summary.setAccessibleName("Ready Check summary")
        layout.addWidget(self._summary)

        self._report = QScrollArea()
        self._report.setWidgetResizable(True)
        self._report.setFrameShape(QScrollArea.Shape.NoFrame)
        self._report.setAccessibleName("Ready Check results")
        self._report_content = QWidget()
        self._report_layout = QVBoxLayout(self._report_content)
        self._report_layout.setContentsMargins(0, 0, 0, 0)
        self._report_layout.setSpacing(8)
        self._report.setWidget(self._report_content)
        layout.addWidget(self._report, stretch=1)

        actions = QHBoxLayout()
        self._rerun = QPushButton("Run Again")
        self._rerun.clicked.connect(self.run_checks)
        actions.addWidget(self._rerun)
        settings = QPushButton("Open Settings")
        settings.clicked.connect(self.settings_requested.emit)
        actions.addWidget(settings)
        practice = QPushButton("Start Practice")
        practice.clicked.connect(self.practice_requested.emit)
        actions.addWidget(practice)
        actions.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)

        self._report_ready.connect(self._apply_report)
        self.run_checks()

    def run_checks(self) -> None:
        self._scan_id += 1
        scan_id = self._scan_id
        self._rerun.setEnabled(False)
        self._summary.setText("Checking devices and recorder…")
        self._summary.setProperty("result", "checking")
        self._clear_rows()

        def _worker() -> None:
            from core.preflight import run_ready_check

            report = run_ready_check(self._settings_provider())
            self._report_ready.emit((scan_id, report))

        threading.Thread(target=_worker, daemon=True, name="ready-check").start()

    def _apply_report(self, payload: object) -> None:
        scan_id, report = payload
        if scan_id != self._scan_id:
            return
        raw_items = getattr(report, "items", [])
        items = list(raw_items) if isinstance(raw_items, (list, tuple)) else []
        required_failures = [item for item in items if item.required and not item.ok]
        optional_warnings = [item for item in items if not item.required and not item.ok]
        if getattr(report, "all_ok", False):
            self._summary.setText("Ready to play — all required checks passed.")
            self._summary.setProperty("result", "pass")
        else:
            count = len(required_failures)
            self._summary.setText(
                f"Fix {count} required item{'s' if count != 1 else ''} before the jam."
                if count
                else "Review the setup details below."
            )
            self._summary.setProperty("result", "fail")
        if optional_warnings:
            self._summary.setText(
                f"{self._summary.text()} {len(optional_warnings)} optional warning"
                f"{'s' if len(optional_warnings) != 1 else ''}."
            )
        self._repolish(self._summary)

        first_failure = None
        if items:
            for item in items:
                row = self._add_row(item)
                if first_failure is None and item.required and not item.ok:
                    first_failure = row
        else:
            # Compatibility for injected/older reports without structured items.
            fallback = QLabel(report.to_text())
            fallback.setWordWrap(True)
            fallback.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._report_layout.addWidget(fallback)
        self._report_layout.addStretch(1)
        self._rerun.setEnabled(True)
        if first_failure is not None:
            self._report.ensureWidgetVisible(first_failure)
            QTimer.singleShot(
                0,
                lambda: first_failure.setFocus(Qt.FocusReason.OtherFocusReason),
            )

    def _clear_rows(self) -> None:
        while self._report_layout.count():
            item = self._report_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_row(self, item) -> QFrame:
        row = QFrame()
        result = "pass" if item.ok else ("fail" if item.required else "warn")
        row.setObjectName("ReadyCheckRow")
        row.setProperty("result", result)
        row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row.setAccessibleName(
            f"{'Passed' if item.ok else 'Required failure' if item.required else 'Optional warning'}: "
            f"{item.name}"
        )
        mark = QLabel("PASS" if item.ok else "FIX" if item.required else "OPTIONAL")
        mark.setObjectName("ReadyCheckMark")
        name = QLabel(item.name)
        name.setObjectName("ReadyCheckName")
        detail = QLabel(item.detail or "No additional details")
        detail.setObjectName("ReadyCheckDetail")
        detail.setWordWrap(True)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(name)
        text.addWidget(detail)
        row_layout = QHBoxLayout(row)
        row_layout.addWidget(mark)
        row_layout.addLayout(text, stretch=1)
        self._report_layout.addWidget(row)
        return row

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
