"""Negotiated, private, fresh Local Original diagnostic presence."""

from dataclasses import asdict, fields, make_dataclass, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from core.session_transfer import (
    EnrollmentRegistry,
    PresenceV2Proof,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionTransferError,
    TransferAuthenticationError,
    TransferConflictError,
    TransferStore,
)


pytestmark = pytest.mark.requires_local_socket

DIAGNOSTIC_FIELDS = {
    "local_original_diagnostic_version",
    "local_original_failure_codes",
    "local_original_required_input_channels",
}
DIAGNOSTIC = {
    "local_original_diagnostic_version": 1,
    "local_original_failure_codes": ("insufficient_input_channels",),
    "local_original_required_input_channels": 2,
}


@pytest.fixture
def peer(tmp_path: Path):
    credentials = SessionCredentials.create()
    now = [100.0]
    registry = EnrollmentRegistry(tmp_path, credentials, presence_clock=lambda: now[0])
    server = SessionPeerServer(
        "127.0.0.1", 0, registry=registry,
        control=SessionControlState(tmp_path, credentials.session_id),
        transfers=TransferStore(tmp_path, credentials.session_id),
    )
    server.start()
    client = SessionPeerClient(*server.address, credentials=credentials)
    enrollment = client.enroll(str(uuid.uuid4()), "Same Name")
    other = client.enroll(str(uuid.uuid4()), "Same Name")
    digest = hashlib.sha256(b"diagnostic roster").hexdigest()
    challenge = registry.install_presence_v2_roster(
        digest, 2, host_roster_fingerprint=digest, ambiguous_ordinals=(),
        process_generation=1, rpc_connection_generation=1, audio_connection_generation=1,
    )
    base = PresenceV2Proof(
        participant_id=enrollment.participant_id, display_name="Same Name",
        ordered_roster_digest=digest, roster_count=2, self_ordinal=0,
        process_generation=1, rpc_connection_generation=1, audio_connection_generation=1,
        challenge=challenge.challenge, challenge_epoch=challenge.challenge_epoch,
        topology_epoch=challenge.topology_epoch, presence_generation=1,
        capture_enabled=True,
    )
    try:
        yield SimpleNamespace(
            registry=registry, server=server, client=client, enrollment=enrollment,
            other=other, base=base, now=now, root=tmp_path,
        )
    finally:
        server.stop()


def _legacy_mapping(proof):
    return {key: value for key, value in asdict(proof).items() if key not in DIAGNOSTIC_FIELDS}


