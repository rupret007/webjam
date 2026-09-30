from __future__ import annotations

from unittest.mock import patch

import pytest

from core.take_library import load_take
from core.take_player import TakePlayer
from core.take_review import REVIEW_FILENAME, TakeReviewError, load_take_review
from tests.test_recording_studio import (
    APP, RATE, _SilentSink, _schema2_studio_take, _wait_until,
)
from webjam_qt.widgets.recording_studio import RecordingStudio


@pytest.fixture
def studio(tmp_path):
    first, _ = _schema2_studio_take(tmp_path)
    first = first.rename(tmp_path / "Take A")
    second, _ = _schema2_studio_take(tmp_path)
    second = second.rename(tmp_path / "Take B")
    widget = RecordingStudio(str(tmp_path), player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    assert widget.open_take(first)
    yield widget, first, second
    widget._review_dialog.dirty = False
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    APP.processEvents()


def test_favorite_and_notes_follow_take_across_reorder_and_restart(studio):
    widget, first, second = studio
    before = (first / "webjam-take.json").read_bytes()
    widget._review_dialog.favorite.setChecked(True)
    widget._review_dialog.notes.setPlainText("Best chorus; keep this take.")
    assert widget._flush_take_review()
    widget._takes.reverse()
    widget.reload(select_path=second)
    assert widget._review_dialog.notes.toPlainText() == ""
    assert widget.open_take(first)
    assert widget._review_dialog.favorite.isChecked()
    assert widget._review_dialog.notes.toPlainText() == "Best chorus; keep this take."
    assert any(item.text().startswith("★ ") for item in
               (widget._take_list.item(i) for i in range(widget._take_list.count())))
    saved = load_take_review(load_take(first))
    assert saved.favorite and saved.notes.startswith("Best")
    assert (first / "webjam-take.json").read_bytes() == before


def test_failed_review_save_keeps_draft_and_blocks_take_switch_and_close(studio):
    widget, first, second = studio
    widget._review_dialog.notes.setPlainText("Do not lose this draft")
    with patch("webjam_qt.widgets.studio_take_review_workflow.save_take_review",
               side_effect=TakeReviewError("Save failed")):
        assert not widget.open_take(second)
        assert not widget.prepare_close()
        assert widget._current.path == first
        assert widget._review_dialog.notes.toPlainText() == "Do not lose this draft"
        assert widget._review_dialog.dirty
    assert widget.prepare_close()
    assert (first / REVIEW_FILENAME).exists()


def test_ab_audition_changes_no_saved_arrangement_or_original(studio):
    widget, first, second = studio
    widget._assign_review_slot("A")
    assert widget.open_take(second)
    widget._assign_review_slot("B")
    paths = [p for take in (first, second) for p in take.rglob("*") if p.is_file()]
    before = {p: p.read_bytes() for p in paths}
    with patch.object(widget, "_toggle_play") as play:
        widget._audition_review_slot("A")
        assert widget._current.path == first
        widget._audition_review_slot("B")
        assert widget._current.path == second
        assert play.call_count == 2
    assert {p: p.read_bytes() for p in paths} == before
    assert not widget._studio_controller.dirty


def test_ab_uses_real_verified_playback_without_editing(studio):
    widget, first, second = studio
    widget._assign_review_slot("A")
    assert widget.open_take(second)
    widget._assign_review_slot("B")
    before = widget._studio_state.to_dict()
    widget._audition_review_slot("A")
    assert _wait_until(lambda: widget._player.is_playing)
    assert widget._current.path == first
    widget._audition_review_slot("B")
    assert _wait_until(lambda: widget._player.is_playing)
    assert widget._current.path == second
    assert widget._studio_state.to_dict() == before
    assert not widget._studio_controller.dirty


@pytest.mark.parametrize("damage", ["removed", "media_missing", "manifest_changed", "wrong_take_id"])
def test_ab_refuses_stale_recording_instead_of_substituting_latest(studio, damage):
    widget, first, second = studio
    widget._assign_review_slot("A")
    assert widget.open_take(second)
    if damage == "removed":
        first.rename(first.with_name("moved-away"))
    elif damage == "media_missing":
        (first / "media" / "server.wav").unlink()
    elif damage == "manifest_changed":
        manifest = first / "webjam-take.json"
        manifest.write_text(manifest.read_text() + "\n")
    else:
        widget._review_slots["A"]["take_id"] = "different-take"
    with patch.object(widget, "_toggle_play") as play:
        widget._audition_review_slot("A")
        play.assert_not_called()
    assert widget._current.path == second
    assert "unavailable" in widget._review_dialog.status.text()


def test_bookmark_uses_exact_identity_and_position_without_playing(studio):
    widget, first, second = studio
    reference = widget.current_take_reference()
    assert widget.open_take(second)
    with patch.object(widget, "_toggle_play") as play:
        assert widget.jump_to_bookmark(first, 0.4, reference["take_id"], reference["source_identity"])
        assert widget._player.position_s == pytest.approx(0.4)
        play.assert_not_called()
    assert not widget.jump_to_bookmark(first, float("nan"))
    assert not widget.jump_to_bookmark(first, -1)
    assert not widget.jump_to_bookmark(first, 2)


@pytest.mark.parametrize("originals", [False, True])
def test_real_export_receipt_identifies_sources_settings_destination(studio, originals):
    widget, first, _second = studio
    before = (first / "webjam-take.json").read_bytes()
    with patch("webjam_qt.widgets.recording_studio.studio_export_supported", return_value=not originals):
        widget._export_reviewed_take()
        assert _wait_until(lambda: not widget.export_in_progress, timeout=12)
    assert widget._review_dialog.receipt_button.isEnabled()
    details = widget._review_dialog._receipt_details
    assert "Export verified at completion" in details
    assert str(widget._reveal_path) in details
    assert widget._current.take_id in details
    assert "source" in details and ("gain" in details or "fader_gain" in details)
    assert (first / "webjam-take.json").read_bytes() == before
    # An edit makes the previous export receipt cease to describe this view.
    widget._lanes[0]._gain.setValue(80)
    assert not widget._review_dialog.receipt_button.isEnabled()


def test_failed_export_clears_previous_receipt(studio):
    widget, _first, _second = studio
    widget._review_dialog.set_receipt("Previous export")
    with patch("webjam_qt.widgets.recording_studio.export_studio_arrangement", side_effect=OSError("disk full")):
        widget._export_reviewed_take()
        assert _wait_until(lambda: not widget.export_in_progress)
    assert not widget._review_dialog.receipt_button.isEnabled()
    assert "did not complete" in widget._review_dialog.status.text()


def test_checksum_verification_failure_never_presents_a_completed_receipt(studio):
    from core.export_receipt import ExportReceiptError

    widget, _first, _second = studio
    finished = []
    widget.export_finished.connect(finished.append)
    with patch("webjam_qt.widgets.recording_studio.verify_export_receipt",
               side_effect=ExportReceiptError("changed output")):
        widget._export_reviewed_take()
        assert _wait_until(lambda: not widget.export_in_progress, timeout=12)
    assert finished == [False]
    assert not widget._review_dialog.receipt_button.isEnabled()
    assert "Unverified" in widget._reveal_btn.text()


def test_review_controls_remain_reachable_with_enlarged_text(studio):
    widget, _first, _second = studio
    dialog = widget._review_dialog
    dialog.setStyleSheet("QLabel, QPushButton, QCheckBox {font-size:22px;}")
    dialog.resize(380, 420)
    dialog.show()
    APP.processEvents()
    assert dialog.width() == 380
    assert dialog.height() == 420
    for control in (dialog.notes, dialog.save_button, dialog.export_button, dialog.receipt_button):
        dialog.scroll.ensureWidgetVisible(control)
        APP.processEvents()
        assert control.isVisibleTo(dialog)
        assert dialog.scroll.viewport().rect().intersects(control.rect().translated(
            control.mapTo(dialog.scroll.viewport(), control.rect().topLeft())))
