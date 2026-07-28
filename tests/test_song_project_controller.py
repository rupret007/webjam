from __future__ import annotations

import hashlib
import os
import stat
import uuid
import wave
from pathlib import Path

import pytest

import core.song_project_controller as controller_module
from core.song_project import InputMapping
from core.song_project_controller import (
    MediaVerificationState,
    SongProjectController,
    SongProjectControllerConflict,
    SongProjectControllerError,
    SongProjectNotOpen,
)
from core.song_project_store import (
    PROJECT_AUTOSAVE_FILENAME,
    SongProjectStoreError,
    load_project_bundle,
    save_project_bundle,
)


_NAMESPACE = uuid.UUID("2caa549b-7238-4394-a751-a3bd47699c2e")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _write_wav(
    path: Path,
    *,
    frames: int = 480,
    sample: int = 1_000,
) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(48_000)
        frame = int(sample).to_bytes(2, "little", signed=True) * 2
        writer.writeframes(frame * frames)
    return path.read_bytes()


def _controller(
    tmp_path: Path,
    *,
    scheduler=None,
    label: str = "Current Song",
) -> tuple[SongProjectController, Path]:
    controller = SongProjectController(
        recent_index_path=tmp_path / "settings" / "recent.json",
        autosave_scheduler=scheduler,
    )
    bundle = tmp_path / f"{label}.webjam"
    controller.create_project(
        bundle,
        label,
        project_id=_id(f"project:{label}"),
    )
    return controller, bundle


def test_create_open_close_and_not_open_contract(tmp_path: Path) -> None:
    controller, bundle = _controller(tmp_path)
    created = controller.snapshot

    assert created.is_open is True
    assert created.project is not None
    assert created.project.name == "Current Song"
    assert created.dirty is False
    assert created.token is not None
    assert created.bundle_path == bundle.resolve()

    closed = controller.close_project()
    assert closed.closed is True
    assert closed.vetoed is False
    assert closed.snapshot.is_open is False

    reopened = controller.open_project(bundle)
    assert reopened.project == created.project
    assert reopened.token == created.token
    assert reopened.dirty is False

    controller.close_project()
    with pytest.raises(SongProjectNotOpen, match="Open or create"):
        controller.add_track("Voice")


def test_project_name_tempo_and_signature_edits_are_autosaved(
    tmp_path: Path,
) -> None:
    scheduled = []
    controller, _bundle = _controller(tmp_path, scheduler=scheduled.append)
    renamed = controller.rename_project("Studio Demo")
    assert renamed.project is not None
    assert renamed.project.name == "Studio Demo"
    tempo = controller.set_tempo(137.5)
    assert tempo.project is not None
    assert tempo.project.tempo_bpm == 137.5
    signature = controller.set_time_signature(6, 8)
    assert signature.project is not None
    assert signature.project.time_signature.numerator == 6
    assert signature.project.time_signature.denominator == 8
    assert signature.dirty
    assert signature.autosave_pending
    assert len(scheduled) == 3
    with pytest.raises(SongProjectControllerError):
        controller.set_tempo(401.0)
    with pytest.raises(SongProjectControllerError):
        controller.set_time_signature(4, 3)


def test_dirty_close_is_vetoed_until_the_user_explicitly_discards(
    tmp_path: Path,
) -> None:
    controller, _bundle = _controller(tmp_path)
    controller.add_track("Voice", track_id=_id("close:voice"))
    before = controller.snapshot

    vetoed = controller.close_project(confirm_discard=lambda _snapshot: False)

    assert vetoed.closed is False
    assert vetoed.vetoed is True
    assert controller.snapshot == before

    accepted = controller.close_project(confirm_discard=lambda snapshot: snapshot.dirty)
    assert accepted.closed is True
    assert accepted.snapshot.project is None


def test_close_vetoes_if_recovery_autosave_cannot_be_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _bundle = _controller(tmp_path)
    controller.add_track("Voice", track_id=_id("close-failure:voice"))

    def fail(_folder):
        raise SongProjectStoreError("/private/user/recovery could not be removed")

    monkeypatch.setattr(controller_module, "discard_project_autosave", fail)
    result = controller.close_project(discard_unsaved=True)

    assert result.closed is False
    assert result.vetoed is True
    assert result.snapshot.is_open is True
    assert "/private/user" not in result.snapshot.autosave_error


