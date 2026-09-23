"""A failed Notes retry must veto Quit before the live room is torn down."""

import errno
from pathlib import Path
from unittest.mock import Mock

from core import file_io
from tests.test_native_art_activities import (
    native_room as _native_room_fixture,
    qapp as _qapp_fixture,
)
from webjam_qt.controllers import session_persistence as persistence_module

native_room = _native_room_fixture
qapp = _qapp_fixture


def test_failed_retry_changed_original_vetoes_real_controller_quit(
    native_room, qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    primary = tmp_path / ".webjam_notes.art.md"
    primary.write_text("Earlier saved Art notes")
    pair = native_room(profile="art")
    app = pair.app
    canvas, owner = app.window.session_canvas, app._persistence
    canvas.edit_notes("Retained Art draft")
    app._notes_save_timer.stop()
    write = file_io._atomic_write

    def fail_primary(path, data, *, mode):
        if Path(path) == primary:
            raise OSError(errno.EACCES, "Controlled access failure")
        return write(path, data, mode=mode)

    with monkeypatch.context() as patch:
        patch.setattr(file_io, "_atomic_write", fail_primary)
        assert not app._save_notes()
    primary.write_text("A newer original edited externally while repairing access")
    stop = Mock(return_value=True)
    monkeypatch.setattr(app.bridge, "stop_jamulus", stop)
    room_before = app._room_participant
    try:
        result = app.shutdown()
        assert (
            primary.read_text()
            == "A newer original edited externally while repairing access"
        )
        assert result is False
        assert not app._shutdown
        assert not app._shutdown_cleanup_pending
        assert owner.unsaved_notes == (("art", "Retained Art draft"),)
        assert owner.notes_recovery_state("art") == "recovery_conflict"
        assert app._room_participant is room_before
        assert app._room_participant.generation == pair.room_generation
        stop.assert_not_called()
    finally:
        if owner.has_unsaved_notes:
            assert owner.export_pending_notes(
                "art", "Retained Art draft", str(tmp_path / "retained-art-copy.md")
            )
