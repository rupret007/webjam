"""Musician-facing Jamulus component update status and controls.

The dialog renders an immutable snapshot supplied by the runtime coordinator.
It never performs network, filesystem, installer, or process work itself.
That separation keeps modal Qt event handling from becoming an updater
lifecycle owner and makes every displayed claim directly testable.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space


class JamulusLicenseDialog(QDialog):
    """Explicit macOS SLA review before WebJam asks ``hdiutil`` to agree."""

    def __init__(
        self,
        license_text: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(license_text, str) or not license_text.strip():
            raise ValueError("Jamulus license text is unavailable")
        self.setObjectName("JamulusLicenseDialog")
        self.setWindowTitle("Review the Jamulus license")
        self.setModal(True)
        self.resize(700, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        root.setSpacing(Space.MD)

        heading = QLabel("Before installing the official Jamulus update")
        heading.setObjectName("DialogTitle")
        root.addWidget(heading)

        summary = QLabel(
            "Jamulus’s disk image requires you to accept its open-source "
            "license. WebJam will not accept it silently. Review the terms "
            "below, then choose Agree to install the verified update."
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        text = QPlainTextEdit()
        text.setObjectName("JamulusLicenseText")
        text.setAccessibleName("Jamulus license terms")
        text.setReadOnly(True)
        text.setPlainText(license_text)
        root.addWidget(text, 1)

        buttons = QDialogButtonBox()
        disagree = buttons.addButton(
            "Not now", QDialogButtonBox.ButtonRole.RejectRole
        )
        agree = buttons.addButton(
            "Agree and install", QDialogButtonBox.ButtonRole.AcceptRole
        )
        disagree.setAccessibleName("Do not install the Jamulus update")
        agree.setAccessibleName("Agree to the Jamulus license and install")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class JamulusUpdateDialog(QDialog):
    """Render updater truth and emit semantic user intents."""

    check_requested = Signal()
    download_requested = Signal()
    activate_requested = Signal()
    approve_requested = Signal()
    rollback_requested = Signal()
    cancel_requested = Signal()

    def __init__(
        self,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("JamulusUpdateDialog")
        self.setWindowTitle("Jamulus Updates")
        self.setModal(False)
        self.setMinimumWidth(520)
        # Qt otherwise caps an un-sized dialog to roughly two thirds of a
        # compact screen on first show. That can squeeze the reachable Windows
        # READY + recovery state until three distinct action labels are clipped.
        # An explicit 640×400 client area still fits the supported 760×600
        # screen, including conservative native-frame allowance, while leaving
        # enough vertical room for the separate recovery-action row under the
        # largest supported application font and production stylesheet.
        self.resize(640, 400)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        root.setSpacing(Space.MD)

        heading = QLabel("Jamulus component")
        heading.setObjectName("DialogTitle")
        root.addWidget(heading)

        explanation = QLabel(
            "WebJam checks only a signed list of Jamulus versions that have "
            "passed its audio, routing, and recording compatibility tests. "
            "It never installs an unreviewed “latest” release. If Jamulus "
            "shows its own red upgrade link first, return here—WebJam may "
            "intentionally wait until that version passes compatibility tests."
        )
        explanation.setWordWrap(True)
        explanation.setAccessibleName("Jamulus update safety explanation")
        root.addWidget(explanation)

        self._status = QLabel("Checking has not started.")
        self._status.setObjectName("StatusBanner")
        self._status.setWordWrap(True)
        self._status.setAccessibleName("Jamulus update status")
        root.addWidget(self._status)

        self._versions = QLabel("")
        self._versions.setWordWrap(True)
        self._versions.setAccessibleName("Jamulus component versions")
        root.addWidget(self._versions)

        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setAccessibleName("Jamulus update details")
        root.addWidget(self._detail)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setAccessibleName("Jamulus update download progress")
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        platform_note = QLabel(
            "Updates never interrupt a live session. macOS keeps and verifies "
            "the upstream Developer ID signature and notarization as source "
            "evidence; only a separately verified WebJam-integrated Mac "
            "runtime may run. Windows and Linux require an explicit "
            "operating-system installation approval; WebJam never uses hidden "
            "elevation."
        )
        platform_note.setWordWrap(True)
        platform_note.setObjectName("SecondaryText")
        platform_note.setAccessibleName("Jamulus platform update behavior")
        root.addWidget(platform_note)

        action_rows = QVBoxLayout()
        action_rows.setSpacing(Space.SM)
        primary_actions = QHBoxLayout()
        primary_actions.setSpacing(Space.SM)

        self._check = QPushButton("Check now")
        self._check.setAccessibleName("Check for approved Jamulus updates")
        self._check.clicked.connect(self.check_requested.emit)
        primary_actions.addWidget(self._check)

        self._download = QPushButton("Download")
        self._download.setAccessibleName("Download approved Jamulus update")
        self._download.clicked.connect(self.download_requested.emit)
        self._download.setVisible(False)
        primary_actions.addWidget(self._download)

        self._activate = QPushButton("Restart when idle")
        self._activate.setAccessibleName(
            "Use the ready Jamulus update when the session is idle"
        )
        self._activate.clicked.connect(self.activate_requested.emit)
        self._activate.setVisible(False)
        primary_actions.addWidget(self._activate)

        self._approve = QPushButton("Open installer")
        self._approve.setAccessibleName(
            "Open the verified operating-system Jamulus installer"
        )
        self._approve.clicked.connect(self.approve_requested.emit)
        self._approve.setVisible(False)
        primary_actions.addWidget(self._approve)

        self._cancel = QPushButton("Cancel download")
        self._cancel.setAccessibleName("Cancel Jamulus update download")
        self._cancel.clicked.connect(self.cancel_requested.emit)
        self._cancel.setVisible(False)
        primary_actions.addWidget(self._cancel)
        primary_actions.addStretch(1)
        action_rows.addLayout(primary_actions)

        recovery_actions = QHBoxLayout()
        recovery_actions.setSpacing(Space.SM)
        self._rollback = QPushButton("Use previous version")
        self._rollback.setAccessibleName("Roll back to the previous Jamulus component")
        self._rollback.clicked.connect(self.rollback_requested.emit)
        self._rollback.setVisible(False)
        recovery_actions.addWidget(self._rollback)
        recovery_actions.addStretch(1)
        self._later = QPushButton("Later")
        self._later.setObjectName("GhostButton")
        self._later.setAccessibleName("Close Jamulus Updates and decide later")
        self._later.clicked.connect(self.close)
        recovery_actions.addWidget(self._later)
        action_rows.addLayout(recovery_actions)
        root.addLayout(action_rows)

    @staticmethod
    def _state_value(snapshot: object) -> str:
        state = getattr(snapshot, "state", "")
        return str(getattr(state, "value", state) or "").strip().lower()

    def set_snapshot(self, snapshot: object) -> None:
        """Render one coordinator-owned immutable snapshot.

        The adapter deliberately tolerates absent optional fields so a failed
        updater initialization can still be shown truthfully rather than
        crashing the Settings surface.
        """

        state = self._state_value(snapshot)
        status = str(
            getattr(snapshot, "title", "")
            or getattr(snapshot, "status", "")
            or getattr(snapshot, "message", "")
            or "Jamulus update status is unavailable."
        )
        detail = str(getattr(snapshot, "detail", "") or "")
        active = str(getattr(snapshot, "active_version", "") or "")
        available = str(getattr(snapshot, "available_version", "") or "")
        previous = str(getattr(snapshot, "previous_version", "") or "")
        reason = str(getattr(snapshot, "reason_code", "") or "").strip()

        self._status.setText(status)
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))

        versions: list[str] = []
        if active:
            versions.append(f"Active: Jamulus {active}")
        if available and available != active:
            if reason == "macos-integrated-runtime-required":
                versions.append(
                    "Unavailable for WebJam integration: "
                    f"Jamulus {available}"
                )
            else:
                versions.append(f"Approved update: Jamulus {available}")
        if previous and previous != active:
            versions.append(f"Recovery version: Jamulus {previous}")
        self._versions.setText(" · ".join(versions))
        self._versions.setVisible(bool(versions))

        progress_value = getattr(snapshot, "progress_percent", None)
        downloading = state in {"downloading", "verifying", "staging"}
        if isinstance(progress_value, (int, float)) and not isinstance(
            progress_value, bool
        ):
            self._progress.setValue(max(0, min(100, round(progress_value))))
        else:
            self._progress.setValue(0)
        self._progress.setVisible(downloading)

        busy = state in {"checking", "downloading", "verifying", "staging"}
        self._check.setEnabled(not busy)
        self._download.setVisible(bool(getattr(snapshot, "can_download", False)))
        self._download.setEnabled(not busy)
        self._activate.setVisible(bool(getattr(snapshot, "can_activate", False)))
        activate_label = str(
            getattr(snapshot, "activate_label", "") or "Restart when idle"
        ).strip()
        self._activate.setText(activate_label)
        self._activate.setAccessibleName(
            "Verify the operating-system Jamulus installation"
            if activate_label == "Verify installation"
            else "Continue the Jamulus update when every session is idle"
        )
        self._activate.setEnabled(not busy)
        self._approve.setVisible(bool(getattr(snapshot, "can_approve", False)))
        approve_label = str(
            getattr(snapshot, "approve_label", "") or "Open installer"
        ).strip()
        self._approve.setText(approve_label)
        self._approve.setEnabled(not busy)
        self._rollback.setVisible(bool(getattr(snapshot, "can_rollback", False)))
        self._rollback.setEnabled(not busy)
        self._cancel.setVisible(state == "downloading")


__all__ = ["JamulusLicenseDialog", "JamulusUpdateDialog"]
