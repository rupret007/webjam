"""Capture W05's real window with fixture room facts, never a live connection.

Run from the worktree root with QT_QPA_PLATFORM=offscreen and pass an output
directory. Uses the layout suite's themed, deterministically retired window.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.art_companion import ArtCompanionProjection  # noqa: E402
from core.art_room_overview import art_room_overview  # noqa: E402
from core.art_room_presence import art_room_presence  # noqa: E402
from core.session_conductor import ArtRoomState  # noqa: E402
from tests.test_art_room_overview_ui import qapp, window, _settle  # noqa: E402


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    app_fixture = qapp.__wrapped__()
    app = next(app_fixture)
    window_fixture = window.__wrapped__(app)
    room = next(window_fixture)
    try:
        for state in (ArtRoomState.WAITING, ArtRoomState.CONNECTED):
            room.session_strip.set_audio_state("End Room")
            room.session_hud.set_state(
                "Room open" if state is ArtRoomState.WAITING else "You’re in",
                "", action_visible=False,
            )
            room.set_art_room_overview(art_room_overview(
                state=state, hosting=True,
                presence=art_room_presence(
                    ArtCompanionProjection(in_room=True),
                    hosting=True, intended_video=True,
                ),
            ))
            for width, height in ((800, 600), (1280, 800)):
                room.resize(width, height)
                _settle(app)
                path = output / f"paint-along-host-{state.value}-{width}x{height}.png"
                assert room.grab().save(str(path))
                print(path)
    finally:
        window_fixture.close()
        app_fixture.close()


if __name__ == "__main__":
    main()
