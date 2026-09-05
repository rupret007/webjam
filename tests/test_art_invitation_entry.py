"""Art invitation copy must lead to a supported, private paste entry."""

from __future__ import annotations

import pytest

from core.meeting_companion import build_invite_message
from core.network_invite import BandInvite, create_invite_link
from core.remote_invitation import RemoteInvitation, issue_remote_invitation
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationIngressErrorCode,
    InvitationSource,
    invitation_from_arguments,
    parse_invitation_at_ingress,
)


PASTE_INSTRUCTION = "Open WebJam, choose Join, then paste this full invitation."
PLATFORMS = ("darwin", "win32", "linux")
MEETING_URLS = ("", "https://band.webex.com/meet/artist", "https://zoom.us/j/123")


def _lan_link():
    return create_invite_link(
        "192.168.1.42",
        session_name="Moon Study",
        session_id="11111111-1111-4111-8111-111111111111",
        peer_port=43121,
        invite_token="t" * 43,
    )


def _remote_issue():
    return issue_remote_invitation(
        "reference-local",
        allowed_profiles={"reference-local"},
        host_spki_sha256=b"p" * 32,
    )


def _copy_without_link(message, link):
    # Keep assertion diagnostics separate from the private clipboard payload.
    lines = message.text.splitlines()
    exact_link_count = lines.count(link)
    invitation_count = sum(line.startswith("webjam://") for line in lines)
    assert exact_link_count == 1
    assert invitation_count == 1
    return "\n".join(line for line in lines if line != link)


def _assert_no_scope_claim(copy):
    for claim in ("same Wi-Fi", "local network", "internet", "anywhere", "public service"):
        assert claim not in copy


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("meeting_url", MEETING_URLS)
def test_art_lan_message_gives_portable_entry_and_preserves_private_invitation(
    platform, meeting_url,
):
    link = _lan_link()
    message = build_invite_message(
        join_link=link,
        session_name="Moon Study",
        creator_profile_key="art",
        same_network_required=True,
        meeting_url=meeting_url,
        song_line="A saved Music song",
    )
    copy = _copy_without_link(message, link)
    assert PASTE_INSTRUCTION in copy
    assert "same Wi-Fi or local network as the host" in copy
    assert "host needs to keep this room open" in copy
    assert "own tools, paper, or usual app" in copy
    assert "Song:" not in copy
    assert "Open the link in WebJam" not in copy
    assert message.includes_meeting is bool(meeting_url)
    if meeting_url:
        assert meeting_url in copy
        assert "conversation and work sharing" in copy
        assert "separate and optional" in copy
        assert "WebJam does not run it" in copy
    else:
        assert "without a meeting" in copy

    parsed = parse_invitation_at_ingress(
        message.text, source=InvitationSource.PASTE, platform=platform,
    )
    assert isinstance(parsed, BandInvite)
    assert parsed.peer_enabled
    preserved = (
        parsed.host == "192.168.1.42"
        and parsed.session_name == "Moon Study"
        and parsed.session_id == "11111111-1111-4111-8111-111111111111"
        and parsed.peer_port == 43121
        and parsed.invite_token == "t" * 43
    )
    assert preserved


@pytest.mark.parametrize("platform", PLATFORMS)
def test_art_native_message_preserves_paste_without_guessing_network_scope(platform):
    issued = _remote_issue()
    link = issued.private_link.reveal_for_clipboard()
    message = build_invite_message(join_link=link, creator_profile_key="art")
    copy = _copy_without_link(message, link)
    assert PASTE_INSTRUCTION in copy
    _assert_no_scope_claim(copy)
    parsed = parse_invitation_at_ingress(
        message.text, source=InvitationSource.PASTE, platform=platform,
    )
    assert isinstance(parsed, RemoteInvitation)
    preserved = (
        parsed.host_spki_sha256 == issued.invitation.host_spki_sha256
        and parsed.capability_for_enrollment() == issued.invitation.capability_for_enrollment()
        and parsed.session_reference == issued.invitation.session_reference
        and parsed.invite_reference == issued.invitation.invite_reference
    )
    assert preserved


@pytest.mark.parametrize("kind", ("private_lan", "legacy_endpoint", "unknown"))
def test_art_copy_requires_explicit_host_scope_instead_of_inspecting_url(kind):
    if kind == "private_lan":
        link = _lan_link()
    elif kind == "legacy_endpoint":
        link = create_invite_link("203.0.113.42")
    else:
        link = "webjam://join?v=99&opaque=unverified"
    message = build_invite_message(join_link=link, creator_profile_key="art")
    copy = _copy_without_link(message, link)
    assert PASTE_INSTRUCTION in copy
    _assert_no_scope_claim(copy)
    if kind != "unknown":
        parsed = parse_invitation_at_ingress(message.text, source=InvitationSource.PASTE)
        assert isinstance(parsed, BandInvite)
        assert parsed.peer_enabled is (kind == "private_lan")


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("kind", ("private_lan", "native"))
def test_art_private_invitation_copy_does_not_enable_bearer_process_arguments(platform, kind):
    link = _lan_link() if kind == "private_lan" else _remote_issue().private_link.reveal_for_clipboard()
    message = build_invite_message(
        join_link=link, creator_profile_key="art", same_network_required=kind == "private_lan",
    )
    _copy_without_link(message, link)
    with pytest.raises(InvitationIngressError) as caught:
        parse_invitation_at_ingress(link, source=InvitationSource.ARGV, platform=platform)
    assert caught.value.code is InvitationIngressErrorCode.SOURCE_NOT_ALLOWED
    exposes_link = link in str(caught.value) or link in repr(caught.value)
    assert not exposes_link
    argv_invitation = invitation_from_arguments(("WebJam", link))
    assert argv_invitation is None


@pytest.mark.parametrize("kind", ("private_lan", "native"))
def test_invitation_representations_exclude_the_private_clipboard_payload(kind):
    issued = _remote_issue() if kind == "native" else None
    link = issued.private_link.reveal_for_clipboard() if issued else _lan_link()
    message = build_invite_message(join_link=link, creator_profile_key="art")
    parsed = parse_invitation_at_ingress(message.text, source=InvitationSource.PASTE)
    representations = repr(message) + repr(parsed)
    if issued:
        representations += repr(issued.private_link) + str(issued.private_link)
    private_exposed = link in representations or message.text in representations
    if isinstance(parsed, BandInvite):
        private_exposed = private_exposed or parsed.invite_token in representations
    else:
        private_exposed = private_exposed or repr(parsed.capability_for_enrollment()) in representations
    assert not private_exposed
    assert "text=" not in repr(message)
    _copy_without_link(message, link)


def test_art_rejects_invalid_optional_meeting_without_changing_invitation():
    link = _lan_link()
    message = build_invite_message(
        join_link=link, creator_profile_key="art", same_network_required=True,
        meeting_url="https://user:password@band.webex.com/meet/artist",
    )
    copy = _copy_without_link(message, link)
    assert not message.includes_meeting
    assert "password" not in copy
    assert "without a meeting" in copy
    assert PASTE_INSTRUCTION in copy


def test_explicit_network_fact_preserves_existing_music_copy():
    link = _lan_link()
    message = build_invite_message(join_link=link, same_network_required=True)
    copy = _copy_without_link(message, link)
    assert "Open the link in WebJam to join as a musician." in copy
    assert PASTE_INSTRUCTION not in copy
    _assert_no_scope_claim(copy)