def test_immutable_replacement_supports_undo_and_exact_dirty_state(
    tmp_path: Path,
) -> None:
    controller, bundle = _controller(tmp_path)
    saved_project = controller.snapshot.project
    assert saved_project is not None
    edited = controller.add_track(
        "Voice",
        track_id=_id("undo:voice"),
    )
    assert edited.project is not saved_project
    assert edited.dirty is True
    controller.flush_autosave()
    assert (bundle / PROJECT_AUTOSAVE_FILENAME).exists()

    undone = controller.replace_project(saved_project)

    assert undone.project is saved_project
    assert undone.dirty is False
    assert undone.autosave_pending is False
    assert not (bundle / PROJECT_AUTOSAVE_FILENAME).exists()


def test_track_add_rename_reorder_arm_and_input_mapping_are_immutable(
    tmp_path: Path,
) -> None:
    controller, _bundle = _controller(tmp_path)
    first = _id("track:first")
    second = _id("track:second")
    controller.add_track("First", track_id=first)
    controller.add_track("Second", track_id=second)
    before = controller.snapshot.project
    controller.rename_track(first, "Lead Vocal")
    controller.reorder_track(second, 0)
    controller.set_track_armed(first, True)
    mapping = InputMapping("coreaudio:Interface", (1, 2))
    final = controller.set_track_input_mapping(first, mapping)

    assert before is not final.project
    assert final.project is not None
    assert [(item.track_id, item.order) for item in final.project.tracks] == [
        (second, 0),
        (first, 1),
    ]
    vocal = final.project.tracks[1]
    assert vocal.name == "Lead Vocal"
    assert vocal.armed is True
    assert vocal.input_mapping == mapping
    assert final.dirty is True

    with pytest.raises(SongProjectControllerError, match="not found"):
        controller.rename_track(_id("missing-track"), "Missing")
    with pytest.raises(SongProjectControllerError, match="outside"):
        controller.reorder_track(first, 10)


def test_save_updates_exact_token_and_external_change_surfaces_conflict(
    tmp_path: Path,
) -> None:
    controller, bundle = _controller(tmp_path)
    initial_token = controller.snapshot.token
    controller.add_track("Voice", track_id=_id("save:voice"))

    saved = controller.save_project()

    assert saved.saved is True
    assert saved.stale is False
    assert saved.snapshot.dirty is False
    assert saved.snapshot.token != initial_token
    assert load_project_bundle(bundle).project == saved.snapshot.project

    controller.add_track("Guitar", track_id=_id("save:guitar"))
    external = load_project_bundle(bundle)
    external_project = external.project.add_track(
        "External",
        track_id=_id("save:external"),
    )
    save_project_bundle(
        bundle,
        external_project,
        expected_token=external.token,
    )

    with pytest.raises(SongProjectControllerConflict) as caught:
        controller.save_project()
    assert str(bundle) not in str(caught.value)
    assert caught.value.__cause__ is None
    assert controller.snapshot.dirty is True


def test_save_clean_project_is_a_noop(tmp_path: Path) -> None:
    controller, _bundle = _controller(tmp_path)
    before = controller.snapshot

    result = controller.save_project()

    assert result.saved is False
    assert result.snapshot == before


def test_edit_arriving_during_save_keeps_new_edit_dirty_on_new_cas_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, bundle = _controller(tmp_path, label="Save Race")
    controller.add_track("First", track_id=_id("save-race:first"))
    real_save = controller_module.save_project_bundle

    def delayed_save(*args, **kwargs):
        result = real_save(*args, **kwargs)
        controller.add_track("Second", track_id=_id("save-race:second"))
        return result

    monkeypatch.setattr(controller_module, "save_project_bundle", delayed_save)
    raced = controller.save_project()

    assert raced.saved is True
    assert raced.stale is True
    assert raced.snapshot.dirty is True
    assert [track.name for track in raced.snapshot.project.tracks] == [
        "First",
        "Second",
    ]
    assert raced.snapshot.token == load_project_bundle(bundle).token

    monkeypatch.setattr(controller_module, "save_project_bundle", real_save)
    final = controller.save_project()
    assert final.saved is True
    assert final.snapshot.dirty is False
    assert load_project_bundle(bundle).project == final.snapshot.project


