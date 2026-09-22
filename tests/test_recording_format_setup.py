"""Explicit format repair stays separate from consent and live audio."""
from __future__ import annotations

import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from core.settings import AppSettings
from webjam_qt.windows.recording_setup import RecordingSetupDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def dialogs(qapp, monkeypatch, tmp_path):
    for name in ("WEBJAM_AUDIO_SAMPLERATE", "WEBJAM_AUDIO_BLOCKSIZE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "webjam_qt.windows.recording_setup.list_input_devices",
        lambda: [{"name": "Test interface", "channels": 2, "index": 7}],
    )
    created, saved = [], []
    monkeypatch.setattr(
        "webjam_qt.windows.recording_setup.save_settings",
        lambda settings: saved.append(deepcopy(settings)),
    )

    def create(*, capture=False, rate=44100, block=256, guard=None, profile="music"):
        settings = AppSettings(
            config_file=str(tmp_path / "settings.json"),
            audio_samplerate=rate, audio_blocksize=block,
            audio_input_device_index=7, local_capture_enabled=capture,
        )
        kwargs = {} if guard is None else {"format_change_guard": guard}
        dialog = RecordingSetupDialog(settings, creator_profile=profile, **kwargs)
        created.append(dialog)
        return dialog, settings, saved

    yield create
    for dialog in created:
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("capture", [False, True])
def test_explicit_repair_format_only_applies_on_save_without_changing_consent(dialogs, capture):
    dialog, original, saved = dialogs(capture=capture)
    assert "44.1" in dialog._format_status.text()
    assert "256" in dialog._format_status.text()
    assert not dialog._repair_format.isChecked()
    assert not dialog.format_repair_requested
    dialog._repair_format.setChecked(True)
    assert dialog.format_repair_requested
    assert (dialog._settings.audio_samplerate, dialog._settings.audio_blocksize) == (44100, 256)
    assert original.local_capture_enabled is capture
    assert not saved

    dialog._save()

    assert dialog.result() == dialog.DialogCode.Accepted
    assert len(saved) == 1
    assert (saved[0].audio_samplerate, saved[0].audio_blocksize) == (48000, 0)
    assert saved[0].local_capture_enabled is capture
    assert (original.audio_samplerate, original.audio_blocksize) == (44100, 256)


def test_cancel_and_unrequested_save_preserve_format(dialogs):
    canceled, original, saved = dialogs()
    canceled._repair_format.setChecked(True)
    canceled.reject()
    assert not saved
    assert (original.audio_samplerate, original.audio_blocksize) == (44100, 256)
    ordinary, _, saved = dialogs()
    ordinary._save()
    assert (saved[0].audio_samplerate, saved[0].audio_blocksize) == (44100, 256)


def test_automatic_buffer_is_named_and_keyboard_can_request_repair(dialogs, qapp):
    dialog, _, saved = dialogs(block=0)
    assert "automatic" in dialog._format_status.text().lower()
    assert dialog._repair_format.accessibleName()
    assert dialog._repair_format.accessibleDescription()
    dialog.show()
    dialog.activateWindow()
    dialog._repair_format.setFocus()
    qapp.processEvents()
    QTest.keyClick(dialog._repair_format, Qt.Key.Key_Space)
    assert dialog._repair_format.hasFocus()
    assert dialog._repair_format.isChecked()
    assert not saved


def test_active_guard_disables_only_repair_format_and_allows_opt_out(dialogs):
    message = "Close Recording Setup, end or leave the session, then reopen Recording Setup."
    dialog, _, saved = dialogs(capture=True, guard=lambda: message)
    assert not dialog._repair_format.isEnabled()
    assert message in dialog._format_help.text()
    assert message in dialog._repair_format.accessibleDescription()
    assert dialog._capture.isEnabled()
    dialog._capture.setChecked(False)
    dialog._save()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert not saved[0].local_capture_enabled
    assert (saved[0].audio_samplerate, saved[0].audio_blocksize) == (44100, 256)


def test_save_rechecks_live_guard_and_preserves_repair_for_retry(dialogs):
    state = {"reason": ""}
    dialog, original, saved = dialogs(guard=lambda: state["reason"])
    dialog._repair_format.setChecked(True)
    state["reason"] = "Close Recording Setup and finish ending the session before reopening it."
    dialog._save()
    assert not saved and dialog.result() == dialog.DialogCode.Rejected
    assert state["reason"] in dialog._error.text()
    assert dialog._repair_format.isChecked()
    assert (dialog._settings.audio_samplerate, dialog._settings.audio_blocksize) == (44100, 256)
    assert (original.audio_samplerate, original.audio_blocksize) == (44100, 256)
    state["reason"] = ""
    dialog._save()
    assert (saved[0].audio_samplerate, saved[0].audio_blocksize) == (48000, 0)


