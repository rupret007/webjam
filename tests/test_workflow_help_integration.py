"""Help routes through the real bootstrap and existing workflow owners."""
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QDesktopServices

# Reuse the controlled bootstrap boundary: real controller/widgets and temp
# stores; only native startup/audio are replaced. This never uses user data.
from tests.test_session_library_launch import bootstrap, qapp  # noqa: F401
from tests.test_recording_studio import _schema2_studio_take


@pytest.mark.parametrize("profile,query,tab", [
    ("music", "draft", 0), ("music", "moment", 1), ("art", "relink", 2),
])
def test_help_opens_exact_existing_library_without_starting_or_opening_tools(
    bootstrap, qapp, monkeypatch, profile, query, tab,  # noqa: F811
):
    record = bootstrap.library.create(profile, "Work to preserve", notes="Keep my existing notes.")
    external_open = Mock()
    monkeypatch.setattr(QDesktopServices, "openUrl", external_open)

    def check(controller):
        window = controller.window
        window.show_help()
        help_dialog = window._workflow_help_dialog
        help_dialog._search.setText(query)
        qapp.processEvents()
        assert controller.session_library.dialog is None
        assert help_dialog._navigate.isVisible()
        help_dialog._navigate.click()
        editor = controller.session_library.dialog
        assert editor.isVisible() and not editor.isModal()
        assert editor.record.id == record.id
        assert editor.tabs.currentIndex() == tab
        assert editor.notes.toPlainText() == record.notes
        assert not controller.audio.connected
        assert not controller.recording.is_recording_active
        external_open.assert_not_called()
        editor.reject()

    bootstrap.run(record, after_open=check)


@pytest.mark.parametrize("query", ["favorite", "blocked export", "receipt"])
def test_help_studio_navigation_preserves_exact_take_and_never_plays_or_exports(
    bootstrap, qapp, tmp_path, monkeypatch, query,  # noqa: F811
):
    take, _ = _schema2_studio_take(tmp_path)
    record = bootstrap.library.create("music", "Review session")
    before = (take / "webjam-take.json").read_bytes()

    def check(controller):
        window = controller.window
        studio = window.recording_studio
        assert studio.open_take(take)
        exact_take = studio._current
        take_open = Mock(wraps=studio.open_take)
        export = Mock()
        monkeypatch.setattr(studio, "open_take", take_open)
        monkeypatch.setattr(studio, "_export_tracks", export)
        window.show_help()
        help_dialog = window._workflow_help_dialog
        help_dialog._search.setText(query)
        qapp.processEvents()
        help_dialog._navigate.click()
        assert window.workspace_stack.currentWidget() is window.reference_studio
        assert studio._current is exact_take
        assert not studio._player.is_playing
        studio._player.play.assert_not_called()
        take_open.assert_not_called()
        export.assert_not_called()
        assert (take / "webjam-take.json").read_bytes() == before

    bootstrap.run(record, after_open=check)
