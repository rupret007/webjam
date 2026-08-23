"""One pulse the whole room can read, and the line it must not cross.

The room clock is what lets a band playing, a painter on the shared canvas, and
someone following a reference be in the same moment. These tests hold it to
being exactly that and nothing more: it renders what an owner published, it
extrapolates only elapsed time, and it can never turn a file offset into a bar.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core.reference_video import ReferenceVideoSnapshot, ReferenceVideoState
from core.room_clock import (
    DEFAULT_STALE_AFTER_S,
    NO_CLOCK_DETAIL,
    NO_CLOCK_HEADLINE,
    NO_PLACE_DETAIL,
    SONG_ASSUMED,
    SONG_COUNT_LIMIT,
    SONG_DETAIL,
    SONG_WITH_TRACK,
    STALE_DETAIL,
    VIDEO_DETAIL,
    RoomClockFacts,
    RoomClockProjection,
    RoomClockSource,
    format_clock,
    reference_video_facts,
    render_room_clock,
    song_form_facts,
    stronger_facts,
)
from core.session_transfer import (
    RecordingSignal,
    RoomClockSessionSnapshot,
    RoomClockSourceValue,
    SessionControlState,
    SessionStateSnapshot,
)
from core.session_transfer_runtime import HostPeerSession


def _song(**changes) -> RoomClockSessionSnapshot:
    values = {
        "source": RoomClockSourceValue.SONG_FORM,
        "running": True,
        "bar": 17,
        "beat": 3,
        "section_label": "Chorus",
        "tempo_bpm": 124.0,
        "meter_numerator": 4,
        "meter_denominator": 4,
    }
    values.update(changes)
    return RoomClockSessionSnapshot(**values)


def _video(**changes) -> RoomClockSessionSnapshot:
    values = {
        "source": RoomClockSourceValue.REFERENCE_VIDEO,
        "running": True,
        "position_s": 134.0,
        "duration_s": 330.0,
    }
    values.update(changes)
    return RoomClockSessionSnapshot(**values)


# ---------------------------------------------------------------------------
# The line this must not cross
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "honesty",
    [
        {"follows_shared_track": True},
        {"section_lengths_assumed": True},
        {"form_shape": "Verse → Chorus"},
    ],
)
def test_a_reference_video_clock_can_never_carry_song_honesty(honesty: dict):
    with pytest.raises(ValueError, match="not a bar"):
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.REFERENCE_VIDEO,
            running=True,
            position_s=10.0,
            **honesty,
        )


@pytest.mark.parametrize(
    "musical",
    [
        {"bar": 17},
        {"beat": 2},
        {"section_label": "Chorus"},
        {"tempo_bpm": 124.0},
        {"meter_numerator": 4, "meter_denominator": 4},
    ],
)
def test_a_reference_video_clock_can_never_state_a_musical_position(musical: dict):
    """A file offset is not a bar, and the wire refuses the combination.

    Making this a schema rule rather than a convention means no future caller
    can quietly turn Art into a fake music engine by passing one extra keyword.
    """

    with pytest.raises(ValueError, match="not a bar"):
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.REFERENCE_VIDEO,
            running=True,
            position_s=10.0,
            **musical,
        )


def test_a_video_clock_never_renders_a_bar_number():
    view = render_room_clock(_video(), age_s=0.0)

    assert view.musical is False
    assert "Bar" not in view.headline
    assert view.detail == VIDEO_DETAIL


def test_only_elapsed_time_is_ever_extrapolated():
    """A running video advances by locally measured age; a bar never does."""

    still = render_room_clock(_video(position_s=100.0), age_s=0.0)
    later = render_room_clock(_video(position_s=100.0), age_s=4.0)
    assert still.headline == "1:40 / 5:30"
    assert later.headline == "1:44 / 5:30"

    song_now = render_room_clock(_song(bar=17), age_s=0.0)
    song_later = render_room_clock(_song(bar=17), age_s=4.0)
    assert song_now.headline == song_later.headline == "Bar 17.3 · Chorus"


def test_a_paused_clock_does_not_advance():
    view = render_room_clock(_video(running=False, position_s=100.0), age_s=9.0)

    assert view.headline == "1:40 / 5:30"
    assert view.running is False
    assert "Holding" in view.detail


def test_extrapolation_never_runs_past_the_end():
    """Advancing by age is clamped to the duration the owner published."""

    view = render_room_clock(_video(position_s=328.0), age_s=4.0)

    assert view.stale is False
    assert view.headline == "5:30 / 5:30"


# ---------------------------------------------------------------------------
# Rendering what an owner said
# ---------------------------------------------------------------------------


def test_no_clock_is_a_first_class_answer():
    for projection in (None, RoomClockSessionSnapshot()):
        view = render_room_clock(projection)

        assert view.source is RoomClockSource.NONE
        assert view.present is False
        assert view.headline == NO_CLOCK_HEADLINE
        assert view.detail == NO_CLOCK_DETAIL


def test_song_form_facts_translates_a_stated_form():
    facts = song_form_facts(
        SimpleNamespace(
            has_form=True,
            sections=("Verse", "Chorus"),
            follows_shared_track=False,
            running=True,
            position_s=12.0,
            bar=17,
            beat=3,
            section_label="Chorus",
            tempo_bpm=124.0,
            meter_numerator=4,
            meter_denominator=4,
            section_lengths_assumed=True,
        )
    )

    assert facts is not None
    assert facts.source is RoomClockSource.SONG_FORM
    assert facts.bar == 17
    assert facts.beat == 3
    assert facts.section_label == "Chorus"
    assert facts.tempo_bpm == pytest.approx(124.0)
    assert facts.meter_numerator == 4
    assert facts.meter_denominator == 4
    assert facts.section_lengths_assumed is True
    assert facts.form_shape == "Verse → Chorus"


def test_song_form_facts_ignore_an_empty_clock():
    assert song_form_facts(None) is None
    assert (
        song_form_facts(
            SimpleNamespace(
                has_form=False, sections=(), follows_shared_track=False
            )
        )
        is None
    )


def test_song_form_facts_ignore_a_shared_track_without_a_written_form():
    """A file playing is not a song form. Painters must not ride a timer."""

    assert (
        song_form_facts(
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
        is None
    )


def test_song_form_facts_do_not_invent_a_meter_from_the_count_default():
    """beats_per_bar=4 is how the clock counts, not a 4/4 the room wrote."""

    facts = song_form_facts(
        SimpleNamespace(
            has_form=True,
            sections=("Verse",),
            follows_shared_track=False,
            running=True,
            bar=1,
            beat=1,
            section_label="Verse",
            beats_per_bar=4,
            meter_numerator=0,
            meter_denominator=0,
        )
    )

    assert facts is not None
    assert facts.section_label == "Verse"
    assert facts.bar == 1
    assert facts.meter_numerator == 0
    assert facts.meter_denominator == 0


def test_song_form_facts_do_not_invent_the_first_part_as_the_position():
    """A written outline without a stated bar or section is not a position."""

    facts = song_form_facts(
        SimpleNamespace(
            has_form=True,
            sections=("Verse", "Chorus"),
            follows_shared_track=True,
            running=True,
            position_s=8.5,
            bar=0,
            beat=0,
            section_label="",
            tempo_bpm=0,
        )
    )

    assert facts is not None
    assert facts.states_place is False
    assert facts.section_label == ""
    assert facts.bar == 0
    assert facts.running is False
    assert facts.position_s == 0.0
    assert facts.form_shape == "Verse → Chorus"


def test_a_song_form_clock_reads_as_music():
    view = render_room_clock(_song(), age_s=0.0)

    assert view.source is RoomClockSource.SONG_FORM
    assert view.musical is True
    assert view.headline == "Bar 17.3 · Chorus"
    assert SONG_DETAIL in view.detail
    assert SONG_COUNT_LIMIT in view.detail
    assert "124 BPM" in view.detail
    assert "4/4" in view.detail


def test_a_song_form_clock_states_only_what_it_was_given():
    assert render_room_clock(_song(beat=0, section_label="")).headline == "Bar 17"
    assert (
        render_room_clock(_song(bar=0, beat=0, section_label="Bridge")).headline
        == "Bridge"
    )
    bare = render_room_clock(
        _song(bar=0, beat=0, section_label="Verse", tempo_bpm=0.0,
              meter_numerator=0, meter_denominator=0)
    )
    assert SONG_DETAIL in bare.detail
    assert SONG_COUNT_LIMIT in bare.detail


def test_a_painter_sees_the_same_form_honesty_musicians_already_see():
    view = render_room_clock(
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.SONG_FORM,
            running=True,
            bar=3,
            beat=1,
            section_label="Chorus",
            follows_shared_track=True,
            section_lengths_assumed=True,
            form_shape="Verse → Chorus",
        )
    )

    assert view.headline == "Bar 3.1 · Chorus"
    assert SONG_DETAIL in view.detail
    assert "Verse → Chorus" in view.detail
    assert SONG_WITH_TRACK in view.detail
    assert SONG_ASSUMED in view.detail
    assert SONG_COUNT_LIMIT in view.detail


def test_a_written_form_publishes_through_the_real_peer_snapshot(tmp_path):
    """#24's publish used beats_per_bar as 4/0 and the wire refused it."""

    from core.song_clock import FormSection, SongClockSnapshot

    snapshot = SongClockSnapshot(
        state="running",
        position_s=12.0,
        section_label="Chorus",
        bar=9,
        beat=2,
        tempo_bpm=120.0,
        beats_per_bar=4,
        sections=(
            FormSection("Verse", "verse", 8, True),
            FormSection("Chorus", "chorus", 8, False),
        ),
        section_lengths_assumed=True,
        position_source="shared_track",
    )
    facts = song_form_facts(snapshot)
    assert facts is not None
    assert facts.meter_numerator == 0
    assert facts.follows_shared_track is True

    control = SessionControlState(
        tmp_path, str(uuid.uuid4()), creator_profile_key="music"
    )
    published = control.publish_room_clock(
        source=facts.source.value,
        running=facts.running,
        position_s=facts.position_s,
        bar=facts.bar,
        beat=facts.beat,
        section_label=facts.section_label,
        tempo_bpm=facts.tempo_bpm,
        meter_numerator=facts.meter_numerator,
        meter_denominator=facts.meter_denominator,
        follows_shared_track=facts.follows_shared_track,
        section_lengths_assumed=facts.section_lengths_assumed,
        form_shape=facts.form_shape,
    )

    assert published.source is RoomClockSourceValue.SONG_FORM
    assert published.bar == 9
    assert published.section_label == "Chorus"
    assert published.follows_shared_track is True
    assert published.section_lengths_assumed is True
    assert published.form_shape == "Verse → Chorus"
    assert "4/4" not in render_room_clock(published).detail


