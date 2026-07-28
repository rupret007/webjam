"""Bounded frozen-runtime proof for standalone Reference Studio.

This module is reachable only through the frozen CI smoke hook in
``webjam_qt.app``.  It exercises the packaged project, media, schema-3 Studio,
renderer, Save As, and bounce stack without opening an audio device or the
interactive desktop.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from core.project_recording_commit import inspect_project_recording_recovery
from core.song_bounce import (
    BounceFormat,
    SongBounceCancelled,
    SongBounceEngine,
    SongBounceRequest,
)
from core.song_media_catalog import SongMediaCatalog
from core.song_project_store import (
    create_project_bundle,
    import_project_media,
    load_project_bundle,
    save_project_bundle,
)
from core.song_studio_clone import save_song_studio_project_as
from core.song_studio_store import (
    load_song_studio_document,
    save_song_studio_document,
)
from core.studio_project import (
    MarkerKind,
    StudioAutomationLane,
    StudioAutomationParameter,
    StudioAutomationPoint,
    StudioEffect,
    StudioEffectKind,
    StudioMarker,
    StudioTrackKind,
)
from core.studio_renderer import StudioRenderer
from core.studio_sections import reorder_section


SUCCESS_MARKER = "WebJam Reference Studio frozen-runtime smoke passed"
_SAMPLE_RATE = 48_000
_FRAMES = 4_800


def _write_success_marker(result_path: Path) -> None:
    path = result_path.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    parent = path.parent
    if (
        parent.parent != temporary_root
        or not parent.name.startswith("webjam-reference-studio-smoke-")
        or not parent.is_dir()
        or path.name != "result.txt"
        or path.exists()
    ):
        raise RuntimeError("Reference Studio runtime smoke result path is invalid.")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(SUCCESS_MARKER + "\n")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_backing(path: Path) -> None:
    frames = np.arange(_FRAMES, dtype=np.float32)
    left = np.sin(frames * np.float32(2.0 * np.pi * 220.0 / _SAMPLE_RATE))
    right = np.sin(frames * np.float32(2.0 * np.pi * 330.0 / _SAMPLE_RATE))
    sf.write(
        path,
        np.column_stack((left, right)),
        _SAMPLE_RATE,
        subtype="FLOAT",
    )


def _verify_artifact(path: Path, expected_sha256: str) -> None:
    if not path.is_file() or path.stat().st_size <= 44:
        raise RuntimeError("Reference Studio smoke bounce was not published.")
    if _sha256(path) != expected_sha256:
        raise RuntimeError("Reference Studio smoke bounce checksum changed.")
    with sf.SoundFile(path) as source:
        if (
            source.samplerate != _SAMPLE_RATE
            or source.channels != 2
            or source.frames <= 0
        ):
            raise RuntimeError("Reference Studio smoke bounce metadata is invalid.")


def _exercise_reference_studio(root: Path) -> None:
    source_bundle = root / "Project With Spaces.webjam"
    created = create_project_bundle(
        source_bundle,
        "Packaged Reference Studio",
        project_id=str(uuid.uuid4()),
    )
    project = created.project.add_track(
        "Lead Guitar",
        track_id=str(uuid.uuid4()),
    )
    project_save = save_project_bundle(
        source_bundle,
        project,
        expected_token=created.token,
    )

    original = root / "Owned Backing With Spaces.wav"
    _write_backing(original)
    original_hash = _sha256(original)
    imported = import_project_media(
        source_bundle,
        project_save.project,
        original,
        designate_backing=True,
        media_id=str(uuid.uuid4()),
    )
    project_save = save_project_bundle(
        source_bundle,
        imported.project,
        expected_token=project_save.token,
    )
    if _sha256(original) != original_hash:
        raise RuntimeError("Reference Studio smoke changed original media.")

    loaded_studio = load_song_studio_document(
        source_bundle,
        project_save.project,
    )
    backing = next(
        track
        for track in loaded_studio.document.tracks
        if track.kind is StudioTrackKind.BACKING
    )
    section_a = StudioMarker(
        marker_id=str(uuid.uuid4()),
        start_frame=0,
        end_frame=_FRAMES // 2,
        label="Verse",
        kind=MarkerKind.SECTION,
    )
    section_b = StudioMarker(
        marker_id=str(uuid.uuid4()),
        start_frame=_FRAMES // 2,
        end_frame=_FRAMES,
        label="Chorus",
        kind=MarkerKind.SECTION,
    )
    document = loaded_studio.document.update_track(
        backing.track_id,
        effects=(
            StudioEffect(
                effect_id=str(uuid.uuid4()),
                kind=StudioEffectKind.HPF,
                hpf_frequency_hz=40.0,
            ),
        ),
        automation=(
            StudioAutomationLane(
                lane_id=str(uuid.uuid4()),
                parameter=StudioAutomationParameter.VOLUME,
                points=(
                    StudioAutomationPoint(0, 0.8),
                    StudioAutomationPoint(_FRAMES - 1, 1.0),
                ),
            ),
        ),
    )
    document = document.upsert_marker(section_a).upsert_marker(section_b)
    document = reorder_section(document, section_a.marker_id, _FRAMES // 2)
    studio_save = save_song_studio_document(
        source_bundle,
        project_save.project,
        document,
        expected_token=loaded_studio.token,
    )
    reopened_project = load_project_bundle(source_bundle)
    reopened_studio = load_song_studio_document(
        source_bundle,
        reopened_project.project,
    )
    if (
        reopened_project.token != project_save.token
        or reopened_studio.token != studio_save.token
        or reopened_studio.document != studio_save.document
        or inspect_project_recording_recovery(source_bundle) is not None
    ):
        raise RuntimeError("Reference Studio smoke save/reopen was not exact.")

    source_catalog = SongMediaCatalog.load(
        reopened_project.project,
        source_bundle,
    )
    renderer = StudioRenderer(
        reopened_project.project,
        reopened_studio.document,
        source_bundle,
        source_catalog=source_catalog,
    )
    rendered = renderer.render_block(0, min(1_024, renderer.timeline_end_frame))
    if (
        rendered.shape[1:] != (2,)
        or not np.all(np.isfinite(rendered))
        or float(np.max(np.abs(rendered))) <= 0.0
    ):
        raise RuntimeError("Reference Studio smoke render was invalid.")

    destination_bundle = root / "Verified Copy With Spaces.webjam"
    copied = save_song_studio_project_as(
        source_bundle,
        destination_bundle,
        reopened_project.project,
        reopened_studio.document,
        expected_project_token=reopened_project.token,
        expected_studio_token=reopened_studio.token,
        new_project_id=str(uuid.uuid4()),
    )
    copied_project = load_project_bundle(destination_bundle)
    copied_studio = load_song_studio_document(
        destination_bundle,
        copied_project.project,
    )
    if (
        copied_project.token != copied.project_token
        or copied_studio.token != copied.studio_token
        or copied_studio.document != copied.document
    ):
        raise RuntimeError("Reference Studio smoke Save As was not exact.")

    copied_catalog = SongMediaCatalog.load(
        copied_project.project,
        destination_bundle,
    )
    copied_renderer = StudioRenderer(
        copied_project.project,
        copied_studio.document,
        destination_bundle,
        source_catalog=copied_catalog,
    )
    engine = SongBounceEngine()
    for audio_format in (BounceFormat.WAV, BounceFormat.FLAC):
        path = root / f"Verified Mix.{audio_format.value}"
        result = engine.bounce(
            copied_renderer,
            SongBounceRequest(
                destination=path,
                audio_format=audio_format,
                disk_reserve_bytes=0,
            ),
            generation=engine.begin(),
        )
        if len(result.artifacts) != 1:
            raise RuntimeError("Reference Studio smoke bounce inventory is invalid.")
        _verify_artifact(path, result.artifacts[0].sha256)

    cancelled_path = root / "Cancelled Mix.wav"
    cancelled = threading.Event()
    cancelled.set()
    try:
        engine.bounce(
            copied_renderer,
            SongBounceRequest(
                destination=cancelled_path,
                disk_reserve_bytes=0,
            ),
            generation=engine.begin(),
            cancel_event=cancelled,
        )
    except SongBounceCancelled:
        pass
    else:
        raise RuntimeError("Reference Studio smoke cancellation was ignored.")
    if (
        cancelled_path.exists()
        or tuple(root.glob(".webjam-bounce-*"))
        or tuple(root.glob(".webjam-bounce-backup-*"))
    ):
        raise RuntimeError("Reference Studio smoke left partial bounce output.")
    if tuple(root.glob(f".{destination_bundle.name}.*.saving")):
        raise RuntimeError("Reference Studio smoke left Save As staging output.")
    if _sha256(original) != original_hash:
        raise RuntimeError("Reference Studio smoke changed original media.")


def run_frozen_reference_studio_smoke(*, result_path: Path) -> int:
    """Exercise packaged standalone project behavior without opening Qt."""

    with tempfile.TemporaryDirectory(
        prefix="webjam-reference-studio-work-"
    ) as directory:
        _exercise_reference_studio(Path(directory))
    _write_success_marker(result_path)
    return 0


__all__ = [
    "SUCCESS_MARKER",
    "run_frozen_reference_studio_smoke",
]
