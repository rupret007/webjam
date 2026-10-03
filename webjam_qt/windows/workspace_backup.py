"""Selected-media backup dialogs and cancellable, GUI-owned worker lifetime."""
from __future__ import annotations

from copy import deepcopy
from threading import Event, Lock, Thread

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.session_library import SessionLibraryImportUnconfirmed
from core.workspace_media_backup import (
    WorkspacePackageCancelled, WorkspacePackagePublicationUnconfirmed,
    WorkspacePackageSelection, export_workspace_package, import_workspace_package,
    plan_workspace_package, preview_workspace_package, preview_workspace_package_receipt,
    reconcile_workspace_package_import,
    workspace_backup_candidates,
)


def _label(text, parent=None):
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


def _size(value):
    return f"{value / 1024**2:,.1f} MiB ({value:,} bytes)"


def package_contents(package):
    record = package.record
    titles = {(c.kind, c.reference_id): c.title for c in workspace_backup_candidates(record)}
    lines = [f"Included files: {package.file_count}", f"Media size: {_size(package.total_bytes)}", "", "Included:"]
    lines.extend(f"{a['kind'].title()}: {titles.get((a['kind'], a['reference_id']), a['reference_id'])}"
                 for a in package.included)
    if not package.included:
        lines.append("No media files selected.")
    lines.extend(("", "Excluded / stored links:"))
    lines.extend(package.excluded or ("None.",))
    return "\n".join(lines)


