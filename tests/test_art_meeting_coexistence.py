"""Art beside a meeting window, not instead of one.

Webex is the primary meeting platform and other providers stay supported, but
either way the meeting is somebody else's window. ADR 0004 settled the shape:
WebJam stores a link, focuses or launches the verified app, or hands the link
off once. It does not embed the meeting, does not own mute, camera, or join,
and does not tap meeting, browser, or system output.

Everything Art added -- a shared canvas, a reference video, an AI image action,
a room clock -- has to be true beside that window rather than in place of it.
These tests hold the boundary from the Art side: Art keeps the handoff, Art's
own paths never reach for the meeting app, Art never claims to be in the
meeting, and opening any Art surface cannot take the conversation's sound or
its focus.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.creative_modes import CREATOR_PROFILES, get_creator_profile_by_key  # noqa: E402
from core.meeting_link import (  # noqa: E402
    MEETING_DIRECT_CAPTURE_BOUNDARY,
    is_allowed_meeting_link,
)

ART = "art"

#: Everything Art added on top of the room. None of it may reach for the
#: meeting app, and none of it may own the conversation.
ART_MODULES = (
    "core/drawpile.py",
    "core/shared_canvas.py",
    "core/ai_image.py",
    "core/krita_ai.py",
    "core/room_clock.py",
    "core/reference_video.py",
    "core/external_program.py",
    "services/drawpile_service.py",
    "services/krita_ai_service.py",
    "webjam_qt/controllers/shared_canvas_coordinator.py",
    "webjam_qt/controllers/reference_video_coordinator.py",
    "webjam_qt/controllers/room_clock_coordinator.py",
    "webjam_qt/windows/shared_canvas.py",
    "webjam_qt/windows/ai_image.py",
    "webjam_qt/windows/reference_video.py",
    "webjam_qt/widgets/room_clock_label.py",
    "webjam_qt/widgets/reference_video_player.py",
    "webjam_qt/widgets/status_chip.py",
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _source(name: str) -> str:
    return Path(name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Art keeps the handoff rather than replacing it
# ---------------------------------------------------------------------------


def test_art_still_hands_the_conversation_to_a_meeting_app():
    """Adding a canvas, a video, and AI must not turn into in-app faces."""

    art = get_creator_profile_by_key(ART)

    assert art.capabilities.meeting_handoff is True
    # Every profile keeps the same handoff; Art is not a special case.
    for profile in CREATOR_PROFILES:
        assert profile.capabilities.meeting_handoff is True, profile.key


def test_art_claims_no_camera_of_its_own():
    """No easel camera, no in-app faces. The meeting app owns video of people."""

    art = get_creator_profile_by_key(ART)
    help_text = art.quick_help.casefold()

    assert "no camera feed" in help_text
    assert MEETING_DIRECT_CAPTURE_BOUNDARY.casefold() in help_text


def test_a_non_webex_meeting_link_still_works_the_same_way():
    """Webex is primary in copy and controls, not the only accepted host."""

    for link in (
        "https://company.webex.com/meet/bandroom",
        "https://zoom.us/j/1234567890",
        "https://meet.google.com/abc-defg-hij",
        "https://teams.microsoft.com/l/meetup-join/x",
    ):
        assert is_allowed_meeting_link(link), link

    for refused in ("http://insecure.example/meet", "not a link", ""):
        assert not is_allowed_meeting_link(refused), refused


# ---------------------------------------------------------------------------
# Art's own paths never reach for the meeting app
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", ART_MODULES)
def test_no_art_module_touches_the_meeting_app(module: str):
    """Opening a canvas, a video, or Krita is not a meeting action.

    The only things that may focus or launch the meeting app are the existing
    Webex Controls and Show Webex App, which live in the conversation card.
    """

    source = _source(module).casefold()

    for forbidden in (
        "webex",
        "webex_app",
        "webex_url",
        "bring_forward",
        "open_meeting",
        "meeting_url",
    ):
        assert forbidden not in source, f"{module} mentions {forbidden}"


@pytest.mark.parametrize("module", ART_MODULES)
def test_no_art_module_imports_a_meeting_owner(module: str):
    """Checked structurally as well, so a rename cannot slip past the text."""

    tree = ast.parse(_source(module))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    for name in imported:
        lowered = name.casefold()
        assert "webex" not in lowered, f"{module} imports {name}"
        assert "meeting" not in lowered or lowered.endswith("meeting_link"), name


def test_art_never_embeds_a_meeting_or_adds_a_web_runtime():
    """No WebEngine, no OAuth, no Guest Issuer, no blind mute shortcut."""

    for module in ART_MODULES:
        source = _source(module).casefold()
        for forbidden in (
            "webengine",
            "qwebengine",
            "oauth",
            "guest issuer",
            "guestissuer",
            "access_token",
            "refresh_token",
            "sendkey",
            "keybd_event",
        ):
            assert forbidden not in source, f"{module} mentions {forbidden}"


def test_art_never_captures_the_meeting_or_system_output():
    """Screen sharing the reference video into a meeting is not the product."""

    for module in ART_MODULES:
        source = _source(module).casefold()
        for forbidden in (
            "screen_capture",
            "screencapture",
            "qscreencapture",
            "windowcapture",
            "getdesktopwindow",
            "loopback_capture",
        ):
            assert forbidden not in source, f"{module} mentions {forbidden}"


# ---------------------------------------------------------------------------
# The conversation keeps its sound
# ---------------------------------------------------------------------------


def test_a_reference_video_is_silent_so_it_cannot_talk_over_the_room():
    """The file is not routed anywhere, so every computer holds its own copy.

    An unmuted one would put a second soundtrack on top of the conversation on
    every machine at once, which is the one way a picture could take the
    meeting's audio.
    """

    from webjam_qt.controllers.reference_video_coordinator import (
        ReferenceVideoCoordinator,
    )

    class Player:
        def __init__(self) -> None:
            self.muted = False

        def set_muted(self, muted: bool) -> None:
            self.muted = bool(muted)

        def load(self, path):  # pragma: no cover - not exercised here
            return 1.0

        def play(self) -> None: ...

        def pause(self) -> None: ...

        def stop(self) -> None: ...

        def seek(self, position_s: float) -> None: ...

        def position_s(self) -> float:
            return 0.0

        def close(self) -> None: ...

    made: list[Player] = []

    def factory() -> Player:
        player = Player()
        made.append(player)
        return player

    coordinator = ReferenceVideoCoordinator(player_factory=factory)
    coordinator.begin_guest(session_id="room", session_key="token")
    player = coordinator._build_player()

    assert player.muted is True
    assert made and made[0].muted is True


def test_the_real_player_starts_muted(qapp):
    """The adapter is silent from the first frame, not from the first tick."""

    pytest.importorskip("PySide6.QtMultimedia")
    from core.reference_video import ReferenceVideoPlayerError

    try:
        from webjam_qt.widgets.reference_video_player import (
            create_qt_reference_video_player,
        )

        player = create_qt_reference_video_player()
    except (ReferenceVideoPlayerError, ImportError):
        pytest.skip("this machine has no video backend")

    try:
        assert player.muted is True
    finally:
        player.close()


def test_no_art_surface_selects_an_audio_device():
    """Jamulus owns the live route and the meeting app owns the voices."""

    for module in ART_MODULES:
        source = _source(module).casefold()
        for forbidden in (
            "setaudiodevice",
            "qaudiodevice",
            "audio_input_device_index",
            "jamulus_audio_input_uid",
            "jamulus_audio_output_uid",
            "sounddevice",
            "portaudio",
        ):
            assert forbidden not in source, f"{module} mentions {forbidden}"


# ---------------------------------------------------------------------------
# The meeting keeps its focus
# ---------------------------------------------------------------------------


def test_no_art_surface_raises_itself_outside_an_explicit_open():
    """A background update must never pull focus off the meeting window.

    Only the code path a person triggers by opening a panel may raise it, so
    the snapshot and tick handlers are checked for the calls that would.
    """

    controller = _source("webjam_qt/controllers/application_controller.py")
    tree = ast.parse(controller)
    background = {
        "_on_shared_canvas_host_snapshot",
        "_on_shared_canvas_follow_snapshot",
        "_on_ai_image_snapshot",
        "_on_room_clock_view",
        "_on_reference_video_host_snapshot",
        "_on_reference_video_follow_snapshot",
        "_tick_reference_video",
        "_tick_room_clock",
        "_tick_creator_start",
        "_announce_shared_canvas_follow_state",
        "_announce_reference_video_follow_state",
    }
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in background:
            continue
        seen.add(node.name)
        called = {
            child.func.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
        }
        for forbidden in ("raise_", "activateWindow", "setFocus", "showFullScreen"):
            assert forbidden not in called, f"{node.name} calls {forbidden}"

    # If a handler is renamed, this test must be updated rather than silently
    # covering nothing.
    assert seen == background, sorted(background - seen)


def test_every_art_panel_is_a_window_that_can_sit_beside_another():
    """Compact and non-modal, so a meeting window is never blocked."""

    from webjam_qt.windows.ai_image import AiImageDialog
    from webjam_qt.windows.reference_video import ReferenceVideoDialog
    from webjam_qt.windows.shared_canvas import SharedCanvasDialog

    panels = [
        AiImageDialog(),
        SharedCanvasDialog(hosting=True),
        SharedCanvasDialog(hosting=False),
        ReferenceVideoDialog(hosting=True),
        ReferenceVideoDialog(hosting=False),
    ]
    try:
        for panel in panels:
            # Modal would freeze the meeting window behind it.
            assert panel.isModal() is False, panel.windowTitle()
            # Narrow enough to leave room for a conversation beside it.
            assert panel.minimumWidth() <= 520, panel.windowTitle()
    finally:
        for panel in panels:
            panel.deleteLater()


def test_a_talk_only_room_leaves_no_empty_video_window_open():
    """Nothing is opened on this artist's behalf, so nothing is in the way."""

    from webjam_qt.windows.reference_video import ReferenceVideoDialog

    dialog = ReferenceVideoDialog(hosting=True)
    try:
        assert dialog.isVisible() is False
        assert dialog._surface_holder.isHidden() is True
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# Art never claims to be in the meeting
# ---------------------------------------------------------------------------


