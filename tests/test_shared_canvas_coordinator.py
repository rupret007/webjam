"""The shared canvas coordinator, exercised headlessly.

The coordinator is the only thing that knows which role this computer is
playing, so these tests are mostly about what it refuses: a guest driving the
room's canvas, a canvas published into a room with no peer plane, and an
optional add-on taking the session down with it.
"""

from __future__ import annotations

import pytest

from core.drawpile import (
    INSTALL_DRAWPILE_MESSAGE,
    CanvasInvite,
    DrawpileUnavailableError,
)
from core.session_transfer import SharedCanvasSessionSnapshot
from core.shared_canvas import (
    SharedCanvasError, SharedCanvasFollowState, SharedCanvasPendingAction,
)
from webjam_qt.controllers.shared_canvas_coordinator import (
    LAUNCHER_UNAVAILABLE_MESSAGE,
    NOT_FOLLOWING_MESSAGE,
    NOT_HOSTING_MESSAGE,
    SharedCanvasCoordinator,
    follow_state_is_blocked,
)

WEB_INVITE = "https://drawpile.net/invites/pub.drawpile.net/kitchen-table?v1#hunter2"
NORMALIZED = "drawpile://pub.drawpile.net/kitchen-table?v1&p=hunter2"


class FakeLauncher:
    def __init__(self, *, installed: bool = True) -> None:
        self.installed = installed
        self.host_pages = 0
        self.joined: list[str] = []

    def available(self) -> bool:
        return self.installed

    def open_host_page(self) -> None:
        self._require_install()
        self.host_pages += 1

    def open_canvas(self, invite: CanvasInvite) -> None:
        self._require_install()
        self.joined.append(invite.join_url)

    def _require_install(self) -> None:
        # Mirrors the real launcher: only it knows why it cannot run.
        if not self.installed:
            raise DrawpileUnavailableError(INSTALL_DRAWPILE_MESSAGE)


class FakeHostPeer:
    """Stands in for the authenticated peer plane."""

    def __init__(self, *, active: bool = True, explode: bool = False) -> None:
        self.active = active
        self.explode = explode
        self.published: list[dict] = []

    def publish_shared_canvas_state(self, **kwargs):
        if self.explode:
            raise RuntimeError("peer plane is unhappy")
        self.published.append(kwargs)
        return SharedCanvasSessionSnapshot(**kwargs)


def _coordinator(*, launcher=None, peer=None, follow=None):
    launcher = launcher if launcher is not None else FakeLauncher()
    return SharedCanvasCoordinator(
        launcher_factory=lambda: launcher,
        host_peer_provider=lambda: peer,
        on_follow_snapshot=follow,
    )


class Projection:
    def __init__(self, *, shared=False, join_url="", server_label="", session_label=""):
        self.shared = shared
        self.join_url = join_url
        self.server_label = server_label
        self.session_label = session_label


class PeerState:
    def __init__(self, canvas):
        self.shared_canvas = canvas


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


def test_an_unbound_coordinator_owns_nothing():
    coordinator = _coordinator()

    assert coordinator.role == ""
    assert coordinator.hosting is False
    assert coordinator.following is False
    assert coordinator.host_snapshot.shared is False
    assert coordinator.follow_snapshot.state is SharedCanvasFollowState.NO_CANVAS


@pytest.mark.parametrize(
    "action",
    [
        lambda c: c.share(WEB_INVITE),
        lambda c: c.withdraw(),
        lambda c: c.open_drawpile_to_host(),
        lambda c: c.open_canvas_as_host(),
    ],
)
def test_host_intents_are_refused_before_hosting(action):
    coordinator = _coordinator()

    with pytest.raises(SharedCanvasError) as failure:
        action(coordinator)

    assert str(failure.value) == NOT_HOSTING_MESSAGE


def test_a_host_is_not_a_follower():
    coordinator = _coordinator()
    coordinator.begin_host()

    with pytest.raises(SharedCanvasError) as failure:
        coordinator.open_canvas()

    assert str(failure.value) == NOT_FOLLOWING_MESSAGE


def test_a_guest_cannot_share_the_rooms_canvas():
    coordinator = _coordinator()
    coordinator.begin_guest()

    with pytest.raises(SharedCanvasError, match=NOT_HOSTING_MESSAGE):
        coordinator.share(WEB_INVITE)


def test_rebinding_releases_the_previous_role():
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer)
    coordinator.begin_host()
    coordinator.share(WEB_INVITE)

    coordinator.begin_guest()

    assert coordinator.hosting is False
    assert coordinator.following is True
    assert peer.published[-1] == {"shared": False}


# ---------------------------------------------------------------------------
# Host to the peer plane
# ---------------------------------------------------------------------------


def test_sharing_reaches_the_peer_plane_normalized():
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer)
    coordinator.begin_host()

    coordinator.share(WEB_INVITE)

    assert peer.published == [
        {
            "shared": True,
            "join_url": NORMALIZED,
            "server_label": "pub.drawpile.net",
            "session_label": "kitchen-table",
        }
    ]


def test_a_published_projection_is_accepted_by_the_real_wire_schema():
    """The coordinator and the schema must not disagree about what is legal."""

    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer)
    coordinator.begin_host()
    coordinator.share(WEB_INVITE)

    snapshot = SharedCanvasSessionSnapshot(**peer.published[-1])

    assert snapshot.shared is True
    assert snapshot.join_url == NORMALIZED


