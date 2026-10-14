"""The blocked Join action explains its prerequisite before activation."""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleHints().tabFocusBehavior()
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    yield app
    app.styleHints().setTabFocusBehavior(previous)


def _luminance(color):
    channels = [color.redF(), color.greenF(), color.blueF()]
    linear = [
        v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels
    ]
    return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


@pytest.mark.parametrize("profile", ["art", "music"])
@pytest.mark.parametrize(
    "size,large_text", [((800, 600), False), ((1280, 800), False), ((760, 600), True)]
)
def test_empty_join_explains_next_step_and_preserves_keyboard_order(
    qapp, tmp_path: Path, profile, size, large_text
):
    dialog = LaunchDialog(
        AppSettings(
            config_file=str(tmp_path / "settings.json"),
            last_creator_profile_key=profile,
        )
    )
    try:
        dialog.setStyleSheet(
            load_stylesheet() + ("\nQWidget { font-size: 20px; }" if large_text else "")
        )
        dialog.show_join()
        dialog.resize(*size)
        dialog.show()
        qapp.processEvents()
        helper = dialog._join_status
        assert helper.text() == "Paste an invite to continue"
        assert helper.isVisibleTo(dialog)
        assert not dialog._join_button_primary.isEnabled()
        assert helper.focusPolicy() == Qt.FocusPolicy.NoFocus
        # Keep default's 800x600 size cap while exercising every requested size.
        assert (dialog.width(), dialog.height()) == (
            min(size[0], 800), min(size[1], 600)
        )
        for widget in (
            helper,
            dialog._invite_input,
            dialog._join_button_primary,
            dialog._join_back_button,
        ):
            assert dialog.rect().contains(
                QRect(widget.mapTo(dialog, QPoint()), widget.size())
            )
        assert (
            helper.fontMetrics().horizontalAdvance(helper.text())
            <= helper.contentsRect().width()
        )
        assert helper.fontMetrics().height() <= helper.contentsRect().height()
        foreground = _luminance(helper.palette().color(QPalette.ColorRole.WindowText))
        background = _luminance(helper.palette().color(QPalette.ColorRole.Window))
        assert (max(foreground, background) + 0.05) / (
            min(foreground, background) + 0.05
        ) >= 4.5

        dialog._invite_input.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(dialog, Qt.Key.Key_Tab)
        assert dialog._join_button_primary.hasFocus()
        QTest.keyClick(dialog, Qt.Key.Key_Tab)
        assert dialog._join_back_button.hasFocus()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("profile", ["art", "music"])
def test_empty_join_helper_tracks_paste_clear_whitespace_and_return(
    qapp, tmp_path: Path, profile
):
    dialog = LaunchDialog(
        AppSettings(
            config_file=str(tmp_path / "settings.json"),
            last_creator_profile_key=profile,
        )
    )
    try:
        dialog.show_join()
        dialog.show()
        qapp.processEvents()
        for empty in ("", "   ", "\t"):
            dialog._invite_input.setText("unchecked invitation text")
            assert dialog._join_status.text() == "Invitation pasted — choose Join"
            assert dialog._join_button_primary.isEnabled()
            dialog._invite_input.setText(empty)
            assert dialog._join_status.text() == "Paste an invite to continue"
            assert dialog._join_status.isVisibleTo(dialog)
            assert not dialog._join_button_primary.isEnabled()
        dialog.show_choices()
        dialog.show_join()
        assert dialog._join_status.text() == "Paste an invite to continue"
        assert dialog._invite_input.text() == ""
        assert dialog.selected_role == ""
        assert not (tmp_path / "settings.json").exists()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
