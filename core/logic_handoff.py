"""Offline, explicit-session handoff to Logic: PCM24 WAV + SMF Type 1.

This writer accepts already-aligned, stopped sources. It does not interpret a
Studio project, capture MIDI, resample audio, or change original media. Phase 1
supports one constant tempo and time signature. All output uses frame zero as
its origin; MIDI's unavoidable tick rounding is recorded in the pack.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import csv
import io
import stat
import struct
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path


MIDI_PPQ = 32760
LOGIC_SAMPLE_RATES = (44100, 48000, 88200, 96000, 176400, 192000)
_CHUNK_FRAMES = 65536
_MAX_VLQ = 0x0FFFFFFF


class LogicHandoffError(ValueError):
    """The requested pack could not be represented without losing information."""


@dataclass(frozen=True)
class AudioStem:
    name: str
    path: Path
    start_frame: int = 0


@dataclass(frozen=True)
class MidiNote:
    start_frame: int
    duration_frames: int
    note: int
    velocity: int = 100
    channel: int = 0


@dataclass(frozen=True)
class MidiTrack:
    name: str
    notes: tuple[MidiNote, ...]


@dataclass(frozen=True)
class Marker:
    name: str
    frame: int


@dataclass(frozen=True)
class HandoffSession:
    name: str
    sample_rate: int
    frames: int
    bpm: float
    stems: tuple[AudioStem, ...]
    midi_tracks: tuple[MidiTrack, ...] = ()
    markers: tuple[Marker, ...] = ()
    numerator: int = 4
    denominator: int = 4


@dataclass(frozen=True)
class HandoffResult:
    folder: Path
    stems: tuple[Path, ...]
    midi: Path
    tempo: Path
    readme: Path
    import_map: Path
    sample_rate: int
    frames: int


def _integer(value: int, label: str, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise LogicHandoffError(f"{label} must be an integer from {low} to {high}.")


def _label(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise LogicHandoffError(f"{label} must be 1–200 printable characters.")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise LogicHandoffError(f"{label} must be valid UTF-8 text.") from exc


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")[:64] or "session"


def _tick(frame: int, sample_rate: int, tempo_us: int) -> int:
    return round(Fraction(frame * 1_000_000 * MIDI_PPQ, sample_rate * tempo_us))


def _validate(session: HandoffSession) -> int:
    if not isinstance(session, HandoffSession):
        raise LogicHandoffError("An explicit HandoffSession is required.")
    _label(session.name, "Session name")
    _integer(session.sample_rate, "Sample rate", 44100, 192000)
    if session.sample_rate not in LOGIC_SAMPLE_RATES:
        raise LogicHandoffError(f"Logic project sample rate must be one of {LOGIC_SAMPLE_RATES} Hz.")
    _integer(session.frames, "Session frames", 1, session.sample_rate * 86400)
    if (
        type(session.bpm) not in (int, float)
        or not 5 <= session.bpm <= 990
        or not math.isfinite(session.bpm)
    ):
        raise LogicHandoffError("BPM must be a finite number from 5 to 990.")
    tempo_us = round(Fraction(60_000_000) / Fraction(str(session.bpm)))
    _integer(session.numerator, "Time signature numerator", 1, 255)
    if type(session.denominator) is not int or session.denominator not in (
        1, 2, 4, 8, 16, 32, 64,
    ):
        raise LogicHandoffError("Time signature denominator must be a power of two, 1–64.")
    if not isinstance(session.stems, tuple) or not 1 <= len(session.stems) <= 256:
        raise LogicHandoffError("Supply a tuple of 1–256 audio stems.")
    for stem in session.stems:
        if not isinstance(stem, AudioStem):
            raise LogicHandoffError("Every stem must be an AudioStem.")
        _label(stem.name, "Stem name")
        _integer(stem.start_frame, "Stem start frame", 0, session.frames - 1)
        if not isinstance(stem.path, (str, Path)):
            raise LogicHandoffError("Every stem needs a local file path.")
    if not isinstance(session.markers, tuple) or len(session.markers) > 10000:
        raise LogicHandoffError("Supply a tuple of at most 10000 markers.")
    for marker in session.markers:
        if not isinstance(marker, Marker):
            raise LogicHandoffError("Every marker must be a Marker.")
        _label(marker.name, "Marker name")
        _integer(marker.frame, "Marker frame", 0, session.frames)
    if not isinstance(session.midi_tracks, tuple) or len(session.midi_tracks) > 256:
        raise LogicHandoffError("Supply a tuple of at most 256 MIDI tracks.")
    for track in session.midi_tracks:
        if not isinstance(track, MidiTrack):
            raise LogicHandoffError("Every MIDI track must be a MidiTrack.")
        _label(track.name, "MIDI track name")
        if not isinstance(track.notes, tuple) or len(track.notes) > 100000:
            raise LogicHandoffError("Supply a tuple of at most 100000 notes per track.")
        endings: dict[tuple[int, int], int] = {}
        for note in track.notes:
            if not isinstance(note, MidiNote):
                raise LogicHandoffError("Every MIDI note must be a MidiNote.")
            _integer(note.start_frame, "Note start frame", 0, session.frames - 1)
            _integer(note.duration_frames, "Note duration", 1, session.frames)
            _integer(note.note, "MIDI pitch", 0, 127)
            _integer(note.velocity, "MIDI velocity", 1, 127)
            _integer(note.channel, "MIDI channel", 0, 15)
            if note.start_frame + note.duration_frames > session.frames:
                raise LogicHandoffError("MIDI notes must end within the session.")
        for note in sorted(track.notes, key=lambda item: item.start_frame):
            start = _tick(note.start_frame, session.sample_rate, tempo_us)
            end = _tick(note.start_frame + note.duration_frames, session.sample_rate, tempo_us)
            if end <= start:
                raise LogicHandoffError("A MIDI note is shorter than the representable tick duration.")
            key = (note.channel, note.note)
            if start < endings.get(key, 0):
                raise LogicHandoffError("Overlapping notes of the same pitch/channel are ambiguous.")
            endings[key] = end
    if _tick(session.frames, session.sample_rate, tempo_us) < 1:
        raise LogicHandoffError("Session duration is shorter than one MIDI tick.")
    return tempo_us


def _vlq(value: int) -> bytes:
    if not 0 <= value <= _MAX_VLQ:
        raise LogicHandoffError("MIDI variable-length value is out of range.")
    encoded = [value & 127]
    while value > 127:
        value >>= 7
        encoded.insert(0, 128 | (value & 127))
    return bytes(encoded)


def _meta(kind: int, data: bytes) -> bytes:
    return bytes((255, kind)) + _vlq(len(data)) + data


def _midi_track(events: list[tuple[int, int, bytes]], end_tick: int) -> bytes:
    events.append((end_tick, 99, b"\xff\x2f\x00"))
    result = bytearray()
    previous = 0
    for tick, _priority, event in sorted(events, key=lambda item: item[:2]):
        delta = tick - previous
        # A long rest may exceed one SMF delta; harmless empty text events keep
        # each delta legal without changing musical timing.
        while delta > _MAX_VLQ:
            result.extend(_vlq(_MAX_VLQ) + b"\xff\x01\x00")
            delta -= _MAX_VLQ
        result.extend(_vlq(delta) + event)
        previous = tick
    return b"MTrk" + struct.pack(">I", len(result)) + result


def _midi_bytes(session: HandoffSession, tempo_us: int) -> bytes:
    end = _tick(session.frames, session.sample_rate, tempo_us)
    conductor = [
        (0, 0, _meta(3, b"Tempo and markers")),
        (0, 0, _meta(81, tempo_us.to_bytes(3, "big"))),
        (0, 0, _meta(88, bytes((session.numerator, session.denominator.bit_length() - 1, 24, 8)))),
    ]
    conductor.extend(
        (_tick(marker.frame, session.sample_rate, tempo_us), 0, _meta(6, marker.name.encode("utf-8")))
        for marker in session.markers
    )
    tracks = [_midi_track(conductor, end)]
    for track in session.midi_tracks or (MidiTrack("No MIDI performance supplied", ()),):
        events = [(0, 0, _meta(3, track.name.encode("utf-8")))]
        for note in track.notes:
            events.append((
                _tick(note.start_frame, session.sample_rate, tempo_us), 2,
                bytes((0x90 | note.channel, note.note, note.velocity)),
            ))
            events.append((
                _tick(note.start_frame + note.duration_frames, session.sample_rate, tempo_us), 1,
                bytes((0x80 | note.channel, note.note, 0)),
            ))
        tracks.append(_midi_track(events, end))
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), MIDI_PPQ) + b"".join(tracks)


def _source_stamp(handle) -> tuple[int, int, int, int, int]:
    info = os.fstat(handle.fileno())
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _write_stem(reader, destination: Path, *, start_frame: int, frames: int, sample_rate: int) -> None:
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(
        str(destination), "w", samplerate=sample_rate, channels=reader.channels,
        format="WAV", subtype="PCM_24",
    ) as writer:
        silence = np.zeros((_CHUNK_FRAMES, reader.channels), dtype="float64")
        padding = start_frame
        while padding:
            count = min(padding, _CHUNK_FRAMES)
            writer.write(silence[:count])
            padding -= count
        remaining = len(reader)
        while remaining:
            count = min(remaining, _CHUNK_FRAMES)
            block = reader.read(count, dtype="float64", always_2d=True)
            if len(block) != count:
                raise LogicHandoffError("Source audio changed or ended unexpectedly during export.")
            if not np.isfinite(block).all() or (np.abs(block) > 1).any():
                raise LogicHandoffError("Source audio contains nonfinite or out-of-range samples.")
            writer.write(block)
            remaining -= count
        padding = frames - start_frame - len(reader)
        while padding:
            count = min(padding, _CHUNK_FRAMES)
            writer.write(silence[:count])
            padding -= count
    os.chmod(destination, 0o600)


def _readme(session: HandoffSession, metadata: dict) -> str:
    notes = metadata["midi_note_count"]
    marker_lines = "\n".join(
        f"- {item['name']}: frame {item['frame']}, {item['seconds']:.6f} seconds"
        for item in metadata["markers"]
    ) or "- No markers supplied."
    stems = "\n".join(f"- {item['file']} ({item['channels']} channel(s))" for item in metadata["stems"])
    return (
        f"# Logic handoff — {session.name}\n\n"
        "This folder is a WebJam handoff pack for Logic Pro and other DAWs.\n\n"
        "It includes:\n"
        "- `session.mid` (SMF Type 1 tempo, meter, markers, and any supplied MIDI notes)\n"
        "- Numbered 24-bit WAV stems aligned to one common timeline\n"
        "- `tempo.json` with exact timing metadata\n"
        "- `logic-import-map.csv` with per-stem timing and source details\n\n"
        f"Sample rate: **{session.sample_rate} Hz**\n"
        f"Length: **{session.frames} frames / {metadata['duration_seconds']:.9f} seconds**\n"
        f"Tempo: **{session.bpm:g} BPM requested**, {metadata['effective_bpm']:.9f} BPM in MIDI "
        f"({metadata['tempo_microseconds_per_quarter']} microseconds/quarter)\n"
        f"Time signature: **{session.numerator}/{session.denominator}**\n\n"
        "## Recommended Logic Pro import flow\n\n"
        "1. Open `session.mid` in Logic as a **new project** so Logic reads tempo, meter, and markers.\n"
        f"2. Set Logic's project sample rate to **{session.sample_rate} Hz** before adding audio.\n"
        "3. Select all WAV files in this folder and drag them together to bar 1 / project time zero.\n"
        "4. Keep original stem timing; disable automatic tempo matching/Flex stretching.\n"
        "5. Compare timeline endpoints and markers against `tempo.json`, then save your `.logicx` project.\n\n"
        "## Notes\n\n"
        f"SMF Type 1, {MIDI_PPQ} ticks/quarter; {notes} supplied MIDI note(s). "
        + (
            "No MIDI performance was supplied: the MIDI file contains tempo/markers and an empty performance track. "
            if notes == 0
            else ""
        )
        + "No audio-to-MIDI transcription or live MIDI capture is performed.\n\n"
        "Every WAV includes leading/trailing silence as needed and has exactly the declared frame length. "
        "Every MIDI track ends at the same tick. MIDI uses the nearest tick to each frame timestamp, "
        f"with at most {metadata['midi_max_quantization_error_seconds']:.9f} seconds of rounding per event. "
        "Tempo is constant in Phase 1. No sample-rate conversion, normalization, effects, BWF, or iXML is applied. "
        "Original source files are unchanged.\n\n"
        f"## Stems\n\n{stems}\n\n## Markers\n\n{marker_lines}\n"
    )


def _import_map_csv(session: HandoffSession, metadata: dict) -> str:
    """Build a Logic-friendly stem timing map for quick import checks."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        (
            "file",
            "stem_name",
            "start_frame",
            "start_seconds",
            "source_frames",
            "source_seconds",
            "channels",
        )
    )
    for stem in metadata["stems"]:
        start_frame = int(stem["start_frame"])
        source_frames = int(stem["source_frames"])
        writer.writerow(
            (
                stem["file"],
                stem["name"],
                start_frame,
                f"{start_frame / session.sample_rate:.9f}",
                source_frames,
                f"{source_frames / session.sample_rate:.9f}",
                int(stem["channels"]),
            )
        )
    return buffer.getvalue()


