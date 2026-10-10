"""Settings starts with the person's goal; live music owns engine guidance."""

import pytest

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.settings import AppSettings
from webjam_qt.windows.simple_settings import SimpleSettingsDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=[
    ("art", 800, 600), ("music", 800, 600),
    ("art", 1280, 800), ("music", 1280, 800),
])
def dialog(request, qapp, tmp_path):
    profile, width, height = request.param
    widget = SimpleSettingsDialog(
        AppSettings(
            config_file=str(tmp_path / "settings.json"),
            musician_name="Alex",
            last_creator_profile_key=profile,
        ),
        webex_opener=lambda _url: False,
    )
    widget.resize(width, height)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_settings_intro_names_the_goal_before_the_fields(dialog):
    subtitle = dialog.findChild(QLabel, "SimpleSettingsSubtitle")
    assert subtitle.text() == "Set your name and optional meeting link."
    assert subtitle.isVisible()
    assert subtitle.mapTo(dialog, QPoint()).y() < dialog._name.mapTo(dialog, QPoint()).y()
    assert dialog._name.isVisible()
    assert dialog._conversation_toggle.text() == "Meeting link (optional)"


def test_settings_jamulus_guidance_stays_under_live_music(dialog):
    music = dialog._open_jamulus.parentWidget()
    heading = music.findChild(QLabel, "SimpleSettingsSectionTitle")
    assert heading.text() == "Live music"
    engine_copy = [
        widget
        for widget in [*dialog.findChildren(QLabel), *dialog.findChildren(QPushButton)]
        if widget.isVisible() and "jamulus" in widget.text().lower()
    ]
    assert engine_copy
    assert all(music.isAncestorOf(widget) for widget in engine_copy)
    assert music.isAncestorOf(dialog._name_preview)
    dialog._name.setText("123456789")
    assert "12345678 / 9" in dialog._name_preview.text()

    action = dialog._open_jamulus
    assert action.text() == "Open Jamulus Audio Settings"
    viewport = dialog._settings_scroll.viewport()
    assert viewport.rect().contains(action.mapTo(viewport, QPoint()))
    assert viewport.rect().contains(action.mapTo(viewport, action.rect().bottomRight()))
    requested = []
    dialog.audio_settings_requested.connect(lambda: requested.append(True))
    action.click()
    assert requested == [True]
