"""Security, immutability, and wire-contract tests for Pocket Stage core."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.pocket_stage import (
    MAX_ACTIVE_PAIRING_CAPABILITIES,
    MAX_WIRE_MESSAGE_BYTES,
    MobileParticipant,
    MobileParticipantState,
    MobileRecordingState,
    MobileSection,
    MobileSessionProjection,
    PairingAcceptanceStatus,
    PairingCapabilityError,
    PairingCapabilityErrorCode,
    PairingCapabilityRegistry,
    PairingCapabilityState,
    PairingClaim,
    PairingScope,
    PocketCommand,
    PocketCommandReceipt,
    PocketCommandRejectionReason,
    PocketCommandRequest,
    PocketCommandStatus,
    PocketStageEnvelope,
    PocketStageMessageKind,
    PocketStageProtocolError,
    PocketStageProtocolErrorCode,
)
from core.session_conductor import (
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
)


MESSAGE_ID = "11111111-1111-4111-8111-111111111111"
COMMAND_ID = "22222222-2222-4222-8222-222222222222"
CLAIM_ID = "33333333-3333-4333-8333-333333333333"
OTHER_CLAIM_ID = "44444444-4444-4444-8444-444444444444"
GOLDEN_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "ios"
    / "Fixtures"
    / "pocket_stage_v1_golden.json"
)


class _Clock:
    def __init__(self, value: float = 1_800_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _participant(slot: int = 1, **overrides: object) -> MobileParticipant:
    values: dict[str, object] = {
        "slot": slot,
        "label": f"Musician {slot}",
        "fader_level": 75,
        "pan": 50,
        "muted": False,
        "solo": False,
        "is_local": slot == 1,
        "connection_state": MobileParticipantState.READY,
        **overrides,
    }
    return MobileParticipant(**values)  # type: ignore[arg-type]


def _section(ordinal: int = 1, **overrides: object) -> MobileSection:
    start = (ordinal - 1) * 10_000
    values: dict[str, object] = {
        "ordinal": ordinal,
        "label": f"Section {ordinal}",
        "start_ms": start,
        "end_ms": start + 10_000,
        **overrides,
    }
    return MobileSection(**values)  # type: ignore[arg-type]


def _projection(**overrides: object) -> MobileSessionProjection:
    values: dict[str, object] = {
        "generation": 3,
        "revision": 9,
        "role": SessionRole.HOST,
        "phase": SessionConductorPhase.RECORDING,
        "primary_action": SessionPrimaryAction.STOP_RECORDING,
        "primary_enabled": True,
        "recording_state": MobileRecordingState.RECORDING,
        "participants": (_participant(1), _participant(2, is_local=False)),
        "sections": (_section(1), _section(2)),
        "current_section_ordinal": 2,
        "cue": "Chorus in four bars",
        **overrides,
    }
    return MobileSessionProjection(**values)  # type: ignore[arg-type]


def _envelope(
    body: object | None = None,
    *,
    kind: PocketStageMessageKind = PocketStageMessageKind.SNAPSHOT,
    generation: int = 3,
    sequence: int = 8,
) -> PocketStageEnvelope:
    return PocketStageEnvelope(
        kind=kind,
        message_id=MESSAGE_ID,
        generation=generation,
        sequence=sequence,
        sent_at_unix_ms=1_800_000_000_123,
        body=body or _projection(),  # type: ignore[arg-type]
    )


def _assert_pairing_error(
    code: PairingCapabilityErrorCode,
    operation,
) -> None:
    with pytest.raises(PairingCapabilityError) as caught:
        operation()
    assert caught.value.code is code


def test_pairing_envelope_round_trip_has_explicit_private_boundary() -> None:
    registry = PairingCapabilityRegistry(clock=_Clock())
    capability = registry.issue(
        scopes=(PairingScope.OBSERVE, PairingScope.CUES),
        ttl_seconds=60,
    )
    secret = capability.reveal_for_pairing()
    claim = PairingClaim(capability_token=secret, claim_id=CLAIM_ID)
    envelope = _envelope(
        claim,
        kind=PocketStageMessageKind.PAIR,
        generation=0,
        sequence=0,
    )

    assert secret not in repr(claim)
    assert secret not in str(claim)
    assert secret not in repr(envelope)
    assert "[redacted]" in repr(envelope)
    raw = envelope.to_json()
    assert secret in raw

    parsed = PocketStageEnvelope.from_json(raw)
    assert parsed.kind is PocketStageMessageKind.PAIR
    assert parsed.generation == parsed.sequence == 0
    assert isinstance(parsed.body, PairingClaim)
    assert parsed.body.claim_id == CLAIM_ID
    assert parsed.body.capability_for_registry() == secret
    assert set(parsed.to_dict()) == {
        "version",
        "kind",
        "message_id",
        "generation",
        "sequence",
        "sent_at_unix_ms",
        "body",
    }


@pytest.mark.parametrize(
    ("kind", "body"),
    [
        (PocketStageMessageKind.SNAPSHOT, _projection()),
        (
            PocketStageMessageKind.COMMAND,
            PocketCommandRequest(
                command_id=COMMAND_ID,
                command=PocketCommand.GO_TO_SECTION,
                generation=3,
                expected_revision=9,
                arguments={"ordinal": 2},
            ),
        ),
        (
            PocketStageMessageKind.RECEIPT,
            PocketCommandReceipt(
                command_id=COMMAND_ID,
                status=PocketCommandStatus.CONFIRMED,
                generation=3,
                revision=10,
            ),
        ),
    ],
)
def test_nonsecret_envelopes_round_trip_as_typed_immutable_bodies(kind, body) -> None:
    envelope = _envelope(body, kind=kind)
    parsed = PocketStageEnvelope.from_json(envelope.to_json())

    assert parsed == envelope
    assert parsed.kind is kind
    assert parsed.version == 1
    with pytest.raises(FrozenInstanceError):
        parsed.sequence = 10  # type: ignore[misc]


def test_python_and_swift_share_exact_golden_wire_envelopes() -> None:
    fixture = json.loads(GOLDEN_FIXTURE.read_text(encoding="utf-8"))

    assert set(fixture) == {
        "pair",
        "snapshot",
        "fader_command",
        "confirmed_receipt",
    }
    for value in fixture.values():
        parsed = PocketStageEnvelope.from_dict(value)
        assert parsed.to_dict() == value


def test_envelope_rejects_kind_body_and_generation_mismatches() -> None:
    with pytest.raises(ValueError):
        _envelope(_projection(), kind=PocketStageMessageKind.COMMAND)
    with pytest.raises(ValueError):
        _envelope(_projection(), generation=4)
    with pytest.raises(ValueError):
        _envelope(
            PairingClaim(
                capability_token="A" * 43,
                claim_id=CLAIM_ID,
            ),
            kind=PocketStageMessageKind.PAIR,
            generation=1,
            sequence=0,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: {**value, "extra": "private-data"},
        lambda value: {key: item for key, item in value.items() if key != "body"},
        lambda value: {**value, "version": True},
        lambda value: {**value, "message_id": "NOT-A-UUID"},
        lambda value: {**value, "generation": True},
        lambda value: {**value, "sequence": -1},
        lambda value: {**value, "sent_at_unix_ms": 1.5},
        lambda value: {**value, "body": []},
        lambda value: {
            **value,
            "body": {**value["body"], "private_path": "/Users/private"},
        },
        lambda value: {**value, "body": {**value["body"], "schema": 2}},
        lambda value: {**value, "body": {**value["body"], "generation": 4}},
    ],
)
def test_envelope_rejects_noncanonical_or_unknown_fields_without_echo(mutation) -> None:
    candidate = mutation(_envelope().to_dict())
    with pytest.raises(PocketStageProtocolError) as caught:
        PocketStageEnvelope.from_dict(candidate)
    assert caught.value.code is PocketStageProtocolErrorCode.MALFORMED
    assert "/Users/private" not in str(caught.value)
    assert "NOT-A-UUID" not in str(caught.value)


@pytest.mark.parametrize("version", [0, 2, 999])
def test_envelope_rejects_unsupported_versions(version: int) -> None:
    candidate = {**_envelope().to_dict(), "version": version}
    with pytest.raises(PocketStageProtocolError) as caught:
        PocketStageEnvelope.from_dict(candidate)
    assert caught.value.code is PocketStageProtocolErrorCode.INCOMPATIBLE


def test_envelope_rejects_unknown_message_kind_as_incompatible() -> None:
    candidate = {**_envelope().to_dict(), "kind": "future_kind"}
    with pytest.raises(PocketStageProtocolError) as caught:
        PocketStageEnvelope.from_dict(candidate)
    assert caught.value.code is PocketStageProtocolErrorCode.INCOMPATIBLE


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"version":1,"version":1}',
        '{"value":NaN}',
        b"\xff\xfe",
    ],
)
def test_json_parser_rejects_invalid_utf8_duplicates_and_nonfinite_values(raw) -> None:
    with pytest.raises(PocketStageProtocolError) as caught:
        PocketStageEnvelope.from_json(raw)
    assert caught.value.code is PocketStageProtocolErrorCode.MALFORMED


def test_json_parser_rejects_duplicate_nested_keys() -> None:
    raw = _envelope().to_json().replace(
        '"cue":"Chorus in four bars"',
        '"cue":"safe","cue":"private"',
    )
    with pytest.raises(PocketStageProtocolError) as caught:
        PocketStageEnvelope.from_json(raw)
    assert caught.value.code is PocketStageProtocolErrorCode.MALFORMED


def test_json_parser_rejects_pathologically_deep_input_as_malformed() -> None:
    raw = "[" * 1_500 + "0" + "]" * 1_500
    with pytest.raises(PocketStageProtocolError) as caught:
        PocketStageEnvelope.from_json(raw)
    assert caught.value.code is PocketStageProtocolErrorCode.MALFORMED


def test_json_parser_rejects_oversized_input_before_parsing() -> None:
    raw = "{" + ("x" * MAX_WIRE_MESSAGE_BYTES) + "}"
    with pytest.raises(PocketStageProtocolError) as caught:
        PocketStageEnvelope.from_json(raw)
    assert caught.value.code is PocketStageProtocolErrorCode.TOO_LARGE


def test_pairing_capability_metadata_and_reprs_never_reveal_secret() -> None:
    clock = _Clock()
    registry = PairingCapabilityRegistry(clock=clock)
    capability = registry.issue(
        scopes=(PairingScope.RECORD, PairingScope.OBSERVE),
        ttl_seconds=60,
    )
    secret = capability.reveal_for_pairing()
    rendered = " ".join(
        (
            str(capability),
            repr(capability),
            repr(registry),
            repr(registry._by_id),  # noqa: SLF001 - prove plaintext is absent
            str(capability.to_public_dict()),
        )
    )

    assert len(secret) == 43
    assert secret not in rendered
    assert "[redacted]" in rendered
    assert capability.to_public_dict()["scopes"] == ["observe", "record"]
    assert not hasattr(capability, "__dict__")
    with pytest.raises(AttributeError):
        capability._token = "B" * 43  # type: ignore[misc]


def test_pairing_consume_returns_status_scopes_and_terminal_tombstone() -> None:
    clock = _Clock()
    registry = PairingCapabilityRegistry(clock=clock)
    capability = registry.issue(
        scopes=(PairingScope.MIX, PairingScope.OBSERVE),
        ttl_seconds=60,
    )
    secret = capability.reveal_for_pairing()

    accepted = registry.consume(secret, claim_id=CLAIM_ID)

    assert accepted.status is PairingAcceptanceStatus.ACCEPTED
    assert accepted.claim_id == CLAIM_ID
    assert accepted.capability_id == capability.capability_id
    assert accepted.scopes == (PairingScope.MIX, PairingScope.OBSERVE)
    assert registry.snapshot(capability.capability_id).state is PairingCapabilityState.CONSUMED
    _assert_pairing_error(
        PairingCapabilityErrorCode.REPLAY,
        lambda: registry.consume(secret, claim_id=CLAIM_ID),
    )
    _assert_pairing_error(
        PairingCapabilityErrorCode.CONSUMED,
        lambda: registry.consume(secret, claim_id=OTHER_CLAIM_ID),
    )


def test_pairing_rejects_unknown_or_malformed_tokens_without_echo() -> None:
    registry = PairingCapabilityRegistry(clock=_Clock())
    capability = registry.issue(scopes=(PairingScope.OBSERVE,), ttl_seconds=60)
    secret = capability.reveal_for_pairing()

    for token in ("short", "!" * 43, "A" * 43):
        with pytest.raises(PairingCapabilityError) as caught:
            registry.consume(token, claim_id=CLAIM_ID)
        assert caught.value.code is PairingCapabilityErrorCode.INVALID
        assert token not in str(caught.value)
    assert secret not in repr(registry)


def test_pairing_expiry_is_monotonic_across_wall_clock_rollback() -> None:
    clock = _Clock(1000.0)
    registry = PairingCapabilityRegistry(clock=clock)
    capability = registry.issue(scopes=(PairingScope.OBSERVE,), ttl_seconds=5)
    secret = capability.reveal_for_pairing()
    clock.value = 1006.0
    assert registry.snapshot(capability.capability_id).state is PairingCapabilityState.EXPIRED

    clock.value = 1001.0
    _assert_pairing_error(
        PairingCapabilityErrorCode.EXPIRED,
        lambda: registry.consume(secret, claim_id=CLAIM_ID),
    )


def test_pairing_revoke_is_idempotent_and_prevents_consumption() -> None:
    registry = PairingCapabilityRegistry(clock=_Clock())
    capability = registry.issue(scopes=(PairingScope.OBSERVE,), ttl_seconds=60)
    first = registry.revoke(capability.capability_id)
    second = registry.revoke(capability.capability_id)

    assert first == second
    assert first.state is PairingCapabilityState.REVOKED
    _assert_pairing_error(
        PairingCapabilityErrorCode.REVOKED,
        lambda: registry.consume(
            capability.reveal_for_pairing(),
            claim_id=CLAIM_ID,
        ),
    )


def test_clear_expired_bounds_tombstones_and_never_resurrects_token() -> None:
    clock = _Clock(500.0)
    registry = PairingCapabilityRegistry(clock=clock)
    capability = registry.issue(scopes=(PairingScope.OBSERVE,), ttl_seconds=1)
    secret = capability.reveal_for_pairing()
    clock.value = 501.0

    assert registry.clear_expired() == 1
    assert registry.clear_expired() == 0
    _assert_pairing_error(
        PairingCapabilityErrorCode.INVALID,
        lambda: registry.consume(secret, claim_id=CLAIM_ID),
    )


@pytest.mark.parametrize(
    ("scopes", "ttl"),
    [
        ((), 60),
        ((PairingScope.OBSERVE, PairingScope.OBSERVE), 60),
        (("administrator",), 60),
        ("observe", 60),
        ((PairingScope.OBSERVE,), 0),
        ((PairingScope.OBSERVE,), 601),
        ((PairingScope.OBSERVE,), True),
    ],
)
def test_pairing_issue_rejects_invalid_scopes_and_lifetimes(scopes, ttl) -> None:
    registry = PairingCapabilityRegistry(clock=_Clock())
    with pytest.raises(ValueError):
        registry.issue(scopes=scopes, ttl_seconds=ttl)


def test_pairing_registry_enforces_active_capacity() -> None:
    registry = PairingCapabilityRegistry(clock=_Clock())
    for _ in range(MAX_ACTIVE_PAIRING_CAPABILITIES):
        registry.issue(scopes=(PairingScope.OBSERVE,), ttl_seconds=60)

    _assert_pairing_error(
        PairingCapabilityErrorCode.CAPACITY,
        lambda: registry.issue(
            scopes=(PairingScope.OBSERVE,),
            ttl_seconds=60,
        ),
    )


def test_concurrent_pairing_claims_have_exactly_one_winner() -> None:
    registry = PairingCapabilityRegistry(clock=_Clock())
    capability = registry.issue(scopes=(PairingScope.OBSERVE,), ttl_seconds=60)
    token = capability.reveal_for_pairing()

    def consume() -> str:
        try:
            return registry.consume(token, claim_id=CLAIM_ID).status.value
        except PairingCapabilityError as exc:
            return exc.code.value

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = tuple(executor.map(lambda _index: consume(), range(8)))

    assert outcomes.count("accepted") == 1
    assert outcomes.count("replay") == 7


def test_mobile_projection_round_trip_is_immutable_and_paired_private() -> None:
    projection = _projection()
    parsed = MobileSessionProjection.from_dict(projection.to_dict())

    assert parsed == projection
    assert isinstance(parsed.participants, tuple)
    assert isinstance(parsed.sections, tuple)
    assert parsed.current_section_ordinal == 2
    assert parsed.cue == "Chorus in four bars"
    assert parsed.participants[0].label == "Musician 1"
    public = json.dumps(parsed.to_dict())
    for forbidden in ("participant_id", "name", "address", "path", "invite"):
        assert forbidden not in public
    with pytest.raises(FrozenInstanceError):
        parsed.revision = 10  # type: ignore[misc]


@pytest.mark.parametrize(
    "operation",
    [
        lambda: _participant(slot=0),
        lambda: _participant(slot=65),
        lambda: _participant(slot=True),
        lambda: _participant(label="Guitar\nprivate"),
        lambda: _participant(fader_level=101),
        lambda: _participant(pan=-1),
        lambda: _participant(muted=1),
        lambda: _section(label="Verse\nprivate"),
        lambda: _section(label="e\u0301"),
        lambda: _section(start_ms=10, end_ms=10),
        lambda: _projection(participants=(_participant(1), _participant(1))),
        lambda: _projection(participants=(_participant(2), _participant(1))),
        lambda: _projection(
            sections=(
                _section(1, start_ms=0, end_ms=15_000),
                _section(2, start_ms=10_000, end_ms=20_000),
            )
        ),
        lambda: _projection(current_section_ordinal=3),
        lambda: _projection(primary_enabled=1),
    ],
)
def test_mobile_projection_rejects_noncanonical_or_ambiguous_facts(operation) -> None:
    with pytest.raises((TypeError, ValueError)):
        operation()


def test_mobile_projection_wire_requires_lists_exact_keys_and_schema() -> None:
    payload = _projection().to_dict()
    invalid = (
        {**payload, "private": "secret"},
        {**payload, "schema": 2},
        {**payload, "participants": tuple(payload["participants"])},
        {**payload, "sections": tuple(payload["sections"])},
    )
    for candidate in invalid:
        with pytest.raises(ValueError):
            MobileSessionProjection.from_dict(candidate)


@pytest.mark.parametrize(
    ("command", "arguments", "scope"),
    [
        (
            PocketCommand.ADD_MARKER,
            {"at_ms": 12_345, "label": "Great entrance"},
            PairingScope.MARKERS,
        ),
        (
            PocketCommand.GO_TO_SECTION,
            {"ordinal": 2},
            PairingScope.TRANSPORT,
        ),
        (
            PocketCommand.SET_PARTICIPANT_MUTE,
            {"slot": 2, "muted": True},
            PairingScope.MIX,
        ),
        (
            PocketCommand.SET_PARTICIPANT_FADER,
            {"slot": 2, "fader_level": 70},
            PairingScope.MIX,
        ),
        (
            PocketCommand.SET_PARTICIPANT_PAN,
            {"slot": 2, "pan": 30},
            PairingScope.MIX,
        ),
        (PocketCommand.START_RECORDING, {}, PairingScope.RECORD),
        (PocketCommand.STOP_RECORDING, {}, PairingScope.RECORD),
    ],
)
def test_every_semantic_command_has_exact_arguments_and_scope(
    command,
    arguments,
    scope,
) -> None:
    request = PocketCommandRequest(
        command_id=COMMAND_ID,
        command=command,
        generation=3,
        expected_revision=9,
        arguments=arguments,
    )

    assert request.required_scope is scope
    assert dict(request.argument_map) == arguments
    assert PocketCommandRequest.from_dict(request.to_dict()) == request
    with pytest.raises(TypeError):
        request.argument_map["private"] = "value"  # type: ignore[index]


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        (PocketCommand.START_RECORDING, {"force": True}),
        (PocketCommand.ADD_MARKER, {"at_ms": 1}),
        (PocketCommand.ADD_MARKER, {"at_ms": -1, "label": ""}),
        (PocketCommand.ADD_MARKER, {"at_ms": 1, "label": "bad\nlabel"}),
        (PocketCommand.GO_TO_SECTION, {"ordinal": 0}),
        (PocketCommand.GO_TO_SECTION, {"ordinal": "2"}),
        (PocketCommand.SET_PARTICIPANT_MUTE, {"slot": 0, "muted": True}),
        (PocketCommand.SET_PARTICIPANT_MUTE, {"slot": 65, "muted": True}),
        (PocketCommand.SET_PARTICIPANT_MUTE, {"slot": 2, "muted": 1}),
        (
            PocketCommand.SET_PARTICIPANT_FADER,
            {"slot": 2, "fader_level": 101},
        ),
        (PocketCommand.SET_PARTICIPANT_PAN, {"slot": 2, "pan": -1}),
    ],
)
def test_command_arguments_reject_unknown_missing_and_wrong_typed_values(
    command,
    arguments,
) -> None:
    with pytest.raises(ValueError):
        PocketCommandRequest(
            command_id=COMMAND_ID,
            command=command,
            generation=3,
            expected_revision=9,
            arguments=arguments,
        )


def test_direct_command_construction_rejects_duplicate_argument_fields() -> None:
    with pytest.raises(ValueError):
        PocketCommandRequest(
            command_id=COMMAND_ID,
            command=PocketCommand.SET_PARTICIPANT_MUTE,
            generation=3,
            expected_revision=9,
            arguments=(("slot", 1), ("slot", 2), ("muted", True)),
        )


@pytest.mark.parametrize(
    ("generation", "revision"),
    [(True, 1), (-1, 1), (1, True), (1, -1)],
)
def test_command_requires_strict_generation_and_expected_revision(
    generation,
    revision,
) -> None:
    with pytest.raises(ValueError):
        PocketCommandRequest(
            command_id=COMMAND_ID,
            command=PocketCommand.START_RECORDING,
            generation=generation,
            expected_revision=revision,
            arguments={},
        )


def test_receipts_distinguish_acknowledgement_from_authoritative_confirmation() -> None:
    accepted = PocketCommandReceipt(
        command_id=COMMAND_ID,
        status=PocketCommandStatus.ACCEPTED,
        generation=3,
        revision=9,
    )
    pending = PocketCommandReceipt(
        command_id=COMMAND_ID,
        status=PocketCommandStatus.PENDING,
        generation=3,
        revision=9,
    )
    confirmed = PocketCommandReceipt(
        command_id=COMMAND_ID,
        status=PocketCommandStatus.CONFIRMED,
        generation=3,
        revision=10,
    )
    rejected = PocketCommandReceipt(
        command_id=COMMAND_ID,
        status=PocketCommandStatus.REJECTED,
        generation=3,
        revision=9,
        reason=PocketCommandRejectionReason.STALE_REVISION,
    )

    assert not accepted.terminal
    assert not pending.terminal
    assert confirmed.terminal
    assert rejected.terminal
    assert PocketCommandReceipt.from_dict(rejected.to_dict()) == rejected
    assert rejected.to_dict()["reason"] == "stale_revision"


def test_receipts_allow_only_fixed_rejection_reasons() -> None:
    with pytest.raises(ValueError):
        PocketCommandReceipt(
            command_id=COMMAND_ID,
            status=PocketCommandStatus.REJECTED,
            generation=3,
            revision=9,
        )
    with pytest.raises(ValueError):
        PocketCommandReceipt(
            command_id=COMMAND_ID,
            status=PocketCommandStatus.CONFIRMED,
            generation=3,
            revision=10,
            reason=PocketCommandRejectionReason.INTERNAL_FAILURE,
        )
    with pytest.raises(ValueError):
        PocketCommandReceipt(
            command_id=COMMAND_ID,
            status=PocketCommandStatus.REJECTED,
            generation=3,
            revision=9,
            reason="/Users/private/raw exception",  # type: ignore[arg-type]
        )


def test_all_rejection_reasons_are_finite_and_secret_free() -> None:
    assert {reason.value for reason in PocketCommandRejectionReason} == {
        "none",
        "unauthorized",
        "stale_generation",
        "stale_revision",
        "unsupported",
        "unavailable",
        "invalid_state",
        "rate_limited",
        "internal_failure",
    }
