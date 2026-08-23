"""Art's first minute: three cards, then Host or Join.

The point of this pass is that a person reads a short list once. These tests
therefore count what is actually on screen, not only what the registry says,
and they hold the other three profiles to the shape they already had.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QWidget

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


def _first_screen_spoken(dialog: LaunchDialog) -> str:
    """Harvest what a person can actually read or hear on the choice page.

    Hidden fail-closed recovery (the Windows Jamulus installer) stays out.
    Picker labels are included even while Music hides the combo, because those
    words become the first screen the moment Art, podcast, or review is
    revealed.
    """

    page = dialog._choice_page
    parts: list[str] = []
    selector = dialog._creator_profile_selector
    parts.extend(selector.itemText(index) for index in range(selector.count()))
    parts.append(selector.accessibleDescription())
    for widget in page.findChildren(QWidget):
        if not widget.isVisibleTo(page):
            continue
        for attr in (
            "text",
            "description",
            "placeholderText",
            "accessibleName",
            "accessibleDescription",
            "toolTip",
        ):
            getter = getattr(widget, attr, None)
            if callable(getter):
                value = getter()
                if value:
                    parts.append(str(value))
        if isinstance(widget, QComboBox):
            parts.extend(
                widget.itemText(index) for index in range(widget.count())
            )
    # Cards chosen for this profile are first-screen copy even if a test
    # inspects them before Qt finishes showing the page.
    parts.extend(card.text() for card in _visible_cards(dialog))
    parts.extend(card.description() for card in _visible_cards(dialog))
    parts.extend(card.accessibleDescription() for card in _visible_cards(dialog))
    parts.extend(card.toolTip() for card in _visible_cards(dialog))
    return " ".join(parts).casefold()


def _assert_first_screen_has_no_banned_words(spoken: str) -> None:
    for component in (
        "drawpile",
        "krita",
        "jamulus",
        "webex",
        "moises",
        "music ai",
        "byok",
        "comfyui",
        "host-clocked",
        "studio visit",
        "stems",
    ):
        assert component not in spoken, component
    for word in ("preview", "ready", "api"):
        assert not re.search(rf"\b{word}\b", spoken), word


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
        # Art still needs a way onto this door; the profile picker stays.
        assert dialog._creator_profile_label.isHidden() is False
        assert dialog._creator_profile_selector.isHidden() is False
    finally:
        dialog.deleteLater()



def test_only_paint_along_shows_the_face_mark(qapp, tmp_path: Path):
    """Jeff locked the squirrel-face mark on Paint along only."""

    dialog = _dialog(tmp_path)
    try:
        cards = {card.start_key: card for card in _visible_cards(dialog)}
        assert cards["paint_along"].icon().isNull() is False
        assert cards["talk_and_make"].icon().isNull() is True
        assert cards["paint_together"].icon().isNull() is True
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
            assert card.minimumHeight() >= 48
            # Bounded above too, so three cards plus Host and Join all stay
            # on screen at the supported window floor.
            assert card.maximumHeight() <= 72
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


def test_choosing_a_card_binds_host_to_that_card(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path)
    try:
        cards = {card.start_key: card for card in _visible_cards(dialog)}

        cards["paint_together"].setChecked(True)
        host_described = dialog._host_button.accessibleDescription().casefold()
        assert "start paint together as the host" in host_described
        assert "canvas everyone can draw on" in host_described

        cards["paint_along"].setChecked(True)
        host_described = dialog._host_button.accessibleDescription().casefold()
        assert "start paint along as the host" in host_described
        assert "own copy of the same file" in host_described
    finally:
        dialog.deleteLater()


def test_the_first_screen_names_no_component(qapp, tmp_path: Path):
    """The ten-second door is about what you are making, not what runs it.

    The painting program, the audio path, and the image generator all
    introduce themselves in the room, at the moment they matter, and only if
    something is missing.
    """

    dialog = _dialog(tmp_path)
    try:
        _assert_first_screen_has_no_banned_words(_first_screen_spoken(dialog))
    finally:
        dialog.deleteLater()


def test_the_name_field_asks_for_a_name(qapp, tmp_path: Path):
    """It is a person's name, not a component's field, and validation stays."""

    dialog = _dialog(tmp_path)
    try:
        label = next(
            widget
            for widget in dialog.findChildren(type(dialog._choice_helper))
            if widget.objectName() == "LaunchNameLabel"
        )
        assert label.text() == "Your name"
        assert dialog._name_input.accessibleName() == "Your name"
        spoken_name = " ".join(
            (
                dialog._name_preview.text(),
                dialog._name_preview.accessibleName(),
                dialog._name_preview.accessibleDescription(),
                dialog._name_input.accessibleDescription(),
            )
        ).casefold()
        assert "jamulus" not in spoken_name

        # The same validation still refuses a name the mixer cannot show.
        dialog._name_input.setText("")
        assert dialog._validated_musician_name() is None
        assert dialog._name_error.isHidden() is False
    finally:
        dialog.deleteLater()


def test_the_page_never_says_the_same_thing_twice(qapp, tmp_path: Path):
    """A card already says what it does; repeating it below is noise.

    On a Mac, where hosting works, the helper line has nothing left to add and
    stays empty. Elsewhere it carries the one thing a card cannot: that
    hosting is unavailable here at all.
    """

    dialog = _dialog(tmp_path)
    try:
        assert dialog._choice_helper.text() == ""
        # The three cards carry the instruction, so the headline and the
        # subtitle above them step aside rather than crowd them.
        assert dialog._choice_title.isVisibleTo(dialog._choice_page) is False
        assert dialog._choice_subtitle.isVisibleTo(dialog._choice_page) is False
    finally:
        dialog.deleteLater()

    settings = _settings(tmp_path)
    settings.last_creator_profile_key = "art"
    with patch.object(sys, "platform", "win32"):
        elsewhere = LaunchDialog(settings)
    try:
        assert "macOS app" in elsewhere._choice_helper.text()
    finally:
        elsewhere.deleteLater()


def test_a_profile_without_cards_keeps_its_headline_and_helper(
    qapp, tmp_path: Path
):
    dialog = _dialog(tmp_path, "music")
    try:
        assert dialog._choice_title.isVisibleTo(dialog._choice_page) is True
        assert dialog._choice_subtitle.isVisibleTo(dialog._choice_page) is False
        assert dialog._choice_helper.text() == "Play live together."
        assert dialog._creator_profile_label.isVisibleTo(dialog._choice_page) is False
        assert dialog._creator_profile_selector.isVisibleTo(dialog._choice_page) is False
        assert dialog._more_rooms_button.isVisibleTo(dialog._choice_page) is True
    finally:
        dialog.deleteLater()


def test_music_door_keeps_host_join_and_offers_a_quiet_path_to_art(
    qapp, tmp_path: Path
):
    """Default Music is Host / Join. Art is one quiet line away."""

    dialog = _dialog(tmp_path, "music")
    try:
        role_buttons = [
            button
            for button in _visible_buttons(dialog)
            if button.objectName() in {"LaunchPrimary", "LaunchSecondary"}
        ]
        assert [button.text() for button in role_buttons] == ["Host", "Join"]
        assert dialog._studio_button.isHidden() is True
        assert dialog._more_rooms_button.isVisibleTo(dialog._choice_page) is True
        assert dialog._more_rooms_button.autoDefault() is False
        assert dialog._more_rooms_button.isDefault() is False
        assert dialog._host_button.isDefault() is True
        _assert_first_screen_has_no_banned_words(_first_screen_spoken(dialog))

        dialog._more_rooms_button.click()
        assert dialog._rooms_picker_revealed is True
        assert dialog._more_rooms_button.isVisibleTo(dialog._choice_page) is False
        assert dialog._creator_profile_label.isVisibleTo(dialog._choice_page) is True
        assert dialog._creator_profile_selector.isVisibleTo(dialog._choice_page) is True
        assert [
            dialog._creator_profile_selector.itemText(index)
            for index in range(dialog._creator_profile_selector.count())
        ] == [profile.label for profile in CREATOR_PROFILES]
        assert [button.text() for button in role_buttons] == ["Host", "Join"]

        selector = dialog._creator_profile_selector
        selector.setCurrentIndex(selector.findData("art"))
        assert dialog.selected_creator_profile_key == "art"
        assert [card.start_key for card in _visible_cards(dialog)] == [
            "talk_and_make",
            "paint_together",
            "paint_along",
        ]
        assert dialog._more_rooms_button.isVisibleTo(dialog._choice_page) is False
        _assert_first_screen_has_no_banned_words(_first_screen_spoken(dialog))

        selector.setCurrentIndex(selector.findData("music"))
        assert dialog._rooms_picker_revealed is False
        assert dialog._more_rooms_button.isVisibleTo(dialog._choice_page) is True
        assert dialog._creator_profile_selector.isVisibleTo(dialog._choice_page) is False
        assert _visible_cards(dialog) == []
    finally:
        dialog.deleteLater()


def test_review_door_is_host_join_not_a_caveat_wall(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path, "review_rehearsal")
    try:
        assert dialog._host_button.text() == "Host Review"
        assert dialog._join_button.text() == "Join Review"
        assert dialog._choice_helper.text() == "Host or join a review."
        _assert_first_screen_has_no_banned_words(_first_screen_spoken(dialog))
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
        assert "nothing to set up" in described
        assert "nothing shared but the conversation" in described
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
        assert "paste the invite" in dialog._join_subtitle.text().casefold()
        described = dialog._join_button_primary.accessibleDescription().casefold()
        assert "nothing else to pick" in described

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
        hide_profile = profile_key == "music"
        assert dialog._creator_profile_label.isHidden() is hide_profile
        assert dialog._creator_profile_selector.isHidden() is hide_profile
        assert dialog._more_rooms_button.isHidden() is not hide_profile
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
        assert not re.search(r"\bpreview\b", spoken), key
        _assert_first_screen_has_no_banned_words(spoken)


@pytest.mark.parametrize(
    "profile_key", ["music", "podcast_voice", "review_rehearsal", "art"]
)
def test_every_first_screen_has_no_banned_words(
    qapp, tmp_path: Path, profile_key: str
):
    """A painter, sculptor, songwriter, and talk-only person share one door law."""

    dialog = _dialog(tmp_path, profile_key)
    try:
        _assert_first_screen_has_no_banned_words(_first_screen_spoken(dialog))
    finally:
        dialog.deleteLater()


def test_art_cards_still_pass_the_ten_second_read(qapp, tmp_path: Path):
    """Exactly these words. A person should know what to click immediately."""

    dialog = _dialog(tmp_path)
    try:
        cards = _visible_cards(dialog)
        assert [
            (card.accessibleName(), card.description()) for card in cards
        ] == [
            (
                "Talk & make",
                "Just the room and your voices. Make whatever you're making.",
            ),
            ("Paint together", "The room, plus one canvas you all draw on."),
            (
                "Paint along",
                "The room, plus one video you all watch in step.",
            ),
        ]
        others = [
            button.text()
            for button in _visible_buttons(dialog)
            if not isinstance(button, StartCard)
        ]
        assert others == ["Host", "Join"]
    finally:
        dialog.deleteLater()