def test_save_as_creates_independent_project_and_preserves_track_media_ids(
    tmp_path: Path,
) -> None:
    controller, source_bundle = _controller(tmp_path, label="Source Song")
    track_id = _id("save-as:track")
    media_id = _id("save-as:media")
    controller.add_track("Voice", track_id=track_id)
    source = tmp_path / "Audio Files" / "reference.wav"
    original = _write_wav(source, frames=720)
    imported = controller.import_backing_media(source, media_id=media_id)
    assert imported.applied
    source_snapshot = controller.snapshot
    destination = tmp_path / "Copies" / "Song Copy.webjam"

    result = controller.save_project_as(
        destination,
        new_project_id=_id("save-as:new-project"),
    )

    assert result.saved is True
    assert result.snapshot.project is not None
    assert result.snapshot.project.project_id == _id("save-as:new-project")
    assert result.snapshot.project.project_id != source_snapshot.project.project_id
    assert result.snapshot.project.tracks[0].track_id == track_id
    assert result.snapshot.project.media[0].media_id == media_id
    assert result.snapshot.project.backing_media_id == media_id
    assert result.snapshot.bundle_path == destination.resolve()
    assert result.snapshot.dirty is False
    assert source.read_bytes() == original
    assert source_bundle.exists()


def test_recovery_candidate_is_surfaced_and_never_silently_promoted(
    tmp_path: Path,
) -> None:
    writer, bundle = _controller(tmp_path, label="Recovery Song")
    base = writer.snapshot.project
    writer.add_track("Recovered Voice", track_id=_id("recovery:voice"))
    flushed = writer.flush_autosave()
    assert flushed.written is True

    reader = SongProjectController()
    opened = reader.open_project(bundle)

    assert opened.project == base
    assert opened.dirty is False
    assert opened.recovery is not None
    assert opened.recovery.project.tracks[0].name == "Recovered Voice"

    recovered = reader.recover_autosave()

    assert recovered.recovery is None
    assert recovered.project is not None
    assert recovered.project.tracks[0].name == "Recovered Voice"
    assert recovered.dirty is False
    assert not (bundle / PROJECT_AUTOSAVE_FILENAME).exists()


def test_recovery_candidate_can_be_explicitly_discarded(tmp_path: Path) -> None:
    writer, bundle = _controller(tmp_path, label="Discard Recovery")
    base = writer.snapshot.project
    writer.add_track("Temporary", track_id=_id("discard:temporary"))
    writer.flush_autosave()
    reader = SongProjectController()
    opened = reader.open_project(bundle)
    assert opened.recovery is not None

    discarded = reader.discard_recovery()

    assert discarded.recovery is None
    assert discarded.project == base
    assert discarded.dirty is False
    assert not (bundle / PROJECT_AUTOSAVE_FILENAME).exists()


def test_recovery_refuses_to_overwrite_new_current_edits(tmp_path: Path) -> None:
    writer, bundle = _controller(tmp_path, label="Recovery Versus Edits")
    writer.add_track("Autosaved", track_id=_id("versus:autosaved"))
    writer.flush_autosave()
    reader = SongProjectController()
    reader.open_project(bundle)
    reader.add_track("New Edit", track_id=_id("versus:new"))

    with pytest.raises(SongProjectControllerError, match="Discard current"):
        reader.recover_autosave()

    assert reader.snapshot.project.tracks[0].name == "New Edit"
    assert "Recover or Discard" in reader.snapshot.autosave_error
    assert (bundle / PROJECT_AUTOSAVE_FILENAME).exists()


def test_undoing_current_edits_preserves_preexisting_recovery_choice(
    tmp_path: Path,
) -> None:
    writer, bundle = _controller(tmp_path, label="Recovery Preserved")
    writer.add_track("Recovered", track_id=_id("preserved:recovered"))
    writer.flush_autosave()
    reader = SongProjectController()
    opened = reader.open_project(bundle)
    base = opened.project
    reader.add_track("Current Edit", track_id=_id("preserved:current"))

    undone = reader.replace_project(base)

    assert undone.dirty is False
    assert undone.recovery is not None
    assert (bundle / PROJECT_AUTOSAVE_FILENAME).exists()
    recovered = reader.recover_autosave()
    assert recovered.project.tracks[0].name == "Recovered"


def test_scheduler_receives_generation_bound_callback_and_flushes_explicitly(
    tmp_path: Path,
) -> None:
    callbacks = []
    controller, bundle = _controller(
        tmp_path,
        scheduler=lambda callback: callbacks.append(callback),
    )

    edited = controller.add_track(
        "Voice",
        track_id=_id("scheduler:voice"),
    )

    assert edited.autosave_pending is True
    assert len(callbacks) == 1
    result = callbacks[0]()
    assert result.written is True
    assert controller.snapshot.autosave_pending is False
    assert (bundle / PROJECT_AUTOSAVE_FILENAME).exists()


