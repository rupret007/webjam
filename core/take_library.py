"""
Take library — discover recorded sessions and read their track layout.

A "take" is one Jamulus multitrack recording: a folder of per-musician WAV
files plus a Reaper ``.rpp`` and/or an Audacity ``.lof`` that carry each
track's *start offset* (musicians connect at different moments, so tracks
don't all begin at t=0). This module turns a directory of such folders into
structured ``TakeInfo`` objects for the Take Deck to play back.

Deliberately dependency-light: only ``soundfile`` (for durations/samplerate),
and even that is imported lazily so the module stays importable in
environments without libsndfile.
"""

from __future__ import annotations

import logging
import json
import hashlib
import math
import re
import time
import uuid
import wave
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from core.take_project import SessionEvidence

_logger = logging.getLogger("webjam.take_library")

_AUDIO_EXTS = {".wav", ".flac", ".ogg", ".aiff", ".aif"}


def _is_visible_take_directory(path: Path) -> bool:
    """Return whether an immediate child may represent a musician-facing take.

    The Takes root also holds private WebJam working folders: local-capture
    recovery, export staging, and the in-progress recording-evidence journal.
    None is a Jamulus take, and choosing one by mtime can hide the real server
    folder immediately after Record stops.  Keep this boundary name-based and
    dependency-free so discovery and the recorder snapshot agree.
    """
    return not path.name.startswith(".") and path.is_dir()


@dataclass
class TrackSegmentInfo:
    """One explicit media/configuration interval on the project timeline."""

    path: Path
    project_start_frame: int
    frame_count: int
    samplerate: int
    channels: int = 1
    media_status: str = "available"
    segment_id: str = ""
    sha256: str = ""
    gaps: tuple[tuple[int, int, tuple[int, ...], str], ...] = ()

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.samplerate if self.samplerate > 0 else 0.0


@dataclass
class TrackInfo:
    """One audio track within a take."""
    path: Path
    name: str
    offset_s: float = 0.0          # start offset within the take timeline
    duration_s: float = 0.0        # audio length (0 if unknown)
    samplerate: int = 0
    source: str = "jamulus_server"
    # Runtime source truth.  Manifest-declared tracks stay in the project even
    # when their file has moved or disappeared, so Studio can show the problem
    # instead of silently presenting a smaller take as verified.
    media_status: str = "available"
    track_id: str = ""
    source_id: str = ""
    participant_id: str = ""
    instrument: str = ""
    quality: str = "unverified"
    segments: tuple[TrackSegmentInfo, ...] = ()
    drift_ppm: float = 0.0
    alignment_confidence: float = 0.0
    alignment_method: str = "unverified"

    @property
    def end_s(self) -> float:
        return self.offset_s + self.duration_s


@dataclass
class TakeInfo:
    """One recorded session: a folder of tracks + timing metadata."""
    path: Path
    name: str
    tracks: List[TrackInfo] = field(default_factory=list)
    reaper_project: Optional[Path] = None
    validation_status: str = "unchecked"
    manifest_path: Optional[Path] = None
    # Findings recorded at validation time, so reviewing a finished take
    # never needs to re-probe its audio files.
    manifest_errors: tuple[str, ...] = ()
    manifest_warnings: tuple[str, ...] = ()
    session_title: str = ""
    session_id: str = ""
    take_id: str = ""
    project_samplerate: int = 0

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def duration_s(self) -> float:
        """Wall-clock length of the take: the latest track end."""
        return max((t.end_s for t in self.tracks), default=0.0)

    @property
    def display_name(self) -> str:
        """Musician-facing name, falling back to the recorder folder name."""
        return self.session_title.strip() or self.name


@dataclass(frozen=True)
class TakeValidationResult:
    """Post-recording confidence report for one take directory."""

    take: Optional[TakeInfo]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return self.take is not None and not self.errors

    @property
    def summary(self) -> str:
        if self.take is None:
            return "No completed take was found."
        rate_values = {t.samplerate for t in self.take.tracks if t.samplerate > 0}
        rate = (
            f"{next(iter(rate_values)) / 1000:g} kHz"
            if len(rate_values) == 1 else "mixed rate"
        )
        duration = int(round(self.take.duration_s))
        return (
            f"{self.take.track_count} track{'s' if self.take.track_count != 1 else ''}"
            f" · {duration // 60}:{duration % 60:02d} · {rate}"
        )


def snapshot_take_directories(root: str | Path) -> dict[Path, int]:
    """Return immediate take directories and mtimes without ever raising."""
    path = Path(root).expanduser()
    try:
        if not path.is_dir():
            return {}
        return {
            child: child.stat().st_mtime_ns
            for child in path.iterdir()
            if _is_visible_take_directory(child)
        }
    except OSError:
        return {}


