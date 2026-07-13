"""Safe, non-destructive exports from a WebJam take.

WebJam cannot and should not manufacture Logic Pro's proprietary ``.logicx``
project format.  Instead it prepares the interchange Logic handles best: one
24-bit WAV per track, all rendered onto the same zero-based timeline, plus a
stereo reference mix and a small evidence manifest.  Original recorder files
are never modified.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from core.take_library import TakeInfo


class TakeExportError(RuntimeError):
    """Raised when a take cannot be exported without compromising alignment."""


@dataclass(frozen=True)
class TrackMixSettings:
    """Non-destructive rough-mix state for one Studio lane."""

    gain: float = 1.0
    pan: float = 0.0
    muted: bool = False
    solo: bool = False


@dataclass(frozen=True)
class LogicExportResult:
    """Files produced by :func:`export_logic_package`."""

    folder: Path
    stems: tuple[Path, ...]
    mixdown: Path
    manifest: Path
    instructions: Path
    samplerate: int
    frames: int


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._() -]+")


def _safe_name(value: str, fallback: str) -> str:
    cleaned = _UNSAFE_NAME.sub("-", str(value or "")).strip(" .-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or fallback)[:80]


def _next_export_folder(root: Path) -> Path:
    candidate = root / "Logic Export"
    number = 2
    while candidate.exists():
        candidate = root / f"Logic Export {number}"
        number += 1
    return candidate


def _write_aligned_stem(
    source: Path,
    destination: Path,
    *,
    offset_frames: int,
    total_frames: int,
    samplerate: int,
    chunk_frames: int,
) -> int:
    """Render one source file onto the common take timeline."""
    import numpy as np
    import soundfile as sf  # type: ignore

    with sf.SoundFile(str(source)) as reader:
        if int(reader.samplerate) != samplerate:
            raise TakeExportError(
                f"{source.name} is {reader.samplerate} Hz; all tracks must use "
                f"{samplerate} Hz before export."
            )
        channels = max(1, int(reader.channels))
        with sf.SoundFile(
            str(destination),
            mode="w",
            samplerate=samplerate,
            channels=channels,
            format="WAV",
            subtype="PCM_24",
        ) as writer:
            timeline_pos = 0
            if offset_frames > 0:
                remaining = min(offset_frames, total_frames)
                while remaining:
                    count = min(chunk_frames, remaining)
                    writer.write(np.zeros((count, channels), dtype="float32"))
                    timeline_pos += count
                    remaining -= count
            elif offset_frames < 0:
                reader.seek(min(len(reader), -offset_frames))

            while timeline_pos < total_frames:
                count = min(chunk_frames, total_frames - timeline_pos)
                block = reader.read(count, dtype="float32", always_2d=True)
                if block.shape[0] == 0:
                    break
                writer.write(block)
                timeline_pos += int(block.shape[0])

            while timeline_pos < total_frames:
                count = min(chunk_frames, total_frames - timeline_pos)
                writer.write(np.zeros((count, channels), dtype="float32"))
                timeline_pos += count
    os.chmod(destination, 0o600)
    return channels


def _to_stereo(block, pan: float):
    """Apply a DAW-style pan/balance control to a mono or stereo block."""
    value = max(-1.0, min(1.0, float(pan)))
    if block.shape[1] == 1:
        mono = block[:, 0]
        import numpy as np
        return np.column_stack(
            (
                mono * (1.0 - max(0.0, value)),
                mono * (1.0 + min(0.0, value)),
            )
        )
    stereo = block[:, :2].copy()
    if value < 0:
        stereo[:, 1] *= 1.0 + value
    elif value > 0:
        stereo[:, 0] *= 1.0 - value
    return stereo


def _write_rough_mix(
    stems: list[Path],
    destination: Path,
    settings: list[TrackMixSettings],
    *,
    samplerate: int,
    total_frames: int,
    chunk_frames: int,
) -> None:
    import numpy as np
    import soundfile as sf  # type: ignore

    readers = [sf.SoundFile(str(path)) for path in stems]
    try:
        any_solo = any(item.solo for item in settings)
        with sf.SoundFile(
            str(destination),
            mode="w",
            samplerate=samplerate,
            channels=2,
            format="WAV",
            subtype="PCM_24",
        ) as writer:
            remaining = total_frames
            while remaining:
                count = min(chunk_frames, remaining)
                mix = np.zeros((count, 2), dtype="float32")
                for reader, state in zip(readers, settings):
                    block = reader.read(count, dtype="float32", always_2d=True)
                    if block.shape[0] < count:
                        block = np.pad(
                            block,
                            ((0, count - block.shape[0]), (0, 0)),
                        )
                    audible = not state.muted and (state.solo or not any_solo)
                    if audible and state.gain > 0:
                        mix += _to_stereo(block, state.pan) * max(
                            0.0, float(state.gain)
                        )
                np.clip(mix, -1.0, 1.0, out=mix)
                writer.write(mix)
                remaining -= count
    finally:
        for reader in readers:
            reader.close()
    os.chmod(destination, 0o600)


def export_logic_package(
    take: TakeInfo,
    *,
    destination_root: Optional[Path] = None,
    mix_settings: Optional[Mapping[int, TrackMixSettings]] = None,
    chunk_frames: int = 65536,
) -> LogicExportResult:
    """Create an atomic, zero-aligned Logic-ready package for ``take``.

    The package contains numbered 24-bit WAV stems of identical length.  A
    negative WebJam offset trims the local pre-roll; a positive offset becomes
    leading silence.  This lets a musician drag every stem into Logic at
    0:00 without manually interpreting the WebJam manifest.
    """
    if not take.tracks:
        raise TakeExportError("This take has no audio tracks to export.")
    if chunk_frames < 1024:
        raise ValueError("chunk_frames must be at least 1024")

    import soundfile as sf  # type: ignore

    rates: set[int] = set()
    source_info: list[tuple[int, int, int]] = []
    for track in take.tracks:
        try:
            info = sf.info(str(track.path))
        except Exception as exc:  # noqa: BLE001
            raise TakeExportError(
                f"{track.path.name} could not be read: {exc}"
            ) from exc
        rate = int(info.samplerate)
        if rate <= 0 or info.frames <= 0:
            raise TakeExportError(f"{track.path.name} is empty or unreadable.")
        rates.add(rate)
        offset_frames = int(round(float(track.offset_s) * rate))
        source_info.append((offset_frames, int(info.frames), int(info.channels)))
    if len(rates) != 1:
        raise TakeExportError(
            f"The take uses mixed sample rates ({sorted(rates)}); fix that before "
            "creating a Logic export."
        )
    samplerate = rates.pop()
    total_frames = max(
        max(0, offset + frames) for offset, frames, _channels in source_info
    )
    if total_frames <= 0:
        raise TakeExportError("No audio remains on the take timeline after alignment.")

    root = Path(destination_root or (take.path / "Logic Exports")).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    final_folder = _next_export_folder(root)
    temporary = root / f".webjam-export-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)

    settings_map = dict(mix_settings or {})
    stem_paths: list[Path] = []
    applied_settings: list[TrackMixSettings] = []
    evidence: list[dict] = []
    used_names: set[str] = set()
    try:
        for index, (track, source) in enumerate(zip(take.tracks, source_info)):
            offset_frames, _frames, _channels = source
            base = _safe_name(track.name, f"Track {index + 1}")
            candidate = base
            suffix = 2
            while candidate.casefold() in used_names:
                candidate = f"{base} {suffix}"
                suffix += 1
            used_names.add(candidate.casefold())
            output = temporary / f"{index + 1:02d} {candidate}.wav"
            channels = _write_aligned_stem(
                track.path,
                output,
                offset_frames=offset_frames,
                total_frames=total_frames,
                samplerate=samplerate,
                chunk_frames=chunk_frames,
            )
            state = settings_map.get(index, TrackMixSettings())
            stem_paths.append(output)
            applied_settings.append(state)
            evidence.append(
                {
                    "track_number": index + 1,
                    "name": track.name,
                    "source": track.source,
                    "original_filename": track.path.name,
                    "output_filename": output.name,
                    "original_offset_s": round(float(track.offset_s), 6),
                    "sample_rate": samplerate,
                    "channels": channels,
                    "gain": round(max(0.0, float(state.gain)), 4),
                    "pan": round(max(-1.0, min(1.0, float(state.pan))), 4),
                    "muted": bool(state.muted),
                    "solo": bool(state.solo),
                }
            )

        mixdown = temporary / "WebJam Rough Mix.wav"
        _write_rough_mix(
            stem_paths,
            mixdown,
            applied_settings,
            samplerate=samplerate,
            total_frames=total_frames,
            chunk_frames=chunk_frames,
        )

        manifest = temporary / "webjam-logic-export.json"
        payload = {
            "schema_version": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_take": take.path.name,
            "session_title": getattr(take, "session_title", ""),
            "sample_rate": samplerate,
            "bit_depth": 24,
            "timeline_frames": total_frames,
            "timeline_duration_s": round(total_frames / samplerate, 6),
            "all_stems_start_at_zero": True,
            "original_files_modified": False,
            "tracks": evidence,
            "rough_mix": mixdown.name,
        }
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(manifest, 0o600)

        instructions = temporary / "IMPORT INTO LOGIC PRO.md"
        instructions.write_text(
            "# Import this WebJam take into Logic Pro\n\n"
            f"- Sample rate: **{samplerate:,} Hz**\n"
            "- Stem depth: **24-bit PCM WAV**\n"
            "- Alignment: **every numbered stem starts at 0:00 and has the same length**\n\n"
            "1. Create an empty Logic Pro project at the sample rate above.\n"
            "2. Select every numbered WAV stem (01, 02, …) and drag them together "
            "into the empty Tracks area at 0:00.\n"
            "3. Put each file on a new audio track. The files are already padded or "
            "trimmed to WebJam's verified timeline; do not move them independently.\n"
            "4. Use `WebJam Rough Mix.wav` only as a reference, not as another stem.\n"
            "5. Keep `webjam-logic-export.json` with the project as the alignment and "
            "source record.\n\n"
            "The original recorder WAVs remain unchanged in the parent take folder.\n",
            encoding="utf-8",
        )
        os.chmod(instructions, 0o600)
        temporary.rename(final_folder)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return LogicExportResult(
        folder=final_folder,
        stems=tuple(final_folder / path.name for path in stem_paths),
        mixdown=final_folder / mixdown.name,
        manifest=final_folder / manifest.name,
        instructions=final_folder / instructions.name,
        samplerate=samplerate,
        frames=total_frames,
    )
