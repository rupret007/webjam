"""Where the application controller meets the reference video.

These tests drive the controller's real seams -- profile gating, coordinator
binding, guest observation, and teardown -- against a partially constructed
controller, which is how the surrounding integration tests exercise this file.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.creative_modes import get_creator_profile_by_key  # noqa: E402
from core.reference_video import (  # noqa: E402
    ReferenceVideoFollowState,
    load_reference_video_source,
    session_identity_signer,
)
from core.session_transfer import (  # noqa: E402
    RecordingSignal,
    ReferenceVideoPlaybackState,
    ReferenceVideoSessionSnapshot,
    SessionStateSnapshot,
)
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


SESSION_ID = str(uuid.uuid4())
INVITE_TOKEN = "invite-token-for-controller-integration"


@pytest.fixture()
def fake_players(monkeypatch):
    """Swap the Qt player for a fake at the module the controller imports."""

    from tests.test_reference_video_coordinator import FakePlayer

    made: list[FakePlayer] = []

    def factory(parent=None):
        player = FakePlayer()
        made.append(player)
        return player

    monkeypatch.setattr(
        "webjam_qt.widgets.reference_video_player.create_qt_reference_video_player",
        factory,
    )
    return made


def _controller(profile_key: str) -> ApplicationController:
    controller = ApplicationController.__new__(ApplicationController)
    controller._active_creator_profile_key = profile_key
    controller._shutdown = False
    controller._shutdown_in_progress = False
    controller._shutdown_cleanup_pending = False
    controller._reference_video = None
    controller._reference_video_dialog = None
    controller._reference_video_binding = ()
    controller.window = SimpleNamespace(
        session_strip=SimpleNamespace(
            set_recording_phase=MagicMock(),
            set_shared_track_snapshot=MagicMock(),
        ),
        recording_studio=SimpleNamespace(set_recording_phase=MagicMock()),
        flash_message=MagicMock(),
    )
    return controller


def _as_guest(controller: ApplicationController) -> None:
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = SimpleNamespace(
        last_state=None,
        invite=SimpleNamespace(
            peer_enabled=True,
            session_id=SESSION_ID,
            invite_token=INVITE_TOKEN,
        ),
    )


def _as_host(controller: ApplicationController) -> None:
    controller.host_peer = SimpleNamespace(
        active=True,
        credentials=SimpleNamespace(
            session_id=SESSION_ID, invite_token=INVITE_TOKEN
        ),
        publish_reference_video_state=MagicMock(),
    )
    controller.guest_peer = None


def _peer_state(reference_video: ReferenceVideoSessionSnapshot):
    return SessionStateSnapshot(
        session_id=SESSION_ID,
        generation=3,
        signal=RecordingSignal.IDLE,
        creator_profile_key="art",
        reference_video=reference_video,
    )


def _shared_video(digest: str, **changes) -> ReferenceVideoSessionSnapshot:
    values = {
        "generation": 2,
        "playback_generation": 1,
        "state": ReferenceVideoPlaybackState.PLAYING,
        "shared": True,
        "source_display_name": "lesson.mp4",
        "identity_digest": digest,
        "position_s": 44.25,
        "duration_s": 1_800.0,
    }
    values.update(changes)
    return ReferenceVideoSessionSnapshot(**values)


def _digest(tmp_path, payload: bytes = b"the shared lesson") -> str:
    source = load_reference_video_source(
        _write(tmp_path / "reference.mp4", payload)
    )
    return session_identity_signer(
        session_id=SESSION_ID, session_key=INVITE_TOKEN
    )(source.content_sha256)


def _write(path, payload: bytes = b"the shared lesson"):
    path.write_bytes(payload)
    return path


# ---------------------------------------------------------------------------
# Profile gating
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile_key", ["music", "podcast_voice", "review_rehearsal"])
def test_a_profile_without_the_capability_owns_no_reference_video(profile_key):
    controller = _controller(profile_key)
    _as_host(controller)

    assert controller._reference_video_supported() is False
    assert controller._reference_video_coordinator() is None
    assert controller._reference_video is None


def test_a_profile_without_the_capability_refuses_to_open_the_panel():
    controller = _controller("music")
    _as_host(controller)

    controller._open_reference_video()

    message = controller.window.flash_message.call_args.args[0]
    assert "Art" in message
    assert controller._reference_video_dialog is None


def test_art_without_a_started_room_owns_nothing():
    controller = _controller("art")
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = None

    assert controller._reference_video_supported() is True
    assert controller._reference_video_coordinator() is None

    controller._open_reference_video()
    message = controller.window.flash_message.call_args.args[0]
    assert "Start or join an art session" in message


def test_an_invite_without_a_peer_plane_is_not_a_room():
    controller = _controller("art")
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = SimpleNamespace(
        invite=SimpleNamespace(
            peer_enabled=False, session_id=SESSION_ID, invite_token=INVITE_TOKEN
        )
    )

    assert controller._reference_video_coordinator() is None


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


def test_a_host_and_a_guest_bind_to_their_own_roles():
    host = _controller("art")
    _as_host(host)
    assert host._reference_video_coordinator().hosting is True

    guest = _controller("art")
    _as_guest(guest)
    assert guest._reference_video_coordinator().following is True


def test_one_room_reuses_one_coordinator():
    controller = _controller("art")
    _as_guest(controller)

    first = controller._reference_video_coordinator()
    assert controller._reference_video_coordinator() is first


def test_a_new_session_rebuilds_the_coordinator():
    controller = _controller("art")
    _as_guest(controller)
    first = controller._reference_video_coordinator()

    controller.guest_peer.invite = SimpleNamespace(
        peer_enabled=True,
        session_id=str(uuid.uuid4()),
        invite_token="a-different-invite-token",
    )
    second = controller._reference_video_coordinator()

    assert second is not first
    assert first.role == ""


def test_switching_away_from_art_releases_the_reference_video():
    controller = _controller("art")
    _as_guest(controller)
    coordinator = controller._reference_video_coordinator()

    controller._active_creator_profile_key = "music"

    assert controller._reference_video_coordinator() is None
    assert controller._reference_video is None
    assert coordinator.role == ""


# ---------------------------------------------------------------------------
# Guest observation through the real render path
# ---------------------------------------------------------------------------


def test_a_guest_render_feeds_the_follower_the_hosts_video(tmp_path, fake_players):
    controller = _controller("art")
    _as_guest(controller)
    digest = _digest(tmp_path)
    controller.guest_peer.last_state = _peer_state(_shared_video(digest))

    controller._render_guest_peer_state()

    coordinator = controller._reference_video
    assert coordinator is not None
    snapshot = coordinator.follow_snapshot
    assert snapshot.state is ReferenceVideoFollowState.NEEDS_FILE
    assert snapshot.source_display_name == "lesson.mp4"

    coordinator.open_local_copy(str(_write(tmp_path / "mine.mp4")))
    following = coordinator.follow_snapshot
    assert following.state is ReferenceVideoFollowState.FOLLOWING
    assert following.should_play is True
    assert following.target_position_s >= 44.25


def test_a_guest_render_in_a_room_with_no_video_stays_quiet():
    controller = _controller("art")
    _as_guest(controller)
    controller.guest_peer.last_state = _peer_state(ReferenceVideoSessionSnapshot())

    controller._render_guest_peer_state()

    coordinator = controller._reference_video
    assert coordinator.follow_snapshot.state is ReferenceVideoFollowState.NO_VIDEO


def test_a_guest_render_never_disturbs_a_profile_without_the_capability():
    controller = _controller("music")
    _as_guest(controller)
    controller.guest_peer.last_state = SessionStateSnapshot(
        session_id=SESSION_ID,
        generation=3,
        signal=RecordingSignal.IDLE,
        creator_profile_key="music",
    )

    controller._render_guest_peer_state()

    assert controller._reference_video is None
    controller.window.session_strip.set_recording_phase.assert_called_once_with("idle")


def test_an_art_host_owns_the_profile_a_guest_renders():
    """The host's profile wins on join, which is what turns the video on."""

    controller = _controller("music")
    _as_guest(controller)
    controller._apply_creator_profile_key = MagicMock(
        side_effect=lambda key, **_: setattr(
            controller, "_active_creator_profile_key", key
        )
    )
    controller.guest_peer.last_state = _peer_state(ReferenceVideoSessionSnapshot())

    controller._render_guest_peer_state()

    assert controller._active_creator_profile_key == "art"
    assert controller._reference_video is not None