class WorkspaceBackupChoicesDialog(QDialog):
    """Choices use stored metadata only, including unavailable/network locations."""

    def __init__(self, record, parent=None, *, selection=None):
        super().__init__(parent)
        self.setWindowTitle("Back up workspace")
        self.resize(560, 520)
        self.setMinimumSize(360, 320)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(_label(f"Back up {record.title}"))
        self.metadata_only = QRadioButton("Metadata only")
        self.selected_media = QRadioButton("Include selected media")
        self.metadata_only.setChecked(True)
        layout.addWidget(self.metadata_only)
        layout.addWidget(_label("Keep notes, plans, Art context and stored links in a JSON file. Audio and artwork are not copied."))
        layout.addWidget(self.selected_media)
        layout.addWidget(_label("Choose completed takes and local Art files. Only checked sources will be inspected. Web links stay as links."))
        self.media_options = QWidget()
        options = QVBoxLayout(self.media_options)
        options.setContentsMargins(0, 0, 0, 0)
        self.candidates = []
        for candidate in workspace_backup_candidates(record):
            check = QCheckBox("Include take" if candidate.kind == "take" else "Include Art file")
            check.setToolTip(candidate.locator)
            check.setEnabled(candidate.selectable)
            check.setAccessibleName(f"Include {candidate.kind}: {candidate.title}")
            options.addWidget(check)
            options.addWidget(_label(candidate.title or candidate.reference_id))
            options.addWidget(_label(candidate.reason))
            self.candidates.append((candidate, check))
        self.include_reviews = QCheckBox("Include saved take reviews")
        self.include_studio = QCheckBox("Include saved Studio edits")
        for check in (self.include_reviews, self.include_studio):
            check.setChecked(True)
            options.addWidget(check)
        options.addWidget(_label("Studio edits may need other linked takes. The preview explains any missing dependencies."))
        layout.addWidget(self.media_options)
        layout.addWidget(_label("Private notes and stored locations remain literal. Unrelated files, credentials and invitations are not added."))
        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.confirm_button = QPushButton("Continue")
        self.confirm_button.clicked.connect(self.accept)
        outer.addWidget(self.confirm_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        outer.addWidget(self.cancel_button)
        self.selected_media.toggled.connect(self.media_options.setEnabled)
        self.media_options.setEnabled(False)
        self.metadata_only.setFocus()
        if selection is not None:
            self.selected_media.setChecked(True)
            for candidate, check in self.candidates:
                selected = selection.take_ids if candidate.kind == "take" else selection.art_reference_ids
                check.setChecked(candidate.selectable and candidate.reference_id in selected)
            self.include_reviews.setChecked(selection.include_reviews)
            self.include_studio.setChecked(selection.include_studio)

    def selection(self):
        return WorkspacePackageSelection(
            take_ids=tuple(c.reference_id for c, check in self.candidates if c.kind == "take" and check.isChecked()),
            art_reference_ids=tuple(c.reference_id for c, check in self.candidates if c.kind == "art" and check.isChecked()),
            include_reviews=self.include_reviews.isChecked(), include_studio=self.include_studio.isChecked(),
        )


class WorkspacePackagePlanDialog(QDialog):
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review workspace backup")
        self.resize(560, 520)
        self.setMinimumSize(360, 300)
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Review selected media before creating a new backup file."))
        self.contents = QPlainTextEdit()
        self.contents.setReadOnly(True)
        self.contents.setTabChangesFocus(True)
        self.contents.setAccessibleName("Selected media backup preview")
        text = package_contents(plan)
        if plan.blockers:
            text += "\n\nResolve before backing up:\n" + "\n".join(plan.blockers)
        self.contents.setPlainText(text)
        layout.addWidget(self.contents, 1)
        layout.addWidget(_label("Up to 4,096 entries and 64 GiB. Size excludes package headers. Keep the backup and its small checksum receipt together. Source changes block export. Original files stay unchanged."))
        self.confirm_button = QPushButton("Create backup…")
        self.confirm_button.setEnabled(plan.exportable)
        self.confirm_button.clicked.connect(self.accept)
        layout.addWidget(self.confirm_button)
        self.change_button = QPushButton("Change choices…")
        self.change_button.clicked.connect(lambda: self.done(2))
        layout.addWidget(self.change_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        self.cancel_button.setDefault(True)


class WorkspaceJob(QObject):
    """No Qt access from the worker; coalesce progress and consume after exit."""

    progress = Signal(object)
    finished = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._cancel = Event()
        self._lock = Lock()
        self._mailbox = {}
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)

    @property
    def pending(self):
        return self._thread is not None

    def start(self, function):
        if self.pending:
            raise RuntimeError("A workspace operation is still owned by this window.")
        self._cancel = Event()
        self._mailbox = {}
        cancel, lock, mailbox = self._cancel, self._lock, self._mailbox

        def progress(value):
            with lock:
                mailbox["progress"] = value

        def run():
            try:
                result, error = function(progress, cancel.is_set), None
            except BaseException as exc:
                result, error = None, exc
            with lock:
                mailbox["terminal"] = (result, error)

        self._thread = Thread(target=run, name="webjam-workspace-media", daemon=False)
        try:
            self._thread.start()
        except BaseException:
            self._thread = None
            raise
        self._timer.start()

    def cancel(self):
        self._cancel.set()

    @property
    def cancellation_requested(self):
        return self._cancel.is_set()

    def _poll(self):
        with self._lock:
            progress = self._mailbox.pop("progress", None)
        if progress is not None:
            self.progress.emit(progress)
        thread = self._thread
        if thread is None or thread.is_alive():
            return
        thread.join(0)
        with self._lock:
            result, error = self._mailbox.pop("terminal")
        self._thread = None
        self._timer.stop()
        self.finished.emit(result, error)


class WorkspaceProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(460, 230)
        self.setMinimumWidth(340)
        self._settled = False
        layout = QVBoxLayout(self)
        self.details = _label(title)
        self.details.setAccessibleName("Workspace operation progress")
        layout.addWidget(self.details)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self.hint = _label("Your draft stays here. Session Stop and End remain available in the main window.")
        layout.addWidget(self.hint)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)

    def update_progress(self, value):
        self.details.setText(f"{value.phase}\nChecked or copied: {_size(value.completed_bytes)}")
        if value.total_bytes and value.completed_bytes <= value.total_bytes:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(1000 * value.completed_bytes / value.total_bytes)))
        else:
            self.progress.setRange(0, 0)

    def reject(self):
        if self._settled:
            super().reject()
            return
        self.cancel_button.setEnabled(False)
        self.hint.setText("Cancellation requested. Waiting for the operation's result; publication may already have completed.")
        self.cancel_requested.emit()

    def closeEvent(self, event):
        if self._settled:
            event.accept()
        else:
            event.ignore()
            self.reject()

    def settle(self):
        self._settled = True
        super().accept()


