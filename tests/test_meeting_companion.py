"""WebJam beside a free Webex window: two mutes, end is not end, one invite.

The load-bearing assertion in this file is
:func:`test_no_music_feature_is_gated_on_a_meeting_or_an_add_on`. A Webex
Embedded App needs a licensed organization and a Control Hub administrator,
which this product's musician does not have, so nothing in Music may depend on
one.
"""

from __future__ import annotations

import pytest

from core.meeting_companion import (
    DEFAULT_MEETING_SERVICE,
    build_invite_message,
    describe_mutes,
    end_session_prompt,
    meeting_departure_note,
    music_features_require_meeting,
)

JOIN_LINK = "webjam://join?v=2&host=192.168.1.5&port=22124&session=Tuesday+Jam"
MEETING_URL = "https://band.webex.com/meet/jeff"


# ----------------------------------------------------------------------
# No add-on gate
# ----------------------------------------------------------------------
def test_no_music_feature_is_gated_on_a_meeting_or_an_add_on():
    """Free and personal Webex users have no org and no Control Hub admin."""

    assert music_features_require_meeting() is False


def test_song_and_shared_track_features_work_with_no_meeting_configured():
    from core.song_workbench import SOURCE_PICKED_FILE, SongWorkbench, evaluate_upload
    from core.music_ai_catalog import resolve_song_tools
    from core.music_ai_client import MusicAIWorkflow

    workbench = SongWorkbench(notes="Key: G major\n[Verse]\nG D Em C\n")
    assert workbench.chord_advice().available
    assert workbench.writing_advice().available

    catalog = resolve_song_tools(
        [MusicAIWorkflow("1", "Stem Separation", "stems", "isolate")]
    )
    # No meeting is involved in the decision at all; the refusal below is
    # about the file, never about Webex.
    decision = evaluate_upload(
        capability=catalog.capability("stems"),
        source_kind=SOURCE_PICKED_FILE,
        path="",
        is_host=True,
        has_api_key=True,
    )
    assert "webex" not in decision.reason.lower()
    assert "meeting" not in decision.reason.lower()


# ----------------------------------------------------------------------
# Two mutes
# ----------------------------------------------------------------------
def test_both_mutes_are_shown_with_what_each_one_actually_does():
    surface = describe_mutes(
        webjam_muted_participants=1,
        participant_count=3,
        meeting_configured=True,
    )

    assert len(surface.controls) == 2
    assert surface.webjam.scope == "what you hear"
    assert surface.webjam.verifiable
    assert surface.meeting.scope == "your microphone in the meeting"
    assert not surface.meeting.verifiable
    assert "cannot read" in surface.meeting.state_text


def test_the_caution_says_neither_mute_stops_the_instrument():
    """Jamulus has no supported live-send mute; claiming otherwise is unsafe."""

    caution = describe_mutes(meeting_configured=True).caution()
    assert "two different mutes" in caution
    assert "Neither stops your instrument reaching the band" in caution


def test_with_no_meeting_only_webjams_mute_is_offered():
    surface = describe_mutes(meeting_configured=False)

    assert surface.meeting is None
    assert len(surface.controls) == 1
    assert "monitor mix only" in surface.caution()


def test_webjam_never_claims_to_change_the_meetings_mute():
    meeting = describe_mutes(meeting_configured=True).meeting
    assert "Open" in meeting.action_label
    assert "does not change or verify it" in meeting.hint


@pytest.mark.parametrize(
    ("muted", "total", "expected"),
    [
        (0, 3, "nobody muted in your mix"),
        (1, 3, "1 muted in your mix"),
        (3, 3, "everyone muted in your mix"),
        (0, 0, "nobody muted in your mix"),
    ],
)
def test_the_mix_mute_state_is_reported_exactly(muted, total, expected):
    surface = describe_mutes(
        webjam_muted_participants=muted, participant_count=total
    )
    assert surface.webjam.state_text == expected


def test_a_custom_meeting_service_name_is_used_throughout():
    surface = describe_mutes(meeting_configured=True, meeting_service="Zoom")
    assert surface.meeting.name == "Zoom"
    assert "Zoom" in surface.meeting.action_label
    assert "Zoom" in surface.caution()


# ----------------------------------------------------------------------
# End is not end
# ----------------------------------------------------------------------
@pytest.mark.parametrize("hosting", [True, False])
def test_ending_the_jam_says_the_meeting_stays_open(hosting):
    prompt = end_session_prompt(hosting=hosting, meeting_configured=True)

    assert "stays open" in prompt.meeting_note
    assert prompt.meeting_note in prompt.full_text()
    assert DEFAULT_MEETING_SERVICE in prompt.meeting_note


def test_with_no_meeting_configured_nothing_is_claimed_about_one():
    prompt = end_session_prompt(hosting=True, meeting_configured=False)

    assert prompt.meeting_note == ""
    assert prompt.full_text() == prompt.question
    assert "webex" not in prompt.full_text().lower()


