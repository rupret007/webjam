"""The room clock as a painter sees it, and where the controller wires it.

The readout exists so someone painting the cover does not have to leave the
canvas to find out where the room is. It is a readout and never a transport:
the room has one owner, and a painter reading the pulse is not it.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLineEdit,
    QPushButton,
    QSlider,
)

from core.creative_modes import CREATOR_PROFILES  # noqa: E402
from core.room_clock import (  # noqa: E402
    NO_CLOCK_HEADLINE,
    SONG_DETAIL,
    RoomClockSource,
    RoomClockView,
    render_room_clock,
)
from core.session_transfer import (  # noqa: E402
    RecordingSignal,
    RoomClockSessionSnapshot,
    RoomClockSourceValue,
    SessionStateSnapshot,
)
from core.shared_canvas import SharedCanvasSnapshot  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.widgets.room_clock_label import RoomClockLabel  # noqa: E402
from webjam_qt.windows.shared_canvas import SharedCanvasDialog  # noqa: E402

SESSION_ID = str(uuid.uuid4())
INVITE_TOKEN = "invite-token-for-room-clock-integration"


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _song_view() -> RoomClockView:
    return render_room_clock(
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.SONG_FORM,
            running=True,
            bar=17,
            beat=3,
            section_label="Chorus",
            tempo_bpm=124.0,
            meter_numerator=4,
            meter_denominator=4,
        )
    )


def _video_view() -> RoomClockView:
    return render_room_clock(
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.REFERENCE_VIDEO,
            running=True,
            position_s=134.0,
            duration_s=330.0,
        )
    )


# ---------------------------------------------------------------------------
# The readout
# ---------------------------------------------------------------------------


def test_the_readout_shows_the_pulse_and_where_it_came_from():
    label = RoomClockLabel()
    try:
        label.set_view(_song_view())

        assert label._headline.text() == "Bar 17.3 · Chorus"
        assert "song the room wrote" in label._detail.text()
        assert "not what anyone is playing" in label._detail.text()
        assert "124 BPM" in label._detail.text()
        assert label.property("clock") == "song_form"
    finally:
        label.deleteLater()


def test_a_video_pulse_is_not_dressed_up_as_music():
    label = RoomClockLabel()
    try:
        label.set_view(_video_view())

        assert label._headline.text() == "2:14 / 5:30"
        assert "Bar" not in label._headline.text()
        assert label.property("clock") == "reference_video"
    finally:
        label.deleteLater()


def test_an_absent_clock_says_so_rather_than_showing_a_hopeful_zero():
    label = RoomClockLabel()
    try:
        label.set_view(RoomClockView())

        assert label._headline.text() == NO_CLOCK_HEADLINE
        assert "0:00" not in label._headline.text()
        assert label.property("clock") == "none"
    finally:
        label.deleteLater()


def test_a_stale_clock_is_marked_rather_than_hidden():
    label = RoomClockLabel()
    try:
        label.set_view(
            render_room_clock(
                RoomClockSessionSnapshot(
                    source=RoomClockSourceValue.REFERENCE_VIDEO,
                    running=True,
                    position_s=100.0,
                    duration_s=600.0,
                ),
                age_s=60.0,
            )
        )

        assert label.property("stale") is True
        assert "out of date" in label._detail.text()
        # The number it stopped at is still readable.
        assert label._headline.text() == "1:40 / 10:00"
    finally:
        label.deleteLater()


def test_the_readout_is_never_a_control():
    """The room has one owner, and a painter reading the pulse is not it."""

    label = RoomClockLabel()
    try:
        label.set_view(_song_view())

        assert label.findChildren(QPushButton) == []
        assert label.findChildren(QSlider) == []
        assert label.findChildren(QLineEdit) == []
        assert label.focusPolicy() is Qt.FocusPolicy.NoFocus
    finally:
        label.deleteLater()


def test_the_readout_announces_itself_to_assistive_technology():
    label = RoomClockLabel()
    try:
        label.set_view(_song_view())

        announced = label.accessibleDescription()
        assert "Bar 17.3 · Chorus" in announced
        assert "song the room wrote" in announced
        assert "not what anyone is playing" in announced
    finally:
        label.deleteLater()


# ---------------------------------------------------------------------------
# Where a painter sees it
# ---------------------------------------------------------------------------


def test_the_canvas_panel_shows_the_pulse_while_you_paint():
    """A painter should not leave the canvas to find out where the room is."""

    dialog = SharedCanvasDialog(hosting=False)
    try:
        dialog.set_room_clock(_song_view())

        assert dialog._room_clock.isHidden() is False
        assert dialog._room_clock._headline.text() == "Bar 17.3 · Chorus"
    finally:
        dialog.deleteLater()


def test_the_canvas_panel_stays_quiet_when_the_room_has_no_pulse():
    dialog = SharedCanvasDialog(hosting=False)
    try:
        dialog.set_room_clock(RoomClockView())
        assert dialog._room_clock.isHidden() is True

        dialog.set_room_clock(_video_view())
        assert dialog._room_clock.isHidden() is False

        dialog.set_room_clock(RoomClockView())
        assert dialog._room_clock.isHidden() is True
    finally:
        dialog.deleteLater()


def test_the_clock_adds_no_control_to_the_canvas_panel():
    dialog = SharedCanvasDialog(hosting=False)
    try:
        before = [
            button.text()
            for button in dialog.findChildren(QPushButton)
            if not button.isHidden()
        ]
        dialog.set_room_clock(_song_view())
        after = [
            button.text()
            for button in dialog.findChildren(QPushButton)
            if not button.isHidden()
        ]

        assert before == after
    finally:
        dialog.deleteLater()


def test_a_canvas_host_sees_the_pulse_too():
    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))
        dialog.set_room_clock(_video_view())

        assert dialog._room_clock.isHidden() is False
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# The controller seam
# ---------------------------------------------------------------------------


def _controller(profile_key: str = "art") -> ApplicationController:
    controller = ApplicationController.__new__(ApplicationController)
    controller._active_creator_profile_key = profile_key
    controller._shutdown = False
    controller._shutdown_in_progress = False
    controller._shutdown_cleanup_pending = False
    controller._room_clock = None
    controller._room_clock_binding = ()
    controller._reference_video = None
    controller._shared_canvas_dialog = None
    controller.window = SimpleNamespace(flash_message=MagicMock())
    return controller


def _as_host(controller: ApplicationController) -> None:
    controller.host_peer = SimpleNamespace(
        active=True,
        credentials=SimpleNamespace(
            session_id=SESSION_ID, invite_token=INVITE_TOKEN
        ),
        publish_room_clock_state=MagicMock(),
    )
    controller.guest_peer = None


def _as_guest(controller: ApplicationController) -> None:
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = SimpleNamespace(
        invite=SimpleNamespace(
            peer_enabled=True,
            session_id=SESSION_ID,
            invite_token=INVITE_TOKEN,
        )
    )


def test_art_still_has_no_song_form_when_music_has_not_opened_one():
    """Art must work with no musical pulse, and it does."""

    controller = _controller()

    assert controller._room_clock_song_form() is None


def test_a_music_song_clock_publishes_into_the_room_clock():
    """Painters ride the same pulse musicians already count."""

    from core.room_clock import RoomClockSource

    controller = _controller("music")
    _as_host(controller)
    controller._song_tools = SimpleNamespace(
        is_available=lambda: True,
        workbench=SimpleNamespace(
            clock_snapshot=lambda: SimpleNamespace(
                has_form=True,
                sections=("Verse", "Chorus"),
                follows_shared_track=True,
                running=True,
                position_s=24.0,
                bar=17,
                beat=1,
                section_label="Chorus",
                tempo_bpm=124.0,
                meter_numerator=0,
                meter_denominator=0,
                section_lengths_assumed=True,
            )
        ),
    )

    facts = controller._room_clock_song_form()
    assert facts is not None
    assert facts.source is RoomClockSource.SONG_FORM
    assert facts.bar == 17
    assert facts.section_label == "Chorus"

    controller._tick_room_clock()
    published = controller.host_peer.publish_room_clock_state.call_args.kwargs
    assert published["source"] == "song_form"
    assert published["bar"] == 17
    assert published["follows_shared_track"] is True
    assert published["section_lengths_assumed"] is True
    assert published["form_shape"] == "Verse → Chorus"
    assert published["meter_numerator"] == 0
    assert published["meter_denominator"] == 0


def test_a_shared_track_without_a_form_does_not_publish_a_song_clock():
    """A file playing with no written parts is not a song form for painters."""

    controller = _controller("music")
    _as_host(controller)
    controller._song_tools = SimpleNamespace(
        is_available=lambda: True,
        workbench=SimpleNamespace(
            clock_snapshot=lambda: SimpleNamespace(
                has_form=False,
                sections=(),
                follows_shared_track=True,
                running=True,
                position_s=34.0,
                bar=0,
                beat=0,
                section_label="",
                tempo_bpm=0,
            )
        ),
    )

    assert controller._room_clock_song_form() is None
    controller._tick_room_clock()
    controller.host_peer.publish_room_clock_state.assert_not_called()


def test_a_non_music_room_never_invents_a_song_form():
    controller = _controller("art")
    controller._song_tools = SimpleNamespace(is_available=lambda: False)

    assert controller._room_clock_song_form() is None


def test_a_painter_sees_the_pulse_on_the_strip_without_opening_the_canvas():
    from core.room_clock import RoomClockView, RoomClockSource

    controller = _controller("art")
    strip = SimpleNamespace(set_song_line=MagicMock())
    controller.window = SimpleNamespace(
        flash_message=MagicMock(), session_strip=strip
    )

    controller._on_room_clock_view(
        RoomClockView(
            source=RoomClockSource.SONG_FORM,
            headline="Bar 17.1 · Chorus",
            detail=SONG_DETAIL,
            running=True,
            musical=True,
        )
    )

    strip.set_song_line.assert_called_once_with(
        "Bar 17.1 · Chorus",
        description=SONG_DETAIL,
    )


def test_music_keeps_its_own_song_line_owner():
    from core.room_clock import RoomClockView, RoomClockSource

    controller = _controller("music")
    strip = SimpleNamespace(set_song_line=MagicMock())
    controller.window = SimpleNamespace(
        flash_message=MagicMock(), session_strip=strip
    )

    controller._on_room_clock_view(
        RoomClockView(
            source=RoomClockSource.SONG_FORM,
            headline="Bar 17.1 · Chorus",
            detail=SONG_DETAIL,
            running=True,
            musical=True,
        )
    )

    strip.set_song_line.assert_not_called()


def test_the_clock_binds_for_a_host_and_for_a_guest():
    for arrange, expected in ((_as_host, "host"), (_as_guest, "guest")):
        controller = _controller()
        arrange(controller)

        coordinator = controller._room_clock_coordinator()

        assert coordinator is not None
        assert coordinator.role == expected


def test_the_clock_is_not_gated_on_a_creator_profile():
    """A music surface owns this pulse without becoming Art."""

    for profile in CREATOR_PROFILES:
        controller = _controller(profile.key)
        _as_host(controller)

        assert controller._room_clock_coordinator() is not None


def test_outside_a_room_there_is_no_clock_to_bind():
    controller = _controller()
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = None

    assert controller._room_clock_coordinator() is None
    assert controller._room_clock is None


def test_a_new_room_rebuilds_the_clock():
    controller = _controller()
    _as_host(controller)
    first = controller._room_clock_coordinator()

    controller.host_peer = SimpleNamespace(
        active=True,
        credentials=SimpleNamespace(
            session_id=str(uuid.uuid4()), invite_token=INVITE_TOKEN
        ),
        publish_room_clock_state=MagicMock(),
    )

    assert controller._room_clock_coordinator() is not first


def test_a_host_with_a_playing_video_publishes_that_position():
    from core.reference_video import ReferenceVideoSnapshot, ReferenceVideoState

    controller = _controller()
    _as_host(controller)
    controller._reference_video = SimpleNamespace(
        hosting=True,
        host_snapshot=ReferenceVideoSnapshot(
            state=ReferenceVideoState.PLAYING,
            shared=True,
            source_display_name="lesson.mp4",
            identity_digest="a" * 64,
            position_s=90.0,
            duration_s=600.0,
        ),
    )

    controller._tick_room_clock()

    published = controller.host_peer.publish_room_clock_state.call_args.kwargs
    assert published["source"] == "reference_video"
    assert published["position_s"] == pytest.approx(90.0)
    # A file offset is never dressed up as a bar.
    assert published["bar"] == 0
    assert published["section_label"] == ""


def test_a_host_with_no_video_publishes_no_pulse():
    controller = _controller()
    _as_host(controller)

    controller._tick_room_clock()

    controller.host_peer.publish_room_clock_state.assert_not_called()


def test_a_guest_render_feeds_the_readout():
    controller = _controller()
    _as_guest(controller)
    coordinator = controller._room_clock_coordinator()

    coordinator.observe_host_state(
        SessionStateSnapshot(
            session_id=SESSION_ID,
            generation=3,
            signal=RecordingSignal.IDLE,
            creator_profile_key="art",
            room_clock=RoomClockSessionSnapshot(
                source=RoomClockSourceValue.SONG_FORM,
                running=True,
                bar=9,
                section_label="Verse",
            ),
        )
    )

    view = coordinator.view
    assert view.source is RoomClockSource.SONG_FORM
    assert view.headline == "Bar 9 · Verse"


def test_a_clock_failure_never_breaks_the_room():
    controller = _controller()
    _as_host(controller)
    controller._reference_video = SimpleNamespace(
        hosting=True,
        host_snapshot=property(lambda self: 1 / 0),
    )

    # Must not raise.
    controller._tick_room_clock()


def test_releasing_the_clock_returns_the_room_to_no_pulse():
    from core.reference_video import ReferenceVideoSnapshot, ReferenceVideoState

    controller = _controller()
    _as_host(controller)
    controller._reference_video = SimpleNamespace(
        hosting=True,
        host_snapshot=ReferenceVideoSnapshot(
            state=ReferenceVideoState.PLAYING,
            shared=True,
            source_display_name="lesson.mp4",
            identity_digest="a" * 64,
            position_s=90.0,
            duration_s=600.0,
        ),
    )
    controller._tick_room_clock()

    controller._release_room_clock()

    assert controller._room_clock is None
    assert (
        controller.host_peer.publish_room_clock_state.call_args.kwargs["source"]
        == "none"
    )
