"""Framework-neutral Studio controller state, persistence, and cancellation."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from core.studio_controller import StudioControllerError, StudioProjectController
from core.studio_project import StudioProjectError
from core.studio_store import StudioStoreError
from core.take_project import (
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
    write_take_project,
)


_NAMESPACE = uuid.UUID("59d5b73a-4c91-4710-b25f-a26806aa3cc7")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _make_take(tmp_path: Path, label: str) -> tuple[Path, TakeProject]:
    folder = tmp_path / label
    folder.mkdir(parents=True)
    media = folder / "track.wav"
    media.write_bytes(f"immutable {label} source".encode("utf-8"))
    project = TakeProject(
        session_id=_id(f"{label}:session"),
        take_id=_id(f"{label}:take"),
        session_title="Controller Test",
        take_name=label,
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48_000,
        participants=(),
        tracks=(
            ProjectTrack(
                track_id=_id(f"{label}:track"),
                source_id=_id(f"{label}:source"),
                participant_id=None,
                name="Track",
                instrument="",
                source_type=SourceType.LOCAL_ISOLATED,
                quality=SourceQuality.VERIFIED_ISOLATED,
                media_status=MediaStatus.AVAILABLE,
                order=0,
                segments=(
                    MediaSegment(
                        segment_id=_id(f"{label}:segment"),
                        path="track.wav",
                        project_start_frame=0,
                        frame_count=48_000,
                        sample_rate=48_000,
                        channels=1,
                        sample_format="PCM_24",
                        sha256=hashlib.sha256(media.read_bytes()).hexdigest(),
                        size_bytes=media.stat().st_size,
                    ),
                ),
            ),
        ),
    )
    write_take_project(folder, project)
    return folder, project


def test_load_owns_exact_path_token_document_and_generation(tmp_path: Path) -> None:
    folder, project = _make_take(tmp_path, "take-a")
    controller = StudioProjectController()

    document = controller.load(folder)
    token = controller.task_token

    assert document.take_id == project.take_id
    assert controller.document is document
    assert controller.take_path == folder.resolve()
    assert controller.store_token is None
    assert controller.dirty is False
    assert controller.generation == 1
    assert token.generation == 1
    assert token.cancelled is False
    assert controller.accepts_async_result(token) is True


def test_edits_selection_undo_redo_and_autosave_requests_are_coalesced(
    tmp_path: Path,
) -> None:
    folder, project = _make_take(tmp_path, "take-a")
    requests: list[int] = []
    controller = StudioProjectController(autosave_requested=requests.append)
    initial = controller.load(folder)
    track_id = project.tracks[0].track_id
    region_id = initial.regions[0].region_id
    controller.select_region(region_id)

    muted = controller.perform(
        "Mute track",
        lambda document: document.update_track(track_id, muted=True),
    )
    panned = controller.perform(
        "Pan track",
        lambda document: document.update_track(track_id, pan=-0.25),
    )

    assert controller.selected_track_id == track_id
    assert controller.selected_region_id == region_id
    assert controller.dirty is True
    assert controller.autosave_pending is True
    assert requests == [controller.generation]
    assert controller.can_undo is True
    assert controller.undo() is muted
    assert controller.undo() is initial
    assert controller.dirty is False
    assert controller.autosave_pending is False
    assert controller.can_redo is True

    assert controller.redo() is muted
    assert controller.dirty is True
    assert requests == [controller.generation, controller.generation]
    assert controller.redo() is panned
    assert len(requests) == 2

    assert controller.flush_autosave(requests[-1]) is True
    assert controller.dirty is False
    assert controller.autosave_pending is False
    assert controller.store_token is not None


def test_deleted_selection_is_cleared_and_invalid_ids_are_rejected(
    tmp_path: Path,
) -> None:
    folder, _project = _make_take(tmp_path, "take-a")
    controller = StudioProjectController()
    document = controller.load(folder)
    region_id = document.regions[0].region_id
    controller.select_region(region_id)

    controller.perform(
        "Delete region",
        lambda current: current.delete_region(region_id),
    )

    assert controller.selected_region_id is None
    assert controller.selected_track_id == document.regions[0].track_id
    with pytest.raises(StudioProjectError, match="UUID|not part"):
        controller.select_track("not-a-track")
    with pytest.raises(StudioProjectError, match="deleted"):
        controller.select_region(region_id)


def test_failed_save_and_exact_token_conflict_retain_pending_edits(
    tmp_path: Path,
) -> None:
    folder, project = _make_take(tmp_path, "take-a")
    first = StudioProjectController()
    second = StudioProjectController()
    first.load(folder)
    second.load(folder)
    track_id = project.tracks[0].track_id
    first.perform(
        "First writer",
        lambda document: document.update_track(track_id, pan=-0.5),
    )
    second_pending = second.perform(
        "Second writer",
        lambda document: document.update_track(track_id, muted=True),
    )

    assert first.save() is True
    assert second.save() is False
    assert second.document is second_pending
    assert second.dirty is True
    assert second.conflicted is True
    assert "changed after it was loaded" in second.last_error

    retained = second.document
    with patch(
        "core.studio_controller.save_studio_document",
        side_effect=StudioStoreError("disk is full"),
    ):
        assert second.save() is False
    assert second.document is retained
    assert second.dirty is True
    assert second.conflicted is False
    assert second.last_error == "disk is full"


def test_dirty_switch_and_reload_require_explicit_discard(tmp_path: Path) -> None:
    first_folder, first_project = _make_take(tmp_path, "take-a")
    second_folder, second_project = _make_take(tmp_path, "take-b")
    controller = StudioProjectController()
    original = controller.load(first_folder)
    original_generation = controller.generation
    original_token = controller.task_token
    controller.perform(
        "Unsaved edit",
        lambda document: document.update_track(
            first_project.tracks[0].track_id,
            muted=True,
        ),
    )

    with pytest.raises(StudioControllerError, match="unsaved edits"):
        controller.load(second_folder)
    with pytest.raises(StudioControllerError, match="unsaved edits"):
        controller.reload()

    assert controller.document.take_id == original.take_id
    assert controller.generation == original_generation
    assert controller.accepts_async_result(original_token) is True

    switched = controller.load(second_folder, discard_dirty=True)
    assert switched.take_id == second_project.take_id
    assert controller.dirty is False
    assert original_token.cancelled is True
    assert controller.accepts_async_result(original_token) is False


def test_unload_clears_loaded_state_and_rejects_document_operations(
    tmp_path: Path,
) -> None:
    folder, _project = _make_take(tmp_path, "take-a")
    controller = StudioProjectController()
    document = controller.load(folder)
    controller.select_region(document.regions[0].region_id)
    token = controller.task_token
    generation = controller.generation

    controller.unload()

    assert token.cancelled is True
    assert controller.accepts_async_result(token) is False
    assert controller.generation == generation + 1
    assert controller.take_path is None
    assert controller.store_token is None
    assert controller.dirty is False
    assert controller.autosave_pending is False
    assert controller.selected_track_id is None
    assert controller.selected_region_id is None
    assert controller.last_error == ""
    assert controller.conflicted is False
    assert controller.recovery_notice == ""
    assert controller.can_undo is False
    assert controller.can_redo is False
    with pytest.raises(StudioControllerError, match="No Studio take is loaded"):
        _ = controller.document
    with pytest.raises(StudioControllerError, match="No Studio take is loaded"):
        _ = controller.task_token
    with pytest.raises(StudioControllerError, match="No Studio take is loaded"):
        controller.reload()
    with pytest.raises(StudioControllerError, match="No Studio take is loaded"):
        controller.save()


def test_unload_protects_dirty_state_until_discard_then_allows_new_load(
    tmp_path: Path,
) -> None:
    first_folder, first_project = _make_take(tmp_path, "take-a")
    second_folder, second_project = _make_take(tmp_path, "take-b")
    controller = StudioProjectController()
    retained = controller.load(first_folder)
    token = controller.task_token
    generation = controller.generation
    controller.perform(
        "Unsaved edit",
        lambda document: document.update_track(
            first_project.tracks[0].track_id,
            muted=True,
        ),
    )

    with pytest.raises(StudioControllerError, match="unsaved edits"):
        controller.unload()

    assert controller.document.take_id == retained.take_id
    assert controller.dirty is True
    assert controller.generation == generation
    assert token.cancelled is False
    assert controller.accepts_async_result(token) is True

    controller.unload(discard_dirty=True)

    assert token.cancelled is True
    assert controller.take_path is None
    assert controller.dirty is False
    assert controller.generation == generation + 1
    loaded = controller.load(second_folder)
    assert loaded.take_id == second_project.take_id
    assert controller.take_path == second_folder.resolve()
    assert controller.dirty is False
    assert controller.task_token.cancelled is False


def test_reload_rejects_changed_take_identity_without_replacing_state(
    tmp_path: Path,
) -> None:
    folder, original_project = _make_take(tmp_path, "take-a")
    controller = StudioProjectController()
    original = controller.load(folder)
    token = controller.task_token
    replacement = replace(
        original_project,
        session_id=_id("replacement:session"),
        take_id=_id("replacement:take"),
        revision=original_project.revision + 1,
    )
    write_take_project(folder, replacement)

    with pytest.raises(StudioControllerError, match="different take"):
        controller.reload()

    assert controller.document is original
    assert controller.generation == token.generation
    assert token.cancelled is False
    assert controller.accepts_async_result(token) is True
    assert "different take" in controller.last_error


def test_generation_tokens_reject_rapid_switch_reload_and_shutdown(
    tmp_path: Path,
) -> None:
    first_folder, _first_project = _make_take(tmp_path, "take-a")
    second_folder, _second_project = _make_take(tmp_path, "take-b")
    controller = StudioProjectController()
    controller.load(first_folder)
    first_token = controller.task_token

    controller.load(second_folder)
    second_token = controller.task_token
    assert first_token.cancelled is True
    assert controller.accepts_async_result(first_token) is False
    assert controller.accepts_async_result(second_token) is True

    controller.reload()
    reloaded_token = controller.task_token
    assert second_token.cancelled is True
    assert controller.accepts_async_result(second_token) is False
    assert controller.accepts_async_result(reloaded_token) is True

    generation_before_shutdown = controller.generation
    controller.shutdown()
    assert controller.is_shutdown is True
    assert controller.generation == generation_before_shutdown + 1
    assert reloaded_token.cancelled is True
    assert controller.accepts_async_result(reloaded_token) is False
    with pytest.raises(StudioControllerError, match="shut down"):
        controller.perform("No edit", lambda document: document)
    with pytest.raises(StudioControllerError, match="shut down"):
        controller.load(first_folder)


def test_stale_autosave_generation_cannot_save_a_new_take(tmp_path: Path) -> None:
    first_folder, first_project = _make_take(tmp_path, "take-a")
    second_folder, second_project = _make_take(tmp_path, "take-b")
    requests: list[int] = []
    controller = StudioProjectController(autosave_requested=requests.append)
    controller.load(first_folder)
    controller.perform(
        "First take edit",
        lambda document: document.update_track(
            first_project.tracks[0].track_id,
            muted=True,
        ),
    )
    stale_generation = requests[-1]
    controller.load(second_folder, discard_dirty=True)
    controller.perform(
        "Second take edit",
        lambda document: document.update_track(
            second_project.tracks[0].track_id,
            solo=True,
        ),
    )

    assert controller.flush_autosave(stale_generation) is False
    assert controller.dirty is True
    assert controller.autosave_pending is True
    assert controller.store_token is None
    assert controller.flush_autosave(controller.generation) is True
    assert controller.dirty is False


def test_constructor_bounds_and_scheduler_failure_remain_recoverable(
    tmp_path: Path,
) -> None:
    with pytest.raises(StudioControllerError, match="positive integer"):
        StudioProjectController(max_history_entries=0)
    with pytest.raises(StudioControllerError, match="callable"):
        StudioProjectController(autosave_requested=object())  # type: ignore[arg-type]

    folder, project = _make_take(tmp_path, "take-a")

    def fail_scheduler(_generation: int) -> None:
        raise RuntimeError("scheduler unavailable")

    controller = StudioProjectController(autosave_requested=fail_scheduler)
    controller.load(folder)
    controller.perform(
        "Keep edit",
        lambda document: document.update_track(
            project.tracks[0].track_id,
            muted=True,
        ),
    )

    assert controller.dirty is True
    assert controller.autosave_pending is False
    assert "scheduler unavailable" in controller.last_error


@pytest.mark.parametrize("undo_first", [False, True])
def test_unconfirmed_save_retries_without_losing_history(tmp_path, undo_first):
    from core.file_io import atomic_write_bytes
    from core.studio_store import studio_state_path
    folder, project = _make_take(tmp_path, "unconfirmed")
    controller = StudioProjectController()
    initial = controller.load(folder)
    track_id = project.tracks[0].track_id
    edited = controller.perform("Pan", lambda d: d.update_track(track_id, pan=0.3))
    primary = studio_state_path(folder)
    def fail_primary_sync(path, data, *, mode=None):
        if Path(path) == primary:
            with patch("core.file_io._fsync_parent_directory", side_effect=OSError("private")):
                atomic_write_bytes(path, data, mode=mode)
        else:
            atomic_write_bytes(path, data, mode=mode)
    with patch("core.studio_store.atomic_write_bytes", side_effect=fail_primary_sync):
        assert controller.save() is False
    assert controller.document is edited
    assert controller.dirty and not controller.conflicted
    assert controller.can_undo
    assert controller.store_token == hashlib.sha256(primary.read_bytes()).hexdigest()
    assert "private" not in controller.last_error
    if undo_first:
        controller.undo()
        assert controller.document == initial
        assert controller.dirty
        assert controller.can_redo
    assert controller.save()
    reopened = StudioProjectController()
    assert reopened.load(folder) == controller.document
    assert not controller.dirty


def test_external_writer_after_unconfirmed_save_still_conflicts(tmp_path):
    folder, project = _make_take(tmp_path, "intervening")
    first = StudioProjectController()
    first.load(folder)
    track_id = project.tracks[0].track_id
    edited = first.perform("Pan", lambda d: d.update_track(track_id, pan=0.2))
    with patch("core.file_io._fsync_parent_directory", side_effect=OSError("sync failed")):
        assert first.save() is False
    other = StudioProjectController()
    other.load(folder)
    other.perform("Other pan", lambda d: d.update_track(track_id, pan=-0.2))
    assert other.save()
    assert first.save() is False
    assert first.conflicted and first.dirty
    assert first.document is edited
    reopened = StudioProjectController()
    assert reopened.load(folder) == other.document


def test_failed_primary_write_never_adopts_different_published_bytes(tmp_path):
    from core.file_io import atomic_write_bytes
    from core.studio_store import studio_state_path

    folder, project = _make_take(tmp_path, "different-publication")
    controller = StudioProjectController()
    controller.load(folder)
    controller.perform("Initial edit", lambda d: d.update_track(project.tracks[0].track_id, muted=True))
    assert controller.save()
    primary = studio_state_path(folder)
    foreign = primary.read_bytes()  # Valid source document, different from the pending edit.
    token = controller.store_token
    edited = controller.perform(
        "Pan", lambda document: document.update_track(project.tracks[0].track_id, pan=0.4)
    )

    def publish_different_then_fail(path, data, *, mode=None):
        if Path(path) == primary:
            # A concurrent publisher's valid bytes must never become our retry token.
            different = foreign.replace(b'"pan": 0.0', b'"pan": -0.2')
            assert different != foreign and different != data
            atomic_write_bytes(path, different, mode=mode)
            raise OSError("replacement not confirmed")
        atomic_write_bytes(path, data, mode=mode)

    with patch("core.studio_store.atomic_write_bytes", side_effect=publish_different_then_fail):
        assert controller.save() is False
    published = primary.read_bytes()
    assert controller.document is edited
    assert controller.dirty and controller.can_undo
    assert controller.store_token == token
    assert hashlib.sha256(published).hexdigest() != token
    assert controller.save() is False
    assert controller.conflicted and controller.dirty
    assert controller.document is edited and controller.can_undo
    assert primary.read_bytes() == published
