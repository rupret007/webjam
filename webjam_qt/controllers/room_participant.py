"""Memory-only room participation beside the existing audio owners.

A LAN observer discovers the authenticated host profile before constructing a
recording guest. Native peers carry the same creative projections over their
already authenticated connection. Neither path manufactures audio evidence.
"""
from __future__ import annotations

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
    SharedCanvasSessionSnapshot,
)


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

    @property
    def active(self):
        return (self.owner is self.app._remote_invite_owner
                and self.identity is not None
                and self.identity == self.owner.room_identity)

    def publish(self):
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
            shared_canvas=self.canvas if profile == "art" else SharedCanvasSessionSnapshot(),
        )
        return self.owner.publish_room_state(state)

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

    def publish_shared_canvas_state(self, **values):
        for name in ("server_label", "session_label"):
            if name in values:
                values[name] = unicodedata.normalize("NFC", values[name])
        self.canvas = SharedCanvasSessionSnapshot(
            generation=self.canvas.generation + 1, **values,
        )
        self.publish()
        return self.canvas


class RoomParticipantController:
    """One current room binding; queued callbacks must prove their owner."""

    def __init__(self, app):
        self.app = app
        self.lan_guest = None
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
    def blocked(self):
        return bool(self.stopping or self.app._shutdown or self.app.audio.stopping
                    or self.app.audio.cleanup_retry_required)

    def start_lan_guest(self, invite):
        if self.lan_guest is not None:
            return True
        from services.lan_room_guest import LanRoomGuest

        self.generation += 1
        generation = self.generation
        self.role = "guest"
        self.probing = True
        self.probe_failed = False
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
        self.app._update_session_hud()
        return True

    def receive_lan(self, owner, generation, state):
        if (owner is not self.lan_guest or generation != self.generation
                or self.blocked):
            return
        profile = canonical_creator_profile_key(state.creator_profile_key)
        if profile is None:
            self.lose_lan(owner, generation, True)
            return
        if profile != "art":
            invite = owner.invite
            # Stop the observer before constructing the existing recording
            # owner. No two workers can enroll this client concurrently.
            if owner.stop() is False:
                self.app.audio.require_cleanup_retry(
                    hosting=False, error="The previous room connection is still closing.",
                    title="Finish leaving the room", detail="Choose Try Leave Room, then open the invitation again.",
                )
                return
            self.lan_guest = None
            self.probing = False
            self.state = ArtRoomState.NONE
            if not self.app._configure_guest_peer(invite):
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
        self.probe_failed = bool(terminal and self.probing)
        if not self.probing:
            self.state = ArtRoomState.FAILED if terminal else ArtRoomState.RECONNECTING
        self.app._update_session_hud()

    def start_lan_host(self):
        from core.network_invite import local_band_address

        self.role = "host"
        self.stopping = False
        self.app._conductor_setup_requested = True
        self.app._start_session_conductor_attempt("host")
        self.state = ArtRoomState.STARTING
        address = local_band_address()
        host = self.app.host_peer
        bound = getattr(getattr(host, "server", None), "address", ("", 0))[0]
        if host.active and bound != address:
            # The artist explicitly chose Try Again after an interface change.
            # Retain a failed listener and its retry rather than overwrite it.
            if host.stop() is False:
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
        self.state = (ArtRoomState.WAITING if address and self.app._ensure_host_peer(address)
                      else ArtRoomState.FAILED)
        self.app._refresh_readiness()
        return True

    def readiness(self):
        from core.network_invite import local_band_address

        address = local_band_address()
        return RoomShareReadiness(address, bool(
            address and self.role == "host" and not self.stopping
            and self.app.host_peer.active
            and getattr(getattr(self.app.host_peer, "server", None), "address", ("", 0))[0] == address
        ))

    def prepare_native(self, role):
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

    def tick(self):
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
                readers = self.app.host_peer.server.room_participants()
                self.state = (ArtRoomState.CONNECTED if readers else ArtRoomState.WAITING
                              ) if self.readiness().shareable else ArtRoomState.FAILED
            self.app._update_session_hud()

    def stop_lan(self):
        self.stopping = True
        self.generation += 1
        owner = self.lan_guest
        if owner is not None and owner.stop() is False:
            return False
        self.lan_guest = None
        self.music_invite = None
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
        if self.probing:
            return GuidanceDisplayOverride(
                "The room could not open" if self.probe_failed else "Contacting the host",
                "Ask the host for a fresh invitation, then choose Paste New Invite." if self.probe_failed else
                "WebJam is opening the room from your invitation.",
                SessionPrimaryAction.PASTE_NEW_INVITE if self.probe_failed else SessionPrimaryAction.WAIT,
            )
        if (self.role == "host" and self.state is ArtRoomState.FAILED
                and self.app._remote_invite_owner is None):
            return GuidanceDisplayOverride(
                "The room could not open", "Check that you are on the same Wi-Fi, then choose Try Again.",
                SessionPrimaryAction.RETRY_SETUP, "Try Again",
            )
        return None
