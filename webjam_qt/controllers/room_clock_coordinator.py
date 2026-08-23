"""Session-scoped ownership of the room's one pulse.

The coordinator is the seam between three things that must not know about each
other: whatever owns a song form, Art's host-clocked reference video, and the
private peer plane. It owns none of their policy. It decides only which owner
speaks for the room right now, publishes that, and renders whatever a
follower was told.

The song-form provider is the point of this whole file. It is a callable that
returns :class:`core.room_clock.RoomClockFacts` or ``None``, and today nothing
in Art supplies it: Art has no song engine and must not pretend to have one. A
music surface can supply it later without a single painting surface changing,
and when it does, a song outranks a reference video because a painter riding a
band should be riding bars rather than a file offset.

It holds no Qt types, so it can be exercised headlessly with fake providers
and a fake host peer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from core.room_clock import (
    DEFAULT_STALE_AFTER_S,
    RoomClockFacts,
    RoomClockSource,
    RoomClockView,
    render_room_clock,
    stronger_facts,
)

LOGGER = logging.getLogger("webjam.qt.room_clock_coordinator")

#: A provider that knows nothing. This is the shipped default: Art owns no
#: song form, so it says so rather than inventing one.
def no_song_form() -> None:
    return None


class RoomClockCoordinator:
    """Publish the room's pulse from its real owner, and render what arrives."""

    def __init__(
        self,
        *,
        host_peer_provider: Callable[[], object] = lambda: None,
        song_form_provider: Callable[[], RoomClockFacts | None] = no_song_form,
        video_facts_provider: Callable[[], RoomClockFacts | None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        on_view: Callable[[RoomClockView], None] | None = None,
    ) -> None:
        self._host_peer_provider = host_peer_provider
        self._song_form_provider = song_form_provider
        self._video_facts_provider = video_facts_provider
        self._clock = clock
        self._stale_after_s = float(stale_after_s)
        self._on_view = on_view
        self._role = ""
        self._projection: object | None = None
        self._received_s = 0.0
        # A room starts with no clock, so the absent state is what has already
        # been "published". Announcing the absence of something nobody ever
        # had would be a write with nothing behind it.
        self._published = RoomClockFacts()
        self._publish_failed = False

    # -- lifecycle -----------------------------------------------------

    @property
    def role(self) -> str:
        return self._role

    @property
    def hosting(self) -> bool:
        return self._role == "host"

    @property
    def following(self) -> bool:
        return self._role == "guest"

    def begin_host(self) -> None:
        self.end()
        self._role = "host"

    def begin_guest(self) -> None:
        self.end()
        self._role = "guest"

    def end(self) -> None:
        """Return the room to having no clock."""

        if self._role == "host" and self._published.source is not RoomClockSource.NONE:
            self._publish_absent()
        self._role = ""
        self._projection = None
        self._received_s = 0.0
        self._published = RoomClockFacts()
        self._publish_failed = False

    # -- what this computer shows --------------------------------------

    @property
    def view(self) -> RoomClockView:
        """The one line a surface renders, honest about its own age."""

        if self.hosting:
            facts = self._current_facts()
            if facts.source is RoomClockSource.NONE:
                return RoomClockView()
            # A host reads its own pulse with no age at all: it is the one
            # holding it, so there is nothing to extrapolate or go stale.
            return render_room_clock(
                facts, age_s=0.0, stale_after_s=self._stale_after_s
            )
        projection = self._projection
        if projection is None:
            return RoomClockView()
        return render_room_clock(
            projection,
            age_s=max(0.0, self._clock() - self._received_s),
            stale_after_s=self._stale_after_s,
        )

    def observe_host_state(self, session_state: object) -> RoomClockView:
        """Record the newest pulse carried by the peer plane.

        Receipt time is measured locally, so no clock is shared between
        computers -- the same rule the reference video follower uses.
        """

        self._projection = getattr(session_state, "room_clock", None)
        self._received_s = self._clock()
        return self._notify(self.view)

    # -- what this computer publishes ----------------------------------

    def tick(self) -> RoomClockView:
        """Publish the current owner's pulse, best effort.

        A failure here must never interrupt the conversation, the canvas, or
        the video, so nothing raises out of it.
        """

        if not self.hosting:
            return self._notify(self.view)
        facts = self._current_facts()
        if facts != self._published:
            self._publish(facts)
            self._published = facts
        return self._notify(self.view)

    def _current_facts(self) -> RoomClockFacts:
        return stronger_facts(self._read(self._song_form_provider), self._read(self._video_facts_provider))

    @staticmethod
    def _read(provider: Callable[[], RoomClockFacts | None]) -> RoomClockFacts | None:
        try:
            facts = provider()
        except Exception:  # noqa: BLE001 - a silent owner is no clock
            LOGGER.debug("A room clock owner could not be read", exc_info=True)
            return None
        return facts if isinstance(facts, RoomClockFacts) else None

    def _publish(self, facts: RoomClockFacts) -> None:
        publish = self._peer_publisher()
        if publish is None:
            return
        try:
            payload = {
                "source": facts.source.value,
                "running": bool(facts.running),
                "position_s": float(facts.position_s),
                "duration_s": float(facts.duration_s),
                "bar": int(facts.bar),
                "beat": int(facts.beat),
                "section_label": str(facts.section_label),
                "tempo_bpm": float(facts.tempo_bpm),
                "meter_numerator": int(facts.meter_numerator),
                "meter_denominator": int(facts.meter_denominator),
            }
            if facts.source is RoomClockSource.SONG_FORM:
                payload["follows_shared_track"] = bool(facts.follows_shared_track)
                payload["section_lengths_assumed"] = bool(
                    facts.section_lengths_assumed
                )
                payload["form_shape"] = str(facts.form_shape)
            publish(**payload)
        except Exception:  # noqa: BLE001 - peer boundary stays UI-optional
            if not self._publish_failed:
                LOGGER.warning("The room clock could not be published")
            self._publish_failed = True
        else:
            self._publish_failed = False

    def _publish_absent(self) -> None:
        publish = self._peer_publisher()
        if publish is None:
            return
        try:
            publish(source=RoomClockSource.NONE.value)
        except Exception:  # noqa: BLE001 - teardown must not raise
            LOGGER.debug("Room clock teardown publish failed", exc_info=True)

    def _peer_publisher(self):
        host_peer = self._host_peer_provider()
        publish = getattr(host_peer, "publish_room_clock_state", None)
        if not bool(getattr(host_peer, "active", False)) or not callable(publish):
            return None
        return publish

    def _notify(self, view: RoomClockView) -> RoomClockView:
        if self._on_view is not None:
            self._on_view(view)
        return view


__all__ = ["RoomClockCoordinator", "no_song_form"]
