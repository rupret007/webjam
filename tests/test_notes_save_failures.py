"""Safe local failure classes preserve draft ownership and useful recovery."""
from __future__ import annotations

import errno
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core import file_io
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog
from webjam_qt.widgets.session_canvas import SessionCanvas


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def notes(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    canvas = SessionCanvas()
    owner = SessionPersistence(SimpleNamespace(), canvas)
    canvas.notes_changed.connect(owner.notes_changed)
    yield canvas, owner
    for dialog in canvas.findChildren(NotesRecoveryDialog):
        dialog.deleteLater()
    canvas.close()
    canvas.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize(
    ("error_number", "reason", "guidance"),
    [
        (errno.ENOSPC, "disk_full", "not enough storage"),
        *([(errno.EDQUOT, "disk_full", "not enough storage")] if hasattr(errno, "EDQUOT") else []),
        (errno.EACCES, "permission_denied", "permission"),
        (errno.EPERM, "permission_denied", "permission"),
        (errno.EROFS, "read_only", "read-only"),
        (errno.EIO, "failed", "could not be confirmed saved"),
        (None, "failed", "could not be confirmed saved"),
    ],
)
def test_failure_reason_is_bounded_and_actionable_without_private_error_text(
    notes, tmp_path, monkeypatch, error_number, reason, guidance,
):
    canvas, owner = notes
    canvas.edit_notes("Keep this draft")
    error = OSError(error_number, "private ENOSPC secret", "/private/customer/notes.md")
    with monkeypatch.context() as patch:
        patch.setattr(persistence_module, "atomic_write_text", mock.Mock(side_effect=error))
        assert not owner._save_notes_only()
        assert owner.notes_recovery_state("music") == reason
        assert owner.notes_recovery_summary == (("music", reason),)
        assert owner.notes_save_state == "failed"
        assert owner.unsaved_notes == (("music", "Keep this draft"),)
        dialog = NotesRecoveryDialog(owner, canvas)
        for value in (dialog._message.text(), dialog._message.accessibleDescription()):
            assert guidance in value
            assert "private" not in value
            assert "secret" not in value
            assert "ENOSPC" not in value
            assert "unchanged" not in value
        assert dialog._save.isEnabled()
        assert dialog._export.isEnabled()

    dialog._save.click()
    assert (tmp_path / ".webjam_notes.md").read_text() == "Keep this draft"
    assert not owner.has_unsaved_notes
    assert owner.notes_recovery_summary == ()
    assert owner.notes_save_state == "saved"
    assert dialog.result() == dialog.DialogCode.Accepted


def test_failures_stay_with_hidden_workspace_and_summary_never_contains_notes(notes, monkeypatch):
    canvas, owner = notes
    changes = []
    monkeypatch.setattr(
        canvas, "set_notes_recovery_context",
        lambda active, summary: changes.append((active, summary)), raising=False,
    )
    canvas.edit_notes("Private music text")
    assert owner.notes_recovery_summary == (("music", "pending"),)

    def fail_for_profile(path, *_args, **_kwargs):
        number = errno.ENOSPC if path.name == ".webjam_notes.md" else errno.EACCES
        raise OSError(number, "private error", str(path))

    monkeypatch.setattr(persistence_module, "atomic_write_text", fail_for_profile)
    assert owner.switch_profile_key("art") == "art"
    assert changes[-1] == ("art", (("music", "disk_full"),))
    canvas.edit_notes("Private art text")
    assert owner.notes_recovery_summary == (("music", "disk_full"), ("art", "pending"))
    assert not owner._save_notes_only()
    expected = (("music", "disk_full"), ("art", "permission_denied"))
    assert owner.notes_recovery_summary == expected
    assert changes[-1] == ("art", expected)
    canvas.edit_notes("Edited private art text")
    assert changes[-1] == ("art", expected)
    assert owner.notes_recovery_state("music") == "disk_full"
    assert owner.notes_recovery_state("art") == "permission_denied"
    assert "Private" not in repr(changes)


@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EACCES, errno.EROFS, errno.EIO])
def test_failed_directory_sync_classification_never_settles_undo(notes, tmp_path, monkeypatch, error_number):
    canvas, owner = notes
    canvas.edit_notes("Original")
    assert owner._save_notes_only()
    canvas._notes.selectAll()
    canvas._notes.insertPlainText("Revised")

    def fail_sync(_path):
        raise OSError(error_number, "private sync error")

    with monkeypatch.context() as patch:
        patch.setattr(file_io, "_fsync_parent_directory", fail_sync)
        assert not owner._save_notes_only()
        assert (tmp_path / ".webjam_notes.md").read_text() == "Revised"
        canvas._notes.undo()
        assert canvas.current_notes() == "Original"
        assert owner.has_unsaved_notes
        assert not owner._save_notes_only()
        assert owner.unsaved_notes == (("music", "Original"),)

    assert owner._save_notes_only()
    assert (tmp_path / ".webjam_notes.md").read_text() == "Original"
    assert owner.notes_recovery_summary == ()


