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
    AudioStem,
    HandoffSession,
    LogicHandoffError,
    Marker,
    MidiTrack,
    LOGIC_SAMPLE_RATES,
)
from core.take_library import TakeInfo, load_take
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


def _load_take_info(take_path: Path) -> TakeInfo:
    """Load TakeInfo from a take directory."""
    try:
        info = load_take(take_path)
    except Exception as exc:
        raise LogicHandoffAdapterError(
            f"Could not load take info: {exc}"
        ) from exc
    if info is None:
        raise LogicHandoffAdapterError(
            "The take folder contains no readable audio tracks."
        )
    return info


def _validate_sample_rate(rate: int) -> None:
    """Validate that the sample rate is supported by Logic."""
    if rate not in LOGIC_SAMPLE_RATES:
        raise LogicHandoffAdapterError(
            f"The project sample rate {rate} Hz is not supported by Logic. "
            f"Supported rates are: {', '.join(str(r) for r in LOGIC_SAMPLE_RATES)} Hz."
        )


def _compute_session_frames(project: TakeProject, take_info: TakeInfo) -> int:
    """Compute the total session frames from track data.

    Uses the maximum extent of all tracks (start_frame + source frames).
    """
    max_frame = 0
    sample_rate = project.project_sample_rate

    for track in project.tracks:
        for segment in track.segments:
            segment_end = segment.project_start_frame + segment.frame_count
            max_frame = max(max_frame, segment_end)

    if max_frame <= 0:
        for track in take_info.tracks:
            offset_frames = round(track.offset_s * sample_rate)
            duration_frames = round(track.duration_s * sample_rate)
            track_end = offset_frames + duration_frames
            max_frame = max(max_frame, track_end)

    if max_frame <= 0:
        raise LogicHandoffAdapterError(
            "The take contains no audio data to export."
        )

    return max_frame


def _build_stems(
    project: TakeProject,
    take_info: TakeInfo,
    take_path: Path,
) -> tuple[AudioStem, ...]:
    """Build AudioStem entries from the take's track data.

    Uses exact start_frame alignment from the recording manifest/timeline.
    """
    stems: list[AudioStem] = []
    sample_rate = project.project_sample_rate

    track_paths: dict[str, Path] = {}
    for track in take_info.tracks:
        if track.path.is_file():
            track_paths[track.name] = track.path

    for project_track in sorted(project.tracks, key=lambda t: t.order):
        if not project_track.segments:
            continue

        primary_segment = project_track.primary_segment
        segment_path = take_path / primary_segment.path
        if not segment_path.is_file():
            if project_track.name in track_paths:
                segment_path = track_paths[project_track.name]
            else:
                continue

        start_frame = primary_segment.project_start_frame

        stems.append(
            AudioStem(
                name=project_track.name,
                path=segment_path,
                start_frame=start_frame,
            )
        )

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
    take_info = _load_take_info(path)

    sample_rate = project.project_sample_rate
    _validate_sample_rate(sample_rate)

    bpm = project.tempo_bpm
    numerator = project.time_signature_numerator
    denominator = project.time_signature_denominator

    bpm_is_default = abs(bpm - DEFAULT_BPM) < 0.001
    time_signature_is_default = (
        numerator == DEFAULT_NUMERATOR and denominator == DEFAULT_DENOMINATOR
    )

    frames = _compute_session_frames(project, take_info)
    stems = _build_stems(project, take_info, path)
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

        take_info = _load_take_info(path)

        if not project.tracks and not take_info.tracks:
            return False, "The take contains no audio tracks."

        has_file = False
        for track in project.tracks:
            for segment in track.segments:
                segment_path = path / segment.path
                if segment_path.is_file():
                    has_file = True
                    break
            if has_file:
                break

        if not has_file:
            for track in take_info.tracks:
                if track.path.is_file():
                    has_file = True
                    break

        if not has_file:
            return False, "No accessible audio files found in the take."

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
