"""The shared canvas panel, and where the controller meets it.

The panel's whole job is to say one true thing and offer one obvious action.
A guest in particular gets exactly one button, because everything else about
painting belongs to Drawpile.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton  # noqa: E402

from core.creative_modes import CREATOR_PROFILES, get_creator_profile_by_key  # noqa: E402
from core.drawpile import parse_canvas_invite  # noqa: E402
from core.session_transfer import (  # noqa: E402
    RecordingSignal,
    SessionStateSnapshot,
    SharedCanvasSessionSnapshot,
)
from core.shared_canvas import (  # noqa: E402
    SharedCanvasFollowSnapshot,
    SharedCanvasFollowState,
    SharedCanvasSnapshot,
    SharedCanvasState,
)
from core.art_room_presence import (  # noqa: E402
    ArtPresenceTarget,
    ArtPresenceTone,
)
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.windows.shared_canvas import SharedCanvasDialog  # noqa: E402

SESSION_ID = str(uuid.uuid4())
INVITE_TOKEN = "invite-token-for-canvas-integration"
WEB_INVITE = "https://drawpile.net/invites/pub.drawpile.net/kitchen-table?v1#hunter2"
NORMALIZED = parse_canvas_invite(WEB_INVITE).join_url


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _host_snapshot(**changes) -> SharedCanvasSnapshot:
    values = {
        "state": SharedCanvasState.SHARED,
        "shared": True,
        "server_label": "pub.drawpile.net",
        "session_label": "kitchen-table",
        "carries_password": True,
        "launcher_available": True,
    }
    values.update(changes)
    return SharedCanvasSnapshot(**values)


def _follow(state: SharedCanvasFollowState, **changes) -> SharedCanvasFollowSnapshot:
    values = {
        "state": state,
        "can_open": state
        in {SharedCanvasFollowState.READY, SharedCanvasFollowState.OPENED},
        "server_label": "pub.drawpile.net",
        "session_label": "kitchen-table",
        "message": "status text",
    }
    values.update(changes)
    return SharedCanvasFollowSnapshot(**values)


def _visible_buttons(dialog: SharedCanvasDialog) -> list[str]:
    return [
        button.text()
        for button in dialog.findChildren(QPushButton)
        if not button.isHidden()
    ]


# ---------------------------------------------------------------------------
# The guest panel
# ---------------------------------------------------------------------------


def test_a_guest_gets_exactly_one_button_and_no_canvas():
    """No brushes, no layers, no colour picker. Drawpile owns all of that."""

    dialog = SharedCanvasDialog(hosting=False)
    try:
        dialog.set_follow_snapshot(_follow(SharedCanvasFollowState.READY))
        assert _visible_buttons(dialog) == ["Open shared canvas"]
        assert dialog.findChildren(QLineEdit) == []
    finally:
        dialog.deleteLater()


def test_a_guest_is_never_offered_two_equal_calls_to_action():
    """ADR 0002: a surface explains the next action, it does not compete.

    Every guest state offers at most one thing to press, so there is never a
    moment where two buttons look equally like the point of the panel.
    """

    dialog = SharedCanvasDialog(hosting=False)
    try:
        for state in SharedCanvasFollowState:
            dialog.set_follow_snapshot(_follow(state))
            assert len(_visible_buttons(dialog)) <= 1, state
    finally:
        dialog.deleteLater()


def test_a_guest_is_offered_no_way_to_share_or_stop_the_canvas():
    dialog = SharedCanvasDialog(hosting=False)
    try:
        assert not hasattr(dialog, "_invite_input")
        for state in SharedCanvasFollowState:
            dialog.set_follow_snapshot(_follow(state))
            assert dialog._quiet.offered is False, state
            assert dialog._chip.property("action") in (None, "install", "open")
    finally:
        dialog.deleteLater()


def test_a_room_with_no_canvas_offers_nothing_rather_than_a_dead_button():
    """A talk-only room is a finished state, not an unfinished one.

    A greyed-out canvas button would be a small taunt repeated every time
    someone glanced at the panel, so nothing is offered at all.
    """

    dialog = SharedCanvasDialog(hosting=False)
    try:
        dialog.set_follow_snapshot(_follow(SharedCanvasFollowState.NO_CANVAS))
        assert _visible_buttons(dialog) == []
        assert dialog._chip.offered is False
    finally:
        dialog.deleteLater()


def test_a_canvas_webjam_cannot_read_offers_nothing_to_press():
    dialog = SharedCanvasDialog(hosting=False)
    try:
        dialog.set_follow_snapshot(_follow(SharedCanvasFollowState.UNREADABLE))
        assert dialog._chip.offered is False
    finally:
        dialog.deleteLater()


def test_a_guest_without_drawpile_gets_a_recovery_not_an_error():
    """"Install Drawpile to join the canvas" is a way forward, not a fault."""

    dialog = SharedCanvasDialog(hosting=False)
    seen: list[int] = []
    dialog.install_drawpile_requested.connect(lambda: seen.append(1))
    try:
        dialog.set_follow_snapshot(_follow(SharedCanvasFollowState.NEEDS_DRAWPILE))

        assert _visible_buttons(dialog) == ["Install Drawpile"]
        assert dialog._chip.property("tone") == "recovery"
        assert "Install Drawpile to join the canvas" in dialog._status.text()
        # Never a fault report, never internal vocabulary.
        lowered = dialog._status.text().casefold()
        for banned in ("error", "failed", "capability", "traceback", "exception"):
            assert banned not in lowered, banned

        dialog._chip.click()
        assert seen == [1]
    finally:
        dialog.deleteLater()


def test_a_guest_sees_which_canvas_they_are_about_to_open():
    dialog = SharedCanvasDialog(hosting=False)
    try:
        dialog.set_follow_snapshot(_follow(SharedCanvasFollowState.READY))
        headline = dialog._headline.text()
        assert "kitchen-table" in headline
        assert "pub.drawpile.net" in headline
    finally:
        dialog.deleteLater()


def test_the_guest_panel_ignores_host_snapshots():
    dialog = SharedCanvasDialog(hosting=False)
    try:
        dialog.set_follow_snapshot(_follow(SharedCanvasFollowState.NO_CANVAS))
        dialog.set_host_snapshot(_host_snapshot())
        assert "kitchen-table" not in dialog._headline.text()
    finally:
        dialog.deleteLater()


def test_the_guest_hint_says_where_the_painting_actually_happens():
    dialog = SharedCanvasDialog(hosting=False)
    try:
        spoken = " ".join(
            label.text() for label in dialog.findChildren(type(dialog._status))
        ).casefold()
        assert "drawpile" in spoken
        assert "webjam cannot see the canvas" in spoken
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# The host panel
# ---------------------------------------------------------------------------


def test_a_host_starts_with_one_verb_and_no_paste_field():
    """There is nothing to paste until Drawpile has actually been opened."""

    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))

        assert _visible_buttons(dialog) == ["Host a canvas in Drawpile"]
        assert dialog._invite_input.isHidden() is True
    finally:
        dialog.deleteLater()


def test_the_paste_field_appears_only_after_hosting_has_started():
    dialog = SharedCanvasDialog(hosting=True)
    opened: list[int] = []
    dialog.host_in_drawpile_requested.connect(lambda: opened.append(1))
    try:
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))
        dialog._chip.click()

        assert opened == [1]
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))
        assert dialog._invite_input.isHidden() is False
        assert "copy its invitation" in dialog._status.text()
        # Still exactly one thing to press.
        assert len(_visible_buttons(dialog)) == 1
    finally:
        dialog.deleteLater()


def test_the_chip_becomes_share_once_something_is_pasted():
    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))
        dialog._chip.click()
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))
        assert dialog._chip.text() == "Host a canvas in Drawpile"

        dialog._invite_input.setText(WEB_INVITE)

        assert dialog._chip.text() == "Share with the room"
        assert len(_visible_buttons(dialog)) == 1
    finally:
        dialog.deleteLater()


def test_a_sharing_host_has_one_primary_and_one_quiet_action():
    """Stopping a share is a real need and not the point of the panel."""

    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(_host_snapshot())

        assert dialog._chip.text() == "Open canvas"
        assert dialog._quiet.text() == "Stop sharing"
        assert dialog._quiet.offered is True
        # The quiet action is visibly lesser, not a second equal CTA.
        assert dialog._quiet.objectName() == "QuietAction"
        assert dialog._chip.minimumHeight() >= 52
        assert dialog._quiet.minimumHeight() < dialog._chip.minimumHeight()
        assert dialog._invite_input.isHidden() is True
    finally:
        dialog.deleteLater()


def test_the_pasted_invitation_is_never_rendered_as_plain_text():
    """A Drawpile invitation can embed the session password."""

    dialog = SharedCanvasDialog(hosting=True)
    try:
        assert dialog._invite_input.echoMode() is QLineEdit.EchoMode.Password
    finally:
        dialog.deleteLater()


def test_sharing_emits_the_pasted_text_once_and_clears_the_field():
    dialog = SharedCanvasDialog(hosting=True)
    seen: list[str] = []
    dialog.share_requested.connect(seen.append)
    try:
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))
        dialog._chip.click()
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))
        dialog._invite_input.setText(WEB_INVITE)
        dialog._chip.click()

        assert seen == [WEB_INVITE]
        assert dialog._invite_input.text() == ""

        # An empty field must not emit an intent to share nothing.
        dialog._chip.click()
        assert seen == [WEB_INVITE]
    finally:
        dialog.deleteLater()


def test_a_host_without_drawpile_gets_a_recovery_not_a_disabled_panel():
    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=False))

        assert _visible_buttons(dialog) == ["Install Drawpile"]
        assert dialog._chip.property("tone") == "recovery"
        assert dialog._status.text() == "Install Drawpile to paint together."
        assert dialog._invite_input.isHidden() is True
    finally:
        dialog.deleteLater()


def test_a_host_panel_never_claims_the_room_is_painting():
    """WebJam cannot see the canvas, so it must not report who is on it."""

    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(_host_snapshot())
        status = dialog._status.text().casefold()
        assert "webjam cannot see the canvas" in status
        assert "everyone is painting" not in status
        assert "artists connected" not in status
    finally:
        dialog.deleteLater()


def test_an_invitation_without_a_password_says_so_plainly():
    """A Personal session's invitation should carry its password."""

    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(_host_snapshot(carries_password=False))
        assert "no session password" in dialog._status.text().casefold()

        dialog.set_host_snapshot(_host_snapshot(carries_password=True))
        assert "no session password" not in dialog._status.text().casefold()
    finally:
        dialog.deleteLater()


