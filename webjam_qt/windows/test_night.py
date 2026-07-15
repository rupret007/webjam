"""Operator-only Test Night surface.

This dialog deliberately has no persistence or diagnostic authority.  It
shows the fixed pilot checklist and emits an operator's intent; the
ApplicationController is responsible for deciding whether an action is
allowed and for writing any private pilot evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


@dataclass(frozen=True)
class TestNightChecklistItem:
    """A stable, non-sensitive operator checklist entry."""

    key: str
    label: str


# These labels are deliberately fixed.  They guide the operator without
# accepting arbitrary notes, personal names, paths, invite links, or audio.
TEST_NIGHT_CHECKLIST: tuple[TestNightChecklistItem, ...] = (
    TestNightChecklistItem("session_start", "Start or join the session"),
    TestNightChecklistItem("connection_truth", "Confirm connection truth"),
    TestNightChecklistItem("hear_each_other", "Both musicians hear each other"),
    TestNightChecklistItem("headphones_correct", "Headphones and talkback are correct"),
    TestNightChecklistItem("session_playable", "Play together without interruption"),
    TestNightChecklistItem("record_take", "Record one band take"),
    TestNightChecklistItem("validate_take", "Validate the recorded take"),
    TestNightChecklistItem("studio_playback", "Review playback in Studio"),
    TestNightChecklistItem("studio_alignment", "Review track alignment in Studio"),
    TestNightChecklistItem("failure_recovery", "Exercise interruption and recovery"),
    TestNightChecklistItem("rehearsal_moment_useful", "Assess one rehearsal moment"),
    TestNightChecklistItem("closeout", "End the session and confirm cleanup"),
)

MANUAL_CHECK_KEYS: tuple[str, ...] = (
    "hear_each_other",
    "headphones_correct",
    "session_playable",
    "studio_playback",
    "studio_alignment",
    "rehearsal_moment_useful",
)

_CHECK_LABELS = {item.key: item.label for item in TEST_NIGHT_CHECKLIST}
_STATUS_LABELS = {
    "waiting": "Waiting",
    "not_run": "Not run",
    "verified": "Verified",
    "failed": "Failed",
    "blocked": "Blocked",
    "indeterminate": "Indeterminate",
    "not_available": "Not available",
}
_RUN_STATUS_LABELS = {
    "not_started": "Not started",
    "running": "In progress",
    "paused": "Paused",
    "abandoned": "Abandoned",
    "completed": "Complete",
}


class TestNightDialog(QDialog):
    """A small, controller-driven checklist for a closed-pilot operator.

    The dialog only emits intents.  In particular, choosing an outcome does
    not update a row locally: the controller must validate and persist it,
    then call :meth:`set_check_statuses` with the resulting presentation.
    """

    start_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    abandon_requested = Signal()
    restart_requested = Signal()
    manual_outcome_requested = Signal(str, str)  # checklist key, outcome key
    export_report_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TestNightDialog")
        self.setWindowTitle("WebJam — Test Night")
        self.setModal(False)
        self.resize(660, 700)
        self.setMinimumSize(540, 540)
        self._run_state = "not_started"
        self._export_available = False
        self._check_status_labels: dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        eyebrow = QLabel("OPERATOR MODE")
        eyebrow.setObjectName("TestNightEyebrow")
        root.addWidget(eyebrow)

        title = QLabel("Test Night")
        title.setObjectName("TestNightTitle")
        root.addWidget(title)

        note = QLabel(
            "Use this guided checklist only during a closed pilot. WebJam does "
            "not verify hearing, headphones, an external editor, or a second Mac by itself."
        )
        note.setObjectName("TestNightNote")
        note.setWordWrap(True)
        note.setAccessibleName("Test Night evidence limitation")
        root.addWidget(note)

        status_row = QHBoxLayout()
        status_caption = QLabel("Pilot status")
        status_caption.setObjectName("TestNightSectionTitle")
        self._run_status = QLabel(_RUN_STATUS_LABELS[self._run_state])
        self._run_status.setObjectName("TestNightRunStatus")
        self._run_status.setAccessibleName("Test Night pilot status")
        status_row.addWidget(status_caption)
        status_row.addStretch(1)
        status_row.addWidget(self._run_status)
        root.addLayout(status_row)

        self._detail = QLabel("Start when the operator is ready to begin.")
        self._detail.setObjectName("TestNightDetail")
        self._detail.setWordWrap(True)
        self._detail.setAccessibleName("Test Night status detail")
        root.addWidget(self._detail)

        checklist_title = QLabel("Guided checklist")
        checklist_title.setObjectName("TestNightSectionTitle")
        root.addWidget(checklist_title)

        checklist_scroll = QScrollArea()
        checklist_scroll.setObjectName("TestNightChecklist")
        checklist_scroll.setWidgetResizable(True)
        checklist_scroll.setFrameShape(QFrame.Shape.NoFrame)
        checklist_content = QWidget()
        checklist_layout = QGridLayout(checklist_content)
        checklist_layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        checklist_layout.setHorizontalSpacing(Space.MD)
        checklist_layout.setVerticalSpacing(Space.SM)
        for row, item in enumerate(TEST_NIGHT_CHECKLIST):
            label = QLabel(item.label)
            label.setObjectName("TestNightCheck")
            label.setWordWrap(True)
            label.setAccessibleName(f"Test Night check: {item.label}")
            value = QLabel(_STATUS_LABELS["waiting"])
            value.setObjectName("TestNightCheckStatus")
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            value.setAccessibleName(f"Status for {item.label}")
            self._check_status_labels[item.key] = value
            checklist_layout.addWidget(label, row, 0)
            checklist_layout.addWidget(value, row, 1)
        checklist_layout.setColumnStretch(0, 1)
        checklist_scroll.setWidget(checklist_content)
        root.addWidget(checklist_scroll, stretch=1)

        manual = QFrame()
        manual.setObjectName("TestNightManualOutcome")
        manual_layout = QVBoxLayout(manual)
        manual_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        manual_layout.setSpacing(Space.SM)
        manual_title = QLabel("Record an operator observation")
        manual_title.setObjectName("TestNightSectionTitle")
        manual_layout.addWidget(manual_title)
        manual_note = QLabel(
            "Choose only what you directly observed. This sends a request to "
            "the controller; it does not create evidence on its own."
        )
        manual_note.setObjectName("TestNightHint")
        manual_note.setWordWrap(True)
        manual_layout.addWidget(manual_note)
        manual_row = QHBoxLayout()
        self._manual_check = QComboBox()
        self._manual_check.setObjectName("TestNightManualCheck")
        self._manual_check.setAccessibleName("Observed Test Night check")
        for key in MANUAL_CHECK_KEYS:
            self._manual_check.addItem(_CHECK_LABELS[key], key)
        self._manual_outcome = QComboBox()
        self._manual_outcome.setObjectName("TestNightManualOutcomePicker")
        self._manual_outcome.setAccessibleName("Observed Test Night outcome")
        for key in ("verified", "failed", "blocked", "not_run", "indeterminate"):
            self._manual_outcome.addItem(_STATUS_LABELS[key], key)
        self._record_manual = QPushButton("Record outcome")
        self._record_manual.setObjectName("GhostButton")
        self._record_manual.setAccessibleName("Record selected Test Night outcome")
        self._record_manual.clicked.connect(self._emit_manual_outcome)
        manual_row.addWidget(self._manual_check, stretch=1)
        manual_row.addWidget(self._manual_outcome)
        manual_row.addWidget(self._record_manual)
        manual_layout.addLayout(manual_row)
        root.addWidget(manual)

        controls = QHBoxLayout()
        self._start = self._button("Start Test Night", "PrimaryButton", self.start_requested)
        self._pause = self._button("Pause", "GhostButton", self.pause_requested)
        self._resume = self._button("Resume", "PrimaryButton", self.resume_requested)
        self._abandon = self._button("Abandon", "GhostButton", self.abandon_requested)
        self._restart = self._button("Restart", "GhostButton", self.restart_requested)
        self._export = self._button("Export report", "GhostButton", self.export_report_requested)
        controls.addWidget(self._start)
        controls.addWidget(self._pause)
        controls.addWidget(self._resume)
        controls.addWidget(self._abandon)
        controls.addWidget(self._restart)
        controls.addStretch(1)
        controls.addWidget(self._export)
        root.addLayout(controls)

        self.set_run_state("not_started")

    def _button(self, label: str, object_name: str, signal: Signal) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName(object_name)
        button.setAccessibleName(label)
        button.clicked.connect(signal.emit)
        return button

    @property
    def run_state(self) -> str:
        """The controller-supplied presentation state, never inferred here."""

        return self._run_state

    def set_run_state(self, state: str, detail: str = "") -> None:
        """Render an authoritative pilot state supplied by the controller."""

        normalized = str(state or "not_started").strip().lower()
        normalized = {"idle": "not_started", "complete": "completed"}.get(
            normalized, normalized
        )
        if normalized not in _RUN_STATUS_LABELS:
            normalized = "not_started"
        self._run_state = normalized
        self._run_status.setText(_RUN_STATUS_LABELS[normalized])
        self._run_status.setAccessibleDescription(
            f"Test Night status: {_RUN_STATUS_LABELS[normalized]}."
        )
        self._detail.setText(
            str(detail).strip()
            or {
                "not_started": "Start when the operator is ready to begin.",
                "running": "Follow the checklist and record only direct observations.",
                "paused": "The pilot is paused. Resume, abandon, or restart it.",
                "abandoned": "This pilot was abandoned. Restart creates a new run.",
                "completed": "The pilot is complete. Export the report when ready.",
            }[normalized]
        )
        self._start.setEnabled(normalized == "not_started")
        self._pause.setEnabled(normalized == "running")
        self._resume.setEnabled(normalized == "paused")
        self._abandon.setEnabled(normalized in {"running", "paused"})
        self._restart.setEnabled(normalized in {"abandoned", "completed"})
        self._record_manual.setEnabled(normalized == "running")
        self._manual_check.setEnabled(normalized == "running")
        self._manual_outcome.setEnabled(normalized == "running")
        self._export.setEnabled(
            self._export_available and normalized in {"paused", "abandoned", "completed"}
        )

    def set_export_available(self, available: bool) -> None:
        """Let the controller expose export only when a report exists."""

        self._export_available = bool(available)
        self._export.setEnabled(
            self._export_available
            and self._run_state in {"paused", "abandoned", "completed"}
        )

    def set_check_statuses(self, statuses: Mapping[str, str]) -> None:
        """Render controller-provided fixed outcomes without mutating evidence."""

        for key, value in statuses.items():
            label = self._check_status_labels.get(str(key))
            if label is None:
                continue
            normalized = str(value or "waiting").strip().lower()
            label.setText(_STATUS_LABELS.get(normalized, _STATUS_LABELS["waiting"]))

    def set_check_status(self, key: str, status: str) -> None:
        """Convenience form for one authoritative checklist presentation update."""

        self.set_check_statuses({str(key): str(status)})

    def _emit_manual_outcome(self) -> None:
        self.manual_outcome_requested.emit(
            str(self._manual_check.currentData() or ""),
            str(self._manual_outcome.currentData() or ""),
        )
