"""The shared host clock, and the contract other creator profiles read.

The load-bearing property is honesty about what the clock is. It is a
reference the host runs, not a measurement of what the band played, and the
published snapshot has to say so.
"""

from __future__ import annotations

import json

import pytest

from core.song_clock import (
    DEFAULT_SECTION_BARS,
    POSITION_HOST_COUNT,
    POSITION_SHARED_TRACK,
    MAX_TEMPO_BPM,
    MIN_TEMPO_BPM,
    STATE_PAUSED,
    STATE_RUNNING,
    STATE_STOPPED,
    SongClock,
    SongClockPublisher,
    SongClockSnapshot,
    describe_contract,
    form_sections,
)
from core.song_form import DETECTED, STATED, parse_song_form

SONG = """Key: G major
Tempo: 120
[Intro x4]
G D
[Verse x8]
G D Em C
[Chorus x8]
C G D G
"""


@pytest.fixture
def clock():
    now = {"value": 0.0}
    instance = SongClock(monotonic=lambda: now["value"])
    instance.set_form(parse_song_form(SONG))
    instance.advance = lambda seconds: now.__setitem__("value", seconds)
    return instance


# ----------------------------------------------------------------------
# Counting
# ----------------------------------------------------------------------
def test_a_stopped_clock_sits_at_the_top_of_the_form(clock):
    snapshot = clock.snapshot()

    assert snapshot.state == STATE_STOPPED
    assert snapshot.section_label == "Intro"
    assert snapshot.bar == 1
    assert snapshot.bars_total == 20
    assert snapshot.parked is True
    assert snapshot.states_place is False
    assert snapshot.form_shape == "Intro → Verse → Chorus"


def test_locating_or_starting_is_a_stated_place(clock):
    clock.locate_section("Chorus")
    located = clock.snapshot()
    assert located.parked is False
    assert located.states_place is True
    assert located.section_label == "Chorus"

    clock.stop()
    clock.start()
    assert clock.snapshot().parked is False
    assert clock.snapshot().states_place is True


def test_the_clock_counts_bars_and_sections_from_the_stated_tempo(clock):
    clock.start()

    clock.advance(2.0)   # 120 BPM, 4/4 -> one bar every two seconds
    assert clock.snapshot().position_label == "Intro · bar 2 of 4"

    clock.advance(8.0)
    assert clock.snapshot().section_label == "Verse"
    assert clock.snapshot().bar_in_section == 1

    clock.advance(24.0)
    assert clock.snapshot().section_label == "Chorus"


def test_beats_advance_within_a_bar(clock):
    clock.start()
    clock.advance(1.0)  # half a bar at 120 BPM
    snapshot = clock.snapshot()

    assert snapshot.bar == 1
    assert snapshot.beat == 3


def test_the_clock_holds_at_the_written_end_rather_than_inventing_bars(clock):
    clock.start()
    clock.advance(600.0)
    snapshot = clock.snapshot()

    assert snapshot.section_label == "Chorus"
    assert snapshot.bar == snapshot.bars_total
    assert snapshot.bar_in_section == 8


def test_pause_holds_the_position_and_start_resumes_it(clock):
    clock.start()
    clock.advance(6.0)
    clock.pause()
    held = clock.snapshot()
    clock.advance(60.0)

    assert clock.snapshot().state == STATE_PAUSED
    assert clock.snapshot().bar == held.bar

    clock.start()
    assert clock.snapshot().state == STATE_RUNNING
    assert clock.snapshot().bar == held.bar


def test_stop_returns_to_the_top(clock):
    clock.start()
    clock.advance(20.0)
    clock.stop()

    assert clock.snapshot().state == STATE_STOPPED
    assert clock.snapshot().bar == 1
    assert clock.snapshot().section_label == "Intro"


def test_the_clock_can_be_moved_to_a_named_part(clock):
    assert clock.locate_section("Chorus")
    snapshot = clock.snapshot()

    assert snapshot.section_label == "Chorus"
    assert snapshot.bar_in_section == 1
    assert snapshot.bar == 13


