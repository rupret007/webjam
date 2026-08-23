"""Exact in-room honesty goldens after #28.

Door law is unchanged and held by the start-UX suite. These goldens hold the
one leftover lie this pass closes: a written outline without a stated bar or
section is still not a place, but the room must not claim the song is absent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.room_clock import (
    NO_CLOCK_DETAIL,
    NO_CLOCK_HEADLINE,
    NO_PLACE_DETAIL,
    SONG_COUNT_LIMIT,
    SONG_DETAIL,
    SONG_WITH_TRACK,
    RoomClockFacts,
    RoomClockSource,
    render_room_clock,
    song_form_facts,
    stronger_facts,
)
from core.session_conductor import (
    EvidenceState,
    MusicPathState,
    ProcessState,
    SessionConductorFacts,
    SessionRole,
    derive_session_conductor,
)
from core.session_transfer import RoomClockSessionSnapshot, RoomClockSourceValue
from core.song_clock import SongClock
from core.song_form import parse_song_form


def _outline_timer(**changes) -> SimpleNamespace:
    values = dict(
        has_form=True,
        sections=("Verse", "Chorus"),
        follows_shared_track=True,
        running=True,
        position_s=8.5,
        bar=0,
        beat=0,
        section_label="",
        tempo_bpm=0,
        meter_numerator=0,
        meter_denominator=0,
        section_lengths_assumed=True,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def _stated_chorus(**changes) -> SimpleNamespace:
    values = dict(
        has_form=True,
        sections=("Verse", "Chorus"),
        follows_shared_track=True,
        running=True,
        position_s=24.0,
        bar=9,
        beat=2,
        section_label="Chorus",
        tempo_bpm=120.0,
        meter_numerator=0,
        meter_denominator=0,
        section_lengths_assumed=True,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def test_elapsed_only_shared_track_is_no_clock_golden():
    """A file playing is a timer. Painters must not ride it as form."""

    facts = song_form_facts(
        SimpleNamespace(
            has_form=False,
            sections=(),
            follows_shared_track=True,
            running=True,
            position_s=8.5,
            bar=0,
            beat=0,
            section_label="",
            tempo_bpm=0,
        )
    )
    view = render_room_clock(facts)

    assert facts is None
    assert view.headline == NO_CLOCK_HEADLINE
    assert view.detail == NO_CLOCK_DETAIL
    assert view.musical is False


def test_written_outline_without_a_position_is_named_not_ridden_golden():
    """[Verse] [Chorus] plus elapsed time is still not where we are."""

    facts = song_form_facts(_outline_timer())
    view = render_room_clock(facts)

    assert facts is not None
    assert facts.source is RoomClockSource.SONG_FORM
    assert facts.states_place is False
    assert facts.running is False
    assert facts.position_s == 0.0
    assert facts.form_shape == "Verse → Chorus"
    assert view.headline == NO_CLOCK_HEADLINE
    assert view.detail == f"Verse → Chorus is written. {NO_PLACE_DETAIL}"
    assert "Verse" not in view.headline
    assert "0:08" not in view.headline
    assert "0:08" not in view.detail
    assert view.musical is False
    assert view.present is True


def test_stated_bar_and_section_remain_form_golden():
    """When the owner stated a place, painters see that place — not elapsed."""

    facts = song_form_facts(_stated_chorus())
    view = render_room_clock(facts)

    assert facts is not None
    assert facts.source is RoomClockSource.SONG_FORM
    assert facts.bar == 9
    assert facts.section_label == "Chorus"
    assert facts.position_s == pytest.approx(24.0)
    assert view.headline == "Bar 9.2 · Chorus"
    assert "0:08" not in view.headline
    assert "0:24" not in view.headline
    assert SONG_DETAIL in view.detail
    assert SONG_WITH_TRACK in view.detail
    assert SONG_COUNT_LIMIT in view.detail
    assert "Verse → Chorus" in view.detail


def test_real_clock_outline_without_tempo_is_no_clock_golden():
    """The live SongClock must not dress Shared Track elapsed as Verse."""

    clock = SongClock()
    clock.set_form(parse_song_form("[Verse]\n[Chorus]\n"))
    clock.follow_shared_track(loaded=True, position_s=8.5, playing=True)
    snapshot = clock.snapshot()

    assert snapshot.has_form is True
    assert snapshot.follows_shared_track is True
    assert snapshot.bar == 0
    assert snapshot.section_label == ""
    facts = song_form_facts(snapshot)
    assert facts is not None
    assert facts.states_place is False
    assert facts.form_shape == "Verse → Chorus"
    view = render_room_clock(facts)
    assert view.headline == NO_CLOCK_HEADLINE
    assert "Verse" not in view.headline
    assert NO_PLACE_DETAIL in view.detail


def test_a_peer_outline_without_a_place_is_named_golden():
    """A guest must see the shape, not a silent room or a fake Verse."""

    parsed = RoomClockSessionSnapshot.from_mapping(
        {
            "schema": 1,
            "generation": 4,
            "source": RoomClockSourceValue.SONG_FORM.value,
            "running": False,
            "position_s": 0.0,
            "duration_s": 0.0,
            "bar": 0,
            "beat": 0,
            "section_label": "",
            "tempo_bpm": 0.0,
            "meter_numerator": 0,
            "meter_denominator": 0,
            "form_shape": "Verse → Chorus",
        }
    )
    view = render_room_clock(parsed)

    assert parsed.source is RoomClockSourceValue.SONG_FORM
    assert parsed.form_shape == "Verse → Chorus"
    assert view.headline == NO_CLOCK_HEADLINE
    assert view.detail == f"Verse → Chorus is written. {NO_PLACE_DETAIL}"
    assert "Verse" not in view.headline


def test_leftover_peer_timer_reads_as_no_clock_golden():
    """A #25-era elapsed-only song clock must not take the room down."""

    parsed = RoomClockSessionSnapshot.from_mapping(
        {
            "schema": 1,
            "generation": 2,
            "source": RoomClockSourceValue.SONG_FORM.value,
            "running": True,
            "position_s": 8.5,
            "duration_s": 0.0,
            "bar": 0,
            "beat": 0,
            "section_label": "",
            "tempo_bpm": 0.0,
            "meter_numerator": 0,
            "meter_denominator": 0,
        }
    )
    view = render_room_clock(parsed)

    assert parsed.source is RoomClockSourceValue.NONE
    assert view.headline == NO_CLOCK_HEADLINE
    assert view.detail == NO_CLOCK_DETAIL


