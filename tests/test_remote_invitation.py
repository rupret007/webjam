"""Security and lifecycle coverage for opaque v3 remote invitations."""

from __future__ import annotations

import base64
import random
import struct
import threading

import pytest

from core.network_invite import InviteLinkError, create_invite_link, parse_invite_link
from core.remote_invitation import (
    DEFAULT_INVITATION_TTL_SECONDS,
    MAX_INVITATION_TTL_SECONDS,
    EnrollmentClaim,
    InvitationLifecycle,
    InvitationLifecycleError,
    InvitationLifecycleErrorCode,
    InvitationState,
    RemoteInvitationError,
    RemoteInvitationErrorCode,
    issue_remote_invitation,
    parse_remote_invitation_link,
)
from core.session_transfer import ParticipantEnrollment, SessionCredentials


PROFILE = "reference-local"
ALLOWED = frozenset({PROFILE})
ISSUED_AT = 1_800_000_000
SESSION_REFERENCE = bytes.fromhex("11" * 16)
INVITE_REFERENCE = bytes.fromhex("22" * 16)
CAPABILITY = bytes.fromhex("33" * 32)
HOST_SPKI_SHA256 = bytes.fromhex("44" * 32)


def _issued(**overrides):
    values = {
        "profile_id": PROFILE,
        "allowed_profiles": ALLOWED,
        "host_spki_sha256": HOST_SPKI_SHA256,
        "issued_at_unix": ISSUED_AT,
        "session_reference": SESSION_REFERENCE,
        "invite_reference": INVITE_REFERENCE,
        "enrollment_capability": CAPABILITY,
        **overrides,
    }
    return issue_remote_invitation(**values)


def _raw_link(**overrides) -> str:
    return _issued(**overrides).private_link.reveal_for_clipboard()


