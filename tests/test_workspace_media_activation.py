"""Restored content opens deliberately with its exact sources and worker owner."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import shutil
from threading import Event, get_ident

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QInputDialog

from core.creative_modes import get_creator_profile_by_key
from core import workspace_media_backup as media
from core.session_library import SessionLibrary
from core.studio_comping import add_take_lane
from core.studio_store import load_studio_document, save_studio_document
from core.take_library import load_take
from core.take_player import TakePlayer
from tests.test_workspace_media_backup import art as art, _take, _package, _digest
from tests.test_workspace_media_library_ui import app as app, make_dialog as make_dialog, _wait
from tests.test_workspace_navigation import navigation as navigation
from tests.test_recording_studio import _InspectableSink
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.widgets import art_workspace as art_ui
from webjam_qt.controllers import workspace_media as actions


@pytest.fixture
def portable_takes(tmp_path):
    originals = tmp_path / "original-takes"
    originals.mkdir()
    first, primary = _take(originals, label="A", amplitude=150)
    second, alternate = _take(originals, session_id=primary.session_id, label="B", amplitude=350)
    saved = load_studio_document(first)
    document = add_take_lane(saved.document, primary, alternate, destination_track_id=primary.tracks[0].track_id)
    save_studio_document(first, document, expected_token=saved.token)
    links = tuple({"take_id": project.take_id, "take_path": str(root),
                   "source_identity": _digest((root / "webjam-take.json").read_bytes())}
                  for root, project in ((first, primary), (second, alternate)))
    source = SessionLibrary(tmp_path / "source").create("music", "Portable duo", take_links=links)
    preview = _package(tmp_path, source, media.WorkspacePackageSelection(take_ids=(primary.take_id, alternate.take_id)))
    library = SessionLibrary(tmp_path / "restored")
    restored = media.import_workspace_package(library, preview)
    return library, restored, originals, (first, second), (primary, alternate)


@pytest.fixture
def studio(app, portable_takes):
    _library, _record, originals, _roots, _projects = portable_takes
    sink = _InspectableSink()
    widget = RecordingStudio(str(originals), player=TakePlayer(samplerate=48000, sink=sink))
    yield widget
    if widget.media_open_pending:
        widget.prepare_close()
        _wait(app, lambda: not widget.media_open_pending)
    widget._review_dialog.dirty = False
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_prepared_activation_uses_restored_dependencies_despite_duplicate_original_ids(
    portable_takes, studio, monkeypatch,
):
    _library, record, _originals, original_roots, projects = portable_takes
    primary, alternate = projects
    verified = media.verify_workspace_media(record, "take", primary.take_id)
    restored_alt = Path(record.take_links[1]["take_path"])
    duplicate = load_take(restored_alt)
    studio._takes.append(duplicate)
    studio._take_list.addItem(studio._take_library_item(duplicate))
    original_bytes = {path: path.read_bytes() for root in original_roots for path in root.iterdir() if path.is_file()}
    from webjam_qt.widgets import studio_take_review_workflow as review
    monkeypatch.setattr(review, "load_take", lambda *_a, **_k: pytest.fail("Prepared activation rehashed audio on Qt"))
    monkeypatch.setattr(studio, "reload", lambda *_a, **_k: pytest.fail("Prepared activation scanned configured takes"))
    configured = studio._takes_dir
    assert studio.open_prepared_take(verified, workspace=record, expected_state=studio.prepare_workspace_open(), position_seconds=.04)
    assert studio._current.path == Path(record.take_links[0]["take_path"])
    assert studio._studio_source_catalog.root_for_take(alternate.take_id) == restored_alt
    assert studio._studio_source_catalog.root_for_take(alternate.take_id) != original_roots[1]
    assert studio._player.position_s == pytest.approx(.04)
    assert studio._takes_dir == configured
    assert not studio._player.is_playing
    assert studio._review_dialog.notes.toPlainText() == "Saved review"
    assert {path: path.read_bytes() for path in original_bytes} == original_bytes
    public = studio.current_take_reference()
    assert set(public) == {"take_path", "take_id", "source_identity", "position_seconds"}


@pytest.mark.parametrize("change", ["media", "selection", "review", "phase"])
def test_prepared_activation_refuses_changed_media_or_studio_owner(portable_takes, studio, change):
    _library, record, _originals, roots, projects = portable_takes
    assert studio.open_take(roots[1])
    verified = media.verify_workspace_media(record, "take", projects[0].take_id)
    request = studio.prepare_workspace_open()
    previous = studio._current
    count = len(studio._takes)
    if change == "media":
        audio = Path(record.take_links[0]["take_path"]) / "audio.wav"
        value = audio.read_bytes()
        audio.write_bytes(value[:-1] + bytes([value[-1] ^ 1]))
    elif change == "selection":
        studio._guidance_take_revision += 1
    elif change == "review":
        studio._review_dialog.notes.setPlainText("Keep my new draft")
    else:
        studio._phase_name = "finalizing"
    assert not studio.open_prepared_take(verified, workspace=record, expected_state=request)
    assert studio._current is previous and len(studio._takes) == count
    assert not studio._player.is_playing
    if change == "review":
        assert studio._review_dialog.notes.toPlainText() == "Keep my new draft" and studio._review_dialog.dirty
    studio._phase_name = "idle"


def test_missing_declared_dependency_never_falls_back_to_original(portable_takes, studio):
    _library, record, _originals, _roots, projects = portable_takes
    verified = media.verify_workspace_media(record, "take", projects[0].take_id)
    # A malformed prepared object is not permission to use a same-ID original.
    incomplete = replace(verified, _dependencies=())
    assert not studio.open_prepared_take(incomplete, workspace=record)
    assert studio._studio_state_error
    assert not studio._player.is_playing


def test_library_verify_runs_hashes_off_qt_without_opening_or_changing_draft(
    app, portable_takes, make_dialog, monkeypatch,
):
    library, record, _originals, _roots, _projects = portable_takes
    dialog = make_dialog(library, current_id=record.id)
    dialog.takes.setCurrentRow(0)
    dialog.notes.setPlainText("Uncommitted notes stay here")
    before = dialog._edited_record()
    opened = []
    dialog.take_open_requested.connect(opened.append)
    from core import take_library
    hashing_threads = []
    original = take_library._streaming_file_identity
    def hash_file(*args, **kwargs):
        hashing_threads.append(get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(take_library, "_streaming_file_identity", hash_file)
    dialog.verify_take_button.click()
    _wait(app, lambda: not dialog.media_operation_pending)
    assert hashing_threads and all(thread != get_ident() for thread in hashing_threads)
    assert not opened
    assert "Content matched when checked" in dialog.takes.item(0).text()
    assert dialog._edited_record() == before and dialog._dirty
    assert not dialog.timer.isActive()


def test_art_verify_open_and_relink_keep_brief_bookmarks_and_expected_content(
    app, art, tmp_path, make_dialog, monkeypatch,
):
    source, original = art
    library = SessionLibrary(tmp_path / "restored")
    record = media.import_workspace_package(library, _package(tmp_path, source))
    path = Path(record.art["references"][0]["locator"])
    dialog = make_dialog(library, current_id=record.id)
    panel = dialog.art
    panel.references.setCurrentRow(0)
    panel.brief.setPlainText("Keep this unsaved brief")
    panel.bookmark_note.setText("Unsubmitted lesson note")
    opened = []
    monkeypatch.setattr(art_ui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    panel.verify_button.click()
    _wait(app, lambda: not dialog.media_operation_pending)
    assert "Content matched when checked" in panel.status.text()
    assert opened == [] and dialog._dirty
    panel._open()
    _wait(app, lambda: not dialog.media_operation_pending)
    assert [Path(value) for value in opened] == [path]
    moved = tmp_path / "moved.png"
    path.rename(moved)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(moved), ""))
    panel._relink()
    _wait(app, lambda: not dialog.media_operation_pending)
    saved = library.load(record.id)
    assert saved.art["references"][0]["locator"] == str(moved)
    assert saved.art["references"][0]["id"] == record.art["references"][0]["id"]
    assert saved.art["bookmarks"] == record.art["bookmarks"]
    assert panel.brief.toPlainText() == "Keep this unsaved brief"
    assert panel.bookmark_note.text() == "Unsubmitted lesson note"
    assert [Path(value) for value in opened] == [path]
    assert original.read_bytes() == moved.read_bytes()
    bad = tmp_path / "different.png"
    bad.write_bytes(b"unrelated bytes")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(bad), ""))
    panel._relink()
    _wait(app, lambda: not dialog.media_operation_pending)
    assert panel._selected_reference()["locator"] == str(moved)
    assert library.load(record.id).art["references"][0]["locator"] == str(moved)
    assert len(opened) == 1


def test_art_open_result_cannot_launch_a_newly_selected_reference(app, art, tmp_path, make_dialog, monkeypatch):
    source, _ = art
    library = SessionLibrary(tmp_path / "restored")
    record = media.import_workspace_package(library, _package(tmp_path, source))
    dialog = make_dialog(library, current_id=record.id)
    dialog.art.references.setCurrentRow(0)
    entered, release = Event(), Event()
    original = actions.verify_workspace_media
    opened = []
    monkeypatch.setattr(art_ui.QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(actions, "verify_workspace_media", held)
    try:
        dialog.art._open()
        assert entered.wait(2)
        dialog.art.references.setCurrentRow(1)
        release.set()
        _wait(app, lambda: not dialog.media_operation_pending)
        assert not opened
        assert "changed during verification" in dialog.status.text()
    finally:
        release.set()


def test_actual_coordinator_open_activates_exact_restored_take_without_playing(
    navigation, portable_takes, monkeypatch,
):
    _navigator, _mailbox, _controllers, app = navigation
    controller = _navigator.controller
    library, record, _originals, _roots, _projects = portable_takes
    coordinator = controller.session_library
    coordinator.library = library
    coordinator.current = record
    coordinator._record_saved(record)
    coordinator.show()
    dialog = coordinator.dialog
    dialog.takes.setCurrentRow(0)
    studio = controller.window.recording_studio
    monkeypatch.setattr(studio, "jump_to_bookmark", lambda *_a, **_k: pytest.fail("Restored open used synchronous hash path"))
    dialog.open_take_button.click()
    _wait(app, lambda: coordinator.dialog is None)
    assert studio._current.path == Path(record.take_links[0]["take_path"])
    assert not studio._player.is_playing
    assert studio._workspace_media_context["workspace"].id == record.id
    assert coordinator.current.id == record.id


def test_prepared_ab_reverifies_then_plays_requested_restored_source(app, portable_takes, studio):
    _library, record, _originals, roots, projects = portable_takes
    for slot, project in zip(("A", "B"), projects):
        checked = media.verify_workspace_media(record, "take", project.take_id)
        assert studio.open_prepared_take(checked, workspace=record)
        studio._assign_review_slot(slot)
    originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for root in roots for p in root.iterdir() if p.is_file()}
    for slot, project, reference in zip(("A", "B"), projects, record.take_links):
        old_epoch = studio._player._playback_epoch
        studio._audition_review_slot(slot)
        _wait(app, lambda: (not studio.media_open_pending and studio._current.take_id == project.take_id
                           and studio._current.path == Path(reference["take_path"])
                           and studio._player.is_playing and studio._player._playback_epoch != old_epoch))
    studio._stop_playback()
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals} == originals


@pytest.mark.parametrize("change", ["hide_return", "profile_return", "edit_undo", "phase_return"])
def test_comparison_result_is_retired_after_intervening_user_action(
    app, portable_takes, studio, monkeypatch, change,
):
    _library, record, _originals, _roots, projects = portable_takes
    studio.show()
    app.processEvents()
    verified = media.verify_workspace_media(record, "take", projects[0].take_id)
    assert studio.open_prepared_take(verified, workspace=record)
    studio._assign_review_slot("A")
    entered, release = Event(), Event()
    verify = media.verify_workspace_media
    played = []
    monkeypatch.setattr(studio, "_toggle_play", lambda: played.append(True))

    def held(*args, **kwargs):
        result = verify(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(media, "verify_workspace_media", held)
    try:
        studio._audition_review_slot("A")
        assert entered.wait(2)
        if change == "hide_return":
            studio.hide()
            studio.show()
        elif change == "profile_return":
            studio.set_creator_profile(get_creator_profile_by_key("art"))
            studio.set_creator_profile(get_creator_profile_by_key("music"))
        elif change == "edit_undo":
            before = studio._studio_controller.document
            generation = studio._studio_controller.generation
            studio._update_studio_state(0, gain=.25)
            assert studio._studio_controller.document != before
            studio._undo_arrange_edit()
            assert studio._studio_controller.document == before
            assert studio._studio_controller.generation == generation
        else:
            studio.set_recording_phase("preflight")
            studio.set_recording_phase("idle")
        release.set()
        _wait(app, lambda: not studio.media_open_pending)
        assert not played, "A retired verification started playback"
    finally:
        release.set()


@pytest.mark.parametrize("via", ["row", "live", "reload"])
def test_restored_context_survives_actual_reselection(app, portable_takes, studio, monkeypatch, via):
    _library, record, _originals, roots, projects = portable_takes
    checked = media.verify_workspace_media(record, "take", projects[0].take_id)
    assert studio.open_prepared_take(checked, workspace=record)
    restored = Path(record.take_links[0]["take_path"])
    expected_alternate = Path(record.take_links[1]["take_path"])
    assert all(t.path != expected_alternate for t in studio._takes)
    if via == "row":
        other = next(i for i, take in enumerate(studio._takes) if take.path == roots[1])
        studio._take_list.setCurrentRow(other)
    elif via == "live":
        studio._new_take_btn.click()
        assert studio._viewing_live
    else:
        from webjam_qt.widgets import recording_studio as ui
        monkeypatch.setattr(ui, "discover_takes", lambda _: [load_take(roots[1]), load_take(restored)])
        studio.reload()
    studio.show()
    app.processEvents()
    row = next(i for i, take in enumerate(studio._takes) if take.path == restored)
    item = studio._take_list.item(row)
    studio._take_list.scrollToItem(item)
    QTest.mouseClick(studio._take_list.viewport(), Qt.MouseButton.LeftButton,
                    pos=studio._take_list.visualItemRect(item).center())
    assert not studio._viewing_live and studio._current.path == restored
    assert not studio._studio_state_error
    assert studio._studio_source_catalog.root_for_take(projects[1].take_id) == expected_alternate
    studio._assign_review_slot("A")
    assert studio._workspace_review_slots["A"].id == record.id


def test_comp_controls_use_declared_alternates_outside_global_take_list(portable_takes, studio):
    _library, record, _originals, _roots, projects = portable_takes
    checked = media.verify_workspace_media(record, "take", projects[0].take_id)
    assert studio.open_prepared_take(checked, workspace=record)
    # Only the primary is in the global browser. Its declared dependency is
    # still available for an explicitly selected comp track.
    studio._take_list.blockSignals(True)
    studio._takes = [studio._current]
    studio._take_list.clear()
    studio._take_list.addItem(studio._take_library_item(studio._current))
    studio._take_list.setCurrentRow(0)
    studio._take_list.blockSignals(False)
    studio._studio_controller.select_track(projects[0].tracks[0].track_id)
    studio._refresh_comp_controls()
    assert studio._add_take_lane_btn.isEnabled()


@pytest.mark.parametrize("kind", ["art", "take"])
def test_relink_refuses_target_replaced_after_worker_check(
    app, request, kind, tmp_path, make_dialog, monkeypatch,
):
    if kind == "art":
        source, _ = request.getfixturevalue("art")
        library = SessionLibrary(tmp_path / "restored")
        record = media.import_workspace_package(library, _package(tmp_path, source))
        old_path = Path(record.art["references"][0]["locator"])
        candidate = tmp_path / "candidate.png"
        shutil.copyfile(old_path, candidate)
        mutate = candidate
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(candidate), ""))
    else:
        library, record, _originals, _roots, _projects = request.getfixturevalue("portable_takes")
        old_path = Path(record.take_links[0]["take_path"])
        candidate = tmp_path / "candidate-take"
        shutil.copytree(old_path, candidate)
        mutate = candidate / "audio.wav"
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(candidate))
    dialog = make_dialog(library, current_id=record.id)
    dialog.notes.setPlainText("Preserve my draft while checking this replacement")
    dialog.art.references.setCurrentRow(0)
    dialog.takes.setCurrentRow(0)
    before = dialog._edited_record()
    relink = actions.relink_workspace_media
    entered, release = Event(), Event()

    def held(*args, **kwargs):
        value = relink(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(actions, "relink_workspace_media", held)
    try:
        if kind == "art":
            dialog.art._relink()
        else:
            dialog.relink_take_button.click()
        assert entered.wait(2)
        value = mutate.read_bytes()
        mutate.write_bytes(value[:-1] + bytes([value[-1] ^ 1]))
        release.set()
        _wait(app, lambda: not dialog.media_operation_pending)
        assert dialog._edited_record() == before
        assert dialog._dirty
        assert library.load(record.id).take_links == record.take_links
        assert library.load(record.id).art == record.art
        assert "did not finish" in dialog.status.text()
    finally:
        release.set()


def test_prepared_open_queued_close_preserves_new_notes(navigation, portable_takes, monkeypatch):
    navigator, _mailbox, _controllers, app = navigation
    library, record, _originals, _roots, _projects = portable_takes
    coordinator = navigator.controller.session_library
    coordinator.library, coordinator.current = library, record
    coordinator._record_saved(record)
    coordinator.show()
    dialog = coordinator.dialog
    dialog.takes.setCurrentRow(0)
    queued = []
    single_shot = QTimer.singleShot

    def hold_close(interval, callback):
        if getattr(callback, "__name__", "") == "close_origin":
            queued.append(callback)
        else:
            single_shot(interval, callback)

    monkeypatch.setattr(QTimer, "singleShot", hold_close)
    dialog.open_take_button.click()
    _wait(app, lambda: bool(queued) and not dialog.media_operation_pending)
    dialog.notes.setPlainText("These notes arrived after the take opened")
    dialog.timer.stop()
    queued.pop()()
    assert coordinator.dialog is dialog and dialog.isVisible()
    assert dialog.notes.toPlainText() == "These notes arrived after the take opened"
    assert dialog._dirty


@pytest.mark.parametrize("kind", ["art", "take"])
def test_relink_chooser_retains_owner_and_honors_close_before_starting_worker(
    app, request, kind, tmp_path, make_dialog, monkeypatch,
):
    if kind == "art":
        source, _ = request.getfixturevalue("art")
        library = SessionLibrary(tmp_path / "restored")
        record = media.import_workspace_package(library, _package(tmp_path, source))
        candidate = record.art["references"][0]["locator"]
        chooser = "getOpenFileName"
    else:
        library, record, _originals, _roots, _projects = request.getfixturevalue("portable_takes")
        candidate = record.take_links[0]["take_path"]
        chooser = "getExistingDirectory"
    dialog = make_dialog(library, current_id=record.id)
    dialog.notes.setPlainText("Keep this draft through chooser cancellation")
    dialog.art.references.setCurrentRow(0)
    dialog.takes.setCurrentRow(0)
    before = dialog._edited_record()
    guarded = []

    def choose(*_args, **_kwargs):
        guarded.append(dialog.media_operation_pending)
        dialog.close()
        assert dialog.isVisible()
        return (candidate, "") if kind == "art" else candidate

    monkeypatch.setattr(QFileDialog, chooser, choose)
    monkeypatch.setattr(actions, "verify_workspace_media", lambda *_a, **_k: pytest.fail("Cancelled chooser started worker"))
    if kind == "art":
        dialog.art._relink()
    else:
        dialog.relink_take_button.click()
    assert guarded == [True]
    assert not dialog.media_operation_pending and dialog.workspace_flow.token is None
    assert dialog._dirty and dialog._edited_record() == before
    assert dialog.backup_button.isEnabled() and "cancelled" in dialog.status.text()


def test_changed_portable_dependency_blocks_reselection_instead_of_using_original(
    portable_takes, studio,
):
    _library, record, _originals, roots, projects = portable_takes
    checked = media.verify_workspace_media(record, "take", projects[0].take_id)
    assert studio.open_prepared_take(checked, workspace=record)
    restored = Path(record.take_links[0]["take_path"])
    studio._take_list.setCurrentRow(next(i for i, t in enumerate(studio._takes) if t.path == roots[1]))
    manifest = Path(record.take_links[1]["take_path"]) / "webjam-take.json"
    # Valid JSON, different immutable bytes. The same-ID original stays intact.
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    studio._take_list.setCurrentRow(next(i for i, t in enumerate(studio._takes) if t.path == restored))
    assert studio._studio_state_error
    assert not studio._play_btn.isEnabled() and not studio._player.is_playing


def test_comparison_worker_blocks_profile_and_workspace_transition(navigation, portable_takes, monkeypatch):
    navigator, _mailbox, _controllers, app = navigation
    controller = navigator.controller
    coordinator = controller.session_library
    library, record, _originals, _roots, projects = portable_takes
    coordinator.library, coordinator.current = library, record
    studio = controller.window.recording_studio
    checked = media.verify_workspace_media(record, "take", projects[0].take_id)
    assert studio.open_prepared_take(checked, workspace=record)
    studio._assign_review_slot("A")
    entered, release = Event(), Event()
    verify = media.verify_workspace_media

    def held(*args, **kwargs):
        value = verify(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(media, "verify_workspace_media", held)
    try:
        studio._audition_review_slot("A")
        assert entered.wait(2)
        controller._apply_creator_profile_key("art")
        assert controller.creator_profile.key == "music"
        assert coordinator.current.id == record.id
        other = library.create("music", "Different owner")
        assert not coordinator.continue_record(other)
        assert coordinator.current.id == record.id
        assert not controller._prepare_workspace_close()
        release.set()
        _wait(app, lambda: not studio.media_open_pending)
        assert not studio._player.is_playing
    finally:
        release.set()


def test_comparison_result_cannot_play_after_save_as_copy_changes_owner(navigation, portable_takes, monkeypatch):
    navigator, _mailbox, _controllers, app = navigation
    controller = navigator.controller
    coordinator = controller.session_library
    library, record, _originals, _roots, projects = portable_takes
    coordinator.library, coordinator.current = library, record
    coordinator._record_saved(record)
    studio = controller.window.recording_studio
    checked = media.verify_workspace_media(record, "take", projects[0].take_id)
    assert studio.open_prepared_take(checked, workspace=record)
    studio._assign_review_slot("A")
    coordinator.show()
    entered, release = Event(), Event()
    verify = media.verify_workspace_media
    played = []
    monkeypatch.setattr(studio, "_toggle_play", lambda: played.append(True))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("New copy owner", True))

    def held(*args, **kwargs):
        value = verify(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(media, "verify_workspace_media", held)
    try:
        studio._audition_review_slot("A")
        assert entered.wait(2)
        coordinator.dialog.copy_button.click()
        copied_id = coordinator.dialog.record.id
        assert copied_id != record.id and coordinator.current.id == copied_id
        release.set()
        _wait(app, lambda: not studio.media_open_pending)
        assert not played
        assert coordinator.current.id == copied_id
    finally:
        release.set()


def test_equivalent_path_spelling_cannot_drop_portable_context(portable_takes, studio):
    _library, record, _originals, _roots, projects = portable_takes
    root = Path(record.take_links[0]["take_path"])
    spelling = root.parent / ".." / root.parent.name / root.name
    assert spelling.resolve() == root.resolve()
    reference = dict(record.take_links[0], take_path=str(spelling))
    aliased = replace(record, take_links=(reference, *record.take_links[1:]))
    verified = media.verify_workspace_media(aliased, "take", projects[0].take_id)
    assert studio.open_prepared_take(verified, workspace=aliased)
    assert studio.open_take(spelling)
    assert studio._workspace_media_context is not None
    assert studio._studio_source_catalog.root_for_take(projects[1].take_id) == Path(record.take_links[1]["take_path"])
