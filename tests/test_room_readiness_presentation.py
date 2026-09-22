"""Visible and spoken room actions agree with the current session state."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from core.creative_modes import get_creator_profile_by_key
from core.settings import AppSettings
from webjam_qt.session_state import SessionUiState
from webjam_qt.theme import load_stylesheet
from webjam_qt.theme.tokens import Color
from webjam_qt.widgets.participant_grid import ParticipantGrid
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
def test_room_recovery_button_speaks_the_action_it_will_perform(qapp, profile):
    grid = ParticipantGrid()
    try:
        grid.set_creator_profile(get_creator_profile_by_key(profile))
        for state in (
            SessionUiState.permission_denied(),
            SessionUiState.connection_failed(),
            SessionUiState.connecting(""),
        ):
            grid.set_session_state(state)
            button = grid._empty_primary
            assert button.accessibleName() == button.text().replace("&&", "&")
            assert button.accessibleDescription() == grid._empty_message.text()
            assert button.toolTip() == grid._empty_message.text()
    finally:
        _dispose(grid)


@pytest.mark.parametrize("retired", ["disabled", "hidden", "participants"])
def test_retired_empty_room_action_cannot_start_audio(qapp, retired):
    from webjam_qt.widgets.participant_card import ParticipantPresentation

    grid = ParticipantGrid()
    calls = []
    grid.start_audio_requested.connect(lambda: calls.append("start"))
    grid.microphone_settings_requested.connect(lambda: calls.append("settings"))
    try:
        grid.set_session_state(SessionUiState.permission_denied())
        grid._empty_primary.click()
        assert calls == ["settings"]
        if retired == "disabled":
            grid.set_session_state(SessionUiState.connecting(""))
        elif retired == "hidden":
            grid.set_session_state(replace(SessionUiState.idle(), show_primary=False))
        else:
            grid.set_participants([ParticipantPresentation(channel_id=1, name="Artist")])
        # A queued click can outlive the state in which the button was offered.
        grid._empty_primary.clicked.emit()
        assert calls == ["settings"]
        grid.set_participants([])
        grid.set_session_state(SessionUiState.connection_failed())
        grid._empty_primary.click()
        assert calls == ["settings", "start"]
    finally:
        _dispose(grid)


def test_navigating_away_retires_a_queued_room_action(qapp):
    pages = QStackedWidget()
    grid = ParticipantGrid()
    pages.addWidget(grid)
    pages.addWidget(QWidget())
    calls = []
    grid.start_audio_requested.connect(lambda: calls.append("start"))
    pages.show()
    qapp.processEvents()
    try:
        pages.setCurrentIndex(1)
        grid._empty_primary.clicked.emit()
        assert calls == []
        pages.setCurrentIndex(0)
        grid._empty_primary.click()
        assert calls == ["start"]
    finally:
        _dispose(pages)


@pytest.mark.parametrize("hosting", [False, True])
def test_idle_room_does_not_claim_recording_or_audio_readiness(qapp, hosting):
    grid = ParticipantGrid()
    try:
        state = SessionUiState.idle(hosting=hosting)
        grid.set_session_state(state)
        assert grid._empty_eyebrow.text() == "NOT CONNECTED"
        assert not state.hint
        assert grid._empty_hint.isHidden()
        assert grid._empty_primary.isEnabled()
    finally:
        _dispose(grid)


def test_enabled_door_cards_do_not_use_the_disabled_text_palette(qapp, tmp_path):
    dialog = LaunchDialog(AppSettings(
        config_file=str(tmp_path / "settings.json"), last_creator_profile_key="art",
    ))
    dialog.setStyleSheet(load_stylesheet())
    dialog.show()
    qapp.processEvents()
    try:
        # QCommandLinkButton paints its text from the base ButtonText palette;
        # a :checked color rule alone does not change the native text painter.
        for card in (*dialog._profile_cards.values(), *dialog._visible_start_cards()):
            assert card.isEnabled()
            assert card.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText).name().upper() == Color.TEXT_PRIMARY
            card.setEnabled(False)
            qapp.processEvents()
            assert card.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText).name().upper() == Color.TEXT_MUTED
    finally:
        _dispose(dialog)
