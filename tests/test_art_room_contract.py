"""What an Art room promises across all three of its starts.

The per-feature tests prove the canvas and the reference video each work. These
prove the things that are only true when you look at the room as a whole: that
talk-only is a complete session rather than a degraded one, that both add-ons
are the host's to drive, and that the other three profiles gained nothing.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core.creative_modes import CREATOR_PROFILES, get_creator_profile_by_key
from core.reference_video import (
    HOST_ONLY_TRANSPORT_MESSAGE,
    ReferenceVideoError,
    ReferenceVideoFollower,
    ReferenceVideoHostController,
    session_identity_signer,
)
from core.session_transfer import (
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionStateSnapshot,
)
from core.shared_canvas import (
    HOST_ONLY_CANVAS_MESSAGE,
    SharedCanvasError,
    SharedCanvasFollower,
    SharedCanvasFollowState,
    SharedCanvasHostController,
)

ART = "art"
OTHER_PROFILES = ("music", "podcast_voice", "review_rehearsal")
WEB_INVITE = "https://drawpile.net/invites/pub.drawpile.net/kitchen-table?v1"


class FakeLauncher:
    def __init__(self) -> None:
        self.joined: list[str] = []

    def available(self) -> bool:
        return True

    def open_host_page(self) -> None:
        pass

    def open_canvas(self, invite) -> None:
        self.joined.append(invite.join_url)


class FakePlayer:
    def __init__(self) -> None:
        self.state = "idle"
        self.position = 0.0

    def load(self, path: Path) -> float:
        self.state = "ready"
        return 600.0

    def play(self) -> None:
        self.state = "playing"

    def pause(self) -> None:
        self.state = "paused"

    def stop(self) -> None:
        self.state = "ready"
        self.position = 0.0

    def seek(self, position_s: float) -> None:
        self.position = float(position_s)

    def position_s(self) -> float:
        return self.position

    def close(self) -> None:
        self.state = "closed"


# ---------------------------------------------------------------------------
# Talk-only is a complete room
# ---------------------------------------------------------------------------


def test_a_talk_only_room_offers_neither_a_canvas_nor_a_video(tmp_path: Path):
    """The plain door is a finished product, not a stripped-down one."""

    credentials = SessionCredentials.create()
    control = SessionControlState(
        tmp_path, credentials.session_id, creator_profile_key=ART
    )
    state = control.snapshot()

    assert state.creator_profile_key == ART
    assert state.shared_canvas.shared is False
    assert state.reference_video.shared is False

    # A guest in that room is quiet on both, and blocked by neither.
    canvas = SharedCanvasFollower(launcher=FakeLauncher())
    assert (
        canvas.observe(state.shared_canvas).state
        is SharedCanvasFollowState.NO_CANVAS
    )
    assert canvas.resolve().blocked is False

    signer = session_identity_signer(
        session_id=credentials.session_id, session_key=credentials.invite_token
    )
    video = ReferenceVideoFollower(identity_signer=signer)
    video.observe(state.reference_video, received_monotonic_s=100.0)
    follow = video.resolve(100.0)
    assert follow.can_follow is False
    assert follow.blocked is False


def test_a_room_that_never_arms_an_add_on_still_has_a_live_session():
    """Talk is the capability every start shares."""

    art = get_creator_profile_by_key(ART)

    assert art.capabilities.live_session is True
    assert art.capabilities.meeting_handoff is True
    for start in art.starts:
        assert start.talk_only or start.shared_canvas or start.reference_video


# ---------------------------------------------------------------------------
# Both add-ons are the host's
# ---------------------------------------------------------------------------


def test_a_guest_can_drive_neither_the_video_nor_the_canvas(tmp_path: Path):
    """Art has two optional add-ons and a guest owns the transport of neither."""

    video_file = tmp_path / "lesson.mp4"
    video_file.write_bytes(b"a shared lesson")
    signer = session_identity_signer(session_id="room", session_key="token")

    guest_video = ReferenceVideoHostController(
        FakePlayer(), identity_signer=signer, is_host=lambda: False
    )
    for attempt in (
        lambda: guest_video.share(video_file),
        guest_video.play,
        guest_video.pause,
        guest_video.stop,
        lambda: guest_video.seek(10.0),
        guest_video.withdraw,
    ):
        with pytest.raises(ReferenceVideoError) as failure:
            attempt()
        assert str(failure.value) == HOST_ONLY_TRANSPORT_MESSAGE

    guest_canvas = SharedCanvasHostController(
        FakeLauncher(), is_host=lambda: False
    )
    for attempt in (
        lambda: guest_canvas.share(WEB_INVITE),
        guest_canvas.withdraw,
        guest_canvas.open_drawpile_to_host,
    ):
        with pytest.raises(SharedCanvasError) as failure:
            attempt()
        assert str(failure.value) == HOST_ONLY_CANVAS_MESSAGE


def _shared_video_projection(signer, tmp_path: Path):
    from core.reference_video import load_reference_video_source
    from core.session_transfer import (
        ReferenceVideoPlaybackState,
        ReferenceVideoSessionSnapshot,
    )

    video_file = tmp_path / "lesson.mp4"
    video_file.write_bytes(b"a shared lesson")
    source = load_reference_video_source(video_file)
    return ReferenceVideoSessionSnapshot(
        generation=1,
        playback_generation=1,
        state=ReferenceVideoPlaybackState.PLAYING,
        shared=True,
        source_display_name=source.display_name,
        identity_digest=signer(source.content_sha256),
        position_s=10.0,
        duration_s=600.0,
    )


def test_a_guest_may_still_act_locally_on_both(tmp_path: Path):
    """Host-owned transport is not the same as a guest having no choices."""

    signer = session_identity_signer(session_id="room", session_key="token")

    video = ReferenceVideoFollower(identity_signer=signer)
    # Hiding is the guest's own call, and it is accepted before the host has
    # shared anything. Until then the honest state is still "no video".
    assert video.set_hidden(True).state.value == "no_video"
    assert video.hidden is True
    video.observe(
        _shared_video_projection(signer, tmp_path),
        received_monotonic_s=100.0,
    )
    assert video.resolve(100.0).state.value == "hidden"

    # Opening the canvas in their own Drawpile is likewise the guest's call.
    launcher = FakeLauncher()
    canvas = SharedCanvasFollower(launcher=launcher)
    canvas.observe(
        SessionStateSnapshot(
            session_id=str(uuid.uuid4()),
            generation=1,
            signal=RecordingSignal.IDLE,
            creator_profile_key=ART,
            shared_canvas={
                "schema": 1,
                "generation": 1,
                "shared": True,
                "join_url": WEB_INVITE,
                "server_label": "pub.drawpile.net",
                "session_label": "kitchen-table",
            },
        ).shared_canvas
    )
    canvas.open_canvas()
    assert launcher.joined == ["drawpile://pub.drawpile.net/kitchen-table?v1"]


# ---------------------------------------------------------------------------
# The other three profiles gained nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile_key", OTHER_PROFILES)
def test_music_podcast_and_review_gain_no_canvas_video_or_ai(profile_key: str):
    profile = get_creator_profile_by_key(profile_key)

    assert profile.capabilities.shared_canvas is False
    assert profile.capabilities.shared_reference_video is False
    assert profile.capabilities.ai_image is False
    assert profile.starts == ()


@pytest.mark.parametrize("profile_key", OTHER_PROFILES)
def test_the_other_profiles_keep_their_recording_contract(profile_key: str):
    """Adding Art must not quietly narrow a profile that records takes."""

    capabilities = get_creator_profile_by_key(profile_key).capabilities

    assert capabilities.live_session is True
    assert capabilities.shared_reference_audio is True
    assert capabilities.session_recording is True
    assert capabilities.take_review is True


def test_exactly_one_profile_ships_each_of_arts_optional_capabilities():
    canvas = [p.key for p in CREATOR_PROFILES if p.capabilities.shared_canvas]
    video = [p.key for p in CREATOR_PROFILES if p.capabilities.shared_reference_video]
    ai = [p.key for p in CREATOR_PROFILES if p.capabilities.ai_image]

    assert canvas == [ART]
    assert video == [ART]
    assert ai == [ART]


def test_ai_image_adds_nothing_to_the_room_that_anyone_else_can_see():
    """One artist's generator is none of the room's business.

    The canvas and the reference video each have a projection because the room
    genuinely shares them. AI has none, so a generated image can only reach
    the room if its owner puts it on the shared canvas themselves.
    """

    import sys

    from core import ai_image
    from core.ai_image import AiImageController

    wire = set(SessionStateSnapshot.__dataclass_fields__)
    assert "shared_canvas" in wire
    assert "reference_video" in wire
    assert not any(name.startswith("ai") for name in wire)

    # Nothing the module or the controller exposes can publish anything.
    exported = set(dir(ai_image)) | set(dir(AiImageController))
    assert not any("publish" in name or "broadcast" in name for name in exported)

    # And it cannot reach the transfer layer even indirectly through its own
    # imports, so there is no seam for a projection to be added quietly.
    imported = {
        name
        for name, value in vars(ai_image).items()
        if getattr(value, "__module__", "").startswith("core.session_transfer")
    }
    assert imported == set()
    assert "core.session_transfer" not in {
        module for module in sys.modules if module in vars(ai_image)
    }


def test_a_non_art_room_never_publishes_a_canvas_or_a_video(tmp_path: Path):
    """The wire default for every other profile stays empty."""

    for profile_key in OTHER_PROFILES:
        control = SessionControlState(
            tmp_path / profile_key,
            str(uuid.uuid4()),
            creator_profile_key=profile_key,
        )
        state = control.snapshot()

        assert state.shared_canvas.shared is False
        assert state.shared_canvas.join_url == ""
        assert state.reference_video.shared is False
