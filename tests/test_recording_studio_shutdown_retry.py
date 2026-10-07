"""Studio retains failed cleanup ownership until an explicit retry succeeds."""
from __future__ import annotations

import threading
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.take_player import PlaybackError, SoundDeviceSink, StudioPlaybackPreparation, TakePlayer
from services.session_workspace_packaged_smoke import _MemorySink
from webjam_qt.widgets.recording_studio import RecordingStudio, _PlaybackPreparationOutcome
from tests.test_workspace_navigation import navigation  # noqa: F401


@pytest.fixture
def studio(tmp_path):
    app = QApplication.instance() or QApplication([])
    widget = RecordingStudio(str(tmp_path), player=TakePlayer(sink=_MemorySink()))
    # Actual threads make a completion latch alone insufficient evidence.
    for executor in (widget._waveform_executor, widget._playback_prepare_executor):
        executor.submit(lambda: None).result(timeout=2)
    yield widget
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("resource,method", [
    ("_studio_controller", "shutdown"), ("_studio_waveforms", "shutdown"),
    ("_waveform_executor", "shutdown"), ("_playback_prepare_executor", "shutdown"),
    ("_player", "stop"),
])
@pytest.mark.parametrize("failure", ["exception", "false"])
def test_partial_cleanup_is_retryable_until_every_resource_releases(studio, monkeypatch, resource, method, failure):
    owner = getattr(studio, resource)
    original = getattr(owner, method)
    allow_close = False

    def close(*args, **kwargs):
        if not allow_close:
            if failure == "exception":
                raise RuntimeError("controlled resource release failure")
            return False
        return original(*args, **kwargs)

    close_spy = Mock(side_effect=close)
    monkeypatch.setattr(owner, method, close_spy)
    if failure == "exception":
        with pytest.raises(RuntimeError, match="controlled resource"):
            studio.shutdown()
    else:
        assert studio.shutdown() is False
    assert studio._waveform_shutdown and not studio._shutdown_complete
    assert not studio._timer.isActive()
    assert not studio._legacy_ruler_alignment_timer.isActive()
    allow_close = True
    assert studio.shutdown() is True
    assert close_spy.call_count == 2
    assert studio._shutdown_complete
    assert studio._studio_controller.is_shutdown
    assert studio._studio_waveforms.stats.shutdown
    for executor in (studio._waveform_executor, studio._playback_prepare_executor):
        assert executor._shutdown
        for thread in executor._threads:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert studio.shutdown() is True
    assert close_spy.call_count == 2  # Completed teardown is a true no-op.


@pytest.mark.parametrize("kind", ["pipeline", "stream"])
@pytest.mark.parametrize("failure", ["exception", "false"])
def test_preparation_close_keeps_ownership_on_failure(kind, failure):
    allow_close = False

    def close():
        if not allow_close:
            if failure == "exception":
                raise RuntimeError("controlled preparation failure")
            return False
        return True

    close_spy = Mock(side_effect=close)
    stream = SimpleNamespace(close=close_spy)
    pipeline = SimpleNamespace(stop=close_spy) if kind == "pipeline" else None
    preparation = StudioPlaybackPreparation(object(), stream, pipeline)
    assert preparation.close() is False
    assert not preparation._installed
    allow_close = True
    assert preparation.close() is True
    assert preparation._installed
    assert preparation.close() is True
    assert close_spy.call_count == 2


@pytest.mark.parametrize("route", ["queued", "stale", "cancelled", "rejected"])
def test_unreleased_preparation_stays_owned_and_blocks_retirement(studio, monkeypatch, tmp_path, route):
    release = threading.Event()
    pipeline = SimpleNamespace(stop=Mock(side_effect=lambda: release.is_set()))
    preparation = StudioPlaybackPreparation(object(), SimpleNamespace(close=Mock()), pipeline)
    player = studio._player
    outcome = _PlaybackPreparationOutcome(0, tmp_path / "old-take", preparation)
    if route in {"queued", "stale"}:
        studio._playback_prepare_results.put(outcome)
        if route == "stale":
            studio._drain_playback_preparation_results()
    elif route == "rejected":
        assert player.install_studio_preparation(preparation) is False
    else:
        # Return a preparation from a real executor only after shutdown has
        # canceled the exact generation. Its worker must not discard it.
        started = threading.Event()
        studio._current = SimpleNamespace(path=tmp_path / "take")
        player._studio_renderer = object()

        def prepare(cancel_check):
            started.set()
            assert studio._playback_prepare_cancel.wait(2)
            return preparation

        monkeypatch.setattr(player, "prepare_studio_playback", prepare)
        studio._begin_playback_preparation(autoplay=False)
        assert started.wait(2)
    try:
        with pytest.raises(PlaybackError, match="did not stop"):
            studio.shutdown()
        assert not studio._shutdown_complete
        assert player._retired_studio_preparations == [preparation]
        assert not preparation._installed
        with pytest.raises(PlaybackError, match="still shutting down"):
            TakePlayer.prepare_studio_playback(player)
    finally:
        release.set()
        assert studio.shutdown()
        studio._current = None
    assert not player._retired_studio_preparations
    assert preparation._installed