def test_a_legacy_song_payload_without_honesty_keys_still_parses():
    payload = _song().to_mapping()
    payload.pop("follows_shared_track", None)
    payload.pop("section_lengths_assumed", None)
    payload.pop("form_shape", None)

    parsed = RoomClockSessionSnapshot.from_mapping(payload)

    assert parsed.bar == 17
    assert parsed.follows_shared_track is False
    assert parsed.form_shape == ""


def test_a_beat_never_appears_without_its_bar():
    """A beat alone is meaningless, so the schema refuses it outright."""

    with pytest.raises(ValueError):
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.SONG_FORM, beat=3
        )


def test_a_song_form_clock_without_written_parts_is_refused():
    """Elapsed time alone is a timer. Painters must not ride it as form."""

    with pytest.raises(ValueError, match="written bar or section"):
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.SONG_FORM,
            running=True,
            position_s=8.5,
        )
    with pytest.raises(ValueError, match="written bar or section"):
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.SONG_FORM,
            tempo_bpm=120.0,
        )
    with pytest.raises(ValueError, match="written bar or section"):
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.SONG_FORM,
            running=True,
            position_s=8.5,
            form_shape="Verse → Chorus",
        )


def test_a_written_outline_without_a_place_may_travel_as_honesty():
    """The shape may be named. It must not look like a running clock."""

    snapshot = RoomClockSessionSnapshot(
        source=RoomClockSourceValue.SONG_FORM,
        form_shape="Verse → Chorus",
    )
    view = render_room_clock(snapshot)

    assert snapshot.running is False
    assert snapshot.position_s == 0.0
    assert snapshot.bar == 0
    assert snapshot.section_label == ""
    assert snapshot.tempo_bpm == 0.0
    assert view.headline == NO_CLOCK_HEADLINE
    assert view.detail == f"Verse → Chorus is written. {NO_PLACE_DETAIL}"
    assert view.musical is False
    assert "Verse" not in view.headline


