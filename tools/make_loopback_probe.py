"""Write an explicit synthetic timing probe; never play or record audio."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import random
import stat
import struct
import sys
import wave

SAMPLE_RATE = 48_000
BURST_COUNT = 100
BURST_FRAMES = 960
QUIET_FRAMES = 57_600


class ProbeError(Exception):
    """A fixed, path-free file creation failure."""


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        del message
        self.exit(2, '{"error":"invalid_arguments"}\n')


def _onsets() -> list[int]:
    rng = random.Random(20260908)
    positions = [QUIET_FRAMES]
    for _ in range(BURST_COUNT - 1):
        positions.append(positions[-1] + rng.randrange(60_000, 79_201))
    return positions


def write_probe(output: str | Path) -> dict:
    """Exclusively create one private mono PCM WAV at the explicit path."""

    path = Path(output)
    descriptor = None
    owned = None
    written = None
    cleanup_allowed = True
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        owned = os.fstat(descriptor)
        if not stat.S_ISREG(owned.st_mode):
            raise OSError("nonregular output")
        onsets = _onsets()
        frame_count = onsets[-1] + BURST_FRAMES + QUIET_FRAMES
        burst = struct.pack(
            "<" + "h" * BURST_FRAMES,
            *(round(
                32767 * 0.2
                * math.sin(2 * math.pi * 3100 * i / SAMPLE_RATE)
                * (0.5 - 0.5 * math.cos(2 * math.pi * i / (BURST_FRAMES - 1))))
              for i in range(BURST_FRAMES)),
        )
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = None
            with wave.open(destination, "wb") as audio:
                audio.setparams((1, 2, SAMPLE_RATE, frame_count, "NONE", "not compressed"))

                def silence(frames: int) -> None:
                    while frames:
                        amount = min(frames, 32_768)
                        audio.writeframesraw(b"\0\0" * amount)
                        frames -= amount

                cursor = 0
                for onset in onsets:
                    silence(onset - cursor)
                    audio.writeframesraw(burst)
                    cursor = onset + BURST_FRAMES
                silence(frame_count - cursor)
            destination.flush()
            written = os.fstat(destination.fileno())
            if written.st_size != 44 + frame_count * 2:
                cleanup_allowed = False
                raise OSError("incomplete output")
            os.fsync(destination.fileno())
            current = path.lstat()
            if (not stat.S_ISREG(current.st_mode)
                    or _identity(os.fstat(destination.fileno())) != _identity(written)
                    or _identity(current) != _identity(written)):
                cleanup_allowed = False
                raise OSError("output changed")
        return {
            "schema_version": 1,
            "status": "probe_created",
            "sample_rate_hz": SAMPLE_RATE,
            "channels": 1,
            "pcm_bits": 16,
            "frame_count": frame_count,
            "duration_seconds": round(frame_count / SAMPLE_RATE, 6),
            "burst_count": BURST_COUNT,
            "burst_duration_ms": 20,
            "maximum_amplitude": 0.2,
            "onset_gap_range_ms": [1250, 1650],
            "quiet_lead_and_tail_ms": 1200,
            "audio_played": False,
            "audio_recorded": False,
            "physical_validation": "not_run",
        }
    except (OSError, ValueError, wave.Error):
        if owned is not None and cleanup_allowed:
            try:
                current = path.lstat()
                if (stat.S_ISREG(current.st_mode)
                        and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino)
                        and (written is None or _identity(current) == _identity(written))):
                    path.unlink()
            except OSError:
                pass
        raise ProbeError("Probe output could not be created safely; use a new local file.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="make_loopback_probe", allow_abbrev=False,
        description=(
            "Create a new synthetic-only timing probe WAV. This writes a file; "
            "it never plays audio, opens devices or records. Physical capture "
            "requires a separate explicit decision and isolated setup."
        ),
    )
    parser.add_argument("output", metavar="NEW_PROBE.wav", help="new file; existing files are refused")
    args = parser.parse_args(argv)
    try:
        result = write_probe(args.output)
    except ProbeError:
        print('{"error":"output_unavailable"}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
