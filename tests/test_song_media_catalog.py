"""Security and lifecycle tests for the sealed song media catalog."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

import numpy as np
import pytest
import soundfile as sf

from core.song_media_catalog import (
    SongMediaCatalog,
    SongMediaCatalogError,
)
from core.song_project import MediaProvenance, SongProject
from core.song_project_store import (
    PROJECT_MANIFEST_FILENAME,
    create_project_bundle,
    import_project_media,
    save_project_bundle,
)


RATE = 48_000


def _audio(path: Path, *, frames: int = 480, value: float = 0.25) -> None:
    data = np.full((frames, 2), value, dtype=np.float32)
    sf.write(path, data, RATE, subtype="FLOAT")


def _project(tmp_path: Path) -> tuple[Path, SongProject]:
    source = tmp_path / "source with spaces.wav"
    _audio(source)
    bundle = tmp_path / "Song with spaces.webjam"
    created = create_project_bundle(bundle, name="Catalog test")
    imported = import_project_media(
        bundle,
        created.project,
        source,
        provenance=MediaProvenance.LOCAL_FILE,
    )
    project = imported.project.designate_backing_media(imported.media.media_id)
    saved = save_project_bundle(bundle, project, expected_token=created.token)
    return bundle, saved.project


def test_catalog_seals_saved_project_and_resolves_only_durable_id(
    tmp_path: Path,
) -> None:
    bundle, project = _project(tmp_path)
    catalog = SongMediaCatalog.load(project, bundle)

    assert catalog.project_id == project.project_id
    assert catalog.project == project
    assert catalog.bundle_root == bundle.resolve()
    assert catalog.media_ids == (project.media[0].media_id,)
    assert len(catalog) == 1
    source = catalog.resolve(project.media[0].media_id)
    assert source.project_id == project.project_id
    assert source.media == project.media[0]
    assert source.path.is_file()
    catalog.require_project(project, bundle)
    catalog.assert_current()
    catalog.assert_current(verify_content=True)


def test_constructor_cannot_be_forged(tmp_path: Path) -> None:
    bundle, project = _project(tmp_path)
    with pytest.raises(SongMediaCatalogError, match="must be loaded"):
        SongMediaCatalog(
            project=project,
            bundle_root=bundle,
            bundle_identity=(0, 0, 0),
            media_root=bundle / "Media",
            media_root_identity=(0, 0, 0),
            manifest_identity=(0, 0, 0, 0, 0, 0),
            manifest_sha256="0" * 64,
            sources={},
            _authority=object(),
        )


def test_catalog_refuses_unsaved_project_revision(tmp_path: Path) -> None:
    bundle, project = _project(tmp_path)
    changed = project.add_track(name="Unsaved")
    with pytest.raises(SongMediaCatalogError, match="does not match"):
        SongMediaCatalog.load(changed, bundle)


def test_catalog_refuses_missing_or_tampered_media(tmp_path: Path) -> None:
    bundle, project = _project(tmp_path)
    member = bundle / project.media[0].path
    member.write_bytes(b"not audio")
    with pytest.raises(SongMediaCatalogError, match="trusted|failed verification"):
        SongMediaCatalog.load(project, bundle)


def test_catalog_refuses_media_symlink(tmp_path: Path) -> None:
    bundle, project = _project(tmp_path)
    member = bundle / project.media[0].path
    external = tmp_path / "external.wav"
    _audio(external)
    member.unlink()
    try:
        member.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")
    with pytest.raises(SongMediaCatalogError, match="trusted|failed verification"):
        SongMediaCatalog.load(project, bundle)


def test_catalog_detects_member_replacement_after_load(tmp_path: Path) -> None:
    bundle, project = _project(tmp_path)
    catalog = SongMediaCatalog.load(project, bundle)
    member = bundle / project.media[0].path
    replacement = tmp_path / "replacement.wav"
    _audio(replacement, value=0.5)
    os.replace(replacement, member)

    with pytest.raises(SongMediaCatalogError, match="changed"):
        _ = catalog.resolve(project.media[0].media_id).path
    with pytest.raises(SongMediaCatalogError, match="changed"):
        catalog.assert_current()


def test_catalog_detects_manifest_replacement_after_load(tmp_path: Path) -> None:
    bundle, project = _project(tmp_path)
    catalog = SongMediaCatalog.load(project, bundle)
    manifest = bundle / PROJECT_MANIFEST_FILENAME
    payload = manifest.read_text(encoding="utf-8")
    replacement = tmp_path / "manifest replacement.json"
    replacement.write_text(payload, encoding="utf-8")
    os.replace(replacement, manifest)

    with pytest.raises(SongMediaCatalogError, match="manifest changed"):
        catalog.assert_current()


def test_catalog_detects_bundle_or_media_directory_replacement(
    tmp_path: Path,
) -> None:
    bundle, project = _project(tmp_path)
    catalog = SongMediaCatalog.load(project, bundle)
    media_root = bundle / "Media"
    old_media = bundle / "OldMedia"
    media_root.rename(old_media)
    media_root.mkdir()

    with pytest.raises(SongMediaCatalogError, match="bundle changed"):
        catalog.assert_current()


def test_catalog_rejects_different_bundle_even_with_copied_bytes(
    tmp_path: Path,
) -> None:
    bundle, project = _project(tmp_path)
    catalog = SongMediaCatalog.load(project, bundle)
    copied = tmp_path / "Copied.webjam"
    shutil.copytree(bundle, copied)
    with pytest.raises(SongMediaCatalogError, match="does not match"):
        catalog.require_project(project, copied)


def test_catalog_unknown_id_and_invalid_cancel_callback_are_safe(
    tmp_path: Path,
) -> None:
    bundle, project = _project(tmp_path)
    catalog = SongMediaCatalog.load(project, bundle)
    with pytest.raises(SongMediaCatalogError, match="not present"):
        catalog.resolve("not-a-media-id")
    with pytest.raises(SongMediaCatalogError, match="callable"):
        catalog.assert_current(cancel_check=object())


def test_catalog_honors_cancellation_during_load_and_revalidation(
    tmp_path: Path,
) -> None:
    bundle, project = _project(tmp_path)

    class Cancelled(RuntimeError):
        pass

    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise Cancelled

    with pytest.raises(Cancelled):
        SongMediaCatalog.load(project, bundle, cancel_check=cancel)

    catalog = SongMediaCatalog.load(project, bundle)
    calls = 0
    with pytest.raises(Cancelled):
        catalog.assert_current(cancel_check=cancel)


def test_catalog_errors_do_not_disclose_bundle_path(tmp_path: Path) -> None:
    bundle, project = _project(tmp_path)
    secret = str(bundle)
    (bundle / project.media[0].path).unlink()
    with pytest.raises(SongMediaCatalogError) as caught:
        SongMediaCatalog.load(project, bundle)
    assert secret not in str(caught.value)
