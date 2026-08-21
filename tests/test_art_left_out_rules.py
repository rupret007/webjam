"""The rules that are easy to state and easy to lose.

Every promise here was already true when this file was written. That is the
point: each one is a property nobody would notice breaking until a person in
a real session got a wrong answer -- a control that looks like it mutes the
meeting, a closed panel that quietly ended a share, a tooltip pointing at a
button this platform cannot honour.

They are pinned in one place, checked against the real widgets and the real
launch arguments, and they cover both faces of Art: the desktop, and the
projection a paired companion panel may read.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication  # noqa: E402

from core.ai_image import (  # noqa: E402
    INSTALL_KRITA_MESSAGE,
    INSTALL_PLUGIN_MESSAGE,
    AiImageSnapshot,
    AiImageState,
)
from core.art_companion import (  # noqa: E402
    ArtCommand,
    ArtCompanionProjection,
    ArtScope,
    CanvasCompanionState,
    VideoCompanionState,
)
from core.drawpile import (  # noqa: E402
    CanvasInvite,
    drawpile_host_arguments,
    drawpile_join_arguments,
    parse_canvas_invite,
)
from core.krita_ai import (  # noqa: E402
    LocalImage,
    krita_edit_arguments,
    krita_make_arguments,
)
from core.reference_video import (  # noqa: E402
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
)
from core.shared_canvas import (  # noqa: E402
    CANVAS_UNREADABLE_MESSAGE,
    NEEDS_DRAWPILE_MESSAGE,
    NO_CANVAS_MESSAGE,
    SharedCanvasFollowSnapshot,
    SharedCanvasFollowState,
)
from webjam_qt.theme.qss_loader import load_stylesheet  # noqa: E402
from webjam_qt.windows.ai_image import AiImageDialog  # noqa: E402
from webjam_qt.windows.reference_video import ReferenceVideoDialog  # noqa: E402
from webjam_qt.windows.shared_canvas import SharedCanvasDialog  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp():
    """Style the shared application for this file, then put it back.

    These tests need WebJam's real stylesheet, because the light/dark and
    hit-size checks are about what the stylesheet actually does. Both the
    stylesheet and the palette are process-wide, though, so leaving either
    one installed would decide font metrics and layout for every file that
    runs afterwards -- the same order-dependence `test_first_run_setup`
    already had to undo.
    """

    app = QApplication.instance() or QApplication([])
    stylesheet = app.styleSheet()
    palette = QPalette(app.palette())
    app.setStyleSheet(load_stylesheet())
    try:
        yield app
    finally:
        app.setStyleSheet(stylesheet)
        app.setPalette(palette)


#: Every Art module. The audio and meeting rules are absence properties, so
#: they are checked across all of them rather than sampled.
ART_MODULES = (
    "core/drawpile.py",
    "core/shared_canvas.py",
    "core/ai_image.py",
    "core/krita_ai.py",
    "core/room_clock.py",
    "core/reference_video.py",
    "core/art_companion.py",
    "core/art_room_presence.py",
    "webjam_qt/widgets/art_room_chip.py",
    "services/drawpile_service.py",
    "services/krita_ai_service.py",
    "webjam_qt/controllers/shared_canvas_coordinator.py",
    "webjam_qt/controllers/reference_video_coordinator.py",
    "webjam_qt/controllers/room_clock_coordinator.py",
    "webjam_qt/controllers/art_companion_projection.py",
    "webjam_qt/windows/shared_canvas.py",
    "webjam_qt/windows/ai_image.py",
    "webjam_qt/windows/reference_video.py",
)

RECOVERY_MESSAGES = (
    INSTALL_KRITA_MESSAGE,
    INSTALL_PLUGIN_MESSAGE,
    NEEDS_DRAWPILE_MESSAGE,
    CANVAS_UNREADABLE_MESSAGE,
    NO_CANVAS_MESSAGE,
)


def _source(name: str) -> str:
    return Path(name).read_text(encoding="utf-8")


def _buttons(widget) -> list[QAbstractButton]:
    return list(widget.findChildren(QAbstractButton))


def _labels_of(widget) -> list[str]:
    return [button.text() for button in _buttons(widget)]


def _all_text(widget) -> str:
    """Every visible string a panel puts in front of a person."""

    parts: list[str] = []
    for button in _buttons(widget):
        parts.extend(
            (button.text(), button.toolTip(), button.accessibleName())
        )
    from PySide6.QtWidgets import QLabel

    parts.extend(label.text() for label in widget.findChildren(QLabel))
    return " ".join(part for part in parts if part)


def _guest_canvas() -> SharedCanvasDialog:
    dialog = SharedCanvasDialog(hosting=False)
    dialog.set_follow_snapshot(
        SharedCanvasFollowSnapshot(
            state=SharedCanvasFollowState.READY, can_open=True
        )
    )
    return dialog


def _guest_video() -> ReferenceVideoDialog:
    dialog = ReferenceVideoDialog(hosting=False)
    dialog.set_follow_snapshot(
        ReferenceVideoFollowSnapshot(
            state=ReferenceVideoFollowState.FOLLOWING,
            can_follow=True,
            should_play=True,
        )
    )
    return dialog


def _ai_panel() -> AiImageDialog:
    dialog = AiImageDialog()
    dialog.set_snapshot(
        AiImageSnapshot(state=AiImageState.READY, message="Ready.")
    )
    return dialog


ART_PANELS = (
    ("shared canvas", _guest_canvas),
    ("reference video", _guest_video),
    ("ai image", _ai_panel),
)


# ---------------------------------------------------------------------------
# Two mutes stay two mutes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "factory"), ART_PANELS)
def test_no_art_panel_offers_anything_that_looks_like_a_mute(name, factory):
    """WebJam's mute and the meeting's mute are different controls, and Art
    must not add a third thing a person could mistake for either."""

    panel = factory()

    for label in _labels_of(panel):
        assert "mute" not in label.lower(), (name, label)
        assert "unmute" not in label.lower(), (name, label)


@pytest.mark.parametrize(("name", "factory"), ART_PANELS)
def test_no_art_panel_claims_to_touch_the_meeting_or_a_microphone(name, factory):
    """The reference video is silent so it cannot talk over the room. That is
    a fact about the video, and must never read as control of the call."""

    text = _all_text(factory()).lower()

    for claim in ("your microphone", "mic ", "webex", "the meeting", "everyone hears"):
        assert claim not in text, (name, claim)


def test_the_videos_silence_is_explained_without_claiming_the_call():
    """It says where sound lives; it does not offer to move it."""

    from webjam_qt.windows.reference_video import _SYNC_HONESTY

    lowered = _SYNC_HONESTY.lower()

    assert "silent" in lowered
    assert "mute" not in lowered
    assert "webex" not in lowered


def test_the_companion_can_neither_see_nor_change_any_mute():
    """A panel inside the meeting window is the most tempting place to put a
    mute, so the contract has no field and no verb for one."""

    published = ArtCompanionProjection(
        generation=1, revision=1, in_room=True
    ).to_public_dict()

    assert not any("mute" in key for key in published)
    assert not any("audio" in key for key in published)
    for command in ArtCommand:
        assert "mute" not in command.value
        assert "audio" not in command.value


# ---------------------------------------------------------------------------
# End meeting != end jam; hide != leave; closing Drawpile != end session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "factory"), ART_PANELS)
def test_closing_an_art_panel_is_only_a_dismissal(name, factory):
    """No Art panel intercepts its own close.

    A panel that withdrew a share or left the room on close would make the
    window's X button destructive, which is the sort of thing nobody discovers
    until they lose a session by tidying their screen.
    """

    panel = factory()
    panel.close()

    tree = ast.parse(_source(f"webjam_qt/windows/{name.replace(' ', '_')}.py"))
    overrides = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"closeEvent", "hideEvent", "reject", "done"}
    }
    assert not overrides, (name, overrides)


@pytest.mark.parametrize(("name", "factory"), ART_PANELS)
def test_no_art_panel_offers_to_end_or_leave_anything(name, factory):
    """Ending the session lives on the session, not on a layer of it."""

    for label in _labels_of(factory()):
        lowered = label.lower()
        for verb in ("end session", "leave", "end jam", "disconnect", "quit"):
            assert verb not in lowered, (name, label)


def test_withdrawing_a_canvas_is_named_as_the_canvas_and_not_the_session():
    """"Stop sharing" is about the canvas. A guest closing Drawpile, or a host
    withdrawing one, is not the end of anything else."""

    from core.shared_canvas import SharedCanvasSnapshot, SharedCanvasState

    dialog = SharedCanvasDialog(hosting=True)
    dialog.set_host_snapshot(
        SharedCanvasSnapshot(
            state=SharedCanvasState.SHARED,
            shared=True,
            launcher_available=True,
            carries_password=True,
        )
    )

    labels = [label.lower() for label in _labels_of(dialog)]
    assert any("stop sharing" in label for label in labels)
    for label in labels:
        assert "session" not in label
        assert "leave" not in label


def test_hiding_the_video_leaves_the_guest_in_the_room_and_on_the_canvas():
    """Hide is a decision about one picture, not a way out."""

    from core.reference_video import ReferenceVideoFollower

    hidden = ReferenceVideoFollowSnapshot(
        state=ReferenceVideoFollowState.HIDDEN
    )

    assert hidden.state is ReferenceVideoFollowState.HIDDEN
    assert hidden.should_play is False
    # Hiding is reversible and carries no session meaning at all.
    assert hasattr(ReferenceVideoFollower, "set_hidden")


def test_the_companion_has_no_verb_for_ending_leaving_or_withdrawing():
    """The contract is closed, and closed deliberately around this.

    A remote panel that could end a session -- or withdraw a canvas everyone
    else is painting on -- would make a backgrounded iframe dangerous.
    """

    verbs = {command.value for command in ArtCommand}

    for forbidden in (
        "end_session",
        "leave_room",
        "withdraw_canvas",
        "stop_sharing",
        "quit",
        "close_canvas",
        "end_meeting",
    ):
        assert forbidden not in verbs


def test_hiding_the_video_from_a_companion_cannot_stop_the_room():
    """It is scoped to observation, so it can never carry host authority."""

    assert ArtCommand.HIDE_VIDEO.required_scope is ArtScope.OBSERVE
    assert ArtCommand.HIDE_VIDEO.drives_host_transport is False
    assert ArtCommand.HIDE_VIDEO.starts_another_program is False


# ---------------------------------------------------------------------------
# One invite; late join sees the canvas too
# ---------------------------------------------------------------------------


def test_the_companion_cannot_issue_or_re_pick_an_invite_or_a_start():
    """One WebJam invitation carries the Art start. A second door into the
    room, opened from a meeting panel, would be a second answer."""

    verbs = {command.value for command in ArtCommand}

    for forbidden in ("invite", "start", "host", "join", "share"):
        assert not any(forbidden in verb for verb in verbs), forbidden


def test_a_late_joining_companion_is_shown_the_canvas_that_already_exists():
    """The projection reads the same follow snapshot the desktop does, so a
    panel opened late shows the canvas rather than an empty room."""

    from webjam_qt.controllers.art_companion_projection import (
        build_art_companion_projection,
    )

    projection = build_art_companion_projection(
        generation=1,
        revision=0,
        in_room=True,
        hosting=False,
        canvas_snapshot=SharedCanvasFollowSnapshot(
            state=SharedCanvasFollowState.READY, can_open=True
        ),
        video_snapshot=ReferenceVideoFollowSnapshot(
            state=ReferenceVideoFollowState.FOLLOWING,
            can_follow=True,
            should_play=True,
        ),
    )

    assert projection.canvas is CanvasCompanionState.READY
    assert projection.video is VideoCompanionState.PLAYING


# ---------------------------------------------------------------------------
# Dual audio: opening a painting program takes nobody's microphone
# ---------------------------------------------------------------------------


AUDIO_TOKENS = (
    "audio",
    "sound",
    "mic",
    "microphone",
    "device",
    "alsa",
    "pulse",
    "jack",
    "asio",
    "wasapi",
    "coreaudio",
    "input",
    "output",
)


def test_opening_drawpile_asks_for_no_audio_device():
    """Drawpile is a painting program. If WebJam ever passed it an audio
    argument, the live conversation would be the thing that broke."""

    invite = parse_canvas_invite("drawpile://paint.example.org/studio?p=secret")
    vectors = (
        drawpile_host_arguments("/usr/bin/drawpile"),
        drawpile_join_arguments("/usr/bin/drawpile", invite),
    )

    for vector in vectors:
        # The join URL is the artist's own text and is not WebJam's claim, so
        # only the flags WebJam chooses are examined.
        flags = [item for item in vector[1:] if item.startswith("-")]
        for token in AUDIO_TOKENS:
            assert not any(token in flag.lower() for flag in flags), (vector, token)


def test_opening_krita_asks_for_no_audio_device(tmp_path):
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    vectors = (
        krita_make_arguments("/usr/bin/krita"),
        krita_edit_arguments(
            "/usr/bin/krita",
            LocalImage(path=image, display_name="photo.png", byte_size=72),
        ),
    )

    for vector in vectors:
        flags = [item for item in vector[1:] if item.startswith("-")]
        for token in AUDIO_TOKENS:
            assert not any(token in flag.lower() for flag in flags), (vector, token)


@pytest.mark.parametrize("name", ART_MODULES)
def test_no_art_module_reaches_for_an_audio_device_or_the_mixer(name):
    """Art adds pictures to a room whose sound is already owned.

    The live audio path and the meeting app own every device between them, so
    an Art surface that opened one would be taking it from a person mid
    sentence.
    """

    tree = ast.parse(_source(name))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    for forbidden in (
        "sounddevice",
        "pyaudio",
        "jamulus_controller",
        "core.jamulus_protocol",
        "core.jamulus_rpc_client",
        "services.audio",
    ):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for module in imported
        ), (name, forbidden)


# ---------------------------------------------------------------------------
# A hosted canvas is personal and passworded, never listed
# ---------------------------------------------------------------------------


def test_hosting_a_canvas_never_requests_a_listing_or_an_adult_flag():
    """Drawpile can announce a session publicly and mark it not-for-minors.
    WebJam sets neither, and lands the artist on the page where they choose.
    """

    arguments = drawpile_host_arguments("/usr/bin/drawpile")

    joined = " ".join(arguments).lower()
    for forbidden in ("nsfm", "announce", "listserver", "public", "--host "):
        assert forbidden not in joined
    assert arguments[1:] == ["--start-page", "host"]


def test_the_host_is_pointed_at_a_personal_password_protected_session():
    """The one recommendation WebJam makes about somebody else's dialog."""

    from core.shared_canvas import SharedCanvasSnapshot

    dialog = SharedCanvasDialog(hosting=True)
    dialog.set_host_snapshot(SharedCanvasSnapshot(launcher_available=True))

    assert "personal" in _all_text(dialog).lower()


