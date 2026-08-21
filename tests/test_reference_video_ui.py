"""The reference video panel and the surfaces that reach it.

The panel is a renderer of immutable snapshots. These tests hold it to that:
it must never invent state, and a follower's copy of it must not carry any
transport a guest is not allowed to use.
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.creative_modes import (  # noqa: E402
    CREATOR_PROFILES,
    get_creator_profile_by_key,
)
from core.reference_video import (  # noqa: E402
    NO_VIDEO_MESSAGE,
    REFERENCE_VIDEO_SUFFIXES,
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
    ReferenceVideoSnapshot,
    ReferenceVideoState,
)
from webjam_qt.windows.reference_video import (  # noqa: E402
    ReferenceVideoDialog,
    clock_text,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture()
def host_dialog(qapp):
    dialog = ReferenceVideoDialog(hosting=True)
    yield dialog
    dialog.deleteLater()


@pytest.fixture()
def guest_dialog(qapp):
    dialog = ReferenceVideoDialog(hosting=False)
    yield dialog
    dialog.deleteLater()


def _shared(**changes) -> ReferenceVideoSnapshot:
    values = {
        "state": ReferenceVideoState.READY,
        "shared": True,
        "source_display_name": "lesson.mp4",
        "identity_digest": "a" * 64,
        "position_s": 0.0,
        "duration_s": 600.0,
        "playback_generation": 0,
    }
    values.update(changes)
    return ReferenceVideoSnapshot(**values)


def _follow(state, **changes) -> ReferenceVideoFollowSnapshot:
    from core.reference_video import _FOLLOW_MESSAGES

    values = {
        "state": state,
        "message": _FOLLOW_MESSAGES[state],
        "duration_s": 600.0,
        "source_display_name": "lesson.mp4",
    }
    values.update(changes)
    return ReferenceVideoFollowSnapshot(**values)


# ---------------------------------------------------------------------------
# Host panel
# ---------------------------------------------------------------------------


def test_the_host_panel_opens_on_the_no_video_path(host_dialog):
    assert host_dialog._headline.text() == "No reference video"
    assert host_dialog._status.text() == NO_VIDEO_MESSAGE
    assert host_dialog._share_button.isEnabled() is True
    for disabled in (
        host_dialog._play_button,
        host_dialog._pause_button,
        host_dialog._stop_button,
        host_dialog._withdraw_button,
    ):
        assert disabled.isEnabled() is False
    assert host_dialog._position.isEnabled() is False


def test_the_host_panel_enables_transport_only_once_a_file_is_shared(host_dialog):
    host_dialog.set_host_snapshot(_shared())

    assert host_dialog._headline.text() == "lesson.mp4"
    assert host_dialog._play_button.isEnabled() is True
    assert host_dialog._pause_button.isEnabled() is False
    assert host_dialog._stop_button.isEnabled() is True
    assert host_dialog._withdraw_button.isEnabled() is True
    assert host_dialog._position.isEnabled() is True
    assert host_dialog._position.maximum() == 600


def test_the_host_panel_swaps_play_and_pause_with_the_transport(host_dialog):
    host_dialog.set_host_snapshot(
        _shared(state=ReferenceVideoState.PLAYING, position_s=61.0)
    )
    assert host_dialog._play_button.isEnabled() is False
    assert host_dialog._pause_button.isEnabled() is True
    assert "Playing for the room" in host_dialog._status.text()
    assert host_dialog._clock.text() == "1:01 / 10:00"

    host_dialog.set_host_snapshot(
        _shared(state=ReferenceVideoState.PAUSED, position_s=61.0)
    )
    assert host_dialog._play_button.isEnabled() is True
    assert host_dialog._pause_button.isEnabled() is False


def test_the_host_panel_shows_a_failure_instead_of_a_stale_source(host_dialog):
    host_dialog.set_host_snapshot(_shared())
    host_dialog.set_host_snapshot(
        ReferenceVideoSnapshot(
            state=ReferenceVideoState.FAILED,
            error="WebJam couldn't open that video on this computer.",
        )
    )

    assert host_dialog._headline.text() == "No reference video"
    assert "couldn't open that video" in host_dialog._status.text()
    assert host_dialog._play_button.isEnabled() is False
    assert host_dialog._clock.text() == "0:00 / 0:00"


def test_a_host_seek_is_emitted_only_when_scrubbing_ends(host_dialog, qapp):
    seeks: list[float] = []
    host_dialog.seek_requested.connect(seeks.append)
    host_dialog.set_host_snapshot(_shared())

    host_dialog._begin_scrub()
    host_dialog._position.setValue(120)
    # A host publishing a moving position must not fight the hand on the slider.
    host_dialog.set_host_snapshot(
        _shared(state=ReferenceVideoState.PLAYING, position_s=5.0)
    )
    assert host_dialog._position.value() == 120
    assert seeks == []

    host_dialog._end_scrub()
    assert seeks == [pytest.approx(120.0)]


def test_a_host_panel_ignores_follower_snapshots(host_dialog):
    host_dialog.set_host_snapshot(_shared())
    host_dialog.set_follow_snapshot(
        _follow(ReferenceVideoFollowState.MISMATCHED_FILE)
    )
    assert host_dialog._headline.text() == "lesson.mp4"


# ---------------------------------------------------------------------------
# Follower panel
# ---------------------------------------------------------------------------


def test_a_follower_panel_offers_no_transport_at_all(guest_dialog):
    for forbidden in (
        "_play_button",
        "_pause_button",
        "_stop_button",
        "_share_button",
        "_withdraw_button",
    ):
        assert not hasattr(guest_dialog, forbidden), forbidden
    assert guest_dialog._position.isEnabled() is False


def test_a_follower_panel_starts_on_the_no_video_path(guest_dialog):
    assert guest_dialog._headline.text() == "No reference video"
    assert guest_dialog._status.text() == NO_VIDEO_MESSAGE
    assert guest_dialog._open_button.isEnabled() is False
    assert guest_dialog._hide_button.isEnabled() is False


def test_a_follower_panel_asks_for_the_hosts_file(guest_dialog):
    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.NEEDS_FILE))

    assert guest_dialog._headline.text() == "lesson.mp4"
    assert "open your own copy" in guest_dialog._status.text().casefold()
    assert guest_dialog._open_button.isEnabled() is True
    assert guest_dialog._hide_button.isEnabled() is True
    assert guest_dialog._close_button.isEnabled() is False


def test_a_follower_panel_names_a_mismatched_file_as_the_reason(guest_dialog):
    guest_dialog.set_follow_snapshot(
        _follow(ReferenceVideoFollowState.MISMATCHED_FILE)
    )
    assert "not the same file" in guest_dialog._status.text().casefold()
    assert guest_dialog._close_button.isEnabled() is True


def test_a_follower_panel_says_when_it_stopped_following(guest_dialog):
    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.STALLED))
    assert "out of date" in guest_dialog._status.text().casefold()


def test_hiding_the_video_flips_the_control_and_keeps_the_panel_usable(guest_dialog):
    requests: list[bool] = []
    guest_dialog.hide_requested.connect(requests.append)
    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.FOLLOWING))
    assert guest_dialog._hide_button.text() == "Hide Video"

    guest_dialog._toggle_hidden()
    assert requests == [True]

    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.HIDDEN))
    assert guest_dialog._hide_button.text() == "Show Video"
    assert guest_dialog._hide_button.isEnabled() is True
    guest_dialog._toggle_hidden()
    assert requests == [True, False]


def test_a_following_panel_renders_the_host_position(guest_dialog):
    guest_dialog.set_follow_snapshot(
        _follow(
            ReferenceVideoFollowState.FOLLOWING,
            can_follow=True,
            should_play=True,
            target_position_s=3_601.0,
            duration_s=7_200.0,
        )
    )
    assert guest_dialog._clock.text() == "1:00:01 / 2:00:00"


def test_clock_text_is_bounded_and_never_negative():
    assert clock_text(-5) == "0:00"
    assert clock_text(0) == "0:00"
    assert clock_text(59.9) == "0:59"
    assert clock_text(3_600) == "1:00:00"


# ---------------------------------------------------------------------------
# Surfaces that reach the panel
# ---------------------------------------------------------------------------


def test_the_panel_embeds_and_releases_a_player_surface(host_dialog, qapp):
    from PySide6.QtWidgets import QWidget

    surface = QWidget()
    host_dialog.attach_surface(surface)
    assert host_dialog._surface_layout.count() == 1

    host_dialog.attach_surface(None)
    assert host_dialog._surface_layout.count() == 0
    surface.deleteLater()


def test_only_studio_visit_exposes_the_reference_video_entry_point(qapp):
    from core.creative_modes import CREATIVE_MODES
    from webjam_qt.widgets.session_strip import SessionStrip

    entries = [(mode.key, mode.label) for mode in CREATIVE_MODES]
    strip = SessionStrip(
        mode_entries=entries,
        initial_mode_key=entries[0][0],
        initial_title="Studio Visit",
    )
    try:
        for profile in CREATOR_PROFILES:
            strip.set_creator_profile(profile)
            expected = profile.capabilities.shared_reference_video
            assert strip._reference_video_action.isVisible() is expected, profile.key
            assert strip._reference_video_action.isEnabled() is expected, profile.key
        strip.set_creator_profile(get_creator_profile_by_key("studio_visit"))
        assert strip._reference_video_action.text() == "Reference Video…"
    finally:
        strip.deleteLater()


def test_the_launch_dialog_offers_studio_visit_without_a_local_project(qapp, tmp_path):
    from unittest.mock import patch

    from core.settings import AppSettings
    from webjam_qt.windows.launch_dialog import LaunchDialog

    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(
            AppSettings(config_file=str(tmp_path / "settings.json"))
        )
    try:
        selector = dialog._creator_profile_selector
        index = selector.findData("studio_visit")
        assert index >= 0
        selector.setCurrentIndex(index)
        assert dialog.selected_creator_profile_key == "studio_visit"
        assert dialog._host_button.text() == "Host Studio Visit"
        assert dialog._join_button.text() == "Join Studio Visit"
        # Studio Visit has no standalone project, so that path stays hidden.
        assert dialog._studio_button.isEnabled() is False
        assert dialog._studio_button.isHidden() is True
        described = " ".join(
            (
                dialog._host_button.accessibleDescription(),
                dialog._join_button.accessibleDescription(),
            )
        ).casefold()
        assert "right to play" in described
        assert "no shared canvas" in described
        assert "hide it and stay in the room" in described
    finally:
        dialog.deleteLater()


def test_every_profile_has_launch_copy():
    from webjam_qt.windows.launch_dialog import _CREATOR_LAUNCH_COPY

    assert set(_CREATOR_LAUNCH_COPY) == {profile.key for profile in CREATOR_PROFILES}


# ---------------------------------------------------------------------------
# The Qt player adapter
# ---------------------------------------------------------------------------


def test_the_qt_player_satisfies_the_seam_and_closes_cleanly(qapp):
    """Real decoding needs a codec and a screen, but the seam must still hold.

    Everything else about the reference video is proven against a fake player,
    so this is the one check that the real adapter implements the same
    contract and does not keep working after it is closed.
    """

    pytest.importorskip("PySide6.QtMultimedia")
    pytest.importorskip("PySide6.QtMultimediaWidgets")

    from core.reference_video import (
        ReferenceVideoPlayer,
        ReferenceVideoPlayerError,
    )
    from webjam_qt.widgets.reference_video_player import (
        create_qt_reference_video_player,
        qt_video_name_filter,
    )

    try:
        player = create_qt_reference_video_player()
    except ReferenceVideoPlayerError as exc:
        pytest.skip(f"no video backend on this machine: {exc}")

    assert isinstance(player, ReferenceVideoPlayer)
    assert player.surface is not None
    assert player.position_s() == 0.0

    player.set_muted(True)
    player.stop()
    player.close()

    assert player.position_s() == 0.0
    player.stop()  # closing twice must stay safe
    player.close()
    for closed in (player.play, player.pause):
        with pytest.raises(ReferenceVideoPlayerError):
            closed()
    with pytest.raises(ReferenceVideoPlayerError):
        player.seek(1.0)

    for suffix in REFERENCE_VIDEO_SUFFIXES:
        assert suffix in qt_video_name_filter()
