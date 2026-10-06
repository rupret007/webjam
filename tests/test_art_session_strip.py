"""Art live chrome must not advertise Record, Shared Track, or Review copy."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.creative_modes import get_creator_profile_by_key  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.widgets.session_strip import SessionStrip  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _strip() -> SessionStrip:
    return SessionStrip(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Art",
    )


def test_art_hides_record_even_when_the_host_path_asks_for_it(qapp):
    strip = _strip()
    try:
        strip.set_creator_profile(get_creator_profile_by_key("art"))
        strip.set_recording_available(True)

        assert strip._record_button.isHidden() is True
        assert strip._recording_control_available is False
        assert strip._recording_setup_action.isVisible() is False
    finally:
        strip.deleteLater()


def test_art_hides_shared_track_even_when_the_host_path_asks_for_it(qapp):
    strip = _strip()
    try:
        strip.set_creator_profile(get_creator_profile_by_key("art"))
        strip.set_reference_track_available(True)

        assert strip._reference_track_button.isHidden() is True
        assert strip._reference_track_action.isVisible() is False
        assert strip._shared_track_host is False
    finally:
        strip.deleteLater()


def test_art_hides_studio_because_it_has_no_take_or_local_project(qapp):
    strip = _strip()
    try:
        strip.set_creator_profile(get_creator_profile_by_key("art"))
        assert strip._studio_button.isHidden() is True

        strip.set_creator_profile(get_creator_profile_by_key("music"))
        assert strip._studio_button.isHidden() is False
        strip.set_recording_available(True)
        strip.set_reference_track_available(True)
        assert strip._record_button.isHidden() is False
        assert strip._reference_track_button.isHidden() is False
    finally:
        strip.deleteLater()


def test_art_strip_copy_addresses_artists_not_a_review(qapp):
    strip = _strip()
    try:
        strip.set_creator_profile(get_creator_profile_by_key("art"))
        rendered = " ".join(
            (
                strip._audio_button.accessibleName(),
                strip._audio_button.toolTip(),
                strip._invite_button.accessibleName(),
                strip._invite_button.toolTip(),
                strip._record_button.accessibleName(),
                strip._record_button.toolTip(),
                strip._studio_button.accessibleName(),
                strip._studio_button.toolTip(),
                strip._ready_action.text(),
                strip._practice_action.text(),
                strip._pocket_stage_action.toolTip(),
            )
        ).casefold()

        assert "art session" in rendered
        assert "artist" in rendered
        assert "review session" not in rendered
        assert "playback-only preview" not in rendered
        assert "band" not in rendered
        assert "musician" not in rendered
        assert "studio visit" not in rendered
        assert "host-clocked" not in rendered
        assert "jamulus" not in strip._reference_track_button.accessibleDescription().casefold()
        assert strip._subtitle.text() == "Art · Preview"
    finally:
        strip.deleteLater()


def test_art_strip_status_shows_preview_not_ready_when_profile_is_preview(qapp):
    """Art stays Preview. The in-session chip must not claim Ready or GA."""

    strip = _strip()
    try:
        art = get_creator_profile_by_key("art")
        assert art.is_preview is True
        strip.set_creator_profile(art)
        assert strip._subtitle.text() == "Art · Preview"
        assert "ready" not in strip._subtitle.text().casefold()

        strip.set_creator_profile(art, locked=True)
        assert strip._subtitle.text() == "Art · Preview · Host profile"
        assert "ready" not in strip._subtitle.text().casefold()

        music = get_creator_profile_by_key("music")
        assert music.is_preview is False
        strip.set_creator_profile(music)
        assert strip._subtitle.text() == "Music · Ready"

        review = get_creator_profile_by_key("review_rehearsal")
        assert review.is_preview is True
        strip.set_creator_profile(review)
        assert strip._subtitle.text() == "Review & Rehearsal · Preview"
    finally:
        strip.deleteLater()


def test_review_and_music_keep_their_record_and_studio_chrome(qapp):
    strip = _strip()
    try:
        for key in ("music", "podcast_voice", "review_rehearsal"):
            strip.set_creator_profile(get_creator_profile_by_key(key))
            strip.set_recording_available(True)
            strip.set_reference_track_available(True)
            assert strip._record_button.isHidden() is False, key
            assert strip._studio_button.isHidden() is False, key
            assert strip._recording_setup_action.isVisible() is True, key
            assert strip._reference_track_button.isHidden() is False, key
    finally:
        strip.deleteLater()
