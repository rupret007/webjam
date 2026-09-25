"""Verify Logic handoff media with independent audio and SMF readers."""

from __future__ import annotations

import csv
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import core.logic_handoff as handoff
from core.logic_handoff import (
    AudioStem,
    HandoffSession,
    LogicHandoffError,
    Marker,
    MidiNote,
    MidiTrack,
    export_logic_handoff,
)


PPQ = 32760


@dataclass(frozen=True)
class _Event:
    tick: int
    status: int
    data: bytes
    meta: int | None = None


def _read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, offset
    raise AssertionError("SMF variable-length value exceeds four bytes")


def _read_midi(path: Path) -> tuple[int, int, list[list[_Event]]]:
    """Parse binary chunks/events without relying on exporter internals."""
    raw = path.read_bytes()
    assert raw[:4] == b"MThd"
    header_length = struct.unpack_from(">I", raw, 4)[0]
    assert header_length == 6
    format_type, track_count, division = struct.unpack_from(">HHH", raw, 8)
    assert not division & 0x8000, "Expected PPQ timing, not SMPTE"
    offset = 8 + header_length
    tracks = []
    for _ in range(track_count):
        assert raw[offset : offset + 4] == b"MTrk"
        chunk_length = struct.unpack_from(">I", raw, offset + 4)[0]
        offset += 8
        chunk = raw[offset : offset + chunk_length]
        assert len(chunk) == chunk_length
        offset += chunk_length
        events = []
        cursor = tick = 0
        running_status = None
        while cursor < len(chunk):
            delta, cursor = _read_vlq(chunk, cursor)
            tick += delta
            if chunk[cursor] & 0x80:
                status = chunk[cursor]
                cursor += 1
            else:
                assert running_status is not None
                status = running_status
            if status == 0xFF:
                meta = chunk[cursor]
                length, cursor = _read_vlq(chunk, cursor + 1)
                payload = chunk[cursor : cursor + length]
                assert len(payload) == length
                cursor += length
                events.append(_Event(tick, status, payload, meta))
                running_status = None
            elif status in (0xF0, 0xF7):
                length, cursor = _read_vlq(chunk, cursor)
                payload = chunk[cursor : cursor + length]
                assert len(payload) == length
                cursor += length
                events.append(_Event(tick, status, payload))
                running_status = None
            else:
                assert 0x80 <= status <= 0xEF
                length = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
                payload = chunk[cursor : cursor + length]
                assert len(payload) == length
                assert all(byte < 0x80 for byte in payload)
                cursor += length
                events.append(_Event(tick, status, payload))
                running_status = status
        assert events[-1].meta == 0x2F
        assert events[-1].data == b""
        assert sum(event.meta == 0x2F for event in events) == 1
        tracks.append(events)
    assert offset == len(raw), "SMF contains trailing or unparsed chunks"
    return format_type, division, tracks


def _source(
    tmp_path: Path,
    name: str = "source.wav",
    *,
    sample_rate: int = 48000,
    frames: int = 32,
    channels: int = 1,
) -> Path:
    path = tmp_path / name
    sf.write(path, np.full((frames, channels), 0.25), sample_rate, subtype="PCM_24")
    return path


def _session(source: Path, **overrides) -> HandoffSession:
    values = dict(
        name="Evening jam",
        sample_rate=48000,
        frames=96000,
        bpm=120.0,
        stems=(AudioStem("Guitar", source),),
    )
    values.update(overrides)
    return HandoffSession(**values)


def _assert_unpublished(destination: Path) -> None:
    assert not destination.exists() or list(destination.iterdir()) == []


