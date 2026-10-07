"""Imported historical links cannot become live recording-completion owners."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from core.session_library import SessionLibrary
from core.workspace_backup import export_workspace_backup, import_workspace_backup
from tests import test_workspace_backup as backup_tests

record = backup_tests.record


def test_imported_copy_cannot_steal_original_pending_recording_recovery(tmp_path, record, monkeypatch):
    from webjam_qt.controllers.session_library import SessionLibraryCoordinator
    library = SessionLibrary(tmp_path / "source")
    initial = library.save(replace(record, take_links=()))
    owner = SimpleNamespace(library=library, _recording_owners={}, _pending={}, current=initial,
                            _run_id="run-1", _live_take_ids=set(), ensure_current=lambda: True)
    def flush(*, include_editor=True):
        for key, value in list(owner._pending.items()):
            saved = library.save(value)
            if owner.current.id == saved.id:
                owner.current = saved
            del owner._pending[key]
    owner.flush = Mock(side_effect=flush)
    SessionLibraryCoordinator.recording_started(owner, "take-1", "original-workspace")
    pending = library.load(initial.id)
    assert pending.take_links[0]["take_path"] == "" and "source_identity" not in pending.take_links[0]
    assert pending.take_links[0]["title"] == "Recording requested"
    imported = import_workspace_backup(library, export_workspace_backup(pending, tmp_path / "backup.json"))
    preserved = (library.root / f"{imported.id}.json").read_bytes()
    # A fresh owner has no in-memory reservation; durable evidence must select
    # the original even while the imported historical copy is visible.
    owner.current = imported
    owner._recording_owners.clear()
    owner._run_id = ""
    owner.flush.reset_mock()
    monkeypatch.setattr("core.take_review.take_source_identity", lambda _take: "final-source-identity")
    take = SimpleNamespace(take_id="take-1", session_id="original-workspace", path=Path("/missing/original"), display_name="Recovered take")
    SessionLibraryCoordinator.recording_completed(owner, take, validated=True)
    assert library.load(pending.id).take_links[0]["status"] == "complete"
    assert library.load(pending.id).take_links[0]["source_identity"] == "final-source-identity"
    assert (library.root / f"{imported.id}.json").read_bytes() == preserved
    assert owner.current == imported and not owner._live_take_ids
    owner.flush.assert_called_once_with(include_editor=False)