def test_stale_scheduled_autosave_callback_cannot_clear_new_pending_state(
    tmp_path: Path,
) -> None:
    callbacks = []
    controller, _bundle = _controller(
        tmp_path,
        scheduler=lambda callback: callbacks.append(callback),
    )
    controller.add_track("One", track_id=_id("scheduled-stale:one"))
    controller.add_track("Two", track_id=_id("scheduled-stale:two"))

    stale = callbacks[0]()

    assert stale.stale is True
    assert stale.written is False
    assert controller.snapshot.autosave_pending is True
    current = callbacks[1]()
    assert current.written is True
    assert controller.snapshot.autosave_pending is False


def test_autosave_schedule_and_write_failures_are_path_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = tmp_path / "private songwriter folder"

    def broken_scheduler(_callback):
        raise OSError(str(private_path))

    controller, _bundle = _controller(tmp_path, scheduler=broken_scheduler)
    scheduled = controller.add_track(
        "Voice",
        track_id=_id("schedule-failure:voice"),
    )
    assert "schedule" in scheduled.autosave_error
    assert str(private_path) not in scheduled.autosave_error

    def fail_autosave(*_args, **_kwargs):
        raise SongProjectStoreError(str(private_path))

    monkeypatch.setattr(controller_module, "write_project_autosave", fail_autosave)
    result = controller.flush_autosave()

    assert result.written is False
    assert result.error
    assert str(private_path) not in result.error
    assert result.snapshot.autosave_pending is True


def test_import_backing_media_preserves_original_and_is_path_redacted(
    tmp_path: Path,
) -> None:
    controller, _bundle = _controller(tmp_path)
    source = tmp_path / "Private Audio" / "backing track.wav"
    original = _write_wav(source, frames=888)
    os.chmod(source, 0o444)
    before = source.stat()

    result = controller.import_backing_media(
        source,
        media_id=_id("controller:backing"),
    )

    after = source.stat()
    assert result.applied is True
    assert result.verified is True
    assert result.snapshot.project is not None
    assert result.snapshot.project.backing_media_id == _id("controller:backing")
    assert result.snapshot.dirty is True
    assert source.read_bytes() == original
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert after.st_mtime_ns == before.st_mtime_ns

    missing = tmp_path / "Secret Folder" / "does-not-exist.wav"
    with pytest.raises(SongProjectControllerError) as caught:
        controller.import_backing_media(missing)
    assert str(missing) not in str(caught.value)
    assert missing.name not in str(caught.value)
    assert caught.value.__cause__ is None


def test_corrupt_backing_is_reported_then_checksum_relink_restores_it(
    tmp_path: Path,
) -> None:
    controller, bundle = _controller(tmp_path, label="Relink Song")
    source = tmp_path / "source backing.wav"
    original = _write_wav(source, frames=444, sample=1_111)
    imported = controller.import_backing_media(
        source,
        media_id=_id("relink:media"),
    )
    controller.save_project()
    assert imported.media is not None
    collected = bundle / imported.media.path
    corrupted = bytearray(collected.read_bytes())
    corrupted[-1] ^= 0x01
    collected.write_bytes(bytes(corrupted))

    with pytest.raises(SongProjectControllerError) as caught:
        controller.verify_backing_media()
    assert str(collected) not in str(caught.value)
    assert controller.snapshot.backing_media_verification is (
        MediaVerificationState.INVALID
    )

    collected.unlink()
    wrong = tmp_path / "wrong.wav"
    _write_wav(wrong, frames=444, sample=-1_111)
    with pytest.raises(SongProjectControllerError):
        controller.relink_backing_media(wrong)
    assert not collected.exists()

    relinked = controller.relink_backing_media(source)
    assert relinked.applied is True
    assert relinked.verified is True
    assert collected.read_bytes() == original
    assert source.read_bytes() == original


def test_stale_import_completion_cannot_replace_current_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _bundle = _controller(tmp_path, label="Stale Import")
    source = tmp_path / "import.wav"
    _write_wav(source)
    token = controller.generation_token()
    real_import = controller_module.import_project_media

    def delayed_import(*args, **kwargs):
        result = real_import(*args, **kwargs)
        controller.add_track(
            "Intervening Edit",
            track_id=_id("stale-import:track"),
        )
        return result

    monkeypatch.setattr(
        controller_module,
        "import_project_media",
        delayed_import,
    )
    result = controller.import_backing_media(
        source,
        generation=token,
        media_id=_id("stale-import:media"),
    )

    assert result.applied is False
    assert result.stale is True
    assert controller.snapshot.project is not None
    assert controller.snapshot.project.backing_media_id is None
    assert controller.snapshot.project.tracks[0].name == "Intervening Edit"


