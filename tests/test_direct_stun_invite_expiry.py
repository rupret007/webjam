"""Late-join boundaries for complete cross-network invitations."""

from types import SimpleNamespace

import pytest

from core import remote_invitation
from core.direct_stun_invite import encode_host_signal_line
from core.session_transport import ConnectionQuality, TransportPath
from services.remote_session_runtime import (
    RemoteGuestConnection,
    RemoteSessionErrorCode,
    RemoteSessionPhase,
    RemoteSessionRuntime,
)
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationIngressErrorCode,
    InvitationSource,
    parse_invitation_at_ingress,
)


SIGNAL = b"WJSE" + bytes(range(12))


def issue_at(now):
    return remote_invitation.issue_remote_invitation(
        "direct-stun",
        allowed_profiles={"direct-stun"},
        host_spki_sha256=bytes.fromhex("44" * 32),
        issued_at_unix=now,
        ttl_seconds=60,
    )


def invitation_text(issued):
    return (
        f"{issued.private_link.reveal_for_clipboard()}\n"
        f"{encode_host_signal_line(SIGNAL)}"
    )


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("seconds_after_expiry", [0, 1, 3600])
def test_complete_direct_stun_invite_expires_at_ingress(
    monkeypatch, platform, seconds_after_expiry,
):
    issued = issue_at(2_000_000_000)
    expires = issued.invitation.expires_at_unix
    clock = [expires - 1]
    # Replace only the invitation module's clock, leaving runtime deadlines
    # and the test runner's wall/monotonic clocks alone.
    monkeypatch.setattr(remote_invitation, "time", SimpleNamespace(time=lambda: clock[0]))
    text = invitation_text(issued)
    parsed = parse_invitation_at_ingress(text, source=InvitationSource.PASTE, platform=platform)
    assert parsed.host_signal == SIGNAL
    assert parsed.invite_reference == issued.invitation.invite_reference

    clock[0] = expires + seconds_after_expiry
    with pytest.raises(InvitationIngressError) as error:
        parse_invitation_at_ingress(text, source=InvitationSource.PASTE, platform=platform)

    assert error.value.code is InvitationIngressErrorCode.EXPIRED
    assert str(error.value) == "That invitation has expired. Ask the host for a new invitation."
    assert error.value.__cause__ is None
    assert issued.private_link.reveal_for_clipboard() not in str(error.value)


@pytest.mark.parametrize("seconds_after_expiry", [0, 1, 3600])
def test_cached_direct_stun_invite_is_rechecked_before_join_and_fresh_invite_recovers(
    monkeypatch, seconds_after_expiry,
):
    issued = issue_at(2_000_000_000)
    clock = [issued.invitation.expires_at_unix - 1]
    monkeypatch.setattr(remote_invitation, "time", SimpleNamespace(time=lambda: clock[0]))
    cached = parse_invitation_at_ingress(
        invitation_text(issued), source=InvitationSource.PASTE,
    )
    calls = []

    class Backend:
        def start_guest(self, invitation, *, generation):
            calls.append((invitation, generation))
            return RemoteGuestConnection(
                loopback_port=34001,
                path=TransportPath.INTERNET_DIRECT,
                quality=ConnectionQuality.UNKNOWN,
                generation=generation,
            )

        def stop(self):
            pass

    snapshots = []
    runtime = RemoteSessionRuntime(Backend(), on_snapshot=snapshots.append)
    try:
        # The paste was valid, but the musician clicks Join after it expires.
        clock[0] = cached.expires_at_unix + seconds_after_expiry
        assert runtime.start_guest(cached) is False
        failed = runtime.snapshot
        assert failed.phase is RemoteSessionPhase.FAILED
        assert failed.error_code is RemoteSessionErrorCode.EXPIRED
        assert not failed.invitation_retry_safe
        assert failed.loopback_port == 0
        assert calls == []
        assert runtime._pending_invitation is None

        replacement = issue_at(clock[0])
        fresh = parse_invitation_at_ingress(
            invitation_text(replacement), source=InvitationSource.PASTE,
        )
        assert fresh.invite_reference != cached.invite_reference
        assert fresh.capability_for_enrollment() != cached.capability_for_enrollment()
        assert runtime.start_guest(fresh) is True
        connected = runtime.wait_until_settled(timeout=2)
        assert connected.phase is RemoteSessionPhase.CONNECTED
        assert connected.generation == failed.generation + 1
        assert connected.loopback_port == 34001
        assert calls == [(fresh, connected.generation)]
        assert runtime._pending_invitation is None
        assert cached.capability_for_enrollment().hex() not in repr(snapshots)
        assert fresh.capability_for_enrollment().hex() not in repr(snapshots)
    finally:
        runtime.stop()