def test_an_invitation_without_a_password_is_flagged_rather_than_accepted_quietly():
    """A canvas anyone with the link can paint on is a different thing from a
    personal one, so the panel says which arrived."""

    open_canvas = parse_canvas_invite("drawpile://paint.example.org/studio")
    personal = parse_canvas_invite("drawpile://paint.example.org/studio?p=secret")

    assert open_canvas.carries_password is False
    assert personal.carries_password is True


def test_the_companion_never_learns_the_canvas_address_or_its_password():
    """Which is the whole reason the projection carries a state and not a
    link: a session password can ride inside a Drawpile invitation."""

    invite = parse_canvas_invite("drawpile://paint.example.org/studio?p=secret")
    published = ArtCompanionProjection(
        generation=1,
        revision=1,
        in_room=True,
        canvas=CanvasCompanionState.READY,
    ).to_public_dict()

    rendered = " ".join(str(value) for value in published.values())
    assert invite.join_url not in rendered
    assert "secret" not in rendered
    assert "paint.example.org" not in rendered
    assert isinstance(invite, CanvasInvite)


# ---------------------------------------------------------------------------
# Fail-closed copy is a recovery, on one line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", RECOVERY_MESSAGES)
def test_every_fail_closed_message_is_one_line_and_bounded(message):
    assert "\n" not in message
    assert len(message) <= 160
    sentences = [
        part for part in re.split(r"(?<=[.!?])\s+", message.strip()) if part
    ]
    assert len(sentences) <= 2, sentences


