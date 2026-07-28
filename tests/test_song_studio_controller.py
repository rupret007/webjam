"""Workflow coverage for the schema-3 song Studio controller."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from core.song_project import SongProject
from core.song_project_store import create_project_bundle, save_project_bundle
from core.song_studio_controller import (
    SongStudioController,
    SongStudioControllerError,
)
from core.song_studio_store import (
    SONG_STUDIO_AUTOSAVE_FILENAME,
    load_song_studio_document,
    write_song_studio_autosave,
)
from core.studio_project import StudioDocument, StudioTrack


_NAMESPACE = uuid.UUID("0aabdb9c-ad06-4640-b0ed-c9971967a400")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _create(
    tmp_path: Path,
    label: str = "Controller Song",
) -> tuple[Path, SongProject]:
    bundle = tmp_path / f"{label}.webjam"
    created = create_project_bundle(
        bundle,
        label,
        project_id=_id(f"project:{label}"),
    )
    project = created.project.add_track(
        "Voice",
        track_id=_id(f"track:{label}:voice"),
    )
    saved = save_project_bundle(
        bundle,
        project,
        expected_token=created.token,
    )
    return bundle, saved.project


def test_edit_autosave_explicit_save_and_reopen(tmp_path: Path) -> None:
    bundle, project = _create(tmp_path)
    requests: list[int] = []
    controller = SongStudioController(autosave_requested=requests.append)
    initial = controller.load(bundle, project)
    token = controller.task_token

    edited = controller.perform(
        "Lower voice",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            fader_gain=0.75,
        ),
    )

    assert edited is controller.document
    assert controller.dirty is True
    assert controller.autosave_pending is True
    assert requests == [controller.generation]
    assert controller.accepts_async_result(token)
    assert controller.flush_autosave(requests[0]) is True
    assert controller.dirty is True
    assert controller.autosave_pending is False
    assert controller.autosave_token is not None
    assert (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()

    assert controller.save() is True
    assert controller.dirty is False
    assert controller.store_token is not None
    assert controller.autosave_token is None
    assert not (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()
    assert token.cancelled is True

    reopened = SongStudioController()
    assert reopened.load(bundle, project) == edited
    assert reopened.document != initial
    assert reopened.dirty is False
    assert reopened.request_close() is True
    assert reopened.is_shutdown is True


def test_two_controllers_use_exact_cas_and_retain_loser_state(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path)
    first = SongStudioController()
    second = SongStudioController()
    first.load(bundle, project)
    second.load(bundle, project)

    first.perform(
        "First writer",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            pan=-0.25,
        ),
    )
    second.perform(
        "Second writer",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            pan=0.25,
        ),
    )
    loser_document = second.document

    assert first.save() is True
    assert second.save() is False
    assert second.conflicted is True
    assert second.dirty is True
    assert second.document is loser_document
    assert "changed after" in second.last_error
    assert second.request_close() is False
    assert second.request_close(discard_dirty=True) is True


def test_recovery_must_be_explicit_before_editing(tmp_path: Path) -> None:
    bundle, project = _create(tmp_path)
    writer = SongStudioController(autosave_requested=lambda _generation: None)
    primary = writer.load(bundle, project)
    writer.perform(
        "Save baseline",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            fader_gain=0.9,
        ),
    )
    assert writer.save()
    writer.perform(
        "Crash edit",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            fader_gain=1.4,
        ),
    )
    crash_document = writer.document
    assert writer.flush_autosave(writer.generation)
    writer.shutdown()

    recovered = SongStudioController()
    loaded_primary = recovered.load(bundle, project)
    assert loaded_primary != crash_document
    assert recovered.recovery_candidate is not None
    assert "explicit recovery" in recovered.recovery_notice
    with pytest.raises(SongStudioControllerError, match="Recover or discard"):
        recovered.perform("Blocked", lambda document: document)
    assert recovered.request_close() is False

    old_token = recovered.task_token
    assert recovered.recover_autosave() == crash_document
    assert recovered.recovery_candidate is None
    assert recovered.dirty is False
    assert old_token.cancelled is True
    assert load_song_studio_document(bundle, project).document == crash_document
    assert primary.project_id == crash_document.project_id


def test_explicit_discard_clears_stale_recovery_without_changing_primary(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path)
    primary = load_song_studio_document(bundle, project)
    saved_controller = SongStudioController()
    saved_controller.load(bundle, project)
    saved_controller.perform(
        "Baseline",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            muted=True,
        ),
    )
    assert saved_controller.save()
    saved = saved_controller.document
    saved_token = saved_controller.store_token
    dirty = saved.update_track(saved.tracks[0].track_id, muted=False)
    write_song_studio_autosave(
        bundle,
        project,
        dirty,
        base_primary_token=saved_token,
    )

    controller = SongStudioController()
    controller.load(bundle, project)
    assert controller.recovery_candidate is not None
    controller.discard_recovery()

    assert controller.recovery_candidate is None
    assert controller.recovery_requires_discard is False
    assert not (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()
    assert controller.document == saved
    assert controller.document != primary.document
    controller.perform("Now editable", lambda document: document)


def test_history_is_bounded_and_rejects_schema_or_project_switch_edits(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path)
    controller = SongStudioController(max_history_entries=1)
    initial = controller.load(bundle, project)
    track_id = initial.tracks[0].track_id
    first = controller.perform(
        "First",
        lambda document: document.update_track(track_id, fader_gain=1.1),
    )
    second = controller.perform(
        "Second",
        lambda document: document.update_track(track_id, fader_gain=1.2),
    )
    assert controller.undo_depth == 1
    assert controller.undo() is first
    assert controller.undo() is first
    assert controller.redo() is second

    before = controller.document
    foreign = replace(
        before,
        project_id=_id("foreign-project"),
        revision=before.revision + 1,
    )
    with pytest.raises(SongStudioControllerError, match="identity"):
        controller.perform("Foreign", lambda _document: foreign)
    assert controller.document is before

    legacy = StudioDocument(
        session_id=_id("legacy-session"),
        take_id=_id("legacy-take"),
        project_sample_rate=project.project_sample_rate,
        tracks=(StudioTrack(_id("legacy-track")),),
        revision=before.revision + 1,
    )
    with pytest.raises(SongStudioControllerError, match="schema-3"):
        controller.perform("Legacy", lambda _document: legacy)
    assert controller.document is before


def test_old_generations_cannot_autosave_or_publish_async_edits(
    tmp_path: Path,
) -> None:
    first_bundle, first_project = _create(tmp_path, "First Generation")
    second_bundle, second_project = _create(tmp_path, "Second Generation")
    requested: list[int] = []
    controller = SongStudioController(autosave_requested=requested.append)
    first_document = controller.load(first_bundle, first_project)
    old_token = controller.task_token
    old_generation = controller.generation

    controller.load(second_bundle, second_project)

    assert old_token.cancelled is True
    assert controller.accepts_async_result(old_token) is False
    assert controller.flush_autosave(old_generation) is False
    with pytest.raises(SongStudioControllerError, match="stale generation"):
        controller.apply_async_edit(
            old_token,
            "Late result",
            lambda document: document.update_track(
                document.tracks[0].track_id,
                muted=True,
            ),
        )
    assert controller.document.project_id == second_project.project_id
    assert controller.document != first_document
    assert not (second_bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()


def test_reload_and_close_veto_preserve_dirty_snapshot(tmp_path: Path) -> None:
    bundle, project = _create(tmp_path)
    controller = SongStudioController()
    controller.load(bundle, project)
    controller.perform(
        "Dirty",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            solo=True,
        ),
    )
    retained = controller.document

    with pytest.raises(SongStudioControllerError, match="unsaved edits"):
        controller.reload()
    assert controller.document is retained
    assert controller.request_close() is False
    assert controller.document is retained
    assert controller.request_close(discard_dirty=True) is True
    assert controller.is_shutdown is True


def test_save_after_undo_to_primary_discards_obsolete_autosave(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path)
    controller = SongStudioController(autosave_requested=lambda _generation: None)
    controller.load(bundle, project)
    controller.perform(
        "Baseline",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            fader_gain=0.9,
        ),
    )
    assert controller.save()
    baseline = controller.document
    controller.perform(
        "Temporary",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            fader_gain=1.4,
        ),
    )
    assert controller.flush_autosave(controller.generation)
    assert (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()

    assert controller.undo() == baseline
    assert controller.dirty is False
    assert controller.autosave_token is not None
    assert controller.save() is True
    assert controller.autosave_token is None
    assert not (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()
    assert controller.request_close() is True


def test_controller_refuses_non_song_project_and_hides_scheduler_details(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path, "Private Path Project")
    controller = SongStudioController(
        autosave_requested=lambda _generation: (_ for _ in ()).throw(
            RuntimeError(str(bundle))
        )
    )
    with pytest.raises(SongStudioControllerError, match="SongProject"):
        controller.load(bundle, object())  # type: ignore[arg-type]

    controller.load(bundle, project)
    controller.perform(
        "Schedule",
        lambda document: document.update_track(
            document.tracks[0].track_id,
            muted=True,
        ),
    )
    assert controller.autosave_pending is False
    assert str(bundle) not in controller.last_error
    assert "Private Path Project" not in controller.last_error
