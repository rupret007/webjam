"""Non-modal end-of-rehearsal recap built from SessionPulse."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.file_io import atomic_write_text
from core.rehearsal_recap import (
    RehearsalRecapSnapshot,
    format_recap_duration,
)
from core.session_intelligence import SessionAction, SessionPulse
from webjam_qt.theme.tokens import Space


class RehearsalRecapPanel(QFrame):
    """Post-session recap; stays in the conductor shell, never modal."""

    open_studio_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RehearsalRecap")
        self.setAccessibleName("Rehearsal recap")
        self._pulse: SessionPulse | None = None

        header = QLabel("REHEARSAL RECAP")
        header.setObjectName("RecapHeader")
        header.setTextFormat(Qt.TextFormat.PlainText)

        self._duration = QLabel()
        self._duration.setObjectName("RecapDuration")
        self._participants = QLabel()
        self._participants.setObjectName("RecapParticipants")
        self._take_status = QLabel()
        self._take_status.setObjectName("RecapTakeStatus")
        self._signals = QLabel()
        self._signals.setObjectName("RecapSignals")
        self._body = QLabel()
        self._body.setObjectName("RecapBody")

        for label in (
            self._duration,
            self._participants,
            self._take_status,
            self._signals,
            self._body,
        ):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)

        self._export_button = QPushButton("Export Recap…")
        self._export_button.setObjectName("GhostButton")
        self._export_button.setAccessibleName("Export Recap")
        self._export_button.clicked.connect(self._export_recap)

        self._open_studio_button = QPushButton("Open Studio")
        self._open_studio_button.setObjectName("PrimaryButton")
        self._open_studio_button.setAccessibleName("Open Studio")
        self._open_studio_button.clicked.connect(self._open_studio)

        self._dismiss_button = QPushButton("Done")
        self._dismiss_button.setObjectName("GhostButton")
        self._dismiss_button.setAccessibleName("Dismiss rehearsal recap")
        self._dismiss_button.clicked.connect(self._dismiss)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(Space.SM)
        actions.addWidget(self._export_button)
        actions.addWidget(self._open_studio_button)
        actions.addStretch(1)
        actions.addWidget(self._dismiss_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.XS)
        layout.addWidget(header)
        layout.addWidget(self._duration)
        layout.addWidget(self._participants)
        layout.addWidget(self._take_status)
        layout.addWidget(self._signals)
        layout.addWidget(self._body, 1)
        layout.addLayout(actions)

        self.hide()

    def apply_snapshot(self, snapshot: RehearsalRecapSnapshot) -> None:
        self._pulse = snapshot.pulse
        self._duration.setText(
            f"Session length: {format_recap_duration(snapshot.duration_seconds)}"
        )
        count = snapshot.pulse.participant_signal.count
        if count:
            self._participants.setText(f"Musicians in the room: {count}")
            self._participants.show()
        else:
            self._participants.hide()
        if snapshot.take_status:
            self._take_status.setText(f"Take: {snapshot.take_status}")
            self._take_status.show()
        else:
            self._take_status.clear()
            self._take_status.hide()
        self._signals.setText(snapshot.pulse.signal_line)
        self._body.setText(_format_pulse_items(snapshot.pulse))
        studio_ready = snapshot.take_status == "Ready"
        self._open_studio_button.setVisible(studio_ready)
        self._open_studio_button.setEnabled(studio_ready)
        self.show()

    def clear_recap(self) -> None:
        self._pulse = None
        self.hide()

    def _export_recap(self) -> None:
        pulse = self._pulse
        if pulse is None:
            return
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")
        default_name = f"webjam_recap_{date_str}.md"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Recap",
            default_name,
            "Markdown (*.md);;Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        text = pulse.to_markdown()
        try:
            atomic_write_text(path, text, mode=0o600)
        except OSError:
            QMessageBox.warning(
                self,
                "Export Failed",
                "WebJam couldn't export the recap. Choose another folder "
                "and try again.",
            )

    def _open_studio(self) -> None:
        self.open_studio_requested.emit()
        self.clear_recap()

    def _dismiss(self) -> None:
        self.clear_recap()
        self.dismissed.emit()


def _format_pulse_items(pulse: SessionPulse) -> str:
    sections: list[str] = []
    if pulse.decisions:
        sections.append(
            "Decisions\n" + "\n".join(f"· {item}" for item in pulse.decisions)
        )
    if pulse.actions:
        sections.append(
            "Actions\n"
            + "\n".join(
                f"· {_action_line(action)}" for action in pulse.actions
            )
        )
    if pulse.blockers:
        sections.append(
            "Blockers\n" + "\n".join(f"· {item}" for item in pulse.blockers)
        )
    if not sections:
        return "No captured decisions or actions yet."
    return "\n\n".join(sections)


def _action_line(action: SessionAction) -> str:
    if action.owner:
        return f"@{action.owner} {action.text}".strip()
    return action.text