def test_stems_are_pcm24_with_exact_common_length_and_offsets(tmp_path):
    mono = np.array([0.125, -0.5, 0.25, 0.0, -0.125], dtype=np.float64)
    stereo = np.array([[0.25, -0.5], [0.0, 0.125], [-0.25, 0.375]])
    mono_source = tmp_path / "mono.wav"
    stereo_source = tmp_path / "stereo.aiff"
    sf.write(mono_source, mono, 48000, subtype="PCM_24")
    sf.write(stereo_source, stereo, 48000, subtype="PCM_16")
    original = {
        path: (hashlib.sha256(path.read_bytes()).digest(), path.stat().st_mtime_ns)
        for path in (mono_source, stereo_source)
    }
    session = _session(
        mono_source,
        frames=40,
        stems=(
            AudioStem("Lead", mono_source, 7),
            AudioStem("Room", stereo_source, 2),
        ),
    )

    result = export_logic_handoff(session, destination_root=tmp_path / "exports")

    assert result.sample_rate == 48000
    assert result.frames == 40
    assert len(result.stems) == 2
    assert result.folder.is_dir()
    for output, samples, start in zip(result.stems, (mono, stereo), (7, 2), strict=True):
        assert output.parent == result.folder
        info = sf.info(output)
        assert (info.format, info.subtype, info.samplerate, info.frames) == (
            "WAV", "PCM_24", 48000, 40
        )
        channels = 1 if samples.ndim == 1 else 2
        assert info.channels == channels
        actual, rate = sf.read(output, dtype="float64", always_2d=True)
        expected = np.zeros((40, channels))
        expected[start : start + len(samples)] = samples.reshape(-1, channels)
        np.testing.assert_array_equal(actual, expected)
        assert rate == 48000
    for source, before in original.items():
        assert (hashlib.sha256(source.read_bytes()).digest(), source.stat().st_mtime_ns) == before
    assert result.midi.name == "session.mid"
    assert result.tempo.name == "tempo.json"
    assert result.readme.name == "README.md"
    assert result.import_map.name == "logic-import-map.csv"
    assert all(path.is_file() for path in (result.midi, result.tempo, result.readme, result.import_map))


def test_type1_midi_has_real_notes_markers_meter_and_common_end(tmp_path):
    source = _source(tmp_path)
    notes = (
        MidiNote(113, 5023, 60, 91, 2),
        MidiNote(88200, 4800, 67, 72, 2),
    )
    session = _session(
        source,
        bpm=123.456,
        frames=240017,
        midi_tracks=(MidiTrack("Piano", notes), MidiTrack("Bass", (MidiNote(48000, 24000, 36),))),
        markers=(Marker("Intro", 0), Marker("Chorus é", 96000)),
        numerator=7,
        denominator=8,
    )
    result = export_logic_handoff(session, destination_root=tmp_path / "exports")

    kind, division, tracks = _read_midi(result.midi)
    tempo_us = round(60_000_000 / session.bpm)
    def tick(frame):
        return round(frame * 1_000_000 * PPQ / (48000 * tempo_us))

    assert (kind, division, len(tracks)) == (1, PPQ, 3)
    assert [track[-1].tick for track in tracks] == [tick(session.frames)] * 3
    tempo_events = [event for event in tracks[0] if event.meta == 0x51]
    assert tempo_events == [_Event(0, 0xFF, tempo_us.to_bytes(3, "big"), 0x51)]
    meters = [event for event in tracks[0] if event.meta == 0x58]
    assert len(meters) == 1
    assert meters[0].tick == 0
    assert meters[0].data == bytes((7, 3, 24, 8))
    markers = [event for event in tracks[0] if event.meta == 0x06]
    assert [(event.tick, event.data.decode("utf-8")) for event in markers] == [
        (0, "Intro"), (tick(96000), "Chorus é")
    ]
    assert not any(0x80 <= event.status <= 0xEF for event in tracks[0])
    piano = tracks[1]
    assert [event.data.decode("utf-8") for event in piano if event.meta == 0x03] == ["Piano"]
    note_events = [event for event in piano if event.status & 0xF0 in (0x80, 0x90)]
    assert [(event.tick, event.status, event.data) for event in note_events] == [
        (tick(113), 0x92, bytes((60, 91))),
        (tick(5136), 0x82, bytes((60, 0))),
        (tick(88200), 0x92, bytes((67, 72))),
        (tick(93000), 0x82, bytes((67, 0))),
    ]
    tick_seconds = tempo_us / 1_000_000 / PPQ
    for frame in (113, 5136, 88200, 93000, session.frames):
        assert abs(tick(frame) * tick_seconds - frame / 48000) <= tick_seconds / 2 + 1e-12


