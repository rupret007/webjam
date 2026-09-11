"""Real Art door policy; platform simulation is not native hosting proof.

The portable runtime journey separately exercises persistence and a real peer
listener. This module owns visible eligibility and the actual submission edge.
"""

from __future__ import annotations

import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from tests.support.start_ux import (
    assert_no_banned_first_screen_words,
    harvest_first_screen,
)
from webjam_qt.windows import launch_dialog as launch


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def door(qapp, monkeypatch, tmp_path):
    made = []
    saved = Mock()
    effects = []

    def forbidden(*_args, **_kwargs):
        effects.append("unexpected external effect")
        raise AssertionError("A door policy test must not start an external effect")

    monkeypatch.delenv("WEBJAM_ENABLE_REFERENCE_LOCAL", raising=False)
    monkeypatch.setattr(launch, "_windows_jamulus_installer", lambda _settings: "")
    monkeypatch.setattr(launch, "default_musician_name", lambda _settings: "Artist")
    monkeypatch.setattr(launch, "save_settings", saved)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("webbrowser.open", forbidden)

    def create(platform="win32", profile="art", *, lab=None, allow_choices=True):
        monkeypatch.setattr(launch, "sys", SimpleNamespace(platform=platform))
        if lab is not None:
            monkeypatch.setenv("WEBJAM_ENABLE_REFERENCE_LOCAL", lab)
        dialog = launch.LaunchDialog(
            AppSettings(
                config_file=str(tmp_path / "settings.json"),
                last_creator_profile_key=profile if profile in {"art", "music"} else "music",
            ),
            allow_workspace_choices=allow_choices,
        )
        made.append(dialog)
        if profile not in {"art", "music"}:
            _select_profile(dialog, profile)
        dialog.show()
        qapp.processEvents()
        return SimpleNamespace(dialog=dialog, saved=saved, accepted=QSignalSpy(dialog.accepted))

    yield create
    for dialog in reversed(made):
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    assert not effects


def _select_profile(dialog, key):
    selector = dialog._creator_profile_selector
    index = selector.findData(key)
    assert index >= 0
    selector.setCurrentIndex(index)


def _select_start(dialog, key):
    next(card for card in dialog._start_cards["art"] if card.start_key == key).click()


def _assert_no_host_submission(rig):
    assert rig.accepted.count() == 0
    assert rig.dialog.selected_role == ""
    assert not rig.dialog._submitting
    rig.saved.assert_not_called()


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("start", ["talk_and_make", "paint_along"])
def test_art_host_click_selects_only_the_chosen_room_without_a_music_install(door, platform, start):
    rig = door(platform)
    dialog = rig.dialog
    _select_start(dialog, start)
    assert dialog._host_button.isEnabled()
    assert not dialog._choice_helper.text()
    assert dialog._join_button.isEnabled()
    assert_no_banned_first_screen_words(harvest_first_screen(dialog))

    dialog._host_button.click()

    assert rig.accepted.count() == 1
    assert dialog.selected_role == "host"
    rig.saved.assert_called_once()
    candidate = rig.saved.call_args.args[0]
    assert candidate.host_server_enabled is True
    assert candidate.last_creator_profile_key == "art"
    assert candidate.last_creator_start_key == start


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
def test_other_profiles_remain_denied_even_when_host_is_called_directly(door, platform, profile):
    rig = door(platform, profile)
    assert not rig.dialog._host_button.isEnabled()
    assert rig.dialog._join_button.isEnabled()
    rig.dialog._host()
    _assert_no_host_submission(rig)


@pytest.mark.parametrize("platform", ["freebsd14", "emscripten"])
def test_unbuilt_platform_cannot_host_art_through_a_direct_call(door, platform):
    rig = door(platform)
    assert not rig.dialog._host_button.isEnabled()
    rig.dialog._host()
    _assert_no_host_submission(rig)