@pytest.mark.parametrize("message", RECOVERY_MESSAGES)
def test_no_fail_closed_message_reads_like_a_crash(message):
    lowered = message.lower()

    for jargon in (
        "error",
        "failed",
        "exception",
        "traceback",
        "capability",
        "unsupported",
        "null",
        "none type",
    ):
        assert jargon not in lowered, jargon


@pytest.mark.parametrize("message", RECOVERY_MESSAGES)
def test_no_fail_closed_message_leaks_a_path(message):
    assert "/" not in message.replace("drawpile.net", "")
    assert "\\" not in message
    assert "C:" not in message


def test_the_companion_carries_no_message_field_to_leak_a_stack_trace():
    """Recovery copy is the desktop's job. A projection with a free-text
    field would eventually carry whatever an exception said."""

    published = ArtCompanionProjection(
        generation=1, revision=1, in_room=True
    ).to_public_dict()

    assert not any(
        key in published for key in ("message", "error", "detail", "reason")
    )


# ---------------------------------------------------------------------------
# One primary action, large hits, and one theme applied consistently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "factory"), ART_PANELS)
def test_each_art_panel_offers_at_most_one_primary_action(name, factory):
    """ADR 0002: a surface explains the next action rather than competing
    with itself."""

    panel = factory()

    primaries = [
        button
        for button in _buttons(panel)
        if button.objectName() == "StatusChip"
        and not button.isHidden()
        and button.property("tone") != "recovery"
    ]
    assert len(primaries) <= 1, (name, [b.text() for b in primaries])