def test_failed_preparation_construction_retains_its_producer_until_stop_retry(monkeypatch):
    player = TakePlayer(sink=_MemorySink())
    release = threading.Event()
    stream = SimpleNamespace(close=Mock())
    player._studio_renderer = SimpleNamespace(open=Mock(return_value=stream))
    pipeline = SimpleNamespace(
        start=Mock(side_effect=RuntimeError("controlled preparation construction")),
        stop=Mock(side_effect=lambda: release.is_set()),
    )
    monkeypatch.setattr(player, "_new_studio_pipeline", lambda *args, **kwargs: pipeline)
    with pytest.raises(RuntimeError, match="controlled preparation construction"):
        player.prepare_studio_playback()
    assert len(player._retired_studio_preparations) == 1
    with pytest.raises(PlaybackError, match="did not stop"):
        player.stop()
    release.set()
    player.stop()
    assert not player._retired_studio_preparations


@pytest.mark.parametrize("failure", ["exception", "false"])
def test_return_to_launch_retains_studio_owner_until_cleanup_retry(navigation, monkeypatch, failure):  # noqa: F811
    navigator, _mailbox, _controllers, _app = navigation
    controller = navigator.controller
    widget = controller.window.recording_studio
    original_stop = widget._player.stop
    allow_close = False

    def stop():
        if not allow_close:
            if failure == "exception":
                raise RuntimeError("controlled playback close failure")
            return False
        return original_stop()

    monkeypatch.setattr(widget._player, "stop", stop)
    monkeypatch.setattr(QMessageBox, "information", Mock())
    try:
        assert not navigator.return_to_launch()
        assert navigator.controller is controller
        assert navigator.window.isVisible() and navigator.launch is None
        assert controller._shutdown_cleanup_pending and not controller._shutdown
        assert widget._waveform_shutdown and not widget._shutdown_complete
    finally:
        allow_close = True
    assert navigator.return_to_launch()
    assert controller._shutdown and widget._shutdown_complete
    assert navigator.controller is None and navigator.launch.isVisible()