def test_a_failed_canvas_shows_the_failure_instead_of_a_stale_session():
    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(_host_snapshot())
        dialog.set_host_snapshot(
            SharedCanvasSnapshot(
                state=SharedCanvasState.FAILED,
                launcher_available=True,
                error="Drawpile could not be started.",
            )
        )

        assert "needs attention" in dialog._headline.text().casefold()
        assert dialog._status.text() == "Drawpile could not be started."
        assert "kitchen-table" not in dialog._headline.text()
    finally:
        dialog.deleteLater()


def test_the_host_panel_ignores_follower_snapshots():
    dialog = SharedCanvasDialog(hosting=True)
    try:
        dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))
        dialog.set_follow_snapshot(_follow(SharedCanvasFollowState.READY))
        assert "kitchen-table" not in dialog._headline.text()
    finally:
        dialog.deleteLater()


def test_the_host_hint_names_a_personal_session_and_disclaims_the_strokes():
    dialog = SharedCanvasDialog(hosting=True)
    try:
        spoken = " ".join(
            label.text() for label in dialog.findChildren(type(dialog._status))
        ).casefold()
        assert "personal" in spoken
        assert "webjam does not paint the strokes" in spoken
    finally:
        dialog.deleteLater()


def test_the_panel_stays_narrow_enough_to_sit_beside_a_meeting_window():
    for hosting in (True, False):
        dialog = SharedCanvasDialog(hosting=hosting)
        try:
            assert dialog.minimumWidth() <= 400
            assert dialog.isModal() is False
        finally:
            dialog.deleteLater()