# ---------------------------------------------------------------------------
# Host transport through the controller
# ---------------------------------------------------------------------------


def test_a_host_share_reaches_the_peer_plane_through_the_controller(
    tmp_path, fake_players
):
    controller = _controller("art")
    _as_host(controller)
    coordinator = controller._reference_video_coordinator()

    coordinator.share(str(_write(tmp_path / "lesson.mp4")))
    coordinator.play()

    published = controller.host_peer.publish_reference_video_state.call_args_list
    assert [call.kwargs["state"] for call in published] == ["ready", "playing"]
    assert published[-1].kwargs["shared"] is True
    assert len(published[-1].kwargs["identity_digest"]) == 64


def test_releasing_a_room_publishes_nothing_shared_and_forgets_the_binding(
    tmp_path, fake_players
):
    controller = _controller("art")
    _as_host(controller)
    coordinator = controller._reference_video_coordinator()
    coordinator.share(str(_write(tmp_path / "lesson.mp4")))

    controller._release_reference_video()

    assert controller._reference_video is None
    assert controller._reference_video_binding == ()
    assert fake_players[0].state == "closed"
    final = controller.host_peer.publish_reference_video_state.call_args
    assert final.kwargs == {"state": "idle", "shared": False}


def test_a_failing_intent_shows_bounded_text_and_keeps_the_room(tmp_path):
    controller = _controller("art")
    _as_host(controller)
    coordinator = controller._reference_video_coordinator()

    from core.reference_video import ReferenceVideoError

    def refuse():
        raise ReferenceVideoError("That file is not one WebJam can share.")

    controller._run_reference_video(refuse)

    controller.window.flash_message.assert_called_once()
    assert (
        controller.window.flash_message.call_args.args[0]
        == "That file is not one WebJam can share."
    )
    assert coordinator.role == "host"


