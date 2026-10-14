"""Connection and closure evidence outrank optional Art room layers."""

from dataclasses import asdict, replace

import pytest

from core.art_companion import (
    ArtCompanionProjection,
    CanvasCompanionState,
    VideoCompanionState,
)
from core.art_room_activities import art_room_activities
from core.art_room_overview import ArtRoomOverview, art_room_overview
from core.meeting_companion import art_make_together_activity_detail
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


@pytest.mark.parametrize("state,hosting,named_connections,detail", [
    (ArtRoomState.WAITING, True, False,
     "Share an invitation; no artist is confirmed yet."),
    (ArtRoomState.CONNECTED, True, False,
     "An artist is here."),
    (ArtRoomState.CONNECTED, False, False,
     "You’re connected."),
    (ArtRoomState.CONNECTED, True, True,
     "Guest-chosen names. Talk and sharing are separate."),
])
@pytest.mark.parametrize("presence,secondary_presence", [
    (paint_along(), ABSENT), (canvas(), ABSENT),
    (ABSENT, paint_along()), (paint_along(), canvas()),
])
def test_presence_action_keeps_connection_detail_short_and_truthful(
    state, hosting, named_connections, detail, presence, secondary_presence,
):
    view = art_room_overview(
        state=state, hosting=hosting, named_connections=named_connections,
        presence=presence, secondary_presence=secondary_presence,
    )
    without_action = art_room_overview(
        state=state, hosting=hosting, named_connections=named_connections,
    )
    assert view.activity_actions
    assert view.connection_label == without_action.connection_label
    assert view.connection_detail == detail
    assert len(view.connection_detail) <= 55
    assert len(view.connection_detail) < len(without_action.connection_detail)


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


@pytest.mark.parametrize("hosting", [False, True])
def test_idle_overview_names_start_session_without_requiring_art_skill(hosting):
    """A person who just opened Art should see the next click, not a medium list."""

    overview = art_room_overview(state=ArtRoomState.NONE, hosting=hosting)
    assert overview.phase == "idle"
    assert overview.title == "Open the room, then make with what you have"
    assert "Start Session" in overview.connection_detail
    assert "paint, sculpt" not in overview.activity_detail.casefold()
    assert "3d print" not in overview.activity_detail.casefold()
    assert "You do not need an art app" in overview.activity_detail
    if hosting:
        assert "share its invitation" in overview.connection_detail
    else:
        assert "paste the host's invitation" in overview.connection_detail


def test_waiting_host_without_meeting_does_not_ask_to_set_up_conversation():
    """HUD Copy Invite is the one waiting-room primary; overview stays own-space."""

    waiting = art_room_overview(
        state=ArtRoomState.WAITING, hosting=True, conversation_configured=False,
    )
    own_space = (
        "You do not need an art app. Use paper, your usual tools, or just talk."
    )
    assert waiting.phase == "waiting"
    assert waiting.activity_detail == own_space
    assert "Conversation" not in waiting.activity_detail
    assert "meeting link" not in waiting.activity_detail
    assert waiting.conversation_action_label == "Set Up Conversation"
    assert waiting.conversation_enabled
    assert not waiting.activity_action
    assert not waiting.activity_enabled


def test_unconfigured_non_waiting_rooms_still_ask_to_set_up_conversation():
    """Connected, failed, and waiting-guest rooms keep the current setup ask."""

    setup_ask = "Choose Set Up Conversation to add your meeting link."
    cases = (
        (ArtRoomState.CONNECTED, True),
        (ArtRoomState.CONNECTED, False),
        (ArtRoomState.FAILED, True),
        (ArtRoomState.FAILED, False),
        (ArtRoomState.WAITING, False),
    )
    for state, hosting in cases:
        overview = art_room_overview(
            state=state, hosting=hosting, conversation_configured=False,
        )
        assert setup_ask in overview.activity_detail, (state, hosting)
        assert overview.conversation_action_label == "Set Up Conversation"


