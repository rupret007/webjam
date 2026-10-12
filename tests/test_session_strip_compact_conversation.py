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


def test_unconfigured_conversation_shows_full_setup_label_when_not_compact() -> None:
    strip = _strip()
    try:
        strip.set_video_configured(False)
        strip.set_compact_control_labels(False)

        assert strip._video_button.text() == "Set Up Conversation"
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
        assert strip._video_button.text() == "Set Up Conversation"
    finally:
        strip.close()
        strip.deleteLater()