def test_withdrawing_publishes_nothing_shared_and_no_address():
    peer = FakeHostPeer()
    coordinator = _coordinator(peer=peer)
    coordinator.begin_host()
    coordinator.share(WEB_INVITE)

    coordinator.withdraw()

    assert peer.published[-1] == {
        "shared": False,
        "join_url": "",
        "server_label": "",
        "session_label": "",
    }


def test_opening_drawpile_publishes_nothing():
    """Opening a program is a local act, not a claim about the room."""

    peer = FakeHostPeer()
    launcher = FakeLauncher()
    coordinator = _coordinator(launcher=launcher, peer=peer)
    coordinator.begin_host()

    coordinator.open_drawpile_to_host()

    assert launcher.host_pages == 1
    assert peer.published == []


def test_publication_is_skipped_while_the_peer_plane_is_inactive():
    peer = FakeHostPeer(active=False)
    coordinator = _coordinator(peer=peer)
    coordinator.begin_host()

    coordinator.share(WEB_INVITE)

    assert peer.published == []
    assert coordinator.host_snapshot.shared is False
    assert coordinator.host_snapshot.pending_action is SharedCanvasPendingAction.SHARE
    assert coordinator.host_snapshot.can_retry_publication


def test_a_peer_failure_never_breaks_the_hosts_canvas():
    peer = FakeHostPeer(explode=True)
    coordinator = _coordinator(peer=peer)
    coordinator.begin_host()

    snapshot = coordinator.share(WEB_INVITE)

    assert snapshot.shared is False
    assert snapshot.pending_action is SharedCanvasPendingAction.SHARE
    assert snapshot.can_retry_publication


# ---------------------------------------------------------------------------
# The guest side
# ---------------------------------------------------------------------------


def test_a_guest_receives_the_hosts_canvas_from_the_peer_plane():
    seen = []
    launcher = FakeLauncher()
    coordinator = _coordinator(launcher=launcher, follow=seen.append)
    coordinator.begin_guest()

    coordinator.observe_host_state(
        PeerState(
            Projection(
                shared=True,
                join_url=NORMALIZED,
                server_label="pub.drawpile.net",
                session_label="kitchen-table",
            )
        )
    )

    assert coordinator.follow_snapshot.state is SharedCanvasFollowState.READY
    assert seen and seen[-1].can_open is True

    coordinator.open_canvas()
    assert launcher.joined == [NORMALIZED]


def test_a_guest_without_drawpile_says_so_and_stays_in_the_room():
    coordinator = _coordinator(launcher=FakeLauncher(installed=False))
    coordinator.begin_guest()

    coordinator.observe_host_state(
        PeerState(Projection(shared=True, join_url=NORMALIZED, session_label="room"))
    )

    snapshot = coordinator.follow_snapshot
    assert snapshot.state is SharedCanvasFollowState.NEEDS_DRAWPILE
    assert follow_state_is_blocked(snapshot.state.value) is True


def test_a_session_state_with_no_canvas_member_is_treated_as_no_canvas():
    coordinator = _coordinator()
    coordinator.begin_guest()

    coordinator.observe_host_state(object())

    assert coordinator.follow_snapshot.state is SharedCanvasFollowState.NO_CANVAS


def test_observing_before_binding_is_a_no_op():
    coordinator = _coordinator()

    coordinator.observe_host_state(
        PeerState(Projection(shared=True, join_url=NORMALIZED))
    )

    assert coordinator.follow_snapshot.state is SharedCanvasFollowState.NO_CANVAS


# ---------------------------------------------------------------------------
# A computer that cannot start Drawpile at all
# ---------------------------------------------------------------------------


def test_a_launcher_that_cannot_be_built_becomes_no_canvas_not_an_exception():
    """Binding runs on the path that renders the whole room.

    An optional add-on that raises here would take the session view down with
    it, so a computer with no launcher reports "no canvas" instead.
    """

    def exploding_factory():
        raise RuntimeError("no Drawpile support on this build")

    coordinator = SharedCanvasCoordinator(launcher_factory=exploding_factory)
    coordinator.begin_guest()

    coordinator.observe_host_state(
        PeerState(Projection(shared=True, join_url=NORMALIZED, session_label="room"))
    )

    assert coordinator.follow_snapshot.state is SharedCanvasFollowState.NEEDS_DRAWPILE
    assert coordinator.launcher_available is False


def test_a_computer_without_a_launcher_still_refuses_to_open_anything():
    def exploding_factory():
        raise RuntimeError("no Drawpile support on this build")

    coordinator = SharedCanvasCoordinator(launcher_factory=exploding_factory)
    coordinator.begin_host()

    with pytest.raises(SharedCanvasError) as failure:
        coordinator.open_drawpile_to_host()

    assert str(failure.value) == LAUNCHER_UNAVAILABLE_MESSAGE


def test_ending_a_room_releases_the_pointer_without_closing_drawpile():
    launcher = FakeLauncher()
    peer = FakeHostPeer()
    coordinator = _coordinator(launcher=launcher, peer=peer)
    coordinator.begin_host()
    coordinator.share(WEB_INVITE)
    coordinator.open_canvas_as_host()
    opened = list(launcher.joined)

    coordinator.end()

    assert coordinator.role == ""
    assert peer.published[-1] == {"shared": False}
    # Drawpile is the artist's own program; nothing closed it.
    assert launcher.joined == opened


def test_an_unknown_follow_state_is_not_reported_as_blocked():
    assert follow_state_is_blocked("not-a-state") is False
    assert follow_state_is_blocked(None) is False
    assert follow_state_is_blocked("ready") is False
