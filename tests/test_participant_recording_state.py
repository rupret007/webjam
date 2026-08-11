"""Participant cards render per-take recording truth inside pinned geometry."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from webjam_qt.widgets.participant_card import (  # noqa: E402
    ParticipantCard,
    ParticipantPresentation,
)


import pytest  # noqa: E402

_cards: list[ParticipantCard] = []


@pytest.fixture(autouse=True)
def _dispose_cards():
    yield
    # Destroy native widgets deterministically; interpreter-exit collection
    # of unparented QWidgets crashes Qt offscreen teardown.
    while _cards:
        card = _cards.pop()
        card.deleteLater()
    _app.processEvents()


def _card(state=""):
    card = ParticipantCard(
        ParticipantPresentation(
            channel_id=1,
            name="Jeff",
            role="Guitar",
            recording_state=state,
        )
    )
    _cards.append(card)
    return card


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
