"""A saved listening mix follows a proved replacement native music process.

ApplicationController, JamulusController, RPC start/stop, epoch snapshots,
registered callbacks, Qt cards and native gain serialization are production
objects. Process publication, authenticated socket receipts and monitor worker
execution are synthetic. No socket, device, meeting, playback or capture runs.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest

from core.jamulus_rpc_client import ChannelInfo, JamulusRpcMonitorSnapshot
from core.settings import AppSettings
from tests import test_music_listening_controls_journey as listening_support
from tests.test_music_gain_dispatch import MemorySocket
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow

qapp = listening_support.qapp
qt_errors = listening_support.qt_errors
set_listening_level = listening_support.set_listening_level


@pytest.fixture(autouse=True)
def logged_callback_errors(caplog):
    """Qt and RPC wrappers log swallowed exceptions instead of raising them."""
    logger = logging.getLogger("webjam")
    # Production disables propagation at this logger, so root-only caplog
    # capture would miss even UiThreadInvoker's ERROR-level exceptions.
    logger.addHandler(caplog.handler)
    for name in ("webjam.ui_thread", "webjam.jamulus_rpc", "webjam.jamulus_controller"):
        caplog.set_level(logging.DEBUG, logger=name)
    try:
        yield
        failures = [
            (record.name, record.levelname, record.getMessage())
            for phase in ("setup", "call", "teardown")
            for record in caplog.get_records(phase)
            if record.levelno >= logging.ERROR
            or "callback error" in record.getMessage().lower()
        ]
        assert not failures, f"Logged callbacks must not hide failed reconnect actions: {failures}"
    finally:
        logger.removeHandler(caplog.handler)


class ProcessReceipt:
    def __init__(self, pid):
        self.pid = pid
        self.alive = True

    def poll(self):
        return None if self.alive else 1


class ControlledWorkers:
    """Keep monitor execution inert; join really drains any held gain closure."""

    def __init__(self):
        self.workers = []
        self.hold_gains = False

    def thread(self, *, target, args=(), name=None, **kwargs):
        kind = (
            "gain" if name == "webjam-listening-mix"
            else "monitor" if getattr(target, "__name__", "") in {"_run_loop", "_monitor_loop"}
            else "unexpected"
        )
        assert kind != "unexpected", f"Unrelated worker cannot start: {name}"
        worker = SimpleNamespace(kind=kind, target=target, started=False, finished=False, joins=0)

        def run():
            if not worker.finished:
                target(*args)
                worker.finished = True

        def start():
            worker.started = True
            if kind == "gain" and not self.hold_gains:
                run()

        def join(timeout=None):
            worker.joins += 1
            if kind == "gain":
                run()
            else:
                # This is a controlled monitor stop receipt. Its real loop
                # never executed and therefore owns no I/O to drain.
                worker.finished = True

        worker.start, worker.join = start, join
        worker.is_alive = lambda: worker.started and not worker.finished
        self.workers.append(worker)
        return worker


def _publish_process(app, process, *, generation, recovery_generation=0):
    """Synthetic receipt at Bridge's existing successful process-publication seam."""
    bridge = app.bridge
    with bridge._reconnect_lock:
        bridge.jamulus_process = process
        bridge._jamulus_process_generation_counter = generation
        bridge._jamulus_process_generation = generation
        bridge._jamulus_process_recovery_generation = recovery_generation
        bridge._jamulus_process_started_at = time.monotonic()
        bridge.jamulus_state = "Running"
    bridge.jamulus_launch_intended = True


def _authenticate_monitor(app, identity):
    """Deliver local authentication/socket facts; use the real typed snapshot."""
    rpc = app.jamulus.rpc_client
    sink = MemorySocket()
    with rpc._state_lock:
        assert rpc._monitor_identity == identity and rpc._running
        rpc._sock = sink
        rpc._sock_epoch = identity.monitor_epoch
        rpc._available = rpc._authed = True
        rpc._last_activity_at = time.monotonic()
    snapshot = rpc.monitor_snapshot()
    assert isinstance(snapshot, JamulusRpcMonitorSnapshot)
    assert snapshot.identity == identity and snapshot.authenticated and snapshot.available
    return sink


def _roster(track=7, collaborator=8, *, local=True):
    remote = [ChannelInfo(track, "WebJam Track"), ChannelInfo(collaborator, "Collaborator")]
    return [ChannelInfo(0, "You", is_local=True), *remote] if local else remote


def _deliver(app, identity, rows):
    # Enter the real RPC callback gate, then its constructor-registered
    # controller callback and the application's registered identity callback.
    app.jamulus.rpc_client._invoke_participant_callbacks(rows, epoch=identity.monitor_epoch)


def _stop_timers(app):
    for timer in app.findChildren(QTimer):
        timer.stop()


