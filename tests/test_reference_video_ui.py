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
        "can_close_local_copy": state in {
            ReferenceVideoFollowState.FOLLOWING,
            ReferenceVideoFollowState.MISMATCHED_FILE,
            ReferenceVideoFollowState.FILE_UNAVAILABLE,
            ReferenceVideoFollowState.STALLED,
        },
    }
    values.update(changes)
    return ReferenceVideoFollowSnapshot(**values)


# ---------------------------------------------------------------------------
# Host panel
# ---------------------------------------------------------------------------


def test_the_host_panel_opens_on_the_no_video_path(host_dialog):
    assert host_dialog.windowTitle() == "Paint along"
    assert host_dialog.minimumWidth() == 720
    assert host_dialog.minimumHeight() == 520
    assert host_dialog._headline.text() == "Choose a process video"
    assert host_dialog._status.text() == (
        "Paint in Procreate, Clip Studio Paint, Krita, or on paper beside WebJam."
    )
    assert host_dialog._surface_holder.isHidden() is False
    assert host_dialog._surface_placeholder.text() == (
        "Your silent process video appears here"
    )
    assert host_dialog._role.text() == "YOU CONTROL"
    assert "silent in webjam" in host_dialog._hint.text().casefold()
    assert "talk in your meeting" in host_dialog._hint.text().casefold()
    process_detail = host_dialog._hint.accessibleDescription().casefold()
    for painting_place in ("procreate", "clip studio paint", "krita", "paper"):
        assert painting_place in process_detail
    assert host_dialog._share_button.isEnabled() is True
    for disabled in (
        host_dialog._play_button,
        host_dialog._pause_button,
    ):
        assert disabled.isEnabled() is False
    assert host_dialog._more_button.isHidden() is True
    assert host_dialog._position.isEnabled() is False


def test_the_host_panel_enables_transport_only_once_a_file_is_shared(host_dialog):
    host_dialog.set_host_snapshot(_shared())

    assert host_dialog._headline.text() == "lesson.mp4"
    assert host_dialog._play_button.isEnabled() is True
    assert host_dialog._pause_button.isEnabled() is False
    assert host_dialog._stop_action.isVisible() is True
    assert host_dialog._withdraw_action.isVisible() is True
    assert host_dialog._more_button.isHidden() is False
    assert host_dialog._position.isEnabled() is True
    assert host_dialog._position.maximum() == 600


def test_the_host_panel_swaps_play_and_pause_with_the_transport(host_dialog):
    host_dialog.set_host_snapshot(
        _shared(state=ReferenceVideoState.PLAYING, position_s=61.0)
    )
    assert host_dialog._play_button.isEnabled() is False
    assert host_dialog._pause_button.isEnabled() is True
    assert host_dialog._status.text() == "Playing."
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

    assert host_dialog._headline.text() == "Choose a process video"
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


def test_the_host_panel_never_claims_the_room_is_watching(host_dialog):
    """WebJam cannot see another artist's screen, so it must not say it can."""

    host_dialog.set_host_snapshot(
        _shared(state=ReferenceVideoState.PLAYING, position_s=10.0)
    )
    surfaced = " ".join(
        widget.text()
        for widget in host_dialog.findChildren(type(host_dialog._status))
    ).casefold()
    detail = host_dialog._hint.accessibleDescription().casefold()

    assert "everyone in the room watches" not in surfaced
    assert "everyone" not in surfaced
    assert "cannot confirm who has opened or watched it" in detail
    assert "silent in webjam" in surfaced


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
    assert guest_dialog.windowTitle() == "Paint along"
    assert guest_dialog._headline.text() == "Waiting for a process video"
    assert guest_dialog._status.text() == "The host will choose the process video."
    assert guest_dialog._surface_placeholder.text() == (
        "Your silent process video appears here"
    )
    assert guest_dialog._role.text() == "YOU FOLLOW"
    assert guest_dialog._open_button.isEnabled() is False
    assert guest_dialog._hide_button.isEnabled() is False


def test_a_follower_panel_asks_for_the_hosts_file(guest_dialog):
    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.NEEDS_FILE))

    assert guest_dialog._headline.text() == "lesson.mp4"
    assert "open your own copy" in guest_dialog._status.text().casefold()
    assert guest_dialog._open_button.isEnabled() is True
    assert guest_dialog._hide_button.isHidden() is True
    assert guest_dialog._hide_action.isVisible() is True
    assert guest_dialog._close_action.isVisible() is False