def test_an_unexpected_intent_failure_never_leaks_a_raw_exception():
    controller = _controller("art")
    _as_host(controller)
    controller._reference_video_coordinator()

    def explode():
        raise RuntimeError("/Users/alice/Movies/private.mp4 is missing")

    controller._run_reference_video(explode)

    message = controller.window.flash_message.call_args.args[0]
    assert "/Users/alice" not in message
    assert "The room is still running." in message


def _follow(state):
    from core.reference_video import _FOLLOW_MESSAGES, ReferenceVideoFollowSnapshot

    return ReferenceVideoFollowSnapshot(state=state, message=_FOLLOW_MESSAGES[state])


def test_an_artist_is_told_once_when_the_host_starts_sharing():
    """A guest who never opens the panel still has to learn a video exists."""

    controller = _controller("art")
    _as_guest(controller)

    controller._on_reference_video_follow_snapshot(
        _follow(ReferenceVideoFollowState.NO_VIDEO)
    )
    controller.window.flash_message.assert_not_called()

    controller._on_reference_video_follow_snapshot(
        _follow(ReferenceVideoFollowState.NEEDS_FILE)
    )
    message = controller.window.flash_message.call_args.args[0]
    assert "host is sharing a reference video" in message
    # It names the event and what the artist can do about it. It does not
    # route them through a menu: the room's own chip carries the control, and
    # a notice telling someone which menu to open is a sign the thing is not
    # findable.
    assert "More" not in message
    assert "→" not in message
    assert "keep working" in message

    # A steady state must not nag on every tick.
    for _ in range(5):
        controller._on_reference_video_follow_snapshot(
            _follow(ReferenceVideoFollowState.NEEDS_FILE)
        )
    assert controller.window.flash_message.call_count == 1


@pytest.mark.parametrize(
    "state, marker",
    [
        (ReferenceVideoFollowState.MISMATCHED_FILE, "not the same file"),
        (ReferenceVideoFollowState.FILE_UNAVAILABLE, "moved, changed"),
    ],
)
def test_an_artist_is_told_when_their_copy_stops_matching(state, marker):
    controller = _controller("art")
    _as_guest(controller)
    controller._on_reference_video_follow_snapshot(
        _follow(ReferenceVideoFollowState.FOLLOWING)
    )
    controller.window.flash_message.assert_not_called()

    controller._on_reference_video_follow_snapshot(_follow(state))

    assert marker in controller.window.flash_message.call_args.args[0]


@pytest.mark.parametrize(
    "state",
    [
        ReferenceVideoFollowState.FOLLOWING,
        ReferenceVideoFollowState.HIDDEN,
        ReferenceVideoFollowState.NO_VIDEO,
        ReferenceVideoFollowState.HOST_ATTENTION,
        ReferenceVideoFollowState.STALLED,
    ],
)
def test_states_an_artist_chose_or_cannot_act_on_stay_quiet(state):
    controller = _controller("art")
    _as_guest(controller)

    controller._on_reference_video_follow_snapshot(_follow(state))

    controller.window.flash_message.assert_not_called()


def test_leaving_a_room_lets_the_next_one_speak_again():
    controller = _controller("art")
    _as_guest(controller)
    controller._on_reference_video_follow_snapshot(
        _follow(ReferenceVideoFollowState.NEEDS_FILE)
    )
    assert controller.window.flash_message.call_count == 1

    controller._release_reference_video()
    controller._on_reference_video_follow_snapshot(
        _follow(ReferenceVideoFollowState.NEEDS_FILE)
    )

    assert controller.window.flash_message.call_count == 2


def test_the_profile_capability_is_the_only_gate_on_the_menu_entry():
    for profile in ("music", "podcast_voice", "review_rehearsal", "art"):
        controller = _controller(profile)
        expected = get_creator_profile_by_key(
            profile
        ).capabilities.shared_reference_video
        assert controller._reference_video_supported() is expected, profile
