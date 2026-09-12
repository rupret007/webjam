"""Visible listening controls retain Mute/Solo through real native commands.

Qt cards, application handlers, JamulusController and RPC gain serialization are
production objects. The native send is an in-memory sink, gain workers run
inline, and process identity/roster receipts are controlled synthetic seams.
No sound, device, socket, recording or session is started by these journeys.
"""
from __future__ import annotations

import os
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.jamulus_rpc_client import JamulusRpcClient
from core.settings import AppSettings
from tests.test_reference_track_application_integration import (
    _primary_source_identity,
    _set_primary_rpc,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def qt_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(kind.__name__))
    yield
    assert errors == [], f"Qt callbacks must not hide failed mixer actions: {errors}"


@pytest.fixture
def listening(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: True)
    monkeypatch.setattr("webjam_qt.controllers.mix_manager.Path.home", lambda: tmp_path)
    external = Mock(side_effect=AssertionError("Listening controls cannot open external media"))
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", external)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(), initial_mode_key="music_jam",
        initial_title="Listening mix test",
    )
    app = ApplicationController(window, settings=AppSettings(
        config_file=str(tmp_path / "settings.json"), mix_file=str(tmp_path / "mix.json"),
        log_file=str(tmp_path / "webjam.log"), host_server_enabled=False,
        last_creator_profile_key="music",
    ))
    for timer in app.findChildren(QTimer):
        timer.stop()
    primary = Mock(pid=4241)
    primary.poll.return_value = None
    app.bridge.jamulus_process = primary
    app.bridge.jamulus_state = "Running"
    original_rpc = app.jamulus.rpc_client
    rpc = _set_primary_rpc(app)
    commands = []
    rpc.GAIN_RANGE_IN = JamulusRpcClient.GAIN_RANGE_IN
    rpc.FADER_MAX = JamulusRpcClient.FADER_MAX

    def send(method, params, *, epoch=None):
        assert epoch == 1
        assert method == "jamulusclient/setFaderLevel"
        commands.append(dict(params))
        return 1

    rpc._send = send
    rpc.set_channel_gain = MethodType(JamulusRpcClient.set_channel_gain, rpc)
    guards = []
    for owner, name in (
        (app, "_play_reference_track"), (app, "begin_startup_journey"),
        (app, "_launch_native_jamulus_for_startup"),
        (app, "_start_hosted_server_for_startup"),
        (app.recording, "on_record_requested"), (app.bridge, "launch_webex"),
        (app.bridge, "launch_jamulus"),
    ):
        guard = Mock(side_effect=AssertionError("A listening gesture must not start another activity"))
        monkeypatch.setattr(owner, name, guard)
        guards.append(guard)

    identity = _primary_source_identity(app)
    rpc.monitor_snapshot.return_value = app.jamulus.rpc_monitor_snapshot_for(
        process_generation=identity.process_generation, process_id=identity.process_id,
    )
    # These are the controlled receipt fields established by native start.
    # Deliver through the real identity callback already registered by the app;
    # do not add a legacy callback which production does not subscribe to.
    app.jamulus.running = True
    app.jamulus._rpc_monitor_identity = identity
    roster = [
        {"channel_id": 0, "name": "You", "is_local": True},
        {"channel_id": 7, "name": "WebJam Track", "is_local": False},
        {"channel_id": 8, "name": "Collaborator", "is_local": False},
    ]
    app.jamulus._on_rpc_participants_with_source(roster, identity)
    window.show()
    qapp.processEvents()
    for timer in app.findChildren(QTimer):
        timer.stop()
    assert app._jamulus_connected
    assert app.jamulus.protocol.enabled is False
    assert set(window.participant_grid._cards) == {0, 7, 8}

    class InlineGainWorker:
        def __init__(self, *, target, name=None, **kwargs):
            assert name == "webjam-listening-mix", "Only the bounded listening worker is permitted"
            self.target = target

        def start(self):
            self.target()

    try:
        with monkeypatch.context() as mix_patch:
            mix_patch.setattr("jamulus_controller.threading.Thread", InlineGainWorker)
            yield SimpleNamespace(app=app, window=window, commands=commands, primary=primary,
                                  identity=identity, roster=roster)
        for guard in guards:
            guard.assert_not_called()
        external.assert_not_called()
        assert app.bridge.jamulus_process is primary and app._jamulus_connected
        assert app._reference_track is None
    finally:
        app.jamulus.rpc_client = original_rpc
        app._jamulus_connected = False
        app._mix_dirty = False
        app.bridge.jamulus_process = None
        app.bridge.jamulus_launch_intended = False
        assert app.shutdown()
        window.close()
        window.deleteLater()
        qapp.processEvents()


