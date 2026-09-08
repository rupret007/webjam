from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
from types import ModuleType
import wave

import numpy as np
import pytest

from tools import analyze_loopback_timing as cli


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "private-artist-path-and-token"


def _args(path: Path | str) -> list[str]:
    return [
        str(path),
        "--reference-channel", "1",
        "--return-channel", "2",
        "--max-delay-ms", "500",
        "--assert-same-clock",
    ]


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "-m", "tools.analyze_loopback_timing", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


@pytest.fixture
def analyzer(monkeypatch):
    module = ModuleType("core.loopback_timing")

    class FakeError(Exception):
        def __init__(self, code):
            self.code = code
            self.message = PRIVATE

    module.LoopbackTimingError = FakeError
    calls = []

    def analyze(path, **kwargs):
        calls.append((path, kwargs))
        return {"status": "measured", "summary": {"round_trip_ms": 100.0}}

    module.analyze_loopback = analyze
    monkeypatch.setitem(sys.modules, "core.loopback_timing", module)
    return module, calls


def _error(capsys, expected_code: str) -> dict:
    output = capsys.readouterr()
    assert output.out == ""
    assert PRIVATE not in output.err
    payload = json.loads(output.err)
    assert set(payload) == {"status", "code", "message"}
    assert payload["status"] == "error"
    assert payload["code"] == expected_code
    return payload


def test_stdout_report_maps_explicit_arguments_without_writing(analyzer, tmp_path, capsys):
    _, calls = analyzer
    selected = tmp_path / f"{PRIVATE}.wav"
    assert cli.main(_args(selected)) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert PRIVATE not in output.out
    assert json.loads(output.out)["status"] == "measured"
    assert calls == [(selected, {
        "reference_channel": 1, "return_channel": 2,
        "max_delay_ms": 500.0, "same_clock_confirmed": True,
    })]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("arguments", [
    [],
    [PRIVATE],
    _args(PRIVATE)[:-1],
    _args(PRIVATE) + [PRIVATE],
    _args(PRIVATE) + [f"--{PRIVATE}"],
    [PRIVATE, "--ref", "1", "--return-channel", "2", "--max-delay-ms", "500", "--assert-same-clock"],
    [PRIVATE, "--reference-channel", PRIVATE, "--return-channel", "2", "--max-delay-ms", "500", "--assert-same-clock"],
])
def test_argument_errors_never_echo_values_or_analyze(arguments, analyzer, capsys):
    assert cli.main(arguments) == 2
    assert analyzer[1] == []
    _error(capsys, "invalid_arguments")


@pytest.mark.parametrize("option,value", [
    ("--reference-channel", "0"), ("--reference-channel", "9"),
    ("--reference-channel", "2"), ("--return-channel", "-1"),
    ("--max-delay-ms", "nan"), ("--max-delay-ms", "inf"),
    ("--max-delay-ms", "-1"), ("--max-delay-ms", "0"),
    ("--max-delay-ms", PRIVATE),
])
def test_invalid_numeric_or_equal_channels_fail_before_analysis(option, value, analyzer, capsys):
    arguments = _args(PRIVATE)
    arguments[arguments.index(option) + 1] = value
    assert cli.main(arguments) == 2
    assert analyzer[1] == []
    _error(capsys, "invalid_arguments")


@pytest.mark.parametrize("code,exit_code", [
    ("invalid_configuration", 2), ("same_clock_required", 2),
    ("invalid_file", 3), ("unsupported_audio", 3), ("file_changed", 3),
    ("file_too_large", 4), ("capture_too_long", 4),
    ("too_many_events", 4), ("analysis_limit", 4),
    (PRIVATE, 6),
])
def test_core_errors_have_fixed_safe_codes_and_never_expose_messages(code, exit_code, analyzer, capsys):
    module, _ = analyzer

    def fail(*args, **kwargs):
        raise module.LoopbackTimingError(code)

    module.analyze_loopback = fail
    assert cli.main(_args(PRIVATE)) == exit_code
    _error(capsys, code if code != PRIVATE else "analysis_failed")


