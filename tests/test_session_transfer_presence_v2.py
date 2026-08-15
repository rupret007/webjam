from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path

import pytest

from core.network_invite import BandInvite
from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS
from core.session_transfer import (
    EnrollmentRegistry,
    LocalOriginalObligation,
    PresenceBinding,
    PresenceV2Challenge,
    PresenceV2Proof,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionTransferError,
    TransferConflictError,
    TransferStore,
)
from core.session_transfer_runtime import (
    GuestPeerSession,
    HostPeerSession,
    _DesiredPresenceV2,
)
from core.take_project import (
    Participant,
    ProjectStatus,
    TakeProject,
    load_take_project,
    write_take_project,
)


def _id() -> str:
    return str(uuid.uuid4())


def _digest(label: str = "ordered-roster") -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _install(
    registry: EnrollmentRegistry,
    digest: str,
    count: int,
    *,
    process_generation: int = 11,
    rpc_connection_generation: int = 12,
    audio_connection_generation: int = 13,
    host_roster_fingerprint: str | None = None,
    ambiguous_ordinals: tuple[int, ...] = (),
    force_rotate: bool = False,
) -> PresenceV2Challenge:
    return registry.install_presence_v2_roster(
        digest,
        count,
        host_roster_fingerprint=(
            host_roster_fingerprint or _digest("host-roster-fingerprint")
        ),
        ambiguous_ordinals=ambiguous_ordinals,
        process_generation=process_generation,
        rpc_connection_generation=rpc_connection_generation,
        audio_connection_generation=audio_connection_generation,
        force_rotate=force_rotate,
    )


def _bind(
    registry: EnrollmentRegistry,
    participant_id: str,
    challenge: PresenceV2Challenge,
    *,
    ordinal: int,
    presence_generation: int,
    display_name: str = "Alex",
    capture_enabled: bool = True,
    process_generation: int = 21,
    rpc_connection_generation: int = 22,
    audio_connection_generation: int = 23,
    local_original_track_count: int | None = None,
    local_original_map_fingerprint: str = "",
) -> PresenceV2Proof:
    return registry.bind_presence_v2(
        participant_id,
        display_name,
        ordered_roster_digest=challenge.ordered_roster_digest,
        roster_count=challenge.roster_count,
        self_ordinal=ordinal,
        process_generation=process_generation,
        rpc_connection_generation=rpc_connection_generation,
        audio_connection_generation=audio_connection_generation,
        challenge=challenge.challenge,
        challenge_epoch=challenge.challenge_epoch,
        topology_epoch=challenge.topology_epoch,
        presence_generation=presence_generation,
        capture_enabled=capture_enabled,
        local_original_track_count=local_original_track_count,
        local_original_map_fingerprint=local_original_map_fingerprint,
    )


@pytest.fixture
def peer(tmp_path: Path):
    credentials = SessionCredentials.create()
    root = tmp_path / "host"
    registry = EnrollmentRegistry(root, credentials)
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=SessionControlState(root, credentials.session_id),
        transfers=TransferStore(root, credentials.session_id),
    )
    server.start()
    client = SessionPeerClient("127.0.0.1", server.address[1], credentials=credentials)
    try:
        yield credentials, registry, server, client
    finally:
        server.stop()


# A clock value where ``(now + 15.0) - now`` exceeds 15.0 by one float ulp.
# ``math.ceil`` amplifies that sub-nanosecond artifact into one extra whole
# millisecond — the exact 15 001 ms vs 15 000 ms pair observed in the flaky
# tag-CI failure — unless the registry caps the report at the granted lease.
_ULP_ARTIFACT_NOW = 4086.3516785314346


@pytest.fixture
def frozen_peer(tmp_path: Path):
    """A peer whose registry clock is frozen at the ulp-artifact instant.

    HTTP round-trip equality assertions must not depend on how much wall
    time elapses between minting a challenge and fetching it; the frozen
    clock makes the comparison deterministic while still exercising the
    float-artifact regression.
    """

    credentials = SessionCredentials.create()
    root = tmp_path / "host"
    registry = EnrollmentRegistry(
        root,
        credentials,
        presence_clock=lambda: _ULP_ARTIFACT_NOW,
    )
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=SessionControlState(root, credentials.session_id),
        transfers=TransferStore(root, credentials.session_id),
    )
    server.start()
    client = SessionPeerClient("127.0.0.1", server.address[1], credentials=credentials)
    try:
        yield credentials, registry, server, client
    finally:
        server.stop()


