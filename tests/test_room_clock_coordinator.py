"""The room clock coordinator, and the seam a music surface plugs into.

Most of this file is about the seam. Art ships with no song-form owner on
purpose, so these tests supply a fake one to prove three things: that a song
outranks a reference video, that Art works exactly as well when nothing owns a
song, and that a music surface can become the owner without any painting
surface changing.
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.reference_video import ReferenceVideoSnapshot, ReferenceVideoState  # noqa: E402
from core.room_clock import (  # noqa: E402
    NO_CLOCK_HEADLINE,
    NO_PLACE_DETAIL,
    RoomClockFacts,
    RoomClockSource,
    reference_video_facts,
    song_form_facts,
)
from core.song_clock import SongClock  # noqa: E402
from core.song_form import parse_song_form  # noqa: E402
from core.session_transfer import (  # noqa: E402
    RecordingSignal,
    RoomClockSessionSnapshot,
    RoomClockSourceValue,
    SessionStateSnapshot,
)
from webjam_qt.controllers.room_clock_coordinator import (  # noqa: E402
    RoomClockCoordinator,
    no_song_form,
)


class FakeHostPeer:
    def __init__(self, *, active: bool = True, explode: bool = False) -> None:
        self.active = active
        self.explode = explode
        self.published: list[dict] = []

    def publish_room_clock_state(self, **kwargs):
        if self.explode:
            raise RuntimeError("peer plane is unhappy")
        self.published.append(kwargs)
        # Prove every publication is a legal projection, not just a dict.
        return RoomClockSessionSnapshot(**kwargs)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _coordinator(
    *, peer=None, song=None, video=None, clock=None, views=None
) -> RoomClockCoordinator:
    return RoomClockCoordinator(
        host_peer_provider=lambda: peer,
        song_form_provider=song if song is not None else no_song_form,
        video_facts_provider=video if video is not None else (lambda: None),
        clock=clock or (lambda: 1_000.0),
        on_view=views.append if views is not None else None,
    )


def _state(clock: RoomClockSessionSnapshot) -> SessionStateSnapshot:
    return SessionStateSnapshot(
        session_id=str(uuid.uuid4()),
        generation=3,
        signal=RecordingSignal.IDLE,
        creator_profile_key="art",
        room_clock=clock,
    )


def _playing_video(position_s: float = 42.0) -> RoomClockFacts:
    return reference_video_facts(
        ReferenceVideoSnapshot(
            state=ReferenceVideoState.PLAYING,
            shared=True,
            source_display_name="lesson.mp4",
            identity_digest="a" * 64,
            position_s=position_s,
            duration_s=600.0,
        ),
        playing_state=ReferenceVideoState.PLAYING,
    )


def _song_form(bar: int = 17, section: str = "Chorus") -> RoomClockFacts:
    return RoomClockFacts(
        source=RoomClockSource.SONG_FORM,
        running=True,
        bar=bar,
        beat=1,
        section_label=section,
        tempo_bpm=124.0,
        meter_numerator=4,
        meter_denominator=4,
    )


# ---------------------------------------------------------------------------
# What Art ships with
# ---------------------------------------------------------------------------


def test_art_ships_with_no_song_form_owner():
    """Art has no song engine and must not pretend to have one."""

    assert no_song_form() is None


def test_a_room_with_nothing_owning_a_pulse_has_no_clock():
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer)
    coordinator.begin_host()

    view = coordinator.tick()

    assert view.present is False
    assert view.source is RoomClockSource.NONE
    # Nothing is published for a room that has no pulse.
    assert peer.published == []


def test_an_unbound_coordinator_owns_nothing():
    coordinator = _coordinator()

    assert coordinator.role == ""
    assert coordinator.hosting is False
    assert coordinator.following is False
    assert coordinator.view.present is False


# ---------------------------------------------------------------------------
# Art's own owner: the reference video
# ---------------------------------------------------------------------------


def test_a_host_publishes_its_reference_video_position():
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer, video=lambda: _playing_video(90.0))
    coordinator.begin_host()

    view = coordinator.tick()

    assert peer.published == [
        {
            "source": "reference_video",
            "running": True,
            "position_s": 90.0,
            "duration_s": 600.0,
            "bar": 0,
            "beat": 0,
            "section_label": "",
            "tempo_bpm": 0.0,
            "meter_numerator": 0,
            "meter_denominator": 0,
        }
    ]
    assert view.source is RoomClockSource.REFERENCE_VIDEO
    assert view.musical is False
    assert view.headline == "1:30 / 10:00"


def test_publication_is_skipped_until_the_pulse_actually_changes():
    peer = FakeHostPeer()
    facts = _playing_video(90.0)
    coordinator = _coordinator(peer=peer, video=lambda: facts)
    coordinator.begin_host()

    coordinator.tick()
    coordinator.tick()
    coordinator.tick()

    assert len(peer.published) == 1


def test_a_host_reads_its_own_pulse_with_no_age():
    """The host is holding the clock, so nothing is extrapolated or stale."""

    clock = Clock()
    coordinator = _coordinator(video=lambda: _playing_video(90.0), clock=clock)
    coordinator.begin_host()

    clock.now += 600.0
    view = coordinator.view

    assert view.stale is False
    assert view.headline == "1:30 / 10:00"


def test_a_video_nobody_is_sharing_returns_the_room_to_no_clock():
    peer = FakeHostPeer()
    shared = [_playing_video(90.0)]
    coordinator = _coordinator(peer=peer, video=lambda: shared[0])
    coordinator.begin_host()
    coordinator.tick()

    shared[0] = None
    view = coordinator.tick()

    assert view.present is False
    assert peer.published[-1]["source"] == "none"


# ---------------------------------------------------------------------------
# The seam: a music surface becomes the owner
# ---------------------------------------------------------------------------


def test_a_song_form_owner_takes_over_the_room_without_art_changing():
    """This is the whole point of the seam.

    Art supplies no song form. A music surface supplies one later, and the
    painter's readout becomes bars without a single painting surface knowing
    that happened.
    """

    peer = FakeHostPeer()
    coordinator = _coordinator(
        peer=peer,
        song=lambda: _song_form(bar=17),
        video=lambda: _playing_video(90.0),
    )
    coordinator.begin_host()

    view = coordinator.tick()

    assert view.source is RoomClockSource.SONG_FORM
    assert view.musical is True
    assert view.headline == "Bar 17.1 · Chorus"
    assert peer.published[-1]["bar"] == 17
    # The video is still running; the song simply speaks for the room.
    assert peer.published[-1]["source"] == "song_form"


def test_an_outline_without_a_place_does_not_take_over_a_video():
    """A written shape is not a where. Painters still ride the file."""

    peer = FakeHostPeer()
    coordinator = _coordinator(
        peer=peer,
        song=lambda: RoomClockFacts(
            source=RoomClockSource.SONG_FORM, form_shape="Verse → Chorus"
        ),
        video=lambda: _playing_video(90.0),
    )
    coordinator.begin_host()

    view = coordinator.tick()

    assert view.source is RoomClockSource.REFERENCE_VIDEO
    assert view.headline == "1:30 / 10:00"
    assert peer.published[-1]["source"] == "reference_video"


def test_a_parked_clock_with_a_tempo_publishes_the_named_outline():
    """Constructor refuse stays. The publish path must not dress parked Verse."""

    clock = SongClock()
    clock.set_form(parse_song_form("[Verse]\n[Chorus]\n"))
    clock.set_tempo(120.0)
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer, song=lambda: song_form_facts(clock.snapshot()))
    coordinator.begin_host()

    view = coordinator.tick()

    assert view.headline == NO_CLOCK_HEADLINE
    assert view.detail == f"Verse → Chorus is written. {NO_PLACE_DETAIL}"
    assert view.musical is False
    published = peer.published[-1]
    assert published["source"] == "song_form"
    assert published["form_shape"] == "Verse → Chorus"
    assert published["bar"] == 0
    assert published["section_label"] == ""
    assert published["running"] is False
    assert published["tempo_bpm"] == 0.0


def test_an_outline_without_a_place_is_named_when_nothing_else_speaks():
    peer = FakeHostPeer()
    coordinator = _coordinator(
        peer=peer,
        song=lambda: RoomClockFacts(
            source=RoomClockSource.SONG_FORM, form_shape="Verse → Chorus"
        ),
    )
    coordinator.begin_host()

    view = coordinator.tick()

    assert view.headline == NO_CLOCK_HEADLINE
    assert view.detail == f"Verse → Chorus is written. {NO_PLACE_DETAIL}"
    assert view.musical is False
    assert peer.published[-1]["source"] == "song_form"
    assert peer.published[-1]["form_shape"] == "Verse → Chorus"
    assert peer.published[-1]["bar"] == 0
    assert peer.published[-1]["running"] is False


def test_a_song_that_stops_hands_the_room_back_to_the_video():
    peer = FakeHostPeer()
    song = [_song_form(bar=17)]
    coordinator = _coordinator(
        peer=peer, song=lambda: song[0], video=lambda: _playing_video(90.0)
    )
    coordinator.begin_host()
    coordinator.tick()

    song[0] = None
    view = coordinator.tick()

    assert view.source is RoomClockSource.REFERENCE_VIDEO
    assert peer.published[-1]["source"] == "reference_video"


def test_an_owner_that_raises_is_treated_as_owning_nothing():
    def exploding():
        raise RuntimeError("the song engine fell over")

    peer = FakeHostPeer()
    coordinator = _coordinator(
        peer=peer, song=exploding, video=lambda: _playing_video(90.0)
    )
    coordinator.begin_host()

    view = coordinator.tick()

    # A broken owner must not take the room's pulse down with it.
    assert view.source is RoomClockSource.REFERENCE_VIDEO


def test_an_owner_returning_nonsense_is_treated_as_owning_nothing():
    coordinator = _coordinator(song=lambda: "bar 17", video=lambda: None)
    coordinator.begin_host()

    assert coordinator.tick().present is False


# ---------------------------------------------------------------------------
# A follower reading the pulse
# ---------------------------------------------------------------------------


def test_a_guest_keeps_a_leftover_outline_without_riding_the_timer():
    """A leftover timer on a named outline must not look like Verse or vanish."""

    views: list = []
    coordinator = _coordinator(views=views)
    coordinator.begin_guest()

    leftover = RoomClockSessionSnapshot.from_mapping(
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
            "form_shape": "Verse → Chorus",
        }
    )
    coordinator.observe_host_state(_state(leftover))

    assert leftover.form_shape == "Verse → Chorus"
    assert leftover.running is False
    assert views[-1].headline == NO_CLOCK_HEADLINE
    assert views[-1].detail == f"Verse → Chorus is written. {NO_PLACE_DETAIL}"
    assert views[-1].musical is False
    assert "Verse" not in views[-1].headline


def test_a_guest_renders_whatever_the_owner_published():
    views: list = []
    coordinator = _coordinator(views=views)
    coordinator.begin_guest()

    coordinator.observe_host_state(
        _state(
            RoomClockSessionSnapshot(
                source=RoomClockSourceValue.SONG_FORM,
                running=True,
                bar=17,
                section_label="Chorus",
            )
        )
    )

    assert views[-1].headline == "Bar 17 · Chorus"
    assert views[-1].musical is True


def test_a_guest_measures_the_pulses_age_locally():
    """No clock is shared between computers, only a locally measured age."""

    clock = Clock()
    coordinator = _coordinator(clock=clock)
    coordinator.begin_guest()
    coordinator.observe_host_state(
        _state(
            RoomClockSessionSnapshot(
                source=RoomClockSourceValue.REFERENCE_VIDEO,
                running=True,
                position_s=100.0,
                duration_s=600.0,
            )
        )
    )

    assert coordinator.view.headline == "1:40 / 10:00"
    clock.now += 3.0
    assert coordinator.view.headline == "1:43 / 10:00"
    clock.now += 30.0
    assert coordinator.view.stale is True


def test_a_guest_publishes_nothing():
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer)
    coordinator.begin_guest()

    coordinator.observe_host_state(
        _state(
            RoomClockSessionSnapshot(
                source=RoomClockSourceValue.SONG_FORM, running=True, bar=4
            )
        )
    )
    coordinator.tick()

    assert peer.published == []


def test_a_session_state_with_no_clock_member_reads_as_no_clock():
    coordinator = _coordinator()
    coordinator.begin_guest()

    assert coordinator.observe_host_state(object()).present is False


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


def test_publication_is_skipped_while_the_peer_plane_is_inactive():
    peer = FakeHostPeer(active=False)
    coordinator = _coordinator(peer=peer, video=lambda: _playing_video())
    coordinator.begin_host()

    view = coordinator.tick()

    assert peer.published == []
    assert view.source is RoomClockSource.REFERENCE_VIDEO


def test_a_peer_failure_never_breaks_the_readout():
    peer = FakeHostPeer(explode=True)
    coordinator = _coordinator(peer=peer, video=lambda: _playing_video())
    coordinator.begin_host()

    assert coordinator.tick().source is RoomClockSource.REFERENCE_VIDEO


def test_rebinding_releases_the_previous_role():
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer, video=lambda: _playing_video())
    coordinator.begin_host()
    coordinator.tick()

    coordinator.begin_guest()

    assert coordinator.hosting is False
    assert coordinator.following is True
    assert peer.published[-1]["source"] == "none"


def test_ending_returns_the_room_to_no_clock():
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer, video=lambda: _playing_video())
    coordinator.begin_host()
    coordinator.tick()

    coordinator.end()

    assert coordinator.role == ""
    assert coordinator.view.present is False
    assert peer.published[-1]["source"] == "none"


def test_the_coordinator_offers_no_way_to_move_the_clock():
    """A readout is not a transport. Only the owner moves the room."""

    coordinator = _coordinator()

    for forbidden in ("play", "pause", "stop", "seek", "set_bar", "set_position"):
        assert not hasattr(coordinator, forbidden), forbidden
