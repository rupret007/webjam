from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from core.reference_video import session_identity_signer
from core.remote_invitation import issue_remote_invitation
from core.room_state import MAX_ROOM_REVISION, RoomIdentity, RoomState
from core.session_transfer import (
    ReferenceVideoSessionSnapshot,
    SharedCanvasSessionSnapshot,
)


def _state() -> RoomState:
    return RoomState(
        revision=1, creator_profile_key="art", art_start_key="paint_along",
        reference_video=ReferenceVideoSessionSnapshot(
            generation=3, playback_generation=7, state="paused", shared=True,
            source_display_name="Private lesson.mp4", identity_digest="a" * 64,
            duration_s=100.0, position_s=42.5,
        ),
        shared_canvas=SharedCanvasSessionSnapshot(
            generation=5, shared=True,
            join_url="drawpile://studio.example/room?p=private-password",
            server_label="studio.example", session_label="Private painting",
        ),
    )


def test_full_room_state_roundtrip_keeps_existing_video_and_canvas_owners() -> None:
    state = _state()
    assert RoomState.from_mapping(state.to_mapping()) == state
    assert state.reference_video.position_s == 42.5
    assert state.shared_canvas.join_url.endswith("private-password")
    for private in ("Private lesson", "Private painting", "private-password", "a" * 64):
        assert private not in repr(state)


@pytest.mark.parametrize("path,value", [
    (("schema",), True), (("schema",), 2), (("revision",), True),
    (("revision",), 0), (("revision",), MAX_ROOM_REVISION + 1),
    (("creator_profile_key",), "Art"), (("art_start_key",), "talk_and_make"),
    (("reference_video", "schema"), True),
    (("reference_video", "extra"), "private"),
    (("reference_video", "source_display_name"), "/private/movie.mp4"),
    (("reference_video", "source_display_name"), "  movie.mp4"),
    (("reference_video", "source_display_name"), "Cafe\u0301.mp4"),
    (("reference_video", "generation"), True),
    (("reference_video", "duration_s"), float("inf")),
    (("reference_video", "position_s"), 101),
    (("reference_video", "identity_digest"), "A" * 64),
    (("shared_canvas", "schema"), True),
    (("shared_canvas", "generation"), -1),
    (("shared_canvas", "join_url"), "https://evil.example/room"),
    (("shared_canvas", "join_url"), "drawpile://STUDIO.example/room"),
    (("shared_canvas", "session_label"), " Private painting"),
    (("shared_canvas", "session_label"), "Cafe\u0301"),
    (("shared_canvas", "shared"), 1),
    (("shared_canvas", "credential"), "private"),
])
def test_room_state_rejects_noncanonical_or_expanded_wire_contract(path, value) -> None:
    raw = deepcopy(_state().to_mapping())
    parent = raw
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ValueError, match="^The room state is not supported.$"):
        RoomState.from_mapping(raw)


@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
def test_non_art_profile_announcements_carry_no_art_media(profile) -> None:
    state = RoomState(1, profile)
    assert RoomState.from_mapping(state.to_mapping()) == state
    with pytest.raises(ValueError):
        replace(_state(), creator_profile_key=profile, art_start_key="")
    # A withdrawal may preserve the existing owners' generation fences.
    cleared = RoomState(2, profile, reference_video=ReferenceVideoSessionSnapshot(generation=9),
                        shared_canvas=SharedCanvasSessionSnapshot(generation=8))
    assert RoomState.from_mapping(cleared.to_mapping()) == cleared


def test_room_identity_agrees_on_both_sides_and_changes_for_replacement_invite() -> None:
    invite = issue_remote_invitation("reference-local", allowed_profiles={"reference-local"},
                                     host_spki_sha256=b"h" * 32).invitation
    host = RoomIdentity.from_invitation(invite)
    guest = RoomIdentity.from_invitation(invite)
    assert host == guest
    assert len(host.session_key) == 64
    assert host.session_id != host.session_key
    assert host.session_key != invite.capability_for_enrollment().hex()
    signer = session_identity_signer(session_id=host.session_id, session_key=host.session_key)
    guest_signer = session_identity_signer(session_id=guest.session_id, session_key=guest.session_key)
    assert signer("b" * 64) == guest_signer("b" * 64)
    replacement = issue_remote_invitation(
        "reference-local", allowed_profiles={"reference-local"}, host_spki_sha256=b"h" * 32,
        session_reference=invite.session_reference,
    ).invitation
    other = RoomIdentity.from_invitation(replacement)
    assert host.session_id != other.session_id
    assert host.session_key != other.session_key
    assert host.session_id not in repr(host)
    assert host.session_key not in repr(host)
