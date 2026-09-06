"""Connection and closure evidence outrank optional Art room layers."""

from dataclasses import asdict, replace

import pytest

from core.art_companion import (
    ArtCompanionProjection,
    CanvasCompanionState,
    VideoCompanionState,
)
from core.art_room_overview import ArtRoomOverview, art_room_overview
from core.art_room_presence import ABSENT, ArtPresenceTarget, ArtRoomPresence, art_room_presence
from core.session_conductor import ArtRoomState


def paint_along():
    return art_room_presence(ArtCompanionProjection(
        in_room=True, video=VideoCompanionState.NEEDS_FILE,
    ))


def canvas():
    return art_room_presence(ArtCompanionProjection(
        in_room=True, canvas=CanvasCompanionState.READY,
    ))


@pytest.mark.parametrize("hosting", [False, True])
def test_only_confirmed_room_connection_offers_shared_activity(hosting):
    presence = paint_along()
    for state in ArtRoomState:
        overview = art_room_overview(
            state=state, hosting=hosting, presence=presence, secondary_presence=canvas(),
        )
        confirmed = state is ArtRoomState.CONNECTED or (
            hosting and state is ArtRoomState.WAITING
        )
        assert overview.activity_enabled is confirmed
        assert overview.secondary_activity_enabled is confirmed
        assert overview.activity_actions == (("video", "canvas") if confirmed else ())
        assert (overview.secondary_activity_action == "canvas") is confirmed
        assert overview.conversation_enabled
        assert (overview.activity_action == "video") is confirmed
        if not confirmed:
            assert "host shared" not in overview.activity_detail
            assert not overview.activity_action


@pytest.mark.parametrize("hosting", [False, True])
def test_cleanup_and_end_remove_stale_room_activity_before_connection_facts(hosting):
    current = dict(
        state=ArtRoomState.CONNECTED, hosting=hosting,
        presence=paint_along(), secondary_presence=canvas(),
    )
    closing = art_room_overview(**current, stopping=True)
    assert closing.phase == "ending"
    cleanup = art_room_overview(**current, stopping=True, ended=True, cleanup_required=True)
    assert cleanup.phase == "cleanup_required"
    assert ("Try End Room" if hosting else "Try Leave Room") in cleanup.connection_detail
    ended = art_room_overview(**current, ended=True)
    assert ended.phase == "ended"
    for view in (closing, cleanup, ended):
        assert not view.activity_enabled
        assert not view.secondary_activity_enabled
        assert not view.secondary_activity_action
        assert not view.secondary_activity_label
        assert view.activity_actions == ()
        assert not view.activity_action
        assert view.conversation_enabled is (view is ended)
        assert "connected to the host" not in view.connection_label.lower()
    assert "own leave controls" in ended.connection_detail


def test_make_together_is_complete_without_optional_tools_or_a_roster():
    waiting = art_room_overview(state=ArtRoomState.WAITING, hosting=True)
    joined = art_room_overview(state=ArtRoomState.CONNECTED, hosting=False)
    assert waiting.phase == "waiting"
    assert joined.phase == "connected"
    assert "Waiting for artists" in waiting.connection_label
    assert joined.connection_label == "Connected to the host"
    for overview in (waiting, joined):
        assert overview.activity_label == "Bring your own tools"
        assert overview.conversation_enabled
        assert not overview.activity_action
        public = repr(asdict(overview)).lower()
        assert "0 artists" not in public
        assert "mixer" not in public
        assert "setup required" not in public


def test_opening_never_promotes_a_cached_connected_state():
    opening = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=False, probing=True,
        presence=paint_along(), secondary_presence=canvas(),
    )
    assert opening.phase == "opening"
    assert not opening.activity_enabled
    assert not opening.secondary_activity_enabled
    assert opening.activity_actions == ()
    assert opening.conversation_enabled


def test_room_loss_does_not_take_away_independent_conversation_controls():
    for state in (ArtRoomState.NONE, ArtRoomState.RECONNECTING, ArtRoomState.FAILED):
        view = art_room_overview(state=state, hosting=False)
        assert view.conversation_enabled
        assert not view.activity_enabled
        assert not view.secondary_activity_enabled
        assert not view.secondary_activity_action
        assert not view.secondary_activity_label
        assert view.activity_actions == ()


def test_quit_cleanup_uses_quit_recovery_even_after_the_room_owner_clears():
    for state in (ArtRoomState.CONNECTED, ArtRoomState.NONE):
        view = art_room_overview(
            state=state, hosting=False, quitting=True,
            presence=paint_along(), secondary_presence=canvas(),
        )
        assert view.phase == "cleanup_required"
        assert "Quit again" in view.connection_detail
        assert "Try Leave Room" not in view.connection_detail
        assert not view.activity_enabled
        assert not view.secondary_activity_enabled
        assert not view.secondary_activity_action
        assert not view.secondary_activity_label
        assert view.activity_actions == ()
        assert not view.conversation_enabled


@pytest.mark.parametrize("primary,secondary", [(paint_along(), canvas()), (canvas(), paint_along())])
def test_both_offered_panels_keep_their_status_and_action(primary, secondary):
    overview = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=False,
        presence=primary, secondary_presence=secondary,
    )
    assert overview.activity_label == primary.label
    assert overview.activity_detail == primary.description
    assert overview.secondary_activity_label == secondary.label
    assert overview.secondary_activity_detail == secondary.description
    assert overview.secondary_activity_action_label == (
        "Open canvas" if secondary.target is ArtPresenceTarget.CANVAS else "Open Paint along"
    )
    assert overview.activity_actions == (primary.target.value, secondary.target.value)
    assert overview.conversation_enabled


@pytest.mark.parametrize("secondary", [
    paint_along(), ABSENT,
    ArtRoomPresence(label="Invalid activity", target="unknown"),
    ArtRoomPresence(label="No target", target=ArtPresenceTarget.NONE),
    ArtRoomPresence(target=ArtPresenceTarget.CANVAS),
])
def test_duplicate_or_invalid_secondary_offers_never_create_an_action(secondary):
    overview = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=False,
        presence=paint_along(), secondary_presence=secondary,
    )
    assert overview.activity_actions == ("video",)
    assert not overview.secondary_activity_enabled
    assert not overview.secondary_activity_label
    assert not overview.secondary_activity_detail
    assert not overview.secondary_activity_action
    assert not overview.secondary_activity_action_label


def test_unknown_primary_target_does_not_publish_an_activity():
    overview = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=False,
        presence=ArtRoomPresence(label="Unknown", target="unknown"),
    )
    assert overview.activity_label == "Bring your own tools"
    assert overview.activity_actions == ()


def test_activity_actions_defensively_reject_disabled_unknown_and_duplicate_targets():
    view = ArtRoomOverview(
        activity_enabled=True, activity_action="video",
        secondary_activity_enabled=True, secondary_activity_action="canvas",
    )
    assert view.activity_actions == ("video", "canvas")
    assert replace(view, secondary_activity_enabled=False).activity_actions == ("video",)
    assert replace(view, secondary_activity_action="video").activity_actions == ("video",)
    assert replace(view, secondary_activity_action="unknown").activity_actions == ("video",)
    assert replace(view, activity_enabled=False).activity_actions == ("canvas",)
    assert ArtRoomOverview().activity_actions == ()
