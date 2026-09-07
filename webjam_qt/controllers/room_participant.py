"""Memory-only room participation beside the existing audio owners.

A LAN observer discovers the authenticated host profile before constructing a
recording guest. Native peers carry the same creative projections over their
already authenticated connection. Neither path manufactures audio evidence.
"""
from __future__ import annotations

import logging
import time
import unicodedata
from dataclasses import dataclass

from PySide6.QtCore import QTimer

from core.creative_modes import canonical_creator_profile_key
from core.musician_guidance import GuidanceDisplayOverride
from core.room_state import RoomState
from core.session_conductor import ArtRoomState, SessionPrimaryAction
from core.session_transfer import (
    ReferenceVideoSessionSnapshot,
    RoomConnectionNames,
    SharedCanvasSessionSnapshot,
)


LOGGER = logging.getLogger("webjam.qt.room_participant")


@dataclass(frozen=True, repr=False)
class RoomShareReadiness:
    address: str = ""
    shareable: bool = False


class NativeRoomPublisher:
    """Adapt the existing creative owners to full native room snapshots."""

    def __init__(self, app, owner):
        self.app = app
        self.owner = owner
        self.identity = owner.room_identity
        self.revision = 0
        self.video = ReferenceVideoSessionSnapshot()
        self.canvas = SharedCanvasSessionSnapshot()
        self._canvas_operation = 0
        self._accepted_room_state: RoomState | None = None

    @property
    def active(self):
        return (self.owner is self.app._remote_invite_owner
                and self.identity is not None
                and self.identity == self.owner.room_identity)

    def publish(self):
        return self._publish_state(self.canvas)

    def _publish_state(self, canvas):
        if not self.active:
            return False
        self.revision += 1
        profile = self.app.creator_profile.key
        start = self.app.creator_start
        art_start = start.key if profile == "art" and start is not None else ""
        if self.video.shared:
            art_start = "paint_along"
        state = RoomState(
            self.revision, profile, art_start,
            reference_video=self.video if profile == "art" else ReferenceVideoSessionSnapshot(),
            shared_canvas=canvas if profile == "art" else SharedCanvasSessionSnapshot(),
        )
        accepted = self.owner.publish_room_state(state)
        if accepted is True and (
            self._accepted_room_state is None
            or state.revision > self._accepted_room_state.revision
        ):
            # A nested full/video publication may have retained a newer
            # state before this older owner's call returns.
            self._accepted_room_state = state
        return accepted

    def publish_reference_video_state(self, **values):
        if "source_display_name" in values:
            values["source_display_name"] = unicodedata.normalize("NFC", values["source_display_name"])
        self.video = ReferenceVideoSessionSnapshot(
            generation=self.video.generation + 1,
            playback_generation=self.video.playback_generation + int(
                values.get("state") == "playing" and self.video.state.value != "playing"
            ),
            **values,
        )
        self.publish()
        return self.video

    def publish_shared_canvas_state(self, **values) -> SharedCanvasSessionSnapshot | None:
        """Retain a canvas only after this owner accepts its full room state.

        Acceptance means the native owner retained it for publication, not
        that any guest received it or connected to Drawpile.
        """
        self._canvas_operation += 1
        operation = self._canvas_operation
        owner, identity = self.owner, self.identity
        try:
            if self.app.creator_profile.key != "art":
                return None
            for name in ("server_label", "session_label"):
                if name in values:
                    values[name] = unicodedata.normalize("NFC", values[name])
            candidate = SharedCanvasSessionSnapshot(
                generation=operation, **values,
            )
            accepted = self._publish_state(candidate)
            if (
                accepted is not True or operation != self._canvas_operation
                or self._accepted_room_state is None
                or self._accepted_room_state.shared_canvas is not candidate
                or self.owner is not owner or self.identity != identity or not self.active
                or self.app.creator_profile.key != "art"
            ):
                return None
        except Exception:  # noqa: BLE001 - only a bounded missing receipt escapes
            # Native exceptions can contain a private canvas invitation.
            return None
        self.canvas = candidate
        return candidate