@pytest.mark.parametrize("failure", ["exception", "false"])
def test_failed_retirement_cannot_accept_review_edits_after_its_final_save(
    navigation, monkeypatch, tmp_path, failure,  # noqa: F811
):
    from core.take_library import load_take
    from core.take_review import load_take_review
    from tests.test_recording_studio import _schema2_studio_take

    navigator, _mailbox, _controllers, app = navigation
    controller = navigator.controller
    widget = controller.window.recording_studio
    take, _ = _schema2_studio_take(tmp_path)
    assert widget.open_take(take)
    controller._on_rail_view_changed("takes")
    widget._open_take_review()
    review = widget._review_dialog
    review.notes.setPlainText("Keep the review before cleanup.")
    review.favorite.setChecked(True)
    assert review.dirty
    original_stop = widget._player.stop
    allow_close = False

    def stop():
        if not allow_close:
            if failure == "exception":
                raise RuntimeError("controlled playback close failure")
            return False
        return original_stop()

    monkeypatch.setattr(widget._player, "stop", stop)
    monkeypatch.setattr(QMessageBox, "information", Mock())
    try:
        assert not navigator.return_to_launch()
        assert controller._shutdown_cleanup_pending
        assert widget._waveform_shutdown and not widget._shutdown_complete
        before = load_take_review(load_take(take))
        review.notes.setFocus()
        QTest.keyClicks(review.notes, "Must not become an unsaved edit")
        QTest.mouseClick(review.favorite, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert review.notes.toPlainText() == before.notes
        assert review.favorite.isChecked() == before.favorite
        assert not review.dirty
        assert not widget.isEnabled() and not review.isEnabled()
        # Cleanup freezes the retired editor, not its window's safe exits.
        window = controller.window
        assert window.session_canvas.isEnabled()
        assert window._return_to_launch_action.isEnabled()
        assert window._workflow_help_action.isEnabled()
        window._workflow_help_action.trigger()
        app.processEvents()
        help_dialog = window._workflow_help_dialog
        assert help_dialog.isVisible() and help_dialog.isEnabled()
        help_dialog.close()
    finally:
        allow_close = True
    controller.window._return_to_launch_action.trigger()
    assert navigator.controller is None and navigator.launch.isVisible()
    after = load_take_review(load_take(take))
    assert after.notes == "Keep the review before cleanup." and after.favorite


def test_failed_review_save_keeps_editor_and_recovery_actions_available(studio, monkeypatch, tmp_path):
    from core.take_library import load_take
    from core.take_review import TakeReviewError, load_take_review
    from tests.test_recording_studio import _schema2_studio_take

    take, _ = _schema2_studio_take(tmp_path / "source")
    assert studio.open_take(take)
    studio.show()
    studio._open_take_review()
    review = studio._review_dialog
    review.notes.setPlainText("Retained draft")
    with monkeypatch.context() as patch:
        patch.setattr("webjam_qt.widgets.studio_take_review_workflow.save_take_review",
                      Mock(side_effect=TakeReviewError("Save failed")))
        assert studio.shutdown() is False
        assert studio.isEnabled() and review.isEnabled()
        assert review.save_button.isEnabled()
        assert not studio._waveform_shutdown
        review.notes.setFocus()
        review.notes.moveCursor(review.notes.textCursor().MoveOperation.End)
        QTest.keyClicks(review.notes, " with another thought")
        assert review.notes.toPlainText() == "Retained draft with another thought"
    assert studio.shutdown()
    assert load_take_review(load_take(take)).notes == "Retained draft with another thought"


def test_failed_local_project_retirement_blocks_ui_and_queued_metadata_edits(
    navigation, monkeypatch, tmp_path,  # noqa: F811
):
    navigator, _mailbox, _controllers, app = navigation
    assert navigator.return_to_launch()
    navigator.launch._workspace_actions["music"].trigger()
    app.processEvents()
    controller = navigator.controller
    local = controller.reference_studio_projects
    local.create_project(tmp_path / "Keep this project", "Keep this project")
    local.project_controller.add_track("Retained track")
    assert local.save(prepare_media=False)
    before = local.project_controller.snapshot.project
    original_shutdown = local._executor.shutdown
    allow_close = False

    def shutdown(*args, **kwargs):
        if not allow_close:
            raise RuntimeError("controlled executor close failure")
        return original_shutdown(*args, **kwargs)

    monkeypatch.setattr(local._executor, "shutdown", shutdown)
    monkeypatch.setattr(QMessageBox, "information", Mock())
    monkeypatch.setattr(local, "_ask_unsaved_choice", Mock(return_value="save"))
    try:
        assert not navigator.return_to_launch()
        assert local._closed and not local._shutdown_complete
        assert local.studio_controller.is_shutdown
        tempo = local.workspace.tempo
        tempo.setFocus()
        QTest.keyClick(tempo, Qt.Key.Key_Up)
        QTest.keyClick(tempo, Qt.Key.Key_Return)
        # A queued UI signal can arrive even after its widget is disabled.
        local.workspace.tempo_changed.emit(before.tempo_bpm + 10)
        app.processEvents()
        assert local.project_controller.snapshot.project == before
        assert not local.project_controller.snapshot.dirty
        assert not local.shell.isEnabled() and not tempo.isEnabled()
        assert controller.window._return_to_launch_action.isEnabled()
        assert controller.window._workflow_help_action.isEnabled()
    finally:
        allow_close = True
    assert navigator.return_to_launch()
    assert navigator.controller is None and navigator.launch.isVisible()


@pytest.mark.parametrize("failure", ["exception", "false"])
def test_native_sink_retains_failed_stream_and_refuses_a_second_output(monkeypatch, caplog, failure):
    allow_close = False
    streams = []

    class NativeStream:
        closed = False

        def __init__(self, **kwargs):
            assert all(stream.closed for stream in streams)
            streams.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            if not allow_close:
                if failure == "exception":
                    raise RuntimeError("private-device-identifier")
                return False
            self.closed = True

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=NativeStream))
    sink = SoundDeviceSink()
    player = TakePlayer(sink=sink)
    sink.start(48000, 512, lambda frames: None)
    original = streams[0]
    with pytest.raises(PlaybackError, match="Audio output is still closing"):
        player.stop()
    assert sink._stream is original and not original.closed
    with pytest.raises(PlaybackError, match="Audio output is still closing"):
        sink.start(48000, 512, lambda frames: None)
    assert streams == [original] and sink._stream is original
    assert "private-device-identifier" not in caplog.text
    allow_close = True
    sink.start(48000, 512, lambda frames: None)
    assert original.closed and len(streams) == 2
    assert sink._stream is streams[1]
    player.stop()
    assert streams[1].closed and sink._stream is None


@pytest.mark.parametrize("failure", ["exception", "false"])
def test_successful_native_close_proves_release_after_stop_error(monkeypatch, caplog, failure):
    stop = Mock(side_effect=RuntimeError("private-device-identifier")) if failure == "exception" else Mock(return_value=False)
    old_stream = SimpleNamespace(start=Mock(), stop=stop, close=Mock())
    new_stream = SimpleNamespace(start=Mock(), stop=Mock(), close=Mock())

    def allocate(**kwargs):
        old_stream.close.assert_called_once_with()
        return new_stream

    factory = Mock(side_effect=allocate)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=factory))
    sink = SoundDeviceSink()
    sink._stream = old_stream
    sink.start(48000, 512, lambda frames: None)
    assert sink._stream is new_stream
    factory.assert_called_once()
    new_stream.start.assert_called_once_with()
    assert "private-device-identifier" not in caplog.text
    sink.stop()
    new_stream.close.assert_called_once_with()
    assert sink._stream is None
