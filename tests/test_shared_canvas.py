"""Art's shared canvas: finding Drawpile, reading its invitations, failing closed.

WebJam paints nothing here. Everything below is about being honest concerning
a program WebJam does not own: whether it is installed, what its invitation
means, and what to say when the answer is "no canvas on this computer".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.drawpile import (
    DEFAULT_DRAWPILE_CANDIDATES,
    INSTALL_DRAWPILE_MESSAGE,
    NOT_A_CANVAS_INVITE_MESSAGE,
    CanvasInvite,
    DrawpileError,
    DrawpileUnavailableError,
    drawpile_host_arguments,
    drawpile_join_arguments,
    find_drawpile,
    parse_canvas_invite,
)
from core.shared_canvas import (
    CANVAS_UNREADABLE_MESSAGE,
    HOST_ONLY_CANVAS_MESSAGE,
    CanvasLauncher,
    SharedCanvasError,
    SharedCanvasFollower,
    SharedCanvasFollowState,
    SharedCanvasHostController,
    SharedCanvasState,
)

WEB_INVITE = "https://drawpile.net/invites/pub.drawpile.net/kitchen-table?v1"
PASSWORDED_INVITE = f"{WEB_INVITE}#hunter2"
DIRECT_INVITE = "drawpile://pub.drawpile.net/kitchen-table"


class FakeLauncher:
    """A Drawpile that records what it was asked to do."""

    def __init__(self, *, installed: bool = True) -> None:
        self.installed = installed
        self.host_pages = 0
        self.joined: list[str] = []
        self.fail_with: Exception | None = None

    def available(self) -> bool:
        return self.installed

    def open_host_page(self) -> None:
        self._require_install()
        if self.fail_with is not None:
            raise self.fail_with
        self.host_pages += 1

    def open_canvas(self, invite: CanvasInvite) -> None:
        self._require_install()
        if self.fail_with is not None:
            raise self.fail_with
        self.joined.append(invite.join_url)

    def _require_install(self) -> None:
        # Mirrors the real launcher: only it knows why it cannot run.
        if not self.installed:
            raise DrawpileUnavailableError(INSTALL_DRAWPILE_MESSAGE)


def _host(launcher: FakeLauncher, *, is_host: bool = True):
    return SharedCanvasHostController(launcher, is_host=lambda: is_host)


# ---------------------------------------------------------------------------
# Reading Drawpile's invitations
# ---------------------------------------------------------------------------


def test_a_direct_session_url_is_accepted_unchanged():
    invite = parse_canvas_invite(DIRECT_INVITE)

    assert invite.join_url == DIRECT_INVITE
    assert invite.server_label == "pub.drawpile.net"
    assert invite.session_label == "kitchen-table"
    assert invite.carries_password is False


@pytest.mark.parametrize("scheme", ["ws", "wss"])
def test_the_websocket_session_urls_drawpile_accepts_are_accepted(scheme: str):
    invite = parse_canvas_invite(f"{scheme}://pub.drawpile.net/studio")

    assert invite.join_url == f"{scheme}://pub.drawpile.net/studio"


def test_a_web_invite_link_is_normalized_the_way_drawpile_normalizes_it():
    """The link a person actually copies must be the link WebJam accepts.

    Drawpile's Invite dialog produces ``https://drawpile.net/invites/...``,
    and its Join page silently rewrites that into a session URL. ``--join``
    does not, so WebJam performs the same documented rewrite instead of
    handing Drawpile an address it cannot act on.
    """

    invite = parse_canvas_invite(WEB_INVITE)

    assert invite.join_url == "drawpile://pub.drawpile.net/kitchen-table?v1"
    assert invite.server_label == "pub.drawpile.net"
    assert invite.session_label == "kitchen-table"


def test_a_password_fragment_becomes_the_query_parameter_drawpile_reads():
    """A Personal session is the recommended shape, so its password must work.

    Without this rewrite a guest handed a passworded invitation would be
    stopped by a prompt WebJam already had the answer to.
    """

    invite = parse_canvas_invite(PASSWORDED_INVITE)

    assert invite.join_url == "drawpile://pub.drawpile.net/kitchen-table?v1&p=hunter2"
    assert invite.carries_password is True


def test_drawpiles_bare_query_flags_survive_verbatim():
    invite = parse_canvas_invite(f"{WEB_INVITE}&w&web&nsfm")

    assert invite.join_url == "drawpile://pub.drawpile.net/kitchen-table?v1&w&web&nsfm"


def test_the_default_port_is_dropped_and_any_other_port_is_kept():
    assert (
        parse_canvas_invite("https://drawpile.net/invites/example.org:27750/abc").join_url
        == "drawpile://example.org/abc"
    )
    assert (
        parse_canvas_invite("https://drawpile.net/invites/example.org:9001/abc").join_url
        == "drawpile://example.org:9001/abc"
    )


def test_an_invite_code_secret_is_never_displayed():
    """``<session>:<secret>`` is a credential in its second half."""

    invite = parse_canvas_invite(
        "https://drawpile.net/invites/pub.drawpile.net/room:s3cr3tc0de?v1"
    )

    assert invite.session_label == "room"
    assert "s3cr3tc0de" not in invite.session_label
    assert "s3cr3tc0de" in invite.join_url


def test_a_parsed_invitation_never_repeats_its_url():
    """A canvas URL can embed a session password, so it stays out of reprs."""

    text = repr(parse_canvas_invite(PASSWORDED_INVITE))

    assert "hunter2" not in text
    assert "kitchen-table" in text
    assert "[redacted]" in text


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not a url",
        "http://evil.example/steal",
        "https://drawpile.net/download/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "drawpile://",
        "drawpile:///no-host",
        "drawpile://user:secret@host/session",
        None,
        42,
        b"drawpile://host/session",
        "drawpile://host/" + "x" * 80,
        "drawpile://host/session?a&b&c&d&e&f&g&h&i",
        "drawpile://host/sess ion",
        "drawpile://host/session\nX",
    ],
)
def test_anything_that_is_not_a_drawpile_invitation_fails_closed(value: object):
    with pytest.raises(DrawpileError, match="not a Drawpile invitation"):
        parse_canvas_invite(value)


def test_the_failure_message_never_echoes_what_was_pasted():
    try:
        parse_canvas_invite("https://attacker.example/steal?token=abc123")
    except DrawpileError as exc:
        assert "abc123" not in str(exc)
        assert "attacker.example" not in str(exc)
        assert str(exc) == NOT_A_CANVAS_INVITE_MESSAGE
    else:  # pragma: no cover - the parse must fail
        pytest.fail("a non-invitation must be refused")


# ---------------------------------------------------------------------------
# Finding a real Drawpile
# ---------------------------------------------------------------------------


def test_no_installed_drawpile_reports_nothing_rather_than_guessing(tmp_path: Path):
    assert find_drawpile([str(tmp_path / "nowhere" / "drawpile")]) is None


def test_a_real_executable_is_found(tmp_path: Path):
    binary = tmp_path / "drawpile"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    assert find_drawpile([str(binary)]) == binary.resolve()


@pytest.mark.skipif(os.name != "posix", reason="POSIX execute bits")
def test_a_file_that_is_not_executable_is_not_drawpile(tmp_path: Path):
    binary = tmp_path / "drawpile"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o644)

    assert find_drawpile([str(binary)]) is None


def test_a_directory_is_never_launched(tmp_path: Path):
    bundle = tmp_path / "Drawpile.app"
    bundle.mkdir()

    assert find_drawpile([str(bundle)]) is None


def test_a_dangling_symlink_is_not_drawpile(tmp_path: Path):
    link = tmp_path / "drawpile"
    link.symlink_to(tmp_path / "gone")

    assert find_drawpile([str(link)]) is None


def test_a_packaged_symlink_resolves_to_the_real_executable(tmp_path: Path):
    """Flatpak, Homebrew, and Snap all publish links, so links must work.

    WebJam makes no publisher claim about a binary it did not ship, so what
    matters is that the resolved object is a real executable file.
    """

    real = tmp_path / "net.drawpile.drawpile.real"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / "net.drawpile.drawpile"
    link.symlink_to(real)

    assert find_drawpile([str(link)]) == real.resolve()


def test_only_explicit_absolute_candidates_are_searched(tmp_path: Path, monkeypatch):
    """No PATH search: a relative name would let anything answer for Drawpile."""

    impostor = tmp_path / "drawpile"
    impostor.write_text("#!/bin/sh\n")
    impostor.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert find_drawpile(["drawpile"]) is None
    assert find_drawpile(["./drawpile"]) is None


def test_the_default_candidates_are_a_fixed_list_with_no_wildcards():
    assert DEFAULT_DRAWPILE_CANDIDATES
    for candidate in DEFAULT_DRAWPILE_CANDIDATES:
        assert not any(character in candidate for character in "*?[")


def test_malformed_candidate_entries_are_skipped_not_raised(tmp_path: Path):
    binary = tmp_path / "drawpile"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    assert find_drawpile([None, 42, "", "relative/path", str(binary)]) == binary.resolve()


# ---------------------------------------------------------------------------
# The commands WebJam actually runs
# ---------------------------------------------------------------------------


def test_hosting_lands_the_artist_on_drawpiles_own_host_page():
    """WebJam cannot answer Drawpile's host dialog, so it does not pretend to."""

    assert drawpile_host_arguments("/bin/drawpile") == [
        "/bin/drawpile",
        "--start-page",
        "host",
    ]