def test_a_follower_panel_names_a_mismatched_file_as_the_reason(guest_dialog):
    guest_dialog.set_follow_snapshot(
        _follow(ReferenceVideoFollowState.MISMATCHED_FILE)
    )
    assert "not the same file" in guest_dialog._status.text().casefold()
    assert guest_dialog._close_action.isVisible() is True


def test_a_follower_panel_says_when_it_stopped_following(guest_dialog):
    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.STALLED))
    assert "out of date" in guest_dialog._status.text().casefold()


def test_hiding_the_video_flips_the_control_and_keeps_the_panel_usable(guest_dialog):
    requests: list[bool] = []
    guest_dialog.hide_requested.connect(requests.append)
    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.FOLLOWING))
    assert guest_dialog._hide_button.text() == "Hide video"

    guest_dialog._toggle_hidden()
    assert requests == [True]

    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.HIDDEN))
    assert guest_dialog._hide_button.text() == "Show video"
    assert guest_dialog._hide_button.isEnabled() is True
    guest_dialog._toggle_hidden()
    assert requests == [True, False]


def test_a_follower_panel_offers_return_when_the_room_is_unavailable(guest_dialog):
    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.FOLLOWING))
    assert guest_dialog._hide_button.isHidden() is False
    assert guest_dialog._return_button.isHidden() is True

    guest_dialog.set_room_available(False)

    assert guest_dialog._headline.text() == "Waiting for the room"
    assert "cannot confirm" in guest_dialog._status.text().casefold()
    assert guest_dialog._return_button.isHidden() is False
    assert guest_dialog._return_button.isEnabled() is True
    assert guest_dialog._return_button.text() == "Return to room"
    assert guest_dialog._hide_button.isHidden() is True
    assert guest_dialog._open_button.isHidden() is True
    assert guest_dialog._clock.isHidden() is True
    assert guest_dialog._position.isHidden() is True
    guest_dialog.set_follow_snapshot(_follow(ReferenceVideoFollowState.FOLLOWING))
    assert guest_dialog._headline.text() == "Waiting for the room"
    assert guest_dialog._return_button.isHidden() is False

    guest_dialog.set_room_available(True)

    assert guest_dialog._headline.text() == "lesson.mp4"
    assert guest_dialog._hide_button.isHidden() is False
    assert guest_dialog._return_button.isHidden() is True


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
    assert host_dialog._attached_surface is surface
    assert host_dialog._surface_placeholder.isHidden() is True

    host_dialog.attach_surface(None)
    assert host_dialog._attached_surface is None
    assert host_dialog._surface_placeholder.isHidden() is False
    assert surface.isWindow() is False
    assert surface.parent() is host_dialog._surface_holder
    assert surface not in QApplication.topLevelWidgets()
    surface.deleteLater()


def test_only_art_exposes_the_reference_video_entry_point(qapp):
    from core.creative_modes import CREATIVE_MODES
    from webjam_qt.widgets.session_strip import SessionStrip

    entries = [(mode.key, mode.label) for mode in CREATIVE_MODES]
    strip = SessionStrip(
        mode_entries=entries,
        initial_mode_key=entries[0][0],
        initial_title="Art",
    )
    try:
        for profile in CREATOR_PROFILES:
            strip.set_creator_profile(profile)
            expected = profile.capabilities.shared_reference_video
            assert strip._reference_video_action.isVisible() is expected, profile.key
            assert strip._reference_video_action.isEnabled() is expected, profile.key
        strip.set_creator_profile(get_creator_profile_by_key("art"))
        assert strip._reference_video_action.text() == "Paint along…"
    finally:
        strip.deleteLater()


def test_the_launch_dialog_offers_art_without_a_local_project(qapp, tmp_path):
    from unittest.mock import patch

    from core.settings import AppSettings
    from webjam_qt.windows.launch_dialog import LaunchDialog

    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(
            AppSettings(config_file=str(tmp_path / "settings.json"))
        )
    try:
        selector = dialog._creator_profile_selector
        index = selector.findData("art")
        assert index >= 0
        selector.setCurrentIndex(index)
        assert dialog.selected_creator_profile_key == "art"
        assert dialog._host_button.text() == "Host"
        assert dialog._join_button.text() == "Join"
        # Art has no standalone project, so that path stays hidden.
        assert dialog._studio_button.isEnabled() is False
        assert dialog._studio_button.isHidden() is True
        # The video's honesty lives on the card that offers it, in words a
        # person can read in ten seconds. The component that plays it, and
        # every caveat, waits until they are in the room.
        cards = {card.start_key: card for card in dialog._visible_start_cards()}
        paint_along = cards["paint_along"].accessibleDescription().casefold()
        assert "already own" in paint_along
        assert "own copy of the same file" in paint_along
        assert "host keeps it in step" in paint_along
        assert "supplies no videos" in paint_along
    finally:
        dialog.deleteLater()


