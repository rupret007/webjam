"""Share and email delivery for an invite that is already prepared."""

from urllib.parse import parse_qs, urlsplit

import pytest

from core.network_invite import (
    InviteLinkError,
    create_invite_link,
    invite_mailto_url,
    invite_message_title,
    preferred_invite_delivery,
)


def test_email_leads_when_share_is_missing_and_a_fixture_gains_no_deliver():
    assert preferred_invite_delivery(share_available=True) == "share"
    assert preferred_invite_delivery(share_available=False) == "email"
    mailto = invite_mailto_url(
        "https://example.com/join/#invite",
        title=invite_message_title(),
    )
    parsed = urlsplit(mailto)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "mailto"
    assert query["subject"] == ["Join me on WebJam"]
    body = query["body"][0]
    assert body == "https://example.com/join/#invite"
    assert "deliver" not in body


def test_music_invite_mailto_does_not_add_deliver():
    mailto = invite_mailto_url(
        create_invite_link("192.0.2.10", session_name="Practice"),
        title=invite_message_title("Practice"),
    )
    assert mailto.startswith("mailto:?subject=Join+me+for+Practice&body=webjam")
    assert "deliver" not in mailto


def test_share_refuses_a_link_that_is_not_an_invite():
    with pytest.raises(InviteLinkError):
        invite_mailto_url("javascript:alert(1)", title="Join me on WebJam")
    with pytest.raises(InviteLinkError):
        invite_mailto_url("https://example.com/join/#invite extra", title="Join me on WebJam")
