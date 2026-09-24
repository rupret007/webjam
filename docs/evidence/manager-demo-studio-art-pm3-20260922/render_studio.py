"""Render actual compact Studio widgets with synthetic takes and failures.

Run from the repository root in a fresh process:
    PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen PYTHONPATH=. \\
      .venv/bin/python -m pytest -q -p no:cacheprovider \\
      docs/evidence/manager-demo-studio-art-pm3-20260922/render_studio.py

WEBJAM_STUDIO_EVIDENCE_DIR overrides webjam-studio-demo-rendered in the system
temporary directory.
The default cases use the actual theme unchanged. Enlarged cases set Studio
labels/buttons to 22px; they do not claim OS scaling or physical audio PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import tempfile
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect

from core.take_player import PlaybackDeviceError
from core.session_conductor import RecorderState, derive_session_conductor
from tests.test_session_conductor import _live_host_facts
from tests.test_studio_compact_workspace import (
    controllers as _controllers_fixture,
    failed_studio_save as _failed_studio_save_fixture,
    loaded as _loaded_fixture,
    qapp as _qapp_fixture,
    _settle,
)
from webjam_qt.theme import load_stylesheet
import webjam_qt

controllers = _controllers_fixture
failed_studio_save = _failed_studio_save_fixture
loaded = _loaded_fixture
qapp = _qapp_fixture

pytest_plugins = ["tests.conftest"]
OUT = Path(os.environ.get(
    "WEBJAM_STUDIO_EVIDENCE_DIR",
    str(Path(tempfile.gettempdir()) / "webjam-studio-demo-rendered"),
))
RECORDS = []


def capture(name, window, studio):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    assert window.grab().save(str(path))
    controls = []
    for widget in (studio._record_btn, studio._play_btn, studio._stop_btn,
                   studio._export_btn, studio._output_picker, studio._hint):
        controls.append({
            "name": widget.accessibleName() or widget.objectName(),
            "text": widget.text() if hasattr(widget, "text") else "",
            "visible": widget.isVisibleTo(window),
            "enabled": widget.isEnabled(),
            "rect": _rect(window, widget),
        })
    RECORDS.append({
        "image": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "window": window.size().toTuple(),
        "studio": studio.size().toTuple(), "controls": controls,
    })


def _rect(root, widget):
    return QRect(widget.mapTo(root, QPoint()), widget.size()).getRect()


def attention(window):
    canvas = window.session_canvas
    canvas.set_notes_recovery_context("music", (("music", "permission_denied"),))
    canvas.set_notes_save_state("failed")


@pytest.fixture(scope="session", autouse=True)
def manifest():
    yield
    root = Path(webjam_qt.__file__).resolve().parent.parent
    files = (
        "webjam_qt/widgets/recording_studio.py",
        "webjam_qt/widgets/studio_editing.py",
        "webjam_qt/widgets/studio_review.py",
        "webjam_qt/windows/conductor_window.py",
        "webjam_qt/theme/conductor.qss",
        "tests/test_studio_compact_workspace.py",
        "docs/evidence/manager-demo-studio-art-pm3-20260922/render_studio.py",
    )
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "studio-render-manifest.json").write_text(json.dumps({
        "base_revision": revision,
        "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files},
        "fixtures": RECORDS,
        "baseline_revision": "844ea1080500fc937101b19b21168b5a5525600c",
        "baseline_image_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).resolve().parent.glob("before-studio-*.png"))
        },
        "limits": "Actual offscreen Qt widgets with synthetic takes, recording states and failures. No physical audio, meeting, signing or release PASS.",
    }, indent=2) + "\n")


@pytest.mark.parametrize("size,font_size", [((760, 600), None), ((760, 600), 22),
                                            ((1000, 740), None), ((1000, 740), 22)])
def test_render_ready(loaded, qapp, size, font_size):
    _, window, studio = loaded
    if font_size:
        studio.setStyleSheet(f"QLabel, QPushButton {{font-size:{font_size}px;}}")
    window.resize(*size)
    _settle(qapp, 30)
    prefix = f"studio-after-{size[0]}-{'22px' if font_size else 'theme'}"
    capture(prefix + "-top", window, studio)
    if size[0] == 760 and font_size is None:
        studio._workspace_scroll.ensureWidgetVisible(studio._track_scroll, 0, 0)
        _settle(qapp)
        capture(prefix + "-mixer", window, studio)
    elif size[0] == 1000 and font_size == 22:
        studio._workspace_scroll.verticalScrollBar().setValue(
            studio._workspace_scroll.verticalScrollBar().maximum(),
        )
        _settle(qapp)
        capture(prefix + "-bottom", window, studio)


def test_render_retry_with_notes(failed_studio_save, qapp):
    rig = failed_studio_save
    window, studio = rig.app.window, rig.studio
    window.setStyleSheet(load_stylesheet())
    studio.setStyleSheet("QLabel, QPushButton {font-size:22px;}")
    window.resize(760, 600)
    attention(window)
    _settle(qapp, 30)
    capture("studio-after-retry-and-notes-22px", window, studio)


def test_render_playback_failure(loaded, qapp):
    _, window, studio = loaded
    studio.setStyleSheet("QLabel, QPushButton {font-size:22px;}")
    studio._handle_playback_error(PlaybackDeviceError("Synthetic private output failure"))
    _settle(qapp, 30)
    capture("studio-after-playback-output-22px", window, studio)


def test_render_live_stop_with_notes(loaded, qapp):
    _, window, studio = loaded
    studio.setStyleSheet("QLabel, QPushButton {font-size:22px;}")
    studio.set_can_record(True)
    studio.set_live_participants([
        SimpleNamespace(channel_id=1, name="Band Drums", is_local=False),
    ])
    studio.set_recording_phase("recording")
    snapshot = derive_session_conductor(replace(_live_host_facts(), recorder=RecorderState.RECORDING))
    window.session_hud.set_state(snapshot.title, snapshot.message, action_visible=False)
    attention(window)
    _settle(qapp, 30)
    capture("studio-after-live-stop-and-notes-22px", window, studio)
    studio.set_recording_phase("idle")


def test_render_export_busy(loaded, qapp, monkeypatch):
    _, window, studio = loaded
    started, release = threading.Event(), threading.Event()

    def held_export(*args, **kwargs):
        started.set()
        assert release.wait(5)
        raise RuntimeError("Synthetic cancelled export")

    monkeypatch.setattr("webjam_qt.widgets.recording_studio.studio_export_supported", lambda: True)
    monkeypatch.setattr("webjam_qt.widgets.recording_studio.export_studio_arrangement", held_export)
    studio.setStyleSheet("QLabel, QPushButton {font-size:22px;}")
    studio._export_tracks()
    try:
        assert started.wait(2)
        _settle(qapp, 30)
        capture("studio-after-export-busy-22px", window, studio)
    finally:
        release.set()
        worker = studio._export_thread
        if worker is not None:
            worker.join(3)
        studio._drain_export_results()


def test_render_aligned_originals(loaded, qapp, monkeypatch):
    _, window, studio = loaded
    monkeypatch.setattr("webjam_qt.widgets.recording_studio.studio_export_supported", lambda: False)
    studio._refresh_export_presentation()
    studio.setStyleSheet("QLabel, QPushButton {font-family:Verdana; font-size:22px;}")
    _settle(qapp, 30)
    capture("studio-after-aligned-originals-22px", window, studio)
