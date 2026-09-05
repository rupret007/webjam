"""Deriving the companion projection from the desktop's own snapshots.

The snapshots this reads from *do* carry private things -- a file's display
name, an identity digest, a canvas server label, a generator's backend
address. The projection carries none of them. These tests feed real snapshot
shapes in and check both that the finite state is right and that the private
fields did not come along for the ride.
"""

from __future__ import annotations

import pytest

from core.ai_image import AiImageSnapshot, AiImageState
from core.art_companion import (
    AiCompanionState,
    CanvasCompanionState,
    VideoCompanionState,
)
from core.reference_video import (
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
    ReferenceVideoSnapshot,
    ReferenceVideoState,
)
from core.shared_canvas import (
    SharedCanvasFollowSnapshot,
    SharedCanvasFollowState,
    SharedCanvasSnapshot,
    SharedCanvasState,
)
from webjam_qt.controllers.art_companion_projection import (
    ai_companion_state,
    build_art_companion_projection,
    canvas_companion_state,
    video_companion_state,
)


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        (SharedCanvasFollowState.NO_CANVAS, CanvasCompanionState.NONE),
        (SharedCanvasFollowState.READY, CanvasCompanionState.READY),
        (SharedCanvasFollowState.OPENED, CanvasCompanionState.OPENING),
        (SharedCanvasFollowState.NEEDS_DRAWPILE, CanvasCompanionState.MISSING_APP),
        (SharedCanvasFollowState.UNREADABLE, CanvasCompanionState.UNREADABLE),
    ),
)
def test_every_guest_canvas_state_maps_to_one_finite_companion_state(state, expected):
    snapshot = SharedCanvasFollowSnapshot(state=state)

    assert canvas_companion_state(snapshot, hosting=False) is expected


def test_a_hosted_canvas_that_is_shared_and_openable_reads_as_ready():
    snapshot = SharedCanvasSnapshot(
        state=SharedCanvasState.SHARED,
        shared=True,
        server_label="paint.example.org",
        session_label="Jeff's room",
        carries_password=True,
        launcher_available=True,
    )

    assert canvas_companion_state(snapshot, hosting=True) is CanvasCompanionState.READY


def test_a_room_with_no_canvas_does_not_advertise_a_missing_program():
    """Nobody needs Drawpile for a room that has no canvas, so saying it is
    missing would be noise pointing at a non-problem."""

    snapshot = SharedCanvasSnapshot(state=SharedCanvasState.IDLE, shared=False)

    assert canvas_companion_state(snapshot, hosting=True) is CanvasCompanionState.NONE


def test_a_shared_canvas_this_computer_cannot_open_reads_as_missing_program():
    snapshot = SharedCanvasSnapshot(
        state=SharedCanvasState.SHARED, shared=True, launcher_available=False
    )

    assert (
        canvas_companion_state(snapshot, hosting=True)
        is CanvasCompanionState.MISSING_APP
    )


def test_a_failed_host_canvas_reads_as_unreadable_because_nothing_opened():
    snapshot = SharedCanvasSnapshot(
        state=SharedCanvasState.FAILED, error="Drawpile did not start."
    )

    assert (
        canvas_companion_state(snapshot, hosting=True)
        is CanvasCompanionState.UNREADABLE
    )


def test_a_missing_canvas_snapshot_is_simply_no_canvas():
    assert canvas_companion_state(None, hosting=True) is CanvasCompanionState.NONE
    assert canvas_companion_state(None, hosting=False) is CanvasCompanionState.NONE


# ---------------------------------------------------------------------------
# Reference video
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        (ReferenceVideoState.READY, VideoCompanionState.READY),
        (ReferenceVideoState.PLAYING, VideoCompanionState.PLAYING),
        (ReferenceVideoState.PAUSED, VideoCompanionState.PAUSED),
    ),
)
def test_a_hosts_transport_states_map_straight_through(state, expected):
    snapshot = ReferenceVideoSnapshot(
        state=state,
        shared=True,
        source_display_name="reference-cut-final.mp4",
        identity_digest="ab" * 32,
        position_s=41.0,
        duration_s=300.0,
    )

    assert video_companion_state(snapshot, hosting=True) is expected