def test_stale_relink_and_verify_completions_do_not_update_current_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, bundle = _controller(tmp_path, label="Stale Media Work")
    source = tmp_path / "backing.wav"
    _write_wav(source)
    imported = controller.import_backing_media(
        source,
        media_id=_id("stale-media:media"),
    )
    controller.save_project()
    controller.close_project()
    controller.open_project(bundle)
    assert imported.media is not None
    collected = bundle / imported.media.path
    collected.unlink()

    relink_token = controller.generation_token()
    real_relink = controller_module.relink_project_media

    def delayed_relink(*args, **kwargs):
        result = real_relink(*args, **kwargs)
        controller.add_track(
            "Edit During Relink",
            track_id=_id("stale-relink:track"),
        )
        return result

    monkeypatch.setattr(
        controller_module,
        "relink_project_media",
        delayed_relink,
    )
    relinked = controller.relink_backing_media(
        source,
        generation=relink_token,
    )
    assert relinked.stale is True
    assert relinked.applied is False
    assert controller.snapshot.backing_media_verification is (
        MediaVerificationState.UNKNOWN
    )

    verify_token = controller.generation_token()
    real_verify = controller_module.verify_project_media

    def delayed_verify(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        controller.add_track(
            "Edit During Verify",
            track_id=_id("stale-verify:track"),
        )
        return result

    monkeypatch.setattr(
        controller_module,
        "verify_project_media",
        delayed_verify,
    )
    verified = controller.verify_backing_media(generation=verify_token)
    assert verified.stale is True
    assert verified.applied is False
    assert controller.snapshot.backing_media_verification is (
        MediaVerificationState.UNKNOWN
    )


def test_generation_from_another_controller_is_rejected_before_media_io(
    tmp_path: Path,
) -> None:
    first, _bundle = _controller(tmp_path, label="Generation One")
    second, _second_bundle = _controller(tmp_path, label="Generation Two")
    foreign = first.generation_token()
    missing = tmp_path / "must-not-be-opened.wav"

    result = second.import_backing_media(missing, generation=foreign)

    assert result.stale is True
    assert result.applied is False
    assert not missing.exists()


def test_recent_projects_are_bounded_and_most_recent_first(tmp_path: Path) -> None:
    recent_index = tmp_path / "settings" / "recent.json"
    controller = SongProjectController(recent_index_path=recent_index)
    first = tmp_path / "First Song.webjam"
    second = tmp_path / "Second Song.webjam"
    controller.create_project(first, "First", project_id=_id("recent:first"))
    controller.close_project()
    controller.create_project(second, "Second", project_id=_id("recent:second"))
    controller.close_project()
    controller.open_project(first)

    assert controller.snapshot.recent_projects == (
        first.resolve(),
        second.resolve(),
    )
    assert len(controller.snapshot.recent_projects) <= 20
    assert SongProjectController(
        recent_index_path=recent_index
    ).snapshot.recent_projects == (first.resolve(), second.resolve())


def test_corrupt_primary_backup_open_is_dirty_until_explicit_save(
    tmp_path: Path,
) -> None:
    writer, bundle = _controller(tmp_path, label="Backup Open")
    writer.add_track("Voice", track_id=_id("backup-open:voice"))
    writer.save_project()
    manifest = bundle / "webjam-project.json"
    manifest.write_bytes(b"{damaged")
    reader = SongProjectController()

    opened = reader.open_project(bundle)

    assert opened.dirty is True
    assert opened.project is not None
    assert opened.project.tracks == ()
    saved = reader.save_project()
    assert saved.saved is True
    assert saved.snapshot.dirty is False
    assert load_project_bundle(bundle).project == saved.snapshot.project


def test_musician_errors_do_not_chain_private_store_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _bundle = _controller(tmp_path)
    private = tmp_path / "Private Name" / "song.webjam"

    def fail_save(*_args, **_kwargs):
        raise SongProjectStoreError(str(private))

    controller.add_track("Voice", track_id=_id("redaction:voice"))
    monkeypatch.setattr(controller_module, "save_project_bundle", fail_save)

    with pytest.raises(SongProjectControllerError) as caught:
        controller.save_project()

    assert str(private) not in str(caught.value)
    assert private.name not in str(caught.value)
    assert caught.value.__cause__ is None


def test_project_controller_never_hashes_or_mutates_jamulus_state(
    tmp_path: Path,
) -> None:
    controller, _bundle = _controller(tmp_path)
    before = hashlib.sha256(repr(controller.snapshot).encode()).hexdigest()
    controller.add_track("Voice", track_id=_id("neutral:voice"))
    after = hashlib.sha256(repr(controller.snapshot).encode()).hexdigest()

    assert before != after
    assert "jamulus" not in vars(controller)
