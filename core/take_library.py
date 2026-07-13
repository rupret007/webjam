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
import json
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
    source: str = "jamulus_server"

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
    validation_status: str = "unchecked"
    manifest_path: Optional[Path] = None
    # Findings recorded at validation time, so reviewing a finished take
    # never needs to re-probe its audio files.
    manifest_errors: tuple[str, ...] = ()
    manifest_warnings: tuple[str, ...] = ()
    session_title: str = ""

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def duration_s(self) -> float:
        """Wall-clock length of the take: the latest track end."""
        return max((t.end_s for t in self.tracks), default=0.0)

    @property
    def display_name(self) -> str:
        """Musician-facing name, falling back to the recorder folder name."""
        return self.session_title.strip() or self.name


@dataclass(frozen=True)
class TakeValidationResult:
    """Post-recording confidence report for one take directory."""

    take: Optional[TakeInfo]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest_path: Optional[Path] = None

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


def validate_take(take_dir: str | Path, *, expected_tracks: int = 0,
                  require_48k: bool = True,
                  required_local_stems: int = 0) -> TakeValidationResult:
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
    if require_48k and any(rate != 48000 for rate in rates):
        errors.append(f"All tracks must be 48 kHz; found {sorted(rates)}.")
    local_tracks = [track for track in take.tracks if track.source == "local_ssl"]
    if required_local_stems and len(local_tracks) < required_local_stems:
        errors.append(
            f"Expected {required_local_stems} isolated host stems but found "
            f"{len(local_tracks)}."
        )
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
    return TakeValidationResult(
        take, tuple(errors), tuple(warnings), take.manifest_path,
    )


_LOCAL_STEM_PREFIXES = ("host-guitar", "host-vocal")
_ENVELOPE_BLOCK = 480  # 10 ms at 48 kHz → a true 100 Hz amplitude envelope

# Confidence is the normalized full-rate correlation at the refined lag: the
# same performance through the local and server paths scores near 1.0 even
# after the Opus codec, while unrelated audio stays near zero (≈0.01 for a
# five-second window), so 0.15 is a conservative floor rather than a tuned
# value.
ALIGNMENT_CONFIDENCE_MIN = 0.15
ALIGNMENT_METHOD = "envelope+refine-v2"


def is_local_stem_name(name: str) -> bool:
    """True for supplemental host stems (host-guitar*.wav / host-vocal*.wav)."""
    lowered = name.lower()
    return lowered.endswith(".wav") and lowered.startswith(_LOCAL_STEM_PREFIXES)


def server_track_channel_id(filename: str) -> Optional[int]:
    """Extract Jamulus's channel id from its ``...-id-channels.wav`` name."""
    match = re.search(r"-(\d+)-\d+\.[A-Za-z0-9]+$", str(filename))
    return int(match.group(1)) if match else None


