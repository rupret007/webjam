"""The room's one line about Art, and the chip that shows it.

A host who chose **Make together** used to land in a room that said nothing
about a canvas, with the only mention being a nine-second message naming the
menu to open. These tests pin the replacement: one small persistent control
that is the way in before anything is set up, the room's status afterwards,
and absent entirely when the room genuinely has nothing to say.

The derivation reads the same projection a paired companion panel reads, so
what the room shows and what a meeting-window panel shows cannot disagree.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.art_companion import (  # noqa: E402
    AiCompanionState,
    ArtCompanionProjection,
    CanvasCompanionState,
    VideoCompanionState,
)
from core.art_room_presence import (  # noqa: E402
    ArtPresenceTarget,
    ArtPresenceTone,
    art_room_presence,
)
from webjam_qt.widgets.status_chip import StatusChip  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _room(**overrides) -> ArtCompanionProjection:
    values = {"generation": 1, "revision": 1, "in_room": True}
    values.update(overrides)
    return ArtCompanionProjection(**values)


# ---------------------------------------------------------------------------
# Nothing to say
# ---------------------------------------------------------------------------


def test_outside_a_room_there_is_no_line():
    assert art_room_presence(ArtCompanionProjection()).offered is False


def test_a_room_with_no_intended_layer_shows_nothing_at_all():
    """No room fact means no invented or greyed-out feature slot."""

    presence = art_room_presence(_room(), hosting=True)

    assert presence.offered is False
    assert presence.label == ""
    assert presence.target is ArtPresenceTarget.NONE


def test_a_guests_own_saved_choice_never_invents_a_line():
    """A guest's saved start describes how they last hosted, not the room they
    joined, so it must not offer them a door the host did not open."""

    presence = art_room_presence(
        _room(), hosting=False, intended_canvas=True, intended_video=True
    )

    assert presence.offered is False


def test_the_image_action_is_never_a_room_fact():
    """It is personal to whoever runs it. Putting it here would make the chip
    permanent in every Art room, which would cost an otherwise empty room its
    finished feel for something nobody shares."""

    presence = art_room_presence(_room(ai=AiCompanionState.IDLE), hosting=True)

    assert presence.offered is False


# ---------------------------------------------------------------------------
# The way in, for a host who chose a layer
# ---------------------------------------------------------------------------


def test_a_host_who_chose_the_canvas_is_offered_the_way_in():
    presence = art_room_presence(_room(), hosting=True, intended_canvas=True)

    assert presence.label == "Set up shared canvas"
    assert "Make together" in presence.description
    assert presence.target is ArtPresenceTarget.CANVAS
    assert presence.tone is ArtPresenceTone.PRESENT


def test_a_host_who_chose_the_video_is_offered_that_way_in_instead():
    presence = art_room_presence(_room(), hosting=True, intended_video=True)

    assert presence.label == "Set up Paint along"
    assert presence.target is ArtPresenceTarget.VIDEO


def test_the_way_in_gives_way_to_what_the_room_actually_has():
    """Once a canvas exists, the line is about the canvas rather than about
    setting one up."""

    presence = art_room_presence(
        _room(canvas=CanvasCompanionState.READY),
        hosting=True,
        intended_canvas=True,
    )

    assert presence.label == "Shared canvas"


# ---------------------------------------------------------------------------
# Requests come before descriptions
# ---------------------------------------------------------------------------


def test_a_missing_painting_program_is_the_line_even_beside_a_live_video():
    """Only one of these is a request, and a request wins."""

    presence = art_room_presence(
        _room(
            canvas=CanvasCompanionState.MISSING_APP,
            video=VideoCompanionState.PLAYING,
        )
    )

    assert presence.label == "Install Drawpile"
    assert presence.tone is ArtPresenceTone.ATTENTION
    assert presence.target is ArtPresenceTarget.CANVAS


@pytest.mark.parametrize(
    ("state", "label"),
    (
        (VideoCompanionState.NEEDS_FILE, "Open your Paint along copy"),
        (VideoCompanionState.MISMATCHED_FILE, "Paint along needs a look"),
        (VideoCompanionState.FILE_UNAVAILABLE, "Paint along needs a look"),
        (VideoCompanionState.STALLED, "Paint along is out of step"),
        (VideoCompanionState.HOST_ATTENTION, "Paint along needs a look"),
    ),
)
def test_every_video_state_this_computer_cannot_follow_asks_for_attention(
    state, label
):
    presence = art_room_presence(_room(video=state))

    assert presence.label == label
    assert presence.tone is ArtPresenceTone.ATTENTION
    assert presence.target is ArtPresenceTarget.VIDEO


def test_a_canvas_that_could_not_be_read_asks_rather_than_describes():
    presence = art_room_presence(_room(canvas=CanvasCompanionState.UNREADABLE))

    assert presence.tone is ArtPresenceTone.ATTENTION
    assert presence.target is ArtPresenceTarget.CANVAS


def test_a_canvas_request_comes_before_a_video_request():
    """Two requests still produce one line, deterministically."""

    presence = art_room_presence(
        _room(
            canvas=CanvasCompanionState.MISSING_APP,
            video=VideoCompanionState.NEEDS_FILE,
        )
    )

    assert presence.target is ArtPresenceTarget.CANVAS


# ---------------------------------------------------------------------------
# Describing what the room has
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", (CanvasCompanionState.READY, CanvasCompanionState.OPENING)
)
def test_a_live_canvas_is_described_without_alarm(state):
    presence = art_room_presence(_room(canvas=state))

    assert presence.label == "Shared canvas"
    assert presence.tone is ArtPresenceTone.PRESENT


@pytest.mark.parametrize(
    "state",
    (
        VideoCompanionState.READY,
        VideoCompanionState.PLAYING,
        VideoCompanionState.PAUSED,
    ),
)
def test_a_live_video_is_described_without_alarm(state):
    presence = art_room_presence(_room(video=state))

    assert presence.label == "Paint along"
    assert presence.tone is ArtPresenceTone.PRESENT


def test_a_hidden_video_keeps_one_quiet_route_back():
    """Hiding is a choice, and the room chrome is the only way to undo it.

    Without this line, hiding the video is a one-way door for anyone who does
    not already know which menu holds the panel.
    """

    presence = art_room_presence(_room(video=VideoCompanionState.HIDDEN))

    assert presence.label == "Paint along (hidden)"
    assert presence.tone is ArtPresenceTone.PRESENT
    assert presence.target is ArtPresenceTarget.VIDEO


def test_a_canvas_outranks_a_video_when_both_are_simply_fine():
    """Combining the two is an in-room decision, so both can be live. One
    line still means one, and the choice is fixed rather than incidental."""

    presence = art_room_presence(
        _room(
            canvas=CanvasCompanionState.READY,
            video=VideoCompanionState.PLAYING,
        )
    )

    assert presence.target is ArtPresenceTarget.CANVAS


def test_no_line_ever_carries_more_than_one_target():
    """The whole contract in one assertion: whatever the room state, the chip
    opens exactly one panel or none."""

    states = [
        (canvas, video)
        for canvas in CanvasCompanionState
        for video in VideoCompanionState
    ]
    for canvas, video in states:
        for hosting in (True, False):
            presence = art_room_presence(
                _room(canvas=canvas, video=video),
                hosting=hosting,
                intended_canvas=True,
                intended_video=True,
            )
            assert presence.target in tuple(ArtPresenceTarget)
            if presence.offered:
                assert presence.target is not ArtPresenceTarget.NONE
            else:
                assert presence.target is ArtPresenceTarget.NONE


def test_every_line_is_a_short_label_and_a_one_line_description():
    """It sits in a strip beside a meeting window, so it has to stay small."""

    seen = 0
    for canvas in CanvasCompanionState:
        for video in VideoCompanionState:
            presence = art_room_presence(
                _room(canvas=canvas, video=video),
                hosting=True,
                intended_canvas=True,
            )
            if not presence.offered:
                continue
            seen += 1
            assert len(presence.label) <= 28, presence.label
            assert "\n" not in presence.description
            assert len(presence.description) <= 140, presence.description
    assert seen > 0


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


def test_the_chip_is_absent_until_the_room_has_something_to_say():
    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()

    assert chip.isHidden() is True
    assert chip.chip.offered is False


def test_the_chip_shows_one_control_whose_label_is_the_status():
    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()
    chip.set_presence(
        art_room_presence(_room(canvas=CanvasCompanionState.READY))
    )

    assert chip.chip.text() == "Shared canvas"
    assert chip.chip.accessibleName() == "Shared canvas"
    assert chip.chip.accessibleDescription()
    assert chip.chip.property("tone") == StatusChip.PRIMARY


def test_an_attention_line_is_a_recovery_rather_than_a_warning():
    """Nothing is broken; something is absent, and those read differently."""

    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()
    chip.set_presence(
        art_room_presence(_room(canvas=CanvasCompanionState.MISSING_APP))
    )

    assert chip.chip.property("tone") == StatusChip.RECOVERY


def _accent_pixels_at_rest(presence) -> int:
    """Render the chip with focus parked elsewhere and count accent pixels.

    Focus rings use the accent colour throughout the app, so a chip that has
    keyboard focus is accented whatever its tone. Resting state is the thing
    worth pinning.
    """

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

    from webjam_qt.theme.qss_loader import load_stylesheet
    from webjam_qt.theme.tokens import Color
    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    app = QApplication.instance()
    previous = app.styleSheet()
    try:
        app.setStyleSheet(load_stylesheet())
        host = QWidget()
        layout = QHBoxLayout(host)
        sink = QLineEdit(host)
        layout.addWidget(sink)
        chip = ArtRoomChip(host)
        chip.set_presence(presence)
        layout.addWidget(chip)
        host.resize(420, 48)
        host.show()
        sink.setFocus(Qt.FocusReason.OtherFocusReason)
        app.processEvents()
        image = chip.grab().toImage()
        accent = QColor(Color.ACCENT_PRIMARY).rgb()
        count = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixel(x, y) == accent
        )
        host.close()
        host.deleteLater()
        app.processEvents()
        return count
    finally:
        app.setStyleSheet(previous)


def test_a_described_room_state_is_not_painted_like_a_call_to_action():
    """The strip already has one loud control. A second accent block would
    make the room argue with itself, so a description carries no accent at
    all -- and a request is marked by an edge rather than by a fill."""

    described = _accent_pixels_at_rest(
        art_room_presence(_room(canvas=CanvasCompanionState.READY))
    )
    requested = _accent_pixels_at_rest(
        art_room_presence(_room(canvas=CanvasCompanionState.MISSING_APP))
    )

    assert described == 0
    assert requested > 0


def test_repeating_the_same_line_does_not_repaint_or_re_announce():
    """It is refreshed on a timer, so an unchanged line has to be a no-op.

    A screen reader hearing "shared canvas" once a second would be worse than
    silence, and repolishing a styled widget every tick is wasted work.
    """

    from unittest.mock import patch

    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()
    presence = art_room_presence(_room(canvas=CanvasCompanionState.READY))
    chip.set_presence(presence)

    with patch.object(ArtRoomChip, "_announce") as announce:
        for _ in range(5):
            chip.set_presence(presence)

    assert announce.call_count == 0
    assert chip.chip.text() == "Shared canvas"


def test_a_changed_line_is_announced_once():
    from unittest.mock import patch

    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()
    chip.set_presence(art_room_presence(_room(canvas=CanvasCompanionState.READY)))

    with patch.object(ArtRoomChip, "_announce") as announce:
        chip.set_presence(
            art_room_presence(_room(canvas=CanvasCompanionState.MISSING_APP))
        )
        chip.set_presence(
            art_room_presence(_room(canvas=CanvasCompanionState.MISSING_APP))
        )

    assert announce.call_count == 1
    assert chip.chip.text() == "Install Drawpile"


def test_announcing_survives_a_headless_accessibility_backend():
    """Offscreen and teardown paths have none, and the room must not care."""

    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()
    chip.set_presence(art_room_presence(_room(canvas=CanvasCompanionState.READY)))

    assert chip.chip.accessibleName() == "Shared canvas"


def test_the_chip_leaves_rather_than_sitting_there_disabled():
    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()
    chip.set_presence(art_room_presence(_room(canvas=CanvasCompanionState.READY)))
    chip.set_presence(art_room_presence(_room(), hosting=False))

    assert chip.isHidden() is True
    assert chip.chip.offered is False


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        (CanvasCompanionState.READY, "canvas"),
        (CanvasCompanionState.MISSING_APP, "canvas"),
    ),
)
def test_pressing_the_chip_asks_for_one_named_panel(state, expected):
    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()
    chip.set_presence(art_room_presence(_room(canvas=state)))
    asked: list[str] = []
    chip.open_requested.connect(asked.append)

    chip.chip.click()

    assert asked == [expected]


def test_pressing_a_video_line_asks_for_the_video_panel():
    from webjam_qt.widgets.art_room_chip import ArtRoomChip

    chip = ArtRoomChip()
    chip.set_presence(art_room_presence(_room(video=VideoCompanionState.PLAYING)))
    asked: list[str] = []
    chip.open_requested.connect(asked.append)

    chip.chip.click()

    assert asked == ["video"]


def test_the_chip_opens_nothing_itself_so_it_cannot_take_focus():
    """It asks; the controller decides, and the controller already declines
    focus when a companion panel is showing this room."""

    import ast
    from pathlib import Path

    source = Path("webjam_qt/widgets/art_room_chip.py").read_text(encoding="utf-8")
    calls = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "raise_" not in calls
    assert "activateWindow" not in calls
    assert "exec" not in calls


def test_the_chip_stays_narrow_enough_for_a_meeting_beside_it():
    from webjam_qt.widgets.art_room_chip import MAX_WIDTH, ArtRoomChip

    chip = ArtRoomChip()
    chip.set_presence(
        art_room_presence(_room(canvas=CanvasCompanionState.MISSING_APP))
    )

    assert chip.maximumWidth() == MAX_WIDTH
    assert MAX_WIDTH <= 260


# ---------------------------------------------------------------------------
# In the room chrome, for Art only
# ---------------------------------------------------------------------------


def _strip(profile_key: str):
    from core.creative_modes import get_creator_profile_by_key
    from webjam_qt.widgets.session_strip import SessionStrip

    strip = SessionStrip(mode_entries=[], initial_mode_key=profile_key)
    strip.set_creator_profile(get_creator_profile_by_key(profile_key))
    return strip


@pytest.mark.parametrize(
    "profile_key", ("music", "podcast_voice", "review_rehearsal")
)
def test_a_profile_without_these_layers_has_no_art_line_to_show(profile_key):
    """Not hidden-but-present: a profile with no canvas and no video is told
    to show nothing even when handed a live line."""

    strip = _strip(profile_key)

    strip.set_art_room_presence(
        art_room_presence(_room(canvas=CanvasCompanionState.READY))
    )

    assert strip.art_room_chip.isHidden() is True
    assert strip.art_room_chip.presence.offered is False


def test_art_shows_the_line_in_the_room_chrome():
    strip = _strip("art")

    strip.set_art_room_presence(
        art_room_presence(_room(canvas=CanvasCompanionState.READY))
    )

    assert strip.art_room_chip.presence.label == "Shared canvas"
    assert strip.art_room_chip.chip.text() == "Shared canvas"


def test_pressing_the_room_line_asks_the_room_for_the_right_tool():
    """It routes to the same tool the menu does, so there is one way in with
    two doors rather than two implementations."""

    strip = _strip("art")
    asked: list[str] = []
    strip.tool_requested.connect(asked.append)

    strip.set_art_room_presence(
        art_room_presence(_room(canvas=CanvasCompanionState.READY))
    )
    strip.art_room_chip.chip.click()
    strip.set_art_room_presence(
        art_room_presence(_room(video=VideoCompanionState.PLAYING))
    )
    strip.art_room_chip.chip.click()

    assert asked == ["shared_canvas", "reference_video"]


def test_switching_a_strip_from_art_to_music_clears_the_line():
    """Profile switches happen live, and a stale canvas line in a music room
    would be a claim about a room that does not have one."""

    from core.creative_modes import get_creator_profile_by_key

    strip = _strip("art")
    strip.set_art_room_presence(
        art_room_presence(_room(canvas=CanvasCompanionState.READY))
    )
    assert strip.art_room_chip.presence.offered is True

    strip.set_creator_profile(get_creator_profile_by_key("music"))

    assert strip.art_room_chip.presence.offered is False
    assert strip.art_room_chip.isHidden() is True
