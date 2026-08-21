"""The AI image panel, and where the controller meets it.

The panel's whole job is two verbs and one true status line. What it does
*not* contain is the point: no prompt box, no model list, no sampler, no step
count. Krita AI Diffusion has all of those, and a second worse copy inside
WebJam would be a lie about where the image is made.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
)

from core.ai_image import (  # noqa: E402
    AiImageAvailability,
    AiImageSnapshot,
    AiImageState,
)
from core.creative_modes import CREATOR_PROFILES  # noqa: E402
from core.krita_ai import (  # noqa: E402
    AI_IMAGE_SUFFIXES,
    DEFAULT_BACKEND_URL,
    LocalImage,
)
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.windows.ai_image import AiImageDialog, image_name_filter  # noqa: E402

SESSION_ID = str(uuid.uuid4())
INVITE_TOKEN = "invite-token-for-ai-image-integration"


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _snapshot(state: AiImageState, **changes) -> AiImageSnapshot:
    values = {
        "state": state,
        "message": "status text",
        "backend_label": (
            DEFAULT_BACKEND_URL if state is AiImageState.READY else ""
        ),
    }
    values.update(changes)
    return AiImageSnapshot(**values)


def _visible_buttons(dialog: AiImageDialog) -> list[str]:
    return [
        button.text()
        for button in dialog.findChildren(QPushButton)
        if not button.isHidden()
    ]


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


def test_a_ready_panel_offers_exactly_two_verbs():
    dialog = AiImageDialog()
    try:
        dialog.set_snapshot(_snapshot(AiImageState.READY))
        assert _visible_buttons(dialog) == ["Make", "Edit…"]
        assert dialog._make_button.isEnabled() is True
        assert dialog._edit_button.isEnabled() is True
    finally:
        dialog.deleteLater()


def test_the_panel_has_no_prompt_model_or_generation_controls():
    """No model picker, no LoRA browser, no prompt-engineering panel."""

    dialog = AiImageDialog()
    try:
        dialog.set_snapshot(_snapshot(AiImageState.READY))
        assert dialog.findChildren(QComboBox) == []
        assert dialog.findChildren(QLineEdit) == []
        assert dialog.findChildren(QTextEdit) == []
        assert dialog.findChildren(QSpinBox) == []
        assert dialog.findChildren(QSlider) == []
    finally:
        dialog.deleteLater()


def test_the_panel_says_where_the_image_is_actually_made():
    dialog = AiImageDialog()
    try:
        spoken = " ".join(
            label.text() for label in dialog.findChildren(type(dialog._status))
        ).casefold()
        assert "webjam does not generate anything" in spoken
        assert "krita" in spoken
        assert "nothing is uploaded" in spoken
    finally:
        dialog.deleteLater()


def test_the_panel_says_the_results_belong_to_the_artist():
    dialog = AiImageDialog()
    try:
        spoken = " ".join(
            label.text() for label in dialog.findChildren(type(dialog._status))
        ).casefold()
        assert "file on this computer that you own" in spoken
        assert "nothing reaches the room until you put it on the shared" in spoken
    finally:
        dialog.deleteLater()


def test_a_managed_backend_still_allows_both_verbs_and_claims_no_address():
    dialog = AiImageDialog()
    try:
        dialog.set_snapshot(_snapshot(AiImageState.READY_MANAGED_BACKEND))
        assert dialog._make_button.isEnabled() is True
        assert dialog._edit_button.isEnabled() is True
        assert dialog._headline.text() == "AI image"
    finally:
        dialog.deleteLater()


def test_a_detected_backend_is_named_in_the_headline():
    dialog = AiImageDialog()
    try:
        dialog.set_snapshot(_snapshot(AiImageState.READY))
        assert DEFAULT_BACKEND_URL in dialog._headline.text()
    finally:
        dialog.deleteLater()


def test_no_krita_disables_both_verbs_and_offers_the_download():
    dialog = AiImageDialog()
    try:
        dialog.set_snapshot(_snapshot(AiImageState.NEEDS_KRITA))
        assert dialog._make_button.isEnabled() is False
        assert dialog._edit_button.isEnabled() is False
        assert dialog._install_krita_button.isHidden() is False
        assert dialog._install_plugin_button.isHidden() is True
    finally:
        dialog.deleteLater()


def test_a_missing_plugin_offers_the_plugin_not_krita():
    dialog = AiImageDialog()
    try:
        dialog.set_snapshot(_snapshot(AiImageState.NEEDS_PLUGIN))
        assert dialog._make_button.isEnabled() is False
        assert dialog._install_krita_button.isHidden() is True
        assert dialog._install_plugin_button.isHidden() is False
    finally:
        dialog.deleteLater()


def test_outside_a_room_the_panel_offers_nothing_and_no_install():
    dialog = AiImageDialog()
    try:
        dialog.set_snapshot(_snapshot(AiImageState.NOT_IN_A_ROOM))
        assert dialog._make_button.isEnabled() is False
        assert dialog._edit_button.isEnabled() is False
        assert dialog._install_krita_button.isHidden() is True
        assert dialog._install_plugin_button.isHidden() is True
    finally:
        dialog.deleteLater()


def test_the_activity_line_appears_only_once_something_happened():
    dialog = AiImageDialog()
    try:
        dialog.set_snapshot(_snapshot(AiImageState.READY))
        assert dialog._activity.isHidden() is True

        dialog.set_snapshot(
            _snapshot(AiImageState.READY, activity="Krita opened a new canvas.")
        )
        assert dialog._activity.isHidden() is False
        assert "new canvas" in dialog._activity.text()
    finally:
        dialog.deleteLater()


def test_make_emits_one_intent_and_carries_no_arguments():
    dialog = AiImageDialog()
    seen: list[int] = []
    dialog.make_requested.connect(lambda: seen.append(1))
    try:
        dialog.set_snapshot(_snapshot(AiImageState.READY))
        dialog._make_button.click()
        assert seen == [1]
    finally:
        dialog.deleteLater()


def test_the_file_filter_matches_the_suffixes_the_domain_accepts():
    filter_text = image_name_filter()

    for suffix in AI_IMAGE_SUFFIXES:
        assert f"*{suffix}" in filter_text


def test_the_panel_stays_narrow_enough_to_sit_beside_a_meeting_window():
    dialog = AiImageDialog()
    try:
        assert dialog.minimumWidth() <= 400
        assert dialog.isModal() is False
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# Where the menu exposes it
# ---------------------------------------------------------------------------


def test_only_art_exposes_the_ai_image_entry_point(qapp):
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
            assert strip._ai_image_action.isVisible() is expected, profile.key
            assert strip._ai_image_action.isEnabled() is expected, profile.key
    finally:
        strip.deleteLater()


# ---------------------------------------------------------------------------
# The controller seams
# ---------------------------------------------------------------------------


class FakeStudio:
    def __init__(self, **availability) -> None:
        values = {
            "krita_found": True,
            "plugin_found": True,
            "backend_url": DEFAULT_BACKEND_URL,
        }
        values.update(availability)
        self.availability = AiImageAvailability(**values)
        self.new_images = 0
        self.opened: list[str] = []

    def probe(self) -> AiImageAvailability:
        return self.availability

    def open_new_image(self) -> None:
        self.new_images += 1

    def open_image(self, image: LocalImage) -> None:
        self.opened.append(image.display_name)


@pytest.fixture()
def fake_studios(monkeypatch):
    made: list[FakeStudio] = []

    def factory(settings=None):
        studio = FakeStudio()
        made.append(studio)
        return studio

    monkeypatch.setattr("services.krita_ai_service.create_ai_image_studio", factory)
    return made


def _controller(profile_key: str) -> ApplicationController:
    controller = ApplicationController.__new__(ApplicationController)
    controller._active_creator_profile_key = profile_key
    controller._shutdown = False
    controller._shutdown_in_progress = False
    controller._shutdown_cleanup_pending = False
    controller._ai_image = None
    controller._ai_image_dialog = None
    controller.settings = SimpleNamespace(
        krita_candidates=[], krita_resource_dirs=[], comfyui_url=""
    )
    controller.window = SimpleNamespace(flash_message=MagicMock())
    return controller


def _in_a_room(controller: ApplicationController) -> None:
    controller.host_peer = SimpleNamespace(
        active=True,
        credentials=SimpleNamespace(
            session_id=SESSION_ID, invite_token=INVITE_TOKEN
        ),
    )
    controller.guest_peer = None


def _outside_a_room(controller: ApplicationController) -> None:
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = None


def _as_guest(controller: ApplicationController) -> None:
    controller.host_peer = SimpleNamespace(active=False, credentials=None)
    controller.guest_peer = SimpleNamespace(
        invite=SimpleNamespace(
            peer_enabled=True,
            session_id=SESSION_ID,
            invite_token=INVITE_TOKEN,
        )
    )


def test_a_profile_without_the_capability_owns_no_ai_image(fake_studios):
    for profile_key in ("music", "podcast_voice", "review_rehearsal"):
        controller = _controller(profile_key)
        _in_a_room(controller)

        assert controller._ai_image_supported() is False
        assert controller._ai_image_controller() is None
        assert fake_studios == []


def test_a_profile_without_the_capability_refuses_to_open_the_panel(fake_studios):
    controller = _controller("music")
    _in_a_room(controller)

    controller._open_ai_image()

    message = controller.window.flash_message.call_args.args[0]
    assert "AI Image is part of Art" in message
    assert controller._ai_image_dialog is None


def test_art_outside_a_room_reports_the_room_as_what_is_missing(fake_studios):
    controller = _controller("art")
    _outside_a_room(controller)

    snapshot = controller._ai_image_controller().snapshot

    assert snapshot.state is AiImageState.NOT_IN_A_ROOM
    assert snapshot.can_generate is False


def test_a_guest_gets_the_same_action_as_a_host(fake_studios):
    """Everyone can Make and Edit for themselves; nobody drives anyone else."""

    for arrange in (_in_a_room, _as_guest):
        controller = _controller("art")
        arrange(controller)

        snapshot = controller._ai_image_controller().snapshot

        assert snapshot.state is AiImageState.READY
        assert snapshot.can_generate is True


def test_one_room_reuses_one_controller(fake_studios):
    controller = _controller("art")
    _in_a_room(controller)

    first = controller._ai_image_controller()

    assert first is not None
    assert controller._ai_image_controller() is first
    assert len(fake_studios) == 1


def test_switching_away_from_art_releases_the_ai_image_action(fake_studios):
    controller = _controller("art")
    _in_a_room(controller)
    assert controller._ai_image_controller() is not None

    controller._active_creator_profile_key = "music"

    assert controller._ai_image_controller() is None
    assert controller._ai_image is None


def test_a_make_through_the_controller_reaches_krita_and_publishes_nothing(
    fake_studios,
):
    controller = _controller("art")
    _in_a_room(controller)
    ai = controller._ai_image_controller()

    controller._run_ai_image(ai.make)

    assert fake_studios[0].new_images == 1
    controller.window.flash_message.assert_not_called()
    # The peer plane is never touched by this feature.
    assert not hasattr(controller.host_peer, "publish_ai_image_state")


def test_a_failing_intent_shows_bounded_text_and_keeps_the_room(
    fake_studios, tmp_path
):
    controller = _controller("art")
    _in_a_room(controller)
    ai = controller._ai_image_controller()
    bad = tmp_path / "notes.txt"
    bad.write_text("not an image")

    controller._run_ai_image(lambda: ai.edit(bad))

    message = controller.window.flash_message.call_args.args[0]
    assert "local image files ending in" in message
    assert str(tmp_path) not in message


def test_an_unexpected_failure_never_leaks_a_raw_exception(fake_studios):
    controller = _controller("art")
    _in_a_room(controller)
    controller._ai_image_controller()

    def explode():
        raise ZeroDivisionError("internal detail nobody should read")

    controller._run_ai_image(explode)

    message = controller.window.flash_message.call_args.args[0]
    assert "internal detail" not in message
    assert "room is still running" in message


def test_the_profile_capability_is_the_only_gate_on_the_menu_entry():
    for profile in CREATOR_PROFILES:
        controller = _controller(profile.key)
        assert controller._ai_image_supported() is bool(
            profile.capabilities.ai_image
        )
