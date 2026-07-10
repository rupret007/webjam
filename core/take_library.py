"""
Take library — discover recorded sessions and read their track layout.

A "take" is one Jamulus multitrack recording: a folder of per-musician WAV
files plus a Reaper ``.rpp`` and/or an Audacity ``.lof`` that carry each
track's *start offset* (musicians connect at different moments, so tracks
don't all begin at t=0). This module turns a directory of such folders into
structured ``TakeInfo`` objects for the Take Deck to play back.

Deliberately dependency-light: only ``soundfile`` (for durations/samplerate),
and even that is imported lazily so the module stays importable in
environments without libsndfile.
"""

from __future__ import annotations

import logging
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_logger = logging.getLogger("webjam.take_library")

_AUDIO_EXTS = {".wav", ".flac", ".ogg", ".aiff", ".aif"}


@dataclass
class TrackInfo:
    """One audio track within a take."""
    path: Path
    name: str
    offset_s: float = 0.0          # start offset within the take timeline
    duration_s: float = 0.0        # audio length (0 if unknown)
    samplerate: int = 0

    @property
    def end_s(self) -> float:
        return self.offset_s + self.duration_s


@dataclass
class TakeInfo:
    """One recorded session: a folder of tracks + timing metadata."""
    path: Path
    name: str
    tracks: List[TrackInfo] = field(default_factory=list)
    reaper_project: Optional[Path] = None

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def duration_s(self) -> float:
        """Wall-clock length of the take: the latest track end."""
        return max((t.end_s for t in self.tracks), default=0.0)


@dataclass(frozen=True)
class TakeValidationResult:
    """Post-recording confidence report for one take directory."""

    take: Optional[TakeInfo]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.take is not None and not self.errors

    @property
    def summary(self) -> str:
        if self.take is None:
            return "No completed take was found."
        rate_values = {t.samplerate for t in self.take.tracks if t.samplerate > 0}
        rate = (
            f"{next(iter(rate_values)) / 1000:g} kHz"
            if len(rate_values) == 1 else "mixed rate"
        )
        duration = int(round(self.take.duration_s))
        return (
            f"{self.take.track_count} track{'s' if self.take.track_count != 1 else ''}"
            f" · {duration // 60}:{duration % 60:02d} · {rate}"
        )


def snapshot_take_directories(root: str | Path) -> dict[Path, int]:
    """Return immediate take directories and mtimes without ever raising."""
    path = Path(root).expanduser()
    try:
        if not path.is_dir():
            return {}
        return {
            child: child.stat().st_mtime_ns
            for child in path.iterdir()
            if child.is_dir()
        }
    except OSError:
        return {}


def find_changed_take(root: str | Path, before: dict[Path, int]) -> Optional[Path]:
    """Find the newest new/modified take directory since ``before``."""
    path = Path(root).expanduser()
    candidates: list[tuple[int, Path]] = []
    try:
        for child in path.iterdir():
            if not child.is_dir():
                continue
            stamp = child.stat().st_mtime_ns
            if child not in before or stamp > before[child]:
                candidates.append((stamp, child))
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def wait_for_take_files_stable(
    take_dir: Path,
    *,
    polls: int = 8,
    interval_s: float = 0.25,
) -> bool:
    """Wait until audio file sizes stop changing for two consecutive polls."""
    previous: Optional[tuple[tuple[str, int], ...]] = None
    stable = 0
    for _ in range(max(1, polls)):
        try:
            current = tuple(sorted(
                (p.name, p.stat().st_size)
                for p in take_dir.iterdir()
                if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
            ))
        except OSError:
            current = ()
        if current and current == previous:
            stable += 1
            if stable >= 2:
                return True
        else:
            stable = 0
        previous = current
        time.sleep(max(0.0, interval_s))
    return False