class RoomParticipantController:
    """One current room binding; queued callbacks must prove their owner."""

    def __init__(self, app):
        self.app = app
        self.lan_guest = None
        self._lan_terminal_owner = None
        self._lan_retry_in_progress = False
        self._lan_host_retry_in_progress = False
        self._music_host_retry_in_progress = False
        self._music_host_retry_owner = None
        self._music_network_owner = None
        self._music_network_interrupted = False
        self._host_names_owner = None
        self._host_names_observed_at = 0.0
        self.generation = 0
        self.role = ""
        self.state = ArtRoomState.NONE
        self.probing = False
        self.probe_failed = False
        self.music_invite = None
        self.borrowed_start = ""
        self.native_source = None
        self.native_generation = 0
        self.native_state = None
        self.native_received_at = 0.0
        self.native_applied = None
        self.native_wait_started = 0.0
        self.publisher = None
        self.stopping = False

    @property
    def active(self):
        return bool(self.probing or self.state is not ArtRoomState.NONE)

    @property
    def _app_closing(self):
        return bool(
            self.app._shutdown or self.app.audio.stopping
            or self.app.audio.cleanup_retry_required
            or getattr(self.app, "_shutdown_cleanup_pending", False)
            or getattr(self.app, "_shutdown_in_progress", False)
        )

    @property
    def blocked(self):
        return bool(self.stopping or self._app_closing)

    @property
    def lan_failed(self):
        """A terminal receipt remains true while its observer is still owned."""
        return self.lan_guest is not None and self.lan_guest is self._lan_terminal_owner

    @property
    def can_retry_lan(self):
        """Only a terminal receipt from this LAN observer authorizes retry."""
        from core.network_invite import BandInvite

        owner = self.lan_guest
        invite = getattr(owner, "invite", None)
        recording = getattr(self.app, "recording", None)
        return bool(
            self.lan_failed
            and isinstance(invite, BandInvite) and invite.peer_enabled
            and self.role == "guest"
            and (self.probe_failed or self.state is ArtRoomState.FAILED)
            and not self._lan_retry_in_progress and not self.blocked
            and not getattr(self.app, "_shutdown_cleanup_pending", False)
            and not getattr(self.app, "_shutdown_in_progress", False)
            and getattr(self.app, "_startup_attempt", None) is None
            and getattr(self.app, "_remote_session", None) is None
            and getattr(self.app, "_remote_invite_owner", None) is None
            and getattr(self.app, "_remote_invitation", None) is None
            and getattr(self.app, "guest_peer", None) is None
            and not getattr(recording, "take_in_progress", False)
            and not getattr(recording, "is_recording_active", False)
        )

    def retry_lan_guest(self):
        """Replace one stopped observer, retaining its reusable invitation."""
        if not self.can_retry_lan:
            return False
        owner = self.lan_guest
        invite = owner.invite
        self._lan_retry_in_progress = True
        # Retire callbacks before stop can run another queued UI operation.
        self.generation += 1
        generation = self.generation
        self.stopping = True
        try:
            try:
                stopped = owner.stop() is True
            except Exception:  # private peer details never reach recovery copy
                stopped = False
            if not stopped:
                self.app.audio.require_cleanup_retry(
                    hosting=False, art_room=True,
                    error="The previous room connection is still closing.",
                    title="Finish leaving the room",
                    detail="Choose Try Leave Room, then open the invitation again.",
                )
                return False
            # End/Quit or a replacement owner may have won while stop ran.
            if (self.lan_guest is not owner or self.generation != generation
                    or self.app._shutdown or self.app.audio.stopping
                    or self.app.audio.cleanup_retry_required
                    or getattr(self.app, "_shutdown_cleanup_pending", False)
                    or getattr(self.app, "_shutdown_in_progress", False)):
                return False
            # Before a first profile arrives, the saved Music conductor can
            # still be nonterminal. Confirmed observer cleanup is the real
            # idle boundary that permits replacing that attempt's token.
            conductor = self.app._live_session_conductor()
            self.app._session_conductor_token = conductor.reset_to_idle("guest")
            self.lan_guest = None
            self.stopping = False
            try:
                return self.start_lan_guest(invite)
            except Exception:  # keep the attempted owner reachable for cleanup
                if self.lan_guest is None:
                    self.lan_guest = owner
                self._lan_terminal_owner = self.lan_guest
                self.probing = True
                self.probe_failed = True
                self.state = ArtRoomState.FAILED
                self._lan_retry_in_progress = False
                self.app._update_session_hud()
                return False
        finally:
            self._lan_retry_in_progress = False

    def start_lan_guest(self, invite):
        if self.lan_guest is not None:
            return True
        from services.lan_room_guest import LanRoomGuest

        self.generation += 1
        generation = self.generation
        self.role = "guest"
        self.state = ArtRoomState.STARTING
        self.probing = True
        self.probe_failed = False
        self._lan_terminal_owner = None
        self.stopping = False
        self.app._conductor_setup_requested = True
        self.app._start_session_conductor_attempt("guest")

        def on_state(owner, state):
            self.app._ui_invoker.invoke(lambda: self.receive_lan(owner, generation, state))

        def on_loss(owner, terminal):
            self.app._ui_invoker.invoke(lambda: self.lose_lan(owner, generation, terminal))

        self.lan_guest = LanRoomGuest(
            invite, display_name=self.app.settings.musician_name,
            on_state=on_state, on_loss=on_loss,
        )
        self.lan_guest.start()
        self.app._refresh_readiness()
        return True

    def receive_lan(self, owner, generation, state):
        if (owner is not self.lan_guest or generation != self.generation
                or self.blocked):
            return
        profile = canonical_creator_profile_key(state.creator_profile_key)
        if profile is None:
            self.lose_lan(owner, generation, True)
            return
        self._lan_terminal_owner = None
        if profile != "art":
            invite = owner.invite
            # Retire queued discovery receipts before the stop can deliver
            # End/Quit or a replacement invitation on the owner thread.
            self.generation += 1
            generation = self.generation
            self.stopping = True
            try:
                stopped = owner.stop() is True
            except Exception:  # private invitation/error details stay local
                stopped = False
            if (self.lan_guest is not owner or self.generation != generation
                    or self.role != "guest" or self._app_closing
                    or self.app._guest_invite is not invite
                    or self.app._remote_session is not None
                    or self.app._remote_invite_owner is not None):
                return
            if not stopped:
                LOGGER.warning("Music LAN room discovery cleanup unconfirmed")
                self.app.audio.require_cleanup_retry(
                    hosting=False, art_room=True,
                    error="The previous room connection is still closing.",
                    title="Finish leaving the room", detail="Choose Try Leave Room, then open the invitation again.",
                )
                return
            self.lan_guest = None
            self.probing = False
            self.state = ArtRoomState.NONE
            self.stopping = False
            if not self.app._configure_guest_peer(invite):
                return
            # Configuration retires the prior peer once. Any additional
            # retirement or new invitation belongs to its newer owner.
            if (self.generation != generation + 1 or self._app_closing
                    or self.app._guest_invite is not invite
                    or self.lan_guest is not None
                    or self.app._remote_session is not None
                    or self.app._remote_invite_owner is not None):
                return
            self.music_invite = invite
            self.stopping = False
            self.app._apply_creator_profile_key(profile, host_owned=True)
            self.app.begin_startup_journey()
            return
        self.probing = False
        self.probe_failed = False
        self.state = ArtRoomState.CONNECTED
        self.app._apply_creator_profile_key(profile, host_owned=True)
        self.observe_creative_state(state)
        self.app._refresh_readiness()

    def lose_lan(self, owner, generation, terminal):
        if owner is not self.lan_guest or generation != self.generation or self.blocked:
            return
        self._lan_terminal_owner = owner if terminal else None
        self.probe_failed = bool(terminal and self.probing)
        if terminal:
            self.state = ArtRoomState.FAILED
        elif not self.probing:
            self.state = ArtRoomState.RECONNECTING
        self.app._refresh_readiness()

    def start_lan_host(self):
        from core.network_invite import local_band_address

        if self._lan_host_retry_in_progress or self._app_closing:
            return False
        address = local_band_address()
        host = self.app.host_peer
        bound = getattr(getattr(host, "server", None), "address", ("", 0))[0]
        if (
            host.active and self.role == "host"
            and self.state in {
                ArtRoomState.WAITING, ArtRoomState.CONNECTED, ArtRoomState.RECONNECTING,
            }
            and (not address or bound == address)
        ):
            # A retry cannot improve an absent route. Keep the listener,
            # invitation and optional local work while its network can return.
            if not address:
                LOGGER.info("Art LAN room retry deferred: network unavailable")
            self.tick()
            self.app._refresh_readiness()
            return True

        self.role = "host"
        self.stopping = False
        self.app._conductor_setup_requested = True
        self.state = ArtRoomState.STARTING
        replacing = bool(host.active and bound != address)
        if replacing:
            self._lan_host_retry_in_progress = True
            self.stopping = True
            self.generation += 1
            generation = self.generation

            def still_current():
                return bool(
                    self.app.host_peer is host and self.generation == generation
                    and self.role == "host" and self.app.creator_profile.key == "art"
                    and self.app._remote_invite_owner is None and not self._app_closing
                )

            try:
                LOGGER.info("Art LAN room listener replacement requested")
                try:
                    stopped = host.stop() is True
                except Exception:  # private listener errors never enter diagnostics
                    stopped = False
                # End/Quit or a newer owner can win while stop is in progress.
                if not still_current():
                    LOGGER.info("Art LAN room listener replacement abandoned")
                    return False
                if not stopped or host.active:
                    LOGGER.warning("Art LAN room listener cleanup unconfirmed")
                    self.state = ArtRoomState.FAILED
                    self.app.audio.require_cleanup_retry(
                        hosting=True, art_room=True,
                        error="The previous room is still closing.",
                        title="Finish closing the room",
                    )
                    return False
                self.app._release_reference_video()
                self.app._release_shared_canvas()
                self.app._release_room_clock()
                if not still_current():
                    LOGGER.info("Art LAN room listener replacement abandoned")
                    return False
                # Confirmed old-listener cleanup permits a new attempt.
                # A timer observation must never manufacture this boundary.
                conductor = self.app._live_session_conductor()
                self.app._session_conductor_token = conductor.reset_to_idle("host")
                self.stopping = False
            finally:
                self._lan_host_retry_in_progress = False
        self.app._start_session_conductor_attempt("host")
        self.state = (ArtRoomState.WAITING if address and self.app._ensure_host_peer(address)
                      else ArtRoomState.FAILED)
        if replacing and self.state is ArtRoomState.WAITING:
            LOGGER.info("Art LAN room listener replacement completed")
        elif not address:
            LOGGER.info("Art LAN room start deferred: network unavailable")
        self.app._refresh_readiness()
        return True

    def _music_lan_host(self):
        return bool(
            self.app.creator_profile.key == "music"
            and self.app.settings.host_server_enabled
            and self.app._remote_invite_owner is None
            and self.app._remote_session is None
            and self.app._remote_invitation is None
        )

    def _music_host_blocked(self, *, ignore_retry=False):
        return bool(
            self._app_closing or self.app.audio.ended_by_user
            or (self._music_host_retry_in_progress and not ignore_retry)
            or getattr(self.app, "_invite_switch_in_flight", False)
            or getattr(self.app, "_primary_recovery_retire_inflight", False)
        )

    def music_host_readiness(self, readiness):
        """Observe the retained room endpoint; never repair it while rendering."""
        from core.host_share_readiness import HostShareReadiness, HostShareReadinessStatus

        if not self._music_lan_host():
            return readiness
        host = self.app.host_peer
        if self._music_host_blocked():
            return HostShareReadiness(HostShareReadinessStatus.ROOM_CONNECTION_UNAVAILABLE)
        if self._music_host_retry_owner is not None and self._music_host_retry_owner is not host:
            self._music_host_retry_owner = None
        bound = getattr(getattr(host, "server", None), "address", ("", 0))[0]
        if readiness.shareable and (
            (host.active and bound != readiness.address)
            or self._music_host_retry_owner is host
        ):
            readiness = HostShareReadiness(
                HostShareReadinessStatus.ROOM_CONNECTION_UNAVAILABLE, readiness.address,
            )
        if self._music_network_owner is not host:
            self._music_network_owner = host
            self._music_network_interrupted = False
        interrupted = bool(
            (host.active or self._music_host_retry_owner is host)
            and readiness.status in {
                HostShareReadinessStatus.NETWORK_UNAVAILABLE,
                HostShareReadinessStatus.ROOM_CONNECTION_UNAVAILABLE,
            }
        )
        if interrupted and not self._music_network_interrupted:
            LOGGER.info("Music LAN room network interrupted")
        elif self._music_network_interrupted and readiness.shareable:
            LOGGER.info("Music LAN room network route restored")
        self._music_network_interrupted = interrupted
        return readiness

    def music_host_work_retained(self):
        recording = self.app.recording
        shared = getattr(getattr(self.app, "_reference_track", None), "snapshot", None)
        return bool(
            recording.take_in_progress or recording.is_recording_active
            or self.app.host_peer.has_recording_work
            or getattr(shared, "active", False)
            or getattr(shared, "cleanup_pending", False)
        )

    def music_host_recovery_guidance(self):
        if self.app.recording.take_in_progress or self.app.recording.is_recording_active:
            # Stop Recording/finalization keeps its canonical action.
            return None
        return GuidanceDisplayOverride(
            "Room network changed",
            "Choose End Session to finish this room safely, then start a new jam on this Wi-Fi.",
            SessionPrimaryAction.END_SESSION, "End Session",
        )

    def retry_music_lan_host(self):
        """Repair only an idle Music room peer; audio keeps its own lifecycle."""
        from core.host_share_readiness import HostShareReadinessStatus
        from core.network_invite import local_band_address

        if not self._music_lan_host():
            return False
        if self._music_host_blocked() or self.app._startup_attempt is not None:
            return True
        host = self.app.host_peer
        readiness = self.app._host_share_readiness()
        if not (host.active or self._music_host_retry_owner is host) or readiness.status not in {
            HostShareReadinessStatus.NETWORK_UNAVAILABLE,
            HostShareReadinessStatus.ROOM_CONNECTION_UNAVAILABLE,
        }:
            return False
        address = readiness.address
        if not address:
            LOGGER.info("Music LAN room retry deferred: network unavailable")
            self.app._update_session_hud()
            return True
        if self.music_host_work_retained():
            LOGGER.info("Music LAN room retry deferred: recording work retained")
            self.app._update_session_hud()
            return True
        self._music_host_retry_in_progress = True
        self._music_host_retry_owner = host
        self.generation += 1
        generation = self.generation

        def still_current():
            return bool(
                self.app.host_peer is host and self.generation == generation
                and self._music_lan_host() and not self._app_closing
                and not self.app.audio.ended_by_user
                and not getattr(self.app, "_invite_switch_in_flight", False)
                and self.app._startup_attempt is None
            )

        try:
            LOGGER.info("Music LAN room listener replacement requested")
            try:
                stopped = host.stop() is True
            except Exception:  # private bind/transfer details never enter logs
                stopped = False
            if not still_current():
                LOGGER.info("Music LAN room listener replacement abandoned")
                return True
            if not stopped or host.active:
                LOGGER.warning("Music LAN room listener cleanup unconfirmed")
                self.app.audio.require_cleanup_retry(
                    hosting=True, error="The previous room is still closing.",
                    title="Finish ending the jam",
                )
                return True
            # The route can change again while cleanup waits. Retain the
            # explicit retry boundary instead of creating a stale endpoint.
            if local_band_address() != address or self.music_host_work_retained():
                LOGGER.info("Music LAN room listener replacement abandoned")
                return True
            # The normal creation helper is reused after the old peer is
            # proven gone. No conductor reset, audio restart, or take cleanup.
            started = self.app._ensure_host_peer(address)
            if not still_current():
                LOGGER.info("Music LAN room listener replacement abandoned")
                return True
            bound = getattr(getattr(host, "server", None), "address", ("", 0))[0]
            if started and host.active and bound == address:
                self._music_host_retry_owner = None
                LOGGER.info("Music LAN room listener replacement completed")
            return True
        finally:
            self._music_host_retry_in_progress = False
            if still_current():
                self.app._update_session_hud()

    def readiness(self):
        from core.network_invite import local_band_address

        address = local_band_address()
        return RoomShareReadiness(address, bool(
            address and self.role == "host" and not self.blocked
            and self.app.host_peer.active
            and getattr(getattr(self.app.host_peer, "server", None), "address", ("", 0))[0] == address
        ))

    def prepare_native(self, role):
        # Native callers reach this boundary only when starting a new owner,
        # after guarding an already active runtime or host constructor. A safe
        # retry needs a fresh conductor token so its Art connection can replace
        # the previous attempt's terminal failure without accepting late work.
        self.app._start_session_conductor_attempt(role)
        self.generation += 1
        self.role = role
        self.stopping = False
        self.native_source = None
        self.native_state = None
        self.native_applied = None
        self.native_generation = 0
        self.native_wait_started = 0.0
        self.probing = role == "guest"
        self.probe_failed = False
        if self.app.creator_profile.key == "art":
            self.state = ArtRoomState.STARTING
        self.app._conductor_setup_requested = True

    def receive_native(self, event, *, source):
        snapshot = getattr(source, "snapshot", None)
        if (source is not self.app._remote_session or self.blocked or snapshot is None
                or snapshot.generation != event.generation
                or snapshot.role.value != "guest"
                or type(event.room_state) is not RoomState):
            return
        if (source is self.native_source and event.generation == self.native_generation
                and self.native_state is not None
                and event.room_state.revision <= self.native_state.revision):
            return
        self.native_source = source
        self.native_generation = event.generation
        self.native_state = event.room_state
        self.native_received_at = time.monotonic()
        if snapshot.phase.value == "connected":
            self.apply_native(source, snapshot)

    def connected_native(self, source, snapshot):
        if self.blocked:
            return True
        if snapshot.role.value == "host":
            if self.app.creator_profile.key == "art":
                self.role = "host"
                self.state = ArtRoomState.CONNECTED
                self.app._refresh_readiness()
                return True
            return False
        self.role = "guest"
        if self.native_state is not None and self.native_source is source:
            self.apply_native(source, snapshot)
        if self.probing:
            if self.native_wait_started <= 0:
                self.native_wait_started = time.monotonic()
                generation = self.generation
                QTimer.singleShot(5000, lambda: self.check_native_timeout(source, snapshot.generation, generation))
            self.app._update_session_hud()
        # Authenticated transport alone cannot select Music or Art.
        return True

    def check_native_timeout(self, source, native_generation, generation):
        if (self.probing and source is self.app._remote_session
                and generation == self.generation and not self.blocked
                and getattr(source.snapshot, "generation", 0) == native_generation):
            from services.remote_session_runtime import RemoteSessionErrorCode
            source.mark_connection_lost(expected_generation=native_generation,
                                        error_code=RemoteSessionErrorCode.PEER_PROTOCOL_UNSUPPORTED)
            self.app._show_remote_session_failure(
                guest_enrollment=True, error_code=RemoteSessionErrorCode.PEER_PROTOCOL_UNSUPPORTED,
            )

    def apply_native(self, source, snapshot):
        state = self.native_state
        if (state is None or snapshot.generation != self.native_generation
                or source is not self.app._remote_session or self.blocked):
            return
        key = (source, snapshot.generation, state.revision)
        if key == self.native_applied:
            return
        if time.monotonic() - self.native_received_at >= 5.0:
            return
        self.native_applied = key
        self.probing = False
        self.borrowed_start = state.art_start_key
        self.app._apply_creator_profile_key(state.creator_profile_key, host_owned=True)
        if state.creator_profile_key == "art":
            self.state = ArtRoomState.CONNECTED
            self.app._remote_invitation = None
            self.observe_creative_state(state)
            self.app._refresh_readiness()
        else:
            self.state = ArtRoomState.NONE
            self.app._activate_remote_guest_route(snapshot, source=source)

    def observe_creative_state(self, state):
        for method, field in (("_reference_video_coordinator", "reference_video"),
                              ("_shared_canvas_coordinator", "shared_canvas"),
                              ("_room_clock_coordinator", "room_clock")):
            if hasattr(state, field):
                coordinator = getattr(self.app, method)()
                if coordinator is not None:
                    coordinator.observe_host_state(state)

    def host_publisher(self):
        if self.blocked:
            return None
        owner = self.app._remote_invite_owner
        if owner is None:
            return self.app.host_peer
        identity = getattr(owner, "room_identity", None)
        if identity is None:
            return None
        if self.publisher is None or not self.publisher.active:
            self.publisher = NativeRoomPublisher(self.app, owner)
        return self.publisher

    def host_connection_names(self) -> RoomConnectionNames | None:
        """Read private names only from this live Art LAN host, without caching."""
        host = self.app.host_peer
        server = getattr(host, "server", None)
        generation = self.generation
        lifecycle = getattr(host, "_lifecycle_generation", None)

        def current():
            return bool(
                not self.blocked and self.role == "host"
                and self.app.creator_profile.key == "art"
                and self.state in {ArtRoomState.WAITING, ArtRoomState.CONNECTED}
                and self.app._remote_invite_owner is None
                and self.app.host_peer is host and host.active
                and host.server is server and self.generation == generation
                and getattr(host, "_lifecycle_generation", None) == lifecycle
                and self._host_names_owner == (id(host), id(server), generation, lifecycle)
                and 0 <= time.monotonic() - self._host_names_observed_at < 5.0
            )

        read = getattr(server, "room_connection_names", None)
        if not callable(read) or not current():
            return None
        result = read()
        # Retiring or replacing an owner during the read wins over its payload.
        return result if current() and isinstance(result, RoomConnectionNames) else None

    def tick(self):
        self._host_names_owner = None
        if self.blocked:
            return
        if self.role == "host" and self.app.creator_profile.key == "art":
            owner = self.app._remote_invite_owner
            if owner is not None:
                publisher = self.host_publisher()
                if publisher is not None and publisher.revision == 0:
                    publisher.publish()
                phase = owner.snapshot.phase.value
                self.state = (ArtRoomState.CONNECTED if owner.connection_available
                              else ArtRoomState.FAILED if phase == "failed"
                              else ArtRoomState.WAITING)
            elif self.app.host_peer.active:
                host = self.app.host_peer
                server = host.server
                generation = self.generation
                lifecycle = getattr(host, "_lifecycle_generation", None)

                def current():
                    return bool(
                        not self.blocked and self.role == "host"
                        and self.app.creator_profile.key == "art"
                        and self.app._remote_invite_owner is None
                        and self.app.host_peer is host and host.active
                        and host.server is server and self.generation == generation
                        and getattr(host, "_lifecycle_generation", None) == lifecycle
                    )

                previous = self.state
                readiness = self.readiness()
                if current():
                    if readiness.shareable:
                        readers = server.room_participants()
                        if current():
                            self.state = ArtRoomState.CONNECTED if readers else ArtRoomState.WAITING
                            # Reuse this owner's normal route observation.
                            # Rendering never repeats OS interface discovery.
                            self._host_names_owner = (id(host), id(server), generation, lifecycle)
                            self._host_names_observed_at = time.monotonic()
                            if previous is ArtRoomState.RECONNECTING:
                                LOGGER.info("Art LAN room network route restored")
                    else:
                        # A retained listener with a missing/mismatched local
                        # route can recover. Ownership is not participant proof.
                        self.state = ArtRoomState.RECONNECTING
                        if previous is not ArtRoomState.RECONNECTING:
                            LOGGER.info("Art LAN room network interrupted")
            self.app._update_session_hud()

    def stop_lan(self):
        self.stopping = True
        self.generation += 1
        generation = self.generation
        owner = self.lan_guest
        if owner is not None and owner.stop() is False:
            return False
        if self.generation != generation or self.lan_guest is not owner:
            return False
        self.lan_guest = None
        self._lan_terminal_owner = None
        self.music_invite = None
        self._music_host_retry_owner = None
        self._music_network_owner = None
        self._music_network_interrupted = False
        self.probing = False
        self.probe_failed = False
        self.native_state = None
        self.native_source = None
        self.publisher = None
        self.borrowed_start = ""
        self.state = ArtRoomState.NONE
        self.role = ""
        return True

    def guidance(self):
        owner = self.app._remote_invite_owner
        if (self.role == "host" and owner is not None
                and getattr(getattr(owner.snapshot, "phase", None), "value", "") == "failed"):
            needs_update = getattr(getattr(owner.snapshot, "error_code", None), "value", "") == "peer_protocol_unsupported"
            return GuidanceDisplayOverride(
                "Update WebJam to reconnect" if needs_update else "The room connection ended",
                "Update WebJam on both computers. Choose End Room, then start a new room and copy its invitation."
                if needs_update else "Choose End Room to close this connection, then start a new room and copy its invitation.",
                SessionPrimaryAction.END_SESSION, "End Room",
            )
        if self.can_retry_lan:
            return GuidanceDisplayOverride(
                "The room could not open" if self.probe_failed else "The room connection ended",
                "Check that the host's room is open and you are on the same network, then choose Try Again.",
                SessionPrimaryAction.RETRY_SETUP, "Try Again",
            )
        if self.probing:
            return GuidanceDisplayOverride(
                "The room could not open" if self.probe_failed else "Contacting the host",
                "Ask the host for a fresh invitation, then choose Paste New Invite." if self.probe_failed else
                "WebJam is opening the room from your invitation.",
                SessionPrimaryAction.PASTE_NEW_INVITE if self.probe_failed else SessionPrimaryAction.WAIT,
            )
        if (self.role == "host" and self.state is ArtRoomState.RECONNECTING
                and self.app._remote_invite_owner is None):
            return GuidanceDisplayOverride(
                "Room network interrupted",
                "Check your Wi-Fi or local network, then choose Try Again.",
                SessionPrimaryAction.RETRY_SETUP, "Try Again",
            )
        if (self.role == "host" and self.state is ArtRoomState.FAILED
                and self.app._remote_invite_owner is None):
            return GuidanceDisplayOverride(
                "The room could not open", "Check that you are on the same Wi-Fi, then choose Try Again.",
                SessionPrimaryAction.RETRY_SETUP, "Try Again",
            )
        return None