@pytest.mark.parametrize("profile", ["art", "music", "podcast_voice", "review_rehearsal"])
@pytest.mark.parametrize("lab", [None, "1"])
def test_mac_host_choice_keeps_existing_profile_and_lab_behavior(door, profile, lab):
    rig = door("darwin", profile, lab=lab)
    assert rig.dialog._host_button.isEnabled()
    rig.dialog._host_button.click()
    assert rig.accepted.count() == 1
    assert rig.dialog.selected_role == "host"
    assert rig.saved.call_args.args[0].last_creator_profile_key == profile


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("lab", ["1", " 1 "])
def test_native_lab_opt_in_never_becomes_non_mac_art_host_permission(door, platform, lab):
    rig = door(platform, lab=lab)
    for start in ("talk_and_make", "paint_along"):
        _select_start(rig.dialog, start)
        assert not rig.dialog._host_button.isEnabled()
        assert rig.dialog._choice_helper.text()
        assert "macOS" in rig.dialog._host_button.accessibleDescription()
    rig.dialog._host()
    _assert_no_host_submission(rig)


@pytest.mark.parametrize("lab", ["0", "true"])
def test_non_opt_in_values_leave_ordinary_art_hosting_available(door, lab):
    rig = door("win32", lab=lab)
    assert rig.dialog._host_button.isEnabled()
    rig.dialog._host_button.click()
    assert rig.accepted.count() == 1


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_profile_start_and_back_changes_keep_current_art_next_action(door, platform):
    rig = door(platform)
    dialog = rig.dialog
    _select_start(dialog, "paint_along")
    assert dialog._host_button.isEnabled()
    assert "Start Paint along as the host" in dialog._host_button.accessibleDescription()
    _select_profile(dialog, "music")
    assert not dialog._host_button.isEnabled()
    assert "macOS" in dialog._choice_helper.text()
    dialog.show_join()
    dialog.show_choices()
    assert not dialog._host_button.isEnabled()
    _select_profile(dialog, "art")
    _select_start(dialog, "talk_and_make")
    assert dialog._host_button.isEnabled()
    assert not dialog._choice_helper.text()
    assert "Start Make together as the host" in dialog._host_button.accessibleDescription()
    assert "macOS" not in dialog._host_button.accessibleDescription()
    assert_no_banned_first_screen_words(harvest_first_screen(dialog))
    rig.saved.assert_not_called()


def test_new_lab_opt_in_rejects_the_previously_enabled_host_action(door, monkeypatch):
    rig = door("win32")
    assert rig.dialog._host_button.isEnabled()
    monkeypatch.setenv("WEBJAM_ENABLE_REFERENCE_LOCAL", "1")
    rig.dialog._host()
    _assert_no_host_submission(rig)


def test_stale_enabled_button_cannot_authorize_music_host(door):
    rig = door("win32", "music")
    rig.dialog._host_button.setEnabled(True)
    rig.dialog._host_button.click()
    _assert_no_host_submission(rig)


def test_join_only_door_never_accepts_programmatic_art_host(door):
    rig = door("linux", allow_choices=False)
    rig.dialog._host()
    _assert_no_host_submission(rig)


def test_failed_save_recovers_same_art_action_and_retry_submits_once(door):
    rig = door("linux")
    dialog = rig.dialog
    _select_start(dialog, "paint_along")
    before = deepcopy(dialog._settings)
    rig.saved.side_effect = [OSError("private path must not appear"), None]
    dialog._host_button.click()
    assert rig.saved.call_count == 1
    assert rig.accepted.count() == 0
    assert dialog.selected_role == ""
    assert dialog._settings == before
    assert not dialog._submitting
    assert dialog._host_button.isEnabled()
    assert dialog._join_button.isEnabled()
    assert not dialog._choice_helper.text()
    assert dialog._choice_error.isVisibleTo(dialog)
    assert "private path" not in dialog._choice_error.text()
    assert "Paint along" in dialog._host_button.accessibleDescription()
    dialog._host_button.click()
    assert rig.saved.call_count == 2
    assert rig.accepted.count() == 1
    assert dialog.selected_role == "host"
    dialog._host()
    assert rig.saved.call_count == 2
    assert rig.accepted.count() == 1


def test_pending_join_submission_cannot_be_replaced_by_art_host(door):
    rig = door("linux")
    dialog = rig.dialog
    dialog.show_join()
    assert dialog._begin_submission(dialog._join_button_primary, "Checking…")
    dialog._host()
    assert rig.accepted.count() == 0
    assert dialog.selected_role == ""
    assert dialog._submitting
    rig.saved.assert_not_called()