def test_paint_along_starts_as_a_deliberate_video_surface(qapp):
    """The chosen Paint along door lands on the making surface, not settings."""

    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    dialog = ReferenceVideoDialog(hosting=True)
    try:
        dialog.set_host_snapshot(ReferenceVideoSnapshot())

        assert dialog._surface_holder.isHidden() is False
        assert dialog._surface_holder.minimumHeight() >= 360
        assert dialog._surface_placeholder.isHidden() is False
        assert dialog._position.isHidden() is True
        assert dialog._clock.isHidden() is True
        # One verb: share a video. Utilities and transport stay out of the way.
        assert dialog._share_button.isHidden() is False
        for button in (
            dialog._play_button,
            dialog._pause_button,
        ):
            assert button.isHidden() is True
        assert dialog._more_button.isHidden() is True
    finally:
        dialog.deleteLater()


def test_a_guest_in_a_room_with_no_video_is_offered_nothing(qapp):
    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    dialog = ReferenceVideoDialog(hosting=False)
    try:
        dialog.set_follow_snapshot(ReferenceVideoFollowSnapshot())

        assert dialog._surface_holder.isHidden() is False
        assert dialog._surface_placeholder.text() == (
            "Your silent process video appears here"
        )
        for button in (
            dialog._open_button,
            dialog._hide_button,
            dialog._return_button,
        ):
            assert button.isHidden() is True
        assert dialog._more_button.isHidden() is True
    finally:
        dialog.deleteLater()


def test_the_host_transport_appears_only_once_something_is_shared(qapp):
    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    dialog = ReferenceVideoDialog(hosting=True)
    try:
        dialog.set_host_snapshot(
            ReferenceVideoSnapshot(
                state=ReferenceVideoState.READY,
                shared=True,
                source_display_name="lesson.mp4",
                identity_digest="a" * 64,
                duration_s=600.0,
            )
        )

        assert dialog._share_button.isHidden() is True
        assert dialog._play_button.isHidden() is False
        assert dialog._pause_button.isHidden() is True
        assert dialog._position.isHidden() is False
    finally:
        dialog.deleteLater()


def test_play_and_pause_are_never_both_offered(qapp):
    """Two transport buttons for one state would be two ways to say now."""

    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    dialog = ReferenceVideoDialog(hosting=True)
    try:
        for state in (
            ReferenceVideoState.READY,
            ReferenceVideoState.PLAYING,
            ReferenceVideoState.PAUSED,
        ):
            dialog.set_host_snapshot(
                ReferenceVideoSnapshot(
                    state=state,
                    shared=True,
                    source_display_name="lesson.mp4",
                    identity_digest="a" * 64,
                    duration_s=600.0,
                )
            )
            offered = [
                not dialog._play_button.isHidden(),
                not dialog._pause_button.isHidden(),
            ]
            assert offered.count(True) == 1, state
    finally:
        dialog.deleteLater()


def test_each_role_state_offers_one_plain_primary_action(qapp):
    host = ReferenceVideoDialog(hosting=True)
    guest = ReferenceVideoDialog(hosting=False)
    try:
        assert host._share_button.text() == "Choose process video…"
        assert host._share_button.isHidden() is False

        host.set_host_snapshot(_shared(state=ReferenceVideoState.READY))
        assert host._play_button.text() == "Play"
        assert host._play_button.isHidden() is False

        host.set_host_snapshot(_shared(state=ReferenceVideoState.PLAYING))
        assert host._pause_button.text() == "Pause"
        assert host._pause_button.isHidden() is False

        guest.set_follow_snapshot(_follow(ReferenceVideoFollowState.NEEDS_FILE))
        assert guest._open_button.text() == "Open my copy…"
        assert guest._open_button.isHidden() is False

        guest.set_follow_snapshot(_follow(ReferenceVideoFollowState.FOLLOWING))
        assert guest._hide_button.text() == "Hide video"
        assert guest._hide_button.isHidden() is False

        guest.set_follow_snapshot(_follow(ReferenceVideoFollowState.HIDDEN))
        assert guest._hide_button.text() == "Show video"
        assert guest._hide_button.isHidden() is False
    finally:
        host.deleteLater()
        guest.deleteLater()