def test_make_together_is_complete_without_optional_tools_or_a_roster():
    waiting = art_room_overview(state=ArtRoomState.WAITING, hosting=True)
    joined = art_room_overview(state=ArtRoomState.CONNECTED, hosting=False)
    assert waiting.phase == "waiting"
    assert joined.phase == "connected"
    assert "Waiting for artists" in waiting.connection_label
    assert joined.connection_label == "Connected to the host"
    assert waiting.activity_detail == art_make_together_activity_detail(
        waiting_host=True,
    )
    assert "Conversation" not in waiting.activity_detail
    assert joined.activity_detail == art_make_together_activity_detail()
    assert "Choose Set Up Conversation" in joined.activity_detail
    for overview in (waiting, joined):
        assert overview.title == "Make from your own space"
        assert overview.activity_label == "Make from your own space"
        assert "You do not need an art app" in overview.activity_detail
        assert "your usual tools, or just talk" in overview.activity_detail
        # Full Webex / movie essay stays on Conversation — not overview.
        assert "Join / Open Meeting or Show Webex App" not in overview.activity_detail
        assert "WebJam does not play the movie." not in overview.activity_detail
        assert "screen share" not in overview.activity_detail
        assert overview.conversation_enabled
        assert not overview.activity_action
        public = repr(asdict(overview)).lower()
        assert "0 artists" not in public
        assert "band check" not in public
        assert "shared track" not in public
        assert "jamulus" not in public
        assert "mixer" not in public
        assert "setup required" not in public
        assert "open canvas" not in public


@pytest.mark.parametrize("hosting", [False, True])
def test_connected_detail_is_short_and_moves_the_roster_caveat_to_the_note(hosting):
    overview = art_room_overview(state=ArtRoomState.CONNECTED, hosting=hosting)
    assert "does not yet" not in overview.connection_detail
    assert "full artist list" not in overview.connection_detail
    assert overview.connection_detail == (
        "An artist is here with you." if hosting else "You’re connected and ready to make."
    )
    assert overview.connection_note == "WebJam does not yet show a full artist list."

    # Once a named roster is actually shown, the caveat no longer applies.
    named = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=True, named_connections=True,
    )
    assert named.connection_note == ""


def test_guest_paint_along_needs_file_names_open_my_copy_button():
    """Room overview must name the same next click as Paint along's panel button.

    Presence used to say "Open your Paint along copy" while the overview button
    said "Open Paint along" and the panel said "Open my copy…". One named click.
    """

    presence = paint_along()
    overview = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=False, presence=presence,
    )
    assert presence.label == "Open my copy…"
    assert presence.next_click == "Open my copy…"
    assert overview.activity_enabled
    assert overview.activity_action == "video"
    assert overview.activity_action_label == "Open my copy…"
    assert overview.activity_label == "Open my copy…"
    assert overview.activity_detail == (
        "Open your own copy of the same video to follow along."
    )
    assert overview.open_my_copy_recovery
    assert not overview.making_detail
    assert overview.conversation_enabled


def test_guest_needs_file_keeps_making_detail_when_a_canvas_is_also_offered():
    """W08 omits making-detail only when Open my copy… is the one next line."""

    overview = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=False,
        presence=paint_along(), secondary_presence=canvas(),
    )
    assert overview.open_my_copy_recovery
    assert overview.activity_actions == ("video", "canvas")
    assert overview.making_detail.startswith("You do not need an art app")
    assert "your usual tools, or just talk" in overview.making_detail
    assert "Shared activities are optional." in overview.making_detail


@pytest.mark.parametrize("canvas_state", [
    CanvasCompanionState.MISSING_APP,
    CanvasCompanionState.UNREADABLE,
    CanvasCompanionState.READY,
])
def test_guest_needs_file_owns_emphasis_when_canvas_is_also_offered(canvas_state):
    """NEEDS_FILE stays the Room primary even when canvas attention ranked first."""

    activities = art_room_activities(ArtCompanionProjection(
        in_room=True, canvas=canvas_state, video=VideoCompanionState.NEEDS_FILE,
    ))
    overview = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=False,
        presence=activities[0],
        secondary_presence=activities[1] if len(activities) > 1 else ABSENT,
    )
    assert overview.open_my_copy_recovery
    assert overview.activity_action == "video"
    assert overview.activity_label == "Open my copy…"
    assert overview.activity_action_label == "Open my copy…"
    assert overview.activity_detail == (
        "Open your own copy of the same video to follow along."
    )
    assert overview.secondary_activity_action == "canvas"
    assert overview.secondary_activity_action_label == "Open workspace"
    assert overview.activity_actions == ("video", "canvas")
    assert overview.making_detail.startswith("You do not need an art app")
    assert "your usual tools, or just talk" in overview.making_detail
    assert overview.conversation_enabled


def test_mismatched_copy_is_not_the_needs_file_open_my_copy_emphasis():
    presence = art_room_presence(ArtCompanionProjection(
        in_room=True, video=VideoCompanionState.MISMATCHED_FILE,
    ))
    overview = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=False, presence=presence,
    )
    assert not overview.open_my_copy_recovery
    assert overview.activity_action_label == "Open my copy…"
    assert overview.activity_label == "Paint along needs a look"
    assert overview.making_detail.startswith("You do not need an art app")
    assert "your usual tools, or just talk" in overview.making_detail


