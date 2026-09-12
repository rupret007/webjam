"""Saved listening mixes cross real ApplicationController and Qt boundaries.

The reused fixture binds the application's actual registered identity callback,
real participant cards and native gain serializer. Process receipts, native
sends and worker scheduling are controlled; no media, sockets or devices run.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest

from tests import test_music_listening_controls_journey as listening_support

# Reuse the production-controller fixture without adding another callback path.
listening = listening_support.listening
qapp = listening_support.qapp
qt_errors = listening_support.qt_errors
set_listening_level = listening_support.set_listening_level


def _saved_mix():
    return {"participants": [
        {"channel_id": 7, "name": "WebJam Track", "fader_level": 40,
         "muted": True, "solo": False},
        {"channel_id": 8, "name": "Collaborator", "fader_level": 55,
         "muted": False, "solo": False},
    ]}


def _save_payload(tmp_path, *, named=False, payload=None):
    path = tmp_path / ("rehearsal-mix.json" if named else ".webjam_mix.json")
    path.write_text(json.dumps(_saved_mix() if payload is None else payload), encoding="utf-8")
    return path


def _activate_load(pair, monkeypatch, *, path=None):
    if path is None:
        pair.window._load_mix_shortcut.activated.emit()
    else:
        # The native picker is the only substituted interaction: the real Qt
        # shortcut is already wired to the production Load Mix From handler.
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(path), "Mix files (*.json)"),
        )
        pair.window._load_mix_from_shortcut.activated.emit()


def _assert_restored(pair, *, track_id=7, collaborator_id=8):
    track = pair.window.participant_grid._cards[track_id]
    collaborator = pair.window.participant_grid._cards[collaborator_id]
    assert track._fader.value() == 40 and track._mute_button.isChecked(), (
        "Successful Load must immediately show the restored muted Track level",
        track._fader.value(), track._mute_button.isChecked(),
    )
    assert collaborator._fader.value() == 55
    assert not collaborator._mute_button.isChecked()
    assert not track._solo_button.isChecked() and not collaborator._solo_button.isChecked()
    gains = {command["channelIndex"]: command["level"] for command in pair.commands}
    assert gains.get(track_id) == 0 and gains.get(collaborator_id) == 43, (
        "Restored cards must agree with actual native listening commands", gains,
    )
    assert pair.app.jamulus.participants[track_id].fader_level == 40
    assert pair.app.jamulus.participants[track_id].muted
    assert pair.app.jamulus.participants[collaborator_id].fader_level == 55


def _watch_user_intentions(pair):
    gestures = []
    grid = pair.window.participant_grid
    grid.fader_changed.connect(lambda *args: gestures.append("fader"))
    grid.mute_toggled.connect(lambda *args: gestures.append("mute"))
    grid.solo_toggled.connect(lambda *args: gestures.append("solo"))
    return gestures


@pytest.mark.parametrize("named", [False, True], ids=["default-slot", "named-file"])
def test_load_shortcut_restores_cards_and_native_gains_before_another_roster(
    listening, qapp, monkeypatch, tmp_path, named,
):
    pair = listening
    set_listening_level(pair.window.participant_grid._cards[7], 95)
    set_listening_level(pair.window.participant_grid._cards[8], 90)
    path = _save_payload(tmp_path, named=named)
    gestures = _watch_user_intentions(pair)
    pair.commands.clear()
    _activate_load(pair, monkeypatch, path=path if named else None)
    qapp.processEvents()
    _assert_restored(pair)
    assert gestures == [], "Restoring cards must not manufacture user fader/mute/solo gestures"
    assert pair.app._jamulus_connected
    assert pair.window.participant_grid._cards[0]._fader.value() == 100
    assert 0 not in {command["channelIndex"] for command in pair.commands}


def test_authenticated_reconnect_restores_mix_with_new_channel_ids_only_after_local_proof(
    listening, qapp, tmp_path,
):
    pair = listening
    _save_payload(tmp_path)
    # Deliver the real authoritative empty roster; the existing loss handler
    # owns connected -> recovering, without forcing a connection flag in test.
    pair.app.jamulus._on_rpc_participants_with_source([], pair.identity)
    qapp.processEvents()
    assert not pair.app._jamulus_connected and pair.app.audio.recovering
    pair.commands.clear()
    renamed_channels = [
        {"channel_id": 17, "name": "WebJam Track", "is_local": False},
        {"channel_id": 18, "name": "Collaborator", "is_local": False},
    ]
    pair.app.jamulus._on_rpc_participants_with_source(renamed_channels, pair.identity)
    qapp.processEvents()
    assert not pair.app._jamulus_connected
    assert pair.commands == [], "A remote-only roster cannot auto-restore the local mix"
    assert pair.app.jamulus.participants[17].fader_level == 100

    pair.app.jamulus._on_rpc_participants_with_source(
        [{"channel_id": 0, "name": "You", "is_local": True}, *renamed_channels],
        pair.identity,
    )
    qapp.processEvents()
    for timer in pair.app.findChildren(QTimer):
        timer.stop()
    assert pair.app._jamulus_connected and not pair.app.audio.recovering
    assert set(pair.window.participant_grid._cards) == {0, 17, 18}
    _assert_restored(pair, track_id=17, collaborator_id=18)
    assert {command["channelIndex"] for command in pair.commands} == {17, 18}


def test_queued_roster_cannot_undo_explicit_saved_mix_or_replay_native_gains(
    listening, qapp, monkeypatch, tmp_path,
):
    pair = listening
    set_listening_level(pair.window.participant_grid._cards[7], 95)
    _save_payload(tmp_path)
    queued = []
    gestures = _watch_user_intentions(pair)
    pair.commands.clear()
    with monkeypatch.context() as hold:
        hold.setattr(pair.app._ui_invoker, "invoke", queued.append)
        pair.app.jamulus._on_rpc_participants_with_source(pair.roster, pair.identity)
        assert len(queued) == 1
        _activate_load(pair, monkeypatch)
    _assert_restored(pair)
    pair.commands.clear()
    queued[0]()
    qapp.processEvents()
    assert pair.window.participant_grid._cards[7]._fader.value() == 40
    assert pair.window.participant_grid._cards[7]._mute_button.isChecked()
    assert pair.window.participant_grid._cards[8]._fader.value() == 55
    assert pair.commands == [] and gestures == []
    assert pair.app._jamulus_connected


@pytest.mark.parametrize("mute_during_solo", [False, True], ids=["muted-before-solo", "muted-during-solo"])
def test_save_load_solo_preserves_immediate_and_after_solo_listening_choices(
    listening, qapp, monkeypatch, tmp_path, mute_during_solo,
):
    pair = listening
    track = pair.window.participant_grid._cards[7]
    collaborator = pair.window.participant_grid._cards[8]
    set_listening_level(track, 40)
    set_listening_level(collaborator, 55)
    if not mute_during_solo:
        QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(track._solo_button, Qt.MouseButton.LeftButton)
    if mute_during_solo:
        QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    assert track._solo_button.isChecked()
    assert track._mute_button.isChecked() is mute_during_solo
    pair.window._save_mix_shortcut.activated.emit()
    assert (tmp_path / ".webjam_mix.json").is_file()

    # Leave a visibly different current mix before loading the actual saved
    # document, rather than constructing a payload that mirrors its schema.
    QTest.mouseClick(track._solo_button, Qt.MouseButton.LeftButton)
    assert track._mute_button.isChecked()
    QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    set_listening_level(track, 95)
    pair.commands.clear()
    _activate_load(pair, monkeypatch)
    qapp.processEvents()
    assert track._fader.value() == 40 and track._solo_button.isChecked()
    assert track._mute_button.isChecked() is mute_during_solo
    assert collaborator._mute_button.isChecked()
    gains = {command["channelIndex"]: command["level"] for command in pair.commands}
    assert gains[7] == (0 if mute_during_solo else 31)
    assert gains[8] == 0

    pair.commands.clear()
    QTest.mouseClick(track._solo_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not track._solo_button.isChecked() and track._mute_button.isChecked()
    assert track._fader.value() == 40
    assert not collaborator._mute_button.isChecked() and collaborator._fader.value() == 55
    gains = {command["channelIndex"]: command["level"] for command in pair.commands}
    assert gains[7] == 0 and gains[8] == 43


@pytest.mark.parametrize("failure", ["cancel", "missing", "corrupt", "no-match"])
def test_unsuccessful_named_load_preserves_mix_and_never_reports_success(
    listening, qapp, monkeypatch, tmp_path, failure,
):
    pair = listening
    set_listening_level(pair.window.participant_grid._cards[7], 65)
    path = tmp_path / "chosen-mix.json"
    if failure == "corrupt":
        path.write_text("{not JSON", encoding="utf-8")
    elif failure == "no-match":
        _save_payload(tmp_path, named=True, payload={"participants": [{
            "channel_id": 77, "name": "Absent musician", "fader_level": 40,
            "muted": True,
        }]})
        path = tmp_path / "rehearsal-mix.json"
    flash = Mock()
    monkeypatch.setattr(pair.app._mix_manager, "_flash", flash)
    gestures = _watch_user_intentions(pair)
    before = pair.app.jamulus.serialize_mix()
    pair.commands.clear()
    _activate_load(pair, monkeypatch, path="" if failure == "cancel" else path)
    qapp.processEvents()
    assert pair.app.jamulus.serialize_mix() == before
    assert pair.window.participant_grid._cards[7]._fader.value() == 65
    assert pair.commands == [] and gestures == []
    if failure == "cancel":
        flash.assert_not_called()
    else:
        assert flash.call_count == 1
        assert "loaded" not in flash.call_args.args[0].lower(), (
            "No matched participant or unreadable file cannot be reported as loaded",
            flash.call_args,
        )
