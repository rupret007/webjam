"""Temporary-data Qt proof for saved work and take review in a frozen build.

Invoked by the existing Reference Studio smoke before its success marker. No
controller, meeting, network, audio device, or real user library is started.
The real TakePlayer renders comparison audio into an in-memory output sink.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QPushButton

from core.export_receipt import ExportReceiptError, verify_export_receipt
from core.session_library import SessionLibrary
from core.take_library import load_take
from core.take_player import TakePlayer
from core.take_project import (
    AlignmentState, MediaSegment, MediaStatus, ProjectStatus, ProjectTrack,
    SourceQuality, SourceType, TakeProject, new_project_id, write_take_project,
)
from core.take_review import load_take_review
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.windows.session_library import SessionLibraryDialog

SUCCESS_MARKER = "WebJam saved-work and take-review Qt smoke passed"
_RATE = 48_000
_APP = None


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(f"Packaged workspace smoke: {message}")


def _wait(app, predicate, message: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    _require(predicate(), message)


def _click(widget, label: str) -> None:
    buttons = [item for item in widget.findChildren(QPushButton) if item.text() == label]
    _require(len(buttons) == 1 and buttons[0].isEnabled(), f"unavailable action: {label}")
    buttons[0].click()


class _MemorySink:
    """Exercise real rendering without creating a sounddevice stream."""

    def __init__(self):
        self.pull = None
        self.starts = 0

    def start(self, samplerate, blocksize, pull):
        _require(samplerate == _RATE and blocksize > 0, "invalid playback format")
        self.starts += 1
        self.pull = pull

    def stop(self):
        self.pull = None


class _ReferenceReceiver(QObject):
    def __init__(self):
        super().__init__()
        self.urls = []

    @Slot(QUrl)
    def open_reference(self, url):
        self.urls.append(url.toLocalFile())


def _take(root: Path, name: str, frequency: float, *, session_id=None) -> Path:
    directory = root / name
    media = directory / "media" / "source.wav"
    media.parent.mkdir(parents=True)
    frames = np.arange(_RATE // 4, dtype=np.float32)
    samples = 0.25 * np.sin(frames * (2 * np.pi * frequency / _RATE))
    sf.write(media, samples, _RATE, subtype="PCM_16")
    segment = MediaSegment(
        segment_id=new_project_id(), path="media/source.wav",
        project_start_frame=0, frame_count=len(frames), sample_rate=_RATE,
        channels=1, sample_format="PCM_16", media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(media.read_bytes()).hexdigest(), size_bytes=media.stat().st_size,
    )
    track = ProjectTrack(
        track_id=new_project_id(), source_id=new_project_id(), participant_id=None,
        name="Smoke audio", instrument="Tone", source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK, media_status=MediaStatus.AVAILABLE,
        order=0, segments=(segment,), alignment=AlignmentState(confidence=1.0, method="server-origin"),
    )
    write_take_project(directory, TakeProject(
        session_id=session_id or new_project_id(), take_id=new_project_id(), session_title="Packaged smoke",
        take_name=name, status=ProjectStatus.COMPLETE, project_sample_rate=_RATE,
        participants=(), tracks=(track,),
    ))
    return directory


def _music(library, studio, first, widgets):
    record = library.create("music", "Friday rehearsal")
    dialog = SessionLibraryDialog(library, current_id=record.id)
    widgets.append(dialog)
    dialog.show()
    dialog.notes.setPlainText("Decision: Keep the chorus.\nAction: Rehearse the ending.")
    panel = dialog.rehearsal
    panel.add_song("Opening song")
    panel._key.setText("A minor")
    panel._tempo.setValue(112)
    panel._goals.setPlainText("Keep the chorus together")
    panel._notes.setPlainText("Keep this song draft")
    panel._next_steps.setPlainText("Practice the ending")
    panel.bookmark_requested.connect(lambda note: panel.add_bookmark(note))
    panel._moment_note.setText("Discuss the entrance")
    _click(panel, "Mark moment")
    _require(panel.payload()["songs"][0]["bookmarks"][0]["position_seconds"] is None,
             "an untimed moment acquired a timecode")
    reference = studio.current_take_reference()
    _require(reference, "generated take has no verified reference")
    _require(studio.jump_to_bookmark(first, 0.05, reference["take_id"], reference["source_identity"]),
             "verified take position could not be selected")
    reference = studio.current_take_reference()
    panel.add_bookmark("Listen to this entrance", **reference, timing_verified=True)
    panel.add_song("Closing song")
    panel._notes.setPlainText("Keep this second draft")
    _click(panel, "Previous")
    _require(panel._notes.toPlainText() == "Keep this song draft", "song selection lost notes")
    _click(panel, "Move later")
    _require(dialog.save_current(), "Music workspace did not save")
    payload = panel.payload()
    _require([song["title"] for song in payload["songs"]] == ["Closing song", "Opening song"],
             "saved rehearsal order differs")
    reopened = SessionLibraryDialog(library, current_id=record.id)
    widgets.append(reopened)
    reopened.show()
    _require(reopened.rehearsal.payload() == payload, "reopened rehearsal lost its exact payload")
    _require("Practice the ending" in reopened.summary.toPlainText(), "rehearsal summary lost next steps")
    opened = []
    reopened.bookmark_open_requested.connect(opened.append)
    reopened.rehearsal._bookmarks.setCurrentRow(1)
    _click(reopened.rehearsal, "Open in take")
    _require(len(opened) == 1 and opened[0]["position_seconds"] == reference["position_seconds"],
             "saved bookmark lost its verified position")
    continued = []
    reopened.continue_requested.connect(continued.append)
    reopened.continue_button.click()
    _require(len(continued) == 1 and continued[0].id == record.id, "Music resume selected different work")
    return {"songs": 2, "plain_notes": 1, "verified_bookmarks": 1}


def _art(library, root, widgets):
    record = library.create("art", "Clay study")
    reference = root / "Owned reference.txt"
    reference.write_text("A reference made only for this smoke check.\n", encoding="utf-8")
    original = reference.read_bytes()
    dialog = SessionLibraryDialog(library, profile="art", current_id=record.id)
    widgets.append(dialog)
    panel = dialog.art
    panel.brief.setPlainText("Shape the first clay study")
    panel.progress.setPlainText("Base shape complete")
    panel.next_steps.setPlainText("Refine the surface")
    panel.add_reference(str(reference), kind="file", title="Local reference")
    panel.position.setValue(12.5)
    panel.bookmark_note.setText("Texture technique")
    _click(panel, "Save lesson bookmark")
    _require(dialog.save_current(), "Art project did not save")
    payload = panel.payload()
    receiver = _ReferenceReceiver()
    QDesktopServices.setUrlHandler("file", receiver, "open_reference")
    try:
        reopened = SessionLibraryDialog(library, profile="art", current_id=record.id)
        widgets.append(reopened)
        reopened.show()
        _require(not receiver.urls, "Art resume opened a reference without a request")
        _require(reopened.art.payload() == payload, "Art resume lost project context")
        _require("Refine the surface" in reopened.summary.toPlainText(), "Art summary lost next steps")
        reopened.art.bookmarks.setCurrentRow(0)
        _require(reopened.art.position.value() == 12.5, "manual lesson position changed")
        _click(reopened.art, "Open reference")
        _wait(QApplication.instance(), lambda: not reopened.media_operation_pending,
              "explicit reference verification did not finish")
        _require([Path(url).resolve() for url in receiver.urls] == [reference],
                 "explicit reference action selected the wrong file")
        continued = []
        reopened.continue_requested.connect(continued.append)
        reopened.continue_button.click()
        _require(len(continued) == 1 and continued[0].art == payload, "Art continuation lost its context")
    finally:
        QDesktopServices.unsetUrlHandler("file")
    _require(reference.read_bytes() == original, "reference bytes changed")
    return {"references": 1, "manual_bookmarks": 1, "explicit_opens": len(receiver.urls)}


def _review(app, studio, first, second, sink):
    _require(studio.open_take(first), "first comparison take could not open")
    studio._open_take_review()
    review = studio._review_dialog
    review.favorite.setChecked(True)
    review.notes.setPlainText("Best entrance; retain this take.")
    review.save_button.click()
    saved = load_take_review(load_take(first))
    _require(saved.favorite and saved.notes == review.notes.toPlainText(), "private take review did not persist")
    _click(review, "Set A")
    _require(studio.open_take(second), "second comparison take could not open")
    _click(review, "Set B")
    for slot, expected in (("A", first), ("B", second)):
        _click(review, f"Listen {slot}")
        _wait(app, lambda: studio._player.is_playing and sink.pull is not None,
              f"comparison {slot} did not start verified rendering")
        _require(studio._current.path == expected, f"comparison {slot} selected a different take")
        output = sink.pull(512)
        _require(output.shape == (512, 2) and np.all(np.isfinite(output))
                 and float(np.max(np.abs(output))) > 0.001, "comparison rendered no usable samples")
        studio._stop_playback()
    _require(studio.open_take(first), "saved review could not reopen")
    _require(review.favorite.isChecked() and review.notes.toPlainText() == saved.notes,
             "review did not follow the selected take")
    _require(not studio._studio_controller.dirty, "A/B modified the arrangement")
    return {"favorites": 1, "saved_notes": 1, "rendered_comparisons": sink.starts}


def _export(app, studio):
    outcomes = []
    studio.export_finished.connect(outcomes.append)
    studio._review_dialog.export_button.click()
    _wait(app, lambda: bool(outcomes), "review export did not finish", timeout=15)
    _require(outcomes == [True], "review export failed")
    review = studio._review_dialog
    _require(review.receipt_button.isEnabled(), "verified export receipt is unavailable")
    receipt = verify_export_receipt(studio._reveal_path)
    _require(studio._current.take_id in receipt.details and str(receipt.folder) in receipt.details,
             "export receipt omitted exact take or destination")
    review.receipt_button.click()
    _require(any(window.windowTitle() == "Verified export receipt"
                 for window in QApplication.topLevelWidgets()), "native export receipt did not open")
    audio = next(receipt.folder.rglob("*.wav"))
    with audio.open("ab") as output:
        output.write(b"smoke checksum rejection")
    try:
        verify_export_receipt(receipt.folder)
    except ExportReceiptError:
        pass
    else:
        raise RuntimeError("Packaged workspace smoke accepted changed export bytes")
    return {"verified_files": receipt.file_count, "changed_output_rejected": True}


def run_session_workspace_smoke() -> dict:
    """Exercise all four new feature areas, returning only path-free counts."""
    global _APP
    if QApplication.instance() is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        _APP = QApplication(["webjam-session-workspace-smoke"])
    app = QApplication.instance()
    widgets = []
    with tempfile.TemporaryDirectory(prefix="webjam-session-workspace-smoke-") as temporary:
        root = Path(temporary).resolve()
        takes = root / "Takes"
        first, second = _take(takes, "Take A", 220.0), _take(takes, "Take B", 330.0)
        originals = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                     for take in (first, second) for path in take.rglob("*") if path.is_file()}
        library = SessionLibrary(root / "Library")
        sink = _MemorySink()
        studio = RecordingStudio(str(takes), player=TakePlayer(samplerate=_RATE, sink=sink))
        widgets.append(studio)
        try:
            _require(studio.open_take(first), "generated take could not open in Studio")
            proof = {"music": _music(library, studio, first, widgets),
                     "art": _art(library, root, widgets),
                     "review": _review(app, studio, first, second, sink),
                     "export": _export(app, studio)}
            _require(all(hashlib.sha256(path.read_bytes()).hexdigest() == original
                         for path, original in originals.items()), "original recording bytes changed")
            proof["originals_unchanged"] = True
            return proof
        finally:
            from services.packaged_smoke_diagnostics import checkpoint
            checkpoint("Saved workspace: Studio shutdown begin")
            _require(studio.shutdown(), "saved-work Studio did not prove cleanup")
            checkpoint("Saved workspace: Studio shutdown complete; widget deletion begin")
            for widget in reversed(widgets):
                widget.close()
                widget.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            checkpoint("Saved workspace: deferred deletion complete; process events begin")
            app.processEvents()
            checkpoint("Saved workspace: process events complete")