def test_a_leftover_peer_outline_with_a_timer_keeps_the_shape():
    """Transfer must not erase #29's named outline just to drop a leftover timer."""

    parsed = RoomClockSessionSnapshot.from_mapping(
        {
            "schema": 1,
            "generation": 5,
            "source": RoomClockSourceValue.SONG_FORM.value,
            "running": True,
            "position_s": 8.5,
            "duration_s": 0.0,
            "bar": 0,
            "beat": 0,
            "section_label": "",
            "tempo_bpm": 120.0,
            "meter_numerator": 0,
            "meter_denominator": 0,
            "follows_shared_track": True,
            "section_lengths_assumed": True,
            "form_shape": "Verse → Chorus",
        }
    )
    view = render_room_clock(parsed)

    assert parsed.source is RoomClockSourceValue.SONG_FORM
    assert parsed.form_shape == "Verse → Chorus"
    assert parsed.running is False
    assert parsed.position_s == 0.0
    assert parsed.bar == 0
    assert parsed.section_label == ""
    assert parsed.tempo_bpm == 0.0
    assert parsed.follows_shared_track is False
    assert view.headline == NO_CLOCK_HEADLINE
    assert view.detail == f"Verse → Chorus is written. {NO_PLACE_DETAIL}"
    assert view.musical is False
    assert "Verse" not in view.headline
    assert "0:08" not in view.headline


