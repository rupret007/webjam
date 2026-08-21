"""The one control a panel offers, and the quiet one beside it.

ADR 0002 settled that a surface explains the next action rather than adding a
competing button. These widgets are how that reads: a chip whose label is the
status, and a companion that is visibly not the point.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from webjam_qt.widgets.status_chip import (  # noqa: E402
    CHIP_MIN_HEIGHT,
    QuietAction,
    StatusChip,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def test_a_chip_starts_with_nothing_to_offer():
    chip = StatusChip()
    try:
        assert chip.offered is False
        assert chip.text() == ""
    finally:
        chip.deleteLater()


def test_a_chip_says_the_same_thing_it_does():
    """The label is the status, so pressing it cannot be a surprise."""

    chip = StatusChip()
    try:
        chip.offer("Open shared canvas", "Open the host's canvas in Drawpile.")

        assert chip.offered is True
        assert chip.text() == "Open shared canvas"
        assert chip.accessibleName() == "Open shared canvas"
        assert chip.accessibleDescription() == (
            "Open the host's canvas in Drawpile."
        )
        assert chip.toolTip() == "Open the host's canvas in Drawpile."
    finally:
        chip.deleteLater()


def test_a_withdrawn_chip_leaves_rather_than_greying_out():
    """A disabled control is a taunt repeated every time someone looks."""

    chip = StatusChip()
    try:
        chip.offer("Open shared canvas", "Open it.")
        chip.withdraw()

        assert chip.offered is False
        assert chip.isHidden() is True
        assert chip.text() == ""
        assert chip.accessibleName() == ""
    finally:
        chip.deleteLater()


def test_a_recovery_reads_differently_from_the_real_verb():
    """Something absent is not something broken."""

    chip = StatusChip()
    try:
        chip.offer("Open shared canvas", "Open it.")
        assert chip.property("tone") == StatusChip.PRIMARY

        chip.offer("Install Drawpile", "Open the download page.", tone=StatusChip.RECOVERY)
        assert chip.property("tone") == StatusChip.RECOVERY
    finally:
        chip.deleteLater()


def test_a_chip_is_a_large_target_and_never_the_dialog_default():
    chip = StatusChip()
    try:
        assert chip.minimumHeight() >= CHIP_MIN_HEIGHT
        assert CHIP_MIN_HEIGHT >= 52
        # A panel is not a dialog to accept, so Return elsewhere must not fire
        # the panel's primary action.
        assert chip.autoDefault() is False
        assert chip.isDefault() is False
        assert chip.cursor().shape() is Qt.CursorShape.PointingHandCursor
    finally:
        chip.deleteLater()


def test_the_quiet_action_is_visibly_lesser_than_a_chip():
    chip = StatusChip()
    quiet = QuietAction()
    try:
        chip.offer("Open canvas", "Open it.")
        quiet.offer("Stop sharing", "Stop offering this canvas.")

        assert quiet.offered is True
        assert quiet.text() == "Stop sharing"
        assert quiet.minimumHeight() < chip.minimumHeight()
        assert quiet.objectName() == "QuietAction"
        assert chip.objectName() == "StatusChip"
    finally:
        quiet.deleteLater()
        chip.deleteLater()


def test_a_quiet_action_with_nothing_to_offer_also_leaves():
    quiet = QuietAction()
    try:
        assert quiet.offered is False
        quiet.offer("Stop sharing", "Stop offering this canvas.")
        assert quiet.offered is True
        quiet.withdraw()
        assert quiet.offered is False
    finally:
        quiet.deleteLater()


def test_a_chip_reports_its_state_before_its_window_is_shown():
    """The trap this pins: ``isVisible`` is false inside an unshown window.

    Panel logic keys off whether a control is *offered*, which must be true
    the moment it is offered rather than only once the window appears. Reading
    ``isVisible`` here is what made a panel silently skip a state change.
    """

    from PySide6.QtWidgets import QWidget

    window = QWidget()
    chip = StatusChip(window)
    try:
        chip.offer("Make an image", "Open a new canvas.")

        assert chip.isVisible() is False  # the window has never been shown
        assert chip.isHidden() is False
        assert chip.offered is True
    finally:
        window.deleteLater()
