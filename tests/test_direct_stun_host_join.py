from __future__ import annotations

import os

import pytest

from core.direct_stun_invite import (
    DIRECT_STUN_PROFILE_ID,
    encode_guest_reply_line,
    encode_host_signal_line,
    parse_guest_reply_from_paste,
    parse_host_signal_from_paste,
)
from core.meeting_companion import build_invite_message
from core.remote_invitation import issue_remote_invitation
from services.native_remote_transport import ordinary_native_host_profile
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationIngressErrorCode,
    InvitationSource,
    parse_invitation_at_ingress,
)

SIGNAL = b"WJSE" + bytes(range(12))


def _direct_link() -> str:
    issued = issue_remote_invitation(
        DIRECT_STUN_PROFILE_ID,
        allowed_profiles={DIRECT_STUN_PROFILE_ID},
        host_spki_sha256=os.urandom(32),
    )
    return issued.private_link.reveal_for_clipboard()


def test_host_signal_round_trip_survives_quoted_paste() -> None:
    line = encode_host_signal_line(SIGNAL)
    paste = f"Join this jam on WebJam:\n> {line}\n{_direct_link()}\n"
    assert parse_host_signal_from_paste(paste) == SIGNAL


def test_guest_reply_round_trip_survives_quoted_paste() -> None:
    line = encode_guest_reply_line(SIGNAL)
    paste = f"Here is my reply:\n> {line}\n"
    assert parse_guest_reply_from_paste(paste) == SIGNAL
    assert parse_host_signal_from_paste(paste) == b""


def test_ordinary_host_stays_on_lan_without_sidecar(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WEBJAM_ENABLE_REFERENCE_LOCAL", raising=False)
    monkeypatch.delenv("WEBJAM_TRANSPORT_BINARY", raising=False)
    monkeypatch.delenv("WEBJAM_RUN_REMOTE_SIDECAR_INTEGRATION", raising=False)
    monkeypatch.setattr(
        "services.native_remote_transport.transport_binary_path",
        lambda: tmp_path / "missing-webjam-fabric",
    )
    assert ordinary_native_host_profile() is None


def test_ordinary_host_uses_direct_stun_when_sidecar_exists(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "webjam-fabric"
    binary.write_bytes(b"\x00")
    binary.chmod(0o755)
    monkeypatch.delenv("WEBJAM_ENABLE_REFERENCE_LOCAL", raising=False)
    monkeypatch.delenv("WEBJAM_RUN_REMOTE_SIDECAR_INTEGRATION", raising=False)
    monkeypatch.setattr(
        "services.native_remote_transport.transport_binary_path",
        lambda: binary,
    )
    assert ordinary_native_host_profile() == "direct-stun"


def test_lab_flag_keeps_reference_local_even_with_sidecar(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "webjam-fabric"
    binary.write_bytes(b"\x00")
    binary.chmod(0o755)
    monkeypatch.setenv("WEBJAM_ENABLE_REFERENCE_LOCAL", "1")
    monkeypatch.setattr(
        "services.native_remote_transport.transport_binary_path",
        lambda: binary,
    )
    assert ordinary_native_host_profile() == "reference-local"


def test_sidecar_integration_job_does_not_flip_ordinary_host(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "webjam-fabric"
    binary.write_bytes(b"\x00")
    binary.chmod(0o755)
    monkeypatch.delenv("WEBJAM_ENABLE_REFERENCE_LOCAL", raising=False)
    monkeypatch.setenv("WEBJAM_RUN_REMOTE_SIDECAR_INTEGRATION", "1")
    monkeypatch.setattr(
        "services.native_remote_transport.transport_binary_path",
        lambda: binary,
    )
    assert ordinary_native_host_profile() is None


def test_direct_stun_paste_requires_the_signal_line() -> None:
    link = _direct_link()
    with pytest.raises(InvitationIngressError) as exc:
        parse_invitation_at_ingress(link, source=InvitationSource.PASTE)
    assert exc.value.code is InvitationIngressErrorCode.INCOMPLETE


def test_direct_stun_paste_attaches_opaque_signal() -> None:
    link = _direct_link()
    paste = build_invite_message(
        join_link=f"{link}\n{encode_host_signal_line(SIGNAL)}",
        same_network_required=False,
    ).text
    invitation = parse_invitation_at_ingress(paste, source=InvitationSource.PASTE)
    assert invitation.profile_id == DIRECT_STUN_PROFILE_ID
    assert invitation.host_signal == SIGNAL


def test_direct_stun_public_share_omits_lan_limitation() -> None:
    message = build_invite_message(
        join_link=_direct_link(),
        same_network_required=False,
    )
    assert "Wi-Fi" not in message.text
    assert "local network" not in message.text


def test_direct_stun_local_only_share_keeps_honest_lan_line() -> None:
    message = build_invite_message(
        join_link=_direct_link(),
        same_network_required=True,
    )
    assert "same Wi-Fi or local network" in message.text
