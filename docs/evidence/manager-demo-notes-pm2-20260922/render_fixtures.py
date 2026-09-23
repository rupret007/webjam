from pathlib import Path
import pytest
from tests.test_art_room_controller import controllers, qapp
from tests.test_notes_unreadable_recheck import unreadable_notes
from tests.test_notes_combined_unreadable import combined
from tests.test_notes_font_reflow import _stress_style
from webjam_qt.theme import load_stylesheet
import os
import tempfile

OUT = Path(
    os.environ.get(
        "WEBJAM_NOTES_EVIDENCE_DIR",
        str(Path(tempfile.gettempdir()) / "webjam-notes-demo-rendered"),
    )
)
OUT.mkdir(exist_ok=True)


def settle(qapp):
    for _ in range(10):
        qapp.processEvents()


@pytest.mark.parametrize("font_size", [13, 22])
@pytest.mark.parametrize("permission", ["denied", "granted"])
def test_render_music(controllers, qapp, monkeypatch, font_size, permission):
    monkeypatch.setattr(
        "webjam_qt.platform_permissions.microphone_permission_status",
        lambda: permission,
    )
    app = controllers(hosting=True)
    window = app.window
    panel = window.session_canvas
    window.setStyleSheet(load_stylesheet())
    panel.setStyleSheet(_stress_style().replace("22px", f"{font_size}px"))
    window.show()
    app._on_rail_view_changed("canvas")
    window.resize(760, 600)
    settle(qapp)
    window.center_splitter.setSizes([480, 280])
    settle(qapp)
    name = f"music-{permission}-{font_size}-760"
    window.grab().save(str(OUT / (name + ".png")))
    panel._music_details_button.setChecked(True)
    settle(qapp)
    window.grab().save(str(OUT / (name + "-details.png")))
    scroll = panel._music_readout_scroll
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    settle(qapp)
    window.grab().save(str(OUT / (name + "-details-bottom.png")))


def test_render_recovery(unreadable_notes, qapp):
    unreadable_notes.canvas.setStyleSheet(_stress_style())
    settle(qapp)
    unreadable_notes.canvas.grab().save(str(OUT / "music-recheck-22.png"))


def test_render_combined(combined, qapp):
    combined.canvas.setStyleSheet(_stress_style())
    settle(qapp)
    combined.canvas.grab().save(str(OUT / "art-combined-22.png"))


def test_render_healthy_standalone(qapp):
    from core.creative_modes import get_creator_profile_by_key_or_default
    from webjam_qt.widgets.session_canvas import SessionCanvas

    panel = SessionCanvas()
    panel.set_creator_profile(get_creator_profile_by_key_or_default("music"))
    panel.setStyleSheet(_stress_style())
    panel.resize(280, 560)
    panel.show()
    settle(qapp)
    try:
        panel.grab().save(str(OUT / "music-healthy-22-280.png"))
        panel._music_details_button.setChecked(True)
        settle(qapp)
        panel.grab().save(str(OUT / "music-healthy-22-280-details.png"))
    finally:
        panel.close()
        panel.deleteLater()
        settle(qapp)


import json, errno
from unittest.mock import Mock
from PySide6.QtCore import QPoint, QRect
from webjam_qt.controllers import session_persistence as owner_module


@pytest.mark.parametrize("font", [13, 22])
@pytest.mark.parametrize("state", ["permission", "unreadable"])
@pytest.mark.parametrize("expanded", [False, True])
def test_actual(controllers, qapp, monkeypatch, tmp_path, font, state, expanded):
    monkeypatch.setattr(owner_module, "_persistence_home", lambda: tmp_path)
    original = tmp_path / ".webjam_notes.md"
    original.write_bytes(b"\xffunreadable" if state == "unreadable" else b"Saved Music")
    app = controllers(hosting=True)
    window = app.window
    panel = window.session_canvas
    window.setStyleSheet(load_stylesheet())
    panel.setStyleSheet(_stress_style().replace("22px", f"{font}px"))
    window.show()
    app._on_rail_view_changed("canvas")
    window.resize(760, 600)
    settle(qapp)
    window.center_splitter.setSizes([480, 280])
    panel._music_details_button.setChecked(expanded)
    if state == "permission":
        panel.edit_notes("Retained draft")
        with monkeypatch.context() as mp:
            mp.setattr(
                owner_module,
                "atomic_write_text",
                Mock(side_effect=PermissionError(errno.EACCES, "Private error")),
            )
            assert not app._save_notes()
    settle(qapp)
    name = f"actual-recovery-{state}-{font}-{expanded}"
    window.grab().save(str(OUT / (name + ".png")))
    rows = []
    for w in (
        panel._notes_save_status,
        panel._save_notes_button,
        panel._recheck_notes_button,
        panel._music_details_button,
        panel._music_readout_scroll,
        panel._notes,
    ):
        rows.append(
            {
                "name": w.objectName(),
                "type": type(w).__name__,
                "visible": w.isVisibleTo(panel),
                "size": [w.width(), w.height()],
                "needed_height": w.heightForWidth(w.width()),
                "in_panel": panel.rect().contains(
                    QRect(w.mapTo(panel, QPoint()), w.size())
                ),
            }
        )
    (OUT / (name + ".json")).write_text(json.dumps(rows, indent=2))