def test_a_host_who_has_loaded_nothing_is_a_room_with_no_video():
    """Whatever the transport last said, nothing is shared."""

    snapshot = ReferenceVideoSnapshot(state=ReferenceVideoState.PAUSED, shared=False)

    assert video_companion_state(snapshot, hosting=True) is VideoCompanionState.NONE


def test_a_hosts_own_failure_asks_for_the_hosts_attention():
    snapshot = ReferenceVideoSnapshot(
        state=ReferenceVideoState.FAILED, error="The file could not be opened."
    )

    assert (
        video_companion_state(snapshot, hosting=True)
        is VideoCompanionState.HOST_ATTENTION
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        (ReferenceVideoFollowState.NO_VIDEO, VideoCompanionState.NONE),
        (ReferenceVideoFollowState.HIDDEN, VideoCompanionState.HIDDEN),
        (ReferenceVideoFollowState.NEEDS_FILE, VideoCompanionState.NEEDS_FILE),
        (
            ReferenceVideoFollowState.MISMATCHED_FILE,
            VideoCompanionState.MISMATCHED_FILE,
        ),
        (
            ReferenceVideoFollowState.FILE_UNAVAILABLE,
            VideoCompanionState.FILE_UNAVAILABLE,
        ),
        (
            ReferenceVideoFollowState.HOST_ATTENTION,
            VideoCompanionState.HOST_ATTENTION,
        ),
        (ReferenceVideoFollowState.LOCAL_ATTENTION, VideoCompanionState.LOCAL_ATTENTION),
        (ReferenceVideoFollowState.STALLED, VideoCompanionState.STALLED),
    ),
)
def test_every_guest_video_state_maps_to_one_finite_companion_state(state, expected):
    snapshot = ReferenceVideoFollowSnapshot(state=state)

    assert video_companion_state(snapshot, hosting=False) is expected


@pytest.mark.parametrize(
    ("should_play", "expected"),
    ((True, VideoCompanionState.PLAYING), (False, VideoCompanionState.PAUSED)),
)
def test_a_following_guest_reports_playing_or_paused_from_the_host(
    should_play, expected
):
    """"Following" is not a state a person recognises; playing and paused are,
    and the host's own transport is what decides which."""

    snapshot = ReferenceVideoFollowSnapshot(
        state=ReferenceVideoFollowState.FOLLOWING,
        can_follow=True,
        should_play=should_play,
    )

    assert video_companion_state(snapshot, hosting=False) is expected


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        (AiImageState.NOT_IN_A_ROOM, AiCompanionState.UNAVAILABLE),
        (AiImageState.NEEDS_KRITA, AiCompanionState.UNAVAILABLE),
        (AiImageState.NEEDS_PLUGIN, AiCompanionState.UNAVAILABLE),
        (AiImageState.READY, AiCompanionState.IDLE),
        (AiImageState.READY_MANAGED_BACKEND, AiCompanionState.IDLE),
    ),
)
def test_every_ai_state_maps_to_one_finite_companion_state(state, expected):
    assert ai_companion_state(AiImageSnapshot(state=state)) is expected


def test_an_opened_generator_reads_as_handed_off_not_as_a_running_job():
    snapshot = AiImageSnapshot(
        state=AiImageState.READY, activity="Krita is open for a new image."
    )

    assert ai_companion_state(snapshot) is AiCompanionState.HANDED_OFF


def test_a_failed_handoff_reads_as_failed():
    snapshot = AiImageSnapshot(
        state=AiImageState.READY, error="Krita could not be started."
    )

    assert ai_companion_state(snapshot) is AiCompanionState.FAILED


def test_the_generators_backend_address_never_reaches_the_projection():
    snapshot = AiImageSnapshot(
        state=AiImageState.READY, backend_label="127.0.0.1:8188"
    )

    assert ai_companion_state(snapshot) is AiCompanionState.IDLE


# ---------------------------------------------------------------------------
# The whole projection
# ---------------------------------------------------------------------------


def _published_text(projection) -> str:
    return " ".join(str(value) for value in projection.to_public_dict().values())


