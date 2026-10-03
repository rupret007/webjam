"""Deterministic font widths registered only for explicit layout probes."""
from pathlib import Path

import pytest
from PySide6.QtGui import QFontDatabase


@pytest.fixture
def box_font(app, request):
    wide = request.node.callspec.params.get("stretch", 100) == 125
    name = "WideEmProbe.ttf" if wide else "BoxEmProbe.ttf"
    family = "WebJam Wide Em Probe" if wide else "WebJam Box Em Probe"
    font_id = QFontDatabase.addApplicationFont(str(Path(__file__).parent / "fonts" / name))
    assert font_id >= 0
    assert QFontDatabase.applicationFontFamilies(font_id) == [family]
    try:
        yield family
    finally:
        assert QFontDatabase.removeApplicationFont(font_id)