def test_two_client_local_channel_zero_bind_to_distinct_server_ordinals(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    first = registry.enroll(_id(), "Alex", invite_token=credentials.invite_token)
    second = registry.enroll(_id(), "Alex", invite_token=credentials.invite_token)

    # Both owned clients report channel 0 in their private RPC namespaces.
    # The legacy binding remains compatible, but cannot represent both and is
    # explicitly ineligible for recorder ownership.
    first_v1 = registry.bind_presence(
        first.participant_id, 0, "Alex", generation=1, capture_enabled=True
    )
    second_v1 = registry.bind_presence(
        second.participant_id, 0, "Alex", generation=1, capture_enabled=True
    )
    assert first_v1.recorder_eligible is False
    assert second_v1.protocol_version == 1
    assert registry.presence_for_participant(first.participant_id) is None
    assert registry.presence_for_channel(0) == second_v1

    challenge = _install(registry, _digest(), 2)
    first_v2 = _bind(
        registry, first.participant_id, challenge, ordinal=0, presence_generation=10
    )
    second_v2 = _bind(
        registry, second.participant_id, challenge, ordinal=1, presence_generation=20
    )

    assert first_v2.recorder_eligible is True
    assert first_v2.protocol_version == 2
    assert registry.recording_presence_snapshot() == (first_v2, second_v2)
    assert all(
        "channel_id" not in asdict(proof)
        for proof in registry.recording_presence_snapshot()
    )


def test_duplicate_or_changed_ordinal_claims_fail_closed(tmp_path: Path) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    first = registry.enroll(_id(), "First", invite_token=credentials.invite_token)
    second = registry.enroll(_id(), "Second", invite_token=credentials.invite_token)
    challenge = _install(registry, _digest(), 2)
    _bind(registry, first.participant_id, challenge, ordinal=0, presence_generation=1)

    # A first claimant cannot retain the ordinal after a second enrolled peer
    # collides with it. Neither cooperative claim can be distinguished, so the
    # topology remains tombstoned until the host proves a topology change.
    with pytest.raises(TransferConflictError, match="conflicting enrolled"):
        _bind(
            registry,
            second.participant_id,
            challenge,
            ordinal=0,
            presence_generation=2,
        )
    assert registry.recording_presence_snapshot() == ()
    with pytest.raises(TransferConflictError, match="topology conflict"):
        _bind(
            registry,
            first.participant_id,
            challenge,
            ordinal=1,
            presence_generation=3,
        )
    with pytest.raises(TransferConflictError, match="topology conflict"):
        _bind(
            registry,
            second.participant_id,
            challenge,
            ordinal=0,
            presence_generation=4,
        )

    rotated = _install(registry, _digest(), 2, audio_connection_generation=14)
    rebound = _bind(
        registry,
        first.participant_id,
        rotated,
        ordinal=0,
        presence_generation=5,
    )
    assert registry.recording_presence_snapshot() == (rebound,)


def test_one_participant_changing_ordinal_poisoned_until_topology_change(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    musician = registry.enroll(_id(), "Musician", invite_token=credentials.invite_token)
    challenge = _install(registry, _digest(), 2)
    _bind(
        registry,
        musician.participant_id,
        challenge,
        ordinal=0,
        presence_generation=1,
    )

    with pytest.raises(TransferConflictError, match="conflicting roster ordinal"):
        _bind(
            registry,
            musician.participant_id,
            challenge,
            ordinal=1,
            presence_generation=2,
        )
    assert registry.recording_presence_snapshot() == ()


def test_challenge_rotation_replay_and_generation_staleness_fail_closed(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    musician = registry.enroll(_id(), "Musician", invite_token=credentials.invite_token)
    first_challenge = _install(registry, _digest(), 1)
    _bind(
        registry,
        musician.participant_id,
        first_challenge,
        ordinal=0,
        presence_generation=100,
    )

    with pytest.raises(TransferConflictError, match="stale or replayed"):
        _bind(
            registry,
            musician.participant_id,
            first_challenge,
            ordinal=0,
            presence_generation=100,
        )

    # An identical proven roster is idempotent; a connection-generation change
    # rotates the challenge and immediately invalidates prior claims.
    assert _install(registry, _digest(), 1) == first_challenge
    second_challenge = _install(registry, _digest(), 1, audio_connection_generation=14)
    assert second_challenge.challenge_epoch == first_challenge.challenge_epoch + 1
    assert second_challenge.challenge != first_challenge.challenge
    assert registry.recording_presence_snapshot() == ()

    with pytest.raises(TransferConflictError, match="challenge is stale") as stale:
        _bind(
            registry,
            musician.participant_id,
            first_challenge,
            ordinal=0,
            presence_generation=101,
        )
    assert first_challenge.challenge not in str(stale.value)
    with pytest.raises(TransferConflictError, match="stale or replayed"):
        _bind(
            registry,
            musician.participant_id,
            second_challenge,
            ordinal=0,
            presence_generation=100,
        )
    rebound = _bind(
        registry,
        musician.participant_id,
        second_challenge,
        ordinal=0,
        presence_generation=101,
    )
    assert registry.recording_presence_snapshot(
        ordered_roster_digest=second_challenge.ordered_roster_digest,
        roster_count=1,
        challenge=second_challenge.challenge,
        challenge_epoch=second_challenge.challenge_epoch,
    ) == (rebound,)
    assert registry.recording_presence_snapshot(challenge="x" * 43) == ()


def test_expired_challenge_clears_claims_and_cannot_be_replayed(
    tmp_path: Path,
) -> None:
    now = [100.0]
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(
        tmp_path,
        credentials,
        presence_clock=lambda: now[0],
        presence_v2_lease_s=2.0,
    )
    musician = registry.enroll(_id(), "Musician", invite_token=credentials.invite_token)
    first = _install(registry, _digest(), 1)
    _bind(
        registry,
        musician.participant_id,
        first,
        ordinal=0,
        presence_generation=1,
    )
    now[0] = 102.01
    assert registry.recording_presence_snapshot() == ()
    with pytest.raises(TransferConflictError, match="challenge is stale"):
        _bind(
            registry,
            musician.participant_id,
            first,
            ordinal=0,
            presence_generation=2,
        )

    refreshed = registry.current_presence_v2_challenge()
    assert refreshed.challenge_epoch == first.challenge_epoch + 1
    assert refreshed.lease_ms == 2000


def test_fresh_challenge_never_reports_more_than_granted_lease(
    tmp_path: Path,
) -> None:
    """A one-ulp float artifact must not inflate the reported lease.

    ``(now + lease) - now`` can exceed the lease by one ulp of ``now``;
    ``math.ceil`` would amplify that into one extra whole millisecond (the
    15 001 ms tag-CI observation).  The reported lease is capped at the
    granted total and only ever decreases while the same epoch ages.
    """

    now = [_ULP_ARTIFACT_NOW]
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials, presence_clock=lambda: now[0])
    created = _install(registry, _digest(), 1)
    assert created.lease_ms == 15_000

    now[0] += 0.0004  # under one elapsed millisecond: ceil rounds back up
    aged = registry.current_presence_v2_challenge()
    assert aged.challenge == created.challenge
    assert aged.lease_ms == 15_000

    now[0] += 0.0011  # past one elapsed millisecond: strictly decreasing
    older = registry.current_presence_v2_challenge()
    assert older.challenge == created.challenge
    assert 1 <= older.lease_ms < 15_000


def test_private_host_roster_fingerprint_rotates_identical_public_roster(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    # The byte-identical-refresh assertion below compares whole challenges,
    # including the remaining lease; a frozen clock keeps that deterministic.
    registry = EnrollmentRegistry(tmp_path, credentials, presence_clock=lambda: 100.0)
    musician = registry.enroll(
        _id(), "Same Name", invite_token=credentials.invite_token
    )
    first = _install(
        registry,
        _digest("identical-public-profile-rows"),
        2,
        host_roster_fingerprint=_digest("ordered-local-ids-a"),
    )
    _bind(
        registry,
        musician.participant_id,
        first,
        ordinal=1,
        presence_generation=1,
    )

    second = _install(
        registry,
        first.ordered_roster_digest,
        2,
        host_roster_fingerprint=_digest("ordered-local-ids-b"),
    )
    assert second.topology_epoch == first.topology_epoch + 1
    assert second.challenge_epoch == first.challenge_epoch + 1
    assert registry.recording_presence_snapshot() == ()
    with pytest.raises(TransferConflictError, match="challenge is stale"):
        _bind(
            registry,
            musician.participant_id,
            first,
            ordinal=1,
            presence_generation=2,
        )
    # Byte-identical refresh is idempotent; an ordinary callback is not a
    # topology change and does not churn the authorization tuple.
    assert (
        _install(
            registry,
            second.ordered_roster_digest,
            2,
            host_roster_fingerprint=_digest("ordered-local-ids-b"),
        )
        == second
    )


def test_unchanged_roster_rolls_leases_without_partial_snapshot_gap(
    tmp_path: Path,
) -> None:
    now = [100.0]
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(
        tmp_path,
        credentials,
        presence_clock=lambda: now[0],
        presence_v2_lease_s=2.0,
    )
    host_enrollment = registry.enroll(
        _id(), "Host", invite_token=credentials.invite_token
    )
    guest_enrollment = registry.enroll(
        _id(), "Guest", invite_token=credentials.invite_token
    )
    fingerprint = _digest("host-private-roster")
    digest = _digest("common-roster")
    host = HostPeerSession()
    host.registry = registry
    host.host_enrollment = host_enrollment
    challenge = host.install_recording_presence_roster(
        digest,
        2,
        self_ordinal=0,
        host_roster_fingerprint=fingerprint,
        ambiguous_ordinals=(),
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    assert challenge is not None
    host.bind_host_recording_presence(
        "Host",
        ordered_roster_digest=digest,
        roster_count=2,
        self_ordinal=0,
        host_roster_fingerprint=fingerprint,
        ambiguous_ordinals=(),
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
        challenge=challenge.challenge,
        challenge_epoch=challenge.challenge_epoch,
        topology_epoch=challenge.topology_epoch,
        presence_generation=1,
        capture_enabled=True,
    )
    guest_generation = 10
    _bind(
        registry,
        guest_enrollment.participant_id,
        challenge,
        ordinal=1,
        presence_generation=guest_generation,
        display_name="Guest",
    )
    assert {proof.participant_id for proof in host.recording_presence_snapshot()} == {
        host_enrollment.participant_id,
        guest_enrollment.participant_id,
    }

    for _cycle in range(2):
        now[0] += 1.1
        pending = registry.current_presence_v2_challenge()
        assert pending.topology_epoch == challenge.topology_epoch
        assert pending.challenge_epoch > challenge.challenge_epoch
        # Host renewal fills the pending epoch, while the prior complete
        # snapshot remains active until the guest also renews.
        overlap = host.recording_presence_snapshot()
        assert {proof.participant_id for proof in overlap} == {
            host_enrollment.participant_id,
            guest_enrollment.participant_id,
        }
        guest_generation += 1
        _bind(
            registry,
            guest_enrollment.participant_id,
            pending,
            ordinal=1,
            presence_generation=guest_generation,
            display_name="Guest",
        )
        promoted = host.recording_presence_snapshot()
        assert {proof.challenge for proof in promoted} == {pending.challenge}
        assert {proof.participant_id for proof in promoted} == {
            host_enrollment.participant_id,
            guest_enrollment.participant_id,
        }
        challenge = pending

    # A disconnected guest does not receive an unbounded overlap. Once the
    # old hard lease ends, only the freshly renewed host remains eligible.
    now[0] += 1.1
    pending = registry.current_presence_v2_challenge()
    assert len(host.recording_presence_snapshot()) == 2
    now[0] += 1.0
    after_disconnect = host.recording_presence_snapshot()
    assert tuple(proof.participant_id for proof in after_disconnect) == (
        host_enrollment.participant_id,
    )
    assert registry.recording_presence_missing_participant_ids() == (
        guest_enrollment.participant_id,
    )
    with pytest.raises(TransferConflictError, match="challenge is stale"):
        _bind(
            registry,
            guest_enrollment.participant_id,
            challenge,
            ordinal=1,
            presence_generation=guest_generation + 1,
            display_name="Guest",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("process_generation", 0),
        ("rpc_connection_generation", 0),
        ("audio_connection_generation", 0),
        ("self_ordinal", 2),
        ("ordered_roster_digest", "not-a-digest"),
        ("capture_enabled", "yes"),
        ("presence_generation", "1"),
        ("roster_count", 2.0),
        ("challenge", 123),
        ("ordered_roster_digest", 456),
        ("topology_epoch", float("inf")),
        ("protocol_version", 2.0),
    ),
)
def test_malformed_v2_proof_fields_are_rejected(field: str, value: object) -> None:
    values = {
        "participant_id": _id(),
        "display_name": "Alex",
        "ordered_roster_digest": _digest(),
        "roster_count": 2,
        "self_ordinal": 0,
        "process_generation": 1,
        "rpc_connection_generation": 2,
        "audio_connection_generation": 3,
        "challenge": "c" * 43,
        "challenge_epoch": 1,
        "topology_epoch": 1,
        "presence_generation": 1,
        "capture_enabled": True,
    }
    values[field] = value
    with pytest.raises(ValueError):
        PresenceV2Proof(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("ordered_roster_digest", 1),
        ("challenge", 1),
        ("challenge_epoch", "1"),
        ("topology_epoch", 1.0),
        ("lease_ms", "15000"),
        ("protocol_version", 2.0),
    ),
)
def test_malformed_v2_challenge_types_are_rejected(field: str, value: object) -> None:
    values = {
        "ordered_roster_digest": _digest(),
        "roster_count": 1,
        "challenge": "c" * 43,
        "challenge_epoch": 1,
        "topology_epoch": 1,
        "lease_ms": 15_000,
    }
    values[field] = value
    with pytest.raises(ValueError):
        PresenceV2Challenge(**values)


def test_presence_v2_reuses_canonical_256_row_roster_limit(tmp_path: Path) -> None:
    assert MAX_JAMULUS_ROSTER_ROWS == 256
    challenge = PresenceV2Challenge(
        ordered_roster_digest=_digest(),
        roster_count=MAX_JAMULUS_ROSTER_ROWS,
        challenge="c" * 43,
        challenge_epoch=1,
        topology_epoch=1,
        lease_ms=15_000,
    )
    assert challenge.roster_count == MAX_JAMULUS_ROSTER_ROWS
    with pytest.raises(ValueError, match="supported limit"):
        PresenceV2Challenge(
            ordered_roster_digest=_digest(),
            roster_count=MAX_JAMULUS_ROSTER_ROWS + 1,
            challenge="c" * 43,
            challenge_epoch=1,
            topology_epoch=1,
            lease_ms=15_000,
        )

    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    assert (
        _install(
            registry, _digest("maximum-roster"), MAX_JAMULUS_ROSTER_ROWS
        ).roster_count
        == MAX_JAMULUS_ROSTER_ROWS
    )
    with pytest.raises(ValueError, match="supported limit"):
        _install(
            registry,
            _digest("oversized-roster"),
            MAX_JAMULUS_ROSTER_ROWS + 1,
        )


def test_v2_is_memory_only_and_repr_and_wire_shape_are_privacy_bounded(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    enrollment = registry.enroll(
        _id(), "Private Musician", invite_token=credentials.invite_token
    )
    challenge = _install(registry, _digest("private-roster"), 1)
    proof = _bind(
        registry,
        enrollment.participant_id,
        challenge,
        ordinal=0,
        presence_generation=7,
        display_name="Private Musician",
    )

    saved = registry.path.read_text(encoding="utf-8")
    for forbidden in (
        challenge.challenge,
        challenge.ordered_roster_digest,
        _digest("host-roster-fingerprint"),
        credentials.invite_token,
        enrollment.participant_token,
        "self_ordinal",
        "process_generation",
        "rpc_connection_generation",
        "audio_connection_generation",
        "challenge_epoch",
        "topology_epoch",
        "host_roster_fingerprint",
    ):
        assert forbidden not in saved
    assert challenge.challenge not in repr(challenge)
    assert challenge.ordered_roster_digest not in repr(challenge)
    assert enrollment.participant_id not in repr(proof)
    assert proof.display_name not in repr(proof)
    legacy = PresenceBinding(
        enrollment.participant_id,
        0,
        "Private Musician",
        1,
        True,
    )
    desired = _DesiredPresenceV2(
        display_name="Private Musician",
        ordered_roster_digest=challenge.ordered_roster_digest,
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
        capture_enabled=True,
    )
    assert enrollment.participant_id not in repr(legacy)
    assert legacy.display_name not in repr(legacy)
    assert desired.display_name not in repr(desired)
    assert desired.ordered_roster_digest not in repr(desired)

    wire = asdict(proof)
    assert set(wire).isdisjoint(
        {
            "channel_id",
            "profile",
            "address",
            "pid",
            "process_id",
            "token",
            "participant_token",
            "invite_token",
            "installation_id",
        }
    )
    reopened = EnrollmentRegistry(tmp_path, credentials)
    assert reopened.recording_presence_snapshot() == ()
    assert reopened.presence_for_participant(enrollment.participant_id) is None


def test_http_v2_requires_complete_authenticated_payload_and_round_trips(
    frozen_peer,
) -> None:
    credentials, registry, _server, client = frozen_peer
    enrollment = client.enroll(_id(), "Alex")
    host_challenge = _install(registry, _digest(), 1)
    fetched = client.presence_v2_challenge(enrollment)
    # The frozen clock sits on the ulp artifact; without the granted-lease
    # cap both sides would report 15 001 ms on a 15 000 ms grant.
    assert fetched.lease_ms == 15_000
    assert fetched == host_challenge

    proof = client.bind_presence_v2(
        enrollment,
        display_name="Alex",
        ordered_roster_digest=fetched.ordered_roster_digest,
        roster_count=fetched.roster_count,
        self_ordinal=0,
        process_generation=4,
        rpc_connection_generation=5,
        audio_connection_generation=6,
        challenge=fetched.challenge,
        challenge_epoch=fetched.challenge_epoch,
        topology_epoch=fetched.topology_epoch,
        presence_generation=7,
        capture_enabled=True,
    )
    assert registry.recording_presence_snapshot() == (proof,)

    incomplete = asdict(proof)
    incomplete.pop("audio_connection_generation")
    with pytest.raises(SessionTransferError):
        client._request(
            "POST",
            "/v2/presence",
            token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
            body=json.dumps(incomplete).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    for field, value in (
        ("presence_generation", "8"),
        ("self_ordinal", 0.0),
        ("roster_count", "1"),
        ("ordered_roster_digest", 123),
        ("challenge", 123),
        ("topology_epoch", float("nan")),
        ("protocol_version", 2.0),
    ):
        malformed = asdict(proof)
        malformed[field] = value
        with pytest.raises(SessionTransferError):
            client._request(
                "POST",
                "/v2/presence",
                token=enrollment.participant_token,
                participant_id=enrollment.participant_id,
                body=json.dumps(malformed).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
    assert registry.recording_presence_snapshot() == (proof,)


def test_guest_runtime_publishes_v2_only_from_explicit_ordered_observation(
    tmp_path: Path,
    peer,
) -> None:
    credentials, registry, server, _client = peer
    digest = _digest()
    first_challenge = _install(registry, digest, 1)
    invite = BandInvite(
        "127.0.0.1",
        22124,
        "Test",
        credentials.session_id,
        server.address[1],
        credentials.invite_token,
    )
    guest = GuestPeerSession(
        invite,
        display_name="Alex",
        takes_root=tmp_path / "guest",
        installation_path=tmp_path / "guest-installation.json",
        capture_enabled=lambda: True,
        capture_config=lambda: (0, 48_000, 128),
    )

    guest.poll_once()
    assert registry.recording_presence_snapshot() == ()
    guest.observe_presence(0, "Alex")
    guest.poll_once()
    assert registry.presence_for_channel(0) is not None
    assert registry.recording_presence_snapshot() == ()

    guest.observe_presence_v2(
        "Alex",
        ordered_roster_digest=digest,
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    guest.poll_once()
    (first_proof,) = registry.recording_presence_snapshot()
    assert first_proof.challenge == first_challenge.challenge
    assert first_proof.capture_enabled is True
    assert first_proof.local_original_track_count == 2
    assert len(first_proof.local_original_map_fingerprint) == 64
    guest.observe_presence_v2(
        "Alex",
        ordered_roster_digest=digest,
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    guest.poll_once()
    assert registry.recording_presence_snapshot() == (first_proof,)

    # A new host audio connection is a new topology epoch. Cached local proof
    # is retired and cannot be re-signed until another exact RPC observation.
    second_challenge = _install(registry, digest, 1, audio_connection_generation=14)
    guest.poll_once()
    assert registry.recording_presence_snapshot() == ()
    assert "fresh local" in guest.last_presence_v2_error
    guest.observe_presence_v2(
        "Alex",
        ordered_roster_digest=digest,
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    guest.poll_once()
    (second_proof,) = registry.recording_presence_snapshot()
    assert second_proof.challenge == second_challenge.challenge
    assert second_proof.presence_generation > first_proof.presence_generation

    guest.observe_presence_v2(
        "Alex",
        ordered_roster_digest=digest,
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
        capture_enabled=False,
    )
    guest.poll_once()
    assert registry.recording_presence_snapshot()[0].capture_enabled is False

    guest.invalidate_recording_presence()
    third_challenge = _install(
        registry,
        digest,
        1,
        audio_connection_generation=14,
        host_roster_fingerprint=_digest("new-identical-profile-order"),
    )
    assert third_challenge.topology_epoch > second_challenge.topology_epoch
    guest.poll_once()
    assert registry.recording_presence_snapshot() == ()


def test_v2_presence_failure_does_not_starve_recording_state_poll(
    tmp_path: Path,
    peer,
) -> None:
    credentials, registry, server, _client = peer
    _install(registry, _digest("host-roster"), 1)
    take_id = _id()
    server.control.begin(take_id, started_utc="2026-08-03T12:00:00Z")
    invite = BandInvite(
        "127.0.0.1",
        22124,
        "Test",
        credentials.session_id,
        server.address[1],
        credentials.invite_token,
    )
    guest = GuestPeerSession(
        invite,
        display_name="Alex",
        takes_root=tmp_path / "guest-mismatch",
        installation_path=tmp_path / "guest-mismatch-installation.json",
        capture_enabled=lambda: False,
        capture_config=lambda: (0, 48_000, 128),
    )
    guest.observe_presence_v2(
        "Alex",
        ordered_roster_digest=_digest("different-guest-roster"),
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )

    state = guest.poll_once()
    assert state.signal.value == "recording"
    assert state.take_id == take_id
    assert guest.last_state == state
    assert "rosters do not match" in guest.last_presence_v2_error
    assert registry.recording_presence_snapshot() == ()


def test_host_runtime_snapshot_never_promotes_legacy_presence(tmp_path: Path) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    host_enrollment = registry.enroll(
        _id(), "Host", invite_token=credentials.invite_token
    )
    host = HostPeerSession()
    host.registry = registry
    host.host_enrollment = host_enrollment
    legacy = registry.bind_presence(
        host_enrollment.participant_id,
        0,
        "Host",
        generation=1,
        capture_enabled=True,
    )
    assert legacy.recorder_eligible is False
    assert host.recording_presence_snapshot() == ()

    challenge = host.install_recording_presence_roster(
        _digest(),
        1,
        self_ordinal=0,
        host_roster_fingerprint=_digest("host-roster-fingerprint"),
        ambiguous_ordinals=(),
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    assert challenge is not None
    proof = host.bind_host_recording_presence(
        "Host",
        ordered_roster_digest=challenge.ordered_roster_digest,
        roster_count=challenge.roster_count,
        self_ordinal=0,
        host_roster_fingerprint=_digest("host-roster-fingerprint"),
        ambiguous_ordinals=(),
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
        challenge=challenge.challenge,
        challenge_epoch=challenge.challenge_epoch,
        topology_epoch=challenge.topology_epoch,
        presence_generation=2,
        capture_enabled=True,
    )
    assert proof is not None
    assert host.recording_presence_snapshot(
        challenge=challenge.challenge,
        challenge_epoch=challenge.challenge_epoch,
    ) == (proof,)
    host.invalidate_recording_presence()
    assert host.recording_presence_snapshot() == ()


def test_identical_public_profiles_reject_guest_ordinal_and_mark_readiness(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    host_enrollment = registry.enroll(
        _id(), "Alex", invite_token=credentials.invite_token
    )
    guest_enrollment = registry.enroll(
        _id(), "Alex", invite_token=credentials.invite_token
    )
    registry.bind_presence(
        guest_enrollment.participant_id,
        0,
        "Alex",
        generation=1,
        capture_enabled=True,
    )
    host = HostPeerSession()
    host.registry = registry
    host.control = SessionControlState(tmp_path, credentials.session_id)
    host.host_enrollment = host_enrollment
    digest = _digest("two-identical-full-profiles")
    fingerprint = _digest("host-local-zero-exact-proof")
    ambiguous = (0, 1)
    challenge = host.install_recording_presence_roster(
        digest,
        2,
        self_ordinal=0,
        host_roster_fingerprint=fingerprint,
        ambiguous_ordinals=ambiguous,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    assert challenge is not None
    host_proof = host.bind_host_recording_presence(
        "Alex",
        ordered_roster_digest=digest,
        roster_count=2,
        self_ordinal=0,
        host_roster_fingerprint=fingerprint,
        ambiguous_ordinals=ambiguous,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
        challenge=challenge.challenge,
        challenge_epoch=challenge.challenge_epoch,
        topology_epoch=challenge.topology_epoch,
        presence_generation=1,
        capture_enabled=True,
    )
    assert host_proof is not None

    with pytest.raises(TransferConflictError, match="ordinal is ambiguous"):
        _bind(
            registry,
            guest_enrollment.participant_id,
            challenge,
            ordinal=1,
            presence_generation=2,
            display_name="Alex",
            capture_enabled=True,
        )
    assert host.recording_presence_snapshot() == (host_proof,)

    take_id = _id()
    host.begin_take(take_id, started_utc="2026-08-03T12:00:00Z")
    assert host._expected_by_take[take_id] == (guest_enrollment.participant_id,)

    assert "readiness was incomplete" in host._presence_readiness_issue_by_take[take_id]


def test_take_expects_two_capture_guests_despite_both_legacy_local_zero(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    root = tmp_path / "host"
    registry = EnrollmentRegistry(root, credentials)
    control = SessionControlState(root, credentials.session_id)
    transfers = TransferStore(root, credentials.session_id)
    host_enrollment = registry.enroll(
        _id(), "Host", invite_token=credentials.invite_token
    )
    first_guest = registry.enroll(_id(), "Alex", invite_token=credentials.invite_token)
    second_guest = registry.enroll(_id(), "Alex", invite_token=credentials.invite_token)
    # Both guests truthfully see local channel 0. Legacy UI/local-transfer
    # compatibility retains only the last claimant. The modelled full public
    # profiles differ even though the names match, so v2 can retain both
    # cooperative server-ordinal claims and both upload obligations.
    registry.bind_presence(
        first_guest.participant_id,
        0,
        "Alex",
        generation=1,
        capture_enabled=True,
    )
    registry.bind_presence(
        second_guest.participant_id,
        0,
        "Alex",
        generation=1,
        capture_enabled=True,
    )

    host = HostPeerSession()
    host.registry = registry
    host.control = control
    host.transfers = transfers
    host.host_enrollment = host_enrollment
    digest = _digest("three-person-roster")
    fingerprint = _digest("three-person-host-fingerprint")
    challenge = host.install_recording_presence_roster(
        digest,
        3,
        self_ordinal=0,
        host_roster_fingerprint=fingerprint,
        ambiguous_ordinals=(),
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    assert challenge is not None
    host.bind_host_recording_presence(
        "Host",
        ordered_roster_digest=digest,
        roster_count=3,
        self_ordinal=0,
        host_roster_fingerprint=fingerprint,
        ambiguous_ordinals=(),
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
        challenge=challenge.challenge,
        challenge_epoch=challenge.challenge_epoch,
        topology_epoch=challenge.topology_epoch,
        presence_generation=1,
        capture_enabled=True,
    )
    _bind(
        registry,
        first_guest.participant_id,
        challenge,
        ordinal=1,
        presence_generation=10,
        capture_enabled=True,
    )
    _bind(
        registry,
        second_guest.participant_id,
        challenge,
        ordinal=2,
        presence_generation=20,
        capture_enabled=True,
    )

    take_id = _id()
    host.begin_take(take_id, started_utc="2026-08-03T12:00:00Z")
    assert set(host._expected_by_take[take_id]) == {
        first_guest.participant_id,
        second_guest.participant_id,
    }
    host.finish_take(take_id, stopped_utc="2026-08-03T12:01:00Z")

    take_dir = tmp_path / "take"
    write_take_project(
        take_dir,
        TakeProject(
            session_id=credentials.session_id,
            take_id=take_id,
            session_title="Test",
            take_name="Take 1",
            status=ProjectStatus.COMPLETE,
            project_sample_rate=48_000,
            participants=(Participant(host_enrollment.participant_id, "Host"),),
            tracks=(),
        ),
    )
    assert host.reconcile_take(take_id, take_dir)
    project = load_take_project(take_dir)
    missing = tuple(
        error
        for error in project.errors
        if "Alex's local original has not arrived" in error
    )
    assert len(missing) == 1  # Duplicate display text is intentionally deduplicated.
    assert {item.participant_id for item in project.participants} >= {
        first_guest.participant_id,
        second_guest.participant_id,
    }
    manifest = json.loads((take_dir / "webjam-take.json").read_text(encoding="utf-8"))
    transfer_rows = manifest["peer_transfers"]["participants"]
    assert {
        row["participant_id"] for row in transfer_rows if row["status"] == "missing"
    } == {first_guest.participant_id, second_guest.participant_id}


def test_take_keeps_mid_take_capture_opt_in_after_later_opt_out(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    host_enrollment = registry.enroll(
        _id(), "Host", invite_token=credentials.invite_token
    )
    guest_enrollment = registry.enroll(
        _id(), "Guest", invite_token=credentials.invite_token
    )
    host = HostPeerSession()
    host.registry = registry
    host.control = SessionControlState(tmp_path, credentials.session_id)
    host.host_enrollment = host_enrollment
    digest = _digest("late-guest-roster")
    fingerprint = _digest("late-guest-fingerprint")
    challenge = host.install_recording_presence_roster(
        digest,
        2,
        self_ordinal=0,
        host_roster_fingerprint=fingerprint,
        ambiguous_ordinals=(),
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    assert challenge is not None
    host.bind_host_recording_presence(
        "Host",
        ordered_roster_digest=digest,
        roster_count=2,
        self_ordinal=0,
        host_roster_fingerprint=fingerprint,
        ambiguous_ordinals=(),
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
        challenge=challenge.challenge,
        challenge_epoch=challenge.challenge_epoch,
        topology_epoch=challenge.topology_epoch,
        presence_generation=1,
        capture_enabled=True,
    )
    take_id = _id()
    host.begin_take(take_id, started_utc="2026-08-03T12:00:00Z")
    assert host._expected_by_take[take_id] == ()

    _bind(
        registry,
        guest_enrollment.participant_id,
        challenge,
        ordinal=1,
        presence_generation=10,
        display_name="Guest",
        capture_enabled=True,
    )
    disabled = _bind(
        registry,
        guest_enrollment.participant_id,
        challenge,
        ordinal=1,
        presence_generation=11,
        display_name="Guest",
        capture_enabled=False,
    )
    assert registry.recording_presence_snapshot()[-1] == disabled
    assert disabled.capture_enabled is False
    host.finish_take(take_id, stopped_utc="2026-08-03T12:01:00Z")
    assert host._expected_by_take[take_id] == (guest_enrollment.participant_id,)


def test_take_begin_includes_capture_opt_in_from_incomplete_pending_lease(
    tmp_path: Path,
) -> None:
    now = [100.0]
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(
        tmp_path,
        credentials,
        presence_clock=lambda: now[0],
        presence_v2_lease_s=2.0,
    )
    host_enrollment = registry.enroll(
        _id(), "Host", invite_token=credentials.invite_token
    )
    guest_enrollment = registry.enroll(
        _id(), "Guest", invite_token=credentials.invite_token
    )
    challenge = _install(registry, _digest("pending-opt-in"), 2)
    _bind(
        registry,
        host_enrollment.participant_id,
        challenge,
        ordinal=0,
        presence_generation=1,
        display_name="Host",
        capture_enabled=True,
    )
    _bind(
        registry,
        guest_enrollment.participant_id,
        challenge,
        ordinal=1,
        presence_generation=1,
        display_name="Guest",
        capture_enabled=False,
    )

    now[0] += 1.1
    pending = registry.current_presence_v2_challenge()
    _bind(
        registry,
        guest_enrollment.participant_id,
        pending,
        ordinal=1,
        presence_generation=2,
        display_name="Guest",
        capture_enabled=True,
    )
    assert registry.recording_presence_snapshot()[-1].capture_enabled is False

    host = HostPeerSession()
    host.registry = registry
    host.control = SessionControlState(tmp_path, credentials.session_id)
    host.host_enrollment = host_enrollment
    take_id = _id()
    host.begin_take(take_id, started_utc="2026-08-03T12:00:00Z")
    assert host._expected_by_take[take_id] == (guest_enrollment.participant_id,)

    _bind(
        registry,
        guest_enrollment.participant_id,
        pending,
        ordinal=1,
        presence_generation=3,
        display_name="Guest",
        capture_enabled=False,
    )
    host.finish_take(take_id, stopped_utc="2026-08-03T12:01:00Z")
    assert host._expected_by_take[take_id] == (guest_enrollment.participant_id,)


@pytest.mark.parametrize(
    ("count", "fingerprint", "capture_enabled"),
    (
        (True, _digest("map"), True),
        (-1, _digest("map"), True),
        (33, _digest("map"), True),
        (1, "", True),
        (0, _digest("map"), True),
        (1, _digest("map"), False),
    ),
)
def test_local_original_presence_contract_rejects_malformed_or_inconsistent_fields(
    count: object,
    fingerprint: str,
    capture_enabled: bool,
) -> None:
    values = {
        "participant_id": _id(),
        "display_name": "Alex",
        "ordered_roster_digest": _digest(),
        "roster_count": 1,
        "self_ordinal": 0,
        "process_generation": 1,
        "rpc_connection_generation": 2,
        "audio_connection_generation": 3,
        "challenge": "c" * 43,
        "challenge_epoch": 1,
        "topology_epoch": 1,
        "presence_generation": 1,
        "capture_enabled": capture_enabled,
        "local_original_track_count": count,
        "local_original_map_fingerprint": fingerprint,
    }
    with pytest.raises(ValueError):
        PresenceV2Proof(**values)


def test_exact_local_original_obligations_cover_zero_one_many_and_reconnect(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    host = registry.enroll(_id(), "Host", invite_token=credentials.invite_token)
    guests = tuple(
        registry.enroll(_id(), f"Guest {index}", invite_token=credentials.invite_token)
        for index in range(3)
    )
    challenge = _install(registry, _digest("obligation-roster"), 4)
    _bind(
        registry,
        host.participant_id,
        challenge,
        ordinal=0,
        presence_generation=1,
        display_name="Host",
        capture_enabled=False,
    )
    specifications = (
        (0, False, _digest("zero-map")),
        (1, True, _digest("one-map")),
        (32, True, _digest("many-map")),
    )
    for ordinal, (count, enabled, fingerprint) in enumerate(specifications, start=1):
        _bind(
            registry,
            guests[ordinal - 1].participant_id,
            challenge,
            ordinal=ordinal,
            presence_generation=ordinal,
            capture_enabled=enabled,
            local_original_track_count=count,
            local_original_map_fingerprint=fingerprint,
        )

    obligations = registry.current_local_original_obligations()
    by_id = {item.participant_id: item for item in obligations}
    assert tuple(by_id[item.participant_id].track_count for item in guests) == (
        0,
        1,
        32,
    )
    assert all(
        item.exact
        for item in by_id.values()
        if item.participant_id != host.participant_id
    )

    replacement_fingerprint = _digest("reconnected-one-map")
    _bind(
        registry,
        guests[1].participant_id,
        challenge,
        ordinal=2,
        presence_generation=99,
        capture_enabled=True,
        local_original_track_count=2,
        local_original_map_fingerprint=replacement_fingerprint,
    )
    reconnected = {
        item.participant_id: item
        for item in registry.current_local_original_obligations()
    }[guests[1].participant_id]
    assert reconnected.track_count == 2
    assert reconnected.map_fingerprint == replacement_fingerprint
    assert reconnected.presence_generation == 99


def test_legacy_local_original_presence_is_readable_but_not_exact() -> None:
    proof = PresenceV2Proof(
        participant_id=_id(),
        display_name="Legacy Guest",
        ordered_roster_digest=_digest(),
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
        challenge="c" * 43,
        challenge_epoch=1,
        topology_epoch=1,
        presence_generation=4,
        capture_enabled=True,
    )
    obligation = LocalOriginalObligation.from_presence_proof(proof)
    assert obligation.track_count is None
    assert not obligation.exact
    assert obligation.capture_requested


def test_host_preflight_exposes_exact_zero_and_rejects_live_legacy_guest(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    host_enrollment = registry.enroll(
        _id(), "Host", invite_token=credentials.invite_token
    )
    guest_enrollment = registry.enroll(
        _id(), "Guest", invite_token=credentials.invite_token
    )
    registry.bind_presence(
        guest_enrollment.participant_id,
        0,
        "Guest",
        generation=1,
        capture_enabled=False,
    )
    host = HostPeerSession()
    host.registry = registry
    host.host_enrollment = host_enrollment
    assert host.recording_local_original_obligations() == ()
    assert any(
        "exact Local Original inventory" in issue
        for issue in host.recording_local_original_obligation_issues()
    )
    _legacy_plan, legacy_issues = host.prepare_local_original_obligations(_id())
    assert legacy_issues

    challenge = _install(registry, _digest("exact-zero-roster"), 2)
    _bind(
        registry,
        host_enrollment.participant_id,
        challenge,
        ordinal=0,
        presence_generation=1,
        display_name="Host",
        capture_enabled=True,
    )
    zero_fingerprint = _digest("exact-zero-map")
    _bind(
        registry,
        guest_enrollment.participant_id,
        challenge,
        ordinal=1,
        presence_generation=2,
        display_name="Guest",
        capture_enabled=False,
        local_original_track_count=0,
        local_original_map_fingerprint=zero_fingerprint,
    )
    obligations = host.recording_local_original_obligations()
    assert obligations == (
        LocalOriginalObligation(
            guest_enrollment.participant_id,
            0,
            zero_fingerprint,
            presence_generation=2,
            capture_requested=False,
        ),
    )
    assert host.recording_local_original_obligation_issues() == ()
    take_id = _id()
    prepared, issues = host.prepare_local_original_obligations(take_id)
    assert prepared == obligations
    assert issues == ()
    assert host.local_original_obligations_for_take(take_id) == obligations


def test_departed_durable_enrollment_does_not_block_a_host_only_take(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    host_enrollment = registry.enroll(
        _id(), "Host", invite_token=credentials.invite_token
    )
    departed = registry.enroll(
        _id(), "Departed Guest", invite_token=credentials.invite_token
    )
    registry.bind_presence(
        departed.participant_id,
        7,
        "Departed Guest",
        generation=1,
        capture_enabled=True,
    )
    assert registry.reconcile_presence_channels((0,)) == 1

    challenge = _install(registry, _digest("host-only-after-departure"), 1)
    _bind(
        registry,
        host_enrollment.participant_id,
        challenge,
        ordinal=0,
        presence_generation=1,
        display_name="Host",
        capture_enabled=False,
    )
    host = HostPeerSession()
    host.registry = registry
    host.host_enrollment = host_enrollment

    assert host.recording_local_original_obligations() == ()
    assert host.recording_local_original_obligation_issues() == ()
    obligations, issues = host.prepare_local_original_obligations(_id())
    assert obligations == ()
    assert issues == ()


def test_discard_prepared_obligations_is_idempotent_and_never_removes_active_take(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials)
    host_enrollment = registry.enroll(
        _id(), "Host", invite_token=credentials.invite_token
    )
    host = HostPeerSession()
    host.registry = registry
    host.host_enrollment = host_enrollment
    host.control = SessionControlState(tmp_path, credentials.session_id)

    abandoned_take = _id()
    obligations, issues = host.prepare_local_original_obligations(abandoned_take)
    assert obligations == ()
    assert issues == ()
    assert abandoned_take in host._local_original_obligations_by_take
    assert host.discard_prepared_local_original_obligations(abandoned_take)
    assert not host.discard_prepared_local_original_obligations(abandoned_take)
    assert abandoned_take not in host._local_original_obligations_by_take
    assert not host.discard_prepared_local_original_obligations("not-a-take-id")

    active_take = _id()
    host.prepare_local_original_obligations(active_take)
    host.control.begin(active_take, started_utc="2026-08-15T12:00:00Z")
    assert not host.discard_prepared_local_original_obligations(active_take)
    assert host.local_original_obligations_for_take(active_take) == ()
    assert active_take in host._local_original_obligations_by_take


def test_http_v2_exact_local_original_contract_is_authenticated_and_private(
    frozen_peer,
) -> None:
    _credentials, registry, _server, client = frozen_peer
    enrollment = client.enroll(_id(), "Private Artist")
    challenge = _install(registry, _digest("exact-contract"), 1)
    map_fingerprint = _digest("name-free-logical-map")
    proof = client.bind_presence_v2(
        enrollment,
        display_name="Private Artist",
        ordered_roster_digest=challenge.ordered_roster_digest,
        roster_count=challenge.roster_count,
        self_ordinal=0,
        process_generation=4,
        rpc_connection_generation=5,
        audio_connection_generation=6,
        challenge=challenge.challenge,
        challenge_epoch=challenge.challenge_epoch,
        topology_epoch=challenge.topology_epoch,
        presence_generation=7,
        capture_enabled=True,
        local_original_track_count=1,
        local_original_map_fingerprint=map_fingerprint,
    )
    wire = asdict(proof)
    assert wire["local_original_track_count"] == 1
    assert wire["local_original_map_fingerprint"] == map_fingerprint
    encoded = json.dumps(wire)
    for private_value in (
        "/Users/alex/Music",
        "Scarlett 2i2",
        "Private Vocal",
    ):
        assert private_value not in encoded
    assert map_fingerprint not in registry.path.read_text(encoding="utf-8")