def test_tempo_metadata_agrees_with_actual_audio_and_midi(tmp_path):
    source = _source(tmp_path, frames=41)
    session = _session(
        source,
        bpm=113.123456,
        frames=123457,
        stems=(AudioStem("Guitar", source, 117),),
        midi_tracks=(MidiTrack("Keys", (MidiNote(1000, 8000, 64),)),),
        markers=(Marker("Bridge", 87654),),
        numerator=3,
        denominator=4,
    )
    result = export_logic_handoff(session, destination_root=tmp_path / "exports")
    metadata = json.loads(result.tempo.read_text(encoding="utf-8"))
    _, division, tracks = _read_midi(result.midi)
    tempo = next(event for event in tracks[0] if event.meta == 0x51)
    tempo_us = int.from_bytes(tempo.data, "big")
    tick_seconds = tempo_us / 1_000_000 / division

    assert metadata["schema_version"] == 1
    assert metadata["session_name"] == session.name
    assert metadata["sample_rate"] == 48000
    assert metadata["bit_depth"] == 24
    assert metadata["audio_format"] == "WAV"
    assert metadata["frames"] == sf.info(result.stems[0]).frames == session.frames
    assert metadata["duration_seconds"] == pytest.approx(session.frames / 48000)
    assert metadata["bpm"] == session.bpm
    assert metadata["effective_bpm"] == pytest.approx(60_000_000 / tempo_us)
    assert metadata["tempo_microseconds_per_quarter"] == tempo_us
    assert metadata["time_signature"] == [3, 4]
    assert metadata["midi_ppq"] == division == PPQ
    assert metadata["midi_end_tick"] == tracks[0][-1].tick
    assert metadata["midi_duration_seconds"] == pytest.approx(tracks[0][-1].tick * tick_seconds)
    assert metadata["midi_tick_seconds"] == pytest.approx(tick_seconds)
    assert metadata["midi_max_quantization_error_seconds"] == pytest.approx(tick_seconds / 2)
    assert metadata["midi_note_count"] == 1
    assert metadata["stems"] == [{
        "file": result.stems[0].name,
        "name": "Guitar",
        "start_frame": 117,
        "source_frames": 41,
        "channels": 1,
    }]
    marker_tick = next(event.tick for event in tracks[0] if event.meta == 0x06)
    assert metadata["markers"] == [{
        "name": "Bridge",
        "frame": 87654,
        "seconds": 87654 / 48000,
        "midi_tick": marker_tick,
    }]


def test_import_map_csv_matches_exported_stems(tmp_path):
    source = _source(tmp_path, frames=25, channels=2)
    session = _session(
        source,
        frames=200,
        stems=(AudioStem("Lead Vocal", source, 40), AudioStem("Double, Left", source, 80)),
    )
    result = export_logic_handoff(session, destination_root=tmp_path / "exports")

    with result.import_map.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["file"] for row in rows] == [path.name for path in result.stems]
    assert [row["stem_name"] for row in rows] == ["Lead Vocal", "Double, Left"]
    assert [int(row["start_frame"]) for row in rows] == [40, 80]
    assert [int(row["source_frames"]) for row in rows] == [25, 25]
    assert [int(row["channels"]) for row in rows] == [2, 2]
    assert [float(row["start_seconds"]) for row in rows] == pytest.approx([40 / 48000, 80 / 48000])
    assert [float(row["source_seconds"]) for row in rows] == pytest.approx([25 / 48000, 25 / 48000])


def test_corrupt_import_map_is_rejected_before_publication(tmp_path, monkeypatch):
    source = _source(tmp_path)
    destination = tmp_path / "exports"

    def corrupt_map(*_args, **_kwargs):
        return "bad,header\nnot,enough\n"

    monkeypatch.setattr(handoff, "_import_map_csv", corrupt_map)
    with pytest.raises(LogicHandoffError, match="logic-import-map.csv"):
        export_logic_handoff(_session(source), destination_root=destination)
    _assert_unpublished(destination)


