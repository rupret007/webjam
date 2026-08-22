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
def test_v1_links_keep_existing_ingress_paths(
    source: InvitationSource, platform: str
) -> None:
    raw = create_invite_link("192.168.1.42")
    parsed = parse_invitation_at_ingress(
        raw,
        source=source,
        platform=platform,
    )
    assert isinstance(parsed, BandInvite)
    assert parsed.version == 1


@pytest.mark.parametrize(
    ("source", "platform"),
    [
        (InvitationSource.PASTE, "darwin"),
        (InvitationSource.PASTE, "win32"),
        (InvitationSource.MAC_FILE_OPEN, "darwin"),
    ],
)
def test_v2_private_links_remain_available_from_approved_ingress(
    source: InvitationSource, platform: str
) -> None:
    raw = create_invite_link(
        "192.168.1.42",
        session_id="11111111-1111-4111-8111-111111111111",
        peer_port=43121,
        invite_token="t" * 43,
    )

    parsed = parse_invitation_at_ingress(raw, source=source, platform=platform)

    assert isinstance(parsed, BandInvite)
    assert parsed.version == 2
    assert parsed.peer_enabled is True


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_v2_private_links_are_rejected_from_argv_without_echoing_bearer(
    platform: str,
) -> None:
    token = "t" * 43
    raw = create_invite_link(
        "192.168.1.42",
        session_id="11111111-1111-4111-8111-111111111111",
        peer_port=43121,
        invite_token=token,
    )

    with pytest.raises(InvitationIngressError) as caught:
        parse_invitation_at_ingress(
            raw,
            source=InvitationSource.ARGV,
            platform=platform,
        )

    assert caught.value.code is InvitationIngressErrorCode.SOURCE_NOT_ALLOWED
    assert token not in str(caught.value)


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


def test_argv_helper_ignores_bearer_links_and_finds_a_later_v1_link() -> None:
    remote = _remote_link()
    private = create_invite_link(
        "192.168.1.42",
        session_id="11111111-1111-4111-8111-111111111111",
        peer_port=43121,
        invite_token="t" * 43,
    )
    legacy = create_invite_link("192.168.1.43")

    parsed = invitation_from_arguments(["WebJam", remote, private, legacy])

    assert isinstance(parsed, BandInvite)
    assert parsed.version == 1
    assert remote not in repr(parsed)
    assert invitation_from_arguments(["WebJam", remote, private]) is None


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


# ----------------------------------------------------------------------
# One invite: a paste is no longer always a bare URL
# ----------------------------------------------------------------------
def test_the_link_is_found_inside_the_one_invite_message() -> None:
    """Copy Invite now sends a short message, and bandmates forward it."""

    from core.meeting_companion import build_invite_message

    link = create_invite_link("192.168.1.5", port=22124, session_name="Tuesday Jam")
    message = build_invite_message(
        join_link=link,
        session_name="Tuesday Jam",
        meeting_url="https://band.webex.com/meet/jeff",
    )

    parsed = parse_invitation_at_ingress(
        message.text, source=InvitationSource.PASTE
    )
    assert isinstance(parsed, BandInvite)


def test_a_v3_link_still_arrives_intact_from_a_surrounding_message() -> None:
    raw = f"Here you go!\n{_remote_link()}\n\n-- sent from my phone"
    parsed = parse_invitation_at_ingress(
        raw,
        source=InvitationSource.PASTE,
        allowed_remote_profiles=ALLOWED,
    )
    assert isinstance(parsed, RemoteInvitation)


def test_quoting_and_punctuation_around_a_forwarded_link_are_tolerated() -> None:
    link = create_invite_link("192.168.1.5", port=22124, session_name="Tuesday Jam")
    for decorated in (f"<{link}>", f'"{link}"', f"{link},", f"> {link}"):
        parsed = parse_invitation_at_ingress(
            decorated, source=InvitationSource.PASTE
        )
        assert isinstance(parsed, BandInvite)


def test_two_different_jam_links_in_one_paste_are_ambiguous_not_guessed() -> None:
    first = create_invite_link("192.168.1.5", port=22124, session_name="One")
    second = create_invite_link("192.168.1.9", port=22124, session_name="Two")
    with pytest.raises(InvitationIngressError) as caught:
        parse_invitation_at_ingress(
            f"{first}\n{second}", source=InvitationSource.PASTE
        )
    assert caught.value.code is InvitationIngressErrorCode.INVALID


def test_the_same_link_repeated_by_a_quoting_mail_client_is_fine() -> None:
    link = create_invite_link("192.168.1.5", port=22124, session_name="Tuesday Jam")
    parsed = parse_invitation_at_ingress(
        f"{link}\n\n> {link}", source=InvitationSource.PASTE
    )
    assert isinstance(parsed, BandInvite)


def test_an_enormous_paste_is_refused_rather_than_scanned() -> None:
    link = create_invite_link("192.168.1.5", port=22124, session_name="Tuesday Jam")
    with pytest.raises(InvitationIngressError) as caught:
        parse_invitation_at_ingress(
            ("x" * 9000) + "\n" + link, source=InvitationSource.PASTE
        )
    assert caught.value.code is InvitationIngressErrorCode.INVALID


def test_extraction_is_paste_only_so_argv_can_never_carry_a_bearer() -> None:
    raw = f"Join us\n{_remote_link()}"
    for source in (InvitationSource.ARGV, InvitationSource.MAC_FILE_OPEN):
        with pytest.raises(InvitationIngressError):
            parse_invitation_at_ingress(
                raw,
                source=source,
                platform="darwin",
                allowed_remote_profiles=ALLOWED,
            )


def test_a_paste_with_no_jam_link_is_still_rejected() -> None:
    with pytest.raises(InvitationIngressError):
        parse_invitation_at_ingress(
            "Hi, see you at 8 — https://band.webex.com/meet/jeff",
            source=InvitationSource.PASTE,
        )
