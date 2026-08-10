"""Participant cards render per-take recording truth inside pinned geometry."""

import sys

import pytest

from PySide6.QtWidgets import QApplication

from webjam_qt.widgets.participant_card import (
    ParticipantCard,
    ParticipantPresentation,
)


@pytest.fixture(autouse=True)
def _qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    yield app


def _card(state=""):
    return ParticipantCard(
        ParticipantPresentation(
            channel_id=1,
            name="Jeff",
            role="Guitar",
            recording_state=state,
        )
    )


def test_states_render_text_not_color_alone_and_stay_in_envelope():
    expected = {
        "armed": "Armed",
        "waiting": "Waiting…",
        "recording": "● REC",
        "conflicted": "Needs attention",
        "missing": "Missing",
        "finalized": "Saved",
    }
    for state, text in expected.items():
        card = _card(state)
        assert card._recording_label.isVisibleTo(card), state
        assert card._recording_label.text() == text, state
        # The pinned card envelope must not grow for the badge.
        assert card.minimumWidth() == ParticipantCard.CARD_MIN_WIDTH
        assert card.minimumHeight() == ParticipantCard.CARD_MIN_HEIGHT
        assert f"Recording: {text}." in card.accessibleDescription()


def test_empty_or_unknown_state_hides_the_badge():
    for state in ("", "unknown-state", None):
        card = _card(state or "")
        assert not card._recording_label.isVisibleTo(card)
        assert "Recording:" not in card.accessibleDescription()


def test_update_presentation_moves_between_states():
    card = _card("")
    presentation = ParticipantPresentation(
        channel_id=1, name="Jeff", role="Guitar", recording_state="recording"
    )
    card.update_presentation(presentation)
    assert card._recording_label.text() == "● REC"
    presentation.recording_state = ""
    card.update_presentation(presentation)
    assert not card._recording_label.isVisibleTo(card)
    assert card._name_label.text() == "Jeff"
    assert card._role_label.text() == "Guitar"