def _post(peer, payload, enrollment=None):
    enrollment = enrollment or peer.enrollment
    return peer.client._request(
        "POST", "/v2/presence", token=enrollment.participant_token,
        participant_id=enrollment.participant_id,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _bind(peer, proof):
    payload = asdict(proof)
    payload.pop("participant_id")
    payload.pop("protocol_version")
    return peer.client.bind_presence_v2(peer.enrollment, **payload)


def test_opted_in_http_roundtrip_binds_private_diagnostic_to_unknown_topology(peer):
    candidate = replace(peer.base, **DIAGNOSTIC)
    proof = _bind(peer, candidate)
    assert proof == candidate
    assert peer.registry.current_local_original_diagnostic_proofs() == (proof,)
    assert not peer.registry.current_local_original_obligations()[0].exact_topology
    assert "insufficient_input_channels" not in peer.registry.path.read_text()
    assert "insufficient_input_channels" not in repr(proof)
    assert set(asdict(proof)) == set(_legacy_mapping(proof)) | DIAGNOSTIC_FIELDS


def test_new_host_old_guest_gets_exact_legacy_response_shape(peer):
    response = _post(peer, _legacy_mapping(peer.base))
    assert not (set(response) & DIAGNOSTIC_FIELDS)
    legacy_type = make_dataclass("LegacyPresenceV2Proof", [
        (field.name, field.type) for field in fields(PresenceV2Proof)
        if field.name not in DIAGNOSTIC_FIELDS
    ])
    legacy_type(**response)
    assert PresenceV2Proof(**response) == peer.base
    assert peer.registry.current_local_original_diagnostic_proofs() == ()


def test_new_client_old_host_omission_keeps_exact_base_proof(peer):
    handler = peer.server._httpd.RequestHandlerClass

    class LegacyHostHandler(handler):
        def _body(self, *, maximum):
            raw = super()._body(maximum=maximum)
            payload = json.loads(raw)
            for key in DIAGNOSTIC_FIELDS:
                payload.pop(key, None)
            return json.dumps(payload).encode()

    peer.server._httpd.RequestHandlerClass = LegacyHostHandler
    returned = _bind(peer, replace(peer.base, **DIAGNOSTIC))
    assert returned == peer.base
    assert returned.local_original_diagnostic_version == 0
    assert peer.registry.current_local_original_diagnostic_proofs() == ()


@pytest.mark.parametrize("mutate", [
    lambda values: values.pop("local_original_failure_codes"),
    lambda values: values.update(local_original_diagnostic_version=2),
    lambda values: values.update(local_original_diagnostic_version=True),
    lambda values: values.update(local_original_failure_codes="insufficient_input_channels"),
    lambda values: values.update(local_original_failure_codes=["PRIVATE_NATIVE_ERROR"]),
    lambda values: values.update(local_original_failure_codes=["invalid_track_map"] * 7),
    lambda values: values.update(local_original_failure_codes=["invalid_track_map"] * 2),
    lambda values: values.update(local_original_required_input_channels=True),
    lambda values: values.update(local_original_required_input_channels=33),
    lambda values: values.update(local_original_required_input_channels=-1),
])
def test_invalid_request_extension_never_publishes_presence(peer, mutate):
    payload = {**_legacy_mapping(peer.base), **DIAGNOSTIC}
    mutate(payload)
    with pytest.raises(SessionTransferError):
        _post(peer, payload)
    assert peer.registry.recording_presence_snapshot() == ()


@pytest.mark.parametrize("mutation", [
    {"local_original_diagnostic_version": 0},
    {"local_original_diagnostic_version": 2},
    {"local_original_diagnostic_version": True},
    {"local_original_failure_codes": "invalid_track_map"},
    {"local_original_failure_codes": ("unknown",)},
    {"local_original_failure_codes": ("invalid_track_map", "invalid_track_map")},
    {"local_original_required_input_channels": 2.0},
    {"local_original_required_input_channels": 33},
    {"capture_enabled": False},
    {"local_original_track_count": 1, "local_original_map_fingerprint": "a" * 64},
])
def test_model_rejects_unbounded_or_contradictory_diagnostics(peer, mutation):
    with pytest.raises(ValueError):
        replace(peer.base, **{**DIAGNOSTIC, **mutation})


def test_model_canonicalizes_codes_and_allows_generic_unknown(peer):
    proof = replace(peer.base, **{**DIAGNOSTIC, "local_original_failure_codes": (
        "input_device_or_format_unavailable", "invalid_track_map",
    )})
    assert proof.local_original_failure_codes == (
        "invalid_track_map", "input_device_or_format_unavailable",
    )
    unknown = replace(peer.base, local_original_diagnostic_version=1)
    assert unknown.local_original_failure_codes == ()
    assert not unknown.local_original_topology_exact


@pytest.mark.parametrize("change", ["partial", "version", "codes", "base", "legacy_base"])
def test_client_rejects_incomplete_unknown_or_mismatched_echo(peer, change):
    handler = peer.server._httpd.RequestHandlerClass

    class ChangedEchoHandler(handler):
        def _json(self, status, payload):
            if status == 200 and "presence_generation" in payload:
                payload = dict(payload)
                if change == "partial":
                    payload.pop("local_original_failure_codes")
                elif change == "version":
                    payload["local_original_diagnostic_version"] = 2
                elif change == "codes":
                    payload["local_original_failure_codes"] = ["invalid_track_map"]
                else:
                    payload["presence_generation"] += 1
                    if change == "legacy_base":
                        for key in DIAGNOSTIC_FIELDS:
                            payload.pop(key)
            super()._json(status, payload)

    peer.server._httpd.RequestHandlerClass = ChangedEchoHandler
    with pytest.raises(SessionTransferError):
        _bind(peer, replace(peer.base, **DIAGNOSTIC))


def test_authenticated_participant_overrides_body_identity_and_names_do_not_merge(peer):
    first = _post(peer, {**_legacy_mapping(peer.base), **DIAGNOSTIC,
                         "participant_id": peer.other.participant_id})
    assert first["participant_id"] == peer.enrollment.participant_id
    other_base = replace(peer.base, participant_id=peer.other.participant_id, self_ordinal=1)
    second = _post(peer, {**_legacy_mapping(other_base), **DIAGNOSTIC}, peer.other)
    assert second["participant_id"] == peer.other.participant_id
    proofs = peer.registry.current_local_original_diagnostic_proofs()
    assert {proof.participant_id for proof in proofs} == {
        peer.enrollment.participant_id, peer.other.participant_id,
    }
    with pytest.raises(TransferAuthenticationError):
        _post(peer, _legacy_mapping(peer.base), replace(
            peer.enrollment, participant_token=peer.other.participant_token,
        ))


def test_newest_pending_diagnostic_supersedes_and_v0_clears_old_failure(peer):
    first = _bind(peer, replace(peer.base, **DIAGNOSTIC))
    other_base = replace(peer.base, participant_id=peer.other.participant_id, self_ordinal=1)
    _post(peer, _legacy_mapping(other_base), peer.other)
    peer.now[0] += 8
    challenge = peer.registry.current_presence_v2_challenge()
    renewed = replace(first, challenge=challenge.challenge,
                      challenge_epoch=challenge.challenge_epoch, presence_generation=2,
                      local_original_failure_codes=("invalid_track_map",))
    _bind(peer, renewed)
    assert first in peer.registry.recording_presence_snapshot()
    assert peer.registry.current_local_original_diagnostic_proofs() == (renewed,)
    clear = replace(renewed, presence_generation=3, local_original_diagnostic_version=0,
                    local_original_failure_codes=(), local_original_required_input_channels=0)
    _bind(peer, clear)
    assert peer.registry.current_local_original_diagnostic_proofs() == ()


def test_replay_expiry_topology_and_invalidation_clear_diagnostics(peer):
    candidate = replace(peer.base, **DIAGNOSTIC)
    _bind(peer, candidate)
    with pytest.raises(TransferConflictError):
        _bind(peer, replace(candidate, local_original_failure_codes=("invalid_track_map",)))
    peer.now[0] += 16
    assert peer.registry.current_local_original_diagnostic_proofs() == ()
    with pytest.raises(TransferConflictError):
        _bind(peer, replace(candidate, presence_generation=2))
    challenge = peer.registry.current_presence_v2_challenge()
    fresh = replace(candidate, challenge=challenge.challenge,
                    challenge_epoch=challenge.challenge_epoch, presence_generation=3)
    _bind(peer, fresh)
    peer.registry.install_presence_v2_roster(
        "b" * 64, 2, host_roster_fingerprint="b" * 64, ambiguous_ordinals=(),
        process_generation=2, rpc_connection_generation=2, audio_connection_generation=2,
    )
    assert peer.registry.current_local_original_diagnostic_proofs() == ()
    peer.registry.invalidate_presence_v2()
    assert peer.registry.current_local_original_diagnostic_proofs() == ()
