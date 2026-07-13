from __future__ import annotations

import logging
import threading

import pytest

from core.remote_invitation import (
    EnrollmentClaim,
    InvitationLifecycleError,
    parse_remote_invitation_link,
)
from services.remote_invitation_owner import (
    RemoteInvitationOwner,
    RemoteInvitationOwnerError,
)


class Clock:
    def __init__(self, value: int = 1_800_000_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class Registrar:
    def __init__(self) -> None:
        self.registered = []
        self.revoked = []

    def register_invitation(self, invitation) -> None:
        self.registered.append(invitation)

    def revoke_invitation(self, invitation) -> None:
        self.revoked.append(invitation)


def _owner(registrar=None, clock=None) -> RemoteInvitationOwner:
    return RemoteInvitationOwner(
        registrar or Registrar(),
        profile_id="reference-local",
        allowed_profiles=frozenset({"reference-local"}),
        host_spki_sha256=bytes.fromhex("44" * 32),
        clock=clock or Clock(),
    )


def _claim(invitation, marker: int = 1):
    return EnrollmentClaim.for_invitation(
        invitation,
        guest_public_key=bytes([marker]) * 32,
        claim_reference=bytes([marker + 1]) * 16,
    )


def test_owner_retains_no_url_and_serializes_only_for_copy() -> None:
    registrar = Registrar()
    owner = _owner(registrar)
    owner.start(session_reference=bytes.fromhex("11" * 16))

    raw = owner.copy_for_clipboard()

    assert raw.startswith("webjam://join?v=3")
    assert raw not in repr(owner)
    assert raw not in repr(vars(owner))
    assert registrar.registered == [owner.invitation]
    assert owner.invitation_available is True


def test_reset_revokes_old_first_and_rotates_bearer_for_same_session() -> None:
    registrar = Registrar()
    owner = _owner(registrar)
    owner.start(session_reference=bytes.fromhex("11" * 16))
    old = owner.invitation
    assert old is not None

    owner.reset()

    new = owner.invitation
    assert new is not None
    assert registrar.revoked == [old]
    assert registrar.registered == [old, new]
    assert new.session_reference == old.session_reference
    assert new.invite_reference != old.invite_reference
    assert new.capability_for_enrollment() != old.capability_for_enrollment()
    assert owner.invitation_available is True


def test_one_claim_reserves_and_consumes_then_cannot_be_copied() -> None:
    owner = _owner()
    owner.start()
    invitation = owner.invitation
    assert invitation is not None
    claim = _claim(invitation)

    assert owner.reserve(claim) is True
    assert owner.reserve(claim) is False
    assert owner.consume(claim) is True
    assert owner.consume(claim) is False

    assert owner.invitation_available is False
    with pytest.raises(RemoteInvitationOwnerError, match="fresh"):
        owner.copy_for_clipboard()


def test_authenticated_peer_retires_copy_until_reset_and_ignores_stale_event() -> None:
    owner = _owner()
    owner.start(session_reference=bytes.fromhex("11" * 16))
    first = owner.invitation
    assert first is not None

    assert owner.mark_enrollment_consumed(first) is True
    assert owner.invitation_available is False
    with pytest.raises(RemoteInvitationOwnerError, match="fresh"):
        owner.copy_for_clipboard()

    owner.reset()
    second = owner.invitation
    assert second is not None and second is not first
    assert owner.invitation_available is True
    assert owner.mark_enrollment_consumed(first) is False
    assert owner.invitation_available is True


def test_concurrent_enrollment_allows_exactly_one_guest() -> None:
    owner = _owner()
    owner.start()
    invitation = owner.invitation
    assert invitation is not None
    barrier = threading.Barrier(3)
    results = []

    def reserve(marker: int) -> None:
        barrier.wait()
        try:
            results.append(owner.reserve(_claim(invitation, marker)))
        except InvitationLifecycleError as exc:
            results.append(exc.code.value)

    threads = [threading.Thread(target=reserve, args=(marker,)) for marker in (1, 3)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(1)

    assert results.count(True) == 1
    assert results.count("replay") == 1


def test_failed_replacement_never_restores_revoked_bearer(caplog) -> None:
    sentinel = "PRIVATE-CAPABILITY-SENTINEL"

    class FailingRegistrar(Registrar):
        def register_invitation(self, invitation) -> None:
            super().register_invitation(invitation)
            if len(self.registered) > 1:
                raise RuntimeError(sentinel)

    registrar = FailingRegistrar()
    owner = _owner(registrar)
    owner.start()
    caplog.set_level(logging.ERROR)

    with pytest.raises(RemoteInvitationOwnerError):
        owner.reset()

    assert owner.invitation is None
    assert owner.invitation_available is False
    assert registrar.revoked[-1] is registrar.registered[-1]
    assert sentinel not in caplog.text


def test_stop_revokes_and_drops_all_typed_state() -> None:
    registrar = Registrar()
    owner = _owner(registrar)
    owner.start()
    invitation = owner.invitation

    owner.stop()

    assert registrar.revoked == [invitation]
    assert owner.invitation is None
    assert owner.invitation_available is False
    with pytest.raises(RemoteInvitationOwnerError):
        owner.copy_for_clipboard()


def test_clipboard_round_trip_remains_strict() -> None:
    owner = _owner()
    owner.start()

    parsed = parse_remote_invitation_link(
        owner.copy_for_clipboard(),
        allowed_profiles={"reference-local"},
    )

    assert parsed.session_reference == owner.invitation.session_reference
