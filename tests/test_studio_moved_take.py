"""Explicit saved-work opens retain exact recording identity after a move."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt

from core.take_player import TakePlayer
from tests.test_recording_studio import APP, RATE, _SilentSink, _schema2_studio_take
from webjam_qt.widgets.recording_studio import RecordingStudio


@pytest.fixture
def moved_take(tmp_path):
    directory = tmp_path / "configured-takes"
    first, _ = _schema2_studio_take(directory)
    first = first.rename(directory / "First take")
    other, _ = _schema2_studio_take(directory)
    widget = RecordingStudio(str(directory), player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    assert widget.open_take(first)
    widget._review_dialog.favorite.setChecked(True)
    widget._review_dialog.notes.setPlainText("Keep the quieter mix")
    assert widget._flush_take_review()
    widget._lanes[0]._gain.setValue(70)
    assert widget._flush_studio_state()
    reference = widget.current_take_reference()
    arrangement = widget._studio_state.to_dict()
    assert widget.open_take(other)
    moved = first.rename(tmp_path / "moved-take")
    original = {p.relative_to(moved): p.read_bytes() for p in moved.rglob("*") if p.is_file()}
    yield widget, moved, other, reference, arrangement, original
    widget._review_dialog.dirty = False
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    APP.processEvents()


@pytest.mark.parametrize("bookmark", [False, True])
def test_exact_moved_take_opens_with_saved_mix_and_review_without_playing_or_reconfiguring(moved_take, bookmark):
    widget, moved, _other, reference, arrangement, original = moved_take
    configured = widget._takes_dir
    count = widget._take_list.count()
    with patch.object(widget, "_toggle_play") as play:
        if bookmark:
            assert widget.jump_to_bookmark(moved, 0.35, reference["take_id"], reference["source_identity"])
            assert widget._player.position_s == pytest.approx(0.35)
        else:
            assert widget.open_take(moved)
        assert not widget._player.is_playing
        play.assert_not_called()
    assert widget._takes_dir == configured
    assert widget._current.path == moved
    assert widget._studio_state.to_dict() == arrangement
    assert widget._review_dialog.favorite.isChecked()
    assert widget._review_dialog.notes.toPlainText() == "Keep the quieter mix"
    assert widget._take_list.currentItem().text().startswith("★ ")
    assert widget._take_list.currentItem().data(Qt.ItemDataRole.UserRole) == str(moved)
    assert widget._take_list.count() == count + 1
    assert widget.open_take(moved)
    assert widget._take_list.count() == count + 1  # Same explicit path is not duplicated.
    assert {p.relative_to(moved): p.read_bytes() for p in moved.rglob("*") if p.is_file()} == original


@pytest.mark.parametrize("damage", ["wrong_take_id", "changed_manifest", "missing_media", "changed_media", "missing_take"])
def test_moved_take_refuses_wrong_or_damaged_evidence_without_substituting_another_take(moved_take, damage):
    widget, moved, other, reference, _arrangement, _original = moved_take
    count = widget._take_list.count()
    if damage == "wrong_take_id":
        reference["take_id"] = "another-recording"
    elif damage == "changed_manifest":
        manifest = moved / "webjam-take.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    elif damage == "missing_media":
        (moved / "media" / "server.wav").unlink()
    elif damage == "changed_media":
        media = moved / "media" / "server.wav"
        contents = media.read_bytes()
        media.write_bytes(contents[:-1] + bytes([contents[-1] ^ 1]))
    else:
        moved.rename(moved.with_name("elsewhere"))
    assert not widget.jump_to_bookmark(moved, 0.35, reference["take_id"], reference["source_identity"])
    assert widget._current.path == other
    assert widget._take_list.count() == count
    assert not widget._player.is_playing


def test_unsaved_current_arrangement_blocks_registering_or_selecting_moved_take(moved_take):
    widget, moved, other, _reference, _arrangement, _original = moved_take
    count = widget._take_list.count()
    widget._lanes[0]._gain.setValue(60)
    draft = widget._studio_state.to_dict()
    with patch.object(widget, "_flush_studio_state", return_value=False):
        assert not widget.open_take(moved)
    assert widget._current.path == other
    assert widget._take_list.count() == count
    assert widget._studio_state.to_dict() == draft


def test_saved_link_opens_when_configured_library_is_empty(moved_take):
    widget, moved, _other, reference, _arrangement, _original = moved_take
    widget.set_takes_directory("")
    assert widget._take_list.count() == 0
    assert widget.jump_to_bookmark(moved, 0, reference["take_id"], reference["source_identity"])
    assert widget._current.path == moved
    assert widget._take_list.count() == 1
    assert not widget._library.isHidden()
    assert widget._takes_dir == ""
    assert not widget._player.is_playing