def _replace_envelope(raw: str, payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    prefix, _old = raw.rsplit("i=", 1)
    return f"{prefix}i={encoded}"


def _payload(raw: str) -> bytearray:
    encoded = raw.rsplit("i=", 1)[1]
    padding = "=" * (-len(encoded) % 4)
    return bytearray(base64.urlsafe_b64decode(encoded + padding))


def _claim(
    invitation,
    *,
    guest: bytes = b"guest-one-ephemeral-public-key",
    claim_reference: bytes = bytes.fromhex("55" * 16),
    protocol_version: int = 3,
) -> EnrollmentClaim:
    return EnrollmentClaim.for_invitation(
        invitation,
        guest_public_key=guest,
        claim_reference=claim_reference,
        protocol_version=protocol_version,
    )


def test_v3_round_trip_is_fixed_canonical_and_contains_no_human_or_network_data():
    issued = _issued()
    raw = issued.private_link.reveal_for_clipboard()

    assert raw.startswith(f"webjam://join?v=3&r={PROFILE}&i=")
    assert raw.count("?") == 1
    assert raw.count("&") == 2
    assert "%" not in raw
    assert "+" not in raw
    assert "=" not in raw.rsplit("i=", 1)[1]
    for forbidden in (
        "Jeff",
        "Drummer",
        "Band Rehearsal",
        "192.168.",
        "127.0.0.1",
        "/Users/",
        "relay.example",
        "22124",
    ):
        assert forbidden not in raw

    parsed = parse_remote_invitation_link(raw, allowed_profiles=ALLOWED)
    assert parsed.version == 3
    assert parsed.profile_id == PROFILE
    assert parsed.issued_at_unix == ISSUED_AT
    assert parsed.expires_at_unix == ISSUED_AT + DEFAULT_INVITATION_TTL_SECONDS
    assert parsed.participant_limit == 1
    assert parsed.session_reference == SESSION_REFERENCE
    assert parsed.invite_reference == INVITE_REFERENCE
    assert parsed.capability_for_enrollment() == CAPABILITY
    assert parsed.host_spki_sha256 == HOST_SPKI_SHA256


def test_secret_objects_require_an_explicit_reveal_and_have_safe_repr():
    issued = _issued()
    raw = issued.private_link.reveal_for_clipboard()
    invitation = issued.invitation
    claim = _claim(invitation)
    lifecycle = InvitationLifecycle(invitation, clock=lambda: ISSUED_AT)

    rendered = "\n".join(
        (
            str(issued.private_link),
            repr(issued.private_link),
            repr(issued),
            str(invitation),
            repr(invitation),
            str(claim),
            repr(claim),
            repr(lifecycle),
        )
    )
    assert raw not in rendered
    for secret in (
        CAPABILITY.hex(),
        SESSION_REFERENCE.hex(),
        INVITE_REFERENCE.hex(),
        HOST_SPKI_SHA256.hex(),
    ):
        assert secret not in rendered
    assert "[redacted]" in rendered
    with pytest.raises(AttributeError):
        invitation._profile_id = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        issued.private_link._invitation = invitation  # type: ignore[misc]
    with pytest.raises(AttributeError):
        issued.invitation = invitation
    with pytest.raises(AttributeError):
        claim.protocol_version = 2
    assert not hasattr(invitation, "__dict__")
    assert raw not in tuple(
        value
        for slot in issued.private_link.__slots__
        if isinstance((value := getattr(issued.private_link, slot)), str)
    )


def test_default_expiry_is_advisory_at_parse_time():
    parsed = parse_remote_invitation_link(_raw_link(), allowed_profiles=ALLOWED)
    assert parsed.advisory_expired(ISSUED_AT + 599) is False
    assert parsed.advisory_expired(ISSUED_AT + 600) is True
    # An expired URL still parses. The authoritative lifecycle/service decides
    # whether it can enroll, so a guest's skewed clock cannot reject it early.
    assert parsed.expires_at_unix == ISSUED_AT + 600


@pytest.mark.parametrize(
    ("profile", "allowed"),
    [
        ("unknown", ALLOWED),
        ("https://relay.example", {"https://relay.example"}),
        ("127.0.0.1", {"127.0.0.1"}),
        ("reference_local", {"reference_local"}),
        ("référence", {"référence"}),
        ("аdmin", {"аdmin"}),  # Cyrillic first letter
        ("-reference", {"-reference"}),
        ("reference-", {"reference-"}),
        ("a" * 33, {"a" * 33}),
    ],
)
def test_issue_rejects_untrusted_or_noncanonical_profile_ids(profile, allowed):
    with pytest.raises(RemoteInvitationError) as caught:
        _issued(profile_id=profile, allowed_profiles=allowed)
    assert caught.value.code is RemoteInvitationErrorCode.UNTRUSTED_PROFILE


def _structural_mutation(name: str, raw: str) -> str:
    query = raw.split("?", 1)[1]
    encoded = raw.rsplit("i=", 1)[1]
    mutations = {
        "wrong_scheme": f"https://join?{query}",
        "uppercase_scheme": f"WEBJAM://join?{query}",
        "wrong_action": f"webjam://host?{query}",
        "userinfo": f"webjam://user@join?{query}",
        "path": f"webjam://join/?{query}",
        "fragment": f"{raw}#private",
        "missing_version": raw.replace("v=3&", ""),
        "wrong_version": raw.replace("v=3", "v=4", 1),
        "encoded_version": raw.replace("v=3", "v=%33", 1),
        "duplicate_version": raw.replace("v=3", "v=3&v=3", 1),
        "duplicate_profile": raw.replace(f"r={PROFILE}", f"r={PROFILE}&r={PROFILE}"),
        "duplicate_envelope": f"{raw}&i={encoded}",
        "unknown_key": f"{raw}&host=192.168.1.2",
        "mixed_v2": f"{raw}&sid={'a' * 36}&token={'b' * 43}",
        "missing_profile": raw.replace(f"r={PROFILE}&", ""),
        "missing_envelope": raw.rsplit("&i=", 1)[0],
        "empty_profile": raw.replace(f"r={PROFILE}", "r="),
        "empty_envelope": raw.rsplit("i=", 1)[0] + "i=",
        "query_reordered": f"webjam://join?r={PROFILE}&v=3&i={encoded}",
        "percent_profile": raw.replace(PROFILE, "reference%2Dlocal"),
        "plus_profile": raw.replace(PROFILE, "reference+local"),
        "unicode_profile": raw.replace(PROFILE, "référence-local"),
        "unicode_action": raw.replace("join", "jοin", 1),  # Greek omicron
        "base64_padding": f"{raw}=",
        "base64_plus": raw.rsplit("i=", 1)[0] + "i=+" + encoded[1:],
        "base64_slash": raw.rsplit("i=", 1)[0] + "i=/" + encoded[1:],
        "internal_space": raw.replace("v=3", "v= 3", 1),
        "internal_newline": raw.replace("&r=", "\n&r=", 1),
        "oversized": f"{raw}{'A' * 600}",
    }
    return mutations[name]


@pytest.mark.parametrize(
    "name",
    [
        "wrong_scheme",
        "uppercase_scheme",
        "wrong_action",
        "userinfo",
        "path",
        "fragment",
        "missing_version",
        "wrong_version",
        "encoded_version",
        "duplicate_version",
        "duplicate_profile",
        "duplicate_envelope",
        "unknown_key",
        "mixed_v2",
        "missing_profile",
        "missing_envelope",
        "empty_profile",
        "empty_envelope",
        "query_reordered",
        "percent_profile",
        "plus_profile",
        "unicode_profile",
        "unicode_action",
        "base64_padding",
        "base64_plus",
        "base64_slash",
        "internal_space",
        "internal_newline",
        "oversized",
    ],
)
def test_v3_parser_rejects_noncanonical_or_mixed_urls(name):
    raw = _raw_link()
    candidate = _structural_mutation(name, raw)
    with pytest.raises(RemoteInvitationError) as caught:
        parse_remote_invitation_link(candidate, allowed_profiles=ALLOWED)
    assert candidate not in str(caught.value)
    assert CAPABILITY.hex() not in str(caught.value)
    expected = (
        RemoteInvitationErrorCode.INCOMPATIBLE
        if name == "wrong_version"
        else RemoteInvitationErrorCode.MALFORMED
    )
    assert caught.value.code is expected


def test_v3_parser_allows_only_profiles_selected_out_of_band():
    raw = _raw_link()
    with pytest.raises(RemoteInvitationError) as caught:
        parse_remote_invitation_link(raw, allowed_profiles={"another-profile"})
    assert caught.value.code is RemoteInvitationErrorCode.UNTRUSTED_PROFILE
    assert PROFILE not in str(caught.value)


def _payload_mutation(name: str, raw: str) -> str:
    payload = _payload(raw)
    if name == "wrong_magic":
        payload[0:4] = b"WJ2\x01"
    elif name == "zero_session":
        payload[21:37] = bytes(16)
    elif name == "zero_invite":
        payload[37:53] = bytes(16)
    elif name == "same_refs":
        payload[37:53] = payload[21:37]
    elif name == "zero_capability":
        payload[53:85] = bytes(32)
    elif name == "zero_host_pin":
        payload[85:117] = bytes(32)
    elif name == "participant_limit_zero":
        payload[20] = 0
    elif name == "participant_limit_two":
        payload[20] = 2
    elif name == "zero_lifetime":
        struct.pack_into("!Q", payload, 12, ISSUED_AT)
    elif name == "reversed_lifetime":
        struct.pack_into("!Q", payload, 12, ISSUED_AT - 1)
    elif name == "excessive_lifetime":
        struct.pack_into(
            "!Q",
            payload,
            12,
            ISSUED_AT + MAX_INVITATION_TTL_SECONDS + 1,
        )
    elif name == "truncated":
        payload.pop()
    elif name == "extended":
        payload.append(1)
    else:  # pragma: no cover - the parametrized table is exhaustive
        raise AssertionError(name)
    return _replace_envelope(raw, payload)


@pytest.mark.parametrize(
    "name",
    [
        "wrong_magic",
        "zero_session",
        "zero_invite",
        "same_refs",
        "zero_capability",
        "zero_host_pin",
        "participant_limit_zero",
        "participant_limit_two",
        "zero_lifetime",
        "reversed_lifetime",
        "excessive_lifetime",
        "truncated",
        "extended",
    ],
)
def test_v3_parser_rejects_invalid_fixed_envelopes(name):
    with pytest.raises(RemoteInvitationError) as caught:
        parse_remote_invitation_link(
            _payload_mutation(name, _raw_link()),
            allowed_profiles=ALLOWED,
        )
    expected = (
        RemoteInvitationErrorCode.INCOMPLETE
        if name == "truncated"
        else RemoteInvitationErrorCode.MALFORMED
    )
    assert caught.value.code is expected


def test_v3_parser_rejects_every_noncanonical_envelope_length_without_crashing():
    prefix = f"webjam://join?v=3&r={PROFILE}&i="
    for length in range(0, 221):
        with pytest.raises(RemoteInvitationError):
            parse_remote_invitation_link(
                prefix + ("A" * length),
                allowed_profiles=ALLOWED,
            )


def test_parser_fuzz_table_fails_closed_with_only_safe_domain_errors():
    rng = random.Random(0x574A33)
    alphabet = "abcXYZ019_-=&%+/.:?# \n\x00éο"
    for _index in range(400):
        candidate = "".join(
            rng.choice(alphabet) for _ in range(rng.randrange(0, 300))
        )
        with pytest.raises(RemoteInvitationError) as caught:
            parse_remote_invitation_link(candidate, allowed_profiles=ALLOWED)
        assert caught.value.code in set(RemoteInvitationErrorCode)
        assert candidate not in str(caught.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"ttl_seconds": 0},
        {"ttl_seconds": 1.5},
        {"ttl_seconds": MAX_INVITATION_TTL_SECONDS + 1},
        {"session_reference": b""},
        {"session_reference": bytes(16)},
        {"invite_reference": b""},
        {"invite_reference": bytes(16)},
        {"invite_reference": SESSION_REFERENCE},
        {"enrollment_capability": b""},
        {"enrollment_capability": bytes(32)},
        {"host_spki_sha256": bytes(32)},
        {"host_spki_sha256": b"short"},
    ],
)
def test_issue_rejects_invalid_lifetime_and_security_material(overrides):
    with pytest.raises(ValueError):
        _issued(**overrides)