def test_locating_a_part_the_song_does_not_have_fails_rather_than_moving(clock):
    before = clock.snapshot().bar
    assert clock.locate_section("Breakdown") is False
    assert clock.snapshot().bar == before


def test_a_tempo_change_keeps_the_musical_position(clock):
    clock.start()
    clock.advance(8.0)
    bar = clock.snapshot().bar

    assert clock.set_tempo(60.0)
    assert clock.snapshot().bar == bar


@pytest.mark.parametrize(
    "bpm", [0, -1, MIN_TEMPO_BPM - 1, MAX_TEMPO_BPM + 1, "fast", None]
)
def test_an_impossible_tempo_is_refused(clock, bpm):
    assert clock.set_tempo(bpm) is False


def test_a_clock_with_no_form_will_not_start():
    empty = SongClock()
    empty.set_form(parse_song_form(""))

    assert empty.start() is False
    assert empty.snapshot().has_form is False


def test_a_clock_with_no_tempo_will_not_start():
    clock = SongClock()
    clock.set_form(parse_song_form("[Verse]\nG D\n"))

    assert clock.start() is False
    assert clock.snapshot().tempo_bpm == 0


# ----------------------------------------------------------------------
# Honesty
# ----------------------------------------------------------------------
def test_the_clock_never_claims_to_follow_the_band(clock):
    """WebJam does no beat tracking on the live mix, so it must not imply it."""

    clock.start()
    clock.advance(4.0)
    snapshot = clock.snapshot()

    assert snapshot.following_audio is False
    assert snapshot.to_public_dict()["following_audio"] is False


def test_assumed_section_lengths_are_reported_as_assumed():
    clock = SongClock()
    clock.set_form(parse_song_form("Tempo: 100\n[Verse]\nG D\n[Chorus x8]\nC G\n"))
    snapshot = clock.snapshot()

    assert snapshot.section_lengths_assumed
    assert snapshot.sections[0].bars == DEFAULT_SECTION_BARS
    assert snapshot.sections[0].bars_stated is False
    assert snapshot.sections[1].bars_stated is True


def test_stated_lengths_are_not_reported_as_assumed(clock):
    assert clock.snapshot().section_lengths_assumed is False


def test_every_musical_fact_carries_its_source(clock):
    snapshot = clock.snapshot()

    assert snapshot.key == "G major"
    assert snapshot.key_source == STATED
    assert snapshot.tempo_source == STATED

    clock.set_tempo(96, source=DETECTED)
    assert clock.snapshot().tempo_source == DETECTED


def test_the_time_signature_is_read_from_the_notes():
    clock = SongClock()
    clock.set_form(parse_song_form("Tempo: 120\nTime: 3/4\n[Verse x4]\nG D\n"))
    assert clock.snapshot().beats_per_bar == 3


def test_the_chords_under_the_current_part_travel_with_the_position(clock):
    clock.locate_section("Verse")
    assert clock.snapshot().chords_now == ("G", "D", "Em", "C")


def test_the_description_is_one_readable_line(clock):
    clock.locate_section("Chorus")
    described = clock.snapshot().describe()

    assert "Chorus · bar 1 of 8" in described
    assert "120 BPM" in described
    assert "G major" in described


def test_a_parked_clock_does_not_describe_the_first_part_as_where(clock):
    """The published where must not ride Intro while the count is only sitting."""

    snapshot = clock.snapshot()

    assert snapshot.parked is True
    assert snapshot.states_place is False
    assert snapshot.position_label == ""
    assert snapshot.describe().startswith("Intro → Verse → Chorus is written")
    assert "bar" not in snapshot.describe().casefold()
    published = snapshot.to_public_dict()
    assert published["section"] == ""
    assert published["bar"] == 0
    assert published["beat"] == 0
    assert published["chords_now"] == []
    assert published["states_place"] is False
    assert published["form_shape"] == "Intro → Verse → Chorus"
    assert published["count_in"] is False


def test_an_empty_clock_describes_itself_without_pretending():
    assert SongClock().snapshot().describe() == "No song form yet."


