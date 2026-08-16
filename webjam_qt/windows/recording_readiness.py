"""Compact, accessible pre-record source-readiness sheet.

The dialog renders an immutable presentation snapshot.  It intentionally does
not start a recorder or inspect devices itself; the controller remains
responsible for comparing its private recording-plan generation before acting
on :attr:`start_requested`.
"""

from __future__ import annotations

from typing import Optional

from PySide6 import QtGui
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.recording_readiness_presentation import (
    RecordingReadinessPresentation,
    RecordingReadinessSource,
)
from webjam_qt.theme.tokens import Space


def _plain_label(text: str, object_name: str = "") -> QLabel:
    label = QLabel(str(text))
    if object_name:
        label.setObjectName(object_name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class _ReadinessSummaryCard(QFrame):
    """One path-free storage or Shared Track status card."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecordingReadinessSummaryCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        layout.setSpacing(2)
        self._title = _plain_label(title, "RecordingReadinessCardTitle")
        self._summary = _plain_label("Checking…", "RecordingReadinessCardSummary")
        self._summary.setWordWrap(True)
        self._summary.setMinimumWidth(0)
        self._detail = _plain_label("", "RecordingReadinessCardDetail")
        self._detail.setWordWrap(True)
        self._detail.setMinimumWidth(0)
        layout.addWidget(self._title)
        layout.addWidget(self._summary)
        layout.addWidget(self._detail)

    def set_status(
        self,
        *,
        state: str,
        state_label: str,
        summary: str,
        detail: str,
    ) -> None:
        self.setProperty("readinessState", str(state))
        self._summary.setText(f"{state_label} · {summary}")
        self._detail.setText(detail)
        self.setAccessibleName(f"{self._title.text()} readiness")
        self.setAccessibleDescription(
            f"{self._title.text()}: {state_label}. {summary}. {detail}"
        )
        style = self.style()
        style.unpolish(self)
        style.polish(self)


class RecordingReadinessSourceRow(QFrame):
    """One exact logical-source row with text that never relies on color."""

    def __init__(
        self,
        source: RecordingReadinessSource,
        parent: Optional[QWidget] = None,
    ) -> None:
        if not isinstance(source, RecordingReadinessSource):
            raise TypeError("source must be a RecordingReadinessSource")
        super().__init__(parent)
        self.source = source
        self.setObjectName("RecordingReadinessSourceRow")
        self.setProperty("readinessState", source.readiness.value)
        self.setAccessibleName(
            f"{source.participant_label}, {source.source_label} recording source"
        )
        self.setAccessibleDescription(source.accessible_description)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        outer.setSpacing(Space.XS)
        columns = QGridLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setHorizontalSpacing(Space.SM)
        columns.setVerticalSpacing(2)
        columns.setColumnStretch(0, 3)
        columns.setColumnStretch(1, 2)
        columns.setColumnStretch(2, 1)
        columns.setColumnStretch(3, 2)
        columns.setColumnStretch(4, 1)

        self.participant_label = _plain_label(
            source.participant_label,
            "RecordingReadinessParticipant",
        )
        self.participant_label.setMinimumWidth(0)
        self.source_label = _plain_label(
            source.source_label,
            "RecordingReadinessSourceLabel",
        )
        self.source_label.setMinimumWidth(0)
        self.source_label.setWordWrap(True)
        identity = QWidget()
        identity.setObjectName("RecordingReadinessIdentity")
        identity.setMinimumWidth(0)
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(1)
        identity_layout.addWidget(self.participant_label)
        identity_layout.addWidget(self.source_label)
        columns.addWidget(identity, 0, 0)

        self.kind_label = _plain_label(
            source.kind.label,
            "RecordingReadinessKind",
        )
        self.topology_label = _plain_label(
            source.topology.label,
            "RecordingReadinessTopology",
        )
        format_block = QWidget()
        format_block.setObjectName("RecordingReadinessFormat")
        format_layout = QVBoxLayout(format_block)
        format_layout.setContentsMargins(0, 0, 0, 0)
        format_layout.setSpacing(1)
        format_layout.addWidget(self.kind_label)
        format_layout.addWidget(self.topology_label)
        columns.addWidget(format_block, 0, 1)

        self.obligation_label = _plain_label(
            source.obligation_label,
            "RecordingReadinessObligation",
        )
        columns.addWidget(self.obligation_label, 0, 2)

        self.state_label = _plain_label(
            source.readiness.label,
            "RecordingReadinessState",
        )
        self.state_label.setProperty("readinessState", source.readiness.value)
        columns.addWidget(self.state_label, 0, 3)

        self.meter = QProgressBar()
        self.meter.setObjectName("RecordingReadinessMeter")
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(True)
        self.meter.setMaximumWidth(92)
        if source.meter_percent is None:
            self.meter.setValue(0)
            self.meter.setFormat("No meter")
            self.meter.setAccessibleName(f"{source.source_label} input meter")
            self.meter.setAccessibleDescription("No live meter value is available.")
        else:
            self.meter.setValue(source.meter_percent)
            self.meter.setFormat(f"{source.meter_percent}%")
            self.meter.setAccessibleName(f"{source.source_label} input meter")
            self.meter.setAccessibleDescription(
                f"Current bounded input level: {source.meter_percent} percent."
            )
        columns.addWidget(self.meter, 0, 4)
        outer.addLayout(columns)

        self.detail_label = _plain_label(
            source.detail,
            "RecordingReadinessSourceDetail",
        )
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumWidth(0)
        outer.addWidget(self.detail_label)


class RecordingReadinessDialog(QDialog):
    """Display one immutable snapshot and fail closed when anything blocks it."""

    start_requested = Signal(object)

    def __init__(
        self,
        presentation: RecordingReadinessPresentation,
        parent: Optional[QWidget] = None,
    ) -> None:
        if not isinstance(presentation, RecordingReadinessPresentation):
            raise TypeError("presentation must be a RecordingReadinessPresentation")
        super().__init__(parent)
        self._presentation = presentation
        self._source_rows: tuple[RecordingReadinessSourceRow, ...] = ()
        self._last_announcement = ""
        self.setObjectName("RecordingReadinessDialog")
        self.setWindowTitle("WebJam — Record Session Readiness")
        self.setAccessibleName("Record Session source readiness")
        self.setModal(True)
        self.setMinimumSize(520, 420)
        self.resize(680, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.MD)
        root.setSpacing(Space.SM)

        self._eyebrow = _plain_label("RECORD SESSION", "RecordingReadinessEyebrow")
        self._title = _plain_label(
            "Check every source before recording",
            "RecordingReadinessTitle",
        )
        self._summary = _plain_label("", "RecordingReadinessSummary")
        self._summary.setWordWrap(True)
        self._summary.setMinimumWidth(0)
        self._summary.setAccessibleName("Recording readiness summary")
        root.addWidget(self._eyebrow)
        root.addWidget(self._title)
        root.addWidget(self._summary)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("RecordingReadinessScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setAccessibleName("Recording source readiness details")
        body = QWidget()
        self._body = body
        body_layout = QVBoxLayout(body)
        self._body_layout = body_layout
        body_layout.setContentsMargins(0, 0, Space.XS, 0)
        body_layout.setSpacing(Space.SM)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(Space.SM)
        self._storage_card = _ReadinessSummaryCard("Recording storage")
        self._shared_track_card = _ReadinessSummaryCard("Shared Track")
        status_row.addWidget(self._storage_card, 1)
        status_row.addWidget(self._shared_track_card, 1)
        body_layout.addLayout(status_row)

        self._blockers = QFrame()
        self._blockers.setObjectName("RecordingReadinessBlockers")
        blockers_layout = QVBoxLayout(self._blockers)
        blockers_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        blockers_layout.setSpacing(2)
        self._blockers_title = _plain_label(
            "FIX BEFORE RECORDING",
            "RecordingReadinessBlockerTitle",
        )
        self._blockers_text = _plain_label(
            "",
            "RecordingReadinessBlockerText",
        )
        self._blockers_text.setWordWrap(True)
        self._blockers_text.setMinimumWidth(0)
        self._blockers_text.setAccessibleName("Recording readiness blockers")
        blockers_layout.addWidget(self._blockers_title)
        blockers_layout.addWidget(self._blockers_text)
        body_layout.addWidget(self._blockers)

        source_heading = QHBoxLayout()
        self._source_title = _plain_label(
            "SOURCES",
            "RecordingReadinessSectionTitle",
        )
        self._source_count = _plain_label("", "RecordingReadinessSourceCount")
        source_heading.addWidget(self._source_title)
        source_heading.addStretch(1)
        source_heading.addWidget(self._source_count)
        body_layout.addLayout(source_heading)

        self._source_container = QWidget()
        self._source_container.setObjectName("RecordingReadinessSourceList")
        self._source_container.setAccessibleName("Exact recording sources")
        self._source_layout = QVBoxLayout(self._source_container)
        self._source_layout.setContentsMargins(0, 0, 0, 0)
        self._source_layout.setSpacing(Space.XS)
        body_layout.addWidget(self._source_container)
        body_layout.addStretch(1)
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, Space.XS, 0, 0)
        footer.addStretch(1)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setObjectName("GhostButton")
        self._cancel_button.setAccessibleName("Cancel recording start")
        self._cancel_button.clicked.connect(self.reject)
        self._start_button = QPushButton("Start Recording")
        self._start_button.setObjectName("StudioRecordButton")
        self._start_button.setAccessibleName("Start Recording")
        self._start_button.clicked.connect(self._request_start)
        footer.addWidget(self._cancel_button)
        footer.addWidget(self._start_button)
        root.addLayout(footer)

        QWidget.setTabOrder(self._scroll, self._cancel_button)
        QWidget.setTabOrder(self._cancel_button, self._start_button)
        self.set_presentation(presentation, announce=False)

    @property
    def presentation(self) -> RecordingReadinessPresentation:
        return self._presentation

    @property
    def source_rows(self) -> tuple[RecordingReadinessSourceRow, ...]:
        return self._source_rows

    def set_presentation(
        self,
        presentation: RecordingReadinessPresentation,
        *,
        announce: bool = True,
    ) -> None:
        """Replace the whole snapshot; partial row mutation is not supported."""

        if not isinstance(presentation, RecordingReadinessPresentation):
            raise TypeError("presentation must be a RecordingReadinessPresentation")
        self._presentation = presentation
        while self._source_layout.count():
            item = self._source_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        rows = tuple(
            RecordingReadinessSourceRow(source) for source in presentation.sources
        )
        for row in rows:
            self._source_layout.addWidget(row)
        self._source_rows = rows
        self._source_count.setText(
            f"{presentation.ready_source_count}/{len(presentation.sources)} ready"
        )
        self._storage_card.set_status(
            state=presentation.storage.readiness.value,
            state_label=presentation.storage.readiness.label,
            summary=presentation.storage.summary,
            detail=presentation.storage.detail,
        )
        self._shared_track_card.set_status(
            state=presentation.shared_track.readiness.value,
            state_label=presentation.shared_track.readiness.label,
            summary=presentation.shared_track.summary,
            detail=presentation.shared_track.detail,
        )
        blockers = presentation.effective_blockers
        self._blockers.setVisible(bool(blockers))
        self._blockers_text.setText("\n".join(f"• {blocker}" for blocker in blockers))
        self._blockers.setAccessibleDescription(
            " ".join(blockers) if blockers else "No recording blockers."
        )
        self._summary.setText(
            (
                f"{presentation.profile_label} · {presentation.ready_source_count} "
                f"of {len(presentation.sources)} exact sources ready · "
                "Start Recording is available."
                if presentation.can_start
                else (
                    f"{presentation.profile_label} · {presentation.ready_source_count} "
                    f"of {len(presentation.sources)} exact sources ready · "
                    f"{len(blockers)} item(s) need attention."
                )
            )
        )
        self._summary.setAccessibleDescription(presentation.accessible_description)
        self.setAccessibleDescription(presentation.accessible_description)
        self._start_button.setEnabled(presentation.can_start)
        self._start_button.setDefault(presentation.can_start)
        self._start_button.setAccessibleDescription(
            (
                "Start this exact Record Session snapshot. WebJam will recheck "
                "private recorder authority before arming."
                if presentation.can_start
                else (
                    f"Unavailable until {len(blockers)} recording readiness "
                    "item(s) are resolved."
                )
            )
        )
        if announce and self.isVisible():
            self._announce(presentation.accessible_description)

    def _announce(self, message: str) -> None:
        if message == self._last_announcement:
            return
        self._last_announcement = message
        event_type = getattr(QtGui, "QAccessibleAnnouncementEvent", None)
        try:
            if event_type is not None:
                QAccessible.updateAccessibility(event_type(self, message))
            else:
                QAccessible.updateAccessibility(
                    QAccessibleEvent(
                        self,
                        QAccessible.Event.DescriptionChanged,
                    )
                )
        except (RuntimeError, TypeError):
            pass

    def _request_start(self) -> None:
        if not self._presentation.can_start:
            return
        snapshot = self._presentation
        self.start_requested.emit(snapshot)
        super().accept()

    def accept(self) -> None:
        """Fail closed if Enter or an integration tries to bypass the button."""

        self._request_start()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._presentation.can_start:
            self._start_button.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._announce(self._presentation.accessible_description)


__all__ = [
    "RecordingReadinessDialog",
    "RecordingReadinessSourceRow",
]
