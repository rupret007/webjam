"""Explicit, offline Logic handoff from a stopped session manifest or a stub."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import wave

from core.logic_handoff import (
    AudioStem,
    HandoffSession,
    LogicHandoffError,
    Marker,
    MidiNote,
    MidiTrack,
    export_logic_handoff,
)


def _object(value: object, *, required: set[str], optional: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise LogicHandoffError(f"{label} must be an object")
    if required - value.keys():
        raise LogicHandoffError(f"{label} is missing required fields: {', '.join(sorted(required - value.keys()))}")
    if value.keys() - required - optional:
        raise LogicHandoffError(f"{label} contains unsupported fields: {', '.join(sorted(value.keys() - required - optional))}")
    return value


def _array(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise LogicHandoffError(f"{label} must be an array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LogicHandoffError(f"{label} must be a nonempty string")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise LogicHandoffError(f"{label} must be an integer")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise LogicHandoffError(f"Manifest contains duplicate field: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise LogicHandoffError(f"Manifest contains a nonfinite number: {value}")


def _resolved_path(value: str | Path, label: str) -> Path:
    try:
        return Path(value).expanduser().resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise LogicHandoffError(f"{label} must be a usable local path") from exc


def load_session(path: Path) -> HandoffSession:
    """Load only the explicit phase-one schema; never infer missing alignment."""
    path = _resolved_path(path, "Manifest path")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except LogicHandoffError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise LogicHandoffError("Cannot read a valid UTF-8 session JSON manifest") from exc
    data = _object(
        raw,
        required={"name", "sample_rate", "frames", "bpm", "stems"},
        optional={"midi_tracks", "markers", "numerator", "denominator"},
        label="session",
    )
    bpm = data["bpm"]
    try:
        finite_bpm = type(bpm) in (int, float) and math.isfinite(bpm)
    except OverflowError:
        finite_bpm = False
    if not finite_bpm:
        raise LogicHandoffError("session.bpm must be a finite number")
    stems = []
    for index, raw_stem in enumerate(_array(data["stems"], "session.stems")):
        label = f"stems[{index}]"
        stem = _object(raw_stem, required={"name", "path"}, optional={"start_frame"}, label=label)
        source = Path(_text(stem["path"], f"{label}.path"))
        if str(source).startswith("~"):
            source = _resolved_path(source, f"{label}.path")
        if not source.is_absolute():
            source = path.parent / source
        stems.append(AudioStem(
            name=_text(stem["name"], f"{label}.name"),
            path=_resolved_path(source, f"{label}.path"),
            start_frame=_integer(stem.get("start_frame", 0), f"{label}.start_frame"),
        ))
    midi_tracks = []
    for index, raw_track in enumerate(_array(data.get("midi_tracks", []), "session.midi_tracks")):
        label = f"midi_tracks[{index}]"
        track = _object(raw_track, required={"name", "notes"}, optional=set(), label=label)
        notes = []
        for note_index, raw_note in enumerate(_array(track["notes"], f"{label}.notes")):
            note_label = f"{label}.notes[{note_index}]"
            note = _object(
                raw_note,
                required={"start_frame", "duration_frames", "note"},
                optional={"velocity", "channel"},
                label=note_label,
            )
            notes.append(MidiNote(
                start_frame=_integer(note["start_frame"], f"{note_label}.start_frame"),
                duration_frames=_integer(note["duration_frames"], f"{note_label}.duration_frames"),
                note=_integer(note["note"], f"{note_label}.note"),
                velocity=_integer(note.get("velocity", 100), f"{note_label}.velocity"),
                channel=_integer(note.get("channel", 0), f"{note_label}.channel"),
            ))
        midi_tracks.append(MidiTrack(name=_text(track["name"], f"{label}.name"), notes=tuple(notes)))
    markers = []
    for index, raw_marker in enumerate(_array(data.get("markers", []), "session.markers")):
        label = f"markers[{index}]"
        marker = _object(raw_marker, required={"name", "frame"}, optional=set(), label=label)
        markers.append(Marker(
            name=_text(marker["name"], f"{label}.name"),
            frame=_integer(marker["frame"], f"{label}.frame"),
        ))
    return HandoffSession(
        name=_text(data["name"], "session.name"),
        sample_rate=_integer(data["sample_rate"], "session.sample_rate"),
        frames=_integer(data["frames"], "session.frames"),
        bpm=bpm,
        stems=tuple(stems),
        midi_tracks=tuple(midi_tracks),
        markers=tuple(markers),
        numerator=_integer(data.get("numerator", 4), "session.numerator"),
        denominator=_integer(data.get("denominator", 4), "session.denominator"),
    )


def _stub_session(folder: Path) -> HandoffSession:
    """Create four seconds of file-only audio and explicit pitched note data."""
    sample_rate = 48_000
    frames = sample_rate * 4
    beat_frames = sample_rate // 2
    pitches = (60, 64, 67, 72, 67, 64, 62, 60)
    for filename, channels in (("bass.wav", 1), ("melody.wav", 2)):
        pcm = bytearray()
        for frame in range(frames):
            beat_frame = frame % beat_frames
            envelope = max(0.0, 1.0 - beat_frame / (beat_frames * 0.75))
            pitch = 45 if channels == 1 else pitches[frame // beat_frames]
            frequency = 440.0 * 2 ** ((pitch - 69) / 12)
            value = round(0.15 * envelope * math.sin(2 * math.pi * frequency * beat_frame / sample_rate) * 8_388_607)
            for channel in range(channels):
                sample = value if channel == 0 else round(value * 0.8)
                pcm.extend(sample.to_bytes(3, "little", signed=True))
        with wave.open(str(folder / filename), "wb") as handle:
            handle.setparams((channels, 3, sample_rate, frames, "NONE", "not compressed"))
            handle.writeframes(pcm)
    return HandoffSession(
        name="Logic Handoff Stub",
        sample_rate=sample_rate,
        frames=frames,
        bpm=120.0,
        stems=(AudioStem("Bass", folder / "bass.wav"), AudioStem("Melody", folder / "melody.wav")),
        midi_tracks=(MidiTrack("Melody", tuple(
            MidiNote(index * beat_frames, beat_frames * 3 // 4, pitch)
            for index, pitch in enumerate(pitches)
        )),),
        markers=(Marker("Start", 0), Marker("Middle", sample_rate * 2)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="logic_handoff", allow_abbrev=False,
        description="Export a stopped session or synthetic stub as aligned PCM24 WAV stems, MIDI and tempo metadata. No devices are opened.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--stub", action="store_true", help="export four seconds of synthetic audio and pitched MIDI notes")
    source.add_argument("--session", type=Path, metavar="SESSION.json", help="explicit stopped-session manifest; audio paths are relative to this file")
    parser.add_argument("--destination-root", type=Path, metavar="DIRECTORY", help="parent for a new handoff folder (default: ~/Music/WebJam/LogicHandoff)")
    args = parser.parse_args(argv)
    try:
        destination = _resolved_path(args.destination_root, "Destination root") if args.destination_root is not None else None
        if args.stub:
            with tempfile.TemporaryDirectory(prefix="webjam-logic-stub-") as temporary:
                result = export_logic_handoff(_stub_session(Path(temporary)), destination_root=destination)
        else:
            result = export_logic_handoff(load_session(args.session), destination_root=destination)
    except (LogicHandoffError, OSError, wave.Error) as exc:
        print(f"Logic handoff failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "folder": str(result.folder),
        "stems": [str(path) for path in result.stems],
        "midi": str(result.midi),
        "tempo": str(result.tempo),
        "readme": str(result.readme),
        "sample_rate": result.sample_rate,
        "frames": result.frames,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