# ----------------------------------------------------------------------
# The cross-profile contract
# ----------------------------------------------------------------------
def test_the_published_snapshot_is_plain_json_ready_values(clock):
    clock.start()
    clock.advance(10.0)
    published = clock.snapshot().to_public_dict()

    encoded = json.dumps(published)
    assert json.loads(encoded) == published


def test_the_contract_carries_the_fields_another_profile_would_read(clock):
    published = clock.snapshot().to_public_dict()

    for field in (
        "section",
        "bar",
        "beat",
        "key",
        "bpm",
        "position_s",
        "sections",
        "beats_per_bar",
    ):
        assert field in published


def test_the_contract_publishes_no_audio_paths_or_people(clock):
    """A canvas subscribing to bars must not receive the room's private data."""

    clock.start()
    clock.advance(10.0)
    # ensure_ascii=False so a written form_shape arrow is not a false "\\" leak.
    encoded = json.dumps(clock.snapshot().to_public_dict(), ensure_ascii=False).lower()

    for leak in ("path", "/", "\\", "musician", "participant", "token", "secret"):
        assert leak not in encoded


def test_the_contract_is_declared_for_other_profiles_to_assert_against():
    contract = describe_contract()

    assert contract["version"] == 1
    assert "section" in contract["fields"]
    assert "states_place" in contract["fields"]
    assert "form_shape" in contract["fields"]
    assert any("not audio-followed" in item for item in contract["guarantees"])
    assert any("named, not ridden" in item for item in contract["guarantees"])


def test_a_subscriber_receives_the_position(clock):
    publisher = SongClockPublisher(clock)
    seen: list[SongClockSnapshot] = []
    publisher.subscribe(seen.append)

    clock.start()
    clock.advance(10.0)
    publisher.publish(force=True)

    assert seen and seen[-1].section_label == "Verse"


def test_unsubscribing_stops_delivery(clock):
    publisher = SongClockPublisher(clock)
    seen: list[SongClockSnapshot] = []
    unsubscribe = publisher.subscribe(seen.append)

    publisher.publish(force=True)
    assert len(seen) == 1

    unsubscribe()
    publisher.publish(force=True)
    assert len(seen) == 1
    assert publisher.subscriber_count == 0


def test_a_broken_subscriber_is_dropped_rather_than_breaking_the_room(clock):
    """A misbehaving canvas must never be able to stop a jam."""

    publisher = SongClockPublisher(clock)
    good: list[SongClockSnapshot] = []

    def explode(_snapshot):
        raise RuntimeError("canvas is broken")

    publisher.subscribe(explode)
    publisher.subscribe(good.append)

    publisher.publish(force=True)
    publisher.publish(force=True)

    assert len(good) == 2
    assert publisher.subscriber_count == 1


def test_a_subscriber_must_be_callable(clock):
    with pytest.raises(TypeError):
        SongClockPublisher(clock).subscribe("not a function")


def test_publishing_is_quiet_when_nothing_changed(clock):
    publisher = SongClockPublisher(clock)
    seen: list[SongClockSnapshot] = []
    publisher.subscribe(seen.append)

    publisher.publish(force=True)
    publisher.publish()

    assert len(seen) == 1


def test_a_running_clock_publishes_every_tick(clock):
    publisher = SongClockPublisher(clock)
    seen: list[SongClockSnapshot] = []
    publisher.subscribe(seen.append)

    clock.start()
    publisher.publish()
    clock.advance(2.0)
    publisher.publish()

    assert len(seen) == 2
    assert seen[-1].bar == 2


def test_the_clock_view_of_a_form_uses_stated_lengths_where_given():
    sections = form_sections(parse_song_form(SONG))

    assert [item.name for item in sections] == ["Intro", "Verse", "Chorus"]
    assert [item.bars for item in sections] == [4, 8, 8]
    assert all(item.bars_stated for item in sections)


# ----------------------------------------------------------------------
# Shared Track is the session's clock for audio
# ----------------------------------------------------------------------
def test_a_loaded_shared_track_at_the_top_is_not_a_place(clock):
    """Loading the file parks the transport. That is not Verse."""

    clock.follow_shared_track(loaded=True, position_s=0.0, playing=False)
    snapshot = clock.snapshot()

    assert snapshot.follows_shared_track is True
    assert snapshot.parked is False
    assert snapshot.states_place is False
    assert snapshot.count_in is False