def test_uncertain_guard_keeps_repair_disabled_without_exposing_exception(dialogs):
    def unknown():
        raise RuntimeError("private device path")

    dialog, _, saved = dialogs(guard=unknown)
    assert not dialog._repair_format.isEnabled()
    assert "Close Recording Setup" in dialog._format_help.text()
    assert "private device path" not in dialog._format_help.text()
    dialog._save()
    assert (saved[0].audio_samplerate, saved[0].audio_blocksize) == (44100, 256)


@pytest.mark.parametrize("name,value", [("WEBJAM_AUDIO_SAMPLERATE", "44100"), ("WEBJAM_AUDIO_BLOCKSIZE", "512")])
def test_conflicting_environment_override_has_fixed_guidance_and_opt_out(dialogs, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    dialog, _, saved = dialogs(capture=True)
    assert not dialog._repair_format.isEnabled()
    assert name in dialog._format_help.text()
    assert value not in dialog._format_help.text()
    assert "restart" in dialog._format_help.text().lower()
    dialog._capture.setChecked(False)
    dialog._save()
    assert not saved[0].local_capture_enabled
    assert (saved[0].audio_samplerate, saved[0].audio_blocksize) == (44100, 256)


@pytest.mark.parametrize("rate,block", [("48000", "0"), ("private/path", "invalid-secret"), ("0", "-1")])
def test_compatible_or_ignored_environment_override_does_not_block(dialogs, monkeypatch, rate, block):
    monkeypatch.setenv("WEBJAM_AUDIO_SAMPLERATE", rate)
    monkeypatch.setenv("WEBJAM_AUDIO_BLOCKSIZE", block)
    dialog, _, saved = dialogs()
    assert dialog._repair_format.isEnabled()
    assert "private/path" not in dialog._format_help.text()
    assert "invalid-secret" not in dialog._format_help.text()
    if rate == "48000":
        assert "WEBJAM_AUDIO_SAMPLERATE" in dialog._format_help.text()
        assert "WEBJAM_AUDIO_BLOCKSIZE" in dialog._format_help.text()
    dialog._repair_format.setChecked(True)
    dialog._save()
    assert (saved[0].audio_samplerate, saved[0].audio_blocksize) == (48000, 0)


def test_new_environment_conflict_blocks_save_without_mutating_draft(dialogs, monkeypatch):
    dialog, _, saved = dialogs()
    dialog._repair_format.setChecked(True)
    monkeypatch.setenv("WEBJAM_AUDIO_SAMPLERATE", "96000")
    dialog._save()
    assert not saved
    assert "WEBJAM_AUDIO_SAMPLERATE" in dialog._error.text()
    assert "96000" not in dialog._error.text()
    assert dialog._repair_format.isChecked()
    assert (dialog._settings.audio_samplerate, dialog._settings.audio_blocksize) == (44100, 256)


def test_failed_save_then_unchecked_retry_does_not_silently_repair(dialogs, monkeypatch):
    dialog, original, saved = dialogs()
    dialog._repair_format.setChecked(True)

    def fail(_settings):
        raise OSError("private saved path")

    monkeypatch.setattr("webjam_qt.windows.recording_setup.save_settings", fail)
    dialog._save()
    assert dialog.result() == dialog.DialogCode.Rejected
    assert not saved
    assert "private saved path" not in dialog._error.text()
    assert (dialog._settings.audio_samplerate, dialog._settings.audio_blocksize) == (44100, 256)
    dialog._repair_format.setChecked(False)
    monkeypatch.setattr("webjam_qt.windows.recording_setup.save_settings", saved.append)
    dialog._save()
    assert (saved[0].audio_samplerate, saved[0].audio_blocksize) == (44100, 256)
    assert (original.audio_samplerate, original.audio_blocksize) == (44100, 256)


@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
def test_format_controls_fit_compact_dialog_at_125_percent_font(dialogs, qapp, profile):
    dialog, _, _ = dialogs(profile=profile, guard=lambda: "Close Recording Setup, end or leave the session, then reopen Recording Setup.")
    font = QFont(dialog.font())
    font.setPointSizeF(font.pointSizeF() * 1.25)
    dialog.setFont(font)
    dialog.resize(620, 360)
    dialog.show()
    qapp.processEvents()
    scroll = dialog.findChild(QScrollArea, "RecordingSetupScrollArea")
    save = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Save Recording Setup")
    assert scroll.verticalScrollBar().maximum() > 0
    assert save.isVisibleTo(dialog)
    assert save.geometry().bottom() <= dialog.contentsRect().bottom()
    for label in (dialog._format_status, dialog._format_help):
        assert label.height() >= label.heightForWidth(label.width())
    assert dialog._repair_format.width() >= dialog._repair_format.sizeHint().width()
