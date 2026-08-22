"""The retired five-mode list must never resurface as a picker.

What someone is making is chosen once, at launch, from the creator profiles.
The legacy creative-mode keys still exist because session metadata records
them, but offering them again beside the profile would be a second,
contradictory choice -- and would put "Visual Studio" back in front of an
artist who already picked Art.
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from core.creative_modes import (  # noqa: E402
    CREATIVE_MODES,
    CREATOR_PROFILES,
    canonical_creator_profile_key,
)
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.widgets.session_strip import SessionStrip  # noqa: E402

LEGACY_LABELS = {mode.label for mode in CREATIVE_MODES}
PROFILE_LABELS = {profile.label for profile in CREATOR_PROFILES}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture()
def strip(qapp):
    widget = SessionStrip(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Art",
    )
    yield widget
    widget.deleteLater()


def _visible_combo_items(widget) -> set[str]:
    items: set[str] = set()
    for combo in widget.findChildren(QComboBox):
        if not combo.isVisibleTo(widget) or not combo.isEnabled():
            continue
        items.update(combo.itemText(index) for index in range(combo.count()))
    return items


def test_the_session_strip_offers_no_legacy_mode_choice(strip):
    offered = _visible_combo_items(strip)
    assert not (offered & LEGACY_LABELS), sorted(offered & LEGACY_LABELS)
    assert "Visual Studio" not in offered


def test_the_legacy_combo_is_hidden_disabled_and_unlaid_out(strip):
    picker = strip._mode_picker

    assert picker.isVisibleTo(strip) is False
    assert picker.isEnabled() is False
    assert strip.layout().indexOf(picker) == -1
    # Nothing may re-parent it into a visible container either.
    assert picker not in [
        child for child in strip.findChildren(QComboBox) if child.isVisibleTo(strip)
    ]


def test_the_legacy_key_still_round_trips_for_session_metadata(strip):
    """Retiring the picker must not drop the mode session metadata records."""

    picker = strip._mode_picker
    index = picker.findData("visual_studio")
    assert index >= 0

    picker.setCurrentIndex(index)

    assert strip.current_mode_key() == "visual_studio"


def test_the_only_visible_workflow_choice_is_the_creator_profile(qapp, tmp_path):
    from unittest.mock import patch

    from core.settings import AppSettings
    from webjam_qt.windows.launch_dialog import LaunchDialog

    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(
            AppSettings(config_file=str(tmp_path / "settings.json"))
        )
    try:
        dialog.show()
        qapp.processEvents()
        offered = _visible_combo_items(dialog)

        # Music door is Host / Join only: no workflow combo on the first screen.
        assert offered == set()
        assert not (offered & LEGACY_LABELS)
        assert not any("Visual Studio" == item for item in offered)

        selector = dialog._creator_profile_selector
        selector.setCurrentIndex(selector.findData("art"))
        qapp.processEvents()
        offered = _visible_combo_items(dialog)
        assert PROFILE_LABELS <= offered or all(
            any(label in item for item in offered) for label in PROFILE_LABELS
        )
        assert not (offered & LEGACY_LABELS)
        assert not any("Visual Studio" == item for item in offered)
    finally:
        dialog.deleteLater()


def test_the_retired_visual_label_still_migrates_where_takes_stay_reviewable():
    """The label is gone; the guarantee about its recordings is not.

    Art is the room that mode was describing, but Art records nothing and
    reviews nothing, so a session recorded under ``visual_studio`` keeps
    resolving to the profile that can play it back.
    """

    assert canonical_creator_profile_key("visual_studio") == "review_rehearsal"
    assert canonical_creator_profile_key("studio_visit") == "art"