def export_logic_handoff(
    session: HandoffSession, *, destination_root: Path | None = None,
) -> HandoffResult:
    """Validate stopped sources, then atomically publish one complete handoff.

    Failure removes this invocation's staging directory. Existing exports and
    source files are never overwritten. Runtime dependencies are the existing
    NumPy/SoundFile packages; all work is local and streaming.
    """
    import soundfile as sf

    tempo_us = _validate(session)
    midi = _midi_bytes(session, tempo_us)
    root = Path(destination_root).expanduser() if destination_root is not None else (
        Path.home() / "Music" / "WebJam" / "LogicHandoff"
    )
    staging = None
    try:
        with ExitStack() as stack:
            sources = []
            stem_metadata = []
            for index, stem in enumerate(session.stems, 1):
                source = Path(stem.path).expanduser()
                if not source.is_file():
                    raise LogicHandoffError("Every stem must be an existing regular audio file.")
                handle = stack.enter_context(source.open("rb"))
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise LogicHandoffError("Every stem must be a regular audio file.")
                stamp = _source_stamp(handle)
                reader = stack.enter_context(sf.SoundFile(handle))
                if reader.samplerate != session.sample_rate:
                    raise LogicHandoffError(
                        f"Mixed sample rates or project SR mismatch: {stem.name} is {reader.samplerate} Hz; "
                        f"the project requires {session.sample_rate} Hz. No resampling is performed."
                    )
                if reader.format not in ("WAV", "WAVEX", "AIFF") or reader.channels not in (1, 2):
                    raise LogicHandoffError("Stems must be mono or stereo WAV/AIFF files.")
                if len(reader) <= 0 or stem.start_frame + len(reader) > session.frames:
                    raise LogicHandoffError("Every nonempty source must fit within the declared session frames.")
                if session.frames * reader.channels * 3 > 0xFFFFFFFF - 128:
                    raise LogicHandoffError("The rendered stem exceeds the standard WAV size limit.")
                sources.append((stem, handle, reader, stamp))
                stem_metadata.append({
                    "file": f"{index:02d}-{_safe_name(stem.name)}.wav", "name": stem.name,
                    "start_frame": stem.start_frame, "source_frames": len(reader), "channels": reader.channels,
                })
            tick_seconds = tempo_us / (1_000_000 * MIDI_PPQ)
            end_tick = _tick(session.frames, session.sample_rate, tempo_us)
            metadata = {
                "schema_version": 1, "session_name": session.name,
                "sample_rate": session.sample_rate, "bit_depth": 24, "audio_format": "WAV",
                "frames": session.frames, "duration_seconds": session.frames / session.sample_rate,
                "bpm": session.bpm, "effective_bpm": 60_000_000 / tempo_us,
                "tempo_microseconds_per_quarter": tempo_us,
                "time_signature": [session.numerator, session.denominator],
                "midi_ppq": MIDI_PPQ, "midi_end_tick": end_tick,
                "midi_duration_seconds": end_tick * tick_seconds,
                "midi_tick_seconds": tick_seconds,
                "midi_max_quantization_error_seconds": tick_seconds / 2,
                "midi_note_count": sum(len(track.notes) for track in session.midi_tracks),
                "stems": stem_metadata,
                "markers": [
                    {"name": marker.name, "frame": marker.frame,
                     "seconds": marker.frame / session.sample_rate,
                     "midi_tick": _tick(marker.frame, session.sample_rate, tempo_us)}
                    for marker in sorted(session.markers, key=lambda item: item.frame)
                ],
            }
            root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".logic-handoff-", dir=root))
            # Retain the exclusive staging suffix in the timestamped final name
            # so parallel exports of the same session do not share a destination.
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            final = root / f"{_safe_name(session.name)}-{timestamp}-{staging.name.removeprefix('.logic-handoff-')}"
            for (stem, _handle, reader, _stamp), item in zip(sources, stem_metadata, strict=True):
                _write_stem(reader, staging / item["file"], start_frame=stem.start_frame,
                            frames=session.frames, sample_rate=session.sample_rate)
            for name, data in (
                ("session.mid", midi),
                ("tempo.json", (json.dumps(metadata, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")),
                ("README.md", _readme(session, metadata).encode("utf-8")),
                ("logic-import-map.csv", _import_map_csv(session, metadata).encode("utf-8")),
            ):
                target = staging / name
                with target.open("xb") as handle:
                    handle.write(data)
                os.chmod(target, 0o600)
            for _stem, handle, _reader, stamp in sources:
                if _source_stamp(handle) != stamp:
                    raise LogicHandoffError("Source audio changed during export; stop recording and retry.")
            if final.exists() or final.is_symlink():
                raise LogicHandoffError("The destination already exists; retry to create a new export.")
            staging.rename(final)
            staging = None
        return HandoffResult(
            folder=final, stems=tuple(final / item["file"] for item in stem_metadata),
            midi=final / "session.mid", tempo=final / "tempo.json", readme=final / "README.md",
            import_map=final / "logic-import-map.csv",
            sample_rate=session.sample_rate, frames=session.frames,
        )
    except (OSError, RuntimeError) as exc:
        raise LogicHandoffError(f"Logic handoff could not be written: {exc}") from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging)