# ---------------------------------------------------------------------------
# Where the menu exposes it
# ---------------------------------------------------------------------------


def test_only_art_exposes_the_shared_canvas_entry_point(qapp):
    from webjam_qt.widgets.session_strip import SessionStrip

    strip = SessionStrip(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Art",
    )
    try:
        for profile in CREATOR_PROFILES:
            strip.set_creator_profile(profile)
            expected = profile.key == "art"
            assert strip._shared_canvas_action.isVisible() is expected, profile.key
            assert strip._shared_canvas_action.isEnabled() is expected, profile.key
    finally:
        strip.deleteLater()


# ---------------------------------------------------------------------------
# The controller seams
# ---------------------------------------------------------------------------


class FakeLauncher:
    def __init__(self, *, installed: bool = True) -> None:
        self.installed = installed
        self.host_pages = 0
        self.joined: list[str] = []

    def available(self) -> bool:
        return self.installed

    def open_host_page(self) -> None:
        self.host_pages += 1

    def open_canvas(self, invite) -> None:
        self.joined.append(invite.join_url)


@pytest.fixture()
def fake_launchers(monkeypatch):
    made: list[FakeLauncher] = []

    def factory(settings=None):
        launcher = FakeLauncher()
        made.append(launcher)
        return launcher

    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", factory)
    return made