@pytest.mark.parametrize("hosting", [False, True])
def test_conversation_button_names_its_next_click_from_the_saved_meeting(hosting):
    """No saved link yet -> the ghost/footer button still names Set Up
    Conversation. Only a waiting host drops the activity-line setup ask
    so HUD Copy Invite stays the one primary. Once a link is saved the
    button is the way back to the panel. The state never disables it."""

    for state in (ArtRoomState.WAITING, ArtRoomState.CONNECTED, ArtRoomState.FAILED):
        needs_setup = art_room_overview(
            state=state, hosting=hosting, conversation_configured=False,
        )
        ready = art_room_overview(
            state=state, hosting=hosting, conversation_configured=True,
        )
        waiting_host = state is ArtRoomState.WAITING and hosting
        if waiting_host:
            assert "Conversation" not in needs_setup.activity_detail
            assert "meeting link" not in needs_setup.activity_detail
        else:
            assert f"Choose {needs_setup.conversation_action_label}" in needs_setup.activity_detail
            assert "add your meeting link" in needs_setup.activity_detail
        assert f"Choose {ready.conversation_action_label}" in ready.activity_detail
        assert "Set Up Conversation" not in ready.activity_detail
        assert needs_setup.conversation_action_label == "Set Up Conversation"
        assert ready.conversation_action_label == "Conversation"


    # A closing or quitting room still names the click honestly; the button
    # is disabled by conversation_enabled, not relabelled.
    closing = art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=hosting, stopping=True,
        conversation_configured=False,
    )
    assert closing.conversation_action_label == "Set Up Conversation"
    assert not closing.conversation_enabled


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
    recovery, other = (
        (primary, secondary) if primary.target is ArtPresenceTarget.VIDEO
        else (secondary, primary)
    )
    assert overview.activity_label == recovery.label
    assert overview.activity_detail == recovery.description
    assert overview.secondary_activity_label == other.label
    assert overview.secondary_activity_detail == other.description
    assert overview.activity_action_label == "Open my copy…"
    assert overview.secondary_activity_action_label == "Open workspace"
    assert overview.activity_actions == ("video", "canvas")
    assert overview.open_my_copy_recovery
    assert overview.conversation_enabled


@pytest.mark.parametrize("hosting", [False, True])
@pytest.mark.parametrize("canvas_state,video_state,expect_making_detail", [
    (CanvasCompanionState.READY, VideoCompanionState.NONE, True),
    (CanvasCompanionState.MISSING_APP, VideoCompanionState.NONE, True),
    (CanvasCompanionState.NONE, VideoCompanionState.NEEDS_FILE, False),
    (CanvasCompanionState.NONE, VideoCompanionState.PLAYING, True),
])
def test_optional_activity_keeps_an_any_artist_next_step(
    hosting, canvas_state, video_state, expect_making_detail,
):
    presence = art_room_presence(ArtCompanionProjection(
        in_room=True, transport_allowed=hosting, canvas=canvas_state, video=video_state,
    ), hosting=hosting)
    for state in (ArtRoomState.WAITING, ArtRoomState.CONNECTED):
        overview = art_room_overview(state=state, hosting=hosting, presence=presence)
        if state is ArtRoomState.WAITING and not hosting:
            assert not overview.making_detail
            continue
        if expect_making_detail:
            assert overview.making_detail.startswith("You do not need an art app")
            assert "your usual tools, or just talk" in overview.making_detail
            assert "Shared activities are optional." in overview.making_detail
        else:
            assert not overview.making_detail
            assert overview.open_my_copy_recovery
        assert overview.activity_actions == (presence.target.value,)
        assert overview.activity_detail == presence.description

    for changes in (
        dict(state=ArtRoomState.FAILED), dict(state=ArtRoomState.RECONNECTING),
        dict(state=ArtRoomState.CONNECTED, stopping=True),
        dict(state=ArtRoomState.CONNECTED, cleanup_required=True),
        dict(state=ArtRoomState.CONNECTED, quitting=True),
        dict(state=ArtRoomState.CONNECTED, ended=True),
    ):
        overview = art_room_overview(hosting=hosting, presence=presence, **changes)
        assert not overview.making_detail
        assert not overview.activity_actions


def test_own_tools_room_does_not_repeat_its_existing_instruction():
    overview = art_room_overview(state=ArtRoomState.CONNECTED, hosting=False)
    assert "You do not need an art app" in overview.activity_detail
    assert "your usual tools, or just talk" in overview.activity_detail
    assert not overview.making_detail


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
    assert overview.activity_label == "Make from your own space"
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