def test_lifecycle_reservation_is_idempotent_then_consumption_is_terminal():
    invitation = _issued().invitation
    lifecycle = InvitationLifecycle(invitation, clock=lambda: ISSUED_AT)
    claim = _claim(invitation)

    assert lifecycle.snapshot().state is InvitationState.ISSUED
    first = lifecycle.reserve(claim)
    again = lifecycle.reserve(claim)
    assert first.state is InvitationState.RESERVED
    assert first.newly_reserved is True
    assert again.newly_reserved is False
    assert lifecycle.consume(claim) is True
    assert lifecycle.consume(claim) is False
    assert lifecycle.snapshot().state is InvitationState.CONSUMED
    with pytest.raises(InvitationLifecycleError) as replay:
        lifecycle.reserve(claim)
    assert replay.value.code is InvitationLifecycleErrorCode.CONSUMED
    forged_after_use = _copy_claim(
        claim,
        session_reference=bytes.fromhex("99" * 16),
        enrollment_capability=bytes.fromhex("aa" * 32),
    )
    with pytest.raises(InvitationLifecycleError) as terminal:
        lifecycle.reserve(forged_after_use)
    assert terminal.value.code is InvitationLifecycleErrorCode.CONSUMED
    with pytest.raises(InvitationLifecycleError) as revoke:
        lifecycle.revoke()
    assert revoke.value.code is InvitationLifecycleErrorCode.CONSUMED