def _controller(profile_key: str) -> ApplicationController:
    controller = ApplicationController.__new__(ApplicationController)
    controller._active_creator_profile_key = profile_key
    controller._shutdown = False
    controller._shutdown_in_progress = False
    controller._shutdown_cleanup_pending = False
    controller._shared_canvas = None
    controller._shared_canvas_dialog = None
    controller._shared_canvas_binding = ()
    controller._shared_canvas_notified_state = ""
    controller.settings = SimpleNamespace(drawpile_candidates=[])
    controller.window = SimpleNamespace(flash_message=MagicMock())
    return controller


def _as_host(controller: ApplicationController) -> None:
    controller.host_peer = SimpleNamespace(
        active=True,
        credentials=SimpleNamespace(
            session_id=SESSION_ID, invite_token=INVITE_TOKEN
        ),
        publish_shared_canvas_state=MagicMock(),
    )
    controller.guest_peer = None


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


def test_a_profile_without_the_capability_owns_no_canvas(fake_launchers):
    for profile_key in ("music", "podcast_voice", "review_rehearsal"):
        controller = _controller(profile_key)
        _as_host(controller)

        assert controller._shared_canvas_supported() is False
        assert controller._shared_canvas_coordinator() is None
        assert fake_launchers == []


def test_a_profile_without_the_capability_refuses_to_open_the_panel(fake_launchers):
    controller = _controller("music")
    _as_host(controller)

    controller._open_shared_canvas()

    controller.window.flash_message.assert_called_once()
    message = controller.window.flash_message.call_args.args[0]
    assert "Art" in message
    assert controller._shared_canvas_dialog is None


def test_art_without_a_started_room_owns_no_canvas(fake_launchers):
    controller = _controller("art")
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = None

    assert controller._shared_canvas_coordinator() is None

    controller._open_shared_canvas()
    message = controller.window.flash_message.call_args.args[0]
    assert "Start or join an art session" in message


def test_a_host_binds_its_own_role_and_reuses_one_coordinator(fake_launchers):
    controller = _controller("art")
    _as_host(controller)

    coordinator = controller._shared_canvas_coordinator()

    assert coordinator is not None
    assert coordinator.hosting is True
    assert controller._shared_canvas_coordinator() is coordinator


def test_a_guest_binds_as_a_follower(fake_launchers):
    controller = _controller("art")
    _as_guest(controller)

    coordinator = controller._shared_canvas_coordinator()

    assert coordinator is not None
    assert coordinator.following is True


def test_switching_away_from_art_releases_the_canvas(fake_launchers):
    controller = _controller("art")
    _as_host(controller)
    assert controller._shared_canvas_coordinator() is not None

    controller._active_creator_profile_key = "music"

    assert controller._shared_canvas_coordinator() is None
    assert controller._shared_canvas is None


def test_a_new_room_rebuilds_the_coordinator(fake_launchers):
    controller = _controller("art")
    _as_host(controller)
    first = controller._shared_canvas_coordinator()

    controller.host_peer = SimpleNamespace(
        active=True,
        credentials=SimpleNamespace(
            session_id=str(uuid.uuid4()), invite_token=INVITE_TOKEN
        ),
        publish_shared_canvas_state=MagicMock(),
    )

    assert controller._shared_canvas_coordinator() is not first


def test_a_share_through_the_controller_reaches_the_peer_plane(fake_launchers):
    controller = _controller("art")
    _as_host(controller)
    coordinator = controller._shared_canvas_coordinator()

    controller._run_shared_canvas(lambda: coordinator.share(WEB_INVITE))

    controller.host_peer.publish_shared_canvas_state.assert_called_once_with(
        shared=True,
        join_url=NORMALIZED,
        server_label="pub.drawpile.net",
        session_label="kitchen-table",
    )
    controller.window.flash_message.assert_not_called()