def test_unexpected_exception_is_sanitized(analyzer, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError(PRIVATE)

    analyzer[0].analyze_loopback = fail
    assert cli.main(_args(PRIVATE)) == 6
    _error(capsys, "analysis_failed")


def test_indeterminate_is_preserved_with_nonzero_exit(analyzer, capsys):
    report = {"status": "indeterminate", "reasons": ["insufficient_events"], "summary": None}
    analyzer[0].analyze_loopback = lambda *args, **kwargs: report
    assert cli.main(_args(PRIVATE)) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == report


def test_output_is_explicit_private_and_has_no_stdout(analyzer, tmp_path, capsys):
    selected = tmp_path / f"{PRIVATE}.wav"
    destination = tmp_path / f"{PRIVATE}.json"
    assert cli.main(_args(selected) + ["--output", str(destination)]) == 0
    output = capsys.readouterr()
    assert output.out == output.err == ""
    assert json.loads(destination.read_text())["status"] == "measured"
    assert PRIVATE not in destination.read_text()
    if os.name == "posix":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert set(tmp_path.iterdir()) == {destination}


@pytest.mark.parametrize("kind", ["existing", "input", "hardlink", "symlink", "dangling", "directory"])
def test_never_overwrites_any_existing_destination(kind, analyzer, tmp_path, capsys):
    selected = tmp_path / f"{PRIVATE}.wav"
    selected.write_bytes(b"original selected input")
    destination = tmp_path / "evidence.json"
    if kind == "existing":
        destination.write_bytes(b"earlier evidence")
    elif kind == "input":
        destination = selected
    elif kind == "hardlink":
        os.link(selected, destination)
    elif kind in {"symlink", "dangling"}:
        try:
            destination.symlink_to(selected if kind == "symlink" else tmp_path / "absent")
        except OSError:
            pytest.skip("symlinks unavailable")
    else:
        destination.mkdir()
    before = destination.lstat()
    assert cli.main(_args(selected) + ["--output", str(destination)]) == 5
    _error(capsys, "output_exists")
    assert selected.read_bytes() == b"original selected input"
    after = destination.lstat()
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns) == (
        before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns,
    )
    if kind == "existing":
        assert destination.read_bytes() == b"earlier evidence"


def test_missing_parent_is_not_created(analyzer, tmp_path, capsys):
    destination = tmp_path / PRIVATE / "report.json"
    assert cli.main(_args(PRIVATE) + ["--output", str(destination)]) == 5
    _error(capsys, "output_unavailable")
    assert not destination.parent.exists()


def test_creation_race_preserves_competing_report(analyzer, monkeypatch, tmp_path, capsys):
    destination = tmp_path / "report.json"
    original_open = os.open

    def race(path, flags, mode):
        assert flags & os.O_EXCL
        destination.write_bytes(b"competing evidence")
        return original_open(path, flags, mode)

    monkeypatch.setattr(cli.os, "open", race)
    assert cli.main(_args(PRIVATE) + ["--output", str(destination)]) == 5
    _error(capsys, "output_exists")
    assert destination.read_bytes() == b"competing evidence"


@pytest.mark.parametrize("replace_partial", [False, True])
def test_failed_flush_removes_only_owned_partial_output(replace_partial, analyzer, monkeypatch, tmp_path, capsys):
    if replace_partial and os.name == "nt":
        pytest.skip("Windows does not allow replacing this open output descriptor")
    destination = tmp_path / "report.json"

    def fail_sync(_descriptor):
        if replace_partial:
            destination.unlink()
            destination.write_bytes(b"other writer")
        raise OSError(PRIVATE)

    monkeypatch.setattr(cli.os, "fsync", fail_sync)
    assert cli.main(_args(PRIVATE) + ["--output", str(destination)]) == 5
    _error(capsys, "output_unavailable")
    if replace_partial:
        assert destination.read_bytes() == b"other writer"
    else:
        assert not destination.exists()