def _envelope_100hz(signal):
    """Rectified block-mean envelope, mean-subtracted for correlation.

    Block averaging (unlike stride decimation) is alias-free, so the peak and
    the confidence it feeds reflect real amplitude agreement.
    """
    import numpy as np

    usable = (signal.size // _ENVELOPE_BLOCK) * _ENVELOPE_BLOCK
    if usable == 0:
        return np.zeros(0, dtype="float32")
    env = np.abs(signal[:usable]).reshape(-1, _ENVELOPE_BLOCK).mean(axis=1)
    return env - float(np.mean(env))


def _refine_lag(server_sig, local_sig, coarse_lag: int, anchor: int) -> tuple[int, float]:
    """Sample-accurate lag within ±one envelope block of the coarse peak.

    Sweeps a bounded normalized correlation of the raw 48 kHz signals around
    the loudest local passage, removing the 10 ms envelope quantization that
    would otherwise land in the manifest offset.  Returns ``(lag, value)``;
    the value doubles as the alignment confidence because raw-sample
    correlation separates a genuine match from unrelated audio far more
    sharply than the coarse envelope does.
    """
    import numpy as np

    half = 48000 * 5 // 2
    best_val = 0.0
    best_lag = coarse_lag
    for lag in range(coarse_lag - _ENVELOPE_BLOCK, coarse_lag + _ENVELOPE_BLOCK + 1):
        start = max(0, anchor - half, -lag)
        stop = min(len(local_sig), anchor + half, len(server_sig) - lag)
        if stop - start < 4800:  # need at least 100 ms of overlap
            continue
        local_part = local_sig[start:stop]
        server_part = server_sig[start + lag:stop + lag]
        denom = float(np.linalg.norm(local_part) * np.linalg.norm(server_part))
        if denom <= 0.0:
            continue
        value = abs(float(np.dot(local_part, server_part))) / denom
        if value > best_val:
            best_val = value
            best_lag = lag
    return best_lag, best_val


def estimate_local_alignment(take_dir: str | Path) -> tuple[float, float]:
    """Estimate local-stem offset against the closest Jamulus server track.

    Returns ``(offset_seconds, confidence)``.  The offset is signed: it is
    negative when the local stems start before the server take, which is the
    normal case because supplemental capture arms before the server recorder
    acknowledges the RPC start.  Correlation is bounded to keep post-record
    validation responsive and never runs on the audio thread.
    """
    path = Path(take_dir)
    wavs = sorted(p for p in path.glob("*.wav") if p.is_file())
    local = [p for p in wavs if is_local_stem_name(p.name)]
    server = [p for p in wavs if not is_local_stem_name(p.name)]
    if len(local) < 2 or not server:
        return (0.0, 0.0)
    try:
        import numpy as np
        import soundfile as sf  # type: ignore

        limit = 48000 * 60
        first, rate = sf.read(str(local[0]), frames=limit, dtype="float32")
        second, second_rate = sf.read(str(local[1]), frames=limit, dtype="float32")
        if rate != 48000 or second_rate != rate:
            return (0.0, 0.0)
        length = min(len(first), len(second))
        local_mix = np.asarray(first)[:length] + np.asarray(second)[:length]
        if float(np.max(np.abs(local_mix))) < 1e-5:
            return (0.0, 0.0)
        local_env = _envelope_100hz(local_mix)
        env_norm = float(np.linalg.norm(local_env))
        if local_env.size < 256 or env_norm <= 0.0:
            return (0.0, 0.0)
        # Refine around the loudest local passage, not the take start, so a
        # quiet count-in doesn't starve the fine correlation of signal.
        anchor = int(np.argmax(np.abs(local_env))) * _ENVELOPE_BLOCK
        best_confidence = 0.0
        best_lag_samples = 0
        for candidate in server:
            audio, candidate_rate = sf.read(
                str(candidate), frames=limit, dtype="float32", always_2d=True
            )
            if candidate_rate != rate or audio.size == 0:
                continue
            mono = np.mean(audio, axis=1)
            candidate_env = _envelope_100hz(mono)
            denom = float(np.linalg.norm(candidate_env)) * env_norm
            if candidate_env.size == 0 or denom <= 0.0:
                continue
            correlation = np.correlate(candidate_env, local_env, mode="full")
            index = int(np.argmax(correlation))
            coarse = (index - (len(local_env) - 1)) * _ENVELOPE_BLOCK
            lag, confidence = _refine_lag(mono, local_mix, coarse, anchor)
            if confidence > best_confidence:
                best_lag_samples = lag
                best_confidence = confidence
        return (best_lag_samples / rate, best_confidence)
    except Exception:  # noqa: BLE001
        _logger.exception("Could not align isolated host stems")
        return (0.0, 0.0)


def write_take_manifest(
    take_dir: str | Path, *, expected_tracks: int, required_local_stems: int,
    local_started_utc: str = "", local_duration_s: float = 0.0,
    capture_errors: tuple[str, ...] = (), app_version: str = "",
    participant_names: Optional[dict[int, str]] = None,
    session_title: str = "",
) -> TakeValidationResult:
    """Validate a take and atomically persist a secret-free evidence manifest."""
    from core.file_io import atomic_write_text

    path = Path(take_dir)
    offset_s, confidence = estimate_local_alignment(path)
    # Write a preliminary manifest so load_take can classify supplemental files.
    manifest_path = path / "webjam-take.json"
    preliminary = {
        "schema_version": 1,
        "app_version": app_version,
        "session_title": str(session_title or "").strip(),
        "status": "validating",
        "expected_server_tracks": expected_tracks,
        "required_local_stems": required_local_stems,
        "local_capture": {
            "started_utc": local_started_utc,
            "duration_s": round(local_duration_s, 6),
            "offset_s": round(offset_s, 6),
            "alignment_confidence": round(confidence, 6),
            "alignment_method": ALIGNMENT_METHOD,
            "errors": list(capture_errors),
        },
        "tracks": [
            {"filename": p.name,
             "name": (
                 (participant_names or {}).get(server_track_channel_id(p.name))
                 if not is_local_stem_name(p.name) else None
             ),
             "source": "local_ssl" if is_local_stem_name(p.name) else "jamulus_server",
             "offset_s": round(offset_s, 6) if is_local_stem_name(p.name) else None}
            for p in sorted(path.glob("*.wav"))
        ],
    }
    atomic_write_text(manifest_path, json.dumps(preliminary, indent=2), mode=0o600)
    result = validate_take(
        path, expected_tracks=expected_tracks + required_local_stems,
        required_local_stems=required_local_stems,
    )
    errors = list(result.errors) + list(capture_errors)
    if required_local_stems and confidence < ALIGNMENT_CONFIDENCE_MIN:
        errors.append("Isolated host stems could not be aligned confidently.")
    take = result.take
    track_evidence = []
    if take is not None:
        for track in take.tracks:
            try:
                size = track.path.stat().st_size
            except OSError:
                size = 0
            track_evidence.append({
                "filename": track.path.name, "name": track.name,
                "source": track.source, "size_bytes": size,
                "sample_rate": track.samplerate,
                "duration_s": round(track.duration_s, 6),
                "offset_s": round(track.offset_s, 6),
                "has_signal": _track_has_signal(track.path),
            })
    preliminary.update({
        "status": "complete" if not errors else "needs_attention",
        "errors": errors,
        "warnings": list(result.warnings),
        "tracks": track_evidence,
    })
    atomic_write_text(manifest_path, json.dumps(preliminary, indent=2), mode=0o600)
    loaded = load_take(path)
    return TakeValidationResult(
        loaded, tuple(errors), result.warnings, manifest_path,
    )


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

    manifest_path = take_dir / "webjam-take.json"
    manifest: dict = {}
    try:
        loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded_manifest, dict):
            manifest = loaded_manifest
    except (OSError, ValueError):
        manifest_path = None
    manifest_tracks = {
        item.get("filename"): item for item in manifest.get("tracks", [])
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }

    # Offsets: prefer a .lof if present, then manifest overrides for local stems.
    offsets: dict[str, float] = {}
    lofs = list(take_dir.glob("*.lof"))
    if lofs:
        offsets = parse_lof_offsets(lofs[0])

    reaper = next(iter(take_dir.glob("*.rpp")), None)

    tracks: List[TrackInfo] = []
    for audio in audio_files:
        duration, rate = _probe_audio(audio)
        evidence = manifest_tracks.get(audio.name, {})
        manifest_offset = evidence.get("offset_s")
        offset = offsets.get(audio.name, 0.0)
        if isinstance(manifest_offset, (int, float)):
            offset = float(manifest_offset)
        tracks.append(TrackInfo(
            path=audio,
            name=str(evidence.get("name") or _prettify(audio.stem)),
            # Signed: local stems normally start before the server take, so a
            # negative offset here is valid alignment, not an error.
            offset_s=offset,
            duration_s=duration,
            samplerate=rate,
            source=str(evidence.get("source") or "jamulus_server"),
        ))

    def _string_items(key: str) -> tuple[str, ...]:
        value = manifest.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    return TakeInfo(
        path=take_dir,
        name=take_dir.name,
        tracks=tracks,
        reaper_project=reaper,
        validation_status=str(manifest.get("status") or "unchecked"),
        manifest_path=manifest_path,
        manifest_errors=_string_items("errors"),
        manifest_warnings=_string_items("warnings"),
        session_title=str(manifest.get("session_title") or "").strip(),
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
