"""First-class feel pass: the words a person actually sees.

A painter, sculptor, 3D-print person, maker, and songwriter
must know what to click in ten seconds. These tests lock those words.
They do not lock engine names, Preview burial, or a chatbot home.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QPushButton

from core.creative_modes import get_creator_profile_by_key
from core.settings import AppSettings
from tests.support.start_ux import (
    assert_no_banned_first_screen_words,
    harvest_first_screen,
    harvest_join_page,
)
from webjam_qt.widgets.session_canvas import SessionCanvas
from webjam_qt.widgets.session_strip import (
    SessionStrip,
    shared_track_next_step_label,
    shared_track_play_is_locked,
    shared_track_status_label,
)
from webjam_qt.widgets.song_overlay import (
    PAGE_MEETING,
    PAGE_SONG,
    PAGE_STEMS,
    PAGE_TOOLS,
    SongOverlay,
)
from webjam_qt.windows.launch_dialog import LaunchDialog, ProfileCard, StartCard


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(config_file=str(tmp_path / "settings.json"))


def _dialog(tmp_path: Path, profile_key: str = "music") -> LaunchDialog:
    settings = _settings(tmp_path)
    settings.last_creator_profile_key = (
        profile_key if profile_key in {"art", "music"} else "music"
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    if profile_key not in {"art", "music"}:
        selector = dialog._creator_profile_selector
        selector.setCurrentIndex(selector.findData(profile_key))
    return dialog


def _visible_buttons(dialog: LaunchDialog) -> list[QPushButton]:
    return [
        button
        for button in dialog._choice_page.findChildren(QPushButton)
        if not button.isHidden()
    ]


def test_art_and_music_are_equal_first_clicks(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path, "music")
    try:
        cards = [
            button
            for button in _visible_buttons(dialog)
            if isinstance(button, ProfileCard)
        ]
        assert [(card.accessibleName(), card.description()) for card in cards] == [
            ("Art", "Make art together."),
            ("Music", "Play live together."),
        ]
        assert cards[0].isChecked() is False
        assert cards[1].isChecked() is True
        spoken = harvest_first_screen(dialog)
        assert "art" in spoken
        assert "music" in spoken
        assert "make art together." in spoken
        assert "play live together." in spoken
        assert "podcast & voice" not in spoken
        assert "review & rehearsal" not in spoken
        assert "art, podcast, or review" not in spoken
        assert "preview" not in spoken.split()
        assert_no_banned_first_screen_words(spoken)
    finally:
        dialog.deleteLater()


def test_a_leftover_podcast_visit_still_shows_art_as_hard_as_music(
    qapp, tmp_path: Path
):
    settings = _settings(tmp_path)
    settings.last_creator_profile_key = "podcast_voice"
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    try:
        cards = {
            card.profile_key: card
            for card in dialog._profile_cards.values()
            if not card.isHidden()
        }
        assert set(cards) == {"art", "music"}
        assert dialog.selected_creator_profile_key == "music"
        assert cards["music"].isChecked() is True
        spoken = harvest_first_screen(dialog)
        assert "podcast & voice" not in spoken
        assert "review & rehearsal" not in spoken
    finally:
        dialog.deleteLater()


def test_art_door_is_two_starts_then_host_join(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path, "art")
    try:
        starts = [
            button
            for button in _visible_buttons(dialog)
            if isinstance(button, StartCard)
        ]
        others = [
            button.text()
            for button in _visible_buttons(dialog)
            if not isinstance(button, (ProfileCard, StartCard))
        ]
        assert [
            (card.accessibleName(), card.description()) for card in starts
        ] == [
            (
                "Make together",
                "Talk, make, or draw together in one room.",
            ),
            (
                "Paint along",
                "Follow one silent process video while you paint.",
            ),
        ]
        assert others == ["Host", "Join"]
        assert dialog._more_rooms_button.isHidden() is True
        assert dialog._creator_profile_selector.isHidden() is True
        assert dialog._name_input.isHidden() is True
        assert dialog._choice_helper.isHidden() is True
        spoken = harvest_first_screen(dialog)
        assert "podcast & voice" not in spoken
        assert "review & rehearsal" not in spoken
        assert_no_banned_first_screen_words(spoken)
    finally:
        dialog.deleteLater()


def test_music_door_is_host_join_only(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path, "music")
    try:
        starts = [
            button
            for button in _visible_buttons(dialog)
            if isinstance(button, StartCard)
        ]
        roles = [
            button.text()
            for button in _visible_buttons(dialog)
            if button.objectName() in {"LaunchPrimary", "LaunchSecondary"}
        ]
        assert starts == []
        assert roles == ["Host", "Join"]
        assert dialog._studio_button.isHidden() is True
        assert dialog._more_rooms_button.text() == "Podcast or review"
        spoken = harvest_first_screen(dialog)
        assert "podcast & voice" not in spoken
        assert "review & rehearsal" not in spoken
        assert_no_banned_first_screen_words(spoken)
    finally:
        dialog.deleteLater()


def test_clicking_art_shows_the_two_starts(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path, "music")
    try:
        dialog._art_profile_card.click()
        assert dialog.selected_creator_profile_key == "art"
        starts = [
            card.start_key
            for card in dialog._visible_start_cards()
            if not card.isHidden()
        ]
        assert starts == ["talk_and_make", "paint_along"]
        assert dialog._more_rooms_button.isHidden() is True
    finally:
        dialog.deleteLater()


def test_only_paint_along_keeps_the_squirrel_face(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path, "art")
    try:
        cards = {card.start_key: card for card in dialog._visible_start_cards()}
        assert cards["paint_along"].icon().isNull() is False
        assert cards["paint_along"].iconSize() == QSize(40, 40)
        assert cards["talk_and_make"].icon().isNull() is True
        assert set(cards) == {"talk_and_make", "paint_along"}
        assert dialog._art_profile_card.icon().isNull() is True
        assert dialog._music_profile_card.icon().isNull() is True
    finally:
        dialog.deleteLater()


def test_join_is_paste_the_invite(qapp, tmp_path: Path):
    dialog = _dialog(tmp_path, "art")
    try:
        dialog.show_join()
        assert dialog._join_title.text() == "Join the room."
        assert "paste the invite" in dialog._join_subtitle.text().casefold()
        assert dialog._invite_input.placeholderText() == "Paste the invite"
        spoken = harvest_join_page(dialog)
        assert_no_banned_first_screen_words(spoken)
    finally:
        dialog.deleteLater()


def test_art_door_keeps_host_and_join_on_the_supported_window(
    qapp, tmp_path: Path
):
    """Three Art cards cannot push Host off a 760×600 screen."""

    from PySide6.QtCore import QPoint, QRect

    dialog = _dialog(tmp_path, "art")
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog.height() + 40 <= 600
        assert dialog._choice_helper.isHidden() is True
        for widget in (
            dialog._art_profile_card,
            dialog._music_profile_card,
            *dialog._visible_start_cards(),
            dialog._host_button,
            dialog._join_button,
        ):
            assert widget.isVisibleTo(dialog)
            mapped = QRect(widget.mapTo(dialog, QPoint(0, 0)), widget.size())
            assert dialog.rect().contains(mapped), widget.accessibleName()
            assert widget.height() >= 48
    finally:
        dialog.close()
        dialog.deleteLater()


def test_shared_track_paused_is_paused_not_needs_attention():
    snapshot = SimpleNamespace(
        state="paused",
        source_name="Taylor Swift - The Fate of Ophelia.mp3",
        cleanup_pending=False,
        count_in_active=False,
        error="",
    )
    assert shared_track_play_is_locked(snapshot) is False
    assert shared_track_status_label(snapshot) == "Paused"
    assert "Needs attention" not in shared_track_status_label(snapshot)


def test_shared_track_loaded_without_a_route_names_the_setup_step():
    snapshot = SimpleNamespace(
        state="ready",
        source_name="Taylor Swift - The Fate of Ophelia.mp3",
        cleanup_pending=False,
        count_in_active=False,
        error="",
        can_play=False,
        capability=SimpleNamespace(
            available=False,
            reason_code="physical_certification_required",
        ),
    )
    assert shared_track_play_is_locked(snapshot) is True
    assert shared_track_status_label(snapshot) == "Set up the audio device"
    assert shared_track_next_step_label(snapshot) == "Set up the audio device"
    assert shared_track_status_label(snapshot) != "Ready"
    assert shared_track_status_label(snapshot) != "Paused"
    assert "Needs attention" not in shared_track_status_label(snapshot)
    paused = SimpleNamespace(**{**snapshot.__dict__, "state": "paused"})
    assert shared_track_status_label(paused) == "Set up the audio device"


def test_shared_track_failed_names_the_next_step():
    isolated = SimpleNamespace(
        state="failed",
        source_name="Taylor Swift - The Fate of Ophelia.mp3",
        cleanup_pending=False,
        count_in_active=False,
        error="Play needs the isolated audio device set up first",
        capability=SimpleNamespace(reason_code="physical_certification_required"),
    )
    assert shared_track_status_label(isolated) == "Set up the audio device"
    guest = SimpleNamespace(
        state="failed",
        source_name="Band Song.wav",
        cleanup_pending=False,
        count_in_active=False,
        error="Shared Track needs host attention.",
    )
    assert shared_track_status_label(guest) == "Host needs to fix play"
    unnamed = SimpleNamespace(
        state="failed",
        source_name="Band Song.wav",
        cleanup_pending=False,
        count_in_active=False,
        error="",
    )
    assert shared_track_status_label(unnamed) == "Open Shared Track"
    for snapshot in (isolated, guest, unnamed):
        assert shared_track_status_label(snapshot) != "Needs attention"


def test_shared_track_strip_renders_the_named_next_step(qapp):
    strip = SessionStrip(
        mode_entries=[("music_jam", "Music Jam")],
        initial_mode_key="music_jam",
    )
    try:
        strip.resize(1100, 60)
        strip.show()
        qapp.processEvents()
        strip.set_reference_track_available(True)
        strip.set_shared_track_snapshot(
            SimpleNamespace(
                state="paused",
                source_name="Taylor Swift - The Fate of Ophelia.mp3",
                duration_s=90.0,
                position_s=3.0,
                loop_start_s=0.0,
                loop_end_s=None,
                count_in_active=False,
                cleanup_pending=False,
                error="",
                waveform_peaks=(),
                waveform_progress=0.0,
            )
        )
        assert strip._shared_track_state.text() == "Paused"
        strip.set_shared_track_snapshot(
            SimpleNamespace(
                state="failed",
                source_name="Taylor Swift - The Fate of Ophelia.mp3",
                duration_s=90.0,
                position_s=3.0,
                loop_start_s=0.0,
                loop_end_s=None,
                count_in_active=False,
                cleanup_pending=False,
                error="Play needs the isolated audio device set up first",
                capability=SimpleNamespace(
                    reason_code="physical_certification_required"
                ),
                waveform_peaks=(),
                waveform_progress=0.0,
            )
        )
        assert strip._shared_track_state.text() == "Set up the audio device"
        assert "Needs attention" not in strip._shared_track_state.text()
        opened: list[str] = []
        strip.tool_requested.connect(opened.append)
        strip.set_reference_track_available(True)
        strip.set_shared_track_snapshot(
            SimpleNamespace(
                state="ready",
                source_name="Taylor Swift - The Fate of Ophelia.mp3",
                duration_s=90.0,
                position_s=3.0,
                loop_start_s=0.0,
                loop_end_s=None,
                count_in_active=False,
                cleanup_pending=False,
                error="",
                can_play=False,
                capability=SimpleNamespace(
                    available=False,
                    reason_code="physical_certification_required",
                ),
                waveform_peaks=(),
                waveform_progress=0.0,
            )
        )
        assert strip._shared_track_state.text() == "Set up the audio device"
        assert strip._reference_track_button.text() == "Set up the audio device"
        assert strip._reference_track_button.isHidden() is False
        assert "Set up the a" != strip._reference_track_button.text()
        strip.show()
        qapp.processEvents()
        assert (
            strip._reference_track_button.width()
            >= strip._reference_track_button.sizeHint().width()
        )
        strip._reference_track_button.click()
        strip._shared_track_transport.click()
        assert opened == ["reference_track", "reference_track"]
    finally:
        strip.deleteLater()


def test_song_suggestion_sits_on_the_section(qapp):
    overlay = SongOverlay()
    try:
        assert overlay._suggestion_button.text() == "Suggestion"
        assert overlay._suggestion_button.isHidden() is False
        assert overlay._page_buttons[PAGE_TOOLS].isHidden() is True
        assert overlay._page_buttons[PAGE_MEETING].isHidden() is True
        assert overlay._page_buttons[PAGE_SONG].isHidden() is False
        assert overlay._page_buttons[PAGE_STEMS].isHidden() is False
        assert overlay._model_row.isHidden() is True
        worded = [
            button.text()
            for button in overlay._stack.widget(0).findChildren(QPushButton)
            if not button.isHidden() and len(button.text()) > 2
        ]
        assert worded == ["Suggestion", "Share sheet to chat"]
        overlay.show_page(PAGE_STEMS)
        assert overlay._split_button.text() == "Split a file you own"
        assert overlay._split_button.isHidden() is False
        seen_split: list[str] = []
        overlay.song_tool_requested.connect(seen_split.append)
        overlay._split_button.click()
        assert seen_split == ["stems"]
        overlay.show_page(PAGE_SONG)
        seen_write: list[bool] = []
        seen_chords: list[str] = []
        overlay.write_help_requested.connect(lambda: seen_write.append(True))
        overlay.chords_requested.connect(seen_chords.append)
        overlay._suggestion_button.click()
        assert seen_write == [True]
        overlay.set_sections(("Verse", "Chorus"))
        overlay._section_picker.setCurrentIndex(2)
        overlay._suggestion_button.click()
        assert seen_chords == ["Chorus"]
    finally:
        overlay.deleteLater()


def test_art_suggestion_sits_on_the_notes(qapp):
    canvas = SessionCanvas()
    strip = SessionStrip(
        mode_entries=[("visual_studio", "Art")],
        initial_mode_key="visual_studio",
    )
    try:
        music = get_creator_profile_by_key("music")
        art = get_creator_profile_by_key("art")
        canvas.set_creator_profile(music)
        assert canvas._suggestion_button.isHidden() is True
        canvas.set_creator_profile(art)
        assert canvas._suggestion_button.text() == "Suggestion"
        assert canvas._suggestion_button.isHidden() is False
        assert "live music path" not in canvas._guidance_why.text().casefold()
        strip.set_creator_profile(art)
        assert strip._ai_image_action.isVisible() is False
        assert canvas._suggestion_button.toolTip() == (
            "A suggestion for what you're making. Not a detected fact. "
            "Nothing is uploaded."
        )
        seen: list[bool] = []
        canvas.suggestion_requested.connect(lambda: seen.append(True))
        canvas._suggestion_button.click()
        assert seen == [True]
    finally:
        canvas.deleteLater()
        strip.deleteLater()
