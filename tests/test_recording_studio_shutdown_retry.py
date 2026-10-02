"""Studio retains failed cleanup ownership until an explicit retry succeeds."""
from __future__ import annotations

import threading
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
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
