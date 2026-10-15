from __future__ import annotations

import os

import pytest

from core.direct_stun_invite import (
    accept_guest_reply,
    encode_guest_reply_line,
    publish_guest_reply,
)
from core.remote_invitation import issue_remote_invitation
from core.signaling_mailbox import (
    MailboxUnavailable,
    ack_answer,
    bind_transport,
    mailbox_id,
    open_slot,
    poll_answer,
    post_answer,
    seal_slot,
    slot_aad,
)


SIGNAL = b"WJSE" + bytes(range(12))


class MemoryMailbox:
    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, object]] = {}
        self.hits: list[str] = []
        self.ip = "203.0.113.10"
        self.rate = 0
        self.rate_limit = 10
        self.daily_limit = 20_000
        self.daily = 0
        self.ttl_expired = False
        self.fail_closed = False

    def request(self, method: str, path: str, body=None):
        self.hits.append(f"{method} {path}")
        if self.fail_closed:
            return 503, {"error": "unreadable"}
        self.daily += 1
        self.rate += 1
        if self.daily > self.daily_limit:
            return 429, {"error": "cap"}
        if self.rate > self.rate_limit:
            return 429, {"error": "rate"}
        parts = [part for part in path.split("/") if part]
        mailbox = parts[1] if len(parts) > 1 else ""
        slot = parts[2] if len(parts) > 2 else ""
        room = self.rooms.get(mailbox)
        if self.ttl_expired or (room and room.get("expired")):
            self.rooms.pop(mailbox, None)
            return 410, {"error": "expired"}
        if method == "DELETE":
            self.rooms.pop(mailbox, None)
            return 204, {}
        if method == "POST":
            payload = str((body or {}).get("payload") or "")
            room = self.rooms.setdefault(mailbox, {"offer": "", "answer": ""})
            if room.get(slot):
                return 409, {"error": "duplicate"}
            room[slot] = payload
            return 201, {}
        if room is None or not room.get(slot):
            return 204, {}
        return 200, {"payload": room[slot]}


@pytest.fixture
def issued():
    return issue_remote_invitation(
        "direct-stun",
        allowed_profiles={"direct-stun"},
        host_spki_sha256=os.urandom(32),
    )


@pytest.fixture
def mailbox():
    store = MemoryMailbox()
    bind_transport(store)
    yield store
    bind_transport(None)


def test_mailbox_id_is_not_the_capability(issued) -> None:
    capability = issued.invitation.capability_for_enrollment()
    locator = mailbox_id(capability)
    assert len(locator) == 64
    assert locator != capability.hex()
    assert locator == mailbox_id(capability)


def test_forged_answer_is_rejected_by_aead_and_binding(issued) -> None:
    invitation = issued.invitation
    key = os.urandom(32)
    payload = seal_slot(
        key,
        SIGNAL,
        kind="answer",
        session_reference=invitation.session_reference,
        host_spki_sha256=invitation.host_spki_sha256,
    )
    with pytest.raises(ValueError):
        open_slot(
            os.urandom(32),
            payload,
            kind="answer",
            session_reference=invitation.session_reference,
            host_spki_sha256=invitation.host_spki_sha256,
        )
    other = issue_remote_invitation(
        "direct-stun",
        allowed_profiles={"direct-stun"},
        host_spki_sha256=os.urandom(32),
    ).invitation
    with pytest.raises(ValueError):
        open_slot(
            key,
            payload,
            kind="answer",
            session_reference=other.session_reference,
            host_spki_sha256=other.host_spki_sha256,
        )
    aad = slot_aad("answer", invitation.session_reference, invitation.host_spki_sha256)
    assert invitation.session_reference in aad
    assert invitation.host_spki_sha256 in aad


def test_duplicate_answer_is_rejected(issued, mailbox) -> None:
    post_answer(issued.invitation, SIGNAL)
    with pytest.raises(MailboxUnavailable):
        post_answer(issued.invitation, SIGNAL)


def test_expired_or_lost_mailbox(issued, mailbox) -> None:
    mailbox.ttl_expired = True
    with pytest.raises(MailboxUnavailable):
        poll_answer(issued.invitation, timeout_seconds=0.1, sleeper=lambda _s: None)
    mailbox.ttl_expired = False
    with pytest.raises(MailboxUnavailable):
        poll_answer(issued.invitation, timeout_seconds=0.1, sleeper=lambda _s: None)


def test_fails_closed_at_the_daily_cap(issued, mailbox) -> None:
    mailbox.daily_limit = 1
    mailbox.rate_limit = 100
    post_answer(issued.invitation, SIGNAL)
    other = issue_remote_invitation(
        "direct-stun",
        allowed_profiles={"direct-stun"},
        host_spki_sha256=os.urandom(32),
    )
    with pytest.raises(MailboxUnavailable):
        post_answer(other.invitation, SIGNAL)


def test_rate_limit(issued, mailbox) -> None:
    mailbox.rate_limit = 1
    post_answer(issued.invitation, SIGNAL)
    with pytest.raises(MailboxUnavailable):
        poll_answer(issued.invitation, timeout_seconds=0.1, sleeper=lambda _s: None)


def test_publish_posts_and_accept_still_has_paste_fallback(issued, mailbox) -> None:
    line = publish_guest_reply(SIGNAL, issued.invitation)
    assert line == encode_guest_reply_line(SIGNAL)
    assert poll_answer(issued.invitation, timeout_seconds=0.1, sleeper=lambda _s: None) == SIGNAL
    ack_answer(issued.invitation)
    assert accept_guest_reply(f"> {line}\n") == SIGNAL