def test_a_session_state_transfer_keeps_form_shape_honesty():
    """A guest reading session state must not invent a place or drop the shape."""

    payload = {
        "session_id": str(uuid.uuid4()),
        "generation": 3,
        "signal": RecordingSignal.IDLE.value,
        "room_clock": {
            "schema": 1,
            "generation": 6,
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
            "form_shape": "Verse → Chorus",
        },
    }
    state = SessionStateSnapshot(
        session_id=payload["session_id"],
        generation=payload["generation"],
        signal=RecordingSignal.IDLE,
        room_clock=payload["room_clock"],
    )
    view = render_room_clock(state.room_clock)

    assert state.room_clock.source is RoomClockSourceValue.SONG_FORM
    assert state.room_clock.form_shape == "Verse → Chorus"
    assert state.room_clock.running is False
    assert state.room_clock.position_s == 0.0
    assert view.headline == NO_CLOCK_HEADLINE
    assert "Verse" not in view.headline


def test_a_peer_elapsed_only_song_clock_reads_as_no_clock():
    """An older host that published a timer must not knock the guest offline."""

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

    assert parsed.source is RoomClockSourceValue.NONE
    assert parsed.running is False
    assert parsed.position_s == 0.0


def test_a_lost_owner_stops_the_clock_instead_of_drifting():
    view = render_room_clock(
        _video(position_s=100.0), age_s=DEFAULT_STALE_AFTER_S + 1.0
    )

    assert view.stale is True
    assert view.running is False
    assert view.detail == STALE_DETAIL
    # The position it stopped at is still shown, un-advanced.
    assert view.headline == "1:40 / 5:30"


