"""Safe, non-destructive multitrack exports from a WebJam take.

WebJam does not create projects for another editor.  It prepares a portable
track package instead: one 24-bit WAV per track, all rendered onto the same
zero-based timeline, plus a stereo reference mix and a small evidence
manifest.  Original recorder files are never modified.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from core.take_library import TakeInfo


class TakeExportError(RuntimeError):
    """Raised when a take cannot be exported without compromising alignment."""


@dataclass(frozen=True)
class TrackMixSettings:
    """Non-destructive rough-mix state for one Studio lane."""

    gain: float = 1.0
    pan: float = 0.0
    muted: bool = False
    solo: bool = False


MixSettings = Mapping[int | str, TrackMixSettings]


@dataclass(frozen=True)
class TrackExportResult:
    """Files produced by :func:`export_track_package`."""

    folder: Path
    stems: tuple[Path, ...]
    mixdown: Path
    manifest: Path
    instructions: Path
    samplerate: int
    frames: int
    reference_mix: Path | None = None
    processed_stems: tuple[Path, ...] = ()
    alignment_report: Path | None = None
    recording_report: Path | None = None
    checksums: Path | None = None
    analysis: Path | None = None
    source_manifest: Path | None = None


# Kept for callers from the pre-editor-neutral API.  New code should use
# ``TrackExportResult`` and ``export_track_package``.
LogicExportResult = TrackExportResult


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._() -]+")
_PEER_VERIFIED_ALIGNMENT_PREFIX = "peer-local-original-verified-alignment/"
_PEER_ALIGNMENT_MIN_CONFIDENCE = 0.85
_PEER_ALIGNMENT_MAX_RESIDUAL_MS = 2.0
_PEER_ALIGNMENT_MIN_ANCHORS = 3


def _safe_name(value: str, fallback: str) -> str:
    cleaned = _UNSAFE_NAME.sub("-", str(value or "")).strip(" .-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or fallback)[:80]


def _next_export_folder(root: Path) -> Path:
    candidate = root / "Track Export"
    number = 2
    while candidate.exists():
        candidate = root / f"Track Export {number}"
        number += 1
    return candidate


def _write_aligned_stem(
    source: Path,
    destination: Path,
    *,
    offset_frames: int,
    total_frames: int,
    samplerate: int,
    chunk_frames: int,
) -> int:
    """Render one source file onto the common take timeline."""
    import numpy as np
    import soundfile as sf  # type: ignore

    with sf.SoundFile(str(source)) as reader:
        if int(reader.samplerate) != samplerate:
            raise TakeExportError(
                f"{source.name} is {reader.samplerate} Hz; all tracks must use "
                f"{samplerate} Hz before export."
            )
        channels = max(1, int(reader.channels))
        with sf.SoundFile(
            str(destination),
            mode="w",
            samplerate=samplerate,
            channels=channels,
            format="WAV",
            subtype="PCM_24",
        ) as writer:
            timeline_pos = 0
            if offset_frames > 0:
                remaining = min(offset_frames, total_frames)
                while remaining:
                    count = min(chunk_frames, remaining)
                    writer.write(np.zeros((count, channels), dtype="float32"))
                    timeline_pos += count
                    remaining -= count
            elif offset_frames < 0:
                reader.seek(min(len(reader), -offset_frames))

            while timeline_pos < total_frames:
                count = min(chunk_frames, total_frames - timeline_pos)
                block = reader.read(count, dtype="float32", always_2d=True)
                if block.shape[0] == 0:
                    break
                writer.write(block)
                timeline_pos += int(block.shape[0])

            while timeline_pos < total_frames:
                count = min(chunk_frames, total_frames - timeline_pos)
                writer.write(np.zeros((count, channels), dtype="float32"))
                timeline_pos += count
    os.chmod(destination, 0o600)
    return channels


def _to_stereo(block, pan: float):
    """Apply a DAW-style pan/balance control to a mono or stereo block."""
    value = max(-1.0, min(1.0, float(pan)))
    if block.shape[1] == 1:
        mono = block[:, 0]
        import numpy as np
        return np.column_stack(
            (
                mono * (1.0 - max(0.0, value)),
                mono * (1.0 + min(0.0, value)),
            )
        )
    stereo = block[:, :2].copy()
    if value < 0:
        stereo[:, 1] *= 1.0 + value
    elif value > 0:
        stereo[:, 0] *= 1.0 - value
    return stereo


def _write_rough_mix(
    stems: list[Path],
    destination: Path,
    settings: list[TrackMixSettings],
    *,
    samplerate: int,
    total_frames: int,
    chunk_frames: int,
) -> None:
    import numpy as np
    import soundfile as sf  # type: ignore

    readers = [sf.SoundFile(str(path)) for path in stems]
    try:
        any_solo = any(item.solo for item in settings)
        with sf.SoundFile(
            str(destination),
            mode="w",
            samplerate=samplerate,
            channels=2,
            format="WAV",
            subtype="PCM_24",
        ) as writer:
            remaining = total_frames
            while remaining:
                count = min(chunk_frames, remaining)
                mix = np.zeros((count, 2), dtype="float32")
                for reader, state in zip(readers, settings):
                    block = reader.read(count, dtype="float32", always_2d=True)
                    if block.shape[0] < count:
                        block = np.pad(
                            block,
                            ((0, count - block.shape[0]), (0, 0)),
                        )
                    audible = not state.muted and (state.solo or not any_solo)
                    if audible and state.gain > 0:
                        mix += _to_stereo(block, state.pan) * max(
                            0.0, float(state.gain)
                        )
                np.clip(mix, -1.0, 1.0, out=mix)
                writer.write(mix)
                remaining -= count
    finally:
        for reader in readers:
            reader.close()
    os.chmod(destination, 0o600)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _inspect_audio(path: Path) -> dict:
    """Return bounded-streaming evidence for an independently playable WAV."""
    import numpy as np
    import soundfile as sf  # type: ignore

    peak = 0.0
    sum_squares = 0.0
    samples = 0
    clipped = 0
    with sf.SoundFile(str(path)) as reader:
        rate = int(reader.samplerate)
        channels = int(reader.channels)
        frames = int(len(reader))
        while True:
            block = reader.read(65_536, dtype="float32", always_2d=True)
            if not len(block):
                break
            absolute = np.abs(block)
            peak = max(peak, float(np.max(absolute)))
            sum_squares += float(np.dot(block.ravel(), block.ravel()))
            samples += int(block.size)
            clipped += int(np.count_nonzero(absolute >= 0.999969))
    return {
        "filename": path.name,
        "sample_rate": rate,
        "channels": channels,
        "frames": frames,
        "duration_s": round(frames / rate, 9),
        "peak": round(peak, 9),
        "rms": round((sum_squares / samples) ** 0.5 if samples else 0.0, 9),
        "clipped_samples": clipped,
        "sha256": _sha256(path),
    }


def _write_processed_stem(
    source: Path,
    destination: Path,
    state: TrackMixSettings,
    *,
    samplerate: int,
    chunk_frames: int,
) -> None:
    import numpy as np
    import soundfile as sf  # type: ignore

    with sf.SoundFile(str(source)) as reader, sf.SoundFile(
        str(destination),
        mode="w",
        samplerate=samplerate,
        channels=2,
        format="WAV",
        subtype="PCM_24",
    ) as writer:
        while True:
            block = reader.read(chunk_frames, dtype="float32", always_2d=True)
            if not len(block):
                break
            processed = _to_stereo(block, state.pan) * max(0.0, float(state.gain))
            if state.muted:
                processed.fill(0.0)
            np.clip(processed, -1.0, 1.0, out=processed)
            writer.write(processed)
    os.chmod(destination, 0o600)


def _render_segment_block(
    reader,
    *,
    output_start: int,
    output_count: int,
    segment_start: int,
    segment_end: int,
    source_frames: int,
    source_rate: int,
    project_rate: int,
    drift_scale: float,
    gaps=(),
):
    """Render one overlap using deterministic linear time conversion.

    Linear conversion is intentionally identified in the export evidence.  It
    is bounded, deterministic across platforms, and applies the same affine
    drift transform used by Studio without rewriting the immutable source.
    """
    import numpy as np

    overlap_start = max(output_start, segment_start)
    overlap_end = min(output_start + output_count, segment_end)
    if overlap_end <= overlap_start:
        return None
    output_positions = np.arange(overlap_start, overlap_end, dtype=np.float64)
    source_positions = (
        (output_positions - segment_start)
        / project_rate
        / drift_scale
        * source_rate
    )
    source_positions = np.clip(source_positions, 0.0, max(0.0, source_frames - 1.0))
    first = max(0, int(np.floor(source_positions[0])) - 1)
    last = min(source_frames, int(np.ceil(source_positions[-1])) + 2)
    reader.seek(first)
    source = reader.read(last - first, dtype="float32", always_2d=True)
    if not len(source):
        return None
    local_positions = source_positions - first
    grid = np.arange(len(source), dtype=np.float64)
    rendered = np.empty((len(output_positions), source.shape[1]), dtype=np.float32)
    for channel in range(source.shape[1]):
        rendered[:, channel] = np.interp(
            local_positions, grid, source[:, channel]
        ).astype(np.float32)
    # Capture gaps describe source frames that were durably unavailable.  They
    # must remain silence after rate/drift conversion exactly as they do in
    # Studio playback; exporting interpolated neighboring samples here would
    # falsely manufacture audio inside a disclosed dropout.
    for gap in gaps:
        gap_start = int(getattr(gap, "start_frame", 0))
        gap_count = int(getattr(gap, "frame_count", 0))
        gap_channels = tuple(getattr(gap, "channels", ()) or ())
        inside = (source_positions >= gap_start) & (
            source_positions < gap_start + gap_count
        )
        if not np.any(inside):
            continue
        targets = gap_channels or tuple(range(rendered.shape[1]))
        for channel in targets:
            if 0 <= channel < rendered.shape[1]:
                rendered[inside, channel] = 0.0
    destination_start = overlap_start - output_start
    return destination_start, rendered


def _write_project_track(
    take_root: Path,
    track,
    destination: Path,
    *,
    project_rate: int,
    total_frames: int,
    chunk_frames: int,
) -> int:
    """Render explicit immutable segments through alignment/drift metadata."""
    import numpy as np
    import soundfile as sf  # type: ignore

    channels = {int(segment.channels) for segment in track.segments}
    if len(channels) != 1:
        raise TakeExportError(
            f"{track.name} changes channel configuration between segments; "
            "review that track before export."
        )
    channel_count = channels.pop()
    drift_scale = 1.0 + float(track.alignment.drift_ppm) / 1_000_000.0
    if drift_scale <= 0.0:
        raise TakeExportError(f"{track.name} has an invalid drift transform.")
    offset_frames = int(
        round(float(track.alignment.effective_offset_s) * project_rate)
    )
    prepared: list[tuple[object, object, int, int]] = []
    intervals: list[tuple[int, int]] = []
    try:
        for segment in sorted(
            track.segments, key=lambda item: (item.project_start_frame, item.segment_id)
        ):
            source = (take_root / segment.path).resolve()
            try:
                source.relative_to(take_root)
            except ValueError as exc:
                raise TakeExportError(
                    f"{track.name} points outside its take folder."
                ) from exc
            if not source.is_file():
                raise TakeExportError(f"{track.name} is missing {segment.path}.")
            if segment.size_bytes and source.stat().st_size != segment.size_bytes:
                raise TakeExportError(f"{track.name} changed size after validation.")
            if segment.sha256 and _sha256(source) != segment.sha256:
                raise TakeExportError(f"{track.name} changed after validation.")
            reader = sf.SoundFile(str(source))
            observed = (
                int(reader.samplerate),
                int(reader.channels),
                int(len(reader)),
            )
            declared = (
                int(segment.sample_rate),
                int(segment.channels),
                int(segment.frame_count),
            )
            if observed != declared:
                reader.close()
                raise TakeExportError(
                    f"{track.name} media facts changed after validation."
                )
            start = int(segment.project_start_frame) + offset_frames
            rendered_frames = int(
                round(segment.frame_count / segment.sample_rate * drift_scale * project_rate)
            )
            end = start + max(0, rendered_frames)
            if intervals and start < intervals[-1][1]:
                reader.close()
                raise TakeExportError(
                    f"{track.name} contains overlapping reconnect segments."
                )
            intervals.append((start, end))
            prepared.append((segment, reader, start, end))

        with sf.SoundFile(
            str(destination),
            mode="w",
            samplerate=project_rate,
            channels=channel_count,
            format="WAV",
            subtype="PCM_24",
        ) as writer:
            output_start = 0
            while output_start < total_frames:
                count = min(chunk_frames, total_frames - output_start)
                block = np.zeros((count, channel_count), dtype=np.float32)
                for segment, reader, start, end in prepared:
                    rendered = _render_segment_block(
                        reader,
                        output_start=output_start,
                        output_count=count,
                        segment_start=start,
                        segment_end=end,
                        source_frames=int(segment.frame_count),
                        source_rate=int(segment.sample_rate),
                        project_rate=project_rate,
                        drift_scale=drift_scale,
                        gaps=segment.gaps,
                    )
                    if rendered is not None:
                        destination_start, audio = rendered
                        block[destination_start : destination_start + len(audio)] = audio
                writer.write(block)
                output_start += count
    finally:
        for _segment, reader, _start, _end in prepared:
            reader.close()
    os.chmod(destination, 0o600)
    return channel_count


def _project_timeline_frames(project) -> int:
    latest = 0
    for track in project.tracks:
        scale = 1.0 + float(track.alignment.drift_ppm) / 1_000_000.0
        offset = int(round(track.alignment.effective_offset_s * project.project_sample_rate))
        for segment in track.segments:
            duration = int(
                round(
                    segment.frame_count
                    / segment.sample_rate
                    * scale
                    * project.project_sample_rate
                )
            )
            latest = max(latest, segment.project_start_frame + offset + duration)
    return latest


def _reference_fingerprint(track) -> str:
    """Match the immutable peer-reference fingerprint recorded at alignment."""

    digest = hashlib.sha256()
    for segment in sorted(
        tuple(track.segments), key=lambda item: item.segment_id
    ):
        digest.update(str(segment.segment_id).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(segment.sha256 or "").lower().encode("ascii"))
        digest.update(b"\0")
        digest.update(str(segment.frame_count).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(segment.sample_rate).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(segment.channels).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _peer_reference_is_still_verified(track, all_tracks, take_root: Path) -> bool:
    """Require the exact source reference and its immutable bytes at export."""

    from core.take_project import MediaStatus, SourceQuality, SourceType

    alignment = track.alignment
    reference_id = str(alignment.reference_track_id or "")
    fingerprint = str(alignment.reference_fingerprint_sha256 or "").lower()
    if not reference_id or not fingerprint:
        return False
    references = [
        candidate
        for candidate in all_tracks
        if candidate.track_id == reference_id
        and candidate.participant_id == track.participant_id
        and candidate.source_type is SourceType.JAMULUS_SERVER
        and candidate.quality is SourceQuality.NETWORK_TRACK
    ]
    if len(references) != 1:
        return False
    reference = references[0]
    if _reference_fingerprint(reference) != fingerprint:
        return False
    if reference.media_status is not MediaStatus.AVAILABLE:
        return False
    for segment in reference.segments:
        if segment.media_status is not MediaStatus.AVAILABLE or not segment.sha256:
            return False
        source = (take_root / segment.path).resolve()
        try:
            source.relative_to(take_root)
        except ValueError:
            return False
        try:
            if (
                not source.is_file()
                or (segment.size_bytes and source.stat().st_size != segment.size_bytes)
                or _sha256(source) != segment.sha256
            ):
                return False
        except OSError:
            return False
    return True


def _unaligned_local_original_names(
    tracks,
    *,
    all_tracks=(),
    take_root: Path | None = None,
) -> list[str]:
    """Return local originals that cannot truthfully share the export timeline.

    A transferred guest original is timing-ready only when the peer attachment
    path records both the explicit verified-alignment provenance and
    ``VERIFIED_ISOLATED`` quality.  An uncertain transient result may retain a
    positive confidence and useful anchors for Studio review, but must not be
    rendered as a trustworthy zero-origin performance stem.  The host's
    existing local-capture path may remain ``UNVERIFIED`` while carrying a
    positive automatic alignment confidence, so this stricter rule is scoped
    to peer-origin provenance rather than quality alone.
    """
    from core.take_project import SourceQuality, SourceType

    blocked: list[str] = []
    for track in tracks:
        if track.source_type is not SourceType.LOCAL_ISOLATED:
            continue
        confidence = float(track.alignment.confidence)
        method = str(track.alignment.method or "").strip().lower()
        peer_timing_ready = (
            method.startswith(_PEER_VERIFIED_ALIGNMENT_PREFIX)
            and track.quality is SourceQuality.VERIFIED_ISOLATED
            and confidence >= _PEER_ALIGNMENT_MIN_CONFIDENCE
            and float(track.alignment.residual_ms)
            <= _PEER_ALIGNMENT_MAX_RESIDUAL_MS
            and len(track.alignment.anchors) >= _PEER_ALIGNMENT_MIN_ANCHORS
            and take_root is not None
            and _peer_reference_is_still_verified(
                track,
                all_tracks,
                take_root,
            )
        )
        peer_original = method.startswith("peer-local-original")
        if confidence <= 0.0 or (peer_original and not peer_timing_ready):
            blocked.append(track.name)
    return blocked


def _explicitly_silent_track_names(tracks) -> list[str]:
    """Return selected tracks with a segment known to contain no signal.

    ``None`` means WebJam could not determine whether the segment contains
    signal, so it remains exportable.  Only an explicit ``False`` is enough to
    stop a musician-facing performance-stem export.
    """
    return [
        track.name
        for track in tracks
        if any(segment.has_signal is False for segment in track.segments)
    ]


def _write_checksum_manifest(folder: Path, destination: Path) -> None:
    files = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path != destination
    )
    destination.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )
    os.chmod(destination, 0o600)


def _project_track_mix_settings(
    settings: MixSettings,
    *,
    track_id: str,
    legacy_position: int,
) -> TrackMixSettings:
    """Resolve one schema-v2 mix state without coupling it to export rows.

    Schema-v2 track IDs are durable, unlike the temporary rows produced after
    filtering tracks for a particular track export.  Prefer an explicit ID
    entry so a selected subset or reordered project cannot borrow another
    lane's mix state.  Integer keys remain the legacy API and continue to
    address the track's position in the full, project-order track list.
    """
    if track_id in settings:
        return settings[track_id]
    return settings.get(legacy_position, TrackMixSettings())


def _export_project_track_package(
    take: TakeInfo,
    project,
    *,
    destination_root: Optional[Path],
    mix_settings: Optional[MixSettings],
    chunk_frames: int,
    selected_track_ids: set[str] | None,
    include_processed_stems: bool,
) -> TrackExportResult:
    """Create the evidence-rich schema-v2 track package on one common timeline."""
    from core.take_project import MediaStatus, SourceType

    if project.take_id != take.take_id and take.take_id:
        raise TakeExportError("The open take and its project manifest do not match.")
    ordered = sorted(project.tracks, key=lambda item: item.order)
    selected = [
        track
        for track in ordered
        if track.selected_for_export
        and (selected_track_ids is None or track.track_id in selected_track_ids)
    ]
    if not selected:
        raise TakeExportError("No project tracks are selected for export.")
    usable = {MediaStatus.AVAILABLE, MediaStatus.RECOVERED}
    blocked = [
        track.name
        for track in selected
        if track.media_status not in usable
        or any(segment.media_status not in usable for segment in track.segments)
    ]
    if blocked:
        raise TakeExportError(
            "Restore or review unavailable media before export: " + ", ".join(blocked)
        )
    explicitly_silent_tracks = _explicitly_silent_track_names(selected)
    if explicitly_silent_tracks:
        raise TakeExportError(
            "WebJam found explicitly silent segments in selected performance tracks: "
            + ", ".join(explicitly_silent_tracks)
            + ". Review the recording or intentionally deselect the affected track "
            "before export."
        )
    unaligned_local_originals = _unaligned_local_original_names(
        selected,
        all_tracks=project.tracks,
        take_root=take.path.resolve(),
    )
    if unaligned_local_originals:
        raise TakeExportError(
            "WebJam cannot create a timing-ready track export because these "
            "local originals have no verified timeline alignment: "
            + ", ".join(unaligned_local_originals)
            + ". Keep the Jamulus server track for this take, or align and "
            "verify each local original before export."
        )
    total_frames = _project_timeline_frames(project)
    if total_frames <= 0:
        raise TakeExportError("No audio remains on the project timeline after alignment.")
    project_rate = int(project.project_sample_rate)
    root = Path(destination_root or (take.path / "Track Exports")).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    final_folder = _next_export_folder(root)
    temporary = root / f".webjam-export-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)

    participant_map = {
        participant.participant_id: participant for participant in project.participants
    }
    settings_map = dict(mix_settings or {})
    legacy_positions = {
        track.track_id: position for position, track in enumerate(ordered)
    }
    stems: list[Path] = []
    processed: list[Path] = []
    applied_settings: list[TrackMixSettings] = []
    track_evidence: list[dict] = []
    used_names: set[str] = set()
    reference_indexes: list[int] = []
    try:
        for export_index, track in enumerate(selected):
            base = _safe_name(track.name, f"Track {export_index + 1}")
            candidate = base
            suffix = 2
            while candidate.casefold() in used_names:
                candidate = f"{base} {suffix}"
                suffix += 1
            used_names.add(candidate.casefold())
            output = temporary / f"{export_index + 1:02d} {candidate}.wav"
            channels = _write_project_track(
                take.path.resolve(),
                track,
                output,
                project_rate=project_rate,
                total_frames=total_frames,
                chunk_frames=chunk_frames,
            )
            state = _project_track_mix_settings(
                settings_map,
                track_id=track.track_id,
                legacy_position=legacy_positions[track.track_id],
            )
            stems.append(output)
            applied_settings.append(state)
            if track.source_type is SourceType.JAMULUS_SERVER:
                reference_indexes.append(export_index)
            person = participant_map.get(track.participant_id)
            source_files = []
            for segment in track.segments:
                source_path = take.path / segment.path
                source_files.append(
                    {
                        "segment_id": segment.segment_id,
                        "path": segment.path,
                        "sample_rate": segment.sample_rate,
                        "channels": segment.channels,
                        "frames": segment.frame_count,
                        "project_start_frame": segment.project_start_frame,
                        "media_status": segment.media_status.value,
                        "declared_sha256": segment.sha256,
                        "observed_sha256": _sha256(source_path),
                        "gaps": [gap.to_dict() for gap in segment.gaps],
                    }
                )
            evidence = {
                "track_number": export_index + 1,
                "track_id": track.track_id,
                "source_id": track.source_id,
                "participant_id": track.participant_id,
                "musician": person.display_name if person else "",
                "instrument": track.instrument or (person.instrument if person else ""),
                "name": track.name,
                "source_type": track.source_type.value,
                "source_quality": track.quality.value,
                "media_status": track.media_status.value,
                "output_filename": output.name,
                "output_sha256": _sha256(output),
                "output_channels": channels,
                "sample_rate": project_rate,
                "automatic_offset_s": track.alignment.automatic_offset_s,
                "manual_nudge_s": track.alignment.manual_nudge_s,
                "effective_offset_s": track.alignment.effective_offset_s,
                "drift_ppm": track.alignment.drift_ppm,
                "alignment_confidence": track.alignment.confidence,
                "alignment_method": track.alignment.method,
                "alignment_residual_ms": track.alignment.residual_ms,
                "resampling": (
                    "deterministic-linear-affine-v1"
                    if any(
                        segment.sample_rate != project_rate
                        for segment in track.segments
                    )
                    or track.alignment.drift_ppm
                    else "none"
                ),
                "gain": round(max(0.0, float(state.gain)), 4),
                "pan": round(max(-1.0, min(1.0, float(state.pan))), 4),
                "muted": bool(state.muted),
                "solo": bool(state.solo),
                "segments": source_files,
            }
            track_evidence.append(evidence)
            if include_processed_stems:
                processed_path = temporary / (
                    f"Processed {export_index + 1:02d} {candidate}.wav"
                )
                _write_processed_stem(
                    output,
                    processed_path,
                    state,
                    samplerate=project_rate,
                    chunk_frames=chunk_frames,
                )
                processed.append(processed_path)
                evidence["processed_filename"] = processed_path.name
                evidence["processed_sha256"] = _sha256(processed_path)

        mixdown = temporary / "WebJam Studio Reference.wav"
        _write_rough_mix(
            stems,
            mixdown,
            applied_settings,
            samplerate=project_rate,
            total_frames=total_frames,
            chunk_frames=chunk_frames,
        )

        reference_mix: Path | None = None
        if reference_indexes:
            # This file is rendered offline from the exported Jamulus-server
            # stems at unity.  It is useful context, but it is not a separately
            # captured live-room mix and must never be labelled as one.
            reference_mix = temporary / "WebJam Server Reference.wav"
            _write_rough_mix(
                [stems[index] for index in reference_indexes],
                reference_mix,
                [TrackMixSettings() for _index in reference_indexes],
                samplerate=project_rate,
                total_frames=total_frames,
                chunk_frames=chunk_frames,
            )

        source_manifest = temporary / "webjam-project-source.json"
        source_manifest.write_text(
            json.dumps(project.to_dict(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        os.chmod(source_manifest, 0o600)

        markers = temporary / "MARKERS.csv"
        marker_lines = ["position_seconds,label\n"]
        for marker in sorted(project.markers, key=lambda item: item.position_s):
            label = marker.label.replace('"', '""')
            marker_lines.append(f'{marker.position_s:.9f},"{label}"\n')
        markers.write_text("".join(marker_lines), encoding="utf-8")
        os.chmod(markers, 0o600)

        alignment_report = temporary / "ALIGNMENT REPORT.md"
        alignment_lines = [
            "# WebJam alignment report\n\n",
            f"Project origin: 0:00 at {project_rate:,} Hz.\n\n",
            "Every numbered stem is rendered from that same origin and length. ",
            "Source recordings remain unchanged. Automatic alignment and manual ",
            "nudge remain separate in the project manifest.\n\n",
            "| Track | Offset | Manual nudge | Drift | Confidence | Residual | Method |\n",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |\n",
        ]
        for track in selected:
            state = track.alignment
            alignment_lines.append(
                f"| {track.name} | {state.automatic_offset_s:+.6f} s | "
                f"{state.manual_nudge_s:+.6f} s | {state.drift_ppm:+.3f} ppm | "
                f"{state.confidence:.3f} | {state.residual_ms:.3f} ms | "
                f"{state.method} |\n"
            )
        alignment_lines.extend(
            [
                "\nMixed-rate or drift-corrected sources use the disclosed ",
                "`deterministic-linear-affine-v1` offline transform. This is a ",
                "repeatable project-time conversion, not a sample-perfect claim.\n",
            ]
        )
        alignment_report.write_text("".join(alignment_lines), encoding="utf-8")
        os.chmod(alignment_report, 0o600)

        recording_report = temporary / "RECORDING REPORT.md"
        report_lines = [
            "# WebJam recording and dropout report\n\n",
            f"Project status: **{project.effective_status.value}**\n\n",
        ]
        if project.errors:
            report_lines.extend(["## Errors\n\n", *[f"- {item}\n" for item in project.errors]])
        if project.warnings:
            report_lines.extend(
                ["\n## Warnings\n\n", *[f"- {item}\n" for item in project.warnings]]
            )
        report_lines.append("\n## Track inventory\n\n")
        for track in selected:
            gaps = sum(len(segment.gaps) for segment in track.segments)
            gap_frames = sum(
                gap.end_frame - gap.start_frame
                for segment in track.segments
                for gap in segment.gaps
            )
            report_lines.append(
                f"- **{track.name}** — {track.source_type.value}; "
                f"{track.quality.value}; {len(track.segments)} segment(s); "
                f"{gaps} disclosed gap(s), {gap_frames} source frame(s).\n"
            )
        recording_report.write_text("".join(report_lines), encoding="utf-8")
        os.chmod(recording_report, 0o600)

        audio_files = [*stems, *processed, mixdown]
        if reference_mix is not None:
            audio_files.append(reference_mix)
        analysis = temporary / "AUDIO ANALYSIS.json"
        analysis_payload = {
            "schema_version": 1,
            "method": "bounded-full-file-rms-peak-v1",
            "files": [_inspect_audio(path) for path in audio_files],
        }
        analysis.write_text(
            json.dumps(analysis_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(analysis, 0o600)

        manifest = temporary / "webjam-track-export.json"
        payload = {
            "schema_version": 2,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_session_id": project.session_id,
            "source_take_id": project.take_id,
            "source_take": take.path.name,
            "session_title": project.session_title,
            "project_sample_rate": project_rate,
            "bit_depth": 24,
            "timeline_frames": total_frames,
            "timeline_duration_s": round(total_frames / project_rate, 9),
            "all_stems_start_at_zero": True,
            "all_stems_same_length": True,
            "original_files_modified": False,
            "tempo_bpm": project.tempo_bpm,
            "time_signature": {
                "numerator": project.time_signature_numerator,
                "denominator": project.time_signature_denominator,
            },
            "markers_file": markers.name,
            "tracks": track_evidence,
            "server_reference": reference_mix.name if reference_mix else None,
            "studio_reference": mixdown.name,
            "alignment_report": alignment_report.name,
            "recording_report": recording_report.name,
            "audio_analysis": analysis.name,
            "source_manifest": source_manifest.name,
            "external_editor_physically_verified": False,
        }
        if not project.session_evidence.is_empty:
            # SessionEvidence is deliberately bounded to recording provenance: it
            # contains no invitation, endpoint, credential, or raw device data.
            # Keep it optional so exports from pre-evidence projects retain their
            # stable metadata shape.
            payload["session_evidence"] = project.session_evidence.to_dict()
        manifest.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        os.chmod(manifest, 0o600)

        instructions = temporary / "IMPORT TRACKS.md"
        instructions.write_text(
            "# Import this WebJam multitrack export\n\n"
            f"- Sample rate: **{project_rate:,} Hz**\n"
            "- Stem format: **24-bit PCM WAV**\n"
            f"- Tempo: **{project.tempo_bpm:g} BPM**\n"
            f"- Time signature: **{project.time_signature_numerator}/"
            f"{project.time_signature_denominator}**\n"
            "- Origin: **every numbered stem begins at 0:00 and has the same length**\n\n"
            "1. Create an empty project in your editor with the sample rate, tempo, and "
            "time signature above.\n"
            "2. Select all numbered WAV files together and drag them to new audio "
            "tracks at 0:00. Do not move stems independently.\n"
            "3. Use `MARKERS.csv` to recreate named markers if needed.\n"
            "4. Use `WebJam Server Reference.wav` to hear the offline unity mix "
            "rendered from the Jamulus server stems when that file is present. "
            "It is not an independently captured live-room mix.\n"
            "5. Use `WebJam Studio Reference.wav` only as a rough mix reference.\n"
            "6. Review the alignment and recording reports before editing uncertain "
            "or recovered material.\n"
            "7. Keep the JSON manifests and `CHECKSUMS.sha256` with the project.\n\n"
            "This package was analyzed by WebJam, but this export alone does not "
            "claim that an external-editor import was physically performed. Original recorder "
            "files remain unchanged in the parent take folder.\n",
            encoding="utf-8",
        )
        os.chmod(instructions, 0o600)

        checksums = temporary / "CHECKSUMS.sha256"
        _write_checksum_manifest(temporary, checksums)
        temporary.rename(final_folder)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return TrackExportResult(
        folder=final_folder,
        stems=tuple(final_folder / path.name for path in stems),
        mixdown=final_folder / mixdown.name,
        manifest=final_folder / manifest.name,
        instructions=final_folder / instructions.name,
        samplerate=project_rate,
        frames=total_frames,
        reference_mix=(
            final_folder / reference_mix.name if reference_mix is not None else None
        ),
        processed_stems=tuple(final_folder / path.name for path in processed),
        alignment_report=final_folder / alignment_report.name,
        recording_report=final_folder / recording_report.name,
        checksums=final_folder / checksums.name,
        analysis=final_folder / analysis.name,
        source_manifest=final_folder / source_manifest.name,
    )


def export_track_package(
    take: TakeInfo,
    *,
    destination_root: Optional[Path] = None,
    mix_settings: Optional[MixSettings] = None,
    chunk_frames: int = 65536,
    selected_track_ids: set[str] | None = None,
    include_processed_stems: bool = False,
) -> TrackExportResult:
    """Create an atomic, zero-aligned track package for ``take``.

    The package contains numbered 24-bit WAV stems of identical length.  A
    negative WebJam offset trims the local pre-roll; a positive offset becomes
    leading silence.  This lets a musician import every stem together at 0:00
    in an editor without manually interpreting the WebJam manifest.
    """
    if not take.tracks:
        raise TakeExportError("This take has no audio tracks to export.")
    if chunk_frames < 1024:
        raise ValueError("chunk_frames must be at least 1024")

    project_manifest = take.path / "webjam-take.json"
    if project_manifest.is_file():
        from core.take_project import TakeProjectError, load_take_project

        try:
            project = load_take_project(take.path)
        except TakeProjectError as exc:
            raise TakeExportError(
                f"The take project manifest could not be verified: {exc}"
            ) from exc
        return _export_project_track_package(
            take,
            project,
            destination_root=destination_root,
            mix_settings=mix_settings,
            chunk_frames=chunk_frames,
            selected_track_ids=selected_track_ids,
            include_processed_stems=include_processed_stems,
        )

    import soundfile as sf  # type: ignore

    rates: set[int] = set()
    source_info: list[tuple[int, int, int]] = []
    for track in take.tracks:
        try:
            info = sf.info(str(track.path))
        except Exception as exc:  # noqa: BLE001
            raise TakeExportError(
                f"{track.path.name} could not be read: {exc}"
            ) from exc
        rate = int(info.samplerate)
        if rate <= 0 or info.frames <= 0:
            raise TakeExportError(f"{track.path.name} is empty or unreadable.")
        rates.add(rate)
        offset_frames = int(round(float(track.offset_s) * rate))
        source_info.append((offset_frames, int(info.frames), int(info.channels)))
    if len(rates) != 1:
        raise TakeExportError(
            f"The take uses mixed sample rates ({sorted(rates)}); fix that before "
            "creating a track export."
        )
    samplerate = rates.pop()
    total_frames = max(
        max(0, offset + frames) for offset, frames, _channels in source_info
    )
    if total_frames <= 0:
        raise TakeExportError("No audio remains on the take timeline after alignment.")

    root = Path(destination_root or (take.path / "Track Exports")).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    final_folder = _next_export_folder(root)
    temporary = root / f".webjam-export-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)

    settings_map = dict(mix_settings or {})
    stem_paths: list[Path] = []
    applied_settings: list[TrackMixSettings] = []
    evidence: list[dict] = []
    used_names: set[str] = set()
    try:
        for index, (track, source) in enumerate(zip(take.tracks, source_info)):
            offset_frames, _frames, _channels = source
            base = _safe_name(track.name, f"Track {index + 1}")
            candidate = base
            suffix = 2
            while candidate.casefold() in used_names:
                candidate = f"{base} {suffix}"
                suffix += 1
            used_names.add(candidate.casefold())
            output = temporary / f"{index + 1:02d} {candidate}.wav"
            channels = _write_aligned_stem(
                track.path,
                output,
                offset_frames=offset_frames,
                total_frames=total_frames,
                samplerate=samplerate,
                chunk_frames=chunk_frames,
            )
            state = settings_map.get(index, TrackMixSettings())
            stem_paths.append(output)
            applied_settings.append(state)
            evidence.append(
                {
                    "track_number": index + 1,
                    "name": track.name,
                    "source": track.source,
                    "original_filename": track.path.name,
                    "output_filename": output.name,
                    "original_offset_s": round(float(track.offset_s), 6),
                    "sample_rate": samplerate,
                    "channels": channels,
                    "gain": round(max(0.0, float(state.gain)), 4),
                    "pan": round(max(-1.0, min(1.0, float(state.pan))), 4),
                    "muted": bool(state.muted),
                    "solo": bool(state.solo),
                }
            )

        mixdown = temporary / "WebJam Rough Mix.wav"
        _write_rough_mix(
            stem_paths,
            mixdown,
            applied_settings,
            samplerate=samplerate,
            total_frames=total_frames,
            chunk_frames=chunk_frames,
        )

        manifest = temporary / "webjam-track-export.json"
        payload = {
            "schema_version": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_take": take.path.name,
            "session_title": getattr(take, "session_title", ""),
            "sample_rate": samplerate,
            "bit_depth": 24,
            "timeline_frames": total_frames,
            "timeline_duration_s": round(total_frames / samplerate, 6),
            "all_stems_start_at_zero": True,
            "original_files_modified": False,
            "tracks": evidence,
            "rough_mix": mixdown.name,
            "external_editor_physically_verified": False,
        }
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(manifest, 0o600)

        instructions = temporary / "IMPORT TRACKS.md"
        instructions.write_text(
            "# Import this WebJam multitrack export\n\n"
            f"- Sample rate: **{samplerate:,} Hz**\n"
            "- Stem depth: **24-bit PCM WAV**\n"
            "- Alignment: **every numbered stem starts at 0:00 and has the same length**\n\n"
            "1. Create an empty project in your editor at the sample rate above.\n"
            "2. Select every numbered WAV stem (01, 02, …) and drag them together "
            "into the empty Tracks area at 0:00.\n"
            "3. Put each file on a new audio track. The files are already padded or "
            "trimmed to WebJam's verified timeline; do not move them independently.\n"
            "4. Use `WebJam Rough Mix.wav` only as a reference, not as another stem.\n"
            "5. Keep `webjam-track-export.json` with the project as the alignment and "
            "source record.\n\n"
            "This export alone does not claim that an external-editor import was "
            "physically performed. The original recorder WAVs remain unchanged in "
            "the parent take folder.\n",
            encoding="utf-8",
        )
        os.chmod(instructions, 0o600)
        temporary.rename(final_folder)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return TrackExportResult(
        folder=final_folder,
        stems=tuple(final_folder / path.name for path in stem_paths),
        mixdown=final_folder / mixdown.name,
        manifest=final_folder / manifest.name,
        instructions=final_folder / instructions.name,
        samplerate=samplerate,
        frames=total_frames,
    )


def export_logic_package(
    take: TakeInfo,
    *,
    destination_root: Optional[Path] = None,
    mix_settings: Optional[MixSettings] = None,
    chunk_frames: int = 65536,
    selected_track_ids: set[str] | None = None,
    include_processed_stems: bool = False,
) -> TrackExportResult:
    """Backward-compatible alias for :func:`export_track_package`.

    The generated package remains editor-neutral; this name is retained only
    so existing callers continue to work during the API transition.
    """
    return export_track_package(
        take,
        destination_root=destination_root,
        mix_settings=mix_settings,
        chunk_frames=chunk_frames,
        selected_track_ids=selected_track_ids,
        include_processed_stems=include_processed_stems,
    )