@pytest.mark.parametrize("change", ["replace", "truncate", "same_size"])
@pytest.mark.parametrize("sync_error", [False, True])
def test_output_changed_during_fsync_fails_and_preserves_competitor(
    change, sync_error, analyzer, monkeypatch, tmp_path, capsys,
):
    if change == "replace" and os.name == "nt":
        pytest.skip("Windows does not allow replacing this open output descriptor")
    destination = tmp_path / "report.json"
    competing = None

    def change_output(_descriptor):
        nonlocal competing
        if change == "replace":
            destination.unlink()
            destination.write_bytes(b"replacement evidence")
        elif change == "truncate":
            destination.write_bytes(b"changed evidence")
        else:
            before = destination.stat()
            with destination.open("r+b") as other:
                other.write(b"X")
            # Force a distinguishable write timestamp even on coarse file
            # systems; size and inode stay unchanged in this case.
            os.utime(destination, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
        competing = destination.read_bytes()
        if sync_error:
            raise OSError(PRIVATE)

    monkeypatch.setattr(cli.os, "fsync", change_output)
    assert cli.main(_args(PRIVATE) + ["--output", str(destination)]) == 5
    _error(capsys, "output_unavailable")
    assert competing is not None
    assert destination.read_bytes() == competing


@pytest.mark.parametrize("report", [
    {"status": "unexpected"},
    {"status": "measured", "summary": float("nan")},
    {"status": "measured", "oversize": "x" * cli.MAX_REPORT_BYTES},
])
def test_unusable_report_never_publishes_output(report, analyzer, tmp_path, capsys):
    analyzer[0].analyze_loopback = lambda *args, **kwargs: report
    destination = tmp_path / "report.json"
    expected = 4 if "oversize" in report else 6
    assert cli.main(_args(PRIVATE) + ["--output", str(destination)]) == expected
    _error(capsys, "analysis_limit" if expected == 4 else "analysis_failed")
    assert not destination.exists()


def test_help_is_safe_in_fresh_process_without_loading_analysis_or_live_modules():
    script = """
import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'soundfile', 'sounddevice', 'services', 'webjam_qt'} or fullname == 'core.loopback_timing':
            raise RuntimeError('forbidden import')
sys.meta_path.insert(0, Guard())
sys.argv = ['private-script-location', '--help']
runpy.run_module('tools.analyze_loopback_timing', run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0
    assert result.stderr == ""
    assert "private-script-location" not in result.stdout
    for term in ("--assert-same-clock", "--output", "RTT", "never captures", "one-way", "Exit codes"):
        assert term in result.stdout


def test_invalid_argument_subprocess_does_not_echo_private_argv():
    result = _run(*_args(PRIVATE), f"--{PRIVATE}")
    assert result.returncode == 2
    assert result.stdout == ""
    assert PRIVATE not in result.stderr
    assert json.loads(result.stderr)["code"] == "invalid_arguments"


def _capture(path: Path, *, silent: bool = False) -> None:
    rate = 48_000
    event_frames = []
    position = 0.3
    for index in range(24):
        event_frames.append(round(position * rate))
        position += (0.7, 0.8, 0.95)[index % 3]
    samples = np.zeros((round((position + 1) * rate), 2), dtype=np.float64)
    if not silent:
        t = np.arange(720) / rate
        burst = 0.3 * np.sin(2 * np.pi * 3100 * t) * np.hanning(len(t))
        for frame in event_frames:
            samples[frame:frame + len(burst), 0] = burst
            samples[frame + 4800:frame + 4800 + len(burst), 1] = burst * 0.6
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(np.rint(samples * 32767).astype("<i2").tobytes())


def test_real_synthetic_wav_cli_reports_rtt_and_preserves_input(tmp_path):
    selected = tmp_path / f"{PRIVATE}.wav"
    _capture(selected)
    original = hashlib.sha256(selected.read_bytes()).hexdigest()
    result = _run(*_args(selected))
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stderr == ""
    assert PRIVATE not in result.stdout
    assert str(tmp_path) not in result.stdout
    report = json.loads(result.stdout)
    assert report["status"] == "measured"
    assert report["summary"]["median_ms"] == pytest.approx(100, abs=1.1)
    assert report["summary"]["p95_ms"] == pytest.approx(100, abs=1.1)
    assert hashlib.sha256(selected.read_bytes()).hexdigest() == original
    assert set(tmp_path.iterdir()) == {selected}


def test_real_silence_returns_indeterminate_report(tmp_path):
    selected = tmp_path / f"{PRIVATE}.wav"
    _capture(selected, silent=True)
    result = _run(*_args(selected))
    assert result.returncode == 1, result.stderr or result.stdout
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["status"] == "indeterminate"
    assert report["summary"] is None
    assert PRIVATE not in result.stdout


def test_real_invalid_file_error_is_private(tmp_path):
    selected = tmp_path / f"{PRIVATE}.wav"
    selected.write_bytes(f"not a wav {PRIVATE}".encode())
    result = _run(*_args(selected))
    assert result.returncode == 3
    assert result.stdout == ""
    assert PRIVATE not in result.stderr
    assert str(tmp_path) not in result.stderr
    assert json.loads(result.stderr)["status"] == "error"


def test_real_wav_metadata_stays_private_in_explicit_report(tmp_path):
    selected = tmp_path / f"{PRIVATE}.wav"
    destination = tmp_path / f"{PRIVATE}.json"
    _capture(selected)
    encoded = PRIVATE.encode() + b"\0"
    artist = b"IART" + struct.pack("<I", len(encoded)) + encoded
    artist += b"\0" * (len(encoded) % 2)
    metadata = b"INFO" + artist
    content = selected.read_bytes() + b"LIST" + struct.pack("<I", len(metadata)) + metadata
    content = content[:4] + struct.pack("<I", len(content) - 8) + content[8:]
    selected.write_bytes(content)
    result = _run(*_args(selected), "--output", str(destination))
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    report_text = destination.read_text()
    assert PRIVATE not in report_text
    assert str(tmp_path) not in report_text
    assert "IART" not in report_text
    assert json.loads(report_text)["summary"]["median_ms"] == pytest.approx(100, abs=1.1)
    assert selected.read_bytes() == content
    before = destination.read_bytes()
    repeated = _run(*_args(selected), "--output", str(destination))
    assert repeated.returncode == 5
    assert repeated.stdout == ""
    assert json.loads(repeated.stderr)["code"] == "output_exists"
    assert PRIVATE not in repeated.stderr
    assert destination.read_bytes() == before


def test_real_analysis_has_no_device_network_or_process_side_effects(tmp_path):
    selected = tmp_path / "capture.wav"
    _capture(selected)
    script = """
import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sounddevice', 'pyaudio', 'services', 'webjam_qt'}:
            raise RuntimeError('forbidden device or application module')
def audit(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
        raise RuntimeError('forbidden live operation')
    if event == 'open' and isinstance(args[0], str) and args[0].startswith('/dev/'):
        raise RuntimeError('forbidden device open')
sys.meta_path.insert(0, Guard())
sys.addaudithook(audit)
sys.argv = ['analyze_loopback_timing', *sys.argv[1:]]
runpy.run_module('tools.analyze_loopback_timing', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, *_args(selected)],
        cwd=ROOT, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stderr == ""
    assert json.loads(result.stdout)["summary"]["median_ms"] == pytest.approx(100, abs=1.1)