def test_different_guest_or_claim_cannot_share_one_reservation():
    invitation = _issued().invitation
    lifecycle = InvitationLifecycle(invitation, clock=lambda: ISSUED_AT)
    first = _claim(invitation)
    different_claim = _claim(
        invitation,
        claim_reference=bytes.fromhex("66" * 16),
    )
    different_guest = _claim(
        invitation,
        guest=b"guest-two-ephemeral-public-key",
    )
    lifecycle.reserve(first)
    for candidate in (different_claim, different_guest):
        with pytest.raises(InvitationLifecycleError) as caught:
            lifecycle.reserve(candidate)
        assert caught.value.code is InvitationLifecycleErrorCode.REPLAY
    assert lifecycle.snapshot().state is InvitationState.RESERVED


def test_two_concurrent_guests_have_exactly_one_atomic_winner():
    invitation = _issued().invitation
    lifecycle = InvitationLifecycle(invitation, clock=lambda: ISSUED_AT)
    claims = (
        _claim(
            invitation,
            guest=b"guest-one-public-key",
            claim_reference=b"1" * 16,
        ),
        _claim(
            invitation,
            guest=b"guest-two-public-key",
            claim_reference=b"2" * 16,
        ),
    )
    barrier = threading.Barrier(2)
    results: list[tuple[str, object]] = []
    result_lock = threading.Lock()

    def reserve(candidate: EnrollmentClaim) -> None:
        barrier.wait(timeout=2)
        try:
            outcome: tuple[str, object] = ("won", lifecycle.reserve(candidate))
        except InvitationLifecycleError as exc:
            outcome = ("lost", exc.code)
        with result_lock:
            results.append(outcome)

    threads = [threading.Thread(target=reserve, args=(item,)) for item in claims]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert sorted(kind for kind, _value in results) == ["lost", "won"]
    assert next(value for kind, value in results if kind == "lost") is (
        InvitationLifecycleErrorCode.REPLAY
    )
    assert lifecycle.snapshot().state is InvitationState.RESERVED