def test_releasing_the_canvas_publishes_nothing_shared(fake_launchers):
    controller = _controller("art")
    _as_host(controller)
    coordinator = controller._shared_canvas_coordinator()
    coordinator.share(WEB_INVITE)

    controller._release_shared_canvas()

    assert controller.host_peer.publish_shared_canvas_state.call_args.kwargs == {
        "shared": False
    }
    assert controller._shared_canvas is None


def test_a_failing_intent_shows_bounded_text_and_keeps_the_room(fake_launchers):
    controller = _controller("art")
    _as_host(controller)
    coordinator = controller._shared_canvas_coordinator()

    controller._run_shared_canvas(lambda: coordinator.share("https://example.com/x"))

    message = controller.window.flash_message.call_args.args[0]
    assert "not a Drawpile invitation" in message
    assert "example.com" not in message


def test_an_unexpected_failure_never_leaks_a_raw_exception(fake_launchers):
    controller = _controller("art")
    _as_host(controller)
    controller._shared_canvas_coordinator()

    def explode():
        raise ZeroDivisionError("internal detail nobody should read")

    controller._run_shared_canvas(explode)

    message = controller.window.flash_message.call_args.args[0]
    assert "internal detail" not in message
    assert "room is still running" in message


def test_a_guest_render_feeds_the_follower_the_hosts_canvas(fake_launchers):
    controller = _controller("art")
    _as_guest(controller)
    coordinator = controller._shared_canvas_coordinator()

    coordinator.observe_host_state(
        SessionStateSnapshot(
            session_id=SESSION_ID,
            generation=3,
            signal=RecordingSignal.IDLE,
            creator_profile_key="art",
            shared_canvas=SharedCanvasSessionSnapshot(
                generation=1,
                shared=True,
                join_url=WEB_INVITE,
                server_label="pub.drawpile.net",
                session_label="kitchen-table",
            ),
        )
    )

    assert coordinator.follow_snapshot.state is SharedCanvasFollowState.READY


def test_a_guest_is_told_once_when_a_canvas_appears(fake_launchers):
    controller = _controller("art")
    _as_guest(controller)

    snapshot = _follow(SharedCanvasFollowState.READY)
    controller._on_shared_canvas_follow_snapshot(snapshot)
    controller._on_shared_canvas_follow_snapshot(snapshot)

    controller.window.flash_message.assert_called_once()
    assert "shared a canvas" in controller.window.flash_message.call_args.args[0]


def test_a_state_the_artist_already_acted_on_stays_quiet(fake_launchers):
    controller = _controller("art")
    _as_guest(controller)

    controller._on_shared_canvas_follow_snapshot(
        _follow(SharedCanvasFollowState.OPENED)
    )
    controller._on_shared_canvas_follow_snapshot(
        _follow(SharedCanvasFollowState.NO_CANVAS)
    )

    controller.window.flash_message.assert_not_called()


def test_a_guest_without_drawpile_is_told_how_to_get_it(fake_launchers):
    controller = _controller("art")
    _as_guest(controller)

    controller._on_shared_canvas_follow_snapshot(
        _follow(SharedCanvasFollowState.NEEDS_DRAWPILE)
    )

    message = controller.window.flash_message.call_args.args[0]
    assert "Drawpile is not installed" in message


def test_the_profile_capability_is_the_only_gate_on_the_menu_entry():
    for profile in CREATOR_PROFILES:
        controller = _controller(profile.key)
        assert controller._shared_canvas_supported() is bool(
            profile.capabilities.shared_canvas
        )
    assert get_creator_profile_by_key("art").capabilities.shared_canvas is True


# ---------------------------------------------------------------------------
# What the chosen start does once a room exists
# ---------------------------------------------------------------------------


def _with_start(controller: ApplicationController, start_key: str) -> None:
    controller.settings = SimpleNamespace(
        drawpile_candidates=[],
        krita_candidates=[],
        krita_resource_dirs=[],
        comfyui_url="",
        last_creator_start_key=start_key,
    )
    controller._art_start_timer = SimpleNamespace(stop=MagicMock())
    controller._reference_video = None
    controller._reference_video_dialog = None
    controller._reference_video_binding = ()
    controller._ai_image = None
    controller.window.session_strip = SimpleNamespace(
        set_art_room_presence=MagicMock()
    )