def test_art_and_music_keep_one_next_step_goldens():
    """#27's next step stays one sentence. This pass does not rewrite feel."""

    art_host = derive_session_conductor(
        SessionConductorFacts(
            creator_profile_key="art",
            role=SessionRole.HOST,
            setup_requested=True,
            identity=EvidenceState.VERIFIED,
            sound=EvidenceState.VERIFIED,
            band_check=EvidenceState.VERIFIED,
            host_server_process=ProcessState.RUNNING,
            host_server_rpc=EvidenceState.VERIFIED,
            host_listener=EvidenceState.VERIFIED,
            invite=EvidenceState.VERIFIED,
            music_path=MusicPathState.AUTHENTICATED,
            local_participant=EvidenceState.VERIFIED,
        )
    )
    music_host = derive_session_conductor(
        SessionConductorFacts(
            creator_profile_key="music",
            role=SessionRole.HOST,
            setup_requested=True,
            identity=EvidenceState.VERIFIED,
            sound=EvidenceState.VERIFIED,
            band_check=EvidenceState.VERIFIED,
            host_server_process=ProcessState.RUNNING,
            host_server_rpc=EvidenceState.VERIFIED,
            host_listener=EvidenceState.VERIFIED,
            invite=EvidenceState.VERIFIED,
            music_path=MusicPathState.AUTHENTICATED,
            local_participant=EvidenceState.VERIFIED,
        )
    )
    assert art_host.message == "Copy the invite. That is the next step."
    assert music_host.message == "Copy the invite. That is the next step."
    spoken = " ".join(
        (art_host.title, art_host.message, music_host.title, music_host.message)
    ).casefold()
    assert "preview" not in spoken
    assert "drawpile" not in spoken
    assert "jamulus" not in spoken
    assert "send it when you want" not in spoken


def test_an_outline_without_a_place_does_not_outrank_a_video_golden():
    """A shape is not a where. Painters still ride the file if it is running."""

    outline = song_form_facts(_outline_timer())
    video = RoomClockFacts(
        source=RoomClockSource.REFERENCE_VIDEO,
        running=True,
        position_s=90.0,
        duration_s=600.0,
    )
    chosen = stronger_facts(outline, video)
    view = render_room_clock(chosen)

    assert chosen is video
    assert view.source is RoomClockSource.REFERENCE_VIDEO
    assert view.headline == "1:30 / 10:00"
    assert "Verse" not in view.headline
