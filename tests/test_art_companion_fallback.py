"""Art without the companion, and Art beside it.

A companion panel is a convenience. The desktop is the product, so the whole
of Art has to work with nothing paired -- and these tests are written to fail
if any Art surface ever starts depending on one.

The second half covers the only behaviour a pairing is allowed to change:
where focus goes. With a panel showing this room inside a meeting window,
yanking the desktop in front of the faces someone is talking to would be the
exact focus-stealing ADR 0004 rules out. The panel still opens; it just does
not jump the queue.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.art_companion import (  # noqa: E402
    AiCompanionState,
    CanvasCompanionState,
    VideoCompanionState,
)
from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _controller(profile_key: str = "art") -> ApplicationController:
    controller = ApplicationController.__new__(ApplicationController)
    controller._active_creator_profile_key = profile_key
    controller._shutdown = False
    controller._shutdown_in_progress = False
    controller._shutdown_cleanup_pending = False
    controller._reference_video = None
    controller._reference_video_binding = ()
    controller._shared_canvas = None
    controller._shared_canvas_binding = ()
    controller._ai_image = None
    controller.host_peer = None
    controller.guest_peer = None
    controller.settings = AppSettings()
    controller.window = SimpleNamespace(flash_message=MagicMock())
    return controller


class FakePanel:
    """A panel that records whether it was shown and whether it took focus."""

    def __init__(self) -> None:
        self.shown = 0
        self.raised = 0
        self.activated = 0

    def show(self) -> None:
        self.shown += 1

    def raise_(self) -> None:
        self.raised += 1

    def activateWindow(self) -> None:  # noqa: N802 - Qt's spelling
        self.activated += 1


# ---------------------------------------------------------------------------
# Full fallback: nothing in Art may require a companion
# ---------------------------------------------------------------------------


#: Every Art surface. None of these may import the companion contract, let
#: alone require a pairing to work.
ART_SURFACES = (
    "core/drawpile.py",
    "core/shared_canvas.py",
    "core/ai_image.py",
    "core/krita_ai.py",
    "core/room_clock.py",
    "core/reference_video.py",
    "services/drawpile_service.py",
    "services/krita_ai_service.py",
    "webjam_qt/controllers/shared_canvas_coordinator.py",
    "webjam_qt/controllers/reference_video_coordinator.py",
    "webjam_qt/controllers/room_clock_coordinator.py",
    "webjam_qt/windows/shared_canvas.py",
    "webjam_qt/windows/ai_image.py",
    "webjam_qt/windows/reference_video.py",
    "webjam_qt/windows/launch_dialog.py",
)


@pytest.mark.parametrize("name", ART_SURFACES)
def test_no_art_surface_knows_the_companion_exists(name):
    """The dependency runs one way only.

    The projection reads Art's state; Art never reads the projection. That is
    what makes the no-companion path the only path these surfaces have.
    """

    tree = ast.parse(Path(name).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not any("art_companion" in module for module in imported)


#: The room's own chrome reads the projection on purpose -- it is the state
#: vocabulary both the room chip and a companion panel render from, so the two
#: cannot disagree. What it may never touch is the *command* side, which is
#: the part that carries a companion's authority.
PROJECTION_READERS = (
    "core/art_room_presence.py",
    "webjam_qt/widgets/art_room_chip.py",
    "webjam_qt/widgets/session_strip.py",
)

COMPANION_AUTHORITY = (
    "ArtCommand",
    "ArtCommandRequest",
    "ArtCommandReceipt",
    "ArtCommandStatus",
    "ArtScope",
    "ArtRejectionReason",
    "authorize_art_command",
)


@pytest.mark.parametrize("name", PROJECTION_READERS)
def test_the_room_reads_companion_state_but_never_companion_authority(name):
    """Sharing a vocabulary is not depending on a companion.

    The room describes itself with the same finite states a panel would read,
    which is why the two can never disagree. Importing a command, a scope, or
    the authorizer would be different: that is the half that decides what a
    remote panel is allowed to do, and the room has no business asking.
    """

    tree = ast.parse(Path(name).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.update(alias.name for alias in node.names)

    for authority in COMPANION_AUTHORITY:
        assert authority not in imported, (name, authority)


@pytest.mark.parametrize("name", PROJECTION_READERS)
def test_the_room_never_asks_whether_a_companion_is_paired(name):
    """If the chrome behaved differently when paired, the unpaired room would
    stop being the one that gets exercised.

    Identifiers are inspected rather than raw text, so prose explaining the
    relationship is allowed while a branch on it is not.
    """

    tree = ast.parse(Path(name).read_text(encoding="utf-8"))
    identifiers = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Name))
    }

    assert not any("paired" in identifier for identifier in identifiers)
    assert not any("companion" in identifier for identifier in identifiers)


def test_the_projection_is_a_read_and_never_a_requirement():
    """Asking for the projection outside a room is answered, not refused.

    A companion bridge that polls before anyone starts a session must not be
    able to knock the desktop over.
    """

    controller = _controller()

    projection = controller.art_companion_projection()

    assert projection.in_room is False
    assert projection.canvas is CanvasCompanionState.NONE
    assert projection.video is VideoCompanionState.NONE
    assert projection.ai is AiCompanionState.UNAVAILABLE


def test_a_shut_down_desktop_still_answers_with_an_empty_room():
    controller = _controller()
    controller._shutdown = True

    projection = controller.art_companion_projection()

    assert projection.in_room is False


@pytest.mark.parametrize(
    "profile_key", ["music", "podcast_voice", "review_rehearsal"]
)
def test_the_other_profiles_project_nothing(profile_key):
    """Only Art gained these surfaces, so only Art has them to project."""

    controller = _controller(profile_key)

    projection = controller.art_companion_projection()

    assert projection.in_room is False
    assert projection.transport_allowed is False


def test_nothing_is_paired_by_default():
    """The fallback is not a mode the user selects; it is the default."""

    assert _controller()._companion_paired() is False


# ---------------------------------------------------------------------------
# Focus: the one thing a pairing changes
# ---------------------------------------------------------------------------


def test_without_a_companion_an_art_panel_comes_to_the_front_as_always():
    controller = _controller()
    panel = FakePanel()

    controller._present_art_panel(panel)

    assert (panel.shown, panel.raised, panel.activated) == (1, 1, 1)


def test_with_a_companion_paired_an_art_panel_opens_without_taking_focus():
    """The meeting window keeps the foreground. The panel is still open, and
    the artist can click to it whenever they want."""

    controller = _controller()
    controller._art_companion_paired = True
    panel = FakePanel()

    controller._present_art_panel(panel)

    assert panel.shown == 1
    assert panel.raised == 0
    assert panel.activated == 0


def test_every_art_panel_goes_through_the_focus_aware_present():
    """Bypassing the helper would silently reintroduce focus stealing, so the
    call sites are pinned rather than trusted."""

    source = Path(
        "webjam_qt/controllers/application_controller.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    openers = {
        "_open_reference_video",
        "_open_shared_canvas",
        "_open_ai_image",
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in openers:
            continue
        calls = {
            child.func.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
        }
        assert "_present_art_panel" in calls, node.name
        assert "raise_" not in calls, node.name
        assert "activateWindow" not in calls, node.name
        openers.discard(node.name)

    assert not openers, f"never checked: {sorted(openers)}"


# ---------------------------------------------------------------------------
# Revision and generation discipline
# ---------------------------------------------------------------------------


def test_the_revision_holds_still_while_the_room_does():
    """A companion binds commands to a revision, so it has to mean "something
    changed" rather than "you asked again"."""

    controller = _controller()

    first = controller.art_companion_projection()
    second = controller.art_companion_projection()

    assert first.revision == second.revision
    assert first is second


