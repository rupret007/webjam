"""Adapter: build a HandoffSession from a completed WebJam take.

This module bridges the recording infrastructure (TakeProject, TakeInfo) and
the Logic handoff writer (HandoffSession). It reads exact per-source stems with
their start_frame alignment from the take's manifest/timeline data, the project
sample rate, BPM and meter from the project when defined (otherwise documented
defaults), and markers where the project has them.

The adapter only includes MIDI tracks if WebJam actually has real note data for
that session (currently never, as WebJam does not capture MIDI). It never
invents timing from file names or timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.logic_handoff import (
    LOGIC_SAMPLE_RATES,
    AudioStem,
    HandoffSession,
    Marker,
)
from core.take_project import TakeProject, load_take_project

DEFAULT_BPM = 120.0
DEFAULT_NUMERATOR = 4
DEFAULT_DENOMINATOR = 4

_MAX_SESSION_NAME = 200


@dataclass(frozen=True)
class LogicHandoffAdapterResult:
    """Result of building a HandoffSession from a take."""

    session: HandoffSession
    bpm_is_default: bool
    time_signature_is_default: bool
    markers_added: int


class LogicHandoffAdapterError(ValueError):
    """The take cannot be adapted for Logic handoff."""


def _load_project(take_path: Path) -> TakeProject:
    """Load and validate a TakeProject from a take directory."""
    try:
        project = load_take_project(take_path)
    except Exception as exc:
        raise LogicHandoffAdapterError(
            f"Could not load take project: {exc}"
        ) from exc
    if project is None:
        raise LogicHandoffAdapterError(
            "The take has no valid project manifest. Complete the take and try again."
        )
    return project


def _validate_sample_rate(rate: int) -> None:
    """Validate that the sample rate is supported by Logic."""
    if rate not in LOGIC_SAMPLE_RATES:
        raise LogicHandoffAdapterError(
            f"The project sample rate {rate} Hz is not supported by Logic. "
            f"Supported rates are: {', '.join(str(r) for r in LOGIC_SAMPLE_RATES)} Hz."
        )


def _compute_session_frames(project: TakeProject) -> int:
    """Compute the total session frames from track data.

    Uses the maximum extent of all tracks (start_frame + source frames).
    """
    max_frame = 0

    for track in project.tracks:
        for segment in track.segments:
            segment_end = segment.project_start_frame + segment.frame_count
            max_frame = max(max_frame, segment_end)

    if max_frame <= 0:
        raise LogicHandoffAdapterError(
            "The take contains no audio data to export."
        )

    return max_frame


def _validate_segment_path(
    segment_path: str,
    take_path: Path,
    track_name: str,
    segment_index: int,
) -> Path:
    """Validate and resolve a segment path, ensuring it's inside the take directory.

    Raises LogicHandoffAdapterError if the path is invalid, escapes the take
    directory, or is a symlink.
    """
    raw_path = take_path / segment_path
    try:
        resolved = raw_path.resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise LogicHandoffAdapterError(
            f"Cannot resolve path for '{track_name}' segment {segment_index + 1}: {exc}"
        ) from exc

    if raw_path.is_symlink():
        raise LogicHandoffAdapterError(
            f"Symlinks are not allowed: '{track_name}' segment {segment_index + 1} "
            f"at '{segment_path}' is a symbolic link."
        )

    try:
        resolved.relative_to(take_path)
    except ValueError:
        raise LogicHandoffAdapterError(
            f"Path traversal detected: '{track_name}' segment {segment_index + 1} "
            f"at '{segment_path}' resolves outside the take directory."
        )

    if not resolved.is_file():
        raise LogicHandoffAdapterError(
            f"Missing audio file for '{track_name}' segment {segment_index + 1}: "
            f"'{segment_path}' does not exist or is not a file."
        )

    return resolved


def _check_segment_overlaps(
    segments: list[tuple[str, int, int]],
    track_name: str,
) -> None:
    """Check for overlapping segments within a track.

    Args:
        segments: List of (name, start_frame, end_frame) tuples.
        track_name: Track name for error messages.

    Raises:
        LogicHandoffAdapterError if segments overlap.
    """
    sorted_segs = sorted(segments, key=lambda s: s[1])
    for i in range(1, len(sorted_segs)):
        prev_name, _, prev_end = sorted_segs[i - 1]
        curr_name, curr_start, _ = sorted_segs[i]
        if curr_start < prev_end:
            raise LogicHandoffAdapterError(
                f"Overlapping segments in track '{track_name}': "
                f"'{prev_name}' and '{curr_name}' overlap. "
                "Flatten or resolve overlaps before exporting."
            )


def _build_stems(
    project: TakeProject,
    take_path: Path,
) -> tuple[AudioStem, ...]:
    """Build AudioStem entries from the take's track data.

    Exports every segment from every track. Each segment becomes its own stem
    with exact start_frame alignment from the recording manifest/timeline.
    Multi-segment tracks get numbered stems (e.g., "Guitar", "Guitar (part 2)").

    Raises LogicHandoffAdapterError if:
    - A segment path escapes the take directory (path traversal)
    - A segment path is a symlink
    - A segment file is missing
    - Segments within a track overlap
    """
    stems: list[AudioStem] = []

    for project_track in sorted(project.tracks, key=lambda t: t.order):
        if not project_track.segments:
            continue

        track_segments: list[tuple[str, int, int]] = []

        for seg_idx, segment in enumerate(project_track.segments):
            resolved_path = _validate_segment_path(
                segment.path, take_path, project_track.name, seg_idx
            )

            start_frame = segment.project_start_frame
            end_frame = start_frame + segment.frame_count

            if len(project_track.segments) == 1:
                stem_name = project_track.name
            else:
                stem_name = (
                    project_track.name
                    if seg_idx == 0
                    else f"{project_track.name} (part {seg_idx + 1})"
                )

            track_segments.append((stem_name, start_frame, end_frame))

            stems.append(
                AudioStem(
                    name=stem_name,
                    path=resolved_path,
                    start_frame=start_frame,
                )
            )

        if len(track_segments) > 1:
            _check_segment_overlaps(track_segments, project_track.name)

    if not stems:
        raise LogicHandoffAdapterError(
            "No exportable audio stems found in the take. "
            "Ensure the take has completed recording and its files are accessible."
        )

    return tuple(stems)


def _build_markers(project: TakeProject) -> tuple[Marker, ...]:
    """Build Marker entries from the project's markers.

    Always includes at least a Start marker at frame 0.
    """
    markers: list[Marker] = []
    sample_rate = project.project_sample_rate

    has_start = False
    for marker in project.markers:
        frame = round(marker.position_s * sample_rate)
        if frame == 0 and marker.label.lower() in ("start", "beginning"):
            has_start = True
        markers.append(Marker(name=marker.label, frame=frame))

    if not has_start:
        markers.insert(0, Marker(name="Start", frame=0))

    return tuple(markers)


def build_handoff_session(
    take_path: str | Path,
) -> LogicHandoffAdapterResult:
    """Build a HandoffSession from a completed WebJam take.

    Args:
        take_path: Path to the take directory.

    Returns:
        LogicHandoffAdapterResult containing the session and metadata about
        defaults used.

    Raises:
        LogicHandoffAdapterError: If the take cannot be adapted.
    """
    path = Path(take_path).expanduser().resolve()
    if not path.is_dir():
        raise LogicHandoffAdapterError(
            "The take path does not exist or is not a directory."
        )

    project = _load_project(path)

    sample_rate = project.project_sample_rate
    _validate_sample_rate(sample_rate)

    bpm = project.tempo_bpm
    numerator = project.time_signature_numerator
    denominator = project.time_signature_denominator

    bpm_is_default = abs(bpm - DEFAULT_BPM) < 0.001
    time_signature_is_default = (
        numerator == DEFAULT_NUMERATOR and denominator == DEFAULT_DENOMINATOR
    )

    frames = _compute_session_frames(project)
    stems = _build_stems(project, path)
    markers = _build_markers(project)

    session_name = project.session_title or project.take_name or path.name
    session_name = session_name[:_MAX_SESSION_NAME]

    session = HandoffSession(
        name=session_name,
        sample_rate=sample_rate,
        frames=frames,
        bpm=bpm,
        stems=stems,
        midi_tracks=(),
        markers=markers,
        numerator=numerator,
        denominator=denominator,
    )

    return LogicHandoffAdapterResult(
        session=session,
        bpm_is_default=bpm_is_default,
        time_signature_is_default=time_signature_is_default,
        markers_added=len(markers),
    )


def can_export_to_logic(take_path: str | Path) -> tuple[bool, str]:
    """Check if a take can be exported to Logic.

    Performs full validation including path safety checks.

    Returns:
        A tuple of (can_export, reason). If can_export is False, reason
        explains why.
    """
    try:
        path = Path(take_path).expanduser().resolve()
        if not path.is_dir():
            return False, "The take path does not exist or is not a directory."

        project = _load_project(path)

        sample_rate = project.project_sample_rate
        if sample_rate not in LOGIC_SAMPLE_RATES:
            return False, (
                f"Sample rate {sample_rate} Hz is not supported by Logic."
            )

        if not project.tracks:
            return False, "The take contains no audio tracks."

        _build_stems(project, path)

        return True, ""

    except LogicHandoffAdapterError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Could not validate take: {exc}"


__all__ = [
    "DEFAULT_BPM",
    "DEFAULT_DENOMINATOR",
    "DEFAULT_NUMERATOR",
    "LogicHandoffAdapterError",
    "LogicHandoffAdapterResult",
    "build_handoff_session",
    "can_export_to_logic",
]