def test_joining_uses_the_explicit_join_option_not_a_bare_argument():
    """``--join`` removes Drawpile's file-or-URL guess from the equation."""

    invite = parse_canvas_invite(PASSWORDED_INVITE)

    assert drawpile_join_arguments("/bin/drawpile", invite) == [
        "/bin/drawpile",
        "--join",
        "drawpile://pub.drawpile.net/kitchen-table?v1&p=hunter2",
    ]


def test_only_a_parsed_invitation_can_become_a_join_command():
    with pytest.raises(DrawpileError):
        drawpile_join_arguments("/bin/drawpile", DIRECT_INVITE)


# ---------------------------------------------------------------------------
# The host's side
# ---------------------------------------------------------------------------


def test_no_canvas_is_a_starting_state_not_a_failure():
    host = _host(FakeLauncher())
    snapshot = host.snapshot

    assert snapshot.state is SharedCanvasState.IDLE
    assert snapshot.shared is False
    assert snapshot.needs_attention is False
    assert snapshot.launcher_available is True
    assert host.invite() is None


def test_hosting_opens_drawpile_once_and_shares_nothing_yet():
    launcher = FakeLauncher()
    host = _host(launcher)

    snapshot = host.open_drawpile_to_host()

    assert launcher.host_pages == 1
    assert snapshot.shared is False


