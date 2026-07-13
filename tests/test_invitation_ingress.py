from __future__ import annotations

import pytest

from core.network_invite import BandInvite, create_invite_link
from core.remote_invitation import RemoteInvitation, issue_remote_invitation
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationIngressErrorCode,
    InvitationSource,
    invitation_from_arguments,
    parse_invitation_at_ingress,
)


PROFILE = "reference-local"
ALLOWED = frozenset({PROFILE})


def _remote_link() -> str:
    issued = issue_remote_invitation(
        PROFILE,
        allowed_profiles=ALLOWED,
        host_spki_sha256=bytes.fromhex("44" * 32),
        issued_at_unix=1_800_000_000,
        session_reference=bytes.fromhex("11" * 16),
        invite_reference=bytes.fromhex("22" * 16),
        enrollment_capability=bytes.fromhex("33" * 32),
    )
    return issued.private_link.reveal_for_clipboard()


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_v3_is_accepted_from_explicit_paste_on_every_desktop(platform: str) -> None:
    parsed = parse_invitation_at_ingress(
        _remote_link(),
        source=InvitationSource.PASTE,
        platform=platform,
        allowed_remote_profiles=ALLOWED,
    )
    assert isinstance(parsed, RemoteInvitation)
    assert parsed.is_remote is True


def test_v3_file_open_is_macos_only_and_argv_is_always_rejected() -> None:
    raw = _remote_link()
    parsed = parse_invitation_at_ingress(
        raw,
        source=InvitationSource.MAC_FILE_OPEN,
        platform="darwin",
        allowed_remote_profiles=ALLOWED,
    )
    assert isinstance(parsed, RemoteInvitation)

    for source, platform in (
        (InvitationSource.MAC_FILE_OPEN, "win32"),
        (InvitationSource.MAC_FILE_OPEN, "linux"),
        (InvitationSource.ARGV, "darwin"),
        (InvitationSource.ARGV, "win32"),
    ):
        with pytest.raises(InvitationIngressError) as caught:
            parse_invitation_at_ingress(
                raw,
                source=source,
                platform=platform,
                allowed_remote_profiles=ALLOWED,
            )
        assert caught.value.code is InvitationIngressErrorCode.SOURCE_NOT_ALLOWED
        assert raw not in str(caught.value)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(
    ("source", "platform"),
    [
        (InvitationSource.PASTE, "darwin"),
        (InvitationSource.PASTE, "win32"),
        (InvitationSource.MAC_FILE_OPEN, "darwin"),
        (InvitationSource.ARGV, "darwin"),
        (InvitationSource.ARGV, "win32"),
    ],
)
def test_legacy_links_keep_existing_ingress_paths(
    version: int, source: InvitationSource, platform: str
) -> None:
    kwargs = {}
    if version == 2:
        kwargs = {
            "session_id": "11111111-1111-4111-8111-111111111111",
            "peer_port": 43121,
            "invite_token": "t" * 43,
        }
    raw = create_invite_link("192.168.1.42", **kwargs)
    parsed = parse_invitation_at_ingress(
        raw,
        source=source,
        platform=platform,
    )
    assert isinstance(parsed, BandInvite)
    assert parsed.version == version


def test_bare_legacy_address_is_paste_only() -> None:
    parsed = parse_invitation_at_ingress(
        "192.168.1.42:22124",
        source=InvitationSource.PASTE,
    )
    assert isinstance(parsed, BandInvite)
    for source in (InvitationSource.ARGV, InvitationSource.MAC_FILE_OPEN):
        with pytest.raises(InvitationIngressError):
            parse_invitation_at_ingress(
                "192.168.1.42:22124",
                source=source,
                platform="darwin",
            )


def test_argv_helper_ignores_v3_and_finds_a_later_v2_link() -> None:
    remote = _remote_link()
    legacy = create_invite_link(
        "192.168.1.42",
        session_id="11111111-1111-4111-8111-111111111111",
        peer_port=43121,
        invite_token="t" * 43,
    )

    parsed = invitation_from_arguments(["WebJam", remote, legacy])

    assert isinstance(parsed, BandInvite)
    assert parsed.version == 2
    assert remote not in repr(parsed)
    assert invitation_from_arguments(["WebJam", remote]) is None


def test_ingress_errors_have_only_fixed_copy_and_no_exception_chain() -> None:
    raw = "webjam://join?v=3&r=reference-local&i=PRIVATE-CAPABILITY-SENTINEL"
    with pytest.raises(InvitationIngressError) as caught:
        parse_invitation_at_ingress(
            raw,
            source=InvitationSource.PASTE,
            allowed_remote_profiles=ALLOWED,
        )
    assert caught.value.code is InvitationIngressErrorCode.INVALID
    assert "PRIVATE-CAPABILITY-SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is not None
    assert caught.value.__suppress_context__ is True


def test_untrusted_profile_is_rejected_without_echoing_it() -> None:
    raw = _remote_link().replace("reference-local", "untrusted-profile")
    with pytest.raises(InvitationIngressError) as caught:
        parse_invitation_at_ingress(
            raw,
            source=InvitationSource.PASTE,
            allowed_remote_profiles=ALLOWED,
        )
    assert caught.value.code is InvitationIngressErrorCode.INVALID
    assert "untrusted-profile" not in str(caught.value)