def _presence(controller: ApplicationController):
    """Run the real derivation and return what the room was told to show."""

    controller._sync_art_room_presence()
    strip = controller.window.session_strip
    strip.set_art_room_presence.assert_called_once()
    return strip.set_art_room_presence.call_args.args[0]


def test_the_chosen_start_resolves_against_the_active_profile(fake_launchers):
    controller = _controller("art")
    _with_start(controller, "paint_together")
    assert controller.creator_start.key == "paint_together"

    # A key saved under another profile must not arm anything here.
    _with_start(controller, "host_guest")
    assert controller.creator_start.key == "talk_and_make"

    music = _controller("music")
    _with_start(music, "paint_together")
    assert music.creator_start is None


def test_a_canvas_start_shows_the_host_a_persistent_way_in(fake_launchers):
    """The room used to answer this with a nine-second message naming the menu
    to open. It carries a control now, which is still there a minute later."""

    controller = _controller("art")
    _with_start(controller, "paint_together")
    _as_host(controller)

    presence = _presence(controller)

    assert presence.label == "Set up shared canvas"
    assert presence.target is ArtPresenceTarget.CANVAS
    assert presence.tone is ArtPresenceTone.PRESENT
    # Nothing is opened at the host, so nothing takes focus from the meeting.
    assert controller.window.flash_message.call_count == 0


def test_the_way_in_does_not_disappear_after_being_shown_once(fake_launchers):
    controller = _controller("art")
    _with_start(controller, "paint_together")
    _as_host(controller)

    controller._sync_art_room_presence()
    controller._sync_art_room_presence()
    strip = controller.window.session_strip

    assert strip.set_art_room_presence.call_count == 2
    labels = {
        call.args[0].label for call in strip.set_art_room_presence.call_args_list
    }
    assert labels == {"Set up shared canvas"}


def test_a_video_start_points_at_the_reference_video_instead(fake_launchers):
    controller = _controller("art")
    _with_start(controller, "paint_along")
    _as_host(controller)
    controller.host_peer.publish_reference_video_state = MagicMock()

    presence = _presence(controller)

    assert presence.label == "Set up reference video"
    assert presence.target is ArtPresenceTarget.VIDEO


def test_a_talk_only_start_leaves_the_room_chrome_empty(fake_launchers):
    """Someone who chose to just talk and work has a finished room."""

    controller = _controller("art")
    _with_start(controller, "talk_and_make")
    _as_host(controller)

    presence = _presence(controller)

    assert presence.offered is False
    assert presence.target is ArtPresenceTarget.NONE


def test_a_start_shows_nothing_until_there_is_a_room(fake_launchers):
    controller = _controller("art")
    _with_start(controller, "paint_together")
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = None

    presence = _presence(controller)

    assert presence.offered is False


def test_a_guest_is_never_shown_a_start_they_did_not_choose(fake_launchers):
    """A guest's saved start says nothing about the room they joined, so an
    empty room stays empty for them however they last hosted."""

    controller = _controller("art")
    _with_start(controller, "paint_together")
    _as_guest(controller)

    presence = _presence(controller)

    assert presence.offered is False


def test_a_profile_without_starts_never_shows_an_art_line(fake_launchers):
    for profile_key in ("music", "podcast_voice", "review_rehearsal"):
        controller = _controller(profile_key)
        _with_start(controller, "paint_together")
        _as_host(controller)

        controller._sync_art_room_presence()

        strip = controller.window.session_strip
        presence = strip.set_art_room_presence.call_args.args[0]
        assert presence.offered is False
        assert fake_launchers == []


def test_the_room_survives_a_presence_failure_without_going_quiet(fake_launchers):
    """Room chrome must not be able to take the room down with it -- and a
    real fault must not vanish silently either."""

    controller = _controller("art")
    _with_start(controller, "paint_together")
    _as_host(controller)
    controller.window.session_strip = SimpleNamespace(
        set_art_room_presence=MagicMock(side_effect=RuntimeError("boom"))
    )

    controller._tick_creator_start()

    assert controller._art_room_presence_failed is True


def test_the_bounded_art_tick_retries_canvas_publication(fake_launchers):
    controller = _controller("art")
    _as_host(controller)
    canvas = controller._shared_canvas_coordinator()
    canvas.tick = MagicMock()

    controller._tick_creator_start()

    canvas.tick.assert_called_once_with()
