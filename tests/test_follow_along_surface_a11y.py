"""Follow-along surfaces: accessible names, keyboard, compact 800x600 / larger text."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from core.creative_modes import get_creator_profile_by_key
from core.lesson_request import LessonRequestIntent, LessonRequestNotice
from core.reference_video import (
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
)
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.lesson_handoff import LessonHandoffPanel
from webjam_qt.widgets.webex_embed import WebexEmbed
from webjam_qt.windows.reference_video import ReferenceVideoDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleHints().tabFocusBehavior()
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    try:
        yield app
    finally:
        app.styleHints().setTabFocusBehavior(previous)


def settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def _notice():
    return LessonRequestNotice(
        participant_id="00000000-0000-0000-0000-000000000001",
        context_id="a" * 32,
        admission_id="1" * 32,
        revision=1,
        intent=LessonRequestIntent.PAUSE,
        state="accepted",
        expires_in_ms=30_000,
    )


def test_guest_lesson_buttons_have_names_descriptions_and_keyboard(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    panel = WebexEmbed()
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    panel.set_shared_lesson_context(False)
    panel.set_lesson_request_guest(
        status="Ask your host when you need time.", can_pause=True, can_ready=True,
    )
    panel.resize(800, 600)
    panel.show()
    settle(qapp)
    events = []
    panel.lesson_request_intent.connect(events.append)
    try:
        pause = panel._lesson_pause_button
        ready = panel._lesson_ready_button
        assert pause.isVisibleTo(panel) and ready.isVisibleTo(panel)
        assert pause.accessibleName() == "Ask for a pause"
        assert ready.accessibleName() == "Ready to continue"
        assert pause.accessibleDescription()
        assert ready.accessibleDescription()
        assert "host controls the browser" in pause.accessibleDescription().casefold()
        pause.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(pause, Qt.Key.Key_Space)
        settle(qapp)
        assert events == ["pause"]
    finally:
        panel.close()
        panel.deleteLater()
        settle(qapp)
        qapp.setStyleSheet(previous)


def test_host_pause_rows_expose_names_and_status(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    panel = WebexEmbed()
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    panel.set_shared_lesson_context(True)
    panel.set_lesson_request_host((("Artist", _notice()),))
    panel.show()
    settle(qapp)
    try:
        assert panel._lesson_request_host_hint.accessibleName() == "How lesson requests work"
        assert panel._lesson_request_host_hint.accessibleDescription()
        assert panel._lesson_request_scroll.accessibleName() == "Requests for this lesson"
        row = next(iter(panel._lesson_request_rows.values()))
        assert row.name_label.accessibleName() == "Artist"
        assert row.status_label.accessibleName() == "Request status"
        assert row.status_label.accessibleDescription()
        assert row.ack_button.accessibleName() == "Acknowledge request from Artist"
        assert row.ack_button.accessibleDescription()
    finally:
        panel.close()
        panel.deleteLater()
        settle(qapp)
        qapp.setStyleSheet(previous)


def test_lesson_handoff_actions_keep_accessible_descriptions(qapp):
    panel = LessonHandoffPanel()
    panel.set_context(hosting=True, profile="art")
    panel.show()
    settle(qapp)
    try:
        assert panel.choose_button.accessibleName() == "Choose YouTube lesson"
        assert "youtube" in panel.choose_button.accessibleDescription().casefold()
        assert panel.open_button.accessibleName() == "Open in browser"
        assert panel.open_button.accessibleDescription()
        panel.set_context(
            hosting=True, profile="art",
            lesson_url="https://www.youtube.com/watch?v=M7lc1UVf-VE",
        )
        assert panel.choose_button.accessibleName() == "Change YouTube lesson"
        assert panel.open_button.isVisibleTo(panel)
        assert "browser" in panel.open_button.accessibleDescription().casefold()
        assert panel.save_button.accessibleName()
        assert panel.save_button.accessibleDescription()
    finally:
        panel.close()
        panel.deleteLater()
        settle(qapp)


@pytest.mark.parametrize("size", [(800, 600), (720, 560)])
def test_guest_lesson_controls_fit_with_larger_text(qapp, size):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet() + "\nQWidget { font-size: 20px; }")
    owner = QWidget()
    layout = QVBoxLayout(owner)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = WebexEmbed()
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    panel.set_shared_lesson_context(False)
    panel.set_lesson_request_guest(
        status="Ask your host when you need time.",
        can_pause=True, can_ready=True, can_retry=True,
    )
    layout.addWidget(panel)
    owner.resize(*size)
    owner.show()
    settle(qapp)
    try:
        assert panel._lesson_pause_button.isVisibleTo(panel)
        assert panel._lesson_ready_button.isVisibleTo(panel)
        assert panel._lesson_retry_button.isVisibleTo(panel)
        for button in (
            panel._lesson_pause_button,
            panel._lesson_ready_button,
            panel._lesson_retry_button,
        ):
            assert button.accessibleName()
            assert button.accessibleDescription()
            assert button.height() >= button.minimumSizeHint().height()
        status = panel._lesson_request_guest_status
        assert status.isVisibleTo(panel)
        assert status.height() >= status.heightForWidth(status.width())
    finally:
        owner.close()
        owner.deleteLater()
        settle(qapp)
        qapp.setStyleSheet(previous)


def test_paint_along_missing_file_keeps_named_next_action(qapp):
    panel = ReferenceVideoDialog(hosting=False)
    panel.set_embedded(True)
    panel.set_follow_snapshot(ReferenceVideoFollowSnapshot(
        state=ReferenceVideoFollowState.NEEDS_FILE,
        source_kind="local",
        source_display_name="lesson.mp4",
    ))
    panel.resize(800, 600)
    panel.setStyleSheet(load_stylesheet() + "\nQWidget { font-size: 20px; }")
    panel.show()
    settle(qapp)
    try:
        assert panel._open_button.isVisibleTo(panel)
        assert panel._open_button.accessibleName() == "Open my copy…"
        assert panel._open_button.accessibleDescription()
        spoken = " ".join(
            (
                panel._status.text(),
                panel._open_button.text(),
                panel._open_button.accessibleName(),
            )
        ).casefold()
        assert "open my copy" in spoken
        assert "jamulus" not in spoken
        assert "drawpile" not in spoken
    finally:
        panel.close()
        panel.deleteLater()
        settle(qapp)