@pytest.mark.parametrize(
    ("name", "factory", "expected"),
    (
        ("shared canvas", _guest_canvas, "Open shared canvas"),
        ("ai image", _ai_panel, "Make an image"),
    ),
)
def test_a_ready_panel_offers_exactly_one_primary_action(name, factory, expected):
    """"At most one" would also be satisfied by offering nothing, so the
    ready state is pinned to the single verb it should show."""

    panel = factory()
    primaries = [
        button.text()
        for button in _buttons(panel)
        if button.objectName() == "StatusChip"
        and not button.isHidden()
        and button.property("tone") != "recovery"
    ]

    assert primaries == [expected]


@pytest.mark.parametrize(("name", "factory"), ART_PANELS)
def test_every_offered_control_is_large_enough_to_hit(name, factory):
    """Quiet is about weight, not size. A secondary action should be easy to
    press and hard to confuse with the primary one -- different problems."""

    panel = factory()
    panel.resize(520, 360)
    panel.show()
    QApplication.instance().processEvents()

    for button in _buttons(panel):
        if button.isHidden() or not button.isEnabled():
            continue
        assert button.height() >= 30, (name, button.text(), button.height())


def test_the_quiet_action_keeps_a_full_size_target():
    from webjam_qt.widgets.status_chip import QuietAction

    action = QuietAction("Edit an image you have…")
    action.show()
    QApplication.instance().processEvents()

    assert action.sizeHint().height() >= 44