def find_changed_take(root: str | Path, before: dict[Path, int]) -> Optional[Path]:
    """Find the newest new/modified take directory since ``before``."""
    path = Path(root).expanduser()
    candidates: list[tuple[int, Path]] = []
    try:
        for child in path.iterdir():
            if not _is_visible_take_directory(child):
                continue
            stamp = child.stat().st_mtime_ns
            if child not in before or stamp > before[child]:
                candidates.append((stamp, child))
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def wait_for_take_files_stable(
    take_dir: Path,
    *,
    polls: int = 8,
    interval_s: float = 0.25,
) -> bool:
    """Wait until audio file sizes stop changing for two consecutive polls."""
    previous: Optional[tuple[tuple[str, int], ...]] = None
    stable = 0
    for _ in range(max(1, polls)):
        try:
            current = tuple(sorted(
                (p.name, p.stat().st_size)
                for p in take_dir.iterdir()
                if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
            ))
        except OSError:
            current = ()
        if current and current == previous:
            stable += 1
            if stable >= 2:
                return True
        else:
            stable = 0
        previous = current
        time.sleep(max(0.0, interval_s))
    return False


def _track_has_signal(path: Path) -> Optional[bool]:
    """Sample a few short windows; return None when the file cannot be read."""
    try:
        import numpy as np
        import soundfile as sf  # type: ignore

        with sf.SoundFile(str(path)) as audio:
            if len(audio) <= 0:
                return False
            window = min(4096, len(audio))
            starts = {0, max(0, len(audio) // 2 - window // 2), max(0, len(audio) - window)}
            for start in starts:
                audio.seek(start)
                block = audio.read(window, dtype="float32", always_2d=True)
                if block.size and float(np.max(np.abs(block))) > 1e-5:
                    return True
            return False
    except Exception:  # noqa: BLE001
        return None


def validate_take(take_dir: str | Path, *, expected_tracks: int = 0,
                  require_48k: bool = True,
                  required_local_stems: int = 0) -> TakeValidationResult:
    """Validate a completed Jamulus take and report errors separately from warnings."""
    path = Path(take_dir).expanduser()
    take = load_take(path)
    if take is None:
        return TakeValidationResult(None, ("No readable audio tracks were created.",))

    errors: list[str] = []
    warnings: list[str] = []
    if expected_tracks > 0 and take.track_count < expected_tracks:
        errors.append(
            f"Expected at least {expected_tracks} tracks but found {take.track_count}."
        )
    rates = {track.samplerate for track in take.tracks if track.samplerate > 0}
    if len(rates) > 1:
        errors.append(f"Tracks use different sample rates: {sorted(rates)}.")
    if require_48k and any(rate != 48000 for rate in rates):
        errors.append(f"All tracks must be 48 kHz; found {sorted(rates)}.")
    local_tracks = [
        track for track in take.tracks
        if track.source in {"local_ssl", "local_isolated"}
    ]
    if required_local_stems and len(local_tracks) < required_local_stems:
        errors.append(
            f"Expected {required_local_stems} isolated host stems but found "
            f"{len(local_tracks)}."
        )
    for track in take.tracks:
        try:
            size = track.path.stat().st_size
        except OSError:
            size = 0
        if size <= 44 or track.duration_s <= 0 or track.samplerate <= 0:
            errors.append(f"{track.name} is empty or unreadable.")
            continue
        signal = _track_has_signal(track.path)
        if signal is False:
            warnings.append(f"{track.name} appears silent.")
        elif signal is None:
            warnings.append(f"{track.name} could not be checked for audible signal.")
    return TakeValidationResult(
        take, tuple(errors), tuple(warnings), take.manifest_path,
    )


_LOCAL_STEM_PREFIXES = ("host-guitar", "host-vocal")
_ENVELOPE_BLOCK = 480  # 10 ms at 48 kHz → a true 100 Hz amplitude envelope

# Confidence is the normalized full-rate correlation at the refined lag: the
# same performance through the local and server paths scores near 1.0 even
# after the Opus codec, while unrelated audio stays near zero (≈0.01 for a
# five-second window), so 0.15 is a conservative floor rather than a tuned
# value.
ALIGNMENT_CONFIDENCE_MIN = 0.15
ALIGNMENT_METHOD = "envelope+refine-v2"


def is_local_stem_name(name: str) -> bool:
    """True for supplemental host stems (host-guitar*.wav / host-vocal*.wav)."""
    lowered = name.lower()
    return lowered.endswith(".wav") and lowered.startswith(_LOCAL_STEM_PREFIXES)


def server_track_channel_id(filename: str) -> Optional[int]:
    """Extract Jamulus's channel id from its ``...-id-channels.wav`` name."""
    match = re.search(r"-(\d+)-\d+\.[A-Za-z0-9]+$", str(filename))
    return int(match.group(1)) if match else None


def _envelope_100hz(signal):
    """Rectified block-mean envelope, mean-subtracted for correlation.

    Block averaging (unlike stride decimation) is alias-free, so the peak and
    the confidence it feeds reflect real amplitude agreement.
    """
    import numpy as np

    usable = (signal.size // _ENVELOPE_BLOCK) * _ENVELOPE_BLOCK
    if usable == 0:
        return np.zeros(0, dtype="float32")
    env = np.abs(signal[:usable]).reshape(-1, _ENVELOPE_BLOCK).mean(axis=1)
    return env - float(np.mean(env))


def _refine_lag(server_sig, local_sig, coarse_lag: int, anchor: int) -> tuple[int, float]:
    """Sample-accurate lag within ±one envelope block of the coarse peak.

    Sweeps a bounded normalized correlation of the raw 48 kHz signals around
    the loudest local passage, removing the 10 ms envelope quantization that
    would otherwise land in the manifest offset.  Returns ``(lag, value)``;
    the value doubles as the alignment confidence because raw-sample
    correlation separates a genuine match from unrelated audio far more
    sharply than the coarse envelope does.
    """
    import numpy as np

    half = 48000 * 5 // 2
    best_val = 0.0
    best_lag = coarse_lag
    for lag in range(coarse_lag - _ENVELOPE_BLOCK, coarse_lag + _ENVELOPE_BLOCK + 1):
        start = max(0, anchor - half, -lag)
        stop = min(len(local_sig), anchor + half, len(server_sig) - lag)
        if stop - start < 4800:  # need at least 100 ms of overlap
            continue
        local_part = local_sig[start:stop]
        server_part = server_sig[start + lag:stop + lag]
        denom = float(np.linalg.norm(local_part) * np.linalg.norm(server_part))
        if denom <= 0.0:
            continue
        value = abs(float(np.dot(local_part, server_part))) / denom
        if value > best_val:
            best_val = value
            best_lag = lag
    return best_lag, best_val


def estimate_local_alignment(take_dir: str | Path) -> tuple[float, float]:
    """Estimate local-stem offset against the closest Jamulus server track.

    Returns ``(offset_seconds, confidence)``.  The offset is signed: it is
    negative when the local stems start before the server take, which is the
    normal case because supplemental capture arms before the server recorder
    acknowledges the RPC start.  Correlation is bounded to keep post-record
    validation responsive and never runs on the audio thread.
    """
    path = Path(take_dir)
    wavs = sorted(p for p in path.glob("*.wav") if p.is_file())
    local = [p for p in wavs if is_local_stem_name(p.name)]
    server = [p for p in wavs if not is_local_stem_name(p.name)]
    if len(local) < 2 or not server:
        return (0.0, 0.0)
    try:
        import numpy as np
        import soundfile as sf  # type: ignore

        limit = 48000 * 60
        first, rate = sf.read(str(local[0]), frames=limit, dtype="float32")
        second, second_rate = sf.read(str(local[1]), frames=limit, dtype="float32")
        if rate != 48000 or second_rate != rate:
            return (0.0, 0.0)
        length = min(len(first), len(second))
        local_mix = np.asarray(first)[:length] + np.asarray(second)[:length]
        if float(np.max(np.abs(local_mix))) < 1e-5:
            return (0.0, 0.0)
        local_env = _envelope_100hz(local_mix)
        env_norm = float(np.linalg.norm(local_env))
        if local_env.size < 256 or env_norm <= 0.0:
            return (0.0, 0.0)
        # Refine around the loudest local passage, not the take start, so a
        # quiet count-in doesn't starve the fine correlation of signal.
        anchor = int(np.argmax(np.abs(local_env))) * _ENVELOPE_BLOCK
        best_confidence = 0.0
        best_lag_samples = 0
        for candidate in server:
            audio, candidate_rate = sf.read(
                str(candidate), frames=limit, dtype="float32", always_2d=True
            )
            if candidate_rate != rate or audio.size == 0:
                continue
            mono = np.mean(audio, axis=1)
            candidate_env = _envelope_100hz(mono)
            denom = float(np.linalg.norm(candidate_env)) * env_norm
            if candidate_env.size == 0 or denom <= 0.0:
                continue
            correlation = np.correlate(candidate_env, local_env, mode="full")
            index = int(np.argmax(correlation))
            coarse = (index - (len(local_env) - 1)) * _ENVELOPE_BLOCK
            lag, confidence = _refine_lag(mono, local_mix, coarse, anchor)
            if confidence > best_confidence:
                best_lag_samples = lag
                best_confidence = confidence
        return (best_lag_samples / rate, best_confidence)
    except Exception:  # noqa: BLE001
        _logger.exception("Could not align isolated host stems")
        return (0.0, 0.0)


def write_take_manifest(
    take_dir: str | Path, *, expected_tracks: int, required_local_stems: int,
    local_started_utc: str = "", local_duration_s: float = 0.0,
    capture_errors: tuple[str, ...] = (), app_version: str = "",
    participant_names: Optional[dict[int, str]] = None,
    session_title: str = "",
    session_id: str = "", take_id: str = "",
    participant_ids: Optional[dict[int, str]] = None,
    local_participant_id: str = "", local_participant_name: str = "Host",
    capture_device=None, capture_gaps: tuple[object, ...] = (),
    local_total_frames: int = 0,
    session_evidence: "SessionEvidence | None" = None,
) -> TakeValidationResult:
    """Validate a take and atomically publish schema-v2 project truth.

    The preliminary receipt exists only long enough to classify legacy local
    filenames during validation.  The final file carries durable IDs, exact
    format/frame/hash evidence, explicit local-capture gaps, and
    non-destructive alignment metadata.
    """
    from core.file_io import atomic_write_text
    from core.take_project import (
        AlignmentState,
        GapInterval,
        MediaSegment,
        MediaStatus,
        Participant,
        ProjectStatus,
        ProjectTrack,
        SessionEvidence,
        SessionTimelineEvent,
        SourceQuality,
        SourceType,
        TakeProject,
        new_project_id,
        write_take_project,
    )

    path = Path(take_dir)
    offset_s, confidence = estimate_local_alignment(path)
    if session_evidence is None:
        final_session_evidence = SessionEvidence()
    elif isinstance(session_evidence, SessionEvidence):
        # It is deliberately copied below rather than mutated: callers may
        # retain the evidence object while the writer adds file-backed facts.
        final_session_evidence = session_evidence
    else:
        raise TypeError("session_evidence must be a SessionEvidence instance.")

    # Write a preliminary manifest so load_take can classify supplemental files.
    manifest_path = path / "webjam-take.json"
    preliminary = {
        "schema_version": 1,
        "app_version": app_version,
        "session_title": str(session_title or "").strip(),
        "status": "validating",
        "expected_server_tracks": expected_tracks,
        "required_local_stems": required_local_stems,
        "local_capture": {
            "started_utc": local_started_utc,
            "duration_s": round(local_duration_s, 6),
            "offset_s": round(offset_s, 6),
            "alignment_confidence": round(confidence, 6),
            "alignment_method": ALIGNMENT_METHOD,
            "errors": list(capture_errors),
        },
        "tracks": [
            {"filename": p.name,
             "name": (
                 (participant_names or {}).get(server_track_channel_id(p.name))
                 if not is_local_stem_name(p.name) else None
             ),
             "source": "local_ssl" if is_local_stem_name(p.name) else "jamulus_server",
             "offset_s": round(offset_s, 6) if is_local_stem_name(p.name) else None}
            for p in sorted(path.glob("*.wav"))
        ],
    }
    if not final_session_evidence.is_empty:
        # A crash before final classification still leaves bounded recording
        # provenance beside the source media. This is a receipt, not a claim
        # that the incomplete folder has become a verified project.
        preliminary["session"] = final_session_evidence.to_dict()
    atomic_write_text(manifest_path, json.dumps(preliminary, indent=2), mode=0o600)
    result = validate_take(
        path, expected_tracks=expected_tracks + required_local_stems,
        required_local_stems=required_local_stems,
    )
    errors = list(result.errors) + list(capture_errors)
    if required_local_stems and confidence < ALIGNMENT_CONFIDENCE_MIN:
        errors.append("Isolated host stems could not be aligned confidently.")
    take = result.take
    if take is None:
        preliminary.update({
            "status": "needs_attention",
            "errors": errors,
            "warnings": list(result.warnings),
            "tracks": [],
        })
        atomic_write_text(manifest_path, json.dumps(preliminary, indent=2), mode=0o600)
        return TakeValidationResult(
            None, tuple(errors), result.warnings, manifest_path,
        )

    def _id_or_new(value: str) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            return new_project_id()

    stable_session_id = _id_or_new(session_id)
    stable_take_id = _id_or_new(take_id)
    stable_local_participant_id = (
        _id_or_new(local_participant_id)
        if local_participant_id
        else str(uuid.uuid5(
            uuid.UUID(stable_session_id), "participant:local-recorder"
        ))
    )

    def _child_id(label: str) -> str:
        return str(uuid.uuid5(uuid.UUID(stable_take_id), label))

    participants_by_id: dict[str, Participant] = {}
    project_tracks: list[ProjectTrack] = []
    for order, track in enumerate(take.tracks):
        local = track.source in {"local_ssl", "local_isolated"}
        channel_id = server_track_channel_id(track.path.name)
        if local:
            participant_id = stable_local_participant_id
            participant_name = (
                " ".join(str(local_participant_name or "Host").split()) or "Host"
            )
            source_type = SourceType.LOCAL_ISOLATED
            quality = SourceQuality.UNVERIFIED
            local_channel = 0 if track.path.name.lower().startswith("host-guitar") else 1
        else:
            provided_id = (
                (participant_ids or {}).get(channel_id)
                if channel_id is not None else None
            )
            if provided_id:
                participant_id = _id_or_new(str(provided_id))
            else:
                participant_key = (
                    f"jamulus-channel:{channel_id}"
                    if channel_id is not None else f"server-file:{track.path.name}"
                )
                participant_id = str(uuid.uuid5(
                    uuid.UUID(stable_session_id), f"participant:{participant_key}"
                ))
            participant_name = track.name
            source_type = SourceType.JAMULUS_SERVER
            quality = SourceQuality.NETWORK_TRACK
            local_channel = -1
        participants_by_id.setdefault(
            participant_id,
            Participant(participant_id, participant_name),
        )

        evidence = _audio_file_evidence(track.path)
        frame_count = int(evidence["frame_count"])
        if local and local_total_frames > 0:
            # The callback-owned absolute timeline is authoritative.  A
            # disagreement remains visible rather than silently changing the
            # declared take length.
            if frame_count != int(local_total_frames):
                errors.append(
                    f"{track.name} contains {frame_count} frames but local "
                    f"capture reported {int(local_total_frames)}."
                )
        gaps: list[GapInterval] = []
        if local:
            for item in capture_gaps:
                channels = tuple(getattr(item, "channels", ()) or ())
                if channels and local_channel not in channels:
                    continue
                try:
                    gap_start = int(getattr(item, "start_frame"))
                    gap_frames = int(getattr(item, "frame_count"))
                    if gap_start < 0 or gap_frames <= 0 or gap_start + gap_frames > frame_count:
                        errors.append(
                            f"{track.name} has gap metadata outside its audio frame range."
                        )
                        continue
                    gaps.append(GapInterval(
                        start_frame=gap_start,
                        frame_count=gap_frames,
                        reason=str(getattr(item, "reason")),
                        channels=(0,),
                    ))
                except (TypeError, ValueError):
                    errors.append(
                        f"{track.name} has unreadable local gap metadata."
                    )
        segment_status = (
            MediaStatus.AVAILABLE
            if track.path.is_file() and evidence["sample_rate"] > 0
            else MediaStatus.DAMAGED
        )
        segment = MediaSegment(
            segment_id=_child_id(f"segment:{order}:{track.path.name}:0"),
            path=track.path.name,
            project_start_frame=0,
            frame_count=frame_count,
            sample_rate=max(1, int(evidence["sample_rate"] or track.samplerate)),
            channels=max(1, int(evidence["channels"])),
            sample_format=str(evidence["sample_format"] or "UNKNOWN"),
            media_status=segment_status,
            sha256=str(evidence["sha256"]),
            device_id=(str(getattr(capture_device, "device_id", "")) if local else ""),
            gaps=tuple(gaps),
            size_bytes=int(evidence["size_bytes"]),
            has_signal=evidence["has_signal"],
        )
        project_tracks.append(ProjectTrack(
            track_id=_child_id(f"track:{order}:{track.path.name}"),
            source_id=_child_id(f"source:{order}:{track.path.name}"),
            participant_id=participant_id,
            name=track.name,
            instrument="",
            source_type=source_type,
            quality=quality,
            media_status=segment_status,
            order=order,
            segments=(segment,),
            alignment=AlignmentState(
                automatic_offset_s=track.offset_s,
                confidence=confidence if local else 0.0,
                method=ALIGNMENT_METHOD if local else "recorder-origin",
            ),
        ))

    # A host can be a durable session participant even when this take has no
    # host-owned stem (for example, a host records only the server mix).
    # Keep that identity in the manifest so session evidence never points at
    # an unresolvable participant.
    if final_session_evidence.host.participant_id:
        participants_by_id.setdefault(
            final_session_evidence.host.participant_id,
            Participant(
                final_session_evidence.host.participant_id,
                final_session_evidence.host.display_name or "Host",
            ),
        )

    errors = list(dict.fromkeys(errors))
    project_sample_rate = next(
        (item.primary_segment.sample_rate for item in project_tracks), 48000
    )

    # Segment gaps remain the source-of-truth, frame-exact records.  The
    # timeline adds a human-reviewable, project-time index without inventing
    # a wall-clock timestamp or changing any source audio.
    timeline = list(final_session_evidence.timeline)
    for project_track in project_tracks:
        for segment in project_track.segments:
            for gap in segment.gaps:
                event = SessionTimelineEvent(
                    event="media_gap",
                    at_s=(
                        segment.project_start_frame / project_sample_rate
                        + gap.start_frame / segment.sample_rate
                    ),
                    participant_id=project_track.participant_id or "",
                    detail=(
                        f"Segment {segment.segment_id}: {gap.frame_count} source "
                        f"frames unavailable ({gap.reason})."
                    ),
                )
                if event not in timeline:
                    timeline.append(event)
    final_session_evidence = replace(
        final_session_evidence,
        timeline=tuple(timeline),
    )

    project = TakeProject(
        session_id=stable_session_id,
        take_id=stable_take_id,
        session_title=str(session_title or "").strip(),
        take_name=path.name or "Take",
        status=ProjectStatus.COMPLETE if not errors else ProjectStatus.NEEDS_ATTENTION,
        project_sample_rate=project_sample_rate,
        participants=tuple(participants_by_id.values()),
        tracks=tuple(project_tracks),
        app_version=app_version,
        created_utc=local_started_utc,
        devices=(capture_device,) if capture_device is not None else (),
        errors=tuple(errors),
        warnings=result.warnings,
        session_evidence=final_session_evidence,
    )
    write_take_project(path, project)
    loaded = load_take(path)
    return TakeValidationResult(
        loaded, tuple(errors), result.warnings, manifest_path,
    )


def _audio_file_evidence(path: Path) -> dict[str, object]:
    """Return exact, streaming evidence without retaining source handles."""
    frame_count = 0
    sample_rate = 0
    channels = 0
    sample_format = ""
    try:
        import soundfile as sf  # type: ignore

        info = sf.info(str(path))
        frame_count = int(info.frames)
        sample_rate = int(info.samplerate)
        channels = int(info.channels)
        sample_format = str(info.subtype or info.format or "")
    except Exception as exc:  # noqa: BLE001
        _logger.debug("soundfile evidence probe failed for %s: %s", path, exc)
        if path.suffix.lower() == ".wav":
            try:
                with wave.open(str(path), "rb") as source:
                    frame_count = int(source.getnframes())
                    sample_rate = int(source.getframerate())
                    channels = int(source.getnchannels())
                    sample_format = f"PCM_{source.getsampwidth() * 8}"
            except Exception as wave_exc:  # noqa: BLE001
                _logger.debug("wave evidence probe failed for %s: %s", path, wave_exc)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        checksum = digest.hexdigest()
    except OSError:
        checksum = ""
    return {
        "frame_count": frame_count,
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_format": sample_format,
        "size_bytes": size,
        "sha256": checksum,
        "has_signal": _track_has_signal(path),
    }


def _probe_audio(path: Path) -> tuple[float, int]:
    """Return (duration_s, samplerate). Tries soundfile, falls back to the
    stdlib ``wave`` module for plain WAVs, returns (0, 0) if unreadable."""
    try:
        import soundfile as sf  # type: ignore
        info = sf.info(str(path))
        if info.samplerate > 0:
            return (info.frames / info.samplerate, int(info.samplerate))
    except Exception as exc:  # noqa: BLE001
        _logger.debug("soundfile probe failed for %s: %s", path, exc)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                if rate > 0:
                    return (w.getnframes() / rate, rate)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("wave probe failed for %s: %s", path, exc)
    return (0.0, 0)


def parse_lof_offsets(lof_path: Path) -> dict[str, float]:
    """Parse an Audacity ``.lof`` file into {filename: offset_seconds}.

    Lines look like:  file "guitar.wav" offset 3.5
    Only ``file`` directives are honoured; malformed lines are skipped.
    Filenames are keyed by basename so they match regardless of quoting or
    any path prefix the recorder wrote.
    """
    offsets: dict[str, float] = {}
    try:
        text = lof_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _logger.debug("could not read lof %s: %s", lof_path, exc)
        return offsets
    # file "name" [offset N]
    pattern = re.compile(
        r'^\s*file\s+"([^"]+)"(?:.*?\boffset\s+([-+]?\d+(?:\.\d+)?))?',
        re.IGNORECASE,
    )
    for line in text.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        name = Path(m.group(1)).name
        try:
            offsets[name] = float(m.group(2)) if m.group(2) is not None else 0.0
        except (TypeError, ValueError):
            offsets[name] = 0.0
    return offsets


def _prettify(stem: str) -> str:
    """Turn a track filename stem into a display name."""
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return cleaned.title() if cleaned else stem


def _safe_manifest_audio_path(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or "\\" in text:
        return None
    path = Path(text)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.lower() not in _AUDIO_EXTS
    ):
        return None
    return path.as_posix()


def _safe_finite_float(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def load_take(take_dir: Path) -> Optional[TakeInfo]:
    """Build a TakeInfo from a single take folder, or None if it has no
    audio tracks or manifest-declared media.

    A completed manifest is the take's expected-media inventory, not merely a
    source of labels for whichever files still happen to exist.  Preserve
    declared missing tracks in the returned model and downgrade stale
    ``complete`` state so review/export can never hide the loss.
    """
    take_dir = Path(take_dir)
    if not take_dir.is_dir():
        return None

    audio_files = sorted(
        p for p in take_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
    )

    manifest_path = take_dir / "webjam-take.json"
    manifest: dict = {}
    try:
        loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded_manifest, dict):
            manifest = loaded_manifest
    except (OSError, ValueError):
        manifest_path = None

    manifest_tracks: dict[str, dict] = {}
    declared_filenames: list[str] = []
    reconciliation_errors: list[str] = []
    raw_tracks = manifest.get("tracks", [])
    if isinstance(raw_tracks, list):
        for item in raw_tracks:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                continue
            filename = _safe_manifest_audio_path(item["filename"])
            # Manifests describe media inside the take directory.  Never turn
            # an untrusted/hand-edited path into a probe outside that boundary.
            if (
                not filename
            ):
                reconciliation_errors.append(
                    "The take manifest contains an invalid audio filename."
                )
                continue
            if filename in manifest_tracks:
                reconciliation_errors.append(
                    f"The take manifest lists {filename} more than once."
                )
                continue
            manifest_tracks[filename] = item
            declared_filenames.append(filename)

    if not audio_files and not declared_filenames:
        return None

    # Offsets: prefer a .lof if present, then manifest overrides for local stems.
    offsets: dict[str, float] = {}
    lofs = list(take_dir.glob("*.lof"))
    if lofs:
        offsets = parse_lof_offsets(lofs[0])

    reaper = next(iter(take_dir.glob("*.rpp")), None)

    tracks: List[TrackInfo] = []
    # Manifest order is stable project order.  Legacy/unlisted files follow in
    # their prior deterministic filename order.
    candidates: list[tuple[Path, dict]] = [
        (take_dir / filename, manifest_tracks[filename])
        for filename in declared_filenames
    ]
    candidates.extend((audio, {}) for audio in audio_files if audio.name not in manifest_tracks)
    project_rate_raw = manifest.get("project_sample_rate", 0)
    project_rate = (
        int(project_rate_raw)
        if isinstance(project_rate_raw, (int, float))
        and not isinstance(project_rate_raw, bool)
        and math.isfinite(float(project_rate_raw))
        and int(project_rate_raw) > 0
        else 0
    )
    for audio, evidence in candidates:
        available = audio.is_file()
        if available:
            duration, rate = _probe_audio(audio)
        else:
            # Final manifests retain the last verified format/duration.  Keep
            # that non-audio evidence so losing the longest file cannot also
            # collapse or shift the take timeline in Studio.
            manifest_duration = evidence.get("duration_s")
            duration = (
                float(manifest_duration)
                if isinstance(manifest_duration, (int, float))
                and math.isfinite(float(manifest_duration))
                and float(manifest_duration) >= 0.0
                else 0.0
            )
            manifest_rate = evidence.get("sample_rate")
            rate = (
                int(manifest_rate)
                if isinstance(manifest_rate, (int, float))
                and math.isfinite(float(manifest_rate))
                and float(manifest_rate) > 0.0
                else 0
            )
        alignment = evidence.get("alignment", {})
        if not isinstance(alignment, dict):
            alignment = {}
        alignment_drift = _safe_finite_float(alignment.get("drift_ppm", 0.0))
        if 1.0 + alignment_drift / 1_000_000.0 <= 0.0:
            alignment_drift = 0.0
        manifest_offset = alignment.get(
            "effective_offset_s", evidence.get("offset_s")
        )
        offset = offsets.get(audio.name, 0.0)
        if isinstance(manifest_offset, (int, float)):
            offset = float(manifest_offset)
        declared_status = str(evidence.get("media_status") or "available")
        media_status = (
            "missing"
            if not available
            else declared_status
            if declared_status
            in {
                "available",
                "recovered",
                "partial",
                "missing",
                "damaged",
                "transferring",
                "transfer_failed",
            }
            else "available"
        )
        name = str(evidence.get("name") or _prettify(audio.stem))
        segment_infos: list[TrackSegmentInfo] = []
        raw_segments = evidence.get("segments", [])
        if isinstance(raw_segments, list) and raw_segments:
            for raw_segment in raw_segments:
                if not isinstance(raw_segment, dict):
                    continue
                relative = _safe_manifest_audio_path(raw_segment.get("path"))
                if relative is None:
                    reconciliation_errors.append(
                        f"{name} contains an invalid segment path."
                    )
                    continue
                segment_path = take_dir / relative
                segment_status = str(
                    raw_segment.get("media_status") or "available"
                )
                if segment_status not in {
                    "available",
                    "recovered",
                    "partial",
                    "missing",
                    "damaged",
                    "transferring",
                    "transfer_failed",
                }:
                    segment_status = "damaged"
                try:
                    segment_rate = int(raw_segment.get("sample_rate", 0))
                    frame_count = int(raw_segment.get("frame_count", 0))
                    channels = int(raw_segment.get("channels", 1))
                    start_frame = int(raw_segment.get("project_start_frame", 0))
                except (TypeError, ValueError):
                    segment_rate = frame_count = start_frame = 0
                    channels = 1
                if (
                    segment_rate <= 0
                    or frame_count < 0
                    or channels <= 0
                    or start_frame < 0
                ):
                    segment_status = "damaged"
                    reconciliation_errors.append(
                        f"{name} contains invalid segment audio facts."
                    )
                if not segment_path.is_file():
                    segment_status = "missing"
                    reconciliation_errors.append(
                        f"{name} is missing segment {relative}."
                    )
                else:
                    observed_duration, observed_rate = _probe_audio(segment_path)
                    observed_frames = int(round(observed_duration * observed_rate))
                    if (
                        observed_rate != segment_rate
                        or abs(observed_frames - frame_count) > 1
                    ):
                        segment_status = "damaged"
                        reconciliation_errors.append(
                            f"{name} segment {relative} changed after validation."
                        )
                gaps: list[tuple[int, int, tuple[int, ...], str]] = []
                raw_gaps = raw_segment.get("gaps", [])
                if isinstance(raw_gaps, list):
                    for gap in raw_gaps:
                        if not isinstance(gap, dict):
                            continue
                        try:
                            gap_start = int(gap.get("start_frame", -1))
                            gap_count = int(gap.get("frame_count", 0))
                            gap_channels = tuple(
                                int(item) for item in gap.get("channels", [])
                            )
                        except (TypeError, ValueError):
                            continue
                        if gap_start >= 0 and gap_count > 0:
                            gaps.append(
                                (
                                    gap_start,
                                    gap_count,
                                    gap_channels,
                                    str(gap.get("reason") or "disclosed_gap")[:120],
                                )
                            )
                segment_infos.append(
                    TrackSegmentInfo(
                        path=segment_path,
                        project_start_frame=start_frame,
                        frame_count=max(0, frame_count),
                        samplerate=max(0, segment_rate),
                        channels=max(1, channels),
                        media_status=segment_status,
                        segment_id=str(raw_segment.get("segment_id") or ""),
                        sha256=str(raw_segment.get("sha256") or ""),
                        gaps=tuple(gaps),
                    )
                )
            if segment_infos:
                audio = segment_infos[0].path
                rate = segment_infos[0].samplerate
                drift_scale = 1.0 + alignment_drift / 1_000_000.0
                timeline_rate = project_rate or rate
                duration = max(
                    (
                        segment.project_start_frame / timeline_rate
                        + segment.duration_s * drift_scale
                        for segment in segment_infos
                    ),
                    default=0.0,
                )
                blocked_statuses = {
                    segment.media_status
                    for segment in segment_infos
                    if segment.media_status not in {"available", "recovered"}
                }
                if blocked_statuses:
                    media_status = (
                        "missing" if "missing" in blocked_statuses else sorted(blocked_statuses)[0]
                    )
        if not segment_infos and available and rate > 0:
            try:
                import soundfile as sf  # type: ignore

                info = sf.info(str(audio))
                channels = int(info.channels)
                frames = int(info.frames)
            except Exception:  # noqa: BLE001
                channels = 1
                frames = int(round(duration * rate))
            segment_infos.append(
                TrackSegmentInfo(
                    path=audio,
                    project_start_frame=0,
                    frame_count=frames,
                    samplerate=rate,
                    channels=channels,
                    media_status=media_status,
                )
            )
        tracks.append(TrackInfo(
            path=audio,
            name=name,
            # Signed: local stems normally start before the server take, so a
            # negative offset here is valid alignment, not an error.
            offset_s=offset,
            duration_s=duration,
            samplerate=rate,
            source=str(evidence.get("source") or "jamulus_server"),
            media_status=media_status,
            track_id=str(evidence.get("track_id") or ""),
            source_id=str(evidence.get("source_id") or ""),
            participant_id=str(evidence.get("participant_id") or ""),
            instrument=str(evidence.get("instrument") or ""),
            quality=str(evidence.get("quality") or "unverified"),
            segments=tuple(segment_infos),
            drift_ppm=alignment_drift,
            alignment_confidence=max(
                0.0,
                min(1.0, _safe_finite_float(alignment.get("confidence", 0.0))),
            ),
            alignment_method=str(alignment.get("method") or "unverified"),
        ))
        if media_status == "missing":
            reconciliation_errors.append(
                f"{name} is missing from this take ({audio.name})."
            )

    def _string_items(key: str) -> tuple[str, ...]:
        value = manifest.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    manifest_errors = list(_string_items("errors"))
    for error in reconciliation_errors:
        if error not in manifest_errors:
            manifest_errors.append(error)
    validation_status = str(manifest.get("status") or "unchecked")
    if reconciliation_errors:
        validation_status = "needs_attention"

    return TakeInfo(
        path=take_dir,
        name=take_dir.name,
        tracks=tracks,
        reaper_project=reaper,
        validation_status=validation_status,
        manifest_path=manifest_path,
        manifest_errors=tuple(manifest_errors),
        manifest_warnings=_string_items("warnings"),
        session_title=str(manifest.get("session_title") or "").strip(),
        session_id=str(manifest.get("session_id") or ""),
        take_id=str(manifest.get("take_id") or ""),
        project_samplerate=project_rate,
    )


def discover_takes(root: str | Path) -> List[TakeInfo]:
    """Scan ``root`` for take folders, newest first.

    A take folder is any immediate subdirectory containing audio files; if
    ``root`` itself directly contains audio files it's treated as a single
    take. Never raises — an unreadable root yields an empty list.
    """
    root = Path(root).expanduser()
    if not root.is_dir():
        return []

    takes: List[TakeInfo] = []
    try:
        # root-as-single-take
        direct = load_take(root)
        if direct is not None:
            takes.append(direct)
        for child in root.iterdir():
            if _is_visible_take_directory(child):
                take = load_take(child)
                if take is not None:
                    takes.append(take)
    except OSError as exc:
        _logger.warning("could not scan takes root %s: %s", root, exc)
        return takes

    def _mtime(take: TakeInfo) -> float:
        # exists()+stat() would TOCTOU-race a deleted folder; stat() alone,
        # guarded, keeps the documented "never raises" contract.
        try:
            return take.path.stat().st_mtime
        except OSError:
            return 0.0

    takes.sort(key=_mtime, reverse=True)
    return takes