def test_import_map_write_failure_reports_specific_file(tmp_path, monkeypatch):
    source = _source(tmp_path)
    destination = tmp_path / "exports"
    real_open = Path.open

    def fail_import_map_open(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode")
        if path.name == "logic-import-map.csv" and mode == "xb":
            raise OSError("simulated import-map write failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_import_map_open)
    with pytest.raises(LogicHandoffError, match="logic-import-map.csv"):
        export_logic_handoff(_session(source), destination_root=destination)
    _assert_unpublished(destination)


def test_audio_only_session_has_honest_empty_performance_track(tmp_path):
    result = export_logic_handoff(_session(_source(tmp_path)), destination_root=tmp_path / "exports")
    kind, _, tracks = _read_midi(result.midi)
    assert kind == 1
    assert len(tracks) >= 2
    assert not any(0x80 <= event.status <= 0xEF for track in tracks for event in track)
    names = [event.data.decode("utf-8").lower() for event in tracks[1] if event.meta == 0x03]
    assert names and any("empty" in name or "no midi" in name for name in names)
    metadata = json.loads(result.tempo.read_text(encoding="utf-8"))
    assert metadata["midi_note_count"] == 0
    assert len({track[-1].tick for track in tracks}) == 1


def test_adjacent_same_pitch_notes_release_before_retrigger(tmp_path):
    session = _session(
        _source(tmp_path),
        midi_tracks=(MidiTrack("Keys", (MidiNote(0, 48000, 60), MidiNote(48000, 48000, 60))),),
    )
    result = export_logic_handoff(session, destination_root=tmp_path / "exports")
    _, _, tracks = _read_midi(result.midi)
    boundary = [event.status & 0xF0 for event in tracks[1] if event.tick == PPQ * 2 and event.status != 0xFF]
    assert boundary == [0x80, 0x90]


@pytest.mark.parametrize("overrides", [
    {"sample_rate": 0}, {"sample_rate": True}, {"sample_rate": 48000.5},
    {"sample_rate": 8000}, {"sample_rate": 22050},
    {"sample_rate": 48001}, {"sample_rate": 384000},
    {"frames": 0}, {"frames": -1}, {"frames": True}, {"frames": 96000.5},
    {"bpm": 0}, {"bpm": -1}, {"bpm": float("nan")}, {"bpm": float("inf")},
    {"bpm": 1}, {"bpm": 120_000_000}, {"bpm": 4}, {"bpm": 991},
    pytest.param({"bpm": 10**400}, id="bpm-oversized-integer"),
    {"numerator": 0}, {"denominator": 3},
    {"stems": ()},
])
def test_invalid_session_is_rejected_without_publication(tmp_path, overrides):
    source = _source(tmp_path)
    destination = tmp_path / "exports"
    with pytest.raises(LogicHandoffError):
        export_logic_handoff(_session(source, **overrides), destination_root=destination)
    _assert_unpublished(destination)


@pytest.mark.parametrize("note_values", [
    {"start_frame": -1}, {"start_frame": 95999},
    {"duration_frames": 0}, {"duration_frames": -1}, {"duration_frames": 96001},
    {"note": -1}, {"note": 128}, {"note": True},
    {"velocity": 0}, {"velocity": 128},
    {"channel": -1}, {"channel": 16},
])
def test_invalid_note_is_rejected_without_publication(tmp_path, note_values):
    source = _source(tmp_path)
    destination = tmp_path / "exports"
    values = dict(start_frame=0, duration_frames=100, note=60)
    values.update(note_values)
    with pytest.raises(LogicHandoffError):
        session = _session(source, midi_tracks=(MidiTrack("Keys", (MidiNote(**values),)),))
        export_logic_handoff(session, destination_root=destination)
    _assert_unpublished(destination)


def test_sub_tick_note_is_rejected_instead_of_silently_disappearing(tmp_path):
    source = _source(tmp_path, sample_rate=192000)
    destination = tmp_path / "exports"
    with pytest.raises(LogicHandoffError):
        session = _session(
            source,
            sample_rate=192000,
            midi_tracks=(MidiTrack("Keys", (MidiNote(0, 1, 60),)),),
        )
        export_logic_handoff(session, destination_root=destination)
    _assert_unpublished(destination)


@pytest.mark.parametrize("start,frames", [(-1, 100), (80, 100), (0, 31)])
def test_source_must_fit_declared_timeline_without_trimming(tmp_path, start, frames):
    source = _source(tmp_path, frames=32)
    before = source.read_bytes()
    destination = tmp_path / "exports"
    with pytest.raises(LogicHandoffError):
        session = _session(source, frames=frames, stems=(AudioStem("Lead", source, start),))
        export_logic_handoff(session, destination_root=destination)
    _assert_unpublished(destination)
    assert source.read_bytes() == before


def test_sample_rate_mismatch_is_rejected_before_audio_output(tmp_path, monkeypatch):
    source = _source(tmp_path)
    other = _source(tmp_path, "other.wav", sample_rate=44100)
    destination = tmp_path / "exports"

    def unexpected_write(*args, **kwargs):
        pytest.fail("Mismatched source must fail preflight before any stem is written")

    monkeypatch.setattr(handoff, "_write_stem", unexpected_write)
    with pytest.raises(LogicHandoffError):
        session = _session(source, stems=(AudioStem("Lead", source), AudioStem("Other", other)))
        export_logic_handoff(session, destination_root=destination)
    _assert_unpublished(destination)


@pytest.mark.parametrize("kind", ["missing", "not_audio", "flac", "surround", "empty"])
def test_unusable_source_is_rejected_without_publication(tmp_path, kind):
    if kind == "missing":
        source = tmp_path / "missing.wav"
    elif kind == "not_audio":
        source = tmp_path / "fake.wav"
        source.write_text("This is not audio.", encoding="utf-8")
    elif kind == "flac":
        source = _source(tmp_path, "unsupported.flac")
    else:
        source = _source(tmp_path, channels=3 if kind == "surround" else 1, frames=0 if kind == "empty" else 32)
    destination = tmp_path / "exports"
    with pytest.raises(LogicHandoffError):
        export_logic_handoff(_session(source), destination_root=destination)
    _assert_unpublished(destination)


@pytest.mark.parametrize("frame", [-1, 96001, 0.5, True])
def test_invalid_marker_position_is_rejected(tmp_path, frame):
    source = _source(tmp_path)
    destination = tmp_path / "exports"
    with pytest.raises(LogicHandoffError):
        export_logic_handoff(
            _session(source, markers=(Marker("Verse", frame),)), destination_root=destination
        )
    _assert_unpublished(destination)


def test_failed_write_removes_partial_package_and_preserves_sources(tmp_path, monkeypatch):
    source = _source(tmp_path)
    before = source.read_bytes()
    destination = tmp_path / "exports"
    real_write = handoff._write_stem
    writes = []

    def fail_after_write(*args, **kwargs):
        real_write(*args, **kwargs)
        writes.append(args[1])
        raise OSError("simulated disk full after writing audio")

    monkeypatch.setattr(handoff, "_write_stem", fail_after_write)
    with pytest.raises(LogicHandoffError):
        export_logic_handoff(_session(source), destination_root=destination)
    assert writes, "The failure must occur after an actual partial stem is written"
    _assert_unpublished(destination)
    assert source.read_bytes() == before


def test_repeated_export_keeps_previous_package_intact(tmp_path):
    source = _source(tmp_path)
    session = _session(source)
    destination = tmp_path / "exports"
    first = export_logic_handoff(session, destination_root=destination)
    previous = {path.name: path.read_bytes() for path in first.folder.iterdir()}
    second = export_logic_handoff(session, destination_root=destination)
    assert first.folder != second.folder
    assert {path.name: path.read_bytes() for path in first.folder.iterdir()} == previous
    assert second.midi.is_file()


def test_unsafe_and_duplicate_names_stay_inside_package(tmp_path):
    source = _source(tmp_path)
    session = _session(
        source,
        name="../../Evening / jam",
        stems=(AudioStem("../Lead", source), AudioStem("../Lead", source)),
    )
    destination = tmp_path / "exports"
    result = export_logic_handoff(session, destination_root=destination)
    assert result.folder.parent.resolve() == destination.resolve()
    assert len(set(result.stems)) == 2
    assert all(path.parent == result.folder for path in result.stems)
    assert all(path.suffix == ".wav" for path in result.stems)


def test_long_stem_keeps_content_and_padding_across_audio_blocks(tmp_path):
    source = tmp_path / "long.wav"
    samples = ((np.arange(140013, dtype=np.int32) % 65536) - 32768) / 65536
    sf.write(source, samples, 48000, subtype="PCM_24")
    start = 70007
    frames = start + len(samples) + 80011
    session = _session(source, frames=frames, stems=(AudioStem("Long", source, start),))
    result = export_logic_handoff(session, destination_root=tmp_path / "exports")
    actual, _ = sf.read(result.stems[0], dtype="float64")
    expected = np.zeros(frames)
    expected[start : start + len(samples)] = samples
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("sample", [float("nan"), float("inf"), 1.25])
def test_invalid_float_audio_is_rejected_without_clipping_or_publication(tmp_path, sample):
    source = tmp_path / "float.wav"
    sf.write(source, np.array([0.25, sample, -0.5]), 48000, subtype="FLOAT")
    before = source.read_bytes()
    destination = tmp_path / "exports"
    with pytest.raises(LogicHandoffError):
        export_logic_handoff(_session(source), destination_root=destination)
    _assert_unpublished(destination)
    assert source.read_bytes() == before


def test_metadata_failure_removes_completed_stems_and_partial_package(tmp_path, monkeypatch):
    source = _source(tmp_path)
    destination = tmp_path / "exports"
    real_open = Path.open
    attempted = []

    def fail_metadata_open(path, *args, **kwargs):
        if path.name == "tempo.json":
            attempted.append(path)
            assert list(path.parent.glob("*.wav")), "Audio must exist before this failure"
            raise OSError("simulated metadata write failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_metadata_open)
    with pytest.raises(LogicHandoffError):
        export_logic_handoff(_session(source), destination_root=destination)
    assert attempted
    _assert_unpublished(destination)


def test_source_changed_during_export_prevents_publication(tmp_path, monkeypatch):
    source = _source(tmp_path)
    destination = tmp_path / "exports"
    real_write = handoff._write_stem
    writes = []

    def modify_after_write(*args, **kwargs):
        real_write(*args, **kwargs)
        writes.append(args[1])
        # Mimic a recorder appending after this exporter has consumed its data.
        with source.open("ab") as stream:
            stream.write(b"new recorder data")

    monkeypatch.setattr(handoff, "_write_stem", modify_after_write)
    with pytest.raises(LogicHandoffError, match="changed"):
        export_logic_handoff(_session(source), destination_root=destination)
    assert writes
    _assert_unpublished(destination)


def test_midi_long_rests_use_legal_deltas_without_changing_absolute_time(tmp_path):
    # Exercise a two-hour timeline through the serializer, avoiding gigabytes
    # of silent audio; the parser above independently enforces the VLQ limit.
    rate = 48000
    start = rate * 3600
    session = _session(
        tmp_path / "unused-source.wav",
        sample_rate=rate,
        frames=rate * 7200,
        bpm=990,
        midi_tracks=(MidiTrack("Late entry", (MidiNote(start, rate, 60),)),),
        markers=(Marker("Late marker", start),),
    )
    midi = tmp_path / "long-rest.mid"
    tempo_us = 60606
    midi.write_bytes(handoff._midi_bytes(session, tempo_us))
    kind, division, tracks = _read_midi(midi)
    assert (kind, division) == (1, PPQ)
    def expected_tick(seconds):
        return round(seconds * PPQ * 1_000_000 / tempo_us)

    expected_start = expected_tick(3600)
    assert all(track[-1].tick == expected_tick(7200) for track in tracks)
    assert next(event.tick for event in tracks[0] if event.meta == 0x06) == expected_start
    notes = [event for event in tracks[1] if event.status & 0xF0 in (0x80, 0x90)]
    assert [(event.tick, event.status) for event in notes] == [
        (expected_start, 0x90), (expected_tick(3601), 0x80)
    ]
    assert all(any(event.meta == 0x01 and event.data == b"" for event in track) for track in tracks)


@pytest.mark.parametrize("sample_rate", [44100, 48000, 88200, 96000, 176400, 192000])
@pytest.mark.parametrize("bpm", [5, 990])
def test_supported_project_rates_and_tempo_boundaries_export(tmp_path, sample_rate, bpm):
    source = _source(tmp_path, sample_rate=sample_rate)
    session = _session(source, sample_rate=sample_rate, frames=64, bpm=bpm)
    result = export_logic_handoff(session, destination_root=tmp_path / "exports")
    info = sf.info(result.stems[0])
    assert (info.samplerate, info.frames, info.subtype) == (sample_rate, 64, "PCM_24")
    metadata = json.loads(result.tempo.read_text(encoding="utf-8"))
    assert metadata["bpm"] == bpm
    assert metadata["tempo_microseconds_per_quarter"] == round(60_000_000 / bpm)
    kind, division, tracks = _read_midi(result.midi)
    assert (kind, division) == (1, PPQ)
    assert all(track[-1].tick == metadata["midi_end_tick"] for track in tracks)
