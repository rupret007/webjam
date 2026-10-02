"""Retirement must keep exact output/read owners until cleanup is proved."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from core.project_playback import (
    ProjectPlaybackEngine, ProjectPlaybackError, ProjectPlaybackState,
    SoundDeviceProjectOutputBackend,
)
from tests.test_project_playback import _Backend, _fixture


class _FailingBackend(_Backend):
    def __init__(self):
        super().__init__()
        self.fail_cleanup = True

    def stop(self):
        if self.fail_cleanup:
            raise OSError("/private/output/device")
        super().stop()

    def abort(self):
        if self.fail_cleanup:
            raise OSError("/private/output/device")
        super().abort()


def test_failed_output_cleanup_silences_and_blocks_replacement_until_retry(tmp_path):
    _bundle, _project, renderer = _fixture(tmp_path)
    backend = _FailingBackend()
    engine = ProjectPlaybackEngine(backend)
    engine.set_renderer(renderer)
    engine.play()
    with pytest.raises(ProjectPlaybackError, match="cleanup is still pending") as error:
        engine.close()
    assert "/private" not in str(error.value)
    assert engine.state is ProjectPlaybackState.FAILED and not engine._closed
    output = np.ones((128, 2), dtype=np.float32)
    backend.callback(output)
    assert not output.any()
    with pytest.raises(ProjectPlaybackError, match="pending"):
        engine.play()
    with pytest.raises(ProjectPlaybackError, match="pending"):
        engine.set_renderer(renderer)
    backend.fail_cleanup = False
    engine.close()
    assert engine.state is ProjectPlaybackState.CLOSED
    assert backend.callback is None


def test_still_live_producer_keeps_its_reader_and_owner_until_exit():
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(8), daemon=True)
    worker.start()

    class Reader:
        closed = False

        def close(self):
            assert not worker.is_alive(), "reader closed under its producer"
            self.closed = True

    reader = Reader()
    engine = ProjectPlaybackEngine(_Backend())
    engine._producer = worker
    engine._stream = reader
    try:
        with pytest.raises(ProjectPlaybackError, match="pending"):
            engine.close()
        assert worker.is_alive()
        assert engine._producer is worker and engine._stream is reader
        assert not reader.closed and not engine._closed
    finally:
        release.set()
        worker.join(timeout=2)
    engine.close()
    assert reader.closed and engine._producer is None and engine._stream is None
    assert engine.state is ProjectPlaybackState.CLOSED


def test_failed_render_reader_close_keeps_exact_owner_for_retry():
    class Reader:
        failing = True

        def close(self):
            if self.failing:
                raise OSError("/private/media")

    reader = Reader()
    engine = ProjectPlaybackEngine(_Backend())
    engine._stream = reader
    with pytest.raises(ProjectPlaybackError, match="pending"):
        engine.close()
    assert engine._stream is reader and not engine._closed
    reader.failing = False
    engine.close()
    assert engine._stream is None and engine.state is ProjectPlaybackState.CLOSED


class _NativeStream:
    def __init__(self, *, fail_stop=False, fail_start=False):
        self.fail_stop = fail_stop
        self.fail_start = fail_start
        self.fail_close = True
        self.closed = False

    def start(self):
        if self.fail_start:
            raise OSError("/private/start/device")

    def stop(self):
        if self.fail_stop:
            raise OSError("/private/stop/device")

    abort = stop

    def close(self):
        if self.fail_close:
            raise OSError("/private/close/device")
        self.closed = True


@pytest.mark.parametrize("method", ["stop", "abort"])
@pytest.mark.parametrize("fail_stop", [False, True])
def test_native_stream_is_retained_until_close_proves_retirement(method, fail_stop):
    stream = _NativeStream(fail_stop=fail_stop)
    backend = SoundDeviceProjectOutputBackend()
    backend._stream = stream
    with pytest.raises(ProjectPlaybackError, match="still closing") as error:
        getattr(backend, method)()
    assert "/private" not in str(error.value)
    assert backend._stream is stream and not stream.closed
    with pytest.raises(ProjectPlaybackError, match="already running"):
        backend.start(lambda output: None)
    stream.fail_close = False
    getattr(backend, method)()
    assert stream.closed and backend._stream is None


def test_failed_native_start_retains_allocated_stream_when_close_fails():
    stream = _NativeStream(fail_start=True)
    backend = SoundDeviceProjectOutputBackend(
        sounddevice_module=SimpleNamespace(OutputStream=lambda **kwargs: stream),
    )
    with pytest.raises(ProjectPlaybackError, match="still closing") as error:
        backend.start(lambda output: None)
    assert "/private" not in str(error.value)
    assert backend._stream is stream
    stream.fail_close = False
    backend.abort()
    assert backend._stream is None and stream.closed


def test_engine_start_failure_does_not_erase_failed_cleanup_owner(tmp_path):
    _bundle, _project, renderer = _fixture(tmp_path)
    stream = _NativeStream(fail_start=True)
    backend = SoundDeviceProjectOutputBackend(
        sounddevice_module=SimpleNamespace(OutputStream=lambda **kwargs: stream),
    )
    engine = ProjectPlaybackEngine(backend)
    engine.set_renderer(renderer)
    with pytest.raises(ProjectPlaybackError, match="pending"):
        engine.play()
    assert backend._stream is stream
    assert not engine._closed and engine.state is ProjectPlaybackState.FAILED
    stream.fail_close = False
    engine.close()
    assert stream.closed and engine.state is ProjectPlaybackState.CLOSED
