"""Offline recording receipt for edited Studio export packages.

Pure formatter: no filesystem, Qt, clock, journal, or live recording access.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any

from core.redaction import redact_text
from core.take_project import (
    MediaSegment,
    ProjectStatus,
    ProjectTrack,
    SourceType,
    TakeProject,
)

_PLAN_UNAVAILABLE = (
    "Plan comparison unavailable. "
    "This saved take does not include the original per-source recording plan."
)
_MAX_LABEL_CHARS = 160
_MAX_SUMMARY_NOTE_CHARS = 120

_SOURCE_ROUTE_LABELS = {
    SourceType.LOCAL_ISOLATED: "Local Original",
    SourceType.JAMULUS_SERVER: "Jamulus server",
    SourceType.LIVE_REFERENCE: "Shared Track",
    SourceType.STUDIO_MIX: "Studio mix",
    SourceType.PROCESSED_STEM: "Processed stem",
    SourceType.UNKNOWN: "Unknown source route",
}

_STATUS_LABELS = {
    ProjectStatus.COMPLETE: "Complete",
    ProjectStatus.RECOVERED: "Recovered",
    ProjectStatus.NEEDS_ATTENTION: "Needs attention",
    ProjectStatus.RECORDING: "Recording",
    ProjectStatus.FINALIZING: "Finalizing",
}

class RecordingReceiptError(ValueError):
    """Raised when receipt inputs disagree with export provenance."""


def render_recording_receipt(
    primary: TakeProject,
    contributing_projects: Mapping[str, TakeProject],
    provenance: Mapping[str, Any],
) -> str:
    """Return deterministic UTF-8 Markdown for one completed export snapshot."""

    if not isinstance(primary, TakeProject):
        raise RecordingReceiptError("The primary take project is required.")
    _validate_provenance_identity(primary, contributing_projects, provenance)
    original_stems = _require_sequence(provenance, "original_stems")
    selection = provenance.get("selection") or {}
    export_track_ids = {
        str(item) for item in selection.get("export_included_track_ids") or ()
    }
    timeline = provenance.get("timeline") or {}
    audio_format = provenance.get("audio_format") or {}
    produced_by = provenance.get("produced_by") or {}

    lines: list[str] = [
        "# WebJam Recording Receipt",
        "",
        (
            f"**Take:** {_bounded_label(primary.take_name)} "
            f"(`{_bounded_label(primary.take_id, redact=False)}`)"
        ),
        f"**Session:** {_bounded_label(primary.session_title)}",
        f"**Saved take status:** {_status_label(primary)}",
        f"**Exported at:** {_bounded_label(produced_by.get('exported_at_utc', 'unknown'))}",
        (
            f"**Export build:** {_bounded_label(produced_by.get('application', 'WebJam'))} "
            f"{_bounded_label(produced_by.get('version', 'unknown'))}"
        ),
        "",
        "## Intended sources",
        "",
        _PLAN_UNAVAILABLE,
        "",
    ]

    warning_count = len(primary.warnings)
    error_count = len(primary.errors)
    recovery_notes = tuple(primary.session_evidence.recovery_notes)
    recovery_count = len(recovery_notes)
    if warning_count or error_count or recovery_count:
        lines.extend(["## Saved take notes", ""])
        if error_count:
            lines.append(
                f"- Errors: {error_count} (see "
                f"[source take manifest]({_primary_manifest_relative(primary.take_id)})"
                ")"
            )
        if warning_count:
            lines.append(
                f"- Warnings: {warning_count} (see "
                f"[source take manifest]({_primary_manifest_relative(primary.take_id)})"
                ")"
            )
        if recovery_count:
            summary = _bounded_note_summary(recovery_notes)
            lines.append(
                f"- Recovery notes: {recovery_count}"
                + (f" — {summary}" if summary else "")
                + f" (see [source take manifest]"
                f"({_primary_manifest_relative(primary.take_id)}))"
            )
        lines.append("")

    lines.extend(
        [
            "## Sources represented in this package",
            "",
            (
                f"{len(original_stems)} saved source track"
                f"{'s' if len(original_stems) != 1 else ''} represented in this package."
            ),
            "",
            "| Source | Take | Route | Recorded format | Listed gaps | Signal |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for stem in original_stems:
        if not isinstance(stem, Mapping):
            raise RecordingReceiptError("Each original stem must be a mapping.")
        take_id = str(stem.get("take_id") or "")
        track_id = str(stem.get("track_id") or "")
        track = _resolve_track(contributing_projects, take_id, track_id)
        segments = _stem_segments(track, stem)
        take_label = _take_label(contributing_projects, take_id, primary.take_id)
        lines.append(
            "| "
            + " | ".join(
                (
                    _bounded_label(track.name),
                    take_label,
                    _source_route_label(track.source_type),
                    _recorded_format(segments),
                    str(_listed_gap_count(segments)),
                    _signal_label(segments),
                )
            )
            + " |"
        )
    lines.append("")

    sample_rate = int(timeline.get("sample_rate") or primary.project_sample_rate)
    frame_count = int(timeline.get("frame_count") or 0)
    duration = float(timeline.get("duration_seconds") or 0.0)
    encoding = str(audio_format.get("encoding") or "PCM_24")
    lines.extend(
        [
            "## Package output",
            "",
            (
                f"Exported audio in this package is stereo {encoding.replace('_', '-')} WAV "
                f"at {sample_rate} Hz with a shared {frame_count}-frame timeline "
                f"({duration:.6f} s)."
            ),
            "",
            (
                "Aligned-original WAV files are manifest-aligned unity renders, not "
                "byte-identical copies of capture files. Edited stems and the rough mix "
                "follow Studio arrangement and processing choices."
            ),
            "",
            "## Live measurements",
            "",
            "Live dropout and overload measurements: unavailable in this saved receipt.",
            "A listed gap count is documented source evidence only, not a live dropout count.",
            "",
        ]
    )

    clipping_lines = _render_clipping_section(provenance.get("outputs") or ())
    if clipping_lines:
        lines.extend(clipping_lines)

    excluded = _excluded_primary_tracks(primary, export_track_ids)
    if excluded:
        lines.extend(["## Export selection", ""])
        for track in excluded:
            lines.append(
                f"- {_bounded_label(track.name)} — Not included in this export"
            )
        lines.append("")
        lines.append(
            "Included sources may be absent from the edited mix because of Studio choices."
        )
        lines.append("")

    lines.extend(["## Supporting evidence", ""])
    lines.append("- [provenance.json](provenance.json)")
    lines.append(
        f"- [source take manifest]({_primary_manifest_relative(primary.take_id)})"
    )
    lines.extend(_extra_manifest_links(contributing_projects, primary.take_id))
    lines.extend(
        [
            "- [import instructions](IMPORT_INSTRUCTIONS.md)",
            "- [checksums](SHA256SUMS.txt)",
            "",
            "This receipt describes this fixed export snapshot only.",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_provenance_identity(
    primary: TakeProject,
    contributing_projects: Mapping[str, TakeProject],
    provenance: Mapping[str, Any],
) -> None:
    if str(provenance.get("session_id") or "") != primary.session_id:
        raise RecordingReceiptError("Receipt provenance session_id does not match.")
    if str(provenance.get("take_id") or "") != primary.take_id:
        raise RecordingReceiptError("Receipt provenance take_id does not match.")
    revision = provenance.get("take_project_revision")
    if revision != primary.revision:
        raise RecordingReceiptError(
            "Receipt provenance take_project_revision does not match."
        )
    if not contributing_projects:
        raise RecordingReceiptError("Contributing take projects are required.")
    for take_id, project in contributing_projects.items():
        if not isinstance(project, TakeProject):
            raise RecordingReceiptError("Each contributing project must be a TakeProject.")
        if project.take_id != take_id:
            raise RecordingReceiptError(
                "A contributing project take_id does not match its snapshot key."
            )
        if project.session_id != primary.session_id:
            raise RecordingReceiptError(
                "A contributing project session_id does not match the primary take."
            )
    for stem in _require_sequence(provenance, "original_stems"):
        if not isinstance(stem, Mapping):
            raise RecordingReceiptError("Each original stem must be a mapping.")
        take_id = str(stem.get("take_id") or "")
        track_id = str(stem.get("track_id") or "")
        track = _resolve_track(contributing_projects, take_id, track_id)
        _stem_segments(track, stem)


def _require_sequence(provenance: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = provenance.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RecordingReceiptError(f"Provenance {key} must be a sequence.")
    return value


def _resolve_track(
    contributing_projects: Mapping[str, TakeProject],
    take_id: str,
    track_id: str,
) -> ProjectTrack:
    project = contributing_projects.get(take_id)
    if project is None:
        raise RecordingReceiptError(
            f"No contributing take project matches original stem take_id {take_id!r}."
        )
    for track in project.tracks:
        if track.track_id == track_id:
            return track
    raise RecordingReceiptError(
        f"No track {track_id!r} on take {take_id!r} matches original stem provenance."
    )


def _stem_segments(track: ProjectTrack, stem: Mapping[str, Any]) -> tuple[MediaSegment, ...]:
    source_keys = stem.get("source_keys")
    if not isinstance(source_keys, Sequence) or isinstance(source_keys, (str, bytes)):
        raise RecordingReceiptError("Original stem source_keys must be a sequence.")
    expected_ids = {
        str(item.get("segment_id") or "")
        for item in source_keys
        if isinstance(item, Mapping)
    }
    if not expected_ids:
        raise RecordingReceiptError("Original stem source_keys are required.")
    active_segments = {
        segment.segment_id: segment
        for segment in track.segments
        if segment.frame_count > 0
    }
    if expected_ids != set(active_segments):
        raise RecordingReceiptError(
            "Original stem source_keys do not match the saved source track segments."
        )
    return tuple(active_segments[segment_id] for segment_id in sorted(expected_ids))


def _status_label(project: TakeProject) -> str:
    return _STATUS_LABELS.get(project.effective_status, project.effective_status.value)


def _source_route_label(source_type: SourceType) -> str:
    return _SOURCE_ROUTE_LABELS.get(source_type, "Unknown source route")


def _recorded_format(segments: Sequence[MediaSegment]) -> str:
    channels = {segment.channels for segment in segments}
    rates = sorted({segment.sample_rate for segment in segments})
    formats = sorted({segment.sample_format for segment in segments})
    if channels == {1}:
        topology = "mono"
    elif channels == {2}:
        topology = "stereo"
    else:
        topology = f"mixed ({', '.join(str(item) for item in sorted(channels))} ch)"
    rate_text = ", ".join(f"{rate} Hz" for rate in rates)
    format_text = ", ".join(formats)
    return f"recorded {topology}; {rate_text}; {format_text}"


def _listed_gap_count(segments: Sequence[MediaSegment]) -> int:
    return sum(len(segment.gaps) for segment in segments)


def _signal_label(segments: Sequence[MediaSegment]) -> str:
    values = {segment.has_signal for segment in segments}
    if values == {True}:
        return "present"
    if values == {False}:
        return "absent"
    return "unknown"


def _take_label(
    contributing_projects: Mapping[str, TakeProject],
    take_id: str,
    primary_take_id: str,
) -> str:
    project = contributing_projects[take_id]
    name = _bounded_label(project.take_name)
    if take_id == primary_take_id:
        return f"{name} (primary)"
    return f"{name} (`{_bounded_label(take_id, redact=False)}`)"


def _primary_manifest_relative(primary_take_id: str) -> str:
    return "source-take-manifest.json"


def _extra_manifest_links(
    contributing_projects: Mapping[str, TakeProject],
    primary_take_id: str,
) -> list[str]:
    links: list[str] = []
    for take_id in sorted(contributing_projects):
        if take_id == primary_take_id:
            continue
        relative = f"source-take-manifests/{take_id}.json"
        project = contributing_projects[take_id]
        label = _bounded_label(project.take_name)
        links.append(f"- [{label} source manifest]({relative})")
    return links


def _excluded_primary_tracks(
    primary: TakeProject,
    export_track_ids: set[str],
) -> tuple[ProjectTrack, ...]:
    return tuple(
        track
        for track in sorted(primary.tracks, key=lambda item: (item.order, item.track_id))
        if track.track_id not in export_track_ids
    )


def _render_clipping_section(outputs: Sequence[Any]) -> list[str]:
    clipped: list[str] = []
    for item in outputs:
        if not isinstance(item, Mapping):
            continue
        count = item.get("clipped_sample_count")
        if count is None:
            continue
        if int(count) <= 0:
            continue
        relative = str(item.get("relative_path") or "")
        if not relative or "/" in relative or "\\" in relative:
            clipped.append(
                f"- Rendered-output clipping: {int(count)} samples in {relative}"
            )
        else:
            clipped.append(
                f"- Rendered-output clipping: {int(count)} samples in "
                f"[{relative}]({relative})"
            )
    if not clipped:
        return []
    return ["## Rendered-output clipping", "", *clipped, ""]


def _bounded_label(value: object, *, redact: bool = True) -> str:
    text = str(value or "")
    if redact:
        text = redact_text(text)
        text = re.sub(r"(?i)\bwebjam:(?://)?\[redacted\]", "private invite", text)
    text = " ".join(text.split())[:_MAX_LABEL_CHARS]
    return _escape_markdown(text) if text else "—"


def _bounded_note_summary(notes: Sequence[str]) -> str:
    if not notes:
        return ""
    first = _bounded_label(notes[0], redact=True)
    if len(notes) == 1:
        return first[:_MAX_SUMMARY_NOTE_CHARS]
    return f"{first[:_MAX_SUMMARY_NOTE_CHARS]} (+{len(notes) - 1} more)"


def _escape_markdown(text: str) -> str:
    without_controls = "".join(
        char if ord(char) >= 32 and ord(char) != 127 else " " for char in text
    )
    return html.escape(without_controls, quote=True)
