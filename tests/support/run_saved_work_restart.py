"""Save and reopen one synthetic rehearsal in genuinely separate processes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.export_receipt import verify_export_receipt
from core.settings import load_settings
from core.take_library import load_take
from core.take_review import load_take_review, take_source_identity
from services.session_workspace_packaged_smoke import _click, _require, _wait
from services.workflow_continuity_packaged_smoke import (
    _application, _close_workspace, _isolated_runtime,
)
from tests.support.controlled_recording_journey import record_completed_takes
from webjam_qt import app as app_module
from webjam_qt.windows.launch_dialog import LaunchDialog
from webjam_qt.windows.session_library import SessionLibraryDialog


def _hashes(root, paths):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def _save(root):
    app = _application()
    with _isolated_runtime(root) as (library, settings_path, _starts):
        window, controller = app_module._create_workspace(app, load_settings(str(settings_path)), None)
        try:
            app.processEvents()
            record = library.create("music", "Restart rehearsal", notes="Decision: Keep both takes.")
            _require(controller.session_library.continue_record(record), "initial rehearsal did not open")
            takes, recording_proof = record_completed_takes(app, controller, root)
            originals = _hashes(root, [file for take in takes for file in take.rglob("*")
                                      if file.is_file() and (file.suffix == ".wav" or file.name == "webjam-take.json")])
            studio = window.recording_studio
            expected_reviews = []
            for index, path in enumerate(takes):
                take = load_take(path)
                _require(studio.open_take(path), "generated take did not verify")
                studio._open_take_review()
                review = studio._review_dialog
                review.favorite.setChecked(index == 0)
                review.notes.setPlainText(f"Independent review for take {index + 1}.")
                review.save_button.click()
                saved = load_take_review(take)
                expected_reviews.append({"favorite": saved.favorite, "notes": saved.notes,
                                         "take_id": take.take_id, "source_identity": take_source_identity(take)})
            _require(studio.open_take(takes[0]), "first take could not be selected")
            outcomes = []
            studio.export_finished.connect(outcomes.append)
            studio._review_dialog.export_button.click()
            _wait(app, lambda: bool(outcomes), "review export did not finish", timeout=20)
            _require(outcomes == [True] and studio._review_dialog.receipt_button.isEnabled(),
                     "recorded take did not produce a verified export receipt")
            receipt = verify_export_receipt(studio._reveal_path)
            _require(load_take(takes[0]).take_id in receipt.details and str(receipt.folder) in receipt.details,
                     "export receipt lost its exact take or destination")
            export_hashes = _hashes(root, [file for file in receipt.folder.rglob("*") if file.is_file()])
            _require(studio.jump_to_bookmark(takes[0], 0.05), "synthetic take position did not verify")
            reference = studio.current_take_reference()
            controller.session_library.show(tab="plan")
            editor = controller.session_library.dialog
            panel = editor.rehearsal
            panel.add_song("Opening song")
            panel._key.setText("A minor")
            panel._tempo.setValue(112)
            panel._notes.setPlainText("Keep the opening draft.")
            panel.add_bookmark("Plain rehearsal thought")
            panel.add_bookmark("Verified entrance", **reference, timing_verified=True)
            panel.add_song("Closing song")
            panel._notes.setPlainText("Keep the closing draft.")
            _click(panel, "Previous")
            _require(panel._notes.toPlainText() == "Keep the opening draft.", "song switch lost the draft")
            _require(editor.save_current(), "rehearsal editor did not save")
            editor.reject()
            _require(controller._save_notes() and controller.session_library.flush(), "notes did not save")
            saved = library.load(record.id)
            expected = {"workspace_id": saved.id, "title": saved.title, "notes": saved.notes,
                        "rehearsal": saved.rehearsal, "take_links": saved.take_links,
                        "reviews": expected_reviews, "originals": originals,
                        "export_folder": str(receipt.folder.relative_to(root)),
                        "export_hashes": export_hashes, "export_files": receipt.file_count,
                        "recording_proof": recording_proof,
                        "writer_pid": os.getpid()}
            (root / "expected.json").write_text(json.dumps(expected, sort_keys=True), encoding="utf-8")
            _require(_hashes(root, [root / name for name in originals]) == originals,
                     "review/export changed original media or published manifests")
            return {"phase": "saved", "pid": os.getpid(), "takes": 2, "songs": 2,
                    **recording_proof, "verified_exports": 1}
        finally:
            _close_workspace(app, window, controller)


def _select_library_from_launch(launch, workspace_id):
    errors = []

    def select():
        editor = next((widget for widget in QApplication.topLevelWidgets()
                       if isinstance(widget, SessionLibraryDialog) and widget.isVisible()), None)
        try:
            _require(editor is not None, "launch did not open its Session library")
            editor.select_id(workspace_id)
            _require(editor.record.id == workspace_id, "launch library selected the wrong workspace")
            editor.continue_button.click()
        except Exception as error:
            errors.append(error)
            if editor is not None:
                editor.reject()

    QTimer.singleShot(0, select)
    launch._session_library_action.trigger()
    if errors:
        raise errors[0]
    _require(launch.selected_role == "library" and launch.selected_workspace_id == workspace_id,
             "Continue this work did not select the saved library route")


def _reopen(root):
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    _require(os.getpid() != expected["writer_pid"], "restart did not use a fresh process")
    app = _application()
    with _isolated_runtime(root) as (library, settings_path, starts):
        launch = LaunchDialog(load_settings(str(settings_path)))
        _select_library_from_launch(launch, expected["workspace_id"])
        window, controller = app_module._create_workspace(app, load_settings(str(settings_path)), launch)
        launch.deleteLater()
        try:
            app.processEvents()
            _require(not any(starts.values()), "reopening saved work scheduled an integration or live startup")
            _require(controller.creator_profile.key == "music", "restart changed creator profile")
            _require(window.session_strip.current_title() == expected["title"], "restart lost the title")
            _require(window.session_canvas.current_notes() == expected["notes"], "restart lost notes")
            record = library.load(expected["workspace_id"])
            _require(record.rehearsal == expected["rehearsal"], "restart changed song drafts or bookmarks")
            _require(list(record.take_links) == expected["take_links"], "restart changed linked take identities")
            _require(controller.session_library.current_take_status() is None,
                     "an older take appeared as a new session recording")
            controller.session_library.show(tab="plan")
            editor = controller.session_library.dialog
            _require(editor.rehearsal.payload() == expected["rehearsal"], "restart UI lost rehearsal state")
            editor.reject()
            identities = []
            for ref, review in zip(record.take_links, expected["reviews"], strict=True):
                controller.session_library.open_take(ref)
                studio = window.recording_studio
                _require(studio._current is not None and studio._current.take_id == review["take_id"],
                         "reopened Studio substituted another take")
                _require(take_source_identity(studio._current) == review["source_identity"],
                         "reopened source identity changed")
                studio._open_take_review()
                _require(studio._review_dialog.favorite.isChecked() == review["favorite"]
                         and studio._review_dialog.notes.toPlainText() == review["notes"],
                         "review state did not follow its exact take")
                _require(not studio._player.is_playing and not controller.recording.is_recording_active,
                         "saved-work reopening started playback or recording")
                identities.append(studio._current.take_id)
            _require(len(set(identities)) == 2, "two take identities collapsed")
            _require(_hashes(root, [root / name for name in expected["originals"]]) == expected["originals"],
                     "restart changed original media or manifests")
            receipt = verify_export_receipt(root / expected["export_folder"])
            _require(receipt.file_count == expected["export_files"]
                     and expected["reviews"][0]["take_id"] in receipt.details,
                     "restart lost verified export provenance")
            _require(_hashes(root, [root / name for name in expected["export_hashes"]]) == expected["export_hashes"],
                     "restart changed exported bytes")
            return {"phase": "reopened", "pid": os.getpid(), "writer_pid": expected["writer_pid"],
                    "takes": 2, "songs": 2, "reviews": 2, "notes_retained": True,
                    "originals_unchanged": True, "older_take_is_not_new": True,
                    "automatic_startup": False, "physical_audio": "not_run",
                    "verified_exports": 1, "production_publications": 2}
        finally:
            _close_workspace(app, window, controller)


if __name__ == "__main__":
    phase, directory = sys.argv[1:]
    root = Path(directory).resolve()
    _require(root.is_dir() and not root.is_symlink(), "private fixture directory is missing")
    action = {"save": _save, "reopen": _reopen}[phase]
    print(json.dumps(action(root), sort_keys=True))
