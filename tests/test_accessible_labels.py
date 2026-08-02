"""Controls must announce the action they are currently showing.

A button whose label changes with state is usually given one fixed
accessible name when it is built. Assistive technology then keeps
announcing that original wording, so a musician using VoiceOver is told
about a different action from the one on screen. These pin the widgets
where the label genuinely changes.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from webjam_qt.widgets.accessible import set_labeled_action  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_helper_announces_exactly_what_it_displays(qt_app) -> None:
    button = QPushButton()

    set_labeled_action(button, "Show Studio Export")

    assert button.text() == "Show Studio Export"
    assert button.accessibleName() == "Show Studio Export"


def test_helper_keeps_an_escaped_ampersand_out_of_the_announcement(
    qt_app,
) -> None:
    """"Bass && Drums" reads as one band name, not a keyboard mnemonic."""

    button = QPushButton()

    set_labeled_action(button, "Bass && Drums")

    assert button.text() == "Bass && Drums"
    assert button.accessibleName() == "Bass & Drums"


def test_helper_sets_a_description_only_when_given(qt_app) -> None:
    button = QPushButton()
    button.setAccessibleDescription("original")

    set_labeled_action(button, "Play")
    assert button.accessibleDescription() == "original"

    set_labeled_action(button, "Pause", description="Stops the take")
    assert button.accessibleDescription() == "Stops the take"


def test_studio_transport_announces_its_current_action(qt_app) -> None:
    """The take player's button toggles Play/Pause; the name must follow."""

    from webjam_qt.widgets.recording_studio import RecordingStudio

    studio = RecordingStudio()
    try:
        button = studio._play_btn
        for label in ("▶ Play", "⏸ Pause", "Preparing…"):
            set_labeled_action(button, label)
            assert button.accessibleName() == button.text()
    finally:
        studio.deleteLater()


def test_every_relabelled_control_uses_the_helper() -> None:
    """Guard the pattern, not just the three widgets fixed today.

    Structural rather than behavioural: a control whose label is reassigned
    must go through set_labeled_action, so a future edit cannot reintroduce
    a fixed accessible name beside a changing one.
    """

    import ast
    import pathlib as _pathlib

    tracked = {
        ("webjam_qt/windows/ready_check.py", "_primary"),
        ("webjam_qt/widgets/recording_studio.py", "_play_btn"),
        ("webjam_qt/widgets/recording_studio.py", "_reveal_btn"),
    }
    offenders = []
    for path, widget in sorted(tracked):
        tree = ast.parse(_pathlib.Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setText"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == widget
            ):
                continue
            offenders.append(f"{path}:{node.lineno} {widget}.setText")

    assert not offenders, (
        "these relabel a control without announcing the new label; use "
        "set_labeled_action: " + ", ".join(offenders)
    )