def _palette(foreground: str, background: str) -> QPalette:
    palette = QPalette()
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(role, QColor(foreground))
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.Button,
    ):
        palette.setColor(role, QColor(background))
    return palette


def _render(factory, palette) -> QImage:
    app = QApplication.instance()
    previous = app.palette()
    try:
        app.setPalette(palette)
        panel = factory()
        panel.resize(520, 340)
        panel.show()
        app.processEvents()
        image = panel.grab().toImage().convertToFormat(
            QImage.Format.Format_RGB32
        )
        panel.close()
        panel.deleteLater()
        app.processEvents()
        return image
    finally:
        app.setPalette(previous)


@pytest.mark.parametrize(("name", "factory"), ART_PANELS)
def test_an_art_panel_renders_the_same_under_a_light_or_dark_os_theme(
    name, factory
):
    """WebJam is one dark theme rather than a follower of the OS.

    That is only honest if it is *complete*. A widget whose background comes
    from the stylesheet but whose text colour falls through to the system
    palette would be unreadable on exactly one of these two machines, and
    whoever wrote it would never see the broken one.
    """

    light = _render(factory, _palette("#000000", "#ffffff"))
    dark = _render(factory, _palette("#ffffff", "#1e1e1e"))

    assert light == dark, name


# ---------------------------------------------------------------------------
# Windows and Linux are not told to use focus they cannot get
# ---------------------------------------------------------------------------


def test_the_meeting_tooltip_only_points_at_show_webex_app_where_it_works():
    """ADR 0004 keeps native focus disabled on Windows and Linux, because
    their detection does not establish publisher proof.

    The button is disabled there, which was already right. Advice is a claim
    too, so it goes away with the capability instead of outliving it.
    """

    from webjam_qt.widgets.webex_embed import WebexEmbed

    card = WebexEmbed()
    card.set_meeting_configured(True)

    # A platform that cannot prove the publisher: found, unverified.
    card.set_app_status("installed", publisher_verified=False)
    unproven = card.fallback_button().toolTip()

    # macOS with a verified Cisco bundle.
    card.set_app_status("installed", publisher_verified=True)
    proven = card.fallback_button().toolTip()

    assert "Show Webex App" not in unproven
    assert "Show Webex App" in proven
    assert unproven.count("\n") == 0


def test_the_show_webex_app_button_stays_disabled_without_publisher_proof():
    from webjam_qt.widgets.webex_embed import WebexEmbed

    card = WebexEmbed()
    card.set_app_status("installed", publisher_verified=False)

    assert card.show_app_button().isEnabled() is False


def test_no_art_surface_mentions_showing_the_meeting_app_at_all():
    """Art is beside the meeting window. Whether that window can be focused
    is not Art's claim to make in either direction."""

    for name in ART_MODULES:
        assert "Show Webex App" not in _source(name), name
