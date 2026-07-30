from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton

from core.jamulus_update_state import JamulusUpdateSnapshot, JamulusUpdateState
from services.jamulus_component_update import JamulusUpdatePresentation
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.jamulus_update import (
    JamulusLicenseDialog,
    JamulusUpdateDialog,
)


APP = QApplication.instance() or QApplication([])


def _presentation(
    state: JamulusUpdateState,
    *,
    reason: str = "",
    restart_when_idle: bool = False,
    download: bool = False,
    activate: bool = False,
    approve: bool = False,
    rollback: bool = False,
    approve_label: str = "Open installer",
    activate_label: str = "Restart when idle",
) -> JamulusUpdatePresentation:
    return JamulusUpdatePresentation(
        snapshot=JamulusUpdateSnapshot(
            state=state,
            active_version="3.12.2",
            available_version="3.12.3",
            target="windows-x64",
            progress_percent=43,
            reason_code=reason,
            message="Bounded musician-facing status.",
            checked_at_utc="2026-07-28T19:00:00Z",
            restart_when_idle=restart_when_idle,
        ),
        previous_version="3.12.1" if rollback else "",
        can_download=download,
        can_activate=activate,
        can_approve=approve,
        can_rollback=rollback,
        approve_label=approve_label,
        activate_label=activate_label,
        detail="Nothing private appears here.",
    )


def test_update_dialog_exposes_only_actions_valid_for_each_state():
    dialog = JamulusUpdateDialog()
    visible_copy = " ".join(
        label.text() for label in dialog.findChildren(QLabel)
    )
    assert "Jamulus shows its own red upgrade link" in visible_copy
    dialog.set_snapshot(
        _presentation(JamulusUpdateState.AVAILABLE, download=True)
    )
    assert dialog._download.isVisibleTo(dialog)
    assert not dialog._approve.isVisibleTo(dialog)
    assert "Active: Jamulus 3.12.2" in dialog._versions.text()
    assert "Approved update: Jamulus 3.12.3" in dialog._versions.text()

    dialog.set_snapshot(
        _presentation(
            JamulusUpdateState.READY,
            approve=True,
            rollback=True,
            approve_label="Review license and install",
        )
    )
    assert not dialog._download.isVisibleTo(dialog)
    assert dialog._approve.isVisibleTo(dialog)
    assert dialog._approve.text() == "Review license and install"
    assert dialog._rollback.isVisibleTo(dialog)

    dialog.set_snapshot(
        _presentation(
            JamulusUpdateState.DEFERRED,
            reason="recording-active",
            restart_when_idle=True,
            activate=True,
        )
    )
    assert dialog._activate.isVisibleTo(dialog)
    assert not dialog._approve.isVisibleTo(dialog)

    dialog.set_snapshot(
        _presentation(
            JamulusUpdateState.DEFERRED,
            reason="finish-platform-installer",
            activate=True,
            activate_label="Verify installation",
        )
    )
    assert dialog._activate.isVisibleTo(dialog)
    assert dialog._activate.text() == "Verify installation"
    assert (
        dialog._activate.accessibleName()
        == "Verify the operating-system Jamulus installation"
    )
    assert not dialog._approve.isVisibleTo(dialog)
    assert dialog._check.isEnabled()


def test_macos_source_only_candidate_is_labeled_unavailable_not_approved():
    dialog = JamulusUpdateDialog()
    dialog.set_snapshot(
        _presentation(
            JamulusUpdateState.FALLBACK,
            reason="macos-integrated-runtime-required",
        )
    )

    assert "Active: Jamulus 3.12.2" in dialog._versions.text()
    assert (
        "Unavailable for WebJam integration: Jamulus 3.12.3"
        in dialog._versions.text()
    )
    assert "Approved update" not in dialog._versions.text()
    assert not dialog._download.isVisibleTo(dialog)
    assert not dialog._activate.isVisibleTo(dialog)
    assert not dialog._approve.isVisibleTo(dialog)


def test_update_dialog_signals_and_download_cancellation_are_accessible():
    dialog = JamulusUpdateDialog()
    events: list[str] = []
    dialog.check_requested.connect(lambda: events.append("check"))
    dialog.cancel_requested.connect(lambda: events.append("cancel"))
    dialog._check.click()
    dialog.set_snapshot(_presentation(JamulusUpdateState.DOWNLOADING))
    assert dialog._progress.isVisibleTo(dialog)
    assert dialog._progress.value() == 43
    assert dialog._cancel.isVisibleTo(dialog)
    assert not dialog._check.isEnabled()
    dialog._cancel.click()
    assert events == ["check", "cancel"]
    assert dialog._status.accessibleName() == "Jamulus update status"
    assert (
        dialog._progress.accessibleName()
        == "Jamulus update download progress"
    )


def test_windows_ready_recovery_actions_open_without_clipped_labels():
    previous_stylesheet = APP.styleSheet()
    APP.setStyleSheet(load_stylesheet())
    dialog = JamulusUpdateDialog()
    dialog.set_snapshot(
        _presentation(
            JamulusUpdateState.READY,
            activate=True,
            approve=True,
            rollback=True,
            approve_label="Open verified installer",
            activate_label="Verify installation",
        )
    )
    dialog.show()
    APP.processEvents()
    try:
        visible_actions = [
            dialog._check,
            dialog._activate,
            dialog._approve,
            dialog._rollback,
            dialog._later,
        ]
        assert dialog.width() <= 720
        assert dialog.height() + 40 <= 600
        for action in visible_actions:
            assert action.isVisibleTo(dialog)
            assert action.width() >= action.sizeHint().width()
        primary_actions = [
            dialog._check,
            dialog._activate,
            dialog._approve,
        ]
        recovery_actions = [dialog._rollback, dialog._later]
        for primary in primary_actions:
            for recovery in recovery_actions:
                assert not primary.geometry().intersects(recovery.geometry())
    finally:
        dialog.close()
        APP.setStyleSheet(previous_stylesheet)


def test_license_dialog_shows_exact_terms_and_requires_explicit_choice():
    terms = "Jamulus exact license\n\nNo acceptance is implied."
    dialog = JamulusLicenseDialog(terms)
    editor = dialog.findChild(QPlainTextEdit, "JamulusLicenseText")
    assert editor is not None
    assert editor.isReadOnly()
    assert editor.toPlainText() == terms
    assert dialog.isModal()
    labels = {button.text() for button in dialog.findChildren(QPushButton)}
    assert labels == {"Not now", "Agree and install"}
