"""Non-blocking, rerunnable pre-session readiness report."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
        self._items: list[object] = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Automated checks cover WebJam configuration and devices. "
            "WebJam cannot inspect native Webex device or mute selections; "
            "VERIFY rows are your manual confirmations. Practice remains the "
            "final proof of real input, headphones, and Jamulus routing."
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
        # A resizable QScrollArea otherwise compresses every result row to
        # fit its viewport, clipping wrapped instructions instead of adding a
        # scrollbar.  Let the content keep its minimum height and scroll.
        self._report_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._report.setWidget(self._report_content)
        layout.addWidget(self._report, stretch=1)

        actions = QHBoxLayout()
        self._rerun = QPushButton("Run Again")
        self._rerun.clicked.connect(self.run_checks)
        actions.addWidget(self._rerun)
        settings = QPushButton("Open Settings")
        settings.clicked.connect(self.settings_requested.emit)
        actions.addWidget(settings)
        practice = QPushButton("Practice Solo")
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
        for item in self._items:
            if getattr(item, "manual_verification", False):
                item.ok = False
        self._rerun.setEnabled(False)
        self._summary.setText("Checking devices and recorder…")
        self._summary.setProperty("result", "checking")
        self._clear_rows()
        self._items = []

        def _worker() -> None:
            from core.preflight import run_ready_check

            report = run_ready_check(self._settings_provider())
            self._report_ready.emit((scan_id, report))

        threading.Thread(target=_worker, daemon=True, name="ready-check").start()

    def _apply_report(self, payload: object) -> None:
        scan_id, report = payload
        if scan_id != self._scan_id:
            return
        self._clear_rows()
        raw_items = getattr(report, "items", [])
        items = list(raw_items) if isinstance(raw_items, (list, tuple)) else []
        self._items = items
        self._update_summary()

        first_failure = None
        if items:
            for item in items:
                row = self._add_row(item)
                if (
                    first_failure is None
                    and item.required
                    and not item.ok
                    and not getattr(item, "manual_verification", False)
                ):
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
            first_failure.setFocus(Qt.FocusReason.OtherFocusReason)
            QTimer.singleShot(
                0,
                lambda: first_failure.setFocus(Qt.FocusReason.OtherFocusReason),
            )

    def _clear_rows(self) -> None:
        while self._report_layout.count():
            item = self._report_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Detach immediately so a replacement report cannot still
                # discover stale rows while deleteLater() is pending.
                widget.setParent(None)
                widget.deleteLater()

    def _update_summary(self) -> None:
        automatic_failures = [
            item for item in self._items
            if item.required and not item.ok
            and not getattr(item, "manual_verification", False)
        ]
        pending_manual = [
            item for item in self._items
            if item.required and not item.ok
            and getattr(item, "manual_verification", False)
        ]
        optional_warnings = [
            item for item in self._items if not item.required and not item.ok
        ]
        if automatic_failures:
            count = len(automatic_failures)
            summary = f"Fix {count} required item{'s' if count != 1 else ''} before the jam."
            result = "fail"
        elif pending_manual:
            count = len(pending_manual)
            summary = (
                "Automated checks passed; confirm "
                f"{count} Webex setting{'s' if count != 1 else ''}."
            )
            result = "checking"
        else:
            summary = "Ready to play — all required checks passed."
            result = "pass"
        if optional_warnings:
            summary += (
                f" {len(optional_warnings)} optional warning"
                f"{'s' if len(optional_warnings) != 1 else ''}."
            )
        self._summary.setText(summary)
        self._summary.setProperty("result", result)
        self._repolish(self._summary)

    def _add_row(self, item) -> QFrame:
        row = QFrame()
        is_manual = bool(getattr(item, "manual_verification", False))
        result = (
            "pass"
            if item.ok
            else "verify"
            if is_manual
            else "fail"
            if item.required
            else "warn"
        )
        row.setObjectName("ReadyCheckRow")
        row.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        row.setProperty("result", result)
        row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row.setAccessibleName(
            f"{'Passed' if item.ok else 'Manual verification' if is_manual else 'Required failure' if item.required else 'Optional warning'}: "
            f"{item.name}"
        )
        if is_manual:
            mark = QCheckBox("VERIFY")
            mark.setChecked(bool(item.ok))
            mark.setAccessibleName(f"Verify: {item.name}")

            def _verified(checked: bool) -> None:
                item.ok = checked
                row.setProperty("result", "pass" if checked else "verify")
                row.setAccessibleName(
                    f"{'Passed' if checked else 'Manual verification'}: {item.name}"
                )
                self._repolish(row)
                self._update_summary()

            mark.toggled.connect(_verified)
        else:
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

    def closeEvent(self, event) -> None:  # noqa: N802
        self._scan_id += 1
        for item in self._items:
            if getattr(item, "manual_verification", False):
                item.ok = False
        self._items = []
        super().closeEvent(event)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
