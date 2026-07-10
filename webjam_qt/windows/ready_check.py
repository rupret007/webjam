"""Non-blocking, rerunnable pre-session readiness report."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
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

        self._report = QTextEdit()
        self._report.setReadOnly(True)
        self._report.setAccessibleName("Ready Check results")
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
        self._report.setPlainText("Checking devices and recorder…")

        def _worker() -> None:
            from core.preflight import run_ready_check

            report = run_ready_check(self._settings_provider())
            self._report_ready.emit((scan_id, report))

        threading.Thread(target=_worker, daemon=True, name="ready-check").start()

    def _apply_report(self, payload: object) -> None:
        scan_id, report = payload
        if scan_id != self._scan_id:
            return
        self._report.setPlainText(report.to_text())
        self._rerun.setEnabled(True)