def _gains(sink):
    # Authenticated reconnect legitimately republishes the synthetic musician
    # name. Keep all other native methods forbidden, and inspect mixer writes.
    assert {row["method"] for row in sink.messages} <= {
        "jamulusclient/setFaderLevel", "jamulusclient/setName",
    }
    return {
        row["params"]["channelIndex"]: row["params"]["level"]
        for row in sink.messages if row["method"] == "jamulusclient/setFaderLevel"
    }


def _deliver_retired_ui(app, callback, qapp, monkeypatch):
    # Observe the real recorder operations without replacing their behavior.
    # A retired roster must neither revoke current authority nor request a
    # new authenticated recording observation, even when it is empty.
    with monkeypatch.context() as observe:
        invalidate = Mock(wraps=app._invalidate_ordered_recording_presence)
        request = Mock(wraps=app.recording.request_authenticated_roster_observation)
        observe.setattr(app, "_invalidate_ordered_recording_presence", invalidate)
        observe.setattr(app.recording, "request_authenticated_roster_observation", request)
        app._ui_invoker.invoke(callback)
        qapp.processEvents()
        invalidate.assert_not_called()
        request.assert_not_called()


@pytest.fixture
def native_mix(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: True)
    monkeypatch.setattr("webjam_qt.controllers.mix_manager.Path.home", lambda: tmp_path)
    external = Mock(side_effect=AssertionError("A reconnect must not open external media"))
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", external)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(), initial_mode_key="music_jam",
        initial_title="Reconnect listening proof",
    )
    app = ApplicationController(window, settings=AppSettings(
        config_file=str(tmp_path / "settings.json"), mix_file=str(tmp_path / "mix.json"),
        log_file=str(tmp_path / "webjam.log"), host_server_enabled=False,
        last_creator_profile_key="music", musician_name="Fixture musician",
        webex_url="https://personal.webex.com/meet/personal",
    ))
    _stop_timers(app)
    app._set_session_meeting_url("https://meet.google.com/abc-defg-hij")
    guards = [external]
    for owner, name in (
        (app, "_play_reference_track"), (app, "begin_startup_journey"),
        (app, "_launch_native_jamulus_for_startup"), (app, "_start_hosted_server_for_startup"),
        (app.recording, "on_record_requested"), (app.bridge, "launch_webex"),
        (app.bridge, "launch_jamulus"), (app.jamulus.audio_engine, "start"),
    ):
        guard = Mock(side_effect=AssertionError("Reconnect proof cannot start another activity"))
        monkeypatch.setattr(owner, name, guard)
        guards.append(guard)
    workers = ControlledWorkers()
    monkeypatch.setattr("jamulus_controller.threading.Thread", workers.thread)
    process = ProcessReceipt(7101)
    _publish_process(app, process, generation=1)
    app.jamulus.set_live_audio_route_owned(True)
    try:
        identity = app.jamulus.start(process_generation=1, process_id=process.pid)
        sink = _authenticate_monitor(app, identity)
        _deliver(app, identity, _roster())
        window.show()
        qapp.processEvents()
        _stop_timers(app)
        assert app._jamulus_connected
        assert app.jamulus.protocol.enabled is False
        assert set(window.participant_grid._cards) == {0, 7, 8}
        yield SimpleNamespace(app=app, window=window, workers=workers, process=process,
                              identity=identity, sink=sink, home=tmp_path)
        for guard in guards:
            guard.assert_not_called()
        assert app._reference_track is None
        assert app._effective_meeting_url() == "https://meet.google.com/abc-defg-hij"
        assert app.settings.webex_url == "https://personal.webex.com/meet/personal"
    finally:
        app.jamulus.stop()
        app._mix_dirty = False
        app.bridge.jamulus_process = None
        app.bridge.jamulus_launch_intended = False
        assert app.shutdown()
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize(
    "replacement_kind,old_delivery,queued_empty",
    [
        ("process", "before-local-proof", False),
        ("process", "after-local-proof", False),
        ("monitor", "after-local-proof", False),
        ("process", "after-local-proof", True),
        ("monitor", "after-local-proof", True),
    ],
    ids=["process-before-proof", "process-after-proof", "monitor-after-proof",
         "process-old-empty", "monitor-old-empty"],
)
def test_new_primary_owner_restores_saved_solo_without_old_roster_or_gain_authority(
    native_mix, qapp, monkeypatch, replacement_kind, old_delivery, queued_empty,
):
    pair = native_mix
    app, grid = pair.app, pair.window.participant_grid
    track, collaborator = grid._cards[7], grid._cards[8]
    set_listening_level(track, 40)
    set_listening_level(collaborator, 55)
    QTest.mouseClick(track._mute_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(collaborator._solo_button, Qt.MouseButton.LeftButton)
    pair.window._save_mix_shortcut.activated.emit()
    assert (pair.home / ".webjam_mix.json").is_file()
    assert track._mute_button.isChecked() and collaborator._solo_button.isChecked()

    # Hold a genuine UI delivery and a native gain worker from the first
    # owner. The old fader differs from the saved choice to expose a replay.
    pair.workers.hold_gains = True
    set_listening_level(track, 95)
    old_gain = next(worker for worker in reversed(pair.workers.workers) if worker.kind == "gain")
    assert old_gain.is_alive()
    queued = []
    with monkeypatch.context() as hold:
        hold.setattr(app._ui_invoker, "invoke", queued.append)
        _deliver(app, pair.identity, [] if queued_empty else _roster())
    assert len(queued) == 1
    pair.sink.messages.clear()

    # Loss and monitor retirement are real operations. The replacement's
    # process publication is a controlled receipt, never a connected flag.
    _deliver(app, pair.identity, [])
    qapp.processEvents()
    assert not app._jamulus_connected and app.audio.recovering
    if replacement_kind == "process":
        pair.process.alive = False
    with app.bridge._reconnect_lock:
        recovery_generation = app.bridge._begin_jamulus_recovery_locked()
    app.jamulus.stop()
    assert old_gain.finished and old_gain.joins == 1
    assert pair.sink.messages == [], "Stop must retire queued old-owner gains before restart"
    pair.workers.hold_gains = False

    replacement = ProcessReceipt(7102) if replacement_kind == "process" else pair.process
    process_generation = 2 if replacement_kind == "process" else 1
    _publish_process(app, replacement, generation=process_generation, recovery_generation=recovery_generation)
    identity = app.jamulus.start(process_generation=process_generation, process_id=replacement.pid)
    assert identity.process_generation == process_generation and identity.process_id == replacement.pid
    assert identity.monitor_epoch > pair.identity.monitor_epoch
    sink = _authenticate_monitor(app, identity)
    _deliver(app, identity, _roster(17, 18, local=False))
    qapp.processEvents()
    assert not app._jamulus_connected and app.audio.recovering
    assert set(grid._cards) == {17, 18}
    assert app.jamulus.participants[17].fader_level == 100
    assert sink.messages == [], "Remote-only roster cannot restore a local saved mix"

    if old_delivery == "before-local-proof":
        _deliver_retired_ui(app, queued[0], qapp, monkeypatch)
        assert not app._jamulus_connected
        assert set(grid._cards) == {17, 18}, "Old process UI delivery cannot replace the new roster"
        assert sink.messages == []

    _deliver(app, identity, _roster(17, 18))
    qapp.processEvents()
    _stop_timers(app)
    assert app._jamulus_connected and not app.audio.recovering
    recovery = app.bridge.jamulus_recovery_snapshot()
    assert recovery.process_id == replacement.pid and recovery.generation == process_generation
    assert not recovery.active
    assert set(grid._cards) == {0, 17, 18}
    assert grid._cards[17]._fader.value() == 40 and grid._cards[17]._mute_button.isChecked()
    assert grid._cards[18]._fader.value() == 55 and grid._cards[18]._solo_button.isChecked()
    assert _gains(sink)[17] == 0 and _gains(sink)[18] == 43
    assert not ({7, 8} & set(_gains(sink)))

    if old_delivery == "after-local-proof":
        sink.messages.clear()
        _deliver_retired_ui(app, queued[0], qapp, monkeypatch)
        assert app._jamulus_connected, "An old process UI delivery cannot demote the proved replacement"
        assert set(grid._cards) == {0, 17, 18}
        assert sink.messages == []

    # The old RPC epoch is also refused at the real serializer's final send
    # boundary; an old participant callback cannot modify the native model.
    sink.messages.clear()
    assert not app.jamulus.rpc_client.set_channel_gain(7, 127, epoch=pair.identity.monitor_epoch)
    _deliver(app, pair.identity, _roster())
    qapp.processEvents()
    assert sink.messages == [] and set(app.jamulus.participants) == {0, 17, 18}
    QTest.mouseClick(grid._cards[18]._solo_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not grid._cards[18]._solo_button.isChecked()
    assert grid._cards[17]._mute_button.isChecked(), "Saved personal mute must survive ending Solo"
    assert _gains(sink)[17] == 0 and _gains(sink)[18] == 43
    QTest.mouseClick(grid._cards[17]._mute_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert grid._cards[17]._fader.value() == 40
    assert _gains(sink)[17] == 31
    assert app._jamulus_connected and app.bridge.jamulus_process is replacement

    # Current-owner loss remains authoritative even if the process died before
    # its final queued empty roster arrived. Stale rejection must not become a
    # blanket "process must still be alive" requirement on loss notifications.
    replacement.alive = False
    _deliver(app, identity, [])
    qapp.processEvents()
    _stop_timers(app)
    assert not app._jamulus_connected and app.audio.recovering
    assert grid._cards == {}