def test_the_generation_moves_when_the_room_does():
    """An intent formed in one room must not be replayable into the next."""

    controller = _controller()
    before = controller.art_companion_projection().generation

    controller._art_companion_binding = ("host", "some-other-session")
    after = controller.art_companion_projection().generation

    assert after > before


# ---------------------------------------------------------------------------
# Through the real controller, in a real room
# ---------------------------------------------------------------------------


SESSION_ID = "8f14e45f-ceea-467a-9c05-0d9c8f14e45f"
INVITE_TOKEN = "invite-token-for-the-companion-projection"


def _in_a_room(controller: ApplicationController, *, hosting: bool) -> None:
    """Give the controller the real credentials its coordinators bind to."""

    if hosting:
        controller.host_peer = SimpleNamespace(
            active=True,
            credentials=SimpleNamespace(
                session_id=SESSION_ID, invite_token=INVITE_TOKEN
            ),
            publish_reference_video_state=MagicMock(),
            publish_shared_canvas_state=MagicMock(),
        )
        controller.guest_peer = None
        return
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = SimpleNamespace(
        last_state=None,
        invite=SimpleNamespace(
            peer_enabled=True,
            session_id=SESSION_ID,
            invite_token=INVITE_TOKEN,
        ),
    )


@pytest.fixture()
def no_real_programs(monkeypatch):
    """Neither Drawpile nor Krita is installed, which is the common case.

    Patched where the services bind the names rather than where they are
    defined, so the fixture really does take the programs away.
    """

    monkeypatch.setattr(
        "services.drawpile_service.find_drawpile", lambda *a, **k: None
    )
    monkeypatch.setattr("services.krita_ai_service.find_krita", lambda *a, **k: None)


def test_a_hosting_desktop_grants_transport_through_the_real_controller(
    no_real_programs,
):
    controller = _controller()
    _in_a_room(controller, hosting=True)

    projection = controller.art_companion_projection()

    assert projection.in_room is True
    assert projection.transport_allowed is True


def test_a_guest_desktop_is_refused_transport_through_the_real_controller(
    no_real_programs,
):
    """The security claim, checked where it actually gets decided: a guest's
    companion cannot be handed the host's transport by any code path."""

    controller = _controller()
    _in_a_room(controller, hosting=False)

    projection = controller.art_companion_projection()

    assert projection.in_room is True
    assert projection.transport_allowed is False


def test_a_real_room_with_nothing_shared_projects_a_talk_only_state(
    no_real_programs,
):
    """With no painting program and no generator installed, the projection
    says so plainly instead of offering a companion something to press."""

    controller = _controller()
    _in_a_room(controller, hosting=True)

    projection = controller.art_companion_projection()

    assert projection.canvas is CanvasCompanionState.NONE
    assert projection.video is VideoCompanionState.NONE
    assert projection.ai is AiCompanionState.UNAVAILABLE


def test_switching_from_host_to_guest_moves_the_generation(no_real_programs):
    controller = _controller()
    _in_a_room(controller, hosting=True)
    hosting = controller.art_companion_projection()

    _in_a_room(controller, hosting=False)
    guesting = controller.art_companion_projection()

    assert guesting.generation > hosting.generation
    assert hosting.transport_allowed is True
    assert guesting.transport_allowed is False
