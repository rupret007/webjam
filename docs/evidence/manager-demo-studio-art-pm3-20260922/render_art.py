"""Render finite Art room fixtures without opening providers, media or external apps.

From the repository root:
    QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
      .venv/bin/python docs/evidence/manager-demo-studio-art-pm3-20260922/render_art.py

WEBJAM_ART_EVIDENCE_DIR overrides the default temporary output directory.
WEBJAM_ART_FIXTURE_PREFIX selects the file prefix (default: after).
WEBJAM_ART_SOURCE_REVISION supplies the source revision for an archived source
without .git. Actual rendered source SHA-256 values are recorded independently.
The 125% fixtures stretch glyph width; they do not change OS scaling or font size.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.art_companion import ArtCompanionProjection, CanvasCompanionState, VideoCompanionState
from core.art_room_activities import art_room_activities
from core.art_room_overview import art_room_overview
from core.art_room_presence import ABSENT
from core.creative_modes import get_creator_profile_by_key
from core.session_conductor import ArtRoomState, SessionFacts, derive_session_presentation
from core.shared_canvas import SharedCanvasFollowSnapshot, SharedCanvasFollowState, SharedCanvasSnapshot
import webjam_qt
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.shared_canvas import SharedCanvasDialog

CASES = (
    ("make-own", "none", "none"),
    ("make-canvas", "ready", "none"),
    ("make-missing-app", "missing_app", "none"),
    ("paint-start", "none", "none"),
    ("paint-both", "ready", "ready"),
    ("paint-both-missing", "missing_app", "needs_file"),
)
SOURCE_FILES = (
    "core/art_companion.py", "core/art_room_activities.py", "core/art_room_overview.py",
    "core/art_room_presence.py", "core/creative_modes.py", "core/meeting_companion.py",
    "core/session_conductor.py", "core/shared_canvas.py",
    "webjam_qt/widgets/art_room_overview.py", "webjam_qt/widgets/session_hud.py",
    "webjam_qt/widgets/session_strip.py", "webjam_qt/windows/conductor_window.py",
    "webjam_qt/windows/shared_canvas.py", "webjam_qt/theme/conductor.qss",
    "webjam_qt/theme/tokens.py",
)


def source_metadata(root: Path) -> dict:
    revision = os.environ.get("WEBJAM_ART_SOURCE_REVISION", "")
    if not revision:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        revision = result.stdout.strip() if result.returncode == 0 else "unavailable"
    paths = [root / name for name in SOURCE_FILES]
    paths += sorted((root / "webjam_qt/theme/fonts").glob("Inter-*.ttf"))
    fingerprints = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    return {
        "base_revision": revision,
        "source_description": "Actual source files loaded for these rendered fixtures.",
        "sha256": fingerprints,
        "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "room_size": [760, 600],
        "font_pixel_size": 13,
        "glyph_width_percent": [100, 125],
        "proof_boundary": "Finite rendered room facts; no live transport, provider or physical device proof.",
    }


def settle(app: QApplication) -> None:
    for _ in range(10):
        app.processEvents()


def finish(app: QApplication, widget) -> None:
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(app)


def stretch_text(parent, stretch: int) -> None:
    for widget in parent.findChildren(QLabel) + parent.findChildren(QPushButton):
        font = widget.font()
        font.setStretch(stretch)
        widget.setFont(font)


def render_room(app, output, prefix, hosting, stretch, case, canvas, video) -> dict:
    role = "host" if hosting else "guest"
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")],
        initial_mode_key="music_jam", initial_title="Making together",
    )
    try:
        window.setStyleSheet(load_stylesheet())
        window.set_creator_profile(get_creator_profile_by_key("art"))
        projection = ArtCompanionProjection(
            in_room=True, transport_allowed=hosting,
            canvas=CanvasCompanionState(canvas), video=VideoCompanionState(video),
        )
        activities = art_room_activities(
            projection, hosting=hosting, intended_video=case.startswith("paint"),
            paint_along_room=case.startswith("paint"),
        )
        overview = art_room_overview(
            state=ArtRoomState.CONNECTED, hosting=hosting,
            presence=activities[0] if activities else ABSENT,
            secondary_presence=activities[1] if len(activities) > 1 else ABSENT,
        )
        window.set_art_room_overview(overview)
        window.session_strip.set_audio_state("End Room" if hosting else "Leave Room")
        window.session_strip.set_art_room_presence(activities[0] if activities else ABSENT)
        presentation = derive_session_presentation(SessionFacts(
            role=role, creator_profile_key="art", setup_requested=True,
            art_room=ArtRoomState.CONNECTED,
        ))
        window.session_hud.set_state(presentation.title, presentation.message, action_visible=False)
        panel = window.art_room_overview
        stretch_text(panel._content, stretch)
        window.resize(760, 600)
        window.show()
        window.activateWindow()
        settle(app)
        buttons = (
            panel.activity_button(), panel.secondary_activity_button(), panel.conversation_button(),
        )
        focused = []
        for button in buttons:
            if not button.isVisibleTo(window):
                continue
            button.setFocus(Qt.FocusReason.TabFocusReason)
            settle(app)
            rect = QRect(button.mapTo(panel.viewport(), QPoint()), button.size())
            assert panel.viewport().rect().contains(rect), (case, role, stretch, button.text(), rect)
            assert button.hasFocus(), (case, role, stretch, button.text())
            focused.append(button.text())
        panel.verticalScrollBar().setValue(0)
        settle(app)
        filename = f"art-{prefix}-{role}-{case}-{stretch}.png"
        assert window.grab().save(str(output / filename))
        return {
            "fixture": filename, "role": role, "case": case, "stretch": stretch,
            "making_detail": getattr(overview, "making_detail", ""),
            "activities": overview.activity_actions, "keyboard_reached": focused,
            "scroll_max": panel.verticalScrollBar().maximum(),
            "connection_detail_accessible": overview.connection_detail in panel.accessibleDescription(),
        }
    finally:
        window.session_strip._record_clock.stop()
        window.session_strip.stop_session_clock()
        window._room_help_dialog.close()
        finish(app, window)


def render_install(app, output, prefix, hosting, stretch) -> dict:
    role = "host" if hosting else "guest"
    panel = SharedCanvasDialog(hosting=hosting)
    try:
        panel.setStyleSheet(load_stylesheet())
        if hosting:
            panel.set_host_snapshot(SharedCanvasSnapshot(launcher_available=False))
        else:
            panel.set_follow_snapshot(SharedCanvasFollowSnapshot(state=SharedCanvasFollowState.NEEDS_DRAWPILE))
        stretch_text(panel, stretch)
        panel.resize(540, 260)
        panel.show()
        settle(app)
        filename = f"art-{prefix}-{role}-install-{stretch}.png"
        assert panel.grab().save(str(output / filename))
        return {
            "fixture": filename, "role": role, "case": "install", "stretch": stretch,
            "status": panel._status.text(), "action": panel._chip.text(),
        }
    finally:
        finish(app, panel)


def main() -> None:
    output = Path(os.environ.get(
        "WEBJAM_ART_EVIDENCE_DIR", str(Path(tempfile.gettempdir()) / "webjam-art-demo-rendered"),
    ))
    output.mkdir(parents=True, exist_ok=True)
    prefix = os.environ.get("WEBJAM_ART_FIXTURE_PREFIX", "after")
    root = Path(webjam_qt.__file__).resolve().parent.parent
    app = QApplication([])
    for path in (root / "webjam_qt/theme/fonts").glob("Inter-*.ttf"):
        QFontDatabase.addApplicationFont(str(path))
    font = QFont("Inter") if "Inter" in QFontDatabase.families() else app.font()
    font.setPixelSize(13)
    app.setFont(font)
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    fixtures = []
    for hosting in (False, True):
        for stretch in (100, 125):
            for case, canvas, video in CASES:
                fixtures.append(render_room(app, output, prefix, hosting, stretch, case, canvas, video))
            fixtures.append(render_install(app, output, prefix, hosting, stretch))
    manifest = {"source": source_metadata(root), "fixtures": fixtures}
    (output / f"art-{prefix}-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({
        "prefix": prefix, "fixtures": len(fixtures),
        "room_scroll": [item for item in fixtures if item.get("scroll_max", 0)],
    }))


if __name__ == "__main__":
    main()