def set_listening_level(card, level):
    """Choose a real slider level with keyboard gestures, retaining its focus."""
    assert level % 5 == 0
    card._fader.setFocus()
    QTest.keyClick(card._fader, Qt.Key.Key_Home)
    for _ in range(level // 5):
        QTest.keyClick(card._fader, Qt.Key.Key_Right)
    assert card._fader.value() == level


@pytest.mark.parametrize("channel", [7, 8], ids=["reference-track", "collaborator"])
def test_muted_listening_fader_stays_silent_until_explicit_unmute(listening, qapp, channel):
    pair = listening
    card = pair.window.participant_grid._cards[channel]
    QTest.mouseClick(card._mute_button, Qt.MouseButton.LeftButton)
    assert card._mute_button.isChecked()
    pair.commands.clear()
    set_listening_level(card, 40)
    qapp.processEvents()
    assert card._mute_button.isChecked()
    assert pair.app.jamulus.participants[channel].muted
    assert pair.app.jamulus.participants[channel].fader_level == 40
    assert pair.commands and {command["channelIndex"] for command in pair.commands} == {channel}
    assert all(command["level"] == 0 for command in pair.commands), (
        "Moving a muted listening fader must store its level without sending audible gain", pair.commands,
    )
    QTest.mouseClick(card._mute_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not card._mute_button.isChecked()
    assert not pair.app.jamulus.participants[channel].muted
    assert pair.commands[-1] == {"channelIndex": channel, "level": 31}
    assert card._fader.value() == 40
    other = 8 if channel == 7 else 7
    assert pair.app.jamulus.participants[other].fader_level == 100
    assert not pair.app.jamulus.participants[other].muted


def test_solo_suppressed_track_keeps_chosen_level_until_solo_released(listening, qapp):
    pair = listening
    track = pair.window.participant_grid._cards[7]
    collaborator = pair.window.participant_grid._cards[8]
    QTest.mouseClick(collaborator._solo_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert collaborator._solo_button.isChecked() and track._mute_button.isChecked()
    pair.commands.clear()
    set_listening_level(track, 55)
    qapp.processEvents()
    assert track._mute_button.isChecked() and collaborator._solo_button.isChecked()
    assert pair.commands and all(command == {"channelIndex": 7, "level": 0} for command in pair.commands)
    # Unmute during another channel's Solo changes the post-Solo preference,
    # but the effective current mute must remain checked and silent.
    QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert track._mute_button.isChecked() and collaborator._solo_button.isChecked()
    assert pair.commands[-1] == {"channelIndex": 7, "level": 0}
    QTest.mouseClick(collaborator._solo_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not collaborator._solo_button.isChecked() and not track._mute_button.isChecked()
    assert track._fader.value() == 55
    last_track = next(command for command in reversed(pair.commands) if command["channelIndex"] == 7)
    assert last_track["level"] == 43
    assert pair.app.jamulus.participants[8].fader_level == 100


def test_unmuted_track_keyboard_and_mouse_reset_change_only_its_listening_gain(listening, qapp):
    pair = listening
    track = pair.window.participant_grid._cards[7]
    pair.commands.clear()
    set_listening_level(track, 45)
    assert pair.commands[-1] == {"channelIndex": 7, "level": 35}
    QTest.mouseDClick(track._fader, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert track._fader.value() == 100
    assert pair.commands[-1] == {"channelIndex": 7, "level": 79}
    assert {command["channelIndex"] for command in pair.commands} == {7}
    assert not track._mute_button.isChecked()
    assert pair.app.jamulus.participants[8].fader_level == 100
    assert not pair.app.jamulus.participants[8].muted


@pytest.mark.parametrize("previously_muted", [False, True])
def test_soloed_collaborator_departure_restores_prior_listening_state_without_feedback(
    listening, qapp, previously_muted,
):
    pair = listening
    grid = pair.window.participant_grid
    track, collaborator = grid._cards[7], grid._cards[8]
    set_listening_level(track, 45)
    if previously_muted:
        QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(collaborator._solo_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert track._mute_button.isChecked() and collaborator._solo_button.isChecked()
    pair.commands.clear()
    # The production participant merge notices the soloed row leaving and
    # feeds the actual application projection through the registered callback.
    pair.app.jamulus._on_rpc_participants_with_source(pair.roster[:2], pair.identity)
    qapp.processEvents()
    assert 8 not in grid._cards
    assert track._fader.value() == 45
    assert track._mute_button.isChecked() is previously_muted
    assert not track._solo_button.isChecked()
    last_track = next(command for command in reversed(pair.commands) if command["channelIndex"] == 7)
    assert last_track["level"] == (0 if previously_muted else 35)

    # Repainting an unchanged roster cannot emit user intentions or create
    # more native gain commands. Projection is one-way and does not replay a mix.
    gestures = []
    grid.fader_changed.connect(lambda *args: gestures.append("fader"))
    grid.mute_toggled.connect(lambda *args: gestures.append("mute"))
    grid.solo_toggled.connect(lambda *args: gestures.append("solo"))
    pair.commands.clear()
    for _ in range(3):
        pair.app.jamulus._on_rpc_participants_with_source(pair.roster[:2], pair.identity)
        qapp.processEvents()
    assert pair.commands == [] and gestures == []
    assert track._mute_button.isChecked() is previously_muted


def test_queued_identity_roster_cannot_restore_an_older_listening_fader(listening, qapp, monkeypatch):
    pair = listening
    track = pair.window.participant_grid._cards[7]
    set_listening_level(track, 40)
    queued = []
    with monkeypatch.context() as hold:
        # Hold the application's real queued roster delivery after the native
        # controller has detached its model snapshot and bound the identity.
        hold.setattr(pair.app._ui_invoker, "invoke", queued.append)
        pair.app.jamulus._on_rpc_participants_with_source(pair.roster, pair.identity)
        assert len(queued) == 1
        set_listening_level(track, 70)
        QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    assert track._fader.value() == 70 and track._mute_button.isChecked()
    pair.commands.clear()
    queued[0]()
    qapp.processEvents()
    assert pair.app._jamulus_connected
    assert track._fader.value() == 70 and track._mute_button.isChecked()
    assert pair.app.jamulus.participants[7].fader_level == 70
    assert pair.app.jamulus.participants[7].muted
    assert pair.commands == []