def test_a_count_in_is_not_a_place_even_at_a_later_bar(clock):
    clock.follow_shared_track(
        loaded=True, position_s=20.0, playing=False, count_in=True
    )
    snapshot = clock.snapshot()

    assert snapshot.count_in is True
    assert snapshot.section_label == "Verse"
    assert snapshot.states_place is False
    assert snapshot.position_label == ""
    published = snapshot.to_public_dict()
    assert published["section"] == ""
    assert published["bar"] == 0
    assert published["position_s"] == 0.0
    assert published["count_in"] is True
    assert published["states_place"] is False
    assert snapshot.describe().startswith("Intro → Verse → Chorus is written")
    assert "bar" not in snapshot.describe().casefold()


def test_a_loaded_shared_track_takes_over_the_position(clock):
    """One host transport for audio; the panel must not count separately."""

    clock.start()
    clock.advance(4.0)
    assert clock.snapshot().position_source == POSITION_HOST_COUNT

    clock.follow_shared_track(loaded=True, position_s=20.0, playing=True)
    snapshot = clock.snapshot()

    assert snapshot.position_source == POSITION_SHARED_TRACK
    assert snapshot.follows_shared_track
    assert snapshot.position_s == 20.0
    assert snapshot.section_label == "Verse"


def test_the_panel_cannot_start_a_second_count_while_a_track_holds_one(clock):
    clock.follow_shared_track(loaded=True, position_s=6.0, playing=True)
    assert clock.start() is False
    assert clock.snapshot().position_s == 6.0


def test_the_tracks_transport_decides_running_or_paused(clock):
    clock.follow_shared_track(loaded=True, position_s=6.0, playing=True)
    assert clock.snapshot().state == STATE_RUNNING

    clock.follow_shared_track(loaded=True, position_s=6.0, playing=False)
    assert clock.snapshot().state == STATE_PAUSED


def test_scrubbing_the_track_moves_the_bar_count_with_it(clock):
    clock.follow_shared_track(loaded=True, position_s=2.0, playing=True)
    assert clock.snapshot().section_label == "Intro"

    clock.follow_shared_track(loaded=True, position_s=34.0, playing=True)
    assert clock.snapshot().section_label == "Chorus"


def test_the_clock_does_not_drift_while_following_a_paused_track(clock):
    clock.follow_shared_track(loaded=True, position_s=10.0, playing=False)
    before = clock.snapshot().bar
    clock.advance(120.0)

    assert clock.snapshot().bar == before


def test_removing_the_track_hands_the_count_back_where_the_room_heard_it(clock):
    clock.follow_shared_track(loaded=True, position_s=20.0, playing=True)
    heard = clock.snapshot()

    clock.follow_shared_track(loaded=False)
    handed_back = clock.snapshot()

    assert handed_back.position_source == POSITION_HOST_COUNT
    assert handed_back.section_label == heard.section_label
    assert handed_back.bar == heard.bar
    assert clock.start() is True


def test_following_a_track_is_still_not_following_the_band(clock):
    """Position comes from a file's transport, not from listening to players."""

    clock.follow_shared_track(loaded=True, position_s=20.0, playing=True)
    snapshot = clock.snapshot()

    assert snapshot.following_audio is False
    assert snapshot.to_public_dict()["position_source"] == POSITION_SHARED_TRACK


def test_the_position_source_is_part_of_the_published_contract(clock):
    published = clock.snapshot().to_public_dict()
    assert published["position_source"] == POSITION_HOST_COUNT
    assert "position_source" in describe_contract()["fields"]


def test_a_track_with_no_movement_does_not_churn_the_generation(clock):
    clock.follow_shared_track(loaded=True, position_s=5.0, playing=True)
    generation = clock.snapshot().generation

    clock.follow_shared_track(loaded=True, position_s=5.0, playing=True)
    assert clock.snapshot().generation == generation

    clock.follow_shared_track(loaded=True, position_s=9.0, playing=True)
    assert clock.snapshot().generation != generation
