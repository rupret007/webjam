"""W04: compact session-strip conversation label matches room overview wording."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from webjam_qt.widgets.session_strip import SessionStrip


@pytest.fixture(scope="module", autouse=True)
def _qt_application() -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication(sys.argv[:1])


def _strip() -> SessionStrip:
    return SessionStrip(
        mode_entries=[("music_jam", "Music")],
        initial_mode_key="music_jam",
        initial_title="Making together",
    )


def test_unconfigured_conversation_uses_setup_copy_in_compact_footer() -> None:
    strip = _strip()
    try:
        strip.set_video_configured(False)
        strip.set_compact_control_labels(True)

        assert strip._video_action.text() == "Set Up Conversation"
        assert strip._video_button.text() == "Set Up"
        assert strip._video_button.text() != "Talk"
    finally:
        strip.close()
        strip.deleteLater()


def test_configured_conversation_keeps_talk_in_compact_footer() -> None:
    strip = _strip()
    try:
        strip.set_video_configured(True)
        strip.set_compact_control_labels(True)

        assert strip._video_action.text() == "Conversation"
        assert strip._video_button.text() == "Talk"
    finally:
        strip.close()
        strip.deleteLater()


def test_unconfigured_conversation_keeps_short_footer_when_not_compact() -> None:
    strip = _strip()
    try:
        strip.set_video_configured(False)
        strip.set_compact_control_labels(False)

        assert strip._video_action.text() == "Set Up Conversation"
        assert strip._video_button.text() == "Set Up"
    finally:
        strip.close()
        strip.deleteLater()


def test_compact_label_tracks_configuration_after_resize_toggle() -> None:
    strip = _strip()
    try:
        strip.set_video_configured(False)
        strip.set_compact_control_labels(True)
        assert strip._video_button.text() == "Set Up"

        strip.set_video_configured(True)
        assert strip._video_button.text() == "Talk"

        strip.set_video_configured(False)
        strip.set_compact_control_labels(False)
        assert strip._video_button.text() == "Set Up"
    finally:
        strip.close()
        strip.deleteLater()


@pytest.mark.parametrize("width", [1060, 1080])
def test_themed_music_footer_fits_with_unconfigured_setup_and_safety_actions(
    width: int,
) -> None:
    from core.creative_modes import get_creator_profile_by_key
    from PySide6.QtWidgets import QApplication
    from webjam_qt.theme import load_stylesheet
    from webjam_qt.windows.conductor_window import ConductorWindow

    app = QApplication.instance()
    assert app is not None

    w = ConductorWindow(
        mode_entries=[("music_jam", "Music")],
        initial_mode_key="music_jam",
        initial_title="Making together",
    )
    w.setStyleSheet(load_stylesheet())
    w.set_creator_profile(get_creator_profile_by_key("music"))
    strip = w.session_strip
    strip.set_invite_available(True)
    strip.set_recording_available(True)
    strip.set_reference_track_available(True)
    strip.set_video_configured(False)
    strip.set_recording_phase("stop_failed")
    strip.set_audio_state("Try End Session")
    w.resize(width, 600)
    w.show()
    for _ in range(3):
        app.processEvents()
    try:
        assert strip._video_button.text() == "Set Up"
        assert strip._video_action.text() == "Set Up Conversation"
        assert strip._audio_button.text() == "Try End Session"
        controls = (
            strip._invite_button,
            strip._record_elapsed,
            strip._record_button,
            strip._video_button,
            strip._studio_button,
            strip._tools_button,
            strip._audio_button,
        )
        visible = [control for control in controls if control.isVisibleTo(w)]
        assert visible == list(controls)
        assert strip._reference_track_button.isVisibleTo(w)
        bar_width = w.session_controls.width()
        for control in visible:
            assert control.geometry().left() >= 0
            assert control.geometry().right() < bar_width
            assert control.width() >= control.minimumSizeHint().width()
        for first, second in zip(visible, visible[1:]):
            assert first.geometry().right() < second.geometry().left()
    finally:
        w.close()
        w.deleteLater()
        for _ in range(3):
            app.processEvents()
