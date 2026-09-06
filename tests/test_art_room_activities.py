"""Both real optional activities remain reachable in the room's priority order."""

from dataclasses import asdict

import pytest

from core.art_companion import (
    AiCompanionState,
    ArtCompanionProjection,
    CanvasCompanionState,
    VideoCompanionState,
)
from core.art_room_activities import art_room_activities
from core.art_room_presence import ArtPresenceTarget, art_room_presence


@pytest.mark.parametrize("canvas", list(CanvasCompanionState))
@pytest.mark.parametrize("video", list(VideoCompanionState))
@pytest.mark.parametrize("hosting", [False, True])
@pytest.mark.parametrize("intent", [(False, False), (False, True), (True, False), (True, True)])
def test_each_real_activity_survives_the_other_activitys_priority(canvas, video, hosting, intent):
    projection = ArtCompanionProjection(
        in_room=True, canvas=canvas, video=video, transport_allowed=hosting,
    )
    before = asdict(projection)
    intended_canvas, intended_video = intent

    activities = art_room_activities(
        projection, hosting=hosting,
        intended_canvas=intended_canvas, intended_video=intended_video,
    )

    expected = set()
    if canvas is not CanvasCompanionState.NONE:
        expected.add(ArtPresenceTarget.CANVAS)
    if video is not VideoCompanionState.NONE:
        expected.add(ArtPresenceTarget.VIDEO)
    if not expected and hosting:
        if intended_canvas:
            expected.add(ArtPresenceTarget.CANVAS)
        elif intended_video:
            expected.add(ArtPresenceTarget.VIDEO)
    assert {activity.target for activity in activities} == expected
    assert len(activities) == len(expected) <= 2
    assert all(activity.offered for activity in activities)
    assert asdict(projection) == before
    primary = art_room_presence(
        projection, hosting=hosting,
        intended_canvas=intended_canvas, intended_video=intended_video,
    )
    if activities:
        assert activities[0] == primary
    else:
        assert not primary.offered


@pytest.mark.parametrize("canvas", [CanvasCompanionState.READY, CanvasCompanionState.MISSING_APP])
def test_hidden_paint_along_keeps_its_own_return_route_beside_a_canvas(canvas):
    activities = art_room_activities(ArtCompanionProjection(
        in_room=True, canvas=canvas, video=VideoCompanionState.HIDDEN,
    ))

    assert [activity.target for activity in activities] == [
        ArtPresenceTarget.CANVAS, ArtPresenceTarget.VIDEO,
    ]
    assert activities[1].label == "Paint along (hidden)"
    assert "show it again" in activities[1].description


@pytest.mark.parametrize("video", [VideoCompanionState.NEEDS_FILE, VideoCompanionState.LOCAL_ATTENTION])
def test_video_recovery_keeps_the_ready_canvas_reachable(video):
    activities = art_room_activities(ArtCompanionProjection(
        in_room=True, canvas=CanvasCompanionState.READY, video=video,
    ))

    assert [activity.target for activity in activities] == [
        ArtPresenceTarget.VIDEO, ArtPresenceTarget.CANVAS,
    ]
    assert activities[1].label == "Shared canvas"


@pytest.mark.parametrize("hosting", [False, True])
def test_outside_the_room_saved_intents_offer_no_activity(hosting):
    assert art_room_activities(
        ArtCompanionProjection(), hosting=hosting,
        intended_canvas=True, intended_video=True,
    ) == ()


@pytest.mark.parametrize("ai", list(AiCompanionState))
def test_personal_image_work_never_becomes_a_room_activity(ai):
    assert art_room_activities(ArtCompanionProjection(in_room=True, ai=ai)) == ()


def test_activity_projection_contains_only_bounded_room_status():
    activities = art_room_activities(ArtCompanionProjection(
        generation=81, revision=92, in_room=True,
        canvas=CanvasCompanionState.MISSING_APP,
        video=VideoCompanionState.LOCAL_ATTENTION,
    ))
    for activity in activities:
        assert set(asdict(activity)) == {"label", "description", "tone", "target"}
        assert len(activity.label) <= 40
        assert len(activity.description) <= 180