def test_a_paused_clock_is_never_stale():
    view = render_room_clock(_video(running=False), age_s=600.0)

    assert view.stale is False


def test_an_unknown_source_reads_as_no_clock():
    """The projection came from another computer, so it is not trusted."""

    class Alien:
        source = "quantum_metronome"
        running = True
        position_s = 10.0
        duration_s = 20.0
        bar = 99
        beat = 1
        section_label = "?"
        tempo_bpm = 120.0
        meter_numerator = 4
        meter_denominator = 4

    assert render_room_clock(Alien()).present is False


def test_a_projection_missing_members_reads_as_no_clock():
    assert render_room_clock(object()).present is False


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0:00"),
        (9, "0:09"),
        (61.9, "1:01"),
        (3600, "1:00:00"),
        (-5, "0:00"),
        (float("nan"), "0:00"),
        ("not a number", "0:00"),
        (None, "0:00"),
    ],
)
def test_the_clock_readout_is_bounded_and_never_negative(seconds, expected):
    assert format_clock(seconds) == expected


# ---------------------------------------------------------------------------
# Who speaks for the room
# ---------------------------------------------------------------------------


def test_a_song_in_the_room_outranks_a_reference_video():
    """A painter riding a band should be riding bars, not a file offset."""

    song = RoomClockFacts(source=RoomClockSource.SONG_FORM, running=True, bar=5)
    video = RoomClockFacts(
        source=RoomClockSource.REFERENCE_VIDEO, running=True, position_s=90.0
    )

    assert stronger_facts(song, video) is song
    assert stronger_facts(None, video) is video
    assert stronger_facts(song, None) is song
    assert stronger_facts(None, None).source is RoomClockSource.NONE


def test_an_outline_without_a_place_does_not_outrank_a_reference_video():
    """A shape is not a where. The file offset still speaks."""

    outline = RoomClockFacts(
        source=RoomClockSource.SONG_FORM, form_shape="Verse → Chorus"
    )
    video = RoomClockFacts(
        source=RoomClockSource.REFERENCE_VIDEO, running=True, position_s=90.0
    )

    assert stronger_facts(outline, video) is video
    assert stronger_facts(outline, None) is outline


def test_the_video_translation_carries_no_musical_position():
    snapshot = ReferenceVideoSnapshot(
        state=ReferenceVideoState.PLAYING,
        shared=True,
        source_display_name="lesson.mp4",
        identity_digest="a" * 64,
        position_s=42.0,
        duration_s=600.0,
    )

    facts = reference_video_facts(
        snapshot, playing_state=ReferenceVideoState.PLAYING
    )

    assert facts.source is RoomClockSource.REFERENCE_VIDEO
    assert facts.running is True
    assert facts.position_s == pytest.approx(42.0)
    assert facts.states_music is False
    assert (facts.bar, facts.beat, facts.section_label) == (0, 0, "")


def test_a_video_nobody_is_sharing_owns_no_pulse():
    assert reference_video_facts(None) is None
    assert reference_video_facts(ReferenceVideoSnapshot()) is None


def test_a_paused_video_still_owns_the_pulse_but_is_not_running():
    snapshot = ReferenceVideoSnapshot(
        state=ReferenceVideoState.PAUSED,
        shared=True,
        source_display_name="lesson.mp4",
        identity_digest="a" * 64,
        position_s=42.0,
        duration_s=600.0,
    )

    facts = reference_video_facts(
        snapshot, playing_state=ReferenceVideoState.PLAYING
    )

    assert facts.source is RoomClockSource.REFERENCE_VIDEO
    assert facts.running is False


# ---------------------------------------------------------------------------
# The wire, and the seam a music surface publishes into
# ---------------------------------------------------------------------------


