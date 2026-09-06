"""Confirmed Art closure keeps the next room action tied to the exited role."""

from dataclasses import replace

import pytest

from core.session_conductor import (
    ArtRoomState,
    CleanupState,
    ExportState,
    ReviewState,
    SessionConductorFacts,
    SessionConductorPhase,
    SessionPrimaryAction,
    derive_session_conductor,
)


@pytest.mark.parametrize("profile", ["art", "music", "podcast_voice"])
@pytest.mark.parametrize("role", ["host", "guest"])
def test_confirmed_art_exit_offers_role_specific_room_entry(profile, role):
    facts = SessionConductorFacts(
        role=role, creator_profile_key=profile,
        art_room_closed=True,
    )
    result = derive_session_conductor(facts)
    assert result.phase is SessionConductorPhase.IDLE
    assert result.title == ("Room ended" if role == "host" else "Room left")
    assert result.primary_action is (
        SessionPrimaryAction.START_SESSION if role == "host"
        else SessionPrimaryAction.PASTE_NEW_INVITE
    )
    assert result.action_label == ("Start New Room" if role == "host" else "Paste New Invite")
    assert "invitation" in result.message
    assert "own tools" in result.message


@pytest.mark.parametrize("cleanup", [CleanupState.ENDING, CleanupState.FAILED, CleanupState.UNKNOWN])
def test_incomplete_cleanup_cannot_offer_art_reentry(cleanup):
    result = derive_session_conductor(SessionConductorFacts(
        role="guest", creator_profile_key="art", cleanup=cleanup,
        art_room_closed=True,
    ))
    assert result.phase is not SessionConductorPhase.ENDED
    assert result.primary_action not in {
        SessionPrimaryAction.START_SESSION, SessionPrimaryAction.PASTE_NEW_INVITE,
    }


def test_closed_room_receipt_does_not_replace_a_new_room_attempt():
    facts = SessionConductorFacts(role="guest", creator_profile_key="art", art_room_closed=True)
    assert derive_session_conductor(facts).phase is SessionConductorPhase.IDLE
    current = derive_session_conductor(replace(
        facts, setup_requested=True, art_room=ArtRoomState.CONNECTED,
    ))
    assert current.phase is SessionConductorPhase.CONNECTED
    assert current.primary_action is SessionPrimaryAction.NONE


def test_music_cleanup_without_art_receipt_keeps_its_existing_action():
    result = derive_session_conductor(SessionConductorFacts(
        role="guest", creator_profile_key="music", cleanup=CleanupState.COMPLETE,
    ))
    assert result.primary_action is SessionPrimaryAction.START_SESSION
    assert result.title == "Safe to end session"


@pytest.mark.parametrize("field,value,phase", [
    ("studio", ReviewState.REVIEWING, SessionConductorPhase.REVIEWING),
    ("export", ExportState.EXPORTING, SessionConductorPhase.EXPORTING),
])
def test_restored_music_studio_work_outranks_historical_art_exit(field, value, phase):
    result = derive_session_conductor(SessionConductorFacts(
        role="guest", creator_profile_key="music", art_room_closed=True,
        **{field: value},
    ))
    assert result.phase is phase
    assert result.primary_action is not SessionPrimaryAction.PASTE_NEW_INVITE