def test_no_private_value_from_any_snapshot_survives_into_the_projection():
    """The one test that matters most: real snapshots carrying a file name, a
    digest, a canvas server and session label, and a backend address, all of
    which must be absent from what a meeting-window panel can read."""

    projection = build_art_companion_projection(
        generation=3,
        revision=1,
        in_room=True,
        hosting=True,
        canvas_snapshot=SharedCanvasSnapshot(
            state=SharedCanvasState.SHARED,
            shared=True,
            server_label="paint.example.org",
            session_label="Jeff's studio",
            carries_password=True,
            launcher_available=True,
        ),
        video_snapshot=ReferenceVideoSnapshot(
            state=ReferenceVideoState.PLAYING,
            shared=True,
            source_display_name="unreleased-master-cut.mov",
            identity_digest="cd" * 32,
            position_s=12.5,
            duration_s=240.0,
        ),
        ai_snapshot=AiImageSnapshot(
            state=AiImageState.READY, backend_label="127.0.0.1:8188"
        ),
    )
    published = _published_text(projection)

    for secret in (
        "paint.example.org",
        "Jeff's studio",
        "unreleased-master-cut.mov",
        "cd" * 32,
        "127.0.0.1",
        "8188",
    ):
        assert secret not in published
    # And the positions the host is at are not published either: a file
    # offset is a detail of the host's disk, not a room fact.
    assert "12.5" not in published
    assert "240" not in published
    assert projection.canvas is CanvasCompanionState.READY
    assert projection.video is VideoCompanionState.PLAYING
    assert projection.transport_allowed is True


def test_a_guest_projection_never_grants_transport():
    projection = build_art_companion_projection(
        generation=1,
        revision=0,
        in_room=True,
        hosting=False,
        video_snapshot=ReferenceVideoFollowSnapshot(
            state=ReferenceVideoFollowState.FOLLOWING,
            can_follow=True,
            should_play=True,
        ),
    )

    assert projection.transport_allowed is False
    assert projection.video is VideoCompanionState.PLAYING


def test_a_host_projection_grants_transport():
    projection = build_art_companion_projection(
        generation=1, revision=0, in_room=True, hosting=True
    )

    assert projection.transport_allowed is True


def test_outside_a_room_the_projection_is_empty_whatever_is_passed_in():
    """A stale coordinator cannot make an empty desktop look busy."""

    projection = build_art_companion_projection(
        generation=5,
        revision=2,
        in_room=False,
        hosting=True,
        canvas_snapshot=SharedCanvasSnapshot(
            state=SharedCanvasState.SHARED, shared=True, launcher_available=True
        ),
        video_snapshot=ReferenceVideoSnapshot(
            state=ReferenceVideoState.PLAYING, shared=True
        ),
        ai_snapshot=AiImageSnapshot(state=AiImageState.READY),
    )

    assert projection.in_room is False
    assert projection.canvas is CanvasCompanionState.NONE
    assert projection.video is VideoCompanionState.NONE
    assert projection.ai is AiCompanionState.UNAVAILABLE
    assert projection.transport_allowed is False


def test_a_talk_only_room_projects_a_finished_state_not_a_broken_one():
    """Three "none"s and an unavailable generator is what a room where people
    are just talking and working looks like. It is a first-class answer."""

    projection = build_art_companion_projection(
        generation=1, revision=0, in_room=True, hosting=True
    )

    assert projection.canvas is CanvasCompanionState.NONE
    assert projection.video is VideoCompanionState.NONE
    assert projection.ai is AiCompanionState.UNAVAILABLE
    assert projection.in_room is True


def test_an_unknown_state_falls_back_to_absent_rather_than_guessing():
    """A future state nobody taught this mapper about must read as "nothing
    here", never as ready or playing."""

    class Invented:
        state = "teleported"
        should_play = True
        shared = True
        launcher_available = True

    assert canvas_companion_state(Invented(), hosting=False) is CanvasCompanionState.NONE
    assert video_companion_state(Invented(), hosting=False) is VideoCompanionState.NONE
    assert ai_companion_state(Invented()) is AiCompanionState.UNAVAILABLE
