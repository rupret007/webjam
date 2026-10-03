"""Actual selected-media controls, live workers, recovery and draft retention."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
from threading import Event, get_ident
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QPushButton, QScrollArea

from core.session_library import SessionLibrary
from core import workspace_media_backup as media
from tests.test_workspace_media_backup import art as art, _package
from tests.test_workspace_navigation import navigation as navigation
from webjam_qt.windows.session_library import SessionLibraryDialog, WorkspaceBackupPreviewDialog
from webjam_qt.windows import workspace_backup as ui


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def box_font(app):
    """Reproduce native missing-font widths without changing normal cases."""
    font_id = QFontDatabase.addApplicationFont(str(Path(__file__).parent / "support/fonts/BoxEmProbe.ttf"))
    assert font_id >= 0
    families = QFontDatabase.applicationFontFamilies(font_id)
    assert families == ["WebJam Box Em Probe"]
    try:
        yield families[0]
    finally:
        QFontDatabase.removeApplicationFont(font_id)


def _wait(app, predicate, timeout=5):
    until = time.monotonic() + timeout
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        time.sleep(.005)
    assert predicate(), "Workspace operation did not reach its expected state"


@pytest.mark.parametrize("timer_owner", ["notes", "workspace"])
def test_expired_owner_save_at_import_retirement_keeps_the_editor_draft(
    navigation, tmp_path, art, monkeypatch, timer_owner,
):
    navigator, _mailbox, _controllers, app = navigation
    controller = navigator.controller
    coordinator = controller.session_library
    package = _package(tmp_path, art[0])
    coordinator.show()
    editor = coordinator.dialog
    editor.notes.setPlainText("Keep this unsaved while importing separate work")
    controller._notes_save_timer.stop()
    coordinator.timer.stop()
    timer = controller._notes_save_timer if timer_owner == "notes" else coordinator.timer
    draft = deepcopy(editor._edited_record())
    base = deepcopy(editor._base_record)
    owner = deepcopy(coordinator.current)
    saved_path = coordinator.library.root / f"{owner.id}.json"
    saved_bytes = saved_path.read_bytes()
    original_end = editor.workspace_flow._end
    retired = []

    def end_with_expired_owner_save():
        original_end()
        if not editor.media_operation_pending:
            # An overdue timer can be dispatched in the same event batch as
            # the worker's final callback, before the caller resumes.
            timer.timeout.emit()
            retired.append(editor._dirty)

    monkeypatch.setattr(editor.workspace_flow, "_end", end_with_expired_owner_save)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(package.path), ""))
    for count in (1, 2):
        editor.import_backup_button.click()
        _wait(app, lambda: editor.workspace_flow.prompt is not None)
        editor.workspace_flow.prompt.confirm_button.click()
        _wait(app, lambda: not editor.media_operation_pending)
        assert retired == [True] * count
        assert editor._edited_record() == draft and editor._dirty
        assert editor._base_record == base and editor._base_record._store_token == base._store_token
        assert coordinator.current == owner
        assert coordinator.current._store_token == owner._store_token
        assert saved_path.read_bytes() == saved_bytes
        assert len(coordinator.library.list()) == 1 + count
        assert not editor.timer.isActive()
    editor.save_button.click()
    assert not editor._dirty
    assert coordinator.library.load(owner.id).notes == draft.notes
    assert coordinator.current.notes == draft.notes
    editor.notes.setPlainText("New typing resumes the editor's own autosave")
    assert editor.timer.isActive()
    editor.timer.timeout.emit()
    assert not editor._dirty
    assert coordinator.library.load(owner.id).notes == editor.notes.toPlainText()


@pytest.fixture
def make_dialog(app):
    dialogs = []

    def create(library, **kwargs):
        dialog = SessionLibraryDialog(library, **kwargs)
        dialogs.append(dialog)
        dialog.show()
        return dialog

    yield create
    for dialog in dialogs:
        if dialog.workspace_flow.active:
            dialog.workspace_flow.request_close()
            _wait(app, lambda: not dialog.workspace_flow.active)
        dialog.timer.stop()
        dialog._dirty = False
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def _choose_media(app, dialog, *, selected=True):
    """Drive the real choices in the nested file-choice event loop."""
    errors = []
    def choose():
        choices = dialog.findChild(ui.WorkspaceBackupChoicesDialog)
        try:
            assert choices is not None and choices.isVisible()
            assert choices.metadata_only.isChecked()
            if selected:
                QTest.mouseClick(choices.selected_media, Qt.MouseButton.LeftButton, pos=QPoint(8, choices.selected_media.height() // 2))
                for candidate, check in choices.candidates:
                    if candidate.selectable:
                        QTest.mouseClick(check, Qt.MouseButton.LeftButton, pos=QPoint(8, check.height() // 2))
                assert choices.selected_media.isChecked()
                assert choices.selection().art_reference_ids == ("painting",)
            QTest.mouseClick(choices.confirm_button, Qt.MouseButton.LeftButton)
        except BaseException as error:
            errors.append(error)
            if choices is not None:
                choices.reject()
    QTimer.singleShot(0, choose)
    dialog.backup_button.click()
    if errors:
        raise errors[0]


def test_choices_default_metadata_never_probe_stored_locations(app, art, monkeypatch):
    record, path = art
    original = Path.stat
    def no_media_stat(value, *args, **kwargs):
        assert value != path, "choices touched an unselected source"
        return original(value, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", no_media_stat)
    dialog = ui.WorkspaceBackupChoicesDialog(record)
    try:
        assert dialog.metadata_only.isChecked()
        assert not dialog.media_options.isEnabled()
        assert all(not check.isChecked() for _, check in dialog.candidates)
        dialog.selected_media.setChecked(True)
        by_id = {c.reference_id: check for c, check in dialog.candidates}
        assert not by_id["lesson"].isEnabled()
        by_id["painting"].setFocus()
        QTest.keyClick(by_id["painting"], Qt.Key.Key_Space)
        assert dialog.selection().art_reference_ids == ("painting",)
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_actual_backup_preview_and_import_preserve_conflicting_draft_and_original(
    app, art, tmp_path, make_dialog, monkeypatch,
):
    source, original = art
    before = original.read_bytes()
    path = tmp_path / "portable.webjambackup"
    source_dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=source.id)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(path), ""))
    threads = []
    plan = ui.plan_workspace_package
    def traced(*args, **kwargs):
        threads.append(get_ident())
        return plan(*args, **kwargs)
    monkeypatch.setattr(ui, "plan_workspace_package", traced)
    _choose_media(app, source_dialog)
    _wait(app, lambda: isinstance(source_dialog.workspace_flow.prompt, ui.WorkspacePackagePlanDialog))
    preview = source_dialog.workspace_flow.prompt
    assert "Painting" in preview.contents.toPlainText()
    assert "Lesson: not selected" in preview.contents.toPlainText()
    assert not preview.isModal()
    assert not source_dialog.copy_button.isEnabled()
    preview.confirm_button.click()
    _wait(app, lambda: not source_dialog.workspace_flow.active)
    assert threads == [threads[0]] and threads[0] != get_ident()
    assert path.is_file() and "backed up" in source_dialog.status.text()
    receipt_paths = list(tmp_path.glob("*.webjamreceipt"))
    assert len(receipt_paths) == 1

    library = SessionLibrary(tmp_path / "restored")
    current = library.create("art", "Current owner", notes="Saved notes")
    dialog = make_dialog(library, current_id=current.id)
    dialog.search.setText("Current owner")
    dialog.notes.setPlainText("Keep this conflicting draft")
    # Exercise a real conflict while importing another workspace.
    newest = library.save(replace(current, notes="Other writer"))
    draft = dialog._edited_record()
    emitted = []
    for signal in (dialog.record_saved, dialog.copy_saved, dialog.continue_requested, dialog.take_open_requested):
        signal.connect(lambda *_: emitted.append(True))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(receipt_paths[0]), ""))
    dialog.import_backup_button.click()
    _wait(app, lambda: isinstance(dialog.workspace_flow.prompt, WorkspaceBackupPreviewDialog))
    assert dialog.media_operation_pending and not dialog.save_current()
    assert not dialog.workspace_flow.prompt.isModal()
    dialog.workspace_flow.prompt.confirm_button.click()
    _wait(app, lambda: not dialog.workspace_flow.active)
    records = library.list()
    assert len(records) == 2
    imported = next(record for record in records if record.id != current.id)
    assert imported.id != source.id and imported.media_provenance
    restored_path = Path(imported.art["references"][0]["locator"])
    assert restored_path != original and restored_path.read_bytes() == before
    assert original.read_bytes() == before
    assert library.load(current.id) == newest
    assert dialog.record == current and dialog._base_record == current
    assert dialog._edited_record() == draft and dialog._dirty
    assert dialog.search.text() == "Current owner"
    assert dialog.history.currentItem().data(Qt.ItemDataRole.UserRole) == current.id
    assert dialog.selected_record is None and not emitted
    assert not dialog.timer.isActive()


def test_plan_missing_source_blocks_export_and_allows_changing_choices(app, art, make_dialog, tmp_path):
    record, path = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    path.unlink()
    _choose_media(app, dialog)
    _wait(app, lambda: dialog.workspace_flow.prompt is not None)
    preview = dialog.workspace_flow.prompt
    assert not preview.confirm_button.isEnabled()
    assert "Resolve before backing up" in preview.contents.toPlainText()
    assert preview.change_button.isEnabled()
    preview.cancel_button.click()
    assert not dialog.media_operation_pending


@pytest.mark.parametrize("action", ["cancel", "close", "accept", "done", "escape"])
def test_worker_keeps_owner_until_thread_dies_and_result_is_consumed(
    app, art, make_dialog, tmp_path, monkeypatch, action,
):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    entered, release = Event(), Event()
    original = ui.plan_workspace_package
    def paused(record, selection, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(record, selection, **kwargs)
    monkeypatch.setattr(ui, "plan_workspace_package", paused)
    try:
        _choose_media(app, dialog)
        assert entered.wait(2)
        flow = dialog.workspace_flow
        progress = flow.progress_dialog
        assert progress.isVisible() and not progress.isModal()
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        app.processEvents()
        assert heartbeat and dialog.isVisible()
        flow.job._timer.stop()  # Separate thread exit from GUI delivery.
        if action == "cancel":
            progress.cancel_button.click()
        elif action == "close":
            dialog.close()
        elif action == "accept":
            dialog.accept()
        elif action == "done":
            dialog.done(QDialog.DialogCode.Accepted)
        else:
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert flow.active and flow.job.pending and dialog.isVisible()
        assert not dialog.save_current()
        release.set()
        flow.job._thread.join(3)
        assert not flow.job._thread.is_alive()
        assert flow.active and flow.job.pending  # Undrained result still owns it.
        flow.job._poll()
        assert not flow.active and not flow.job.pending
        assert "cancelled" in dialog.status.text()
    finally:
        release.set()
        if dialog.workspace_flow.job._thread is not None:
            dialog.workspace_flow.job._thread.join(3)
            dialog.workspace_flow.job._poll()


def test_late_cancel_does_not_hide_successful_publication(app, art, make_dialog, tmp_path, monkeypatch):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    path = tmp_path / "completed.webjambackup"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(path), ""))
    published, release = Event(), Event()
    original = ui.export_workspace_package
    def delayed(*args, **kwargs):
        receipt = original(*args, **kwargs)
        published.set()
        assert release.wait(5)
        return receipt
    monkeypatch.setattr(ui, "export_workspace_package", delayed)
    try:
        _choose_media(app, dialog)
        _wait(app, lambda: dialog.workspace_flow.prompt is not None)
        dialog.workspace_flow.prompt.confirm_button.click()
        assert published.wait(2)
        dialog.workspace_flow.progress_dialog.cancel_button.click()
        release.set()
        _wait(app, lambda: not dialog.workspace_flow.active)
        assert path.exists()
        assert "backed up" in dialog.status.text()
        assert "cancelled" not in dialog.status.text()
        assert "SHA-256" in dialog.status.toolTip()
    finally:
        release.set()


def test_duplicate_flow_start_cannot_replace_live_callback(app, art, make_dialog, tmp_path, monkeypatch):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    entered, release = Event(), Event()
    original = ui.plan_workspace_package
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, "plan_workspace_package", held)
    try:
        _choose_media(app, dialog)
        assert entered.wait(2)
        flow = dialog.workspace_flow
        worker, completion, progress = flow.job._thread, flow._completion, flow.progress_dialog
        flow.backup(record, media.WorkspacePackageSelection())
        flow.import_path(tmp_path / "does-not-exist.webjambackup")
        flow._run("duplicate", lambda *_: None, lambda *_: None)
        assert flow.job._thread is worker and flow._completion == completion
        assert flow.progress_dialog is progress and dialog.media_operation_pending
        assert not dialog.copy_button.isEnabled()
        release.set()
        _wait(app, lambda: isinstance(flow.prompt, ui.WorkspacePackagePlanDialog))
        flow.prompt.cancel_button.click()
        assert not dialog.media_operation_pending
    finally:
        release.set()


def test_late_inspection_cancel_does_not_open_next_prompt(app, art, make_dialog, tmp_path):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    flow = dialog.workspace_flow
    flow.backup(record, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
    flow.job._timer.stop()
    flow.job._thread.join(3)
    assert not flow.job._thread.is_alive() and flow.active
    flow.progress_dialog.cancel_button.click()
    flow.job._poll()
    assert not flow.active and flow.prompt is None
    assert "cancelled" in dialog.status.text()


@pytest.mark.parametrize("failure", ["close_during_chooser", "chooser_error"])
def test_no_new_export_after_close_or_prompt_callback_error(app, art, make_dialog, tmp_path, monkeypatch, failure):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    destination = tmp_path / "must-not-publish.webjambackup"
    def choose(*_args, **_kwargs):
        if failure == "close_during_chooser":
            dialog.close()
            return str(destination), ""
        raise OSError("chooser unavailable")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    _choose_media(app, dialog)
    _wait(app, lambda: dialog.workspace_flow.prompt is not None)
    dialog.workspace_flow.prompt.confirm_button.click()
    assert not dialog.workspace_flow.active and not dialog.media_operation_pending
    assert dialog.workspace_flow.job._thread is None
    assert not destination.exists()
    if failure == "chooser_error":
        assert "chooser unavailable" in dialog.status.text()


def test_change_choices_retains_selected_media_and_sidecar_options(app, art, make_dialog, tmp_path):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    selection = media.WorkspacePackageSelection(art_reference_ids=("painting",), include_reviews=False, include_studio=False)
    dialog.workspace_flow.backup(record, selection)
    _wait(app, lambda: dialog.workspace_flow.prompt is not None)
    dialog.workspace_flow.prompt.change_button.click()
    choices = dialog.workspace_flow.prompt
    assert isinstance(choices, ui.WorkspaceBackupChoicesDialog)
    assert choices.selected_media.isChecked() and choices.selection() == selection
    choices.cancel_button.click()
    assert not dialog.workspace_flow.active


def test_uncertain_export_outcome_survives_requested_close(app, art, make_dialog, tmp_path, monkeypatch):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    destination = tmp_path / "uncertain.webjambackup"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(destination), ""))
    published, release = Event(), Event()
    original = ui.export_workspace_package
    receipts = []
    def uncertain(*args, **kwargs):
        receipt = original(*args, **kwargs)
        receipts.append(receipt)
        published.set()
        assert release.wait(5)
        raise media.WorkspacePackagePublicationUnconfirmed(receipt.path, receipt.sha256, receipt.size_bytes,
                                                          receipt.publication_receipt)
    monkeypatch.setattr(ui, "export_workspace_package", uncertain)
    try:
        _choose_media(app, dialog)
        _wait(app, lambda: dialog.workspace_flow.prompt is not None)
        dialog.workspace_flow.prompt.confirm_button.click()
        assert published.wait(2)
        dialog.close()
        release.set()
        _wait(app, lambda: not dialog.workspace_flow.active)
        assert dialog.isVisible() and destination.is_file()
        assert "publication needs checking" in dialog.status.text()
        assert receipts[0].sha256 in dialog.status.toolTip()
    finally:
        release.set()


@pytest.mark.parametrize("failure", ["thread", "progress_constructor", "progress_show"])
def test_worker_start_failure_releases_controls(app, art, make_dialog, tmp_path, monkeypatch, failure):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    class RefusedThread:
        def __init__(self, **_kwargs):
            pass
        def start(self):
            raise RuntimeError("worker unavailable")
    if failure == "thread":
        monkeypatch.setattr(ui, "Thread", RefusedThread)
    else:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("progress unavailable")
        if failure == "progress_constructor":
            monkeypatch.setattr(ui, "WorkspaceProgressDialog", unavailable)
        else:
            monkeypatch.setattr(ui.WorkspaceProgressDialog, "show", unavailable)
    _choose_media(app, dialog)
    assert not dialog.media_operation_pending
    assert dialog.backup_button.isEnabled()
    assert "unavailable" in dialog.status.text()
    assert dialog.workspace_flow.token is None and dialog.workspace_flow._completion is None


@pytest.mark.parametrize("failure", ["completion", "failure_callback", "recovery_refresh"])
def test_terminal_reporting_failure_always_releases_media_operation(
    app, art, make_dialog, tmp_path, monkeypatch, failure,
):
    record, _ = art
    dialog = make_dialog(SessionLibrary(tmp_path / "original"), current_id=record.id)
    dialog.notes.setPlainText("Keep my draft after a reporting failure")
    before = dialog._edited_record()
    flow = dialog.workspace_flow
    callbacks = []

    def work(_report, _cancel):
        if failure != "completion":
            raise ValueError("original failure")
        return None

    def complete(_result, token):
        assert token is flow.token and flow.active
        raise ValueError("original failure")

    def failed(error):
        callbacks.append((str(error), flow.active, flow.token is not None))
        if failure == "failure_callback":
            raise RuntimeError("failure evidence unavailable")

    if failure == "recovery_refresh":
        def refresh():
            raise RuntimeError("recovery unavailable")
        monkeypatch.setattr(dialog, "_sync_import_recovery", refresh)
    flow.execute("Explicit action", work, complete, failed=failed)
    _wait(app, lambda: not flow.job.pending)
    assert callbacks == [("original failure", True, True)]
    assert not dialog.media_operation_pending
    assert flow.token is None and flow._completion is None
    assert dialog.backup_button.isEnabled()
    assert dialog._dirty and dialog._edited_record() == before
    assert "original failure" in dialog.status.text()


def test_media_cancel_then_fresh_dialog_check_and_resume_same_id(
    app, art, make_dialog, tmp_path, monkeypatch,
):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "restored")
    current = library.create("art", "Owner")
    dialog = make_dialog(library, current_id=current.id)
    dialog.notes.setPlainText("Keep my draft")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(preview.path), ""))
    original = ui.import_workspace_package
    def cancel_after_prepare(library, preview, **_kwargs):
        media.prepare_workspace_package_import(library, preview)
        raise media.WorkspacePackageCancelled("cancelled after durable preparation")
    with monkeypatch.context() as fault:
        fault.setattr(ui, "import_workspace_package", cancel_after_prepare)
        dialog.import_backup_button.click()
        _wait(app, lambda: dialog.workspace_flow.prompt is not None)
        dialog.workspace_flow.prompt.confirm_button.click()
        _wait(app, lambda: not dialog.workspace_flow.active)
    assert ui.import_workspace_package is original
    pending = library.pending_import()
    assert pending is not None and library.pending_import_has_media()
    assert dialog.record.id == current.id and dialog.notes.toPlainText() == "Keep my draft"
    assert dialog.import_backup_button.text() == "Check previous import"
    # The second store/owner has only durable recovery state.
    reopened = make_dialog(SessionLibrary(library.root), current_id=current.id)
    reopened.import_backup_button.click()
    _wait(app, lambda: not reopened.workspace_flow.active)
    assert reopened.import_backup_button.text() == "Choose original backup…"
    reopened.import_backup_button.click()
    _wait(app, lambda: reopened.workspace_flow.prompt is not None)
    assert reopened.workspace_flow.prompt.confirm_button.text() == "Resume same import"
    reopened.workspace_flow.prompt.confirm_button.click()
    _wait(app, lambda: not reopened.workspace_flow.active)
    assert {record.id for record in library.list()} == {current.id, pending.id}
    assert reopened.library.pending_import() is None
    assert reopened.record.id == current.id


def test_retry_preview_cannot_switch_to_replacement_pending_id(app, art, make_dialog, tmp_path, monkeypatch):
    record, _ = art
    preview = _package(tmp_path, record)
    root = tmp_path / "restored"
    old = media.prepare_workspace_package_import(SessionLibrary(root), preview)
    dialog = make_dialog(SessionLibrary(root))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(preview.path), ""))
    dialog.import_backup_button.click()
    _wait(app, lambda: not dialog.workspace_flow.active)
    dialog.import_backup_button.click()
    _wait(app, lambda: dialog.workspace_flow.prompt is not None)
    other = SessionLibrary(root)
    media.import_workspace_package(other, preview, retry=True, expected_record=old)
    replacement = media.prepare_workspace_package_import(other, preview)
    assert replacement.id != old.id
    dialog.workspace_flow.prompt.confirm_button.click()
    _wait(app, lambda: not dialog.workspace_flow.active)
    assert "intended import changed" in dialog.status.text()
    assert {saved.id for saved in other.list()} == {old.id}
    assert other.pending_import().id == replacement.id


def test_return_to_launch_and_shutdown_keep_worker_owner(navigation, tmp_path, art, monkeypatch):
    navigator, _mailbox, controllers, app = navigation
    controller = navigator.controller
    library = controller.session_library
    library.show()
    dialog = library.dialog
    record, _ = art
    entered, release = Event(), Event()
    original = ui.plan_workspace_package
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, "plan_workspace_package", held)
    try:
        dialog.workspace_flow.backup(record, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
        assert entered.wait(2)
        job = dialog.workspace_flow.job
        job._timer.stop()
        dialog.hide()
        library.show()
        assert library.dialog is dialog and dialog.isVisible()
        assert not navigator.return_to_launch()
        assert not controller._confirm_close()
        assert not controller.shutdown()
        assert navigator.controller is controller and len(controllers) == 1
        assert not controller._shutdown_cleanup_pending
        current = library.current
        live_ids = set(library._live_take_ids)
        assert library.profile_changing() is False
        assert library.current is current and library._live_take_ids == live_ids
        release.set()
        job._thread.join(3)
        assert not job._thread.is_alive() and library.media_operation_pending
        assert not controller.shutdown()
        job._poll()
        app.processEvents()
        assert not library.media_operation_pending
        assert navigator.return_to_launch()
    finally:
        release.set()
        if dialog.workspace_flow.job._thread is not None:
            dialog.workspace_flow.job._thread.join(3)
            dialog.workspace_flow.job._poll()


@pytest.mark.parametrize("font_size,stretch,full_em", [(13, 100, False), (22, 100, False), (22, 125, False), (22, 100, True)])
def test_choices_and_previews_fit_compact_enlarged_text(app, box_font, art, tmp_path, font_size, stretch, full_em):
    from webjam_qt.theme import load_stylesheet
    record, _ = art
    changed_art = dict(record.art)
    changed_art["references"] = [dict(ref, title="Long reference title " * 12) for ref in record.art["references"]]
    record = replace(record, art=changed_art)
    plan = media.plan_workspace_package(record, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
    preview = _package(tmp_path, record)
    previous_font = app.font()
    font = QFont(previous_font)
    if full_em:
        font.setFamily(box_font)
    font.setStretch(stretch)
    app.setFont(font)
    dialogs = [ui.WorkspaceBackupChoicesDialog(record), ui.WorkspacePackagePlanDialog(plan),
               WorkspaceBackupPreviewDialog(SessionLibrary(tmp_path / "restored"), preview),
               ui.WorkspaceProgressDialog("Inspect selected media")]
    try:
        for dialog in dialogs:
            family = f'font-family: "{box_font}";' if full_em else ""
            dialog.setStyleSheet(load_stylesheet() + f"QWidget {{ font-size: {font_size}px; {family} }}")
            dialog.show()
            dialog.resize(480, 500)
            for _ in range(5):
                app.processEvents()
            assert dialog.width() == 480 and dialog.height() == 500
            if full_em:
                assert dialog.fontMetrics().horizontalAdvance("MW") == 2 * font_size
            for button in dialog.findChildren(QPushButton):
                assert button.width() >= button.minimumSizeHint().width()
                assert dialog.rect().contains(QRect(button.mapTo(dialog, QPoint()), button.size())), button.text()
            for label in dialog.findChildren(QLabel):
                assert label.wordWrap()
            for scroll in dialog.findChildren(QScrollArea):
                assert scroll.horizontalScrollBar().maximum() == 0, (
                    type(dialog).__name__, font_size, stretch, full_em, scroll.viewport().size(),
                    scroll.widget().minimumSizeHint(), scroll.horizontalScrollBar().maximum(),
                )
    finally:
        app.setFont(previous_font)
        for dialog in dialogs:
            if isinstance(dialog, ui.WorkspaceProgressDialog):
                dialog.settle()
            else:
                dialog.reject()
            dialog.deleteLater()
        app.processEvents()
