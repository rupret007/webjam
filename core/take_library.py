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
