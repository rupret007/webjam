"""Queued take-layout work cannot outlive its Studio/controller owner."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid

from core.take_player import TakePlayer
from services.session_workspace_packaged_smoke import _MemorySink, _take
from services.workflow_continuity_packaged_smoke import _application
from webjam_qt.widgets.recording_studio import RecordingStudio


@pytest.mark.parametrize("delete_widget", [False, True])
def test_pending_take_alignment_retires_with_shutdown(tmp_path, delete_widget):
    app = _application()
    calls = []

    class ObservedStudio(RecordingStudio):
        def _align_legacy_ruler_origin(self):
            calls.append(isValid(self))
            super()._align_legacy_ruler_origin()

    take = _take(tmp_path, "Owned take", 220)
    widget = ObservedStudio(str(tmp_path), player=TakePlayer(samplerate=48000, sink=_MemorySink()))
    widget.show()
    assert widget.open_take(take)
    assert calls and all(calls)
    initial_calls = len(calls)
    assert widget.shutdown()
    if delete_widget:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(widget)
    app.processEvents()
    assert len(calls) == initial_calls  # No layout action after terminal ownership ends.
    if not delete_widget:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
