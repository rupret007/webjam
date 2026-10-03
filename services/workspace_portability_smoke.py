"""Owned-data portable Music/Art proof shared by source and frozen gates.

Source tests inject the production controlled recorder at the external audio
boundary. Frozen builds use clearly labeled synthetic completed takes. Neither
mode opens a socket, sound device, browser or editor. All backup/import/media
gestures use real Library controls, workers and publication paths.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import builtins
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf
from PySide6.QtCore import QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog

from core.export_receipt import verify_export_receipt
from core.settings import load_settings, save_settings
from core.studio_comping import add_take_lane, select_lane_range
from core.studio_export import studio_export_supported
from core.studio_store import load_studio_document, save_studio_document
from core.take_library import load_take
from core.take_project import load_take_project
from core.take_review import load_take_review, take_source_identity
from services.session_workspace_packaged_smoke import (
    _MemorySink, _ReferenceReceiver, _click, _require, _take, _wait,
)
from services.workflow_continuity_packaged_smoke import (
    _application, _close_workspace, _isolated_runtime, _replace,
)
from webjam_qt import app as app_module
from webjam_qt.widgets import recording_studio as studio_ui
from webjam_qt.windows.launch_dialog import LaunchDialog
from webjam_qt.windows.session_library import SessionLibraryDialog, WorkspaceBackupPreviewDialog
from webjam_qt.windows.workspace_backup import WorkspaceBackupChoicesDialog, WorkspacePackagePlanDialog

SUCCESS_MARKER = "WebJam portable Music and Art workflow smoke passed"


def _hashes(root, paths):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def _assert_hashes(root, expected):
    _require(_hashes(root, [root / name for name in expected]) == expected,
             "original or restored bytes changed")


def _facts(record):
    return {key: deepcopy(getattr(record, key)) for key in
            ("id", "title", "notes", "take_links", "rehearsal", "art", "import_provenance", "media_provenance")}


def _assert_imported_context(source, restored):
    """Compare against saved source data, allowing only included-media moves."""
    _require(restored.title == source["title"] and restored.notes == source["notes"],
             "import lost workspace title or notes")
    rehearsal = deepcopy(source["rehearsal"])
    paths = {(ref["take_id"], ref["source_identity"]): ref["take_path"] for ref in restored.take_links}
    for song in rehearsal.get("songs", []):
        for bookmark in song["bookmarks"]:
            key = (bookmark.get("take_id"), bookmark.get("source_identity"))
            if key in paths:
                bookmark["take_path"] = paths[key]
    _require(restored.rehearsal == rehearsal, "import changed song fields, drafts or moments")
    art = deepcopy(source["art"])
    references = {ref["id"]: ref for ref in restored.art.get("references", [])}
    for reference in art.get("references", []):
        _require(reference["id"] in references, "import lost an Art reference")
        if reference["kind"] == "file":
            reference["locator"] = references[reference["id"]]["locator"]
    _require(restored.art == art, "import changed Art brief, progress, next steps, references or bookmarks")


@contextmanager
def _passive_media_guard(media_root):
    """Fail on filesystem access to imported assets before a deliberate action."""
    attempts = []

    def guarded(function):
        def call(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                target = Path(os.path.abspath(os.fsdecode(path)))
                if target.is_relative_to(media_root):
                    attempts.append(function.__name__)
                    raise RuntimeError("Passive restart accessed imported media")
            return function(path, *args, **kwargs)
        return call

    with ExitStack() as patches:
        for owner, names in ((os, ("stat", "lstat", "open", "scandir")),
                             (builtins, ("open",)), (io, ("open",))):
            for name in names:
                patches.enter_context(_replace(owner, name, guarded(getattr(owner, name))))
        yield attempts


@contextmanager
def _workspace(app, settings_path, launch=None):
    sink = _MemorySink()
    settings = load_settings(str(settings_path))
    proof_root = settings_path.parent.parent
    for value in (settings.config_file, settings.mix_file, settings.log_file,
                  settings.server_rpc_secret_file, settings.takes_directory):
        _require(Path(value).resolve().is_relative_to(proof_root), "smoke settings escaped the owned proof root")
    # Only the external output device is replaced. Real playback validation,
    # source catalog, renderer and pull path remain in use.
    with _replace(studio_ui, "SoundDeviceSink", lambda: sink):
        window, controller = app_module._create_workspace(app, settings, launch)
    try:
        _require(Path(controller.repository.db_path).resolve().is_relative_to(settings_path.parent),
                 "smoke repository escaped the owned workspace root")
        app.processEvents()
        yield window, controller, sink
    finally:
        studio = window.recording_studio
        dialog = controller.session_library.dialog
        if dialog is not None and dialog.media_operation_pending:
            dialog.prepare_close()
            _wait(app, lambda: not dialog.media_operation_pending, "Library worker did not retire")
        if studio.media_open_pending:
            studio.prepare_close()
            _wait(app, lambda: not studio.media_open_pending, "comparison worker did not retire")
        _close_workspace(app, window, controller)


def _backup(app, editor, destination, *, take_ids=(), art_ids=()):
    errors = []

    def choose():
        choices = editor.findChild(WorkspaceBackupChoicesDialog)
        try:
            _require(choices is not None and choices.metadata_only.isChecked(), "metadata-only default changed")
            choices.selected_media.setChecked(True)
            selected = set()
            for candidate, check in choices.candidates:
                wanted = candidate.reference_id in (take_ids if candidate.kind == "take" else art_ids)
                if wanted:
                    _require(candidate.selectable, "completed source was excluded from backup")
                    check.setChecked(True)
                    selected.add(candidate.reference_id)
            _require(selected == set(take_ids) | set(art_ids), "backup choices lost a requested source")
            choices.confirm_button.click()
        except Exception as error:
            errors.append(error)
            if choices is not None:
                choices.reject()

    with _replace(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(destination), "")):
        QTimer.singleShot(0, choose)
        editor.backup_button.click()
        if errors:
            raise errors[0]
        _wait(app, lambda: editor.workspace_flow.prompt is not None or not editor.media_operation_pending,
              "backup plan did not finish")
        plan = editor.workspace_flow.prompt
        _require(isinstance(plan, WorkspacePackagePlanDialog) and plan.confirm_button.isEnabled(),
                 "selected media plan is unavailable or blocked")
        plan.confirm_button.click()
        _wait(app, lambda: not editor.media_operation_pending, "backup worker did not finish")
    _require(destination.is_file() and "backed up" in editor.status.text(), "backup was not published")
    receipts = []
    for path in destination.parent.glob("*.webjamreceipt"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if destination.name in value.values():
            receipts.append(path)
    _require(len(receipts) == 1, "backup has no unique durable publication receipt")
    return receipts[0]


def _import(app, editor, path):
    before = {record.id for record in editor.library.list()}
    draft = editor._edited_record()
    with _replace(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(path), "")):
        editor.import_backup_button.click()
        _wait(app, lambda: editor.workspace_flow.prompt is not None or not editor.media_operation_pending,
              "import preview did not finish")
        preview = editor.workspace_flow.prompt
        _require(isinstance(preview, WorkspaceBackupPreviewDialog), "import preview is unavailable")
        preview.confirm_button.click()
        _wait(app, lambda: not editor.media_operation_pending, "import worker did not finish")
    added = [record for record in editor.library.list() if record.id not in before]
    _require(len(added) == 1, "import did not publish exactly one fresh workspace")
    _require(editor._edited_record() == draft and editor._dirty,
             "import changed selection or the current unsaved draft")
    return added[0]


def _export(app, studio):
    outcomes = []
    studio.export_finished.connect(outcomes.append)
    try:
        studio._open_take_review()
        _require(not studio._review_dialog.receipt_button.isEnabled(), "historical export became a new receipt")
        studio._review_dialog.export_button.click()
        _wait(app, lambda: bool(outcomes), "explicit restored export did not finish", timeout=20)
        _require(outcomes == [True], "explicit restored export failed")
        receipt = verify_export_receipt(studio._reveal_path)
        _require(studio._current.take_id in receipt.details and str(receipt.folder) in receipt.details,
                 "export receipt lost its exact take or destination")
        return receipt
    finally:
        studio.export_finished.disconnect(outcomes.append)


def _prepare_source(app, root, record_takes):
    source_root = root / "Source"
    packages = root / "Backups"
    packages.mkdir(mode=0o700)
    with _isolated_runtime(source_root) as (library, settings_path, starts):
        record = library.create("music", "Portable rehearsal", notes="Decision: Keep both recordings.")
        launch = _launch_saved(app, settings_path, record.id)
        with _workspace(app, settings_path, launch) as (window, controller, _sink):
            launch.deleteLater()
            _require(controller.session_library.continue_record(record), "source rehearsal could not continue")
            if record_takes is not None:
                takes, recording = record_takes(app, controller, source_root)
            else:
                first = _take(source_root / "Takes", "Take A", 330)
                second = _take(source_root / "Takes", "Take B", 440, session_id=load_take(first).session_id)
                takes = [first, second]
                links = tuple({"take_id": load_take(path).take_id, "take_path": str(path),
                               "source_identity": take_source_identity(load_take(path))} for path in takes)
                record = library.save(replace(record, take_links=links))
                controller.session_library.current = record
                controller.session_library._record_saved(record)
                recording = {"fixture_takes": 2, "production_publications": 0, "physical_audio": "not_run"}
            studio = window.recording_studio
            studio._new_take_btn.click()
            _require(studio._viewing_live, "source Studio draft could not be saved before preparing its comp")
            # A declared alternate is required: leaving original same-ID
            # copies in the global browser must never affect restored comping.
            primary, alternate = load_take_project(takes[1]), load_take_project(takes[0])
            _require(primary.session_id == alternate.session_id, "recorded takes do not share a session")
            stored = load_studio_document(takes[1])
            document = stored.document
            if not any(lane.source_take_id == alternate.take_id and not lane.deleted for lane in document.take_lanes):
                document = add_take_lane(document, primary, alternate,
                                         destination_track_id=primary.tracks[0].track_id)
            lane = next(lane for lane in document.take_lanes if lane.source_take_id == alternate.take_id and not lane.deleted)
            document = select_lane_range(document, lane.lane_id, 1024, 4096)
            save_studio_document(takes[1], document, expected_token=stored.token)
            reviews = []
            for index, path in enumerate(takes):
                _require(studio.open_take(path), "source recording could not open")
                studio._open_take_review()
                review = studio._review_dialog
                review.favorite.setChecked(index == 0)
                review.notes.setPlainText(f"Independent review {index + 1}")
                review.save_button.click()
                saved = load_take_review(studio._current)
                reviews.append({"favorite": saved.favorite, "notes": saved.notes,
                                "take_id": studio._current.take_id,
                                "source_identity": take_source_identity(studio._current)})
            original_export = _export(app, studio)
            _require(studio.jump_to_bookmark(takes[0], .05), "source moment did not verify")
            controller.session_library.show(tab="plan")
            editor = controller.session_library.dialog
            panel = editor.rehearsal
            panel.add_song("Opening song")
            panel._key.setText("A minor")
            panel._tempo.setValue(112)
            panel._notes.setPlainText("Opening notes")
            panel._goals.setPlainText("Keep the two entrances distinct")
            panel._next_steps.setPlainText("Compare the restored recordings")
            panel.add_bookmark("Plain thought")
            panel.add_bookmark("Verified entrance", **studio.current_take_reference(), timing_verified=True)
            panel._moment_note.setText("Unsubmitted moment survives")
            panel.add_song("Closing song")
            panel._notes.setPlainText("Closing notes")
            panel._goals.setPlainText("Finish together")
            panel._next_steps.setPlainText("Review the last chord")
            _click(panel, "Previous")
            _require(editor.save_current(), "source rehearsal did not save")
            music = library.load(record.id)
            take_ids = tuple(ref["take_id"] for ref in music.take_links)
            source_hashes = _hashes(root, [path for take in takes for path in take.rglob("*") if path.is_file()])
            studio_documents = {load_take(path).take_id: load_studio_document(path).document.to_dict() for path in takes}
            music_receipt = _backup(app, editor, packages / "music.webjambackup", take_ids=take_ids)
            _assert_hashes(root, source_hashes)

            art_record = library.create("art", "Portable painting")
            editor.refresh()
            editor.select_id(art_record.id)
            _require(editor.record.id == art_record.id, "new Art workspace was not selected")
            art_file = source_root / "Painting.svg"
            art_file.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
                                '<rect width="32" height="32" fill="#3266b0"/></svg>\n', encoding="utf-8")
            art = editor.art
            art.brief.setPlainText("Paint a quiet blue study")
            art.progress.setPlainText("First layer complete")
            art.next_steps.setPlainText("Refine the edge")
            art.add_reference(str(art_file), kind="file", title="Owned painting")
            art.position.setValue(12.5)
            art.bookmark_note.setText("Edge technique")
            _click(art, "Save lesson bookmark")
            _require(editor.save_current(), "source Art project did not save")
            art_record = library.load(art_record.id)
            art_ids = tuple(ref["id"] for ref in art_record.art["references"])
            source_hashes.update(_hashes(root, [art_file]))
            art_receipt = _backup(app, editor, packages / "art.webjambackup", art_ids=art_ids)
            _assert_hashes(root, source_hashes)
            _require(not any(starts.values()), "creating source work started a live integration")
            return {"music": _facts(music), "art": _facts(art_record), "reviews": reviews,
                    "recording": recording, "source_hashes": source_hashes, "studio_documents": studio_documents,
                    "takes_directory": str(source_root / "Takes"),
                    "original_export": str(original_export.folder),
                    "packages": [str(path) for path in (music_receipt, art_receipt)]}


def prepare_portability(root: Path, *, record_takes=None):
    app = _application()
    root = Path(root).resolve()
    source = _prepare_source(app, root, record_takes)
    with _isolated_runtime(root / "Destination") as (library, settings_path, starts):
        settings = load_settings(str(settings_path))
        settings.takes_directory = source["takes_directory"]
        save_settings(settings)
        owner = library.create("music", "Retained live owner", notes="Saved owner notes")
        launch = _launch_saved(app, settings_path, owner.id)
        with _workspace(app, settings_path, launch) as (window, controller, _sink):
            launch.deleteLater()
            _require(controller.session_library.continue_record(owner), "destination owner did not open")
            controller.session_library.show()
            editor = controller.session_library.dialog
            editor.notes.setPlainText("Unsaved owner draft stays here during every import")
            music = _import(app, editor, source["packages"][0])
            art = _import(app, editor, source["packages"][1])
            duplicate = _import(app, editor, source["packages"][0])
            _require(len({music.id, art.id, duplicate.id, owner.id}) == 4, "duplicate import reused a workspace identity")
            _require(controller.session_library.current.id == owner.id, "import changed runtime ownership")
            _require(not window.recording_studio._player.is_playing and not any(starts.values()),
                     "import started playback or an integration")
            _require(editor.save_current(), "retained owner draft did not save after imports")
            _require(music.id != source["music"]["id"] and art.id != source["art"]["id"], "import reused a source ID")
            _require(music.import_provenance and art.import_provenance, "import lost historical provenance")
            _require([r["take_id"] for r in music.take_links] == [r["take_id"] for r in source["music"]["take_links"]],
                     "import changed take identities")
            for before, after in ((source["music"], music), (source["music"], duplicate), (source["art"], art)):
                _assert_imported_context(before, after)
            roots = [Path(ref["take_path"]) for ref in music.take_links]
            _require(all(path.is_relative_to(library.root) for path in roots), "restored take escaped destination")
            _require({r["take_path"] for r in music.take_links}.isdisjoint(r["take_path"] for r in duplicate.take_links),
                     "duplicate imports share mutable take folders")
            art_path = Path(art.art["references"][0]["locator"])
            restored_files = [p for folder in roots for p in folder.rglob("*")
                              if p.is_file() and (p.suffix == ".wav" or p.name == "webjam-take.json")] + [art_path]
            for old, new in zip(source["music"]["take_links"], music.take_links, strict=True):
                old_root, new_root = Path(old["take_path"]), Path(new["take_path"])
                for path in new_root.rglob("*"):
                    if path.is_file() and (path.suffix == ".wav" or path.name == "webjam-take.json"):
                        original = old_root / path.relative_to(new_root)
                        _require(hashlib.sha256(path.read_bytes()).hexdigest() == source["source_hashes"][str(original.relative_to(root))],
                                 "restored recording differs from its pre-backup bytes")
            original_art = Path(source["art"]["art"]["references"][0]["locator"])
            _require(hashlib.sha256(art_path.read_bytes()).hexdigest() == source["source_hashes"][str(original_art.relative_to(root))],
                     "restored artwork differs from its pre-backup bytes")
            source.update(imported_music=_facts(music), imported_art=_facts(art),
                          duplicate_id=duplicate.id, restored_hashes=_hashes(root, restored_files), writer_pid=os.getpid())
    _assert_hashes(root, source["source_hashes"])
    (root / "portability-expected.json").write_text(json.dumps(source, sort_keys=True), encoding="utf-8")
    return {"phase": "prepared", "pid": os.getpid(), "imported_workspaces": 3, "duplicate_ids_distinct": True,
            "draft_and_owner_retained": True, **source["recording"]}


def _launch_saved(app, settings_path, workspace_id, *, take_id=None):
    launch = LaunchDialog(load_settings(str(settings_path)))
    errors = []

    def select():
        editor = next((w for w in QApplication.topLevelWidgets()
                       if isinstance(w, SessionLibraryDialog) and w.isVisible()), None)
        try:
            _require(editor is not None, "launch library is unavailable")
            editor.select_id(workspace_id)
            _require(editor.record.id == workspace_id, "launch selected a different workspace")
            if take_id is None:
                editor.continue_button.click()
            else:
                editor.tabs.setCurrentIndex(4)
                row = next(i for i, ref in enumerate(editor.record.take_links) if ref["take_id"] == take_id)
                editor.takes.setCurrentRow(row)
                editor.open_take_button.click()
                _wait(app, lambda: not editor.media_operation_pending, "launch take verification did not finish")
                app.processEvents()
                _require(editor.selected_record is not None, "verified launch Open did not accept its workspace")
        except Exception as error:
            errors.append(error)
            if editor is not None:
                editor.reject()

    QTimer.singleShot(0, select)
    launch._session_library_action.trigger()
    if errors:
        raise errors[0]
    _require(launch.selected_workspace_id == workspace_id and launch.selected_role == "library",
             "Continue this work did not select saved work")
    return launch


def _open_imported(app, controller, reference):
    coordinator = controller.session_library
    coordinator.show()
    editor = coordinator.dialog
    row = next(i for i, ref in enumerate(editor.record.take_links) if ref["take_id"] == reference["take_id"])
    editor.tabs.setCurrentIndex(4)
    editor.takes.setCurrentRow(row)
    _require("stored link" in editor.takes.item(row).text(), "restored path retained a permanent verified claim")
    editor.verify_take_button.click()
    _wait(app, lambda: not editor.media_operation_pending, "explicit take verification did not finish")
    _require("Content matched when checked" in editor.takes.item(row).text(), "restored take did not verify")
    _require(not controller.window.recording_studio._player.is_playing, "Verify started playback")
    editor.open_take_button.click()
    _wait(app, lambda: not editor.media_operation_pending, "explicit take open did not finish")
    studio = controller.window.recording_studio
    _wait(app, lambda: coordinator.dialog is None, "exact originating Library did not close")
    _require(studio._current.take_id == reference["take_id"]
             and studio._current.path == Path(reference["take_path"])
             and take_source_identity(studio._current) == reference["source_identity"], "Open substituted another take")
    _require(not studio._player.is_playing and not studio._review_dialog.receipt_button.isEnabled(),
             "Open replayed playback or historical export completion")
    return studio


def resume_portability(root: Path, *, require_fresh_process=True):
    root = Path(root).resolve()
    expected = json.loads((root / "portability-expected.json").read_text(encoding="utf-8"))
    if require_fresh_process:
        _require(os.getpid() != expected["writer_pid"], "restart inherited its writer process")
    app = _application()
    receiver = _ReferenceReceiver()
    QDesktopServices.setUrlHandler("file", receiver, "open_reference")
    try:
        with _isolated_runtime(root / "Destination") as (library, settings_path, starts), ExitStack() as passive:
            probes = passive.enter_context(_passive_media_guard(library.root / "media"))
            launch = _launch_saved(app, settings_path, expected["imported_music"]["id"])
            with _workspace(app, settings_path, launch) as (window, controller, sink):
                launch.deleteLater()
                _require(not any(starts.values()) and not receiver.urls and not controller.recording.is_recording_active,
                         "restart started live work or an external application")
                music = library.load(expected["imported_music"]["id"])
                art = library.load(expected["imported_art"]["id"])
                for record, key in ((music, "imported_music"), (art, "imported_art")):
                    _require(json.loads(json.dumps(_facts(record))) == expected[key], "restart changed imported context")
                _require(controller.session_library.current_take_status() is None,
                         "historical completed take became a new recording")
                studio = window.recording_studio
                originals = {str(Path(ref["take_path"])) for ref in expected["music"]["take_links"]}
                _require(originals.issubset(str(t.path) for t in studio._takes), "same-ID originals are absent from global browser")
                passive.close()
                _require(not probes, "passive imported media probes occurred before explicit verification")
                for index, reference in enumerate(music.take_links):
                    _open_imported(app, controller, reference)
                    saved = expected["reviews"][index]
                    _require(studio._review_dialog.notes.toPlainText() == saved["notes"]
                             and studio._review_dialog.favorite.isChecked() == saved["favorite"], "restored review changed")
                    _require(studio._studio_controller.document.to_dict() == expected["studio_documents"][reference["take_id"]],
                             "restored Studio choices differ from the saved comp")
                    studio._open_take_review()
                    _click(studio._review_dialog, "Set " + ("A" if index == 0 else "B"))
                    for take_id in studio._studio_source_catalog.take_ids:
                        ref = next(r for r in music.take_links if r["take_id"] == take_id)
                        _require(studio._studio_source_catalog.root_for_take(take_id) == Path(ref["take_path"]),
                                 "restored arrangement used an original dependency")
                _require(len(studio._studio_source_catalog.take_ids) == 2, "declared alternate did not survive restore")
                rendered = []
                for slot, reference in zip(("A", "B"), music.take_links, strict=True):
                    epoch, starts_before = studio._player._playback_epoch, sink.starts
                    _click(studio._review_dialog, f"Listen {slot}")
                    _wait(app, lambda: (not studio.media_open_pending and studio._player.is_playing
                          and studio._current.path == Path(reference["take_path"])
                          and studio._player._playback_epoch != epoch and sink.starts > starts_before),
                          "comparison did not start the requested restored take")
                    samples = sink.pull(512)
                    _require(samples.shape == (512, 2) and np.all(np.isfinite(samples))
                             and float(np.max(np.abs(samples))) > .001, "restored comparison rendered silence or invalid samples")
                    rendered.append(samples.copy())
                    studio._stop_playback()
                _require(not np.allclose(*rendered), "distinct restored takes rendered identical audio")
                studio._review_dialog.notes.setPlainText("Review continued after restore")
                studio._review_dialog.save_button.click()
                _require(load_take_review(studio._current).notes == "Review continued after restore", "restored review cannot be edited")
                receipt = _export(app, studio)
                _require(receipt.folder.is_relative_to(library.root)
                         and str(receipt.folder) != expected["original_export"], "restored export reused the source destination")
                export_hashes = _hashes(root, [p for p in receipt.folder.rglob("*") if p.is_file()])
                if studio_export_supported():
                    provenance = json.loads((receipt.folder / "provenance.json").read_text(encoding="utf-8"))
                    _require({item["take_id"]: item["sha256"] for item in provenance["take_manifests"]}
                             == {ref["take_id"]: ref["source_identity"] for ref in music.take_links},
                             "Studio export lost the declared take identities")
                    _require(json.loads((receipt.folder / "studio-document.json").read_text(encoding="utf-8"))
                             == studio._studio_controller.document.to_dict(), "Studio export lost saved arrangement choices")
                    _require({item["source_key"]["take_id"] for item in provenance["sources"]}
                             == {ref["take_id"] for ref in music.take_links}, "Studio export did not retain both recorded sources")
                    export_kind = "studio_arrangement"
                else:
                    evidence = json.loads((receipt.folder / "webjam-track-export.json").read_text(encoding="utf-8"))
                    _require(evidence["source_take_id"] == studio._current.take_id,
                             "aligned-originals export substituted another take")
                    export_kind = "aligned_originals"
                audio_outputs = list(receipt.folder.rglob("*.wav"))
                _require(audio_outputs, "restored export has no audio")
                for path in audio_outputs:
                    samples, rate = sf.read(path, dtype="float32", always_2d=True)
                    _require(rate == 48000 and len(samples) > 0 and np.all(np.isfinite(samples))
                             and float(np.max(np.abs(samples))) > .001, "exported audio is silent or invalid")
                _require(controller.settings.takes_directory == expected["takes_directory"], "restored Open changed recording destination")
                _require(controller.session_library.continue_record(art), "restored Art project could not continue")
                _require(not receiver.urls, "Art continuation opened a reference")
                controller.session_library.show(tab="plan")
                editor = controller.session_library.dialog
                panel = editor.art
                _require(panel.payload() == art.art, "Art UI lost restored context")
                panel.references.setCurrentRow(0)
                panel.bookmarks.setCurrentRow(0)
                _require(panel.position.value() == 12.5, "Art bookmark position changed")
                panel.brief.setPlainText("Continue the restored painting")
                panel.bookmark_note.setText("Unsubmitted edge note")
                panel.verify_button.click()
                _wait(app, lambda: not editor.media_operation_pending, "Art verification did not finish")
                _require(not receiver.urls and editor._dirty and "Content matched" in panel.status.text(),
                         "Art Verify lost draft, launched an app or failed")
                _click(panel, "Open reference")
                _wait(app, lambda: not editor.media_operation_pending, "Art Open did not finish")
                old_path = Path(art.art["references"][0]["locator"])
                _require([Path(p) for p in receiver.urls] == [old_path], "Art Open selected a different file")
                moved = root / "Destination" / "Moved painting.svg"
                old_path.rename(moved)
                with _replace(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(moved), "")):
                    _click(panel, "Relink…")
                    _wait(app, lambda: not editor.media_operation_pending, "Art Relink did not finish")
                saved_art = library.load(art.id)
                _require(saved_art.art["references"][0]["locator"] == str(moved)
                         and saved_art.art["bookmarks"] == art.art["bookmarks"]
                         and panel.brief.toPlainText() == "Continue the restored painting"
                         and panel.bookmark_note.text() == "Unsubmitted edge note", "Art Relink lost identity or drafts")
                _require(len(receiver.urls) == 1, "Relink launched an external app")
                expected["restored_hashes"][str(moved.relative_to(root))] = expected["restored_hashes"].pop(str(old_path.relative_to(root)))
                _assert_hashes(root, expected["restored_hashes"])
                _assert_hashes(root, expected["source_hashes"])
                _assert_hashes(root, export_hashes)
                _require(verify_export_receipt(receipt.folder).file_count == receipt.file_count, "export changed after Art work")
                _require(not any(starts.values()) and not controller.recording.is_recording_active,
                         "resume started live work")
                return {"phase": "resumed", "pid": os.getpid(), "writer_pid": expected["writer_pid"],
                        "fresh_process": os.getpid() != expected["writer_pid"],
                        "rendered_distinct_takes": 2, "declared_sources": 2, "verified_exports": 1,
                        "export_kind": export_kind,
                        "explicit_art_opens": 1, "art_relinked": True, "originals_unchanged": True,
                        "restored_media_unchanged": True, "automatic_startup": False,
                        "passive_media_probes": len(probes),
                        "physical_audio": "not_run", "recording": expected["recording"]}
    finally:
        QDesktopServices.unsetUrlHandler("file")


def check_portable_launch(root):
    """Use the initial Library Open gesture, without an intermediate Continue."""
    root = Path(root).resolve()
    expected = json.loads((root / "portability-expected.json").read_text(encoding="utf-8"))
    app = _application()
    with _isolated_runtime(root / "Destination") as (library, settings_path, starts):
        record = library.load(expected["imported_music"]["id"])
        reference = record.take_links[-1]
        launch = _launch_saved(app, settings_path, record.id, take_id=reference["take_id"])
        with _workspace(app, settings_path, launch) as (window, controller, _sink):
            launch.deleteLater()
            studio = window.recording_studio
            _require(controller.session_library.current.id == record.id
                     and studio._current.path == Path(reference["take_path"]), "launch Open changed workspace or take")
            _require(launch.selected_library_open is None, "verified launch intent was not consumed")
            _require(not studio._player.is_playing and not any(starts.values()), "launch Open started live work")
            _require(len(studio._studio_source_catalog.take_ids) == 2, "launch Open lost its declared alternate")
            for take_id in studio._studio_source_catalog.take_ids:
                ref = next(item for item in record.take_links if item["take_id"] == take_id)
                _require(studio._studio_source_catalog.root_for_take(take_id) == Path(ref["take_path"]),
                         "launch Open substituted an original same-ID source")
            _assert_hashes(root, expected["source_hashes"])
    return {"initial_library_take_open": True}


def run_workspace_portability_smoke():
    with tempfile.TemporaryDirectory(prefix="webjam-portability-smoke-") as directory:
        root = Path(directory).resolve()
        prepare_portability(root)
        result = resume_portability(root, require_fresh_process=False)
        result.update(check_portable_launch(root))
        return result