def test_sharing_an_invitation_points_the_room_at_it():
    host = _host(FakeLauncher())

    snapshot = host.share(PASSWORDED_INVITE)

    assert snapshot.state is SharedCanvasState.SHARED
    assert snapshot.shared is True
    assert snapshot.server_label == "pub.drawpile.net"
    assert snapshot.session_label == "kitchen-table"
    assert snapshot.carries_password is True
    assert host.invite().join_url.endswith("p=hunter2")


def test_a_host_snapshot_carries_no_canvas_url():
    """Panels render snapshots and logs repeat them; the URL goes elsewhere."""

    host = _host(FakeLauncher())
    snapshot = host.share(PASSWORDED_INVITE)

    assert "hunter2" not in repr(snapshot)
    assert not hasattr(snapshot, "join_url")


def test_a_bad_paste_is_refused_without_losing_the_shared_canvas():
    host = _host(FakeLauncher())
    host.share(DIRECT_INVITE)

    with pytest.raises(SharedCanvasError, match="not a Drawpile invitation"):
        host.share("https://example.com/not-a-canvas")

    assert host.snapshot.shared is True
    assert host.invite().join_url == DIRECT_INVITE


def test_withdrawing_returns_the_room_to_the_no_canvas_path():
    host = _host(FakeLauncher())
    host.share(DIRECT_INVITE)

    snapshot = host.withdraw()

    assert snapshot.state is SharedCanvasState.IDLE
    assert snapshot.shared is False
    assert host.invite() is None


def test_a_host_without_drawpile_cannot_open_a_canvas_and_says_so():
    host = _host(FakeLauncher(installed=False))

    with pytest.raises(DrawpileUnavailableError) as failure:
        host.open_drawpile_to_host()

    assert str(failure.value) == INSTALL_DRAWPILE_MESSAGE
    assert host.snapshot.launcher_available is False


def test_a_host_without_drawpile_still_cannot_open_a_shared_canvas():
    launcher = FakeLauncher()
    host = _host(launcher)
    host.share(DIRECT_INVITE)
    launcher.installed = False

    with pytest.raises(DrawpileUnavailableError):
        host.open_canvas()

    assert launcher.joined == []


def test_a_drawpile_that_will_not_start_is_reported_not_swallowed():
    launcher = FakeLauncher()
    launcher.fail_with = OSError("no exec")
    host = _host(launcher)

    with pytest.raises(SharedCanvasError, match="couldn't start Drawpile"):
        host.open_drawpile_to_host()


@pytest.mark.parametrize(
    "action",
    [
        lambda host: host.share(DIRECT_INVITE),
        lambda host: host.withdraw(),
        lambda host: host.open_drawpile_to_host(),
    ],
)
def test_a_guest_cannot_choose_the_rooms_canvas(action):
    """A guest that could republish would point the room somewhere else."""

    guest = _host(FakeLauncher(), is_host=False)

    with pytest.raises(SharedCanvasError) as failure:
        action(guest)

    assert str(failure.value) == HOST_ONLY_CANVAS_MESSAGE


def test_transport_after_closing_is_refused():
    host = _host(FakeLauncher())
    host.close()

    with pytest.raises(SharedCanvasError, match="has ended"):
        host.share(DIRECT_INVITE)


def test_a_launcher_that_raises_while_probing_reads_as_no_drawpile():
    class Exploding:
        def available(self) -> bool:
            raise RuntimeError("probe blew up")

        def open_host_page(self) -> None:  # pragma: no cover - never reached
            raise AssertionError

        def open_canvas(self, invite) -> None:  # pragma: no cover
            raise AssertionError

    host = SharedCanvasHostController(Exploding(), is_host=lambda: True)

    assert host.snapshot.launcher_available is False