def test_no_art_copy_claims_webjam_joined_or_owns_the_meeting():
    from core.ai_image import (
        MANAGED_BACKEND_MESSAGE,
        NOT_IN_A_ROOM_MESSAGE,
        READY_MESSAGE,
        RESULTS_ARE_YOURS_MESSAGE,
    )
    from core.drawpile import INSTALL_DRAWPILE_MESSAGE, NOT_A_CANVAS_INVITE_MESSAGE
    from core.krita_ai import INSTALL_KRITA_MESSAGE, INSTALL_PLUGIN_MESSAGE
    from core.reference_video import (
        FILE_UNAVAILABLE_MESSAGE,
        HOST_ATTENTION_MESSAGE,
        MISMATCHED_FILE_MESSAGE,
        NEEDS_FILE_MESSAGE,
        NO_VIDEO_MESSAGE,
    )
    from core.room_clock import NO_CLOCK_DETAIL, STALE_DETAIL
    from core.shared_canvas import (
        CANVAS_READY_MESSAGE,
        CANVAS_UNREADABLE_MESSAGE,
        HOST_CANVAS_HINT,
        NEEDS_DRAWPILE_MESSAGE,
        NO_CANVAS_MESSAGE,
    )

    spoken = " ".join(
        (
            MANAGED_BACKEND_MESSAGE,
            NOT_IN_A_ROOM_MESSAGE,
            READY_MESSAGE,
            RESULTS_ARE_YOURS_MESSAGE,
            INSTALL_DRAWPILE_MESSAGE,
            NOT_A_CANVAS_INVITE_MESSAGE,
            INSTALL_KRITA_MESSAGE,
            INSTALL_PLUGIN_MESSAGE,
            FILE_UNAVAILABLE_MESSAGE,
            HOST_ATTENTION_MESSAGE,
            MISMATCHED_FILE_MESSAGE,
            NEEDS_FILE_MESSAGE,
            NO_VIDEO_MESSAGE,
            NO_CLOCK_DETAIL,
            STALE_DETAIL,
            CANVAS_READY_MESSAGE,
            CANVAS_UNREADABLE_MESSAGE,
            HOST_CANVAS_HINT,
            NEEDS_DRAWPILE_MESSAGE,
            NO_CANVAS_MESSAGE,
        )
    ).casefold()

    for claim in (
        "in the meeting",
        "joined the meeting",
        "webjam is in",
        "muted you",
        "your camera",
        "everyone can hear",
        "everyone can see",
    ):
        assert claim not in spoken, claim


