#!/usr/bin/env python3
"""Analyze one explicitly selected WAV offline; never capture or play audio."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import math
import os
from pathlib import Path
import stat
import sys


MAX_REPORT_BYTES = 256 * 1024
_ERRORS = {
    "invalid_arguments": (2, "Use --help for the required offline analysis arguments."),
    "invalid_configuration": (2, "The selected analysis configuration is invalid."),
    "same_clock_required": (2, "Confirm the same-clock capture assertion to analyze."),
    "invalid_file": (3, "The selected input is not a readable regular WAV file."),
    "unsupported_audio": (3, "The selected WAV encoding or sample format is unsupported."),
    "file_changed": (3, "The selected input changed during analysis."),
    "file_too_large": (4, "The selected input exceeds the file size limit."),
    "capture_too_long": (4, "The selected capture exceeds the duration limit."),
    "too_many_events": (4, "The selected capture exceeds the event limit."),
    "analysis_limit": (4, "Analysis stopped at a bounded resource limit."),
    "output_exists": (5, "The selected report destination already exists."),
    "output_unavailable": (5, "Could not create a new private report at that destination."),
    "analysis_failed": (6, "Offline analysis could not complete."),
}


class _CommandError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's message may contain private paths or arbitrary argument values.
        raise _CommandError("invalid_arguments")


def _channel(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 8:
        raise ValueError
    return result


def _delay(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError
    return result


def _parser() -> argparse.ArgumentParser:
    parser = _PrivateParser(
        prog="analyze_loopback_timing",
        allow_abbrev=False,
        description=(
            "Measure offline round-trip timing (RTT) from one explicit PCM WAV. "
            "This command never captures, plays audio, probes devices, or uses a network."
        ),
        epilog=(
            "RTT is not one-way latency or proof of playable latency. Physical "
            "validation is not performed. Exit codes: 0 measured, 1 indeterminate, "
            "2 arguments, 3 input, 4 resource limit, 5 output, 6 unexpected failure."
        ),
    )
    parser.add_argument("input", metavar="INPUT.wav", type=Path)
    parser.add_argument(
        "--reference-channel", type=_channel, required=True, metavar="N",
        help="1-based channel containing the direct synthetic launch",
    )
    parser.add_argument(
        "--return-channel", type=_channel, required=True, metavar="N",
        help="distinct 1-based channel containing the once-returned signal",
    )
    parser.add_argument(
        "--max-delay-ms", type=_delay, required=True, metavar="MS",
        help="explicit positive upper bound on RTT in milliseconds",
    )
    parser.add_argument(
        "--assert-same-clock",
        action="store_true",
        required=True,
        help=(
            "assert that both selected channels are one unedited, synthetic-only "
            "launch/once-returned loopback capture from one ADC clock; the file "
            "cannot verify this assertion or the wiring"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="NEW.json",
        help="write a new private JSON report instead of stdout; never overwrite",
    )
    return parser


def _output_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _write_report(path: Path, payload: bytes) -> None:
    """Exclusively create a private report, without replacing existing evidence."""
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    written_identity: tuple[int, ...] | None = None
    try:
        # Existing directories can report access denied rather than EEXIST on
        # Windows. This normalizes that error; O_EXCL still handles the race.
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise _CommandError("output_exists")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        for flag in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW"):
            flags |= getattr(os, flag, 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            raise _CommandError("output_exists") from None
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = None
            destination.write(payload)
            destination.flush()
            written = os.fstat(destination.fileno())
            if written.st_size != len(payload):
                raise OSError
            written_identity = _output_identity(written)
            os.fsync(destination.fileno())
            try:
                finished = os.fstat(destination.fileno())
                current = path.lstat()
            except OSError:
                # Ownership cannot be established. Do not delete another
                # writer's possible replacement while reporting this failure.
                raise _CommandError("output_unavailable") from None
            if (
                not stat.S_ISREG(current.st_mode)
                or not stat.S_ISREG(finished.st_mode)
                or _output_identity(finished) != written_identity
                or _output_identity(current) != written_identity
            ):
                raise _CommandError("output_unavailable")
    except (OSError, ValueError):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if identity is not None:
            try:
                current = path.lstat()
                if stat.S_ISREG(current.st_mode) and (
                    current.st_dev, current.st_ino
                ) == identity and (
                    written_identity is None
                    or _output_identity(current) == written_identity
                ):
                    path.unlink()
            except OSError:
                pass
        raise _CommandError("output_unavailable") from None


def _error(code: str) -> int:
    safe_code = code if code in _ERRORS else "analysis_failed"
    exit_code, message = _ERRORS[safe_code]
    print(
        json.dumps({"status": "error", "code": safe_code, "message": message}),
        file=sys.stderr,
    )
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.reference_channel == arguments.return_channel:
            raise _CommandError("invalid_arguments")
        # Lazy import keeps --help and invalid-argument handling independent of audio IO.
        from core.loopback_timing import LoopbackTimingError, analyze_loopback

        try:
            report = analyze_loopback(
                arguments.input,
                reference_channel=arguments.reference_channel,
                return_channel=arguments.return_channel,
                max_delay_ms=arguments.max_delay_ms,
                same_clock_confirmed=arguments.assert_same_clock,
            )
        except LoopbackTimingError as exc:
            raise _CommandError(exc.code) from None
        status = report.get("status")
        if status not in {"measured", "indeterminate"}:
            raise _CommandError("analysis_failed")
        payload = (
            json.dumps(report, allow_nan=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if len(payload) > MAX_REPORT_BYTES:
            raise _CommandError("analysis_limit")
        if arguments.output is None:
            sys.stdout.write(payload.decode("utf-8"))
        else:
            _write_report(arguments.output, payload)
        return 0 if status == "measured" else 1
    except _CommandError as exc:
        return _error(exc.code)
    except Exception:
        return _error("analysis_failed")


if __name__ == "__main__":
    raise SystemExit(main())
