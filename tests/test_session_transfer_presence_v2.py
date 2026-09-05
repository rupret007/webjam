from __future__ import annotations

import hashlib
import json
import uuid
import wave
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.network_invite import BandInvite
from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS
from core.session_transfer import (
    CaptureArmAcknowledgement,
    EnrollmentRegistry,
    LocalOriginalObligation,
    PresenceBinding,
    PresenceV2Challenge,
    PresenceV2Proof,
    RecordingSignal,
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


pytestmark = pytest.mark.requires_local_socket


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
    local_original_channel_counts: tuple[int, ...] = (),
    local_original_source_ids: tuple[str, ...] = (),
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
        local_original_channel_counts=local_original_channel_counts,
        local_original_source_ids=local_original_source_ids,
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
    # Whole-snapshot equality below means the same instant as well as the
    # same roster. Real elapsed milliseconds legitimately reduce lease_ms.
    registry = EnrollmentRegistry(
        tmp_path, credentials, presence_clock=lambda: _ULP_ARTIFACT_NOW
    )
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


def test_positive_guest_inventory_without_ordered_topology_blocks_preflight(
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
    challenge = _install(registry, _digest("missing-exact-topology"), 2)
    _bind(
        registry,
        host_enrollment.participant_id,
        challenge,
        ordinal=0,
        presence_generation=1,
        capture_enabled=False,
    )
    _bind(
        registry,
        guest_enrollment.participant_id,
        challenge,
        ordinal=1,
        presence_generation=2,
        capture_enabled=True,
        local_original_track_count=1,
        local_original_map_fingerprint=_digest("legacy-positive-map"),
    )
    host = HostPeerSession()
    host.registry = registry
    host.host_enrollment = host_enrollment

    obligations = host.recording_local_original_obligations()
    assert len(obligations) == 1
    assert obligations[0].exact
    assert not obligations[0].exact_topology
    assert any(
        "ordered mono/stereo source topology" in issue
        for issue in host.recording_local_original_obligation_issues()
    )
    _prepared, issues = host.prepare_local_original_obligations(_id())
    assert any("ordered mono/stereo source topology" in issue for issue in issues)


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
    logical_source_id = _id()
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
        local_original_channel_counts=(2,),
        local_original_source_ids=(logical_source_id,),
    )
    wire = asdict(proof)
    assert wire["local_original_track_count"] == 1
    assert wire["local_original_map_fingerprint"] == map_fingerprint
    assert wire["local_original_channel_counts"] == (2,)
    assert wire["local_original_source_ids"] == (logical_source_id,)
    assert proof.local_original_topology_exact
    encoded = json.dumps(wire)
    for private_value in (
        "/Users/alex/Music",
        "Scarlett 2i2",
        "Private Vocal",
    ):
        assert private_value not in encoded
    assert map_fingerprint not in registry.path.read_text(encoding="utf-8")


def test_capture_arm_ack_is_exact_authenticated_idempotent_and_stale_safe(
    frozen_peer,
) -> None:
    _credentials, registry, server, client = frozen_peer
    enrollment = client.enroll(_id(), "Guest")
    challenge = _install(registry, _digest("capture-arm-roster"), 1)
    map_fingerprint = _digest("capture-arm-map")
    logical_source_id = _id()
    proof = client.bind_presence_v2(
        enrollment,
        display_name="Guest",
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
        local_original_channel_counts=(2,),
        local_original_source_ids=(logical_source_id,),
    )
    take_id = _id()
    plan_fingerprint = _digest("capture-arm-plan")
    obligation = LocalOriginalObligation.from_presence_proof(proof)
    arm = server.control.publish_capture_arm(
        take_id,
        recording_plan_fingerprint=plan_fingerprint,
        requirements=(obligation,),
    )
    non_required = client.enroll(_id(), "Departed Guest")
    assert client.state(non_required).capture_arm is None
    state = client.state(enrollment)
    assert state.signal.value == "idle"
    assert state.capture_arm == arm

    acknowledgement = CaptureArmAcknowledgement(
        participant_id=enrollment.participant_id,
        take_id=take_id,
        arm_generation=arm.arm_generation,
        recording_plan_fingerprint=plan_fingerprint,
        presence_generation=proof.presence_generation,
        local_original_map_fingerprint=map_fingerprint,
        local_original_channel_counts=(2,),
        local_original_source_ids=(logical_source_id,),
    )
    contradictory = (
        replace(acknowledgement, arm_generation=arm.arm_generation + 1),
        replace(acknowledgement, take_id=_id()),
        replace(
            acknowledgement,
            recording_plan_fingerprint=_digest("wrong-plan"),
        ),
        replace(
            acknowledgement,
            presence_generation=proof.presence_generation + 1,
        ),
        replace(
            acknowledgement,
            local_original_map_fingerprint=_digest("wrong-map"),
        ),
        replace(acknowledgement, local_original_channel_counts=(1,)),
        replace(acknowledgement, local_original_source_ids=(_id(),)),
    )
    for candidate in contradictory:
        with pytest.raises(TransferConflictError):
            client.acknowledge_capture_arm(enrollment, candidate)
    with pytest.raises(TransferConflictError, match="active capture arm"):
        server.control.begin_armed_finalizing(
            take_id,
            arm_generation=arm.arm_generation + 1,
            stopped_utc="2026-08-16T12:00:01Z",
        )
    with pytest.raises(TransferConflictError, match="not fully acknowledged"):
        server.control.begin_armed_finalizing(
            take_id,
            arm_generation=arm.arm_generation,
            stopped_utc="2026-08-16T12:00:01Z",
        )
    with pytest.raises(TransferConflictError, match="not fully acknowledged"):
        server.control.begin(
            take_id,
            started_utc="2026-08-16T12:00:00Z",
        )
    assert server.control.publish_capture_arm(
        take_id,
        recording_plan_fingerprint=plan_fingerprint,
        requirements=(obligation,),
    ) == arm
    assert client.acknowledge_capture_arm(enrollment, acknowledgement) == acknowledgement
    assert client.acknowledge_capture_arm(enrollment, acknowledgement) == acknowledgement

    assert server.control.cancel_capture_arm(
        take_id,
        arm_generation=arm.arm_generation,
    )
    replacement = server.control.publish_capture_arm(
        take_id,
        recording_plan_fingerprint=plan_fingerprint,
        requirements=(obligation,),
    )
    assert replacement.arm_generation > arm.arm_generation
    assert not server.control.cancel_capture_arm(
        take_id,
        arm_generation=arm.arm_generation,
    )
    with pytest.raises(TransferConflictError, match="stale"):
        client.acknowledge_capture_arm(enrollment, acknowledgement)
    assert server.control.cancel_capture_arm(
        take_id,
        arm_generation=replacement.arm_generation,
    )
    assert client.state(enrollment).capture_arm is None


def test_capture_arm_excludes_exact_zero_track_opt_outs(tmp_path: Path) -> None:
    credentials = SessionCredentials.create()
    control = SessionControlState(tmp_path, credentials.session_id)
    zero = LocalOriginalObligation(
        participant_id=_id(),
        track_count=0,
        map_fingerprint=_digest("zero-track-map"),
        presence_generation=1,
        capture_requested=False,
    )
    arm = control.publish_capture_arm(
        _id(),
        recording_plan_fingerprint=_digest("zero-track-plan"),
        requirements=(zero,),
    )

    current, requirements, acknowledgements = control.capture_arm_state()
    assert current == arm
    assert requirements == ()
    assert acknowledgements == ()
    durable = control.path.read_text(encoding="utf-8")
    assert "capture_arm" not in durable
    assert arm.recording_plan_fingerprint not in durable
    recovered = SessionControlState(tmp_path, credentials.session_id).snapshot()
    assert recovered.capture_arm is None
    assert recovered.arm_handshake_required
    assert recovered.arm_handshake_generation == arm.arm_generation


def test_reloaded_capture_arm_fails_closed_until_exact_cancellation(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    prior_take_id = _id()
    take_id = _id()
    control = SessionControlState(tmp_path, credentials.session_id)
    control.begin(prior_take_id, started_utc="2026-08-16T11:59:58Z")
    control.begin_finalizing(
        prior_take_id,
        stopped_utc="2026-08-16T11:59:59Z",
    )
    control.finish(prior_take_id, stopped_utc="2026-08-16T11:59:59Z")
    arm = control.publish_capture_arm(
        take_id,
        recording_plan_fingerprint=_digest("restart-capture-arm-plan"),
        requirements=(),
    )

    recovered = SessionControlState(tmp_path, credentials.session_id)
    recovered_state = recovered.snapshot()
    assert recovered_state.capture_arm is None
    assert recovered_state.signal is RecordingSignal.COMPLETE
    assert recovered_state.arm_handshake_required
    assert recovered_state.arm_handshake_take_id == take_id
    with pytest.raises(TransferConflictError, match="unresolved after restart"):
        recovered.begin(_id(), started_utc="2026-08-16T12:00:00Z")
    with pytest.raises(TransferConflictError, match="unresolved after restart"):
        recovered.publish_capture_arm(
            _id(),
            recording_plan_fingerprint=_digest("unrelated-restart-plan"),
            requirements=(),
        )
    assert not recovered.cancel_capture_arm(take_id)
    assert not recovered.cancel_capture_arm(
        take_id,
        arm_generation=arm.arm_generation + 1,
    )
    assert not recovered.cancel_capture_arm(
        _id(),
        arm_generation=arm.arm_generation,
    )
    assert recovered.cancel_capture_arm(
        take_id,
        arm_generation=arm.arm_generation,
    )
    cancellation = recovered.snapshot().capture_arm_cancellation
    assert cancellation is not None
    assert cancellation.take_id == take_id
    assert cancellation.arm_generation == arm.arm_generation

    replacement = recovered.publish_capture_arm(
        _id(),
        recording_plan_fingerprint=_digest("replacement-restart-plan"),
        requirements=(),
    )
    assert replacement.arm_generation > arm.arm_generation
    assert recovered.snapshot().capture_arm_cancellation == cancellation


def test_guest_capture_arm_starts_before_ack_and_cancel_is_idempotent(
    tmp_path: Path,
    peer,
    monkeypatch,
) -> None:
    credentials, registry, server, _client = peer
    digest = _digest("runtime-capture-arm-roster")
    _install(registry, digest, 1)
    events: list[str] = []
    forbid_post_start_preflight = [False]

    class ArmedCapture:
        instances: list["ArmedCapture"] = []

        def __init__(self, _root, **kwargs) -> None:
            self.started = False
            self.aborted = False
            self.stopped = False
            self.tracks = tuple(kwargs.get("tracks", ()))
            self.__class__.instances.append(self)

        def start(self) -> None:
            events.append("capture-start")
            self.started = True

        def abort(self) -> None:
            events.append("capture-abort")
            self.aborted = True

        def stop_into(self, destination):
            events.append("capture-stop")
            self.stopped = True
            destination = Path(destination)
            destination.mkdir(parents=True, exist_ok=True)
            source = destination / "input-1.wav"
            with wave.open(str(source), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(48_000)
                wav.writeframes(b"\x00\x00" * 128)
            return SimpleNamespace(
                files=(source,),
                started_utc="2026-08-16T12:00:00Z",
                errors=(),
                gaps=(),
                capture_device=None,
                tracks=self.tracks,
            )

    invite = BandInvite(
        "127.0.0.1",
        22124,
        "Test",
        credentials.session_id,
        server.address[1],
        credentials.invite_token,
    )

    def capture_tracks():
        if forbid_post_start_preflight[0] and any(
            item.started for item in ArmedCapture.instances
        ):
            raise AssertionError("capture map was queried after the stream opened")
        return (("input-1", 0),)

    guest = GuestPeerSession(
        invite,
        display_name="Guest",
        takes_root=tmp_path / "guest",
        installation_path=tmp_path / "guest-installation.json",
        capture_enabled=lambda: True,
        capture_config=lambda: (0, 48_000, 128),
        capture_tracks=capture_tracks,
        capture_factory=ArmedCapture,
    )
    guest.poll_once()
    guest.observe_presence_v2(
        "Guest",
        ordered_roster_digest=digest,
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    guest.poll_once()

    host = HostPeerSession()
    host.registry = registry
    host.control = server.control
    take_id = _id()
    obligations, issues = host.prepare_local_original_obligations(take_id)
    assert len(obligations) == 1
    assert issues == ()
    arm = host.publish_capture_arm(
        take_id,
        recording_plan_fingerprint=_digest("runtime-capture-arm-plan"),
    )
    real_ack = guest.client.acknowledge_capture_arm

    def assert_started_before_ack(enrollment, acknowledgement):
        assert ArmedCapture.instances[-1].started
        events.append("capture-ack")
        return real_ack(enrollment, acknowledgement)

    monkeypatch.setattr(
        guest.client,
        "acknowledge_capture_arm",
        assert_started_before_ack,
    )
    forbid_post_start_preflight[0] = True
    observed = guest.poll_once()
    forbid_post_start_preflight[0] = False

    assert observed.signal.value == "idle"
    assert observed.capture_arm == arm
    assert events == ["capture-start", "capture-ack"]
    assert host.capture_arm_pending_participant_ids(
        take_id,
        arm_generation=arm.arm_generation,
    ) == ()
    assert host.wait_for_capture_arm_acknowledgements(
        take_id,
        arm_generation=arm.arm_generation,
        timeout_s=0.0,
    )

    assert host.cancel_capture_arm(
        take_id,
        arm_generation=arm.arm_generation,
    )
    assert not host.cancel_capture_arm(
        take_id,
        arm_generation=arm.arm_generation,
    )
    guest.poll_once()
    assert events == ["capture-start", "capture-ack", "capture-abort"]
    assert ArmedCapture.instances[-1].aborted
    assert guest.active_take_id == ""

    # Arm state is intentionally memory-only. If the host restarts, then
    # publishes unrelated state at a newer generation, that ambiguity must
    # preserve, not abort, guest audio without an exact cancellation proof.
    recovery_take_id = _id()
    _obligations, issues = host.prepare_local_original_obligations(
        recovery_take_id
    )
    assert issues == ()
    host.publish_capture_arm(
        recovery_take_id,
        recording_plan_fingerprint=_digest("recovery-capture-arm-plan"),
    )
    recovery_state = guest.poll_once()
    recovery_capture = ArmedCapture.instances[-1]
    real_state = guest.client.state
    monkeypatch.setattr(
        guest.client,
        "state",
        lambda _enrollment: replace(
            recovery_state,
            generation=recovery_state.generation + 1,
            capture_arm=None,
            capture_arm_cancellation=None,
            capture_arm_supported=True,
        ),
    )
    guest.poll_once()
    monkeypatch.setattr(guest.client, "state", real_state)
    assert recovery_capture.stopped
    assert not recovery_capture.aborted
    assert {
        item.status
        for item in guest.pending_segments
        if item.descriptor.take_id == recovery_take_id
    } == {"recovery_only"}
    recovery_segment = next(
        item
        for item in guest.pending_segments
        if item.descriptor.take_id == recovery_take_id
    )
    assert not server.transfers.status(recovery_segment.descriptor).complete
    server.control.begin(
        recovery_take_id,
        started_utc="2026-08-16T12:00:00Z",
    )
    guest.poll_once()
    # A later matching take UUID is insufficient authority to upload this
    # media: the host may have canceled/re-armed that UUID at a newer arm
    # generation. Keep ambiguous media local for explicit recovery.
    assert {
        item.status
        for item in guest.pending_segments
        if item.descriptor.take_id == recovery_take_id
    } == {"recovery_only"}
    server.control.begin_finalizing(
        recovery_take_id,
        stopped_utc="2026-08-16T12:00:01Z",
    )
    server.control.finish(
        recovery_take_id,
        stopped_utc="2026-08-16T12:00:01Z",
    )

    outsider = GuestPeerSession(
        invite,
        display_name="Departed Guest",
        takes_root=tmp_path / "departed-guest",
        installation_path=tmp_path / "departed-installation.json",
        capture_enabled=lambda: True,
        capture_config=lambda: (0, 48_000, 128),
        capture_tracks=lambda: (("input-1", 0),),
        capture_factory=ArmedCapture,
    )
    outsider.poll_once()

    committed_take_id = _id()
    _obligations, issues = host.prepare_local_original_obligations(
        committed_take_id
    )
    assert issues == ()
    committed_arm = host.publish_capture_arm(
        committed_take_id,
        recording_plan_fingerprint=_digest("committed-capture-arm-plan"),
    )
    capture_count = len(ArmedCapture.instances)
    outsider_arm_state = outsider.poll_once()
    assert outsider_arm_state.capture_arm is None
    assert outsider_arm_state.capture_arm_supported
    assert len(ArmedCapture.instances) == capture_count
    guest.poll_once()
    committed_capture = ArmedCapture.instances[-1]
    assert committed_capture.started
    assert host.capture_arm_ready(
        committed_take_id,
        arm_generation=committed_arm.arm_generation,
    )
    server.control.begin(
        committed_take_id,
        started_utc="2026-08-16T12:00:00Z",
    )
    outsider_recording_state = outsider.poll_once()
    assert outsider_recording_state.signal is RecordingSignal.RECORDING
    assert outsider_recording_state.capture_arm_supported
    assert outsider.active_take_id == ""
    assert len(ArmedCapture.instances) == capture_count + 1
    server.control.begin_finalizing(
        committed_take_id,
        stopped_utc="2026-08-16T12:00:01Z",
    )
    # The 750 ms poll may legitimately miss a very short RECORDING snapshot.
    # Matching terminal truth still commits and finalizes the armed capture.
    guest.poll_once()
    assert not committed_capture.aborted
    assert committed_capture.stopped
    assert guest.active_take_id == ""
    server.control.finish(
        committed_take_id,
        stopped_utc="2026-08-16T12:00:01Z",
    )

    # A confirmed Stop after an unconfirmed server start commits the exact,
    # fully ACKed arm directly to FINALIZING without inventing started_utc.
    ambiguous_take_id = _id()
    _obligations, issues = host.prepare_local_original_obligations(
        ambiguous_take_id
    )
    assert issues == ()
    ambiguous_arm = host.publish_capture_arm(
        ambiguous_take_id,
        recording_plan_fingerprint=_digest("ambiguous-capture-arm-plan"),
    )
    guest.poll_once()
    ambiguous_capture = ArmedCapture.instances[-1]
    with pytest.raises(TransferConflictError, match="active capture arm"):
        host.begin_armed_take_finalization(
            ambiguous_take_id,
            arm_generation=ambiguous_arm.arm_generation + 1,
            stopped_utc="2026-08-16T12:00:03Z",
        )
    ambiguous_finalizing = host.begin_armed_take_finalization(
        ambiguous_take_id,
        arm_generation=ambiguous_arm.arm_generation,
        stopped_utc="2026-08-16T12:00:03Z",
        message="The server start was not confirmed; audio was preserved.",
    )
    assert ambiguous_finalizing is not None
    assert ambiguous_finalizing.signal is RecordingSignal.FINALIZING
    assert ambiguous_finalizing.take_id == ambiguous_take_id
    assert ambiguous_finalizing.started_utc == ""
    assert ambiguous_finalizing.capture_arm is None
    assert ambiguous_finalizing.arm_handshake_required
    assert host.begin_armed_take_finalization(
        ambiguous_take_id,
        arm_generation=ambiguous_arm.arm_generation,
        stopped_utc="2026-08-16T12:00:03Z",
    ) == ambiguous_finalizing
    with pytest.raises(TransferConflictError, match="committed capture arm"):
        host.begin_armed_take_finalization(
            ambiguous_take_id,
            arm_generation=ambiguous_arm.arm_generation + 1,
            stopped_utc="2026-08-16T12:00:03Z",
        )
    guest.poll_once()
    assert ambiguous_capture.stopped
    assert not ambiguous_capture.aborted
    assert {
        item.status
        for item in guest.pending_segments
        if item.descriptor.take_id == ambiguous_take_id
    } == {"verified"}
    server.control.finish(
        ambiguous_take_id,
        stopped_utc="2026-08-16T12:00:03Z",
        needs_attention=True,
        message="The server start confirmation was unavailable.",
    )

    # A current exact-roster host also advertises the protocol when the plan
    # has zero guest tracks.  Session-wide RECORDING is never permission for a
    # previously enrolled guest that received no take-scoped arm.
    unarmed_take_id = _id()
    capture_count = len(ArmedCapture.instances)
    server.control.begin(
        unarmed_take_id,
        started_utc="2026-08-16T12:00:04Z",
    )
    assert outsider.poll_once().capture_arm_supported
    assert guest.poll_once().capture_arm_supported
    assert outsider.active_take_id == ""
    assert guest.active_take_id == ""
    assert len(ArmedCapture.instances) == capture_count
    server.control.begin_finalizing(
        unarmed_take_id,
        stopped_utc="2026-08-16T12:00:05Z",
    )
    server.control.finish(
        unarmed_take_id,
        stopped_utc="2026-08-16T12:00:05Z",
    )

    # Closing after a locally confirmed ACK but before the next state poll is
    # also uncertain.  The app must stop/finalize recovery media, never abort.
    shutdown_take_id = _id()
    _obligations, issues = host.prepare_local_original_obligations(shutdown_take_id)
    assert issues == ()
    shutdown_arm = host.publish_capture_arm(
        shutdown_take_id,
        recording_plan_fingerprint=_digest("shutdown-capture-arm-plan"),
    )
    guest.poll_once()
    shutdown_capture = ArmedCapture.instances[-1]
    assert guest.stop()
    assert shutdown_capture.stopped
    assert not shutdown_capture.aborted
    assert {
        item.status
        for item in guest.pending_segments
        if item.descriptor.take_id == shutdown_take_id
    } == {"recovery_only"}
    shutdown_segment = next(
        item
        for item in guest.pending_segments
        if item.descriptor.take_id == shutdown_take_id
    )
    assert not server.transfers.status(shutdown_segment.descriptor).complete
    assert host.cancel_capture_arm(
        shutdown_take_id,
        arm_generation=shutdown_arm.arm_generation,
    )
    assert outsider.stop()


def test_guest_capture_start_failure_never_acknowledges_arm(
    tmp_path: Path,
    peer,
) -> None:
    credentials, registry, server, _client = peer
    digest = _digest("failed-capture-arm-roster")
    _install(registry, digest, 1)

    class FailingCapture:
        def __init__(self, _root, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("device disappeared")

        def abort(self) -> None:
            pass

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
        display_name="Guest",
        takes_root=tmp_path / "failed-guest",
        installation_path=tmp_path / "failed-installation.json",
        capture_enabled=lambda: True,
        capture_config=lambda: (0, 48_000, 128),
        capture_factory=FailingCapture,
    )
    guest.poll_once()
    guest.observe_presence_v2(
        "Guest",
        ordered_roster_digest=digest,
        roster_count=1,
        self_ordinal=0,
        process_generation=1,
        rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    guest.poll_once()

    host = HostPeerSession()
    host.registry = registry
    host.control = server.control
    take_id = _id()
    _obligations, issues = host.prepare_local_original_obligations(take_id)
    assert issues == ()
    arm = host.publish_capture_arm(
        take_id,
        recording_plan_fingerprint=_digest("failed-capture-arm-plan"),
    )

    with pytest.raises(RuntimeError, match="device disappeared"):
        guest.poll_once()
    assert not host.wait_for_capture_arm_acknowledgements(
        take_id,
        arm_generation=arm.arm_generation,
        timeout_s=0.0,
    )
    assert host.capture_arm_pending_participant_ids(
        take_id,
        arm_generation=arm.arm_generation,
    ) == (guest.participant_id,)
    assert server.control.snapshot().signal.value == "idle"
    assert host.cancel_capture_arm(
        take_id,
        arm_generation=arm.arm_generation,
    )
    guest.stop()


def test_identical_roster_does_not_extend_an_aging_challenge_lease(tmp_path):
    now = [100.0]
    credentials = SessionCredentials.create()
    registry = EnrollmentRegistry(tmp_path, credentials, presence_clock=lambda: now[0])
    first = _install(registry, _digest(), 1)
    now[0] += 0.25
    aged = _install(registry, _digest(), 1)
    assert aged.lease_ms == first.lease_ms - 250
    assert replace(aged, lease_ms=first.lease_ms) == first
