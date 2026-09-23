from dataclasses import FrozenInstanceError
from pathlib import Path
import re
from unittest.mock import Mock

import pytest
from tests.test_art_room_controller import qapp as _qapp_fixture
from tests.test_recording_studio import _schema2_studio_take, _schema2_repeated_take, _SilentSink, RATE
from core import studio_controller as owner_module
from core.creative_modes import get_creator_profile_by_key_or_default
from core.studio_store import StudioStoreError
from core.take_player import TakePlayer
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.recording_studio import RecordingStudio

qapp = _qapp_fixture


@pytest.fixture
def review(tmp_path, qapp, monkeypatch):
    take, _ = _schema2_studio_take(tmp_path)
    other, _ = _schema2_repeated_take(tmp_path, take)
    studio = RecordingStudio(str(tmp_path), player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
    studio._take_list.setCurrentRow(next(i for i,t in enumerate(studio._takes) if t.path == take))
    studio._lanes[1]._pan.setValue(35)
    original = owner_module.save_studio_document
    with monkeypatch.context() as patch:
        patch.setattr(owner_module, 'save_studio_document', Mock(side_effect=StudioStoreError('blocked')))
        assert not studio._flush_studio_state()
    yield studio, other
    with monkeypatch.context() as patch:
        patch.setattr(owner_module, 'save_studio_document', original)
        studio.shutdown()
    studio.close()


def test_retry_api_token_is_immutable_and_saves_only_exact_failed_document(review):
    studio, _ = review
    context = studio.studio_save_retry_context()
    assert context is not None
    assert context == studio.studio_save_retry_context()
    with pytest.raises(FrozenInstanceError):
        context.document_revision += 1
    assert not studio.retry_studio_save(None)
    assert studio.retry_studio_save(context)
    assert not studio._studio_controller.dirty
    assert not studio._studio_persistence_failed
    assert studio.studio_save_retry_context() is None
    assert not studio.retry_studio_save(context)


@pytest.mark.parametrize('change', ['edit', 'selected_take', 'profile', 'live', 'export', 'shutdown'])
def test_changed_retry_context_does_not_write(review, monkeypatch, change):
    studio, other = review
    context = studio.studio_save_retry_context()
    assert context is not None
    if change == 'edit':
        studio._lanes[1]._pan.setValue(40)
    elif change == 'selected_take':
        studio._take_list.setCurrentRow(next(i for i,t in enumerate(studio._takes) if t.path == other))
        studio._lanes[0]._pan.setValue(20)
        with monkeypatch.context() as patch:
            patch.setattr(owner_module, 'save_studio_document', Mock(side_effect=StudioStoreError('blocked')))
            assert not studio._flush_studio_state()
    elif change == 'profile':
        studio.set_creator_profile(get_creator_profile_by_key_or_default('review_rehearsal'))
    elif change == 'live':
        studio._show_live_session()
    elif change == 'export':
        studio._exporting = True
    else:
        assert studio.shutdown()
    studio._studio_state_save_timer.stop()
    write = Mock()
    with monkeypatch.context() as patch:
        patch.setattr(owner_module, 'save_studio_document', write)
        assert context != studio.studio_save_retry_context()
        assert not studio.retry_studio_save(context)
        write.assert_not_called()
    studio._exporting = False


def test_published_unconfirmed_save_uses_the_current_owner_token_on_retry(review, monkeypatch):
    studio, _ = review
    previous = studio.studio_save_retry_context()
    with monkeypatch.context() as patch:
        patch.setattr('core.file_io._fsync_parent_directory', Mock(side_effect=OSError('unconfirmed')))
        assert not studio._flush_studio_state()
    owner = studio._studio_controller
    assert owner.dirty
    assert owner.store_token != studio._studio_state_token
    current = studio.studio_save_retry_context()
    assert current is not None and current != previous
    assert not studio.retry_studio_save(previous)
    assert studio.retry_studio_save(current)
    assert not owner.dirty


@pytest.mark.parametrize('scale', [1.0, 1.25])
@pytest.mark.parametrize('kind', ['save', 'export', 'published_export'])
def test_compact_recovery_text_wraps_inside_supported_geometry(review, qapp, kind, scale):
    studio, _ = review
    stylesheet = load_stylesheet()
    studio.setStyleSheet(stylesheet)
    studio.ensurePolished()
    normal_line_spacing = studio._hint.fontMetrics().lineSpacing()
    # QSS pins pixel sizes, so scale those rules and verify effective metrics.
    if scale != 1:
        stylesheet = re.sub(
            r"(font-size:\s*)([0-9.]+)px",
            lambda match: f"{match[1]}{float(match[2]) * scale:g}px",
            stylesheet,
        )
    studio.setStyleSheet(stylesheet)
    if kind == 'export':
        studio._finish_export(None, 'PRIVATE', studio_export_attempted=True)
    elif kind == 'published_export':
        studio._finish_export(None, 'PRIVATE', published_folder=Path('/tmp/unverified-test-export'))
    studio.resize(760, 600)
    studio.show()
    qapp.processEvents()
    qapp.processEvents()
    if scale > 1:
        assert studio._hint.fontMetrics().lineSpacing() > normal_line_spacing
    assert studio.size().toTuple() == (760, 600)
    assert studio.minimumSizeHint().width() <= 760
    assert studio.minimumSizeHint().height() <= 600
    assert studio._hint.wordWrap()
    assert studio._hint.height() >= studio._hint.heightForWidth(studio._hint.width())
    assert studio._studio_arrange.height() >= 150
    assert studio._track_scroll.height() >= 88
    assert studio._hint.geometry().bottom() <= studio._hint.parentWidget().contentsRect().bottom()
    for control in (studio._play_btn, studio._output_picker, studio._export_btn, studio._setup_btn):
        assert control.isVisibleTo(studio)
        assert studio.contentsRect().contains(control.mapTo(studio, control.rect().bottomRight()))
