"""Offline, styled room screenshots. Run from the worktree root with an output directory."""

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path.cwd()))

from core.art_companion import ArtCompanionProjection  # noqa: E402
from core.art_room_overview import art_room_overview  # noqa: E402
from core.art_room_presence import art_room_presence  # noqa: E402
from core.creative_modes import get_creator_profile_by_key  # noqa: E402
from core.session_conductor import ArtRoomState  # noqa: E402
from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402

app = QApplication([])
for path in Path("webjam_qt/theme/fonts").glob("Inter-*.ttf"):
    QFontDatabase.addApplicationFont(str(path))
font = QFont("Inter") if "Inter" in QFontDatabase.families() else QFont(app.font())
font.setPixelSize(13)
app.setFont(font)
output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
for width, height in ((800, 600), (1280, 800)):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")],
        initial_mode_key="music_jam", initial_title="Making together",
    )
    window.menuBar().setNativeMenuBar(False)
    window.setStyleSheet(load_stylesheet())
    window.set_creator_profile(get_creator_profile_by_key("art"))
    presence = art_room_presence(
        ArtCompanionProjection(in_room=True), hosting=True, intended_video=True,
    )
    window.set_art_room_overview(art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=True, presence=presence,
    ))
    window.session_strip.set_audio_state("End Room")
    window.session_strip.set_video_configured(False)
    window.session_hud.set_state("You’re in", "", action_visible=False)
    window.resize(width, height)
    window.show()
    for _ in range(6):
        app.processEvents()
    path = output / f"paint-along-next-action-{width}x{height}.png"
    assert window.grab().save(str(path))
    print(path)
    window.session_strip._record_clock.stop()
    window.session_strip.stop_session_clock()
    window._room_help_dialog.close()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    app.processEvents()