def test_fail_closed_copy_is_a_recovery_rather_than_a_fault_report():
    from core.drawpile import INSTALL_DRAWPILE_MESSAGE
    from core.krita_ai import INSTALL_KRITA_MESSAGE, INSTALL_PLUGIN_MESSAGE

    for message in (
        INSTALL_DRAWPILE_MESSAGE,
        INSTALL_KRITA_MESSAGE,
        INSTALL_PLUGIN_MESSAGE,
    ):
        lowered = message.casefold()
        assert "install" in lowered
        for banned in ("error", "failed", "capability", "traceback", "exception"):
            assert banned not in lowered, message


def test_opening_art_surfaces_leaves_the_meeting_handoff_alone():
    """A canvas, an image, and a clock must not disturb the saved link.

    The conversation is configured once and owned elsewhere. Art running
    beside it may not rewrite the link, and may not reach for the app.
    """

    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from core.krita_ai import DEFAULT_BACKEND_URL
    from webjam_qt.controllers.application_controller import ApplicationController

    controller = ApplicationController.__new__(ApplicationController)
    controller._active_creator_profile_key = ART
    controller._shutdown = False
    controller._shutdown_in_progress = False
    controller._shutdown_cleanup_pending = False
    controller._shared_canvas = None
    controller._shared_canvas_dialog = None
    controller._shared_canvas_binding = ()
    controller._shared_canvas_notified_state = ""
    controller._ai_image = None
    controller._ai_image_dialog = None
    controller._room_clock = None
    controller._room_clock_binding = ()
    controller._reference_video = None
    controller.settings = SimpleNamespace(
        webex_url="https://company.webex.com/meet/bandroom",
        webex_audio_mode="talkback",
        drawpile_candidates=[],
        krita_candidates=[],
        krita_resource_dirs=[],
        comfyui_url=DEFAULT_BACKEND_URL,
    )
    controller.window = SimpleNamespace(flash_message=MagicMock())
    controller.host_peer = SimpleNamespace(
        active=True,
        credentials=SimpleNamespace(
            session_id="11111111-1111-4111-8111-111111111111",
            invite_token="invite-token-for-coexistence",
        ),
        publish_shared_canvas_state=MagicMock(),
        publish_room_clock_state=MagicMock(),
    )
    controller.guest_peer = None

    assert controller._shared_canvas_coordinator() is not None
    assert controller._ai_image_controller() is not None
    assert controller._room_clock_coordinator() is not None
    controller._tick_room_clock()

    # The link is exactly as it was, and no meeting control was reached for.
    assert controller.settings.webex_url == (
        "https://company.webex.com/meet/bandroom"
    )
    assert controller.settings.webex_audio_mode == "talkback"
    for forbidden in (
        "bring_forward_webex",
        "_show_webex_conversation",
        "_open_meeting",
    ):
        assert forbidden not in [
            call for call in dir(controller.host_peer)
        ], forbidden