def test_the_projection_round_trips_and_satisfies_the_domain_protocol():
    song = _song(generation=4)

    assert RoomClockSessionSnapshot.from_mapping(song.to_mapping()) == song
    assert isinstance(song, RoomClockProjection)
    # It says where the room is, never who may move it.
    assert not hasattr(song, "can_control")


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 2, "generation": 0, "source": "none"},
        {"generation": 0, "source": "none"},
        "not an object",
        11,
    ],
)
def test_an_incomplete_or_unknown_schema_payload_is_refused(payload: object):
    with pytest.raises(ValueError):
        RoomClockSessionSnapshot.from_mapping(payload)


def test_an_absent_clock_cannot_expose_a_position():
    with pytest.raises(ValueError, match="cannot expose a position"):
        RoomClockSessionSnapshot(
            source=RoomClockSourceValue.NONE, position_s=5.0
        )
    with pytest.raises(ValueError, match="cannot expose a position"):
        RoomClockSessionSnapshot(source=RoomClockSourceValue.NONE, running=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"bar": -1},
        {"bar": 10**9},
        {"beat": 500},
        {"tempo_bpm": 5.0},
        {"tempo_bpm": 900.0},
        {"section_label": "x" * 200},
        {"section_label": "line\nbreak"},
        {"meter_numerator": 4, "meter_denominator": 0},
        {"generation": -1},
        {"running": "yes"},
    ],
)
def test_an_unbounded_or_contradictory_clock_is_refused(changes: dict):
    with pytest.raises(ValueError):
        _song(**changes)


def test_a_legacy_snapshot_without_a_clock_defaults_to_none():
    assert RoomClockSessionSnapshot.from_mapping(None) == RoomClockSessionSnapshot()
    state = SessionStateSnapshot(
        session_id=str(uuid.uuid4()),
        generation=1,
        signal=RecordingSignal.IDLE,
    )
    assert state.room_clock.source is RoomClockSourceValue.NONE


def test_the_clock_is_not_gated_on_any_creator_profile(tmp_path):
    """A music surface must be able to publish this without becoming Art."""

    for profile_key in ("music", "podcast_voice", "review_rehearsal", "art"):
        control = SessionControlState(
            tmp_path / profile_key,
            str(uuid.uuid4()),
            creator_profile_key=profile_key,
        )
        published = control.publish_room_clock(
            source=RoomClockSourceValue.SONG_FORM,
            running=True,
            bar=9,
            section_label="Verse",
        )

        assert published.bar == 9
        assert control.snapshot().room_clock == published


def test_publication_is_idempotent_and_advances_only_on_real_change(tmp_path):
    control = SessionControlState(tmp_path, str(uuid.uuid4()), creator_profile_key="art")

    first = control.publish_room_clock(
        source=RoomClockSourceValue.SONG_FORM, running=True, bar=1
    )
    again = control.publish_room_clock(
        source=RoomClockSourceValue.SONG_FORM, running=True, bar=1
    )
    moved = control.publish_room_clock(
        source=RoomClockSourceValue.SONG_FORM, running=True, bar=2
    )

    assert first.generation == 1
    assert again is first
    assert moved.generation == 2


def test_the_clock_never_reaches_the_durable_journal(tmp_path):
    """A restarted host has no pulse until its owner republishes."""

    control = SessionControlState(tmp_path, str(uuid.uuid4()), creator_profile_key="art")
    control.publish_room_clock(
        source=RoomClockSourceValue.SONG_FORM, running=True, bar=17,
        section_label="Chorus",
    )
    control.begin(str(uuid.uuid4()), started_utc="2026-08-21T00:00:00Z")

    written = control.path.read_text(encoding="utf-8")

    assert "room_clock" not in written
    assert "Chorus" not in written


def test_a_host_runtime_publishes_only_once_a_session_owns_control(tmp_path):
    session = HostPeerSession()

    assert (
        session.publish_room_clock_state(
            source=RoomClockSourceValue.SONG_FORM, bar=3
        )
        is None
    )

    session.control = SessionControlState(
        tmp_path, str(uuid.uuid4()), creator_profile_key="art"
    )
    published = session.publish_room_clock_state(
        source=RoomClockSourceValue.SONG_FORM, running=True, bar=3
    )

    assert published is not None
    assert published.bar == 3
