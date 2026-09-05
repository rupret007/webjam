"""Connection and closure evidence outrank optional Art room layers."""

from dataclasses import asdict

import pytest

from core.art_companion import ArtCompanionProjection, VideoCompanionState
from core.art_room_overview import art_room_overview
from core.art_room_presence import art_room_presence
from core.session_conductor import ArtRoomState


def paint_along():
    return art_room_presence(ArtCompanionProjection(
        in_room=True, video=VideoCompanionState.NEEDS_FILE,
    ))


@pytest.mark.parametrize("hosting", [False, True])
def test_only_confirmed_room_connection_offers_shared_activity(hosting):
    presence = paint_along()
    for state in ArtRoomState:
        overview = art_room_overview(
            state=state, hosting=hosting, presence=presence,
        )
        confirmed = state is ArtRoomState.CONNECTED or (
            hosting and state is ArtRoomState.WAITING
        )
        assert overview.activity_enabled is confirmed
        assert overview.conversation_enabled
        assert (overview.activity_action == "video") is confirmed
        if not confirmed:
            assert "host shared" not in overview.activity_detail
            assert not overview.activity_action


@pytest.mark.parametrize("hosting", [False, True])
def test_cleanup_and_end_remove_stale_room_activity_before_connection_facts(hosting):
    current = dict(state=ArtRoomState.CONNECTED, hosting=hosting, presence=paint_along())
    closing = art_room_overview(**current, stopping=True)
    assert closing.phase == "ending"
    cleanup = art_room_overview(**current, stopping=True, ended=True, cleanup_required=True)
    assert cleanup.phase == "cleanup_required"
    assert ("Try End Room" if hosting else "Try Leave Room") in cleanup.connection_detail
    ended = art_room_overview(**current, ended=True)
    assert ended.phase == "ended"
    for view in (closing, cleanup, ended):
        assert not view.activity_enabled
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
        presence=paint_along(),
    )
    assert opening.phase == "opening"
    assert not opening.activity_enabled
    assert opening.conversation_enabled


def test_room_loss_does_not_take_away_independent_conversation_controls():
    for state in (ArtRoomState.NONE, ArtRoomState.RECONNECTING, ArtRoomState.FAILED):
        view = art_room_overview(state=state, hosting=False)
        assert view.conversation_enabled
        assert not view.activity_enabled


def test_quit_cleanup_uses_quit_recovery_even_after_the_room_owner_clears():
    for state in (ArtRoomState.CONNECTED, ArtRoomState.NONE):
        view = art_room_overview(
            state=state, hosting=False, quitting=True,
            presence=paint_along(),
        )
        assert view.phase == "cleanup_required"
        assert "Quit again" in view.connection_detail
        assert "Try Leave Room" not in view.connection_detail
        assert not view.activity_enabled
        assert not view.conversation_enabled