def test_expiry_is_monotonic_even_if_the_observed_clock_moves_backward():
    now = [ISSUED_AT]
    invitation = _issued(ttl_seconds=10).invitation
    lifecycle = InvitationLifecycle(invitation, clock=lambda: now[0])
    assert lifecycle.snapshot().state is InvitationState.ISSUED
    now[0] = ISSUED_AT + 10
    assert lifecycle.snapshot().state is InvitationState.EXPIRED
    now[0] = ISSUED_AT - 10_000
    assert lifecycle.snapshot().state is InvitationState.EXPIRED
    assert lifecycle.revoke() is False
    with pytest.raises(InvitationLifecycleError) as caught:
        lifecycle.reserve(_claim(invitation))
    assert caught.value.code is InvitationLifecycleErrorCode.EXPIRED


def test_revocation_is_idempotent_and_a_new_invite_never_revives_the_old_one():
    old_invitation = _issued().invitation
    old = InvitationLifecycle(old_invitation, clock=lambda: ISSUED_AT)
    assert old.revoke() is True
    assert old.revoke() is False
    with pytest.raises(InvitationLifecycleError) as revoked:
        old.reserve(_claim(old_invitation))
    assert revoked.value.code is InvitationLifecycleErrorCode.REVOKED

    replacement = _issued(
        invite_reference=bytes.fromhex("77" * 16),
        enrollment_capability=bytes.fromhex("88" * 32),
    ).invitation
    current = InvitationLifecycle(replacement, clock=lambda: ISSUED_AT)
    assert current.reserve(_claim(replacement)).newly_reserved is True
    assert old.snapshot().state is InvitationState.REVOKED


def _copy_claim(base: EnrollmentClaim, **changes) -> EnrollmentClaim:
    values = {
        "protocol_version": base.protocol_version,
        "session_reference": base.session_reference,
        "invite_reference": base.invite_reference,
        "claim_reference": base.claim_reference,
        "guest_key_sha256": base.guest_key_sha256,
        "enrollment_capability": base.capability_for_host(),
        **changes,
    }
    return EnrollmentClaim(**values)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"protocol_version": 2}, InvitationLifecycleErrorCode.DOWNGRADE),
        (
            {"protocol_version": 4},
            InvitationLifecycleErrorCode.VERSION_MISMATCH,
        ),
        (
            {"session_reference": bytes.fromhex("99" * 16)},
            InvitationLifecycleErrorCode.CROSS_SESSION,
        ),
        (
            {"invite_reference": bytes.fromhex("aa" * 16)},
            InvitationLifecycleErrorCode.WRONG_INVITATION,
        ),
        (
            {"enrollment_capability": bytes.fromhex("bb" * 32)},
            InvitationLifecycleErrorCode.INVALID_CAPABILITY,
        ),
    ],
)
def test_lifecycle_rejects_downgrade_cross_session_and_forged_claims(
    changes,
    expected,
):
    invitation = _issued().invitation
    lifecycle = InvitationLifecycle(invitation, clock=lambda: ISSUED_AT)
    candidate = _copy_claim(_claim(invitation), **changes)
    with pytest.raises(InvitationLifecycleError) as caught:
        lifecycle.reserve(candidate)
    assert caught.value.code is expected
    assert lifecycle.snapshot().state is InvitationState.ISSUED
    assert CAPABILITY.hex() not in str(caught.value)


