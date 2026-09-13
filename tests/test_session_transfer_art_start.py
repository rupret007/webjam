"""LAN Art guests observe the host's start without inventing a local one."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

import pytest

from core.session_transfer import (
    EnrollmentRegistry,
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionStateSnapshot,
    SessionTransferError,
    TransferAuthenticationError,
    TransferStore,
)


def _id():
    return str(uuid.uuid4())


@pytest.mark.parametrize("profile", ["art", "music", "podcast_voice", "review_rehearsal"])
def test_legacy_state_never_invents_an_art_start(profile):
    state = SessionPeerClient._parse_state({
        "session_id": _id(), "generation": 0, "signal": "idle",
        "creator_profile_key": profile,
    })

    assert state.art_start_key == ""


@pytest.mark.parametrize("start", [None, False, 1, [], {}, "paint_together", "Paint along", "paint_along "])
def test_art_start_rejects_noncanonical_wire_values(start):
    with pytest.raises(ValueError, match="art_start_key"):
        SessionPeerClient._parse_state({
            "session_id": _id(), "generation": 0, "signal": "idle",
            "creator_profile_key": "art", "art_start_key": start,
        })


@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
@pytest.mark.parametrize("start", ["talk_and_make", "paint_along"])
def test_an_art_start_cannot_turn_another_profile_into_art(profile, start):
    with pytest.raises(ValueError, match="art_start_key"):
        SessionStateSnapshot(
            _id(), 0, RecordingSignal.IDLE,
            creator_profile_key=profile, art_start_key=start,
        )


@pytest.mark.parametrize("start", ["talk_and_make", "paint_along"])
def test_start_survives_live_updates_but_is_never_restored_from_a_journal(tmp_path, start):
    session_id, take_id = _id(), _id()
    control = SessionControlState(
        tmp_path, session_id, creator_profile_key="art", art_start_key=start,
    )
    control.begin(take_id, started_utc="2026-09-12T12:00:00Z")
    control.publish_reference_video(
        state="ready", shared=True, duration_s=60.0, identity_digest="a" * 64,
    )
    control.finish(take_id, stopped_utc="2026-09-12T12:01:00Z")

    assert control.snapshot().art_start_key == start
    assert control.snapshot().reference_video.shared
    journal = json.loads(control.path.read_text(encoding="utf-8"))
    assert "art_start_key" not in journal
    # Even an older/foreign writer cannot turn a recording journal into the
    # owner of the next room's selected start.
    journal["art_start_key"] = start
    control.path.write_text(json.dumps(journal), encoding="utf-8")
    restarted = SessionControlState(tmp_path, session_id)
    assert restarted.snapshot().creator_profile_key == "art"
    assert restarted.snapshot().art_start_key == ""
    assert not restarted.snapshot().reference_video.shared
    fresh_owner = SessionControlState(
        tmp_path, session_id, creator_profile_key="art", art_start_key=start,
    )
    assert fresh_owner.snapshot().art_start_key == start


@pytest.mark.requires_local_socket
@pytest.mark.parametrize("start", ["talk_and_make", "paint_along", ""])
def test_authenticated_lan_guest_reads_but_cannot_choose_the_host_start(tmp_path, start):
    credentials = SessionCredentials.create()
    control = SessionControlState(
        tmp_path, credentials.session_id,
        creator_profile_key="art", art_start_key=start,
    )
    server = SessionPeerServer(
        "127.0.0.1", 0,
        registry=EnrollmentRegistry(tmp_path, credentials),
        control=control,
        transfers=TransferStore(tmp_path, credentials.session_id),
    )
    server.start()
    try:
        client = SessionPeerClient(*server.address, credentials=credentials, timeout_s=1)
        enrollment = client.enroll(_id(), "Mobile Artist")
        invalid = replace(enrollment, participant_token="z" * 43)
        with pytest.raises(TransferAuthenticationError):
            client.state(invalid)
        assert server.room_participants() == frozenset()

        raw = client._request(
            "GET", "/v1/state", token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
        )
        assert raw.get("art_start_key", "") == start
        assert ("art_start_key" in raw) == bool(start)
        assert client.state(enrollment).art_start_key == start
        assert server.room_participants() == frozenset({enrollment.participant_id})
        assert not control.snapshot().reference_video.shared

        with pytest.raises(SessionTransferError):
            client._request(
                "POST", "/v1/state", token=enrollment.participant_token,
                participant_id=enrollment.participant_id,
                body=b'{"art_start_key":"paint_along"}',
                headers={"Content-Type": "application/json"},
            )
        assert client.state(enrollment).art_start_key == start
    finally:
        server.stop()
        assert server.active_handler_count == 0
