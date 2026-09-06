"""Creative guidance follows completed profile and local Notes ownership changes."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core.settings import AppSettings
from webjam_qt.controllers import application_controller as controller_module
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow

_NOTES = {
    "music": "  Next: PRIVATE_MUSIC tighten the chorus groove  \n\n",
    "art": "  Next: PRIVATE_ART shape the clay base  \n\n",
    "podcast_voice": "  Next: PRIVATE_VOICE rehearse the interview opening  \n\n",
    "review_rehearsal": "  Next: PRIVATE_REVIEW revisit the second scene  \n\n",
}
_OTHER_PROFILES = ("music", "podcast_voice", "review_rehearsal")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def local_app(qapp, monkeypatch, tmp_path):
    """Real local owners, with no personal files or live room startup."""
    notes_root = tmp_path / "local-notes"
    notes_root.mkdir()
    for key, filename in persistence_module._PROFILE_NOTES_FILES.items():
        (notes_root / filename).write_text(_NOTES[key], encoding="utf-8")
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: notes_root)
    monkeypatch.setattr(ApplicationController, "_start_routing_scan", lambda self: None)
    made = []

    def create(profile="music"):
        root = tmp_path / f"app-{len(made)}"
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"),
            takes_directory=str(root / "takes"),
            last_creator_profile_key=profile,
            last_creator_start_key="talk_and_make",
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="My local work",
        )
        app = ApplicationController(window, settings=settings)
        made.append(app)
        app.bridge.launch_webex = Mock()
        app._launch_native_jamulus_for_startup = Mock()
        return SimpleNamespace(app=app, canvas=window.session_canvas, notes_root=notes_root)

    yield create
    for app in reversed(made):
        assert app.shutdown()
        window = app.window
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(window)
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _assert_current(pair, profile, *, notes=None):
    app, canvas = pair.app, pair.canvas
    expected = _NOTES[profile] if notes is None else notes
    assert app.creator_profile.key == app._persistence.profile_key == profile
    assert canvas.current_notes() == expected
    pulse = app._current_session_pulse
    assert pulse is canvas._current_pulse
    assert pulse.mode_key == profile
    assert pulse.title == app.window.session_strip.current_title()
    assert expected.strip().removeprefix("Next: ") in pulse.next_step
    assert canvas._pulse_next.text() == f"Next: {pulse.next_step}"
    assert pulse.participant_signal.count == 0
    # Operational guidance may await its normal readiness refresh, but may
    # never retain another profile's creative text beside these Notes.
    assert app._last_musician_guidance.creative in (None, pulse)
    assert canvas._current_guidance.creative in (None, pulse)
    assert app.bridge.launch_webex.call_count == 0
    assert app._launch_native_jamulus_for_startup.call_count == 0


@pytest.mark.parametrize("previous", _OTHER_PROFILES)
def test_explicit_art_profile_uses_restored_art_notes_without_an_edit(local_app, previous):
    pair = local_app(previous)
    app, canvas = pair.app, pair.canvas
    before = {path.name: path.read_bytes() for path in pair.notes_root.iterdir()}
    changes = Mock()
    canvas.notes_changed.connect(changes)

    app._apply_creator_profile_key("art", host_owned=False)

    _assert_current(pair, "art")
    assert app.settings.last_creator_profile_key == "art"
    assert not app._creator_profile_host_owned
    assert not app._pulse_refresh_timer.isActive()
    assert not app._notes_save_timer.isActive()
    changes.assert_not_called()
    assert {path.name: path.read_bytes() for path in pair.notes_root.iterdir()} == before
    assert _NOTES[previous].strip() not in canvas.current_session_brief()


@pytest.mark.parametrize("target", _OTHER_PROFILES)
def test_explicit_return_to_another_profile_uses_its_current_notes(local_app, target):
    pair = local_app("art")
    changes = Mock()
    pair.canvas.notes_changed.connect(changes)

    pair.app._apply_creator_profile_key(target, host_owned=False)

    _assert_current(pair, target)
    assert pair.app.settings.last_creator_profile_key == target
    assert "PRIVATE_ART" not in pair.canvas.current_session_brief()
    changes.assert_not_called()


def _edit_and_select(canvas):
    editor = canvas._notes
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    cursor.insertText("Next: keep the edge rough\n")
    cursor.endEditBlock()
    cursor.setPosition(3)
    cursor.setPosition(17, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    assert editor.document().isUndoAvailable()
    return (editor.toPlainText(), cursor.position(), cursor.anchor())


def _assert_editor(canvas, expected):
    cursor = canvas._notes.textCursor()
    assert (canvas.current_notes(), cursor.position(), cursor.anchor()) == expected
    assert canvas._notes.document().isUndoAvailable()


@pytest.mark.parametrize("ownership_change", [False, True])
def test_same_profile_keeps_draft_selection_undo_and_save_state(
    local_app, monkeypatch, ownership_change,
):
    pair = local_app("art")
    app, canvas = pair.app, pair.canvas
    expected = _edit_and_select(canvas)
    app._pulse_refresh_timer.stop()
    app._notes_save_timer.stop()
    app._refresh_session_pulse()
    pending = app._persistence.unsaved_notes
    state = app._persistence.notes_save_state
    pulse = app._current_session_pulse
    changes, writes = Mock(), Mock(wraps=persistence_module.atomic_write_text)
    canvas.notes_changed.connect(changes)
    monkeypatch.setattr(persistence_module, "atomic_write_text", writes)

    app._apply_creator_profile_key("art", host_owned=ownership_change)

    _assert_editor(canvas, expected)
    assert app._persistence.unsaved_notes == pending
    assert app._persistence.notes_save_state == state == "pending"
    changes.assert_not_called()
    writes.assert_not_called()
    if not ownership_change:
        assert app._current_session_pulse is pulse
    else:
        assert app._current_session_pulse.mode_key == "art"
    canvas._notes.undo()
    assert canvas.current_notes() == _NOTES["art"]
    canvas._notes.redo()
    assert canvas.current_notes() == expected[0]


def test_repeated_current_context_does_not_rebuild_guidance(local_app, monkeypatch):
    pair = local_app("art")
    app = pair.app
    app._apply_creator_profile_key("art", host_owned=True)
    build = Mock(wraps=controller_module.build_session_pulse)
    monkeypatch.setattr(controller_module, "build_session_pulse", build)
    pulse = app._current_session_pulse
    guidance = app._last_musician_guidance

    for _ in range(3):
        app._apply_creator_profile_key("art", host_owned=True)

    build.assert_not_called()
    assert app._current_session_pulse is pulse
    assert app._last_musician_guidance is guidance


def test_pending_art_draft_wins_over_older_disk_notes_after_profile_round_trip(
    local_app, monkeypatch,
):
    pair = local_app("art")
    app, canvas = pair.app, pair.canvas
    draft = "  Next: PRIVATE_UNSAVED_ART keep the new clay base  \n\n"
    canvas.edit_notes(draft)
    app._pulse_refresh_timer.stop()
    app._notes_save_timer.stop()

    def fail_write(*args, **kwargs):
        raise OSError("controlled write refusal")

    with monkeypatch.context() as patcher:
        patcher.setattr(persistence_module, "atomic_write_text", fail_write)
        assert app._save_notes() is False
        app._apply_creator_profile_key("music", host_owned=False)
        app._apply_creator_profile_key("art", host_owned=False)
        _assert_current(pair, "art", notes=draft)
        assert dict(app._persistence.unsaved_notes)["art"] == draft
        assert app._persistence.notes_save_state == canvas._notes_save_state == "failed"
        assert (pair.notes_root / ".webjam_notes.art.md").read_text() == _NOTES["art"]


def test_failed_derivation_discards_old_profile_creative_text_everywhere(
    local_app, monkeypatch, caplog,
):
    pair = local_app("music")
    private_failure = "PRIVATE_DERIVATION_EXCEPTION"

    def fail_derivation(**kwargs):
        raise ValueError(private_failure)

    with monkeypatch.context() as patcher:
        patcher.setattr(controller_module, "build_session_pulse", fail_derivation)
        pair.app._apply_creator_profile_key("art", host_owned=False)

    assert pair.app.creator_profile.key == "art"
    assert pair.canvas.current_notes() == _NOTES["art"]
    assert pair.app._current_session_pulse is None
    assert pair.canvas._current_pulse is None
    assert pair.app._last_musician_guidance.creative is None
    assert pair.canvas._current_guidance.creative is None
    assert "PRIVATE_MUSIC" not in pair.canvas.current_session_brief()
    assert "PRIVATE_ART" in pair.canvas.current_session_brief()
    assert "raw notes" in pair.canvas._pulse_next.text()
    assert private_failure not in caplog.text
    assert "PRIVATE_ART" not in caplog.text
    assert "PRIVATE_MUSIC" not in caplog.text


def test_shutdown_drops_late_profile_and_note_refresh_without_touching_draft(
    local_app, monkeypatch,
):
    pair = local_app("art")
    app, canvas = pair.app, pair.canvas
    expected = _edit_and_select(canvas)
    app._pulse_refresh_timer.stop()
    app._notes_save_timer.stop()
    pulse = app._current_session_pulse
    build = Mock(wraps=controller_module.build_session_pulse)
    monkeypatch.setattr(controller_module, "build_session_pulse", build)
    app._shutdown = True
    try:
        app._apply_creator_profile_key("music", host_owned=False)
        app._refresh_session_pulse()
        assert app.creator_profile.key == "art"
        assert app._persistence.profile_key == "art"
        assert app._current_session_pulse is pulse
        _assert_editor(canvas, expected)
        build.assert_not_called()
    finally:
        app._shutdown = False


def test_restoration_refresh_waits_for_saved_profile_title_metadata(local_app):
    pair = local_app("music")
    app = pair.app
    app._apply_creator_profile_key("art", host_owned=True)
    app._set_session_entry_title("PRIVATE_BORROWED_ART_TITLE", borrowed=True)
    (pair.notes_root / ".webjam_session.json").write_text(json.dumps({
        "schema_version": 2,
        "profiles": {"music": {"title": "PRIVATE_SAVED_MUSIC_TITLE", "mode": "music_jam"}},
    }))

    assert app._stop_session_peer(clear_invite=True)

    _assert_current(pair, "music")
    assert app._current_session_pulse.title == "PRIVATE_SAVED_MUSIC_TITLE"
    assert "PRIVATE_BORROWED_ART_TITLE" not in pair.canvas.current_session_brief()


def test_restoration_failure_keeps_guidance_with_the_rolled_back_art_draft(
    local_app, monkeypatch,
):
    pair = local_app("music")
    app, canvas = pair.app, pair.canvas
    app._apply_creator_profile_key("art", host_owned=True)
    app._set_session_entry_title("PRIVATE_BORROWED_ART_TITLE", borrowed=True)

    def fail_metadata():
        raise RuntimeError("controlled metadata restore failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(app._persistence, "_load_session_metadata", fail_metadata)
        with pytest.raises(RuntimeError, match="controlled metadata restore failure"):
            app._stop_session_peer(clear_invite=True)

    _assert_current(pair, "art")
    assert app._creator_profile_host_owned
    assert app._persistence._borrowed_title == "PRIVATE_BORROWED_ART_TITLE"
    assert app._current_session_pulse.title == "PRIVATE_BORROWED_ART_TITLE"
    assert "PRIVATE_MUSIC" not in canvas.current_session_brief()
    assert app.settings.last_creator_profile_key == "music"


def test_profile_refresh_does_not_attach_art_creative_to_old_music_facts(local_app):
    pair = local_app("music")
    app = pair.app
    operational = app._last_session_conductor_snapshot
    guidance = app._last_musician_guidance

    app._apply_creator_profile_key("art", host_owned=False)

    _assert_current(pair, "art")
    assert app._last_session_conductor_snapshot is operational
    assert app._last_musician_guidance.creative is None
    assert app._last_musician_guidance.phase is guidance.phase
    assert app._last_musician_guidance.primary_action is guidance.primary_action
    app._refresh_readiness()
    assert app._last_musician_guidance.creative is app._current_session_pulse
    assert pair.canvas._current_guidance.creative is app._current_session_pulse


def test_active_recovery_draft_revision_updates_guidance_without_fake_edit_or_save(
    local_app, monkeypatch,
):
    pair = local_app("art")
    app, canvas = pair.app, pair.canvas
    draft = "Next: PRIVATE_PENDING_ART refine the rim\n"
    revised = "  Next: PRIVATE_REVISED_ART keep the rim broad  \n\n"
    canvas.edit_notes(draft)
    app._pulse_refresh_timer.stop()
    app._notes_save_timer.stop()
    changes = Mock()
    canvas.notes_changed.connect(changes)
    writes = Mock(side_effect=OSError("controlled write refusal"))
    with monkeypatch.context() as patcher:
        patcher.setattr(persistence_module, "atomic_write_text", writes)
        assert app._save_notes() is False
        writes.reset_mock()

        assert app._persistence.revise_pending_notes("art", draft, revised)

        _assert_current(pair, "art", notes=revised)
        assert dict(app._persistence.unsaved_notes)["art"] == revised
        assert app._persistence.notes_save_state == "failed"
        assert not app._notes_save_timer.isActive()
        assert not app._pulse_refresh_timer.isActive()
        changes.assert_not_called()
        writes.assert_not_called()


def test_context_becoming_unreadable_retires_creative_text_without_raising(
    local_app, monkeypatch, caplog,
):
    pair = local_app("art")
    app, canvas = pair.app, pair.canvas
    expected = _edit_and_select(canvas)
    app._pulse_refresh_timer.stop()
    app._notes_save_timer.stop()
    app._refresh_session_pulse()
    assert app._last_musician_guidance.creative is app._current_session_pulse
    changes = Mock()
    canvas.notes_changed.connect(changes)
    private_failure = "PRIVATE_UNREADABLE_NOTES_CONTEXT"
    reads = 0

    def read_then_fail():
        nonlocal reads
        reads += 1
        if reads == 1:
            return expected[0]
        raise RuntimeError(private_failure)

    with monkeypatch.context() as patcher:
        patcher.setattr(canvas, "current_notes", read_then_fail)
        app._refresh_session_pulse()

    assert reads >= 2
    assert app._current_session_pulse is None
    assert canvas._current_pulse is None
    assert app._last_musician_guidance.creative is None
    assert canvas._current_guidance.creative is None
    _assert_editor(canvas, expected)
    assert "raw notes" in canvas._pulse_next.text()
    assert private_failure not in caplog.text
    assert "PRIVATE_ART" not in caplog.text
    changes.assert_not_called()