def test_the_surface_swaps_its_placeholder_for_the_one_player(qapp):
    from PySide6.QtWidgets import QWidget

    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    dialog = ReferenceVideoDialog(hosting=True)
    surface = QWidget()
    try:
        assert dialog._surface_holder.isHidden() is False
        assert dialog._surface_placeholder.isHidden() is False

        dialog.attach_surface(surface)
        assert dialog._surface_holder.isHidden() is False
        assert dialog._surface_placeholder.isHidden() is True

        dialog.attach_surface(None)
        assert dialog._surface_holder.isHidden() is False
        assert dialog._surface_placeholder.isHidden() is False
    finally:
        surface.deleteLater()
        dialog.deleteLater()


def test_paint_along_reuses_webjams_single_top_level_window(qapp):
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtTest import QTest

    from webjam_qt.windows.conductor_window import ConductorWindow

    # Other UI modules share this QApplication in the repository-wide run.
    # Hide any leftover top-level test windows before proving a WindowShortcut;
    # the real app has one WebJam window, and the test should model that truth.
    # Hiding avoids invoking another test window's product close confirmation.
    for stale_window in QApplication.topLevelWidgets():
        stale_window.hide()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()

    entries = [(profile.key, profile.label) for profile in CREATOR_PROFILES]
    window = ConductorWindow(
        mode_entries=entries,
        initial_mode_key="art",
        initial_title="Art",
    )
    panel = ReferenceVideoDialog(hosting=True, parent=window)
    original_title = window.windowTitle()
    try:
        panel.return_requested.connect(lambda: window.hide_paint_along(panel))
        window.show_paint_along(panel)
        window.show()
        qapp.processEvents()

        assert window.workspace_stack.currentWidget() is panel
        assert panel.isWindow() is False
        assert panel not in QApplication.topLevelWidgets()
        assert panel._back_button.isHidden() is False
        assert window.windowTitle() == original_title

        # The full suite deliberately reuses one QApplication. Re-activate
        # this window before exercising its WindowShortcut so a hidden window
        # from an earlier module cannot own Escape in an offscreen run.
        window.activateWindow()
        panel.setFocus()
        qapp.processEvents()
        QTest.keyClick(panel, Qt.Key.Key_Escape)
        qapp.processEvents()
        assert window.workspace_stack.currentWidget() is window.center_splitter

        window.show_paint_along(panel)
        window.release_paint_along(panel)
        assert window.workspace_stack.currentWidget() is window.center_splitter
        assert window._paint_along_widget is None
        assert window.workspace_stack.indexOf(panel) == -1
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        replacement = ReferenceVideoDialog(hosting=True, parent=window)
        window.show_paint_along(replacement)
        assert window.workspace_stack.currentWidget() is replacement
        assert replacement.isWindow() is False
        window.release_paint_along(replacement)
        replacement.deleteLater()
    finally:
        window.deleteLater()


def test_embedded_paint_along_fits_the_supported_compact_window(qapp):
    from webjam_qt.windows.conductor_window import ConductorWindow

    entries = [(profile.key, profile.label) for profile in CREATOR_PROFILES]
    window = ConductorWindow(
        mode_entries=entries,
        initial_mode_key="art",
        initial_title="Art",
    )
    panel = ReferenceVideoDialog(hosting=True, parent=window)
    try:
        panel.set_host_snapshot(
            _shared(state=ReferenceVideoState.PLAYING, position_s=61.0)
        )
        window.show_paint_along(panel)
        window.resize(760, 600)
        window.show()
        qapp.processEvents()

        assert window.size().width() == 760
        assert window.size().height() == 600
        assert panel.minimumHeight() == 0
        assert panel._surface_holder.geometry().bottom() < panel._position.geometry().top()
        assert panel._position.geometry().bottom() < panel._pause_button.geometry().top()
        assert panel._pause_button.geometry().bottom() < panel._hint.geometry().top()
        assert panel.geometry().bottom() <= window.workspace_stack.rect().bottom()
        assert panel._hint.isVisible() is True
        assert window.session_controls.isVisible() is True
    finally:
        window.release_paint_along(panel)
        panel.deleteLater()
        window.deleteLater()


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

    from core.reference_video import (
        ReferenceVideoPlayer,
        ReferenceVideoPlayerError,
    )
    from webjam_qt.widgets.reference_video_player import (
        create_qt_reference_video_player,
        qt_video_name_filter,
    )

    # A machine without QtMultimedia, or without the shared libraries it
    # loads, is exactly the case the adapter turns into a bounded error so an
    # artist stays in the room. Skipping here relies on that conversion rather
    # than on importing QtMultimedia directly, because a missing shared object
    # raises a plain ImportError that ``importorskip`` re-raises.
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