def _track_has_signal(path: Path) -> Optional[bool]:
    """Sample a few short windows; return None when the file cannot be read."""
    try:
        import numpy as np
        import soundfile as sf  # type: ignore

        with sf.SoundFile(str(path)) as audio:
            if len(audio) <= 0:
                return False
            window = min(4096, len(audio))
            starts = {0, max(0, len(audio) // 2 - window // 2), max(0, len(audio) - window)}
            for start in starts:
                audio.seek(start)
                block = audio.read(window, dtype="float32", always_2d=True)
                if block.size and float(np.max(np.abs(block))) > 1e-5:
                    return True
            return False
    except Exception:  # noqa: BLE001
        return None


def validate_take(take_dir: str | Path, *, expected_tracks: int = 0) -> TakeValidationResult:
    """Validate a completed Jamulus take and report errors separately from warnings."""
    path = Path(take_dir).expanduser()
    take = load_take(path)
    if take is None:
        return TakeValidationResult(None, ("No readable audio tracks were created.",))

    errors: list[str] = []
    warnings: list[str] = []
    if expected_tracks > 0 and take.track_count < expected_tracks:
        errors.append(
            f"Expected at least {expected_tracks} tracks but found {take.track_count}."
        )
    rates = {track.samplerate for track in take.tracks if track.samplerate > 0}
    if len(rates) > 1:
        errors.append(f"Tracks use different sample rates: {sorted(rates)}.")
    for track in take.tracks:
        try:
            size = track.path.stat().st_size
        except OSError:
            size = 0
        if size <= 44 or track.duration_s <= 0 or track.samplerate <= 0:
            errors.append(f"{track.name} is empty or unreadable.")
            continue
        signal = _track_has_signal(track.path)
        if signal is False:
            warnings.append(f"{track.name} appears silent.")
        elif signal is None:
            warnings.append(f"{track.name} could not be checked for audible signal.")
    return TakeValidationResult(take, tuple(errors), tuple(warnings))


def _probe_audio(path: Path) -> tuple[float, int]:
    """Return (duration_s, samplerate). Tries soundfile, falls back to the
    stdlib ``wave`` module for plain WAVs, returns (0, 0) if unreadable."""
    try:
        import soundfile as sf  # type: ignore
        info = sf.info(str(path))
        if info.samplerate > 0:
            return (info.frames / info.samplerate, int(info.samplerate))
    except Exception as exc:  # noqa: BLE001
        _logger.debug("soundfile probe failed for %s: %s", path, exc)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                if rate > 0:
                    return (w.getnframes() / rate, rate)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("wave probe failed for %s: %s", path, exc)
    return (0.0, 0)


def parse_lof_offsets(lof_path: Path) -> dict[str, float]:
    """Parse an Audacity ``.lof`` file into {filename: offset_seconds}.

    Lines look like:  file "guitar.wav" offset 3.5
    Only ``file`` directives are honoured; malformed lines are skipped.
    Filenames are keyed by basename so they match regardless of quoting or
    any path prefix the recorder wrote.
    """
    offsets: dict[str, float] = {}
    try:
        text = lof_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _logger.debug("could not read lof %s: %s", lof_path, exc)
        return offsets
    # file "name" [offset N]
    pattern = re.compile(
        r'^\s*file\s+"([^"]+)"(?:.*?\boffset\s+([-+]?\d+(?:\.\d+)?))?',
        re.IGNORECASE,
    )
    for line in text.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        name = Path(m.group(1)).name
        try:
            offsets[name] = float(m.group(2)) if m.group(2) is not None else 0.0
        except (TypeError, ValueError):
            offsets[name] = 0.0
    return offsets


def _prettify(stem: str) -> str:
    """Turn a track filename stem into a display name."""
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return cleaned.title() if cleaned else stem


def load_take(take_dir: Path) -> Optional[TakeInfo]:
    """Build a TakeInfo from a single take folder, or None if it has no
    audio tracks."""
    take_dir = Path(take_dir)
    if not take_dir.is_dir():
        return None

    audio_files = sorted(
        p for p in take_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
    )
    if not audio_files:
        return None

    # Offsets: prefer a .lof if present (simple, unambiguous).
    offsets: dict[str, float] = {}
    lofs = list(take_dir.glob("*.lof"))
    if lofs:
        offsets = parse_lof_offsets(lofs[0])

    reaper = next(iter(take_dir.glob("*.rpp")), None)

    tracks: List[TrackInfo] = []
    for audio in audio_files:
        duration, rate = _probe_audio(audio)
        tracks.append(TrackInfo(
            path=audio,
            name=_prettify(audio.stem),
            offset_s=max(0.0, offsets.get(audio.name, 0.0)),
            duration_s=duration,
            samplerate=rate,
        ))

    return TakeInfo(
        path=take_dir,
        name=take_dir.name,
        tracks=tracks,
        reaper_project=reaper,
    )


def discover_takes(root: str | Path) -> List[TakeInfo]:
    """Scan ``root`` for take folders, newest first.

    A take folder is any immediate subdirectory containing audio files; if
    ``root`` itself directly contains audio files it's treated as a single
    take. Never raises — an unreadable root yields an empty list.
    """
    root = Path(root).expanduser()
    if not root.is_dir():
        return []

    takes: List[TakeInfo] = []
    try:
        # root-as-single-take
        direct = load_take(root)
        if direct is not None:
            takes.append(direct)
        for child in root.iterdir():
            if child.is_dir():
                take = load_take(child)
                if take is not None:
                    takes.append(take)
    except OSError as exc:
        _logger.warning("could not scan takes root %s: %s", root, exc)
        return takes

    def _mtime(take: TakeInfo) -> float:
        # exists()+stat() would TOCTOU-race a deleted folder; stat() alone,
        # guarded, keeps the documented "never raises" contract.
        try:
            return take.path.stat().st_mtime
        except OSError:
            return 0.0

    takes.sort(key=_mtime, reverse=True)
    return takes
