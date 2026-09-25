"""Offline CLI and strict manifest coverage for the phase-one Logic handoff."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from core.logic_handoff import LogicHandoffError
from tools import logic_handoff


ROOT = Path(__file__).resolve().parents[1]


def _manifest(tmp_path: Path) -> tuple[Path, dict]:
    source = tmp_path / "audio" / "source.wav"
    source.parent.mkdir()
    sf.write(source, np.full((12, 1), 0.125), 48_000, subtype="PCM_24")
    data = {
        "name": "Stopped Song",
        "sample_rate": 48_000,
        "frames": 96,
        "bpm": 120,
        "stems": [{"name": "Guitar", "path": "audio/source.wav", "start_frame": 12}],
        "midi_tracks": [{"name": "Keys", "notes": [{"start_frame": 12, "duration_frames": 24, "note": 60}]}],
        "markers": [{"name": "Middle", "frame": 48}],
    }
    path = tmp_path / "session.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "-m", "tools.logic_handoff", *arguments],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )


def test_manifest_resolves_paths_at_manifest_and_preserves_explicit_data(tmp_path: Path, monkeypatch):
    path, _data = _manifest(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    session = logic_handoff.load_session(path)
    assert session.name == "Stopped Song"
    assert session.sample_rate == 48_000
    assert session.frames == 96
    assert session.bpm == 120
    assert (session.numerator, session.denominator) == (4, 4)
    assert session.stems[0].path == tmp_path / "audio" / "source.wav"
    assert session.stems[0].start_frame == 12
    note = session.midi_tracks[0].notes[0]
    assert (note.start_frame, note.duration_frames, note.note, note.velocity, note.channel) == (12, 24, 60, 100, 0)
    assert (session.markers[0].name, session.markers[0].frame) == ("Middle", 48)


def test_manifest_accepts_minimal_audio_only_and_explicit_note_defaults(tmp_path: Path):
    path, data = _manifest(tmp_path)
    data.pop("midi_tracks")
    data.pop("markers")
    data["stems"][0].pop("start_frame")
    data["numerator"] = 3
    data["denominator"] = 8
    data["stems"][0]["path"] = str(tmp_path / "audio" / "source.wav")
    path.write_text(json.dumps(data), encoding="utf-8")
    session = logic_handoff.load_session(path)
    assert session.stems[0].start_frame == 0
    assert session.midi_tracks == session.markers == ()
    assert (session.numerator, session.denominator) == (3, 8)


@pytest.mark.parametrize("target,field,value", [
    ("session", "tempo_map", []),
    ("session", "live_midi", True),
    ("session", "sample_rate", True),
    ("session", "frames", "96"),
    ("session", "frames", 96.0),
    ("session", "bpm", "120"),
    ("session", "bpm", True),
    ("session", "bpm", 10 ** 400),
    ("session", "stems", {}),
    ("session", "midi_tracks", {}),
    ("session", "markers", None),
    ("session", "name", ""),
    ("stem", "offset_seconds", 0),
    ("stem", "path", 1),
    ("stem", "path", "audio/invalid\0.wav"),
    ("stem", "start_frame", False),
    ("track", "program", 1),
    ("track", "notes", {}),
    ("note", "pitch_bend", 0),
    ("note", "note", "60"),
    ("note", "duration_frames", 1.5),
    ("note", "velocity", True),
    ("note", "channel", "0"),
    ("marker", "seconds", 2),
    ("marker", "frame", False),
])
def test_manifest_rejects_unsupported_fields_and_wrong_types(tmp_path: Path, target, field, value):
    path, data = _manifest(tmp_path)
    objects = {
        "session": data,
        "stem": data["stems"][0],
        "track": data["midi_tracks"][0],
        "note": data["midi_tracks"][0]["notes"][0],
        "marker": data["markers"][0],
    }
    objects[target][field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LogicHandoffError):
        logic_handoff.load_session(path)


@pytest.mark.parametrize("payload", [
    "[]", "null", "{", '{"name":"one","name":"two"}',
    '{"bpm":NaN}', '{"bpm":Infinity}', '{"bpm":-Infinity}',
    '{"bpm":' + "1" * 5000 + "}",
])
def test_manifest_rejects_malformed_or_ambiguous_json(tmp_path: Path, payload: str):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(LogicHandoffError):
        logic_handoff.load_session(path)


@pytest.mark.parametrize("target,field", [("session", "frames"), ("stem", "path"), ("note", "note"), ("marker", "frame")])
def test_manifest_rejects_missing_required_fields(tmp_path: Path, target, field):
    path, data = _manifest(tmp_path)
    objects = {
        "session": data,
        "stem": data["stems"][0],
        "note": data["midi_tracks"][0]["notes"][0],
        "marker": data["markers"][0],
    }
    del objects[target][field]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LogicHandoffError):
        logic_handoff.load_session(path)


@pytest.mark.parametrize("arguments", [[], ["--stub", "--session", "session.json"], ["--stu"]])
def test_cli_requires_exactly_one_explicit_input(arguments):
    result = _run(*arguments)
    assert result.returncode == 2
    assert result.stdout == ""


def test_cli_real_manifest_exports_stopped_audio_with_declared_alignment(tmp_path: Path):
    path, _data = _manifest(tmp_path)
    destination = tmp_path / "handoffs"
    result = _run("--session", str(path), "--destination-root", str(destination))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert Path(receipt["folder"]).parent == destination
    assert receipt["sample_rate"] == 48_000
    assert receipt["frames"] == 96
    assert len(receipt["stems"]) == 1
    output = Path(receipt["stems"][0])
    info = sf.info(output)
    assert (info.format, info.subtype, info.samplerate, info.frames, info.channels) == ("WAV", "PCM_24", 48_000, 96, 1)
    values, _sample_rate = sf.read(output)
    np.testing.assert_array_equal(values[:12], 0)
    np.testing.assert_allclose(values[12:24], 0.125, atol=1 / 8_388_608)
    np.testing.assert_array_equal(values[24:], 0)
    assert Path(receipt["midi"]).read_bytes().startswith(b"MThd")
    assert Path(receipt["tempo"]).is_file()
    assert Path(receipt["readme"]).is_file()
    assert Path(receipt["import_map"]).is_file()


def test_cli_stub_exports_real_stems_and_pitched_notes_then_removes_temporary_sources(tmp_path: Path, monkeypatch, capsys):
    captured = []
    writer = logic_handoff.export_logic_handoff

    def inspect_session(session, **kwargs):
        captured.append(session)
        assert all(stem.path.is_file() for stem in session.stems)
        assert session.midi_tracks[0].notes
        assert len({note.note for note in session.midi_tracks[0].notes}) > 1
        assert all(note.duration_frames > 0 and note.velocity > 0 for note in session.midi_tracks[0].notes)
        return writer(session, **kwargs)

    monkeypatch.setattr(logic_handoff, "export_logic_handoff", inspect_session)
    assert logic_handoff.main(["--stub", "--destination-root", str(tmp_path)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert len(captured) == 1
    assert all(not stem.path.exists() for stem in captured[0].stems)
    assert not captured[0].stems[0].path.parent.exists()
    assert (receipt["sample_rate"], receipt["frames"]) == (48_000, 192_000)
    assert len(receipt["stems"]) == 2
    assert [sf.info(path).channels for path in receipt["stems"]] == [1, 2]
    for output in receipt["stems"]:
        info = sf.info(output)
        assert (info.format, info.subtype, info.samplerate, info.frames) == ("WAV", "PCM_24", 48_000, 192_000)
        values, _rate = sf.read(output)
        assert np.max(np.abs(values)) > 0.01
    assert Path(receipt["midi"]).read_bytes().startswith(b"MThd")


def test_stub_cleans_up_temporary_sources_when_writer_fails_and_uses_default_root(tmp_path: Path, monkeypatch, capsys):
    captured = []

    def reject(session, *, destination_root):
        assert destination_root is None
        captured.append(session.stems[0].path.parent)
        raise LogicHandoffError("test failure")

    monkeypatch.setattr(logic_handoff, "export_logic_handoff", reject)
    assert logic_handoff.main(["--stub"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "test failure" in output.err
    assert not captured[0].exists()


def test_cli_invalid_manifest_does_not_create_destination(tmp_path: Path):
    path, data = _manifest(tmp_path)
    data["tempo_map"] = [{"frame": 48, "bpm": 90}]
    path.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "handoffs"
    result = _run("--session", str(path), "--destination-root", str(destination))
    assert result.returncode == 1
    assert "unsupported fields" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert not destination.exists()


def test_cli_missing_manifest_fails_without_traceback(tmp_path: Path):
    result = _run("--session", str(tmp_path / "absent.json"), "--destination-root", str(tmp_path / "handoffs"))
    assert result.returncode == 1
    assert "valid UTF-8 session JSON" in result.stderr
    assert "Traceback" not in result.stderr


def test_stub_subprocess_never_opens_sockets_launches_processes_or_imports_device_apis(tmp_path: Path):
    script = """
import importlib.abc
import runpy
import sys

class NoDevices(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sounddevice', 'pyaudio', 'rtmidi', 'PySide6', 'PyQt6'}:
            raise AssertionError('Device or UI API imported: ' + fullname)

def audit(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
        raise AssertionError('Unexpected external side effect: ' + event)

sys.meta_path.insert(0, NoDevices())
sys.addaudithook(audit)
sys.argv = ['logic_handoff', '--stub', '--destination-root', sys.argv[1]]
runpy.run_module('tools.logic_handoff', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert len(receipt["stems"]) == 2
    assert Path(receipt["midi"]).is_file()