def test_the_existing_host_and_guest_wording_is_preserved():
    assert end_session_prompt(hosting=True).title == "End Jam?"
    assert "End this jam for everyone?" in end_session_prompt(hosting=True).question
    assert end_session_prompt(hosting=False).title == "Leave Jam?"
    assert (
        "The host and other musicians will stay connected."
        in end_session_prompt(hosting=False).question
    )
    assert (
        "The host's recording will keep running."
        in end_session_prompt(hosting=False, recording_active=True).question
    )


def test_the_other_half_of_end_is_not_end_is_also_stated():
    note = meeting_departure_note()
    assert "does not end the jam" in note


# ----------------------------------------------------------------------
# One invite
# ----------------------------------------------------------------------
def test_one_invite_carries_the_jam_link_and_the_meeting_link():
    message = build_invite_message(
        join_link=JOIN_LINK, session_name="Tuesday Jam", meeting_url=MEETING_URL
    )

    assert message.includes_meeting
    assert JOIN_LINK in message.text
    assert MEETING_URL in message.text
    assert "band.webex.com" in message.text
    assert "WebJam does not run" in message.text
    assert "you do not need it to play" in message.text


def test_the_jam_link_is_passed_through_untouched():
    """The invitation protocol is not changed by the message around it."""

    message = build_invite_message(join_link=JOIN_LINK, meeting_url=MEETING_URL)
    assert JOIN_LINK in message.text.split()


@pytest.mark.parametrize(
    "meeting_url",
    [
        "",
        "   ",
        "https://evil.example.com/meet/jeff",
        "http://band.webex.com/meet/jeff",
        "javascript:alert(1)",
        "https://user:pw@band.webex.com/meet/jeff",
    ],
)
def test_an_invalid_meeting_link_is_dropped_not_pasted(meeting_url):
    message = build_invite_message(join_link=JOIN_LINK, meeting_url=meeting_url)

    assert not message.includes_meeting
    assert JOIN_LINK in message.text
    assert "evil.example.com" not in message.text
    assert "javascript" not in message.text


def test_an_invite_without_a_jam_link_is_a_programming_error():
    with pytest.raises(ValueError):
        build_invite_message(join_link="  ")


def test_the_invite_names_the_session_and_the_participant_noun():
    named = build_invite_message(join_link=JOIN_LINK, session_name="Tuesday Jam")
    assert named.text.startswith("Join Tuesday Jam on WebJam:")

    unnamed = build_invite_message(join_link=JOIN_LINK, participant_noun="speaker")
    assert unnamed.text.startswith("Join this jam on WebJam:")
    assert "as a speaker" in unnamed.text


def test_the_invite_stays_short_enough_to_paste_into_a_chat():
    message = build_invite_message(
        join_link=JOIN_LINK, session_name="Tuesday Jam", meeting_url=MEETING_URL
    )
    assert message.line_count <= 8
    assert len(message.text) < 500


# ----------------------------------------------------------------------
# Wiring: the live End/Leave dialog uses this copy
# ----------------------------------------------------------------------
def test_the_live_end_session_dialog_says_the_meeting_stays_open():
    """The prompt is not just available; the End action actually shows it."""

    from types import SimpleNamespace
    from unittest.mock import patch

    from PySide6.QtWidgets import QMessageBox

    from webjam_qt.controllers.audio_coordinator import AudioCoordinator

    coordinator = AudioCoordinator.__new__(AudioCoordinator)
    coordinator.stopping = False
    coordinator.cleanup_retry_required = False
    coordinator._c = SimpleNamespace(
        settings=SimpleNamespace(host_server_enabled=True, webex_url=MEETING_URL),
        recording=SimpleNamespace(is_recording_active=False, take_in_progress=False),
        window=SimpleNamespace(),
    )

    with patch.object(
        QMessageBox, "question", return_value=QMessageBox.StandardButton.No
    ) as question:
        coordinator.stop()

    title, body = question.call_args.args[1:3]
    assert title == "End Jam?"
    assert "End this jam for everyone?" in body
    assert "stays open" in body


def test_the_end_session_dialog_claims_nothing_when_no_meeting_is_set():
    from types import SimpleNamespace
    from unittest.mock import patch

    from PySide6.QtWidgets import QMessageBox

    from webjam_qt.controllers.audio_coordinator import AudioCoordinator

    coordinator = AudioCoordinator.__new__(AudioCoordinator)
    coordinator.stopping = False
    coordinator.cleanup_retry_required = False
    coordinator._c = SimpleNamespace(
        settings=SimpleNamespace(host_server_enabled=False, webex_url=""),
        recording=SimpleNamespace(is_recording_active=False, take_in_progress=False),
        window=SimpleNamespace(),
    )

    with patch.object(
        QMessageBox, "question", return_value=QMessageBox.StandardButton.No
    ) as question:
        coordinator.stop()

    title, body = question.call_args.args[1:3]
    assert title == "Leave Jam?"
    assert "webex" not in body.lower()
