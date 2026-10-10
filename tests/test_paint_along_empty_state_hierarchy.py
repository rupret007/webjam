"""W07: empty Paint along states one headline next step with subordinate silent path."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.reference_video import ReferenceVideoDialog, _HOST_EMPTY_HEADLINE

_COMPACT = (760, 600)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleSheet()
    app.setStyleSheet(load_stylesheet())
    try:
        yield app
    finally:
        app.setStyleSheet(previous)


def _settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def test_host_empty_paint_along_leads_with_one_next_step_headline(qapp):
    panel = ReferenceVideoDialog(hosting=True)
    panel.set_embedded(True)
    panel.resize(*_COMPACT)
    panel.show()
    _settle(qapp)
    try:
        assert panel._headline._full_text == _HOST_EMPTY_HEADLINE
        assert panel._headline.accessibleName() == "Paint along next step"
        assert "Or choose a silent reference" not in panel._headline._full_text
        assert panel._headline.geometry().bottom() <= panel._watch_lesson_button.geometry().top()
        assert panel._watch_lesson_button.geometry().bottom() <= panel._silent_section.geometry().top()
        assert panel._silent_section.isVisibleTo(panel)
        assert panel._silent_section.text() == "Silent reference in WebJam"
        assert panel._share_button.objectName() == "GhostButton"
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)


def test_guest_empty_paint_along_matches_host_hierarchy(qapp):
    panel = ReferenceVideoDialog(hosting=False)
    panel.set_embedded(True)
    panel.resize(*_COMPACT)
    panel.show()
    _settle(qapp)
    try:
        assert panel._headline._full_text == _HOST_EMPTY_HEADLINE
        assert panel._headline.geometry().bottom() <= panel._watch_lesson_button.geometry().top()
        assert panel._silent_section.isVisibleTo(panel)
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)
