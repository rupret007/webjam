"""Canvas publication receipts and retries through the real memory-only peer model."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.drawpile import MAX_CANVAS_LABEL_CHARS, parse_canvas_invite
from core.session_transfer import SessionControlState, SharedCanvasSessionSnapshot
from core.shared_canvas import SharedCanvasError, SharedCanvasPendingAction as Pending
from webjam_qt.controllers.shared_canvas_coordinator import SharedCanvasCoordinator

A = "drawpile://canvas.example.org/lesson-one?p=PRIVATE_A"
B = "drawpile://canvas.example.org/lesson-two?p=PRIVATE_B"
C = "drawpile://canvas.example.org/lesson-three?p=PRIVATE_C"


class Launcher:
    def __init__(self):
        self.opened = []
        self.host_pages = 0
        self.fail = False

    def available(self):
        return True

    def open_host_page(self):
        self.host_pages += 1

    def open_canvas(self, invite):
        if self.fail:
            raise RuntimeError("PRIVATE launcher arguments and password")
        self.opened.append(invite.join_url)


class Peer:
    def __init__(self, root):
        self.control = SessionControlState(
            root, "b42f95a9-d72f-49e4-803e-4098557f20f6", creator_profile_key="art",
        )
        self.active = True
        self.mode = "accept"
        self.calls = []
        self.on_publish = None

    def publish_shared_canvas_state(self, **values):
        self.calls.append(values)
        mode = self.mode
        if mode == "raise":
            raise RuntimeError("PRIVATE canvas URL and transport failure")
        if mode == "none":
            return None
        if mode == "false":
            return False
        if mode == "true":
            return True
        if mode == "object":
            return object()
        if mode == "wrong_shared":
            return SharedCanvasSessionSnapshot()
        if mode == "wrong_url":
            invite = parse_canvas_invite(C)
            return SharedCanvasSessionSnapshot(
                shared=True, join_url=invite.join_url,
                server_label=invite.server_label, session_label=invite.session_label,
            )
        if mode == "wrong_label":
            return replace(SharedCanvasSessionSnapshot(**values), session_label="other")
        receipt = self.control.publish_shared_canvas(**values)
        callback, self.on_publish = self.on_publish, None
        if callback is not None:
            callback()
        return None if mode == "accept_then_none" else receipt


def room(tmp_path):
    launcher = Launcher()
    peer = Peer(tmp_path / "peer")
    current = [peer]
    snapshots = []
    coordinator = SharedCanvasCoordinator(
        launcher_factory=lambda: launcher, host_peer_provider=lambda: current[0],
        on_host_snapshot=snapshots.append,
    )
    coordinator.begin_host()
    return coordinator, peer, current, launcher, snapshots


def assert_offer(snapshot, url):
    assert snapshot.shared is (url is not None)
    assert snapshot.session_label == (parse_canvas_invite(url).session_label if url else "")
    assert snapshot.server_label == (parse_canvas_invite(url).server_label if url else "")


def test_valid_long_hostname_reaches_guests_with_full_joining_url(tmp_path):
    host, peer, _, launcher, _ = room(tmp_path)
    long_host = "artists-" + "a" * 20 + ".studios-" + "b" * 20 + ".department-" + "c" * 20 + ".example.org"
    url = f"drawpile://{long_host}/lesson-two?p=PRIVATE_LONG"
    host.share(A)
    result = host.share(url)
    assert result.shared and result.pending_action is Pending.NONE
    assert len(result.server_label) == MAX_CANVAS_LABEL_CHARS
    assert "…" in result.server_label
    assert result.server_label.endswith(".example.org")
    assert peer.control.snapshot().shared_canvas.join_url == url
    assert "PRIVATE_LONG" not in repr(result)
    assert launcher.opened == [] and launcher.host_pages == 0

    guest_launcher = Launcher()
    guest = SharedCanvasCoordinator(launcher_factory=lambda: guest_launcher)
    guest.begin_guest()
    guest.observe_host_state(peer.control.snapshot())
    assert guest.follow_snapshot.session_label == "lesson-two"
    assert guest_launcher.opened == []
    guest.open_canvas()
    assert guest_launcher.opened == [url]


@pytest.mark.parametrize("previous", [None, A])
@pytest.mark.parametrize("rejection", ["none", "false", "true", "object", "raise", "wrong_shared", "wrong_url", "wrong_label"])
def test_share_requires_matching_typed_receipt_and_retry_retains_candidate(
    tmp_path, caplog, previous, rejection,
):
    host, peer, _, launcher, snapshots = room(tmp_path)
    if previous:
        host.share(previous)
    peer.mode = rejection
    result = host.share(B)
    assert_offer(result, previous)
    assert result.pending_action is Pending.SHARE
    assert result.can_retry_publication and result.needs_attention
    assert result is snapshots[-1]
    assert "PRIVATE" not in repr(result)
    assert "PRIVATE" not in caplog.text
    assert peer.control.snapshot().shared_canvas.shared is bool(previous)
    peer.mode = "accept"
    result = host.retry_publication()
    assert_offer(result, B)
    assert result.pending_action is Pending.NONE
    assert not result.can_retry_publication and not result.error
    assert peer.control.snapshot().shared_canvas.join_url == B
    assert launcher.opened == [] and launcher.host_pages == 0
    count = len(peer.calls)
    host.retry_publication()
    assert len(peer.calls) == count


@pytest.mark.parametrize("missing", ["inactive", "none", "no_method", "provider_raises"])
def test_unavailable_publisher_leaves_share_pending_until_explicit_retry(tmp_path, missing):
    host, peer, current, _, _ = room(tmp_path)
    if missing == "inactive":
        peer.active = False
    elif missing == "none":
        current[0] = None
    elif missing == "no_method":
        current[0] = SimpleNamespace(active=True)
    else:
        def unavailable():
            raise RuntimeError("PRIVATE provider state")
        host._host_peer_provider = unavailable
    assert host.share(B).pending_action is Pending.SHARE
    assert not host.host_snapshot.shared
    assert peer.calls == []
    peer.active = True
    current[0] = peer
    host._host_peer_provider = lambda: current[0]
    # Reading/rendering the model never retries in the background.
    for _ in range(3):
        assert host.host_snapshot.pending_action is Pending.SHARE
    assert peer.calls == []
    assert_offer(host.retry_publication(), B)


@pytest.mark.parametrize("rejection", ["none", "false", "raise", "wrong_url"])
def test_withdrawal_preserves_last_accepted_offer_until_matching_receipt(tmp_path, rejection):
    host, peer, _, launcher, _ = room(tmp_path)
    host.share(A)
    peer.mode = rejection
    result = host.withdraw()
    assert_offer(result, A)
    assert result.pending_action is Pending.WITHDRAW
    assert result.can_retry_publication
    assert peer.control.snapshot().shared_canvas.join_url == A
    host.open_canvas_as_host()
    assert launcher.opened == [A]
    peer.mode = "accept"
    result = host.retry_publication()
    assert_offer(result, None)
    assert result.pending_action is Pending.NONE
    assert not peer.control.snapshot().shared_canvas.shared
    assert launcher.opened == [A]


def test_stop_after_ambiguous_initial_share_confirms_removal_instead_of_forgetting_it(tmp_path):
    host, peer, _, _, _ = room(tmp_path)
    peer.mode = "accept_then_none"
    assert not host.share(B).shared
    assert peer.control.snapshot().shared_canvas.join_url == B
    peer.mode = "accept"
    result = host.withdraw()
    assert result.pending_action is Pending.NONE
    assert not result.shared
    assert not peer.control.snapshot().shared_canvas.shared
    host.retry_publication()
    assert len(peer.calls) == 2


def test_duplicate_retry_cannot_publish_while_same_intent_is_in_flight(tmp_path):
    host, peer, _, _, _ = room(tmp_path)
    peer.mode = "none"
    host.share(B)
    peer.mode = "accept"
    nested = []

    def duplicate():
        count = len(peer.calls)
        nested.append(host.retry_publication())
        assert len(peer.calls) == count
        assert not nested[-1].can_retry_publication

    peer.on_publish = duplicate
    assert_offer(host.retry_publication(), B)
    assert len(peer.calls) == 2


@pytest.mark.parametrize("retire", ["end", "guest", "new_host"])
def test_end_or_rebind_during_publication_cannot_commit_or_notify_old_offer(tmp_path, retire):
    host, peer, current, launcher, snapshots = room(tmp_path)
    host.share(A)
    other = Peer(tmp_path / "other")
    after_retirement = []

    def retire_room():
        if retire == "end":
            host.end()
        elif retire == "guest":
            host.begin_guest()
        else:
            current[0] = other
            host.begin_host()
        after_retirement.append(len(snapshots))

    peer.on_publish = retire_room
    host.share(B)
    assert len(snapshots) == after_retirement[0]
    assert not host.host_snapshot.shared
    assert host.host_snapshot.pending_action is Pending.NONE
    assert other.calls == []
    if retire == "new_host":
        assert_offer(host.share(C), C)
        assert other.control.snapshot().shared_canvas.join_url == C
    else:
        with pytest.raises(SharedCanvasError):
            host.retry_publication()
    assert launcher.opened == []


def test_retirement_inside_provider_does_not_send_to_the_new_room(tmp_path):
    host, peer, _, _, snapshots = room(tmp_path)

    def provider():
        host._host_peer_provider = lambda: peer
        host.begin_guest()
        return peer

    host._host_peer_provider = provider
    host.share(B)
    assert peer.calls == []
    assert len(snapshots) == 1
    assert snapshots[0].pending_action is Pending.SHARE
    assert not snapshots[0].can_retry_publication
    assert host.following


def test_newer_intent_wins_when_old_publication_returns_later(tmp_path):
    host, peer, _, _, snapshots = room(tmp_path)
    host.share(A)
    count = len(snapshots)
    peer.on_publish = lambda: host.share(C)
    assert_offer(host.share(B), C)
    completed = [snapshot for snapshot in snapshots[count:]
                 if snapshot.pending_action is Pending.NONE]
    assert len(completed) == 1
    assert_offer(completed[0], C)
    assert_offer(snapshots[-1], C)
    assert peer.control.snapshot().shared_canvas.join_url == C


def test_changed_publisher_identity_cannot_confirm_old_receipt(tmp_path):
    host, peer, current, _, _ = room(tmp_path)
    host.share(A)
    other = Peer(tmp_path / "other")
    peer.on_publish = lambda: current.__setitem__(0, other)
    result = host.share(B)
    assert_offer(result, A)
    assert result.pending_action is Pending.SHARE
    assert_offer(host.retry_publication(), B)
    assert other.control.snapshot().shared_canvas.join_url == B


def test_local_launcher_failure_cannot_remove_the_room_offer(tmp_path):
    host, peer, _, launcher, _ = room(tmp_path)
    host.share(A)
    launcher.fail = True
    with pytest.raises(SharedCanvasError, match="couldn't open Drawpile"):
        host.open_canvas_as_host()
    assert_offer(host.host_snapshot, A)
    assert peer.control.snapshot().shared_canvas.join_url == A
    assert host.host_snapshot.pending_action is Pending.NONE
    launcher.fail = False
    host.open_canvas_as_host()
    assert launcher.opened == [A]
    host.withdraw()
    assert not peer.control.snapshot().shared_canvas.shared


def test_invalid_replacement_keeps_the_accepted_offer_and_previous_pending_intent(tmp_path):
    host, peer, _, _, _ = room(tmp_path)
    host.share(A)
    peer.mode = "none"
    host.share(B)
    calls = len(peer.calls)
    with pytest.raises(SharedCanvasError):
        host.share("PRIVATE malformed clipboard value")
    assert len(peer.calls) == calls
    assert_offer(host.host_snapshot, A)
    assert host.host_snapshot.pending_action is Pending.SHARE
    peer.mode = "accept"
    assert_offer(host.retry_publication(), B)


def test_retry_is_rendered_disabled_before_provider_or_publisher_can_pump_events(tmp_path):
    host, peer, _, _, snapshots = room(tmp_path)
    peer.mode = "none"
    host.share(B)
    assert snapshots[-1].can_retry_publication
    peer.mode = "accept"
    snapshots.clear()
    observations = []

    def check_rendered():
        assert snapshots[-1].pending_action is Pending.SHARE
        assert not snapshots[-1].can_retry_publication
        observations.append("disabled")

    def provider():
        check_rendered()
        return peer

    host._host_peer_provider = provider
    peer.on_publish = check_rendered
    assert_offer(host.retry_publication(), B)
    assert len(observations) >= 2
    assert snapshots[-1].pending_action is Pending.NONE


@pytest.mark.parametrize("callback_action", ["end", "new_intent"])
def test_inflight_notification_can_retire_or_replace_intent_before_old_send(
    tmp_path, callback_action,
):
    host, peer, _, _, snapshots = room(tmp_path)
    host.share(A)
    previous_calls = len(peer.calls)

    def on_snapshot(snapshot):
        snapshots.append(snapshot)
        assert snapshot.pending_action is Pending.SHARE
        assert not snapshot.can_retry_publication
        # Only the first in-flight announcement acts; newer callbacks render.
        host._on_host_snapshot = snapshots.append
        if callback_action == "end":
            host.end()
        else:
            host.share(C)

    host._on_host_snapshot = on_snapshot
    result = host.share(B)
    assert all(call.get("join_url") != B for call in peer.calls[previous_calls:])
    if callback_action == "end":
        assert not result.shared and not host.hosting
        assert not peer.control.snapshot().shared_canvas.shared
    else:
        assert_offer(result, C)
        assert peer.control.snapshot().shared_canvas.join_url == C
        assert snapshots[-1].pending_action is Pending.NONE