def test_host_restart_session_binding_rejects_an_old_claim():
    old_invitation = _issued().invitation
    old_claim = _claim(old_invitation)
    restarted_invitation = _issued(
        session_reference=bytes.fromhex("cc" * 16),
        invite_reference=bytes.fromhex("dd" * 16),
        enrollment_capability=bytes.fromhex("ee" * 32),
        host_spki_sha256=bytes.fromhex("ff" * 32),
    ).invitation
    restarted = InvitationLifecycle(restarted_invitation, clock=lambda: ISSUED_AT)
    with pytest.raises(InvitationLifecycleError) as caught:
        restarted.reserve(old_claim)
    assert caught.value.code is InvitationLifecycleErrorCode.CROSS_SESSION


def test_capability_bit_change_is_well_formed_but_fails_host_lifecycle_binding():
    raw = _raw_link()
    payload = _payload(raw)
    payload[53] ^= 0x01
    altered = parse_remote_invitation_link(
        _replace_envelope(raw, payload),
        allowed_profiles=ALLOWED,
    )
    original = _issued().invitation
    lifecycle = InvitationLifecycle(original, clock=lambda: ISSUED_AT)
    forged = _claim(altered)
    with pytest.raises(InvitationLifecycleError) as caught:
        lifecycle.reserve(forged)
    assert caught.value.code is InvitationLifecycleErrorCode.INVALID_CAPABILITY


def test_legacy_v1_v2_output_and_parsing_remain_compatible_but_repr_is_safe():
    credentials = SessionCredentials.create()
    legacy = create_invite_link("192.168.1.42", session_name="Legacy")
    private = create_invite_link(
        "192.168.1.42",
        session_name="Private Session",
        session_id=credentials.session_id,
        peer_port=43121,
        invite_token=credentials.invite_token,
    )
    parsed_legacy = parse_invite_link(legacy)
    parsed_private = parse_invite_link(private)
    assert legacy.startswith("webjam://join?v=1&host=192.168.1.42")
    assert private.startswith("webjam://join?v=2&host=192.168.1.42")
    assert parsed_legacy.peer_enabled is False
    assert parsed_private.peer_enabled is True
    assert parsed_legacy.version == 1
    assert parsed_private.version == 2
    assert parsed_legacy.is_remote is False
    assert _issued().invitation.is_remote is True
    rendered = repr(parsed_private)
    for secret in (
        credentials.session_id,
        credentials.invite_token,
        "192.168.1.42",
        "Private Session",
    ):
        assert secret not in rendered


def test_existing_session_secret_and_enrollment_repr_are_safe():
    credentials = SessionCredentials.create()
    enrollment = ParticipantEnrollment(
        participant_id="11111111-1111-4111-8111-111111111111",
        installation_id="22222222-2222-4222-8222-222222222222",
        display_name="Private Musician",
        participant_token="t" * 43,
    )
    rendered = f"{credentials!r}\n{enrollment!r}"
    for secret in (
        credentials.session_id,
        credentials.invite_token,
        enrollment.participant_id,
        enrollment.installation_id,
        enrollment.display_name,
        enrollment.participant_token,
    ):
        assert secret not in rendered
    assert rendered.count("[redacted]") == 2


def test_shared_parser_dispatches_v3_without_a_plaintext_fallback():
    raw = _raw_link()

    parsed = parse_invite_link(raw)

    assert parsed.version == 3
    assert parsed.profile_id == PROFILE
    mixed = f"{raw}&host=192.168.1.42&port=22124"
    with pytest.raises(InviteLinkError) as caught:
        parse_invite_link(mixed)
    assert mixed not in str(caught.value)


def test_shared_parser_rejects_a_v3_profile_outside_its_exact_allowlist():
    with pytest.raises(InviteLinkError, match="unavailable"):
        parse_invite_link(
            _raw_link(),
            allowed_remote_profiles=frozenset({"another-profile"}),
        )
