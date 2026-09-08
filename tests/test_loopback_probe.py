"""The standard probe is a file-only, bounded synthetic calibration source."""

import json
import os
from pathlib import Path
import subprocess
import sys
import wave

import numpy as np
import pytest

from tools.make_loopback_probe import ProbeError, main, write_probe


def test_probe_has_100_isolated_aperiodic_bursts_and_quiet_ends(tmp_path):
    path = tmp_path / "probe.wav"
    report = write_probe(path)
    with wave.open(str(path), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 48000)
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        assert samples.size == report["frame_count"]
    # Discover bursts from the actual PCM, independently of the generator's
    # onset schedule or implementation helpers.
    active = np.flatnonzero(np.abs(samples.astype(np.int32)) > 5)
    splits = np.flatnonzero(np.diff(active) > 4800) + 1
    events = np.split(active, splits)
    assert len(events) == 100
    onsets = np.array([event[0] for event in events])
    intervals_ms = np.diff(onsets) / 48
    assert intervals_ms.min() >= 1250 and intervals_ms.max() <= 1650
    assert np.ptp(intervals_ms) > 200
    assert all(900 < event[-1] - event[0] < 960 for event in events)
    assert active[0] >= 57600 and samples.size - active[-1] >= 57600
    assert np.max(np.abs(samples.astype(np.int32))) <= 6554
    assert 125 < samples.size / 48000 < 180
    assert report["audio_played"] is False and report["audio_recorded"] is False
    assert report["physical_validation"] == "not_run"
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["file", "symlink", "directory", "missing_parent"])
def test_probe_creation_preserves_existing_files_and_refuses_unsafe_outputs(tmp_path, kind):
    target = tmp_path / "private-sentinel.wav"
    target.write_bytes(b"existing evidence")
    output = target
    if kind == "symlink":
        output = tmp_path / "link.wav"
        output.symlink_to(target)
    elif kind == "directory":
        output = tmp_path
    elif kind == "missing_parent":
        output = tmp_path / "absent" / "new.wav"
    with pytest.raises(ProbeError) as error:
        write_probe(output)
    assert "private-sentinel" not in str(error.value)
    assert str(tmp_path) not in str(error.value)
    assert target.read_bytes() == b"existing evidence"


def test_failed_probe_write_removes_only_its_own_partial_file(tmp_path, monkeypatch):
    output = tmp_path / "failed.wav"
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("private error")))
    with pytest.raises(ProbeError, match="use a new local file"):
        write_probe(output)
    assert not output.exists()


@pytest.mark.parametrize("replace_file", [False, True])
@pytest.mark.parametrize("sync_fails", [False, True])
def test_probe_output_mutation_during_flush_does_not_report_success(
    tmp_path, monkeypatch, replace_file, sync_fails,
):
    output = tmp_path / "new.wav"

    def change_output(fd):
        if replace_file:
            output.unlink()
        output.write_bytes(b"competing content")
        if sync_fails:
            raise OSError("private sync error")

    monkeypatch.setattr(os, "fsync", change_output)
    with pytest.raises(ProbeError, match="use a new local file"):
        write_probe(output)
    assert output.read_bytes() == b"competing content"


def test_probe_cli_errors_are_path_free(tmp_path, capsys):
    output = tmp_path / "private-sentinel.wav"
    output.write_bytes(b"untouched")
    assert main([str(output)]) == 1
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err) == {"error": "output_unavailable"}
    assert "private" not in streams.err
    with pytest.raises(SystemExit) as result:
        main(["--private-sentinel"])
    assert result.value.code == 2
    streams = capsys.readouterr()
    assert json.loads(streams.err) == {"error": "invalid_arguments"}
    assert output.read_bytes() == b"untouched"


def test_probe_help_does_not_import_or_call_media_device_libraries(tmp_path):
    script = (
        "import sys; "
        "sys.modules['sounddevice']=None; sys.modules['soundfile']=None; "
        "sys.modules['socket']=None; "
        "from tools.make_loopback_probe import main; main(['--help'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "never plays audio" in " ".join(result.stdout.split())
    assert result.stderr == ""
