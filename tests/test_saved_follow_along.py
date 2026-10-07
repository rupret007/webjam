"""Saved references return to explicit lesson setup without external playback."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QDialog

from core.rehearsal_plan import RehearsalPlan, make_song
from core.session_library import SessionLibrary
from core.workspace_backup import export_workspace_backup, preview_workspace_backup, import_workspace_backup
from tests.test_follow_along_continuity import begin
from tests.test_follow_along_journey import (
    CANONICAL, LINK, music, qapp as qapp, room as room,
    external_handoffs as external_handoffs,
    no_unhandled_qt_slot_errors as no_unhandled_qt_slot_errors,
)


def test_optional_reference_keeps_old_backup_shape_and_new_template(tmp_path):
    old_song = dict(id="song", title="Tune", key="C", tempo=80, goals="Bridge", notes="Draft",
                    next_steps="Practice", moment_draft="Half typed", completed=False, bookmarks=[])
    old = dict(version=1, title="Plan", active_song_id="song", songs=[old_song])
    assert RehearsalPlan.from_payload(old).payload() == old
    source = SessionLibrary(tmp_path / "source")
    for index, payload in enumerate((old, dict(old, songs=[dict(old_song, lesson_url=CANONICAL)]))):
        record = source.create("music", "Saved", rehearsal=payload)
        path = tmp_path / f"backup-{index}.json"
        export_workspace_backup(record, path)
        restored = import_workspace_backup(SessionLibrary(tmp_path / f"target-{index}"), preview_workspace_backup(path))
        assert restored.rehearsal == payload
    song = make_song("Tune", lesson_url=LINK, notes="Private notes", completed=True)
    template = RehearsalPlan("Plan", [song], song["id"]).template_payload()
    assert template["songs"][0]["lesson_url"] == CANONICAL
    assert template["songs"][0]["notes"] == "" and not template["songs"][0]["completed"]
    with pytest.raises(ValueError):
        make_song("Unsafe reference", lesson_url="file:///private/file")


@pytest.mark.parametrize("fail_save", [False, True])
def test_remember_music_preserves_existing_song_drafts(room, qapp, monkeypatch, external_handoffs, fail_save):
    app, panel = begin(room, qapp, monkeypatch, "music")
    library = app.session_library
    library.show(tab="plan")
    dialog = library.dialog
    dialog.rehearsal.add_song("Our tune")
    dialog.rehearsal._notes.setPlainText("Keep the lower harmony")
    dialog.rehearsal._moment_note.setText("Half-typed moment")
    song_id = dialog.rehearsal._plan.active_song_id
    assert dialog.save_current()
    real_save = library.library.save
    if fail_save:
        monkeypatch.setattr(library.library, "save", Mock(side_effect=OSError("Disk unavailable")))
    panel.save_button.click()
    song = dialog.rehearsal._plan.current
    assert song["id"] == song_id and song["lesson_url"] == CANONICAL
    assert song["notes"] == "Keep the lower harmony" and song["moment_draft"] == "Half-typed moment"
    if fail_save:
        assert dialog._dirty and "retained draft" in dialog.rehearsal._feedback.text()
    else:
        restored = SessionLibrary(library.library.root).load(library.current.id)
        assert restored.rehearsal["songs"][0] == song
    external_handoffs[1].assert_not_called()
    if fail_save:
        monkeypatch.setattr(library.library, "save", real_save)
        assert dialog.save_current()
        assert SessionLibrary(library.library.root).load(library.current.id).rehearsal["songs"][0] == song


def test_remember_first_song_requires_explicit_name_then_reuses_saved_link(room, qapp, monkeypatch, external_handoffs):
    app, panel = begin(room, qapp, monkeypatch, "music")
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *a, **k: ("Our lesson", True))
    panel.save_button.click()
    library, dialog = app.session_library, app.session_library.dialog
    stored = SessionLibrary(library.library.root).load(library.current.id)
    assert stored.rehearsal["songs"][0]["title"] == "Our lesson"
    assert stored.rehearsal["songs"][0]["lesson_url"] == CANONICAL
    dialog.reject()
    qapp.processEvents()
    app._clear_shared_lesson_context()
    app.follow_along._sources.clear()
    library.show(tab="plan")
    reopened = library.dialog
    reopened.rehearsal._use_lesson.click()
    qapp.processEvents()
    assert panel.isVisibleTo(app.window) and panel.lesson_url == CANONICAL
    assert not reopened.isVisible()
    library.show(tab="plan")
    assert library.dialog is reopened and reopened.isVisible()
    external_handoffs[1].assert_not_called()


@pytest.mark.parametrize("role", ["host", "native"])
def test_art_saved_bookmark_returns_to_setup_without_playback(room, qapp, external_handoffs, role):
    pair = room(role=role, profile="art", configured=True)
    app = pair.app
    app.session_library.show(tab="plan")
    dialog = app.session_library.dialog
    dialog.art.add_reference(LINK, kind="url", title="Painting")
    dialog.art.references.setCurrentRow(0)
    dialog.art.position.setValue(123)
    dialog.art._add_bookmark()
    dialog.art.bookmarks.setCurrentRow(0)
    assert dialog.save_current()
    dialog.art.use_lesson_button.click()
    qapp.processEvents()
    panel = app.follow_along.panel
    assert panel.isVisibleTo(app.window)
    assert panel.hosting is (role == "host")
    assert panel.lesson_url.endswith("&t=123s")
    assert panel.open_button.isHidden() is (role != "host")
    external_handoffs[1].assert_not_called()
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("change", ["room", "workspace", "song", "modal"])
def test_saved_lesson_rejects_changed_owner_or_modal(room, qapp, monkeypatch, external_handoffs, change):
    app = music(room, qapp)
    library = app.session_library
    library.show(tab="plan")
    dialog = library.dialog
    dialog.rehearsal.add_song("One")
    song_id = dialog.rehearsal._plan.active_song_id
    dialog.rehearsal.set_lesson_url(CANONICAL, expected_song_id=song_id)
    request = (dialog._lesson_binding, dialog.record.id, "music", song_id, CANONICAL)
    modal = None
    if change == "room":
        app._stop_session_peer()
    elif change == "workspace":
        other = library.library.create("music", "Other")
        library.current = other
    elif change == "song":
        dialog.rehearsal.add_song("Two")
    else:
        modal = QDialog(app.window)
        modal.setModal(True)
        modal.show()
        qapp.processEvents()
    try:
        library._use_saved_lesson(dialog, request)
        assert not app.follow_along.panel.isVisibleTo(app.window)
        assert dialog.isVisible()
        external_handoffs[1].assert_not_called()
    finally:
        if modal is not None:
            modal.close()
            modal.deleteLater()
            qapp.processEvents()


def test_lesson_chooser_cannot_write_replacement_plan_with_same_song_id(room, qapp, monkeypatch):
    app = music(room, qapp)
    app.session_library.show(tab="plan")
    editor = app.session_library.dialog.rehearsal
    editor.add_song("Original")
    replacement = deepcopy(editor.payload())
    replacement["songs"][0]["title"] = "Copied workspace"

    def replace_during_chooser(*_args, **_kwargs):
        editor.load_payload(replacement)
        return LINK, True

    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", replace_during_chooser)
    editor._change_lesson.click()
    assert editor.payload() == replacement


def test_art_bookmark_does_not_override_another_references_position(room, qapp):
    pair = room(role="host", profile="art", configured=True)
    pair.app.session_library.show(tab="plan")
    editor = pair.app.session_library.dialog.art
    editor.add_reference(LINK, kind="url", title="First")
    editor.references.setCurrentRow(0)
    editor.position.setValue(123.5)
    editor._add_bookmark()
    editor.add_reference("https://youtu.be/dQw4w9WgXcQ?t=25", kind="url", title="Second")
    editor.bookmarks.setCurrentRow(0)
    assert editor.selected_lesson()[1].endswith("&t=123s")
    editor.references.setCurrentRow(1)
    assert editor.position.value() == 123.5
    assert editor.selected_lesson()[1].endswith("&t=25s")


@pytest.mark.parametrize("profile", ["music", "art"])
def test_library_without_a_live_handoff_explains_continue_first(tmp_path, qapp, external_handoffs, profile):
    from webjam_qt.windows.session_library import SessionLibraryDialog

    library = SessionLibrary(tmp_path / "library")
    song = make_song("Practice", lesson_url=CANONICAL)
    record = library.create(profile, "Saved work", rehearsal=RehearsalPlan("Plan", [song], song["id"]).payload())
    dialog = SessionLibraryDialog(library, profile=profile, current_id=record.id)
    dialog.show()
    qapp.processEvents()
    requests = []
    dialog.lesson_requested.connect(requests.append)
    try:
        if profile == "music":
            dialog.tabs.setCurrentIndex(1)
            dialog.rehearsal._use_lesson.click()
        else:
            dialog.tabs.setCurrentIndex(2)
            dialog.art.add_reference(CANONICAL, kind="url", title="Lesson")
            dialog.art.references.setCurrentRow(0)
            dialog.art.use_lesson_button.click()
        assert "Continue" in dialog.status.text() and "from the room" in dialog.status.text()
        assert dialog.isVisible() and dialog.selected_record is None
        assert requests == []
        external_handoffs[1].assert_not_called()
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()
