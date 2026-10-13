"""Art Settings describes identity and conversation without music setup."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.jamulus_name import JAMULUS_NAME_HELP
from core.settings import AppSettings, load_settings
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.jamulus_name_preview import PLAIN_NAME_HELP
from webjam_qt.windows.simple_settings import SimpleSettingsDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def art_dialog(qapp, tmp_path):
    dialog = SimpleSettingsDialog(
        AppSettings(
            musician_name="Alex", config_file=str(tmp_path / "settings.json")
        ),
        creator_profile_key="art",
        webex_opener=lambda _url: pytest.fail("Settings must not open a meeting"),
    )
    dialog.setStyleSheet(load_stylesheet())
    yield dialog
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
def test_art_settings_first_paint_has_neutral_subtitle(art_dialog, qapp, size):
    art_dialog.resize(*size)
    art_dialog.show()
    qapp.processEvents()

    subtitle = art_dialog.findChild(QLabel, "SimpleSettingsSubtitle")
    assert subtitle.text() == "Your name and optional meeting link."
    visible_copy = " ".join(
        label.text() for label in art_dialog.findChildren(QLabel) if label.isVisible()
    )
    assert "Jamulus" not in visible_copy
    assert "Live music" not in visible_copy
    assert not hasattr(art_dialog, "_open_jamulus")
    for widget in (subtitle, art_dialog._name, art_dialog._name_preview):
        assert widget.isVisible()
        assert widget.visibleRegion().boundingRect() == widget.rect()


def test_art_settings_name_uses_plain_help_and_accessible_identity(art_dialog):
    assert art_dialog._name.accessibleName() == "Your name"
    assert art_dialog._name.accessibleDescription() == PLAIN_NAME_HELP
    assert art_dialog._name_preview.accessibleName() == "What others see"
    assert PLAIN_NAME_HELP in art_dialog._name_preview.text()
    art_dialog._name.setText("Alex Painter")
    assert "Alex Pai / nter" in art_dialog._name_preview.text()
    assert "Jamulus" not in art_dialog._name_preview.accessibleDescription()


@pytest.mark.parametrize("profile", ["art", "music"])
@pytest.mark.parametrize("show_band_check", [True, False])
def test_verify_sound_is_only_offered_for_music(
    qapp, tmp_path, profile, show_band_check
):
    dialog = SimpleSettingsDialog(
        AppSettings(config_file=str(tmp_path / "settings.json")),
        creator_profile_key=profile,
        show_band_check_action=show_band_check,
        webex_opener=lambda _url: False,
    )
    try:
        buttons = [button.text() for button in dialog.findChildren(QPushButton)]
        assert ("Verify Sound" in buttons) == (profile == "music" and show_band_check)
        assert "Save" in buttons
        assert "Cancel" in buttons
        if profile == "music":
            assert dialog._name.accessibleDescription() == JAMULUS_NAME_HELP
            assert dialog._open_jamulus.text() == "Open Jamulus Audio Settings"
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_art_settings_save_keeps_identity_and_meeting_without_sound_check(art_dialog):
    art_dialog._name.setText("Alex Painter")
    art_dialog._conversation_toggle.setChecked(True)
    art_dialog._video.setText("https://meet.jit.si/ArtRoom")
    save = next(
        button for button in art_dialog.findChildren(QPushButton)
        if button.text() == "Save"
    )
    save.click()

    assert art_dialog.result() == SimpleSettingsDialog.DialogCode.Accepted
    assert not art_dialog.run_band_check_after_save
    saved = load_settings(art_dialog._settings.config_file)
    assert saved.musician_name == "Alex Painter"
    assert saved.webex_url == "https://meet.jit.si/ArtRoom"
