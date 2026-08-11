"""The input-map editor validates and round-trips exactly like the loader."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import pytest  # noqa: E402

from core.settings import _coerce_input_maps  # noqa: E402
from webjam_qt.windows.input_map_editor import (  # noqa: E402
    InputMapEditorDialog,
)

_dialogs: list[InputMapEditorDialog] = []


@pytest.fixture(autouse=True)
def _dispose():
    yield
    while _dialogs:
        _dialogs.pop().deleteLater()
    _app.processEvents()


def _editor(maps=None):
    dialog = InputMapEditorDialog(maps)
    _dialogs.append(dialog)
    return dialog


def test_existing_maps_render_as_rows_and_round_trip():
    maps = [
        {"name": "Guitar DI", "channels": 1, "enabled": True,
         "local_original_enabled": True},
        {"name": "Room Pair", "channels": 2, "enabled": False,
         "local_original_enabled": False},
    ]
    editor = _editor(maps)
    assert len(editor._rows) == 2
    ok, error, collected = editor.collect()
    assert ok and error == ""
    assert collected == maps
    # What the editor collects must survive the loader's coercion unchanged.
    assert _coerce_input_maps(collected) == maps


def test_add_and_remove_rows():
    editor = _editor([])
    assert editor._rows == []
    editor._add_row({"name": "Bass", "channels": 1})
    editor._add_row({"name": "Keys", "channels": 2})
    assert len(editor._rows) == 2
    editor._remove_row(editor._rows[0])
    ok, _error, collected = editor.collect()
    assert ok
    assert [e["name"] for e in collected] == ["Keys"]


def test_validation_rejects_empty_duplicate_and_control_names():
    editor = _editor([{"name": "", "channels": 1}])
    ok, error, _ = editor.collect()
    assert not ok and "needs a name" in error

    editor = _editor([
        {"name": "Same", "channels": 1},
        {"name": "same", "channels": 2},
    ])
    ok, error, _ = editor.collect()
    assert not ok and "both named" in error

    editor = _editor([{"name": "Bad\nName", "channels": 1}])
    ok, error, _ = editor.collect()
    assert not ok and "invalid characters" in error


def test_empty_editor_is_valid_and_clears_configuration():
    editor = _editor([])
    ok, error, collected = editor.collect()
    assert ok and error == "" and collected == []


def test_save_populates_result_maps_only_when_valid():
    editor = _editor([{"name": "OK", "channels": 1}])
    editor._save()
    assert editor.result_maps() == [
        {"name": "OK", "channels": 1, "enabled": True,
         "local_original_enabled": True}
    ]

    bad = _editor([{"name": "", "channels": 1}])
    bad._save()
    assert bad.result_maps() == []
    assert bad._error.isVisibleTo(bad)


def test_row_cap_is_enforced():
    editor = _editor([{"name": f"T{i}", "channels": 1} for i in range(32)])
    assert len(editor._rows) == 32
    editor._add_row({"name": "overflow", "channels": 1})
    assert len(editor._rows) == 32
    assert not editor._add_btn.isEnabled()
