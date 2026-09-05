"""Ephemeral, host-owned room facts for authenticated native peers.

This is a typed state channel, separate from optional Session help and audio.
It carries only the existing local-video and optional-canvas projections; no
file path, media bytes, meeting control, or room lookup travels here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import unicodedata
from dataclasses import dataclass, field

from core.remote_invitation import RemoteInvitation
from core.session_transfer import (
    ReferenceVideoPlaybackState,
    ReferenceVideoSessionSnapshot,
    SharedCanvasSessionSnapshot,
)

MAX_ROOM_STATE_BYTES = 8 * 1024
MAX_ROOM_REVISION = (1 << 53) - 1
_ROOM_FIELDS = frozenset({
    "schema", "revision", "creator_profile_key", "art_start_key",
    "reference_video", "shared_canvas",
})
_VIDEO_FIELDS = frozenset(ReferenceVideoSessionSnapshot().to_mapping())
_CANVAS_FIELDS = frozenset(SharedCanvasSessionSnapshot().to_mapping())
_PROFILES = frozenset({"music", "art", "podcast_voice", "review_rehearsal"})
_ART_STARTS = frozenset({"talk_and_make", "paint_along"})


def _invalid() -> ValueError:
    return ValueError("The room state is not supported.")


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise _invalid()
    if type(value.get("schema")) is not int or value["schema"] != 1:
        raise _invalid()
    return value


def _canonical_text(value: object) -> None:
    if not isinstance(value, str) or unicodedata.normalize("NFC", value) != value:
        raise _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class RoomState:
    """One full replacement snapshot; private payload is absent from repr."""

    revision: int
    creator_profile_key: str
    art_start_key: str = ""
    reference_video: ReferenceVideoSessionSnapshot = field(
        default_factory=ReferenceVideoSessionSnapshot
    )
    shared_canvas: SharedCanvasSessionSnapshot = field(
        default_factory=SharedCanvasSessionSnapshot
    )

    def __post_init__(self) -> None:
        if type(self.revision) is not int or not 1 <= self.revision <= MAX_ROOM_REVISION:
            raise _invalid()
        if type(self.creator_profile_key) is not str or self.creator_profile_key not in _PROFILES:
            raise _invalid()
        if type(self.art_start_key) is not str or (
            self.art_start_key not in _ART_STARTS if self.creator_profile_key == "art"
            else self.art_start_key != ""
        ):
            raise _invalid()
        if type(self.reference_video) is not ReferenceVideoSessionSnapshot:
            raise _invalid()
        if type(self.shared_canvas) is not SharedCanvasSessionSnapshot:
            raise _invalid()
        for value in (
            self.reference_video.source_display_name,
            self.shared_canvas.server_label,
            self.shared_canvas.session_label,
        ):
            _canonical_text(value)
        if self.creator_profile_key != "art" and (
            self.shared_canvas.shared
            or self.reference_video.state is not ReferenceVideoPlaybackState.IDLE
            or self.reference_video.needs_attention
        ):
            raise _invalid()
        if self.reference_video.shared and self.art_start_key != "paint_along":
            raise _invalid()
        try:
            encoded = json.dumps(self.to_mapping(), ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise _invalid() from None
        if len(encoded) > MAX_ROOM_STATE_BYTES:
            raise _invalid()

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": 1,
            "revision": self.revision,
            "creator_profile_key": self.creator_profile_key,
            "art_start_key": self.art_start_key,
            "reference_video": self.reference_video.to_mapping(),
            "shared_canvas": self.shared_canvas.to_mapping(),
        }

    @classmethod
    def from_mapping(cls, value: object) -> RoomState:
        try:
            raw = _exact_mapping(value, _ROOM_FIELDS)
            video_raw = _exact_mapping(raw["reference_video"], _VIDEO_FIELDS)
            canvas_raw = _exact_mapping(raw["shared_canvas"], _CANVAS_FIELDS)
            video = ReferenceVideoSessionSnapshot.from_mapping(video_raw)
            canvas = SharedCanvasSessionSnapshot.from_mapping(canvas_raw)
            # Existing projections normalize UI input. Wire data must already
            # have that canonical spelling, including URLs and display names.
            if video.to_mapping() != video_raw or canvas.to_mapping() != canvas_raw:
                raise _invalid()
            return cls(raw["revision"], raw["creator_profile_key"], raw["art_start_key"],
                       reference_video=video, shared_canvas=canvas)
        except (ValueError, TypeError, KeyError, OverflowError, UnicodeError):
            raise _invalid() from None

    def __repr__(self) -> str:
        return f"RoomState(revision={self.revision}, private=[redacted])"


@dataclass(frozen=True, slots=True, repr=False)
class RoomIdentity:
    """Purpose-separated, memory-only identity for existing video matching."""

    session_id: str
    session_key: str

    @classmethod
    def from_invitation(cls, invitation: RemoteInvitation) -> RoomIdentity:
        if not isinstance(invitation, RemoteInvitation):
            raise TypeError("invitation must be a RemoteInvitation")
        context = (b"WebJam/v3/room-identity/v1\x00" + invitation.session_reference
                   + invitation.invite_reference + invitation.host_spki_sha256)
        key = invitation.capability_for_enrollment()
        return cls(
            hmac.new(key, context + b"/id", hashlib.sha256).hexdigest(),
            hmac.new(key, context + b"/video-key", hashlib.sha256).hexdigest(),
        )

    def __repr__(self) -> str:
        return "RoomIdentity(private=[redacted])"
