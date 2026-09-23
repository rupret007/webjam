"""Studio recovery actions stay with the selected take and visible controls."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox

from core import studio_controller as studio_owner_module
from core.settings import AppSettings
from core.studio_store import StudioStoreError
from core.take_player import PlaybackDeviceError, TakePlayer
from tests.test_art_room_controller import (
    controllers as _controllers_fixture,
    qapp as _qapp_fixture,
)
from tests.test_recording_studio import (
    RATE,
    _SilentSink,
    _mark_verified,
    _schema2_repeated_take,
    _schema2_studio_take,
    _wav,
)
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.windows.recording_setup import RecordingSetupDialog

controllers = _controllers_fixture
qapp = _qapp_fixture


def _row_for(studio, path):
    return next(
        row for row in range(studio._take_list.count())
        if Path(studio._take_list.item(row).data(Qt.ItemDataRole.UserRole)) == path
    )


@pytest.fixture
def failed_studio_save(controllers, qapp, monkeypatch):
    app = controllers(hosting=True)
    app.window.show()
    app._open_take_deck()
    studio = app.window.recording_studio
    root = Path(app.settings.takes_directory)
    take, (_, local_track_id) = _schema2_studio_take(root)
    other_take, _ = _schema2_repeated_take(root, take)
    studio.reload()
    studio._take_list.setCurrentRow(_row_for(studio, take))
    studio._lanes[1]._pan.setValue(35)
    original_write = studio_owner_module.save_studio_document
    with monkeypatch.context() as patch:
        patch.setattr(
            studio_owner_module, 'save_studio_document',
            Mock(side_effect=StudioStoreError('PRIVATE_STORAGE_DETAIL')),
        )
        assert not studio._flush_studio_state()
    qapp.processEvents()
    assert studio._studio_controller.dirty
    assert studio._studio_persistence_failed
    assert studio._current.path == take
    assert studio._studio_controller.document.state_for(local_track_id).pan == pytest.approx(.35)
    starts = Mock()
    monkeypatch.setattr(app, '_on_session_audio_requested', starts)
    meter = Mock()
    monkeypatch.setattr(app.jamulus.audio_engine, 'start', meter)
    record = Mock()
    monkeypatch.setattr(app.recording, 'on_record_requested', record)
    check = Mock()
    monkeypatch.setattr(app, '_on_ready_check', check)
    rig = SimpleNamespace(
        app=app, studio=studio, take=take, other_take=other_take,
        local_track_id=local_track_id, original_write=original_write,
        starts=starts, meter=meter, record=record, check=check,
        media={path: path.read_bytes() for path in take.rglob('*.wav')},
    )
    yield rig
    # A failed assertion must not leave a simulated write failure blocking the
    # real controller fixture's orderly shutdown.
    with monkeypatch.context() as patch:
        patch.setattr(studio_owner_module, 'save_studio_document', original_write)
        studio._flush_studio_state()


def _assert_no_audio_or_unrelated_check(rig):
    rig.starts.assert_not_called()
    rig.meter.assert_not_called()
    rig.record.assert_not_called()
    rig.check.assert_not_called()


def _click_save_retry(rig):
    hud = rig.app.window.session_hud
    assert hud._action.isVisibleTo(rig.app.window)
    assert hud._action.isEnabled()
    assert hud._action.text() == 'Retry Save'
    assert hud._action_kind == 'retry_studio_save'
    QTest.mouseClick(hud._action, Qt.MouseButton.LeftButton)


def test_failed_studio_save_retries_from_the_actual_hud_without_leaving_take(
    failed_studio_save, monkeypatch,
):
    rig = failed_studio_save
    selected = rig.studio._current
    owner = rig.studio._studio_controller
    generation = owner.generation
    write = Mock(wraps=rig.original_write)
    monkeypatch.setattr(studio_owner_module, 'save_studio_document', write)

    _click_save_retry(rig)

    assert write.call_count == 1
    assert rig.studio._current is selected
    assert owner.generation == generation
    assert not owner.dirty
    assert not rig.studio._studio_persistence_failed
    assert owner.document.state_for(rig.local_track_id).pan == pytest.approx(.35)
    assert 'saved' in rig.studio._hint.text().lower()
    assert rig.studio._hint.text().lower().find("couldn't") < 0
    assert all(path.read_bytes() == data for path, data in rig.media.items())
    _assert_no_audio_or_unrelated_check(rig)


def test_retry_save_failure_retains_current_edit_and_retry_action(
    failed_studio_save, monkeypatch,
):
    rig = failed_studio_save
    owner = rig.studio._studio_controller
    before = owner.document
    write = Mock(side_effect=StudioStoreError('PRIVATE_STORAGE_DETAIL'))
    monkeypatch.setattr(studio_owner_module, 'save_studio_document', write)

    _click_save_retry(rig)

    assert write.call_count == 1
    assert owner.dirty
    assert rig.studio._studio_persistence_failed
    assert owner.document == before
    assert rig.app.window.session_hud._action.text() == 'Retry Save'
    assert 'PRIVATE' not in rig.studio._hint.text()
    assert 'safe' in rig.studio._hint.text().lower()
    assert all(path.read_bytes() == data for path, data in rig.media.items())
    _assert_no_audio_or_unrelated_check(rig)


def test_queued_save_retry_cannot_write_while_quit_is_in_progress(
    failed_studio_save, monkeypatch,
):
    rig = failed_studio_save
    write = Mock(wraps=rig.original_write)
    with monkeypatch.context() as patch:
        patch.setattr(studio_owner_module, "save_studio_document", write)
        patch.setattr(rig.app, "_shutdown_in_progress", True)
        rig.app.window.session_hud.action_requested.emit("retry_studio_save")
        write.assert_not_called()
    assert rig.studio._studio_controller.dirty
    _assert_no_audio_or_unrelated_check(rig)


@pytest.mark.parametrize('change', ['selected_take', 'creator_profile', 'visible_workspace'])
def test_stale_save_retry_cannot_write_or_start_audio_after_context_changes(
    failed_studio_save, qapp, monkeypatch, change,
):
    rig = failed_studio_save
    studio = rig.studio
    if change == 'selected_take':
        # A real selection change first saves the old edit. The new take's
        # separate unsaved edit is not authority for the old failure action.
        studio._take_list.setCurrentRow(_row_for(studio, rig.other_take))
        assert studio._current.path == rig.other_take
        studio._lanes[0]._pan.setValue(20)
        studio._studio_state_save_timer.stop()
        assert studio._studio_controller.dirty
        assert not studio._studio_persistence_failed
    else:
        with monkeypatch.context() as patch:
            patch.setattr(
                studio_owner_module, 'save_studio_document',
                Mock(side_effect=StudioStoreError('PRIVATE_STORAGE_DETAIL')),
            )
            if change == 'creator_profile':
                rig.app._apply_creator_profile_key('review_rehearsal')
                assert rig.app.creator_profile.key == 'review_rehearsal'
            else:
                rig.app._on_rail_view_changed('stage')
                assert not studio.isVisibleTo(rig.app.window)
        studio._studio_state_save_timer.stop()
    qapp.processEvents()
    owner = studio._studio_controller
    before = owner.document
    write = Mock(wraps=rig.original_write)
    monkeypatch.setattr(studio_owner_module, 'save_studio_document', write)

    # Simulate a queued command from the former Retry Save presentation.
    rig.app._on_conductor_action_requested('retry_studio_save')

    write.assert_not_called()
    assert owner.document == before
    assert owner.dirty
    _assert_no_audio_or_unrelated_check(rig)


def test_failed_save_still_vetoes_new_live_take_and_other_take_selection(
    failed_studio_save, monkeypatch,
):
    rig = failed_studio_save
    studio = rig.studio
    current = studio._current
    before = studio._studio_controller.document
    monkeypatch.setattr(
        studio_owner_module, 'save_studio_document',
        Mock(side_effect=StudioStoreError('PRIVATE_STORAGE_DETAIL')),
    )
    assert studio._new_take_btn.isVisibleTo(rig.app.window)
    QTest.mouseClick(studio._new_take_btn, Qt.MouseButton.LeftButton)
    assert studio._current is current
    assert not studio._viewing_live
    studio._take_list.setCurrentRow(_row_for(studio, rig.other_take))
    assert studio._current is current
    assert studio._take_list.currentRow() == _row_for(studio, rig.take)
    assert not studio.prepare_close()
    assert studio._studio_controller.document == before
    assert studio._studio_controller.dirty
    assert rig.app.window.session_hud._action.text() == 'Retry Save'
    _assert_no_audio_or_unrelated_check(rig)


def test_playback_failure_names_visible_playback_output_and_play_controls(
    tmp_path, qapp, monkeypatch,
):
    take = tmp_path / 'Take 01'
    take.mkdir()
    _wav(take / 'guitar.wav')
    _mark_verified(take, 'guitar.wav')
    original = (take / 'guitar.wav').read_bytes()
    studio = RecordingStudio(
        str(tmp_path), player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
    )
    monkeypatch.setattr('webjam_qt.windows.recording_setup.list_input_devices', lambda: [])
    setup = RecordingSetupDialog(AppSettings())
    try:
        studio._take_list.setCurrentRow(0)
        studio.resize(1024, 768)
        studio.show()
        qapp.processEvents()
        assert studio._output_picker.isVisibleTo(studio)
        assert studio._output_label.text() == 'Playback output'
        assert not any(
            'playback' in combo.accessibleName().lower()
            for combo in setup.findChildren(QComboBox)
        )
        monkeypatch.setattr(
            studio._player, 'play',
            Mock(side_effect=PlaybackDeviceError('PRIVATE_DEVICE_DETAIL')),
        )
        QTest.mouseClick(studio._play_btn, Qt.MouseButton.LeftButton)
        text = studio._hint.text()
        assert 'Recording Setup' not in text
        assert 'Playback output' in text
        assert 'Play' in text.replace('Playback output', '')
        assert 'PRIVATE' not in text
        assert studio._play_btn.text() == '▶ Play'
        assert studio._play_btn.isEnabled()
        assert studio._output_picker.isEnabled()
        assert (take / 'guitar.wav').read_bytes() == original
    finally:
        setup.reject()
        studio.shutdown()
        studio.close()