@pytest.mark.parametrize(
    ("export_errno", "guidance"),
    [
        (errno.ENOSPC, "not enough storage"),
        (errno.EACCES, "permission"),
        (errno.EROFS, "read-only"),
        (errno.EIO, "could not be confirmed saved"),
    ],
)
def test_failed_export_keeps_original_reason_until_exact_copy_is_confirmed(
    notes, tmp_path, monkeypatch, export_errno, guidance,
):
    canvas, owner = notes
    canvas.edit_notes("Keep this local draft")
    with monkeypatch.context() as patch:
        patch.setattr(
            persistence_module, "atomic_write_text",
            mock.Mock(side_effect=OSError(errno.ENOSPC, "private original error")),
        )
        assert not owner._save_notes_only()
    dialog = NotesRecoveryDialog(owner, canvas)
    copy = tmp_path / "recovered.md"
    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName",
        lambda *args: (str(copy), ""),
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            persistence_module, "atomic_write_text",
            mock.Mock(side_effect=OSError(export_errno, "private copy error", str(copy))),
        )
        dialog._export.click()
    assert guidance in dialog._message.text()
    assert dialog._message.accessibleDescription() == dialog._message.text()
    assert "private" not in dialog._message.text()
    assert owner.notes_recovery_state("music") == "disk_full"
    assert owner.has_unsaved_notes
    assert not copy.exists()

    dialog._export.click()
    assert copy.read_text() == "Keep this local draft"
    assert not owner.has_unsaved_notes
    assert owner.notes_recovery_summary == ()
    assert owner.notes_save_state == "exported"


def test_stale_export_cannot_clear_newer_failure(notes, tmp_path, monkeypatch):
    canvas, owner = notes
    canvas.edit_notes("Older draft")
    monkeypatch.setattr(
        persistence_module, "atomic_write_text",
        mock.Mock(side_effect=OSError(errno.EROFS, "private error")),
    )
    assert not owner._save_notes_only()
    canvas.edit_notes("Newer draft")
    assert not owner.export_pending_notes("music", "Older draft", str(tmp_path / "old.md"))
    assert owner.unsaved_notes == (("music", "Newer draft"),)
    assert owner.notes_recovery_summary == (("music", "read_only"),)


@pytest.mark.parametrize(
    ("error_number", "reason"),
    [(errno.ENOSPC, "disk_full"), (errno.EACCES, "permission_denied"), (errno.EROFS, "read_only")],
)
def test_classified_notes_failure_vetoes_quit_before_runtime_cleanup(
    qapp, tmp_path, monkeypatch, error_number, reason,
):
    from core.settings import AppSettings
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.windows.conductor_window import ConductorWindow

    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam", initial_title="Local Notes recovery",
    )
    controller = ApplicationController(
        window,
        settings=AppSettings(
            config_file=str(tmp_path / "settings.json"),
            takes_directory=str(tmp_path / "takes"),
        ),
    )
    controller.bridge.stop_jamulus = mock.Mock(return_value=True)
    try:
        window.session_canvas.edit_notes("Retain these notes before quitting")
        controller._notes_save_timer.stop()
        with monkeypatch.context() as patch:
            patch.setattr(
                persistence_module, "atomic_write_text",
                mock.Mock(side_effect=OSError(error_number, "private save failure")),
            )
            with mock.patch.object(window, "flash_message") as flash:
                assert controller.shutdown() is False
            assert controller._shutdown is False
            assert controller._shutdown_in_progress is False
            assert controller._shutdown_cleanup_pending is False
            assert controller._persistence.notes_recovery_summary == (("music", reason),)
            assert window.session_canvas.current_notes() == "Retain these notes before quitting"
            controller.bridge.stop_jamulus.assert_not_called()
            assert "Notes are not saved yet" in flash.call_args.args[0]
        assert controller._save_notes()
        assert (tmp_path / ".webjam_notes.md").read_text() == "Retain these notes before quitting"
        assert controller.shutdown()
    finally:
        controller.shutdown()
        window.close()
        window.deleteLater()
        controller.deleteLater()
