from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone  # noqa: E402

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from core.support_bundle import SupportFacts, build_support_bundle  # noqa: E402
from webjam_qt.windows.support_bundle_preview import (  # noqa: E402
    SupportBundlePreviewDialog,
)


APP = QApplication.instance() or QApplication([])


def _preview():
    return build_support_bundle(
        SupportFacts(
            webjam_version="1.0.0",
            jamulus_version="3.12.2",
            os_name="macOS",
            architecture="arm64",
            jamulus_state="Running",
            recorder_health={"state": "idle", "writable": True},
        ),
        log_excerpts={"webjam": "engine ready"},
        created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
    ).preview()


def test_preview_dialog_shows_exact_members_and_canonical_report() -> None:
    preview = _preview()
    dialog = SupportBundlePreviewDialog(preview)
    dialog.show()
    APP.processEvents()
    try:
        shown_files = {
            label.text().removeprefix("• ")
            for label in dialog.findChildren(QLabel, "SupportPreviewFile")
        }
        assert shown_files == set(preview.archive_files)
        assert dialog.exact_archive_files == preview.archive_files
        assert dialog._report.toPlainText() == preview.copy_text
        assert dialog.logical_fields == tuple(preview.manifest["logical_fields"])
        assert dialog.privacy_facts == preview.manifest["privacy"]
    finally:
        dialog.close()


def test_preview_leads_with_plain_privacy_exclusions() -> None:
    dialog = SupportBundlePreviewDialog(_preview())
    try:
        privacy = dialog.findChild(QLabel, "SupportPreviewPrivacy").text()
        for word in ("recordings", "session notes", "meeting links", "secrets"):
            assert word in privacy
        buttons = {button.text() for button in dialog.findChildren(QPushButton)}
        assert buttons == {"Cancel", "Choose Where to Save"}
    finally:
        dialog.close()


def test_preview_does_not_contain_audio_or_personal_archive_members() -> None:
    dialog = SupportBundlePreviewDialog(_preview())
    try:
        joined = " ".join(dialog.exact_archive_files).lower()
        for forbidden in (".wav", ".aiff", "notes", "transcript", ".db"):
            assert forbidden not in joined
    finally:
        dialog.close()