class WorkspaceBackupFlow(QObject):
    """Keep one operation, accepted snapshot and recovery intent with its dialog."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.active = False
        self.closing = False
        self.recovery = None
        self.job = WorkspaceJob(self)
        self.job.progress.connect(self._progress)
        self.job.finished.connect(self._finished)
        self.progress_dialog = None
        self.prompt = None
        self._completion = None
        self._publishing = False
        self._selection = None

    def _begin(self):
        if self.active or self.job.pending:
            return False
        self.active = True
        self.closing = False
        self.owner._set_workspace_busy(True)
        return True

    def _end(self):
        if self.job.pending:
            return
        self.active = False
        self._completion = None
        self.owner._set_workspace_busy(False)
        # Show the terminal outcome before a second explicit Close. In
        # particular, never delete the only visible uncertainty receipt.
        self.closing = False

    def request_close(self):
        self.closing = True
        self.job.cancel()
        if self.progress_dialog is not None:
            self.progress_dialog.reject()
        if self.prompt is not None:
            self.prompt.reject()

    def _run(self, title, function, complete, *, publishing=False):
        if self.job.pending or self._completion is not None:
            return False
        self._completion = complete
        self._publishing = publishing
        self.progress_dialog = WorkspaceProgressDialog(title, self.owner)
        self.progress_dialog.cancel_requested.connect(self.job.cancel)
        self.progress_dialog.show()
        try:
            self.job.start(function)
        except Exception as error:
            self._finished(None, error)
        return True

    def _progress(self, value):
        if self.progress_dialog is not None:
            self.progress_dialog.update_progress(value)

    def _finished(self, result, error):
        if self.progress_dialog is not None:
            self.progress_dialog.settle()
            self.progress_dialog.deleteLater()
            self.progress_dialog = None
        complete, self._completion = self._completion, None
        if error is not None:
            self._failed(error)
            return
        if not self._publishing and self.job.cancellation_requested:
            self.owner.status.setText("Operation cancelled. Draft kept.")
            self._end()
            return
        # A late cancellation does not turn a successful publication into a
        # cancellation claim. Terminal callbacks always see the real receipt.
        try:
            complete(result)
        except Exception as error:
            self._failed(error)

    def _failed(self, error):
        if isinstance(error, SessionLibraryImportUnconfirmed):
            self.owner._import_unconfirmed(error)
        elif isinstance(error, WorkspacePackagePublicationUnconfirmed):
            self.owner.status.setText("Backup publication needs checking. Keep the destination and receipt. Choose its .webjamreceipt file in Import backup to check the intended bytes before retrying.")
            self.owner.status.setToolTip(f"Destination: {error.destination}\nSHA-256: {error.expected_sha256}\nBytes: {error.expected_size}\nReceipt: {error.publication_receipt or 'not available'}")
        elif isinstance(error, WorkspacePackageCancelled):
            self.owner.status.setText("Operation cancelled. Draft kept. Check previous import if recovery is offered.")
        else:
            self.owner.status.setText(f"Workspace operation did not finish: {error}")
        self.owner._sync_import_recovery()
        self._end()

    def _show(self, prompt, complete):
        if self.closing:
            prompt.deleteLater()
            self._end()
            return
        self.prompt = prompt
        prompt.setModal(False)

        def finished(result):
            if self.prompt is not prompt:
                return
            self.prompt = None
            prompt.deleteLater()
            if self.closing:
                self._end()
            else:
                try:
                    complete(result)
                except Exception as error:
                    self._failed(error)

        prompt.finished.connect(finished)
        prompt.show()

    def backup(self, record, selection):
        if not self._begin():
            return
        record = deepcopy(record)
        self._selection = selection
        self._run("Inspect selected media", lambda progress, cancel: plan_workspace_package(
            record, selection, progress=progress, cancel_check=cancel), self._planned)

    def _planned(self, plan):
        dialog = WorkspacePackagePlanDialog(plan, self.owner)

        def chosen(result):
            if result == 2:
                self._change_choices(plan.record)
                return
            if result != QDialog.DialogCode.Accepted:
                self.owner.status.setText("Backup cancelled. Original work is unchanged.")
                self._end()
                return
            path, _ = QFileDialog.getSaveFileName(self.owner, "Create selected-media backup", "workspace.webjambackup",
                                                 "WebJam media backup (*.webjambackup)")
            if not path or self.closing:
                self._end()
                return
            self._run("Create workspace backup", lambda progress, cancel: export_workspace_package(
                plan, path, progress=progress, cancel_check=cancel), self._exported, publishing=True)

        self._show(dialog, chosen)

    def _change_choices(self, record):
        dialog = WorkspaceBackupChoicesDialog(record, self.owner, selection=self._selection)

        def chosen(result):
            if result != QDialog.DialogCode.Accepted:
                self._end()
                return
            if dialog.selected_media.isChecked():
                self._selection = dialog.selection()
                selection = self._selection
                self._run("Inspect selected media", lambda progress, cancel: plan_workspace_package(
                    record, selection, progress=progress, cancel_check=cancel), self._planned)
            else:
                from core.workspace_backup import export_workspace_backup
                path, _ = QFileDialog.getSaveFileName(self.owner, "Back up workspace…", "workspace-backup.json",
                                                     "WebJam workspace backup (*.json)")
                if path and not self.closing:
                    export_workspace_backup(record, path)
                    self.owner.status.setText("Workspace backed up. Metadata only; media files are not included.")
                self._end()

        self._show(dialog, chosen)

    def _exported(self, receipt):
        self.owner.status.setText(f"Workspace backed up with {receipt.file_count} package files. Original work is unchanged.")
        self.owner.status.setToolTip(f"{receipt.path}\nSHA-256: {receipt.sha256}\nBytes: {receipt.size_bytes}\nReceipt: {receipt.publication_receipt or 'not available'}")
        self._end()

    def import_path(self, path, *, retry=False, expected_record=None):
        if not self._begin():
            return
        expected_record = deepcopy(expected_record)
        inspect = preview_workspace_package_receipt if str(path).lower().endswith(".webjamreceipt") else preview_workspace_package
        self._run("Check workspace backup", lambda progress, cancel: inspect(
            path, progress=progress, cancel_check=cancel), lambda preview: self._previewed(
                preview, retry=retry, expected_record=expected_record))

    def _previewed(self, preview, *, retry, expected_record):
        from webjam_qt.windows.session_library import WorkspaceBackupPreviewDialog
        dialog = WorkspaceBackupPreviewDialog(self.owner.library, preview, self.owner)
        if retry:
            dialog.setWindowTitle("Resume previous import")
            dialog.confirm_button.setText("Resume same import")

        def chosen(result):
            if result != QDialog.DialogCode.Accepted:
                self.owner.status.setText("Import cancelled. Current draft kept.")
                self._end()
                return
            library = self.owner.library
            self._run("Restore selected media", lambda progress, cancel: import_workspace_package(
                library, preview, retry=retry, expected_record=expected_record,
                progress=progress, cancel_check=cancel), self._imported, publishing=True)

        self._show(dialog, chosen)

    def _imported(self, record):
        self.recovery = None
        self.owner._import_finished(record, media=True)
        self._end()

    def recover(self, pending):
        if self.active or self.job.pending:
            return
        pending = deepcopy(pending)
        intent = self.recovery if self.recovery and self.recovery[0] == pending.id else None
        if intent is not None and intent[1] == "partial":
            path, _ = QFileDialog.getOpenFileName(self.owner, "Choose original backup to resume", "",
                                                 "WebJam media backup (*.webjambackup *.webjamreceipt)")
            if path and not self.closing:
                self.import_path(path, retry=True, expected_record=pending)
            return
        if not self._begin():
            return
        retry = intent is not None and intent[1] in {"ready", "published"}
        library = self.owner.library
        self._run("Check previous import", lambda progress, cancel: reconcile_workspace_package_import(
            library, retry=retry, expected_record=pending, progress=progress, cancel_check=cancel),
            self._imported if retry else self._reconciled, publishing=retry)

    def _reconciled(self, result):
        self.recovery = (result.record.id, result.state)
        self.owner._sync_import_recovery()
        self.owner.status.setText(("Restore is incomplete. Choose original backup to resume the same import. "
                                   if result.state == "partial" else "Stored media checked. Choose Retry same import to finish. ")
                                  + "Current draft kept.")
        self.owner.import_backup_button.setToolTip(result.detail)
        self._end()