# ---------------------------------------------------------------------------
# A guest's side
# ---------------------------------------------------------------------------


class Projection:
    """The shape ``core.session_transfer`` publishes."""

    def __init__(self, *, shared=False, join_url="", server_label="", session_label=""):
        self.shared = shared
        self.join_url = join_url
        self.server_label = server_label
        self.session_label = session_label


def _shared_projection(join_url: str = DIRECT_INVITE) -> Projection:
    return Projection(
        shared=True,
        join_url=join_url,
        server_label="pub.drawpile.net",
        session_label="kitchen-table",
    )


def test_a_guest_in_a_room_with_no_canvas_stays_quiet():
    follower = SharedCanvasFollower(launcher=FakeLauncher())

    snapshot = follower.observe(None)

    assert snapshot.state is SharedCanvasFollowState.NO_CANVAS
    assert snapshot.can_open is False
    assert snapshot.blocked is False


def test_a_guest_offered_a_canvas_can_open_it():
    launcher = FakeLauncher()
    follower = SharedCanvasFollower(launcher=launcher)

    ready = follower.observe(_shared_projection())
    assert ready.state is SharedCanvasFollowState.READY
    assert ready.can_open is True
    assert ready.session_label == "kitchen-table"

    opened = follower.open_canvas()
    assert opened.state is SharedCanvasFollowState.OPENED
    assert launcher.joined == [DIRECT_INVITE]


def test_a_guest_without_drawpile_is_told_to_install_it_and_stays_in_the_room():
    follower = SharedCanvasFollower(launcher=FakeLauncher(installed=False))

    snapshot = follower.observe(_shared_projection())

    assert snapshot.state is SharedCanvasFollowState.NEEDS_DRAWPILE
    assert snapshot.can_open is False
    assert snapshot.blocked is True
    assert "Install Drawpile" in snapshot.message

    with pytest.raises(DrawpileUnavailableError):
        follower.open_canvas()


def test_a_projection_this_computer_cannot_read_never_reaches_a_launcher():
    """The projection came from another computer, so it is not trusted."""

    launcher = FakeLauncher()
    follower = SharedCanvasFollower(launcher=launcher)

    snapshot = follower.observe(
        Projection(shared=True, join_url="file:///etc/passwd", session_label="x")
    )

    assert snapshot.state is SharedCanvasFollowState.UNREADABLE
    assert snapshot.can_open is False
    assert snapshot.message == CANVAS_UNREADABLE_MESSAGE

    with pytest.raises(SharedCanvasError, match="could not read"):
        follower.open_canvas()
    assert launcher.joined == []


def test_a_shared_flag_without_an_address_is_no_canvas():
    follower = SharedCanvasFollower(launcher=FakeLauncher())

    assert (
        follower.observe(Projection(shared=True, join_url="")).state
        is SharedCanvasFollowState.NO_CANVAS
    )


def test_a_missing_projection_member_is_treated_as_no_canvas():
    follower = SharedCanvasFollower(launcher=FakeLauncher())

    assert follower.observe(object()).state is SharedCanvasFollowState.NO_CANVAS


def test_the_host_withdrawing_returns_a_guest_to_no_canvas():
    follower = SharedCanvasFollower(launcher=FakeLauncher())
    follower.observe(_shared_projection())
    follower.open_canvas()

    snapshot = follower.observe(Projection(shared=False))

    assert snapshot.state is SharedCanvasFollowState.NO_CANVAS
    assert snapshot.can_open is False


def test_a_new_canvas_is_not_one_this_computer_has_already_opened():
    follower = SharedCanvasFollower(launcher=FakeLauncher())
    follower.observe(_shared_projection())
    assert follower.open_canvas().state is SharedCanvasFollowState.OPENED

    moved = follower.observe(_shared_projection("drawpile://pub.drawpile.net/other"))

    assert moved.state is SharedCanvasFollowState.READY


def test_the_same_canvas_stays_opened_across_repeated_projections():
    follower = SharedCanvasFollower(launcher=FakeLauncher())
    follower.observe(_shared_projection())
    follower.open_canvas()

    assert (
        follower.observe(_shared_projection()).state
        is SharedCanvasFollowState.OPENED
    )


def test_a_guest_can_neither_share_nor_withdraw_a_canvas():
    """Which canvas the room uses is the host's decision, structurally."""

    follower = SharedCanvasFollower(launcher=FakeLauncher())

    for forbidden in ("share", "withdraw", "open_drawpile_to_host"):
        assert not hasattr(follower, forbidden), forbidden


def test_the_follower_satisfies_the_launcher_seam():
    assert isinstance(FakeLauncher(), CanvasLauncher)
