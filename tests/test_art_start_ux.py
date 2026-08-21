"""Art's first minute: three cards, then Host or Join.

The point of this pass is that a person reads a short list once. These tests
therefore count what is actually on screen, not only what the registry says,
and they hold the other three profiles to the shape they already had.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from PySide6.QtWidgets import QApplication, QPushButton

from core.creative_modes import CREATOR_PROFILES, get_creator_profile_by_key
from core.settings import AppSettings, load_settings
from webjam_qt.windows.launch_dialog import (
    _CREATOR_LAUNCH_COPY,
    LaunchDialog,
    StartCard,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(config_file=str(tmp_path / "settings.json"))


def _dialog(tmp_path: Path, profile_key: str = "art") -> LaunchDialog:
    settings = _settings(tmp_path)
    settings.last_creator_profile_key = profile_key
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    return dialog


def _visible_cards(dialog: LaunchDialog) -> list[StartCard]:
    return [card for card in dialog._visible_start_cards() if not card.isHidden()]


def _visible_buttons(dialog: LaunchDialog) -> list[QPushButton]:
    return [
        button
        for button in dialog._choice_page.findChildren(QPushButton)
        if not button.isHidden()
    ]


# ---------------------------------------------------------------------------
# Three cards, and nothing more
# ---------------------------------------------------------------------------


def test_art_shows_exactly_three_start_cards(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        cards = _visible_cards(dialog)
        assert [card.start_key for card in cards] == [
            "talk_and_make",
            "paint_together",
            "paint_along",
        ]
    finally:
        dialog.deleteLater()


def test_art_adds_no_start_action_beyond_the_cards_and_host_join(
    qapp, tmp_path: Path
):
    """No fourth card, no tool picker, no preset forest.

    The whole visible button set on the choice page is counted, so an added
    control cannot slip in as "just one more".
    """

    dialog = _dialog(tmp_path)
    try:
        buttons = _visible_buttons(dialog)
        cards = [b for b in buttons if isinstance(b, StartCard)]
        others = [b for b in buttons if not isinstance(b, StartCard)]

        assert len(cards) == 3
        assert [button.text() for button in others] == ["Host", "Join"]
        # Art has no standalone Studio project, so that door stays shut
        # rather than being offered and then refused.
        assert dialog._studio_button.isHidden() is True
        assert dialog._studio_button.isEnabled() is False
    finally:
        dialog.deleteLater()


def test_each_card_says_what_it_does_in_one_short_line(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        for card in _visible_cards(dialog):
            start = get_creator_profile_by_key("art").get_start(card.start_key)
            assert card.accessibleName() == start.label
            assert start.summary in card.accessibleDescription()
            assert start.detail in card.accessibleDescription()
            assert len(start.summary) <= 72
    finally:
        dialog.deleteLater()


def test_a_card_is_a_large_target_that_never_submits_the_dialog(
    qapp, tmp_path: Path
):
    dialog = _dialog(tmp_path)
    try:
        for card in _visible_cards(dialog):
            assert card.minimumHeight() >= 56
            assert card.isCheckable() is True
            # Return belongs to Host. A card that kept autoDefault could take
            # the dialog's default action away from it.
            assert card.autoDefault() is False
            assert card.isDefault() is False
        assert dialog._host_button.isDefault() is True
    finally:
        dialog.deleteLater()


def test_exactly_one_card_is_chosen_at_all_times(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        cards = _visible_cards(dialog)
        assert sum(card.isChecked() for card in cards) == 1
        assert dialog.selected_start_key == "talk_and_make"

        cards[2].setChecked(True)
        assert sum(card.isChecked() for card in cards) == 1
        assert dialog.selected_start_key == "paint_along"
    finally:
        dialog.deleteLater()


def test_choosing_a_card_says_what_hosting_will_actually_do(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        cards = {card.start_key: card for card in _visible_cards(dialog)}

        cards["paint_together"].setChecked(True)
        helper = dialog._choice_helper.text().casefold()
        assert "paint together" in helper
        assert "drawpile" in helper
        host_described = dialog._host_button.accessibleDescription().casefold()
        assert "webjam does not paint the strokes" in host_described

        cards["paint_along"].setChecked(True)
        assert "paint along" in dialog._choice_helper.text().casefold()
        assert (
            "right to play" in dialog._host_button.accessibleDescription().casefold()
        )
    finally:
        dialog.deleteLater()


def test_talk_and_make_promises_neither_a_canvas_nor_a_video(qapp, tmp_path: Path):
    """Talk-only is first class, not a stripped-down version of the others."""

    dialog = _dialog(tmp_path)
    try:
        start = get_creator_profile_by_key("art").get_start("talk_and_make")
        assert start.talk_only is True

        cards = {card.start_key: card for card in _visible_cards(dialog)}
        cards["talk_and_make"].setChecked(True)
        described = cards["talk_and_make"].accessibleDescription().casefold()
        assert "nothing else" in described
        assert "no shared canvas and no video are needed" in described
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# The choice is remembered, and persisted safely
# ---------------------------------------------------------------------------


def test_hosting_persists_the_chosen_start(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        cards = {card.start_key: card for card in _visible_cards(dialog)}
        cards["paint_together"].setChecked(True)
        dialog._host()

        saved = load_settings(str(tmp_path / "settings.json"))
        assert dialog.selected_role == "host"
        assert saved.last_creator_profile_key == "art"
        assert saved.last_creator_start_key == "paint_together"
    finally:
        dialog.deleteLater()


def test_a_remembered_start_is_restored_without_looking_like_a_new_choice(
    qapp, tmp_path: Path
):
    settings = _settings(tmp_path)
    settings.last_creator_profile_key = "art"
    settings.last_creator_start_key = "paint_along"
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    try:
        assert dialog.selected_start_key == "paint_along"
    finally:
        dialog.deleteLater()


def test_a_start_from_another_profile_never_survives(qapp, tmp_path: Path):
    settings = _settings(tmp_path)
    settings.last_creator_profile_key = "art"
    settings.last_creator_start_key = "host_guest"
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    try:
        assert dialog.selected_start_key == "talk_and_make"
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# Joining re-picks nothing
# ---------------------------------------------------------------------------


def test_joining_asks_for_one_invitation_and_nothing_else(qapp, tmp_path: Path):
    """The invitation carries whatever the host started."""

    dialog = _dialog(tmp_path)
    try:
        dialog.show_join()
        assert dialog.showing_choices is False
        assert dialog._join_title.text() == "Join the room."
        assert "paste the webjam invitation" in dialog._join_subtitle.text().casefold()
        described = dialog._join_button_primary.accessibleDescription().casefold()
        assert "nothing else to choose here" in described

        # No start card is reachable from the join page.
        for cards in dialog._start_cards.values():
            for card in cards:
                assert card.isVisibleTo(dialog._join_page) is False
    finally:
        dialog.deleteLater()


# ---------------------------------------------------------------------------
# The other profiles are untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile_key", ["music", "podcast_voice", "review_rehearsal"])
def test_a_profile_without_starts_shows_no_cards(
    qapp, tmp_path: Path, profile_key: str
):
    dialog = _dialog(tmp_path, profile_key)
    try:
        assert dialog._start_container.isHidden() is True
        assert _visible_cards(dialog) == []
        assert dialog.selected_start_key == ""
        assert dialog.selected_start is None
    finally:
        dialog.deleteLater()


def test_switching_profiles_never_leaves_another_profiles_card_on_screen(
    qapp, tmp_path: Path
):
    dialog = _dialog(tmp_path, "music")
    try:
        selector = dialog._creator_profile_selector
        for key in ("art", "music", "art", "review_rehearsal", "art"):
            selector.setCurrentIndex(selector.findData(key))
            shown = [
                card
                for cards in dialog._start_cards.values()
                for card in cards
                if not card.isHidden()
            ]
            expected = get_creator_profile_by_key(key).starts
            assert len(shown) == len(expected)
            assert {card.start_key for card in shown} == {
                start.key for start in expected
            }
    finally:
        dialog.deleteLater()


def test_every_profile_still_has_launch_copy():
    assert set(_CREATOR_LAUNCH_COPY) == {profile.key for profile in CREATOR_PROFILES}


def test_no_launch_copy_still_says_studio_visit():
    for key, copy in _CREATOR_LAUNCH_COPY.items():
        spoken = " ".join(
            (
                copy.host,
                copy.join,
                copy.local,
                copy.host_description,
                copy.join_description,
                copy.local_description,
                copy.helper,
                copy.join_title,
                copy.join_subtitle,
            )
        ).casefold()
        assert "studio visit" not in spoken, key
