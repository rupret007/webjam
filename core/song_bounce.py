"""Bounded, transactional offline bounce for standalone song projects.

This module deliberately owns no audio mixing.  Every sample comes from a
schema-3 :class:`~core.studio_renderer.StudioRenderer`, the same authority used
by Reference Studio playback.  Bounce adds only output policy:

* explicit range/cycle and track selection;
* optional processed stereo stems;
* fixed PCM24 WAV/FLAC delivery;
* capability-gated MP3 adapters;
* streaming analysis and exact output checksums; and
* same-directory staging with rollback-safe atomic publication.

Project media is never opened for writing.  Bounce destinations inside the
project bundle are refused so an output name cannot replace source evidence or
project state.
"""

from __future__ import annotations

import errno
import hashlib
import math
import os
import re
import shutil
import stat
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import numpy as np

from core.song_media_catalog import SongMediaCatalog, SongMediaCatalogError
from core.song_project import SongProject
from core.studio_project import (
    MAX_PROJECT_FRAMES,
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    StudioTrack,
    StudioTrackKind,
)
from core.studio_renderer import (
    DEFAULT_RENDER_BLOCK_FRAMES,
    MAX_RENDER_BLOCK_FRAMES,
    StudioRenderer,
    StudioRenderError,
    studio_delivery_block,
)

DEFAULT_BOUNCE_DISK_RESERVE_BYTES = 64 * 1024 * 1024
MAX_BOUNCE_DURATION_SECONDS = 24 * 60 * 60
# Leave conservative space for RIFF headers/chunks below the unsigned 32-bit
# container limit. Longer lossless bounces remain available as FLAC.
MAX_PCM24_WAV_FRAMES = (0xFFFFFFFF - 1024 * 1024) // 6
_HASH_BLOCK_BYTES = 1024 * 1024
_SAFE_STEM_NAME = re.compile(r"[^A-Za-z0-9._() -]+")
_MP3_ADAPTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DENIED_ENCODER_LICENSE = re.compile(
    r"(?:^|[^A-Z])(?:AGPL|GPL|SSPL)(?:V|-)",
)


class SongBounceError(RuntimeError):
    """Raised when a bounce cannot be completed safely."""


class SongBounceCancelled(SongBounceError):
    """Raised after a user cancellation removes all unpublished output."""


class SongBounceStale(SongBounceCancelled):
    """Raised when a newer generation supersedes an older bounce."""


class BounceFormat(str, Enum):
    WAV = "wav"
    FLAC = "flac"
    MP3 = "mp3"


class BounceArtifactKind(str, Enum):
    MIX = "mix"
    STEM = "stem"


class CancellationSignal(Protocol):
    def is_set(self) -> bool:
        """Return true when the caller wants the bounce cancelled."""


@dataclass(frozen=True)
class Mp3EncoderCapability:
    """Truthful result of an adapter's runtime self-test."""

    available: bool
    self_tested: bool
    adapter_id: str = ""
    license_spdx: str = ""
    detail: str = ""


class Mp3EncoderAdapter(Protocol):
    """Optional, caller-supplied MP3 encoder.

    The adapter must write into the already-created regular ``destination``
    inode (not replace it), call ``cancel_check`` during long work, and provide
    a decoder-backed ``verify_output`` check.  WebJam ships no default MP3
    encoder through this module.
    """

    def probe(self) -> Mp3EncoderCapability:
        """Run a bounded runtime self-test and report license/capability."""

    def encode_pcm24_wav(
        self,
        source_wav: Path,
        destination: Path,
        *,
        sample_rate: int,
        channels: int,
        cancel_check: Callable[[], None],
    ) -> None:
        """Encode the PCM24 staging WAV into the reserved destination inode."""

    def verify_output(
        self,
        destination: Path,
        *,
        sample_rate: int,
        channels: int,
        frame_count: int,
    ) -> None:
        """Raise if the encoded output is not a decodable matching MP3."""


@dataclass(frozen=True)
class SongBounceRequest:
    """One immutable offline bounce request.

    ``destination`` names the stereo mix.  When ``create_stems`` is true,
    collision-resistant sibling names are derived from it.  The mix is always
    produced; ``track_ids`` restricts both that mix and its optional stems.
    Stems select AUDIO/BACKING source tracks only.  Each selected contribution
    traverses its normal sends, shared buses, built-in effects, and optional
    master channel with all other source contributions absent; the final
    ``StudioMaster`` gain/limiter is bypassed. BUS/MASTER nodes are therefore
    signal flow, never independently attributable stem candidates.
    """

    destination: Path
    audio_format: BounceFormat = BounceFormat.WAV
    track_ids: tuple[str, ...] | None = None
    include_backing: bool = True
    create_stems: bool = False
    respect_export_included: bool = True
    use_cycle_range: bool = False
    start_frame: int | None = None
    end_frame: int | None = None
    block_frames: int = DEFAULT_RENDER_BLOCK_FRAMES
    replace_existing: bool = True
    disk_reserve_bytes: int = DEFAULT_BOUNCE_DISK_RESERVE_BYTES

    def __post_init__(self) -> None:
        try:
            destination = Path(self.destination).expanduser()
        except (TypeError, ValueError) as exc:
            raise SongBounceError("Bounce destination is invalid.") from exc
        if not destination.name or destination.name in {".", ".."}:
            raise SongBounceError("Bounce destination must name an audio file.")
        object.__setattr__(self, "destination", destination)
        try:
            audio_format = (
                self.audio_format
                if isinstance(self.audio_format, BounceFormat)
                else BounceFormat(str(self.audio_format).lower())
            )
        except ValueError as exc:
            raise SongBounceError("Bounce format must be WAV, FLAC, or MP3.") from exc
        object.__setattr__(self, "audio_format", audio_format)
        expected_suffix = f".{audio_format.value}"
        if destination.suffix.lower() != expected_suffix:
            raise SongBounceError(
                f"{audio_format.value.upper()} bounce requires a "
                f"{expected_suffix} filename."
            )
        for field_name in (
            "include_backing",
            "create_stems",
            "respect_export_included",
            "use_cycle_range",
            "replace_existing",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise SongBounceError(f"{field_name} must be true or false.")
        if self.track_ids is not None:
            if isinstance(self.track_ids, (str, bytes)) or not isinstance(
                self.track_ids, Sequence
            ):
                raise SongBounceError("track_ids must be a sequence of track IDs.")
            normalized = tuple(str(item) for item in self.track_ids)
            if not normalized:
                raise SongBounceError("track_ids must not be empty.")
            if len(normalized) != len(set(normalized)):
                raise SongBounceError("track_ids contains a duplicate ID.")
            object.__setattr__(self, "track_ids", normalized)
        if (self.start_frame is None) != (self.end_frame is None):
            raise SongBounceError(
                "Bounce range requires both start_frame and end_frame."
            )
        if self.use_cycle_range and self.start_frame is not None:
            raise SongBounceError(
                "Choose either the cycle range or an explicit bounce range."
            )
        if self.start_frame is not None:
            start = _strict_int(
                self.start_frame,
                "start_frame",
                minimum=0,
                maximum=MAX_PROJECT_FRAMES,
            )
            end = _strict_int(
                self.end_frame,
                "end_frame",
                minimum=1,
                maximum=MAX_PROJECT_FRAMES,
            )
            if end <= start:
                raise SongBounceError("Bounce end must be later than its start.")
        _strict_int(
            self.block_frames,
            "block_frames",
            minimum=1,
            maximum=MAX_RENDER_BLOCK_FRAMES,
        )
        _strict_int(
            self.disk_reserve_bytes,
            "disk_reserve_bytes",
            minimum=0,
            maximum=(1 << 63) - 1,
        )


@dataclass(frozen=True)
class BounceAnalysis:
    """Deterministic streaming measurements of delivered pre-encode PCM."""

    peak_amplitude: float
    peak_dbfs: float | None
    clipped_sample_count: int
    loudness_dbfs: float | None
    loudness_method: str = "ungated stereo RMS of delivered PCM"


@dataclass(frozen=True)
class BounceArtifact:
    kind: BounceArtifactKind
    path: Path
    sha256: str
    size_bytes: int
    frame_count: int
    track_id: str | None
    track_name: str | None
    analysis: BounceAnalysis


@dataclass(frozen=True)
class SongBounceResult:
    generation: int
    audio_format: BounceFormat
    sample_rate: int
    start_frame: int
    end_frame: int
    selected_track_ids: tuple[str, ...]
    included_backing: bool
    artifacts: tuple[BounceArtifact, ...]
    mp3_encoder_id: str | None = None

    @property
    def mix(self) -> BounceArtifact:
        return self.artifacts[0]

    @property
    def stems(self) -> tuple[BounceArtifact, ...]:
        return tuple(
            item for item in self.artifacts if item.kind is BounceArtifactKind.STEM
        )


@dataclass
class _Stage:
    path: Path
    identity: tuple[int, int, int]
    final_path: Path
    kind: BounceArtifactKind
    track: StudioTrack | None
    analysis: BounceAnalysis | None = None
    checksum: str = ""
    size_bytes: int = 0


@dataclass
class _Published:
    stage: _Stage
    backup_path: Path | None
    backup_identity: tuple[int, int, int] | None


class _AnalysisAccumulator:
    def __init__(self) -> None:
        self.peak = 0.0
        self.clipped = 0
        self.square_sum = 0.0
        self.square_compensation = 0.0
        self.sample_count = 0

    def add(self, block: np.ndarray) -> np.ndarray:
        if (
            not isinstance(block, np.ndarray)
            or block.dtype != np.float32
            or block.ndim != 2
            or block.shape[1] != 2
            or not np.all(np.isfinite(block))
        ):
            raise SongBounceError("Studio renderer returned an invalid audio block.")
        if len(block):
            self.peak = max(self.peak, float(np.max(np.abs(block))))
        try:
            delivered, clipped = studio_delivery_block(block)
        except StudioRenderError as exc:
            raise SongBounceError(
                "Studio audio could not be prepared for fixed-point delivery."
            ) from exc
        self.clipped += clipped
        block_sum = float(
            np.sum(
                np.square(delivered, dtype=np.float64),
                dtype=np.float64,
            )
        )
        # Neumaier summation keeps long exports stable without retaining blocks.
        total = self.square_sum + block_sum
        if abs(self.square_sum) >= abs(block_sum):
            self.square_compensation += (self.square_sum - total) + block_sum
        else:
            self.square_compensation += (block_sum - total) + self.square_sum
        self.square_sum = total
        self.sample_count += int(delivered.size)
        return delivered

    def result(self) -> BounceAnalysis:
        peak_dbfs = (
            20.0 * math.log10(self.peak) if self.peak > 0.0 else None
        )
        energy = self.square_sum + self.square_compensation
        loudness_dbfs = (
            10.0 * math.log10(max(energy, 0.0) / self.sample_count)
            if energy > 0.0 and self.sample_count
            else None
        )
        return BounceAnalysis(
            peak_amplitude=self.peak,
            peak_dbfs=peak_dbfs,
            clipped_sample_count=self.clipped,
            loudness_dbfs=loudness_dbfs,
        )


def _strict_int(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SongBounceError(f"{field_name} must be an integer.")
    if not minimum <= value <= maximum:
        raise SongBounceError(
            f"{field_name} must be between {minimum} and {maximum}."
        )
    return value


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
    )


def _safe_track_name(value: str, fallback: str) -> str:
    cleaned = _SAFE_STEM_NAME.sub("-", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return (cleaned or fallback)[:48]


def _catalog_check(
    renderer: StudioRenderer,
    cancel_check: Callable[[], None],
) -> None:
    catalog = renderer.source_catalog
    if type(catalog) is not SongMediaCatalog:
        raise SongBounceError("Song bounce requires a trusted song media catalog.")
    try:
        catalog.assert_current(cancel_check=cancel_check)
    except SongMediaCatalogError as exc:
        raise SongBounceError(
            "Project media changed while the bounce was running."
        ) from exc


def _destination(request: SongBounceRequest, renderer: StudioRenderer) -> Path:
    value = request.destination
    if not value.is_absolute():
        value = Path.cwd() / value
    try:
        parent = value.parent.resolve(strict=True)
        parent_info = parent.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise SongBounceError("Bounce destination folder is unavailable.") from exc
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise SongBounceError("Bounce destination must be a real folder.")
    destination = parent / value.name
    try:
        destination.relative_to(renderer.take_root)
    except ValueError:
        pass
    else:
        raise SongBounceError(
            "Bounce destination must be outside the song project bundle."
        )
    try:
        existing = destination.lstat()
    except FileNotFoundError:
        return destination
    except OSError as exc:
        raise SongBounceError("Bounce destination could not be inspected.") from exc
    if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
        raise SongBounceError(
            "Bounce destination must not be a link, folder, or special file."
        )
    if not request.replace_existing:
        raise SongBounceError("Bounce destination already exists.")
    return destination


def _selected_tracks(
    renderer: StudioRenderer,
    request: SongBounceRequest,
) -> tuple[StudioTrack, ...]:
    document = renderer.document
    if (
        not isinstance(renderer.project, SongProject)
        or document.schema_version != STUDIO_SONG_PROJECT_SCHEMA_VERSION
        or renderer.project.project_id != document.project_id
    ):
        raise SongBounceError("Bounce requires a schema-3 song project renderer.")
    source_tracks = tuple(
        item
        for item in document.tracks
        if item.kind in {StudioTrackKind.AUDIO, StudioTrackKind.BACKING}
    )
    known = {item.track_id for item in source_tracks}
    if request.track_ids is not None and not set(request.track_ids).issubset(known):
        raise SongBounceError(
            "Bounce selection contains an unknown or non-source track."
        )
    requested = set(request.track_ids) if request.track_ids is not None else known
    selected = tuple(
        track
        for track in sorted(
            source_tracks,
            key=lambda item: (item.order, item.track_id),
        )
        if track.track_id in requested
        and (request.include_backing or track.kind is not StudioTrackKind.BACKING)
        and (
            not request.respect_export_included
            or track.export_included
        )
    )
    if not selected:
        raise SongBounceError("No enabled tracks remain in the bounce selection.")
    return selected


def _range(
    renderer: StudioRenderer,
    request: SongBounceRequest,
) -> tuple[int, int]:
    if request.use_cycle_range:
        cycle = renderer.document.cycle_range
        if cycle is None or not cycle.enabled:
            raise SongBounceError("The project has no enabled cycle range to bounce.")
        start, end = cycle.start_frame, cycle.end_frame
    elif request.start_frame is not None:
        start, end = request.start_frame, request.end_frame
    else:
        start, end = 0, renderer.timeline_end_frame
    assert end is not None
    if end <= start:
        raise SongBounceError("No audio remains in the requested bounce range.")
    maximum = renderer.sample_rate * MAX_BOUNCE_DURATION_SECONDS
    if end - start > maximum:
        raise SongBounceError("Bounce range exceeds the 24-hour safety limit.")
    return start, end


def _stem_paths(
    destination: Path,
    tracks: Sequence[StudioTrack],
) -> tuple[Path, ...]:
    base = _safe_track_name(destination.stem, "Bounce")

    def candidates(identifier_length: int) -> tuple[Path, ...]:
        return tuple(
            destination.with_name(
                f"{base} - Stem {index:02d} - "
                f"{_safe_track_name(track.name, f'Track {index}')} "
                f"[{track.track_id[:identifier_length]}]"
                f"{destination.suffix.lower()}"
            )
            for index, track in enumerate(tracks, start=1)
        )
    result = candidates(12)
    all_paths = (destination, *result)
    if len({item.name.casefold() for item in all_paths}) != len(all_paths):
        result = candidates(36)
        all_paths = (destination, *result)
        if len({item.name.casefold() for item in all_paths}) != len(all_paths):
            raise SongBounceError("Bounce output names are not collision-free.")
    return result


def _reserve_stage(final_path: Path, kind: BounceArtifactKind, track=None) -> tuple[_Stage, int]:
    name = f".webjam-bounce-{uuid.uuid4().hex}{final_path.suffix.lower()}"
    path = final_path.parent / name
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        fchmod = getattr(os, "fchmod", None)
        if callable(fchmod):
            fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or _identity(opened) != _identity(current)
        ):
            raise SongBounceError("Could not reserve a safe bounce staging file.")
        return (
            _Stage(
                path=path,
                identity=_identity(opened),
                final_path=final_path,
                kind=kind,
                track=track,
            ),
            descriptor,
        )
    except SongBounceError:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SongBounceError("Could not reserve a bounce staging file.") from exc


def _require_stage(stage: _Stage) -> os.stat_result:
    try:
        info = stage.path.lstat()
    except OSError as exc:
        raise SongBounceError("A bounce staging file disappeared.") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or _identity(info) != stage.identity
    ):
        raise SongBounceError("A bounce staging file changed unexpectedly.")
    return info


def _write_pcm24(
    descriptor: int,
    stage: _Stage,
    renderer: StudioRenderer,
    *,
    start_frame: int,
    end_frame: int,
    block_frames: int,
    file_format: BounceFormat,
    check: Callable[[], None],
) -> BounceAnalysis:
    try:
        import soundfile as sf  # type: ignore
    except ImportError as exc:  # pragma: no cover - packaged dependency
        raise SongBounceError("PCM24 audio export support is unavailable.") from exc
    accumulator = _AnalysisAccumulator()
    writer = None
    written = 0
    try:
        writer = sf.SoundFile(
            descriptor,
            mode="w",
            samplerate=renderer.sample_rate,
            channels=2,
            format=file_format.value.upper(),
            subtype="PCM_24",
            closefd=False,
        )
        for block in renderer.iter_blocks(
            start_frame=start_frame,
            end_frame=end_frame,
            block_frames=block_frames,
        ):
            check()
            if len(block) > block_frames:
                raise SongBounceError("Studio renderer exceeded the bounce block limit.")
            delivered = accumulator.add(block)
            writer.write(delivered)
            written += len(delivered)
            check()
        writer.close()
        writer = None
        if written != end_frame - start_frame:
            raise SongBounceError("Bounce output ended before the requested range.")
        os.fsync(descriptor)
        _require_stage(stage)
        return accumulator.result()
    except (SongBounceError, SongBounceCancelled, SongBounceStale):
        raise
    except StudioRenderError as exc:
        raise SongBounceError(
            "The Studio arrangement could not be rendered safely."
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise SongBounceError("Could not write the bounce audio file.") from exc
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass


def _verify_pcm24(
    stage: _Stage,
    *,
    audio_format: BounceFormat,
    sample_rate: int,
    frame_count: int,
) -> None:
    try:
        import soundfile as sf  # type: ignore

        info = sf.info(stage.path)
    except (ImportError, OSError, RuntimeError) as exc:
        raise SongBounceError("Bounce output could not be decoded for verification.") from exc
    expected_format = audio_format.value.upper()
    if (
        info.format != expected_format
        or info.subtype != "PCM_24"
        or info.samplerate != sample_rate
        or info.channels != 2
        or info.frames != frame_count
    ):
        raise SongBounceError("Bounce output failed its audio identity check.")


def _hash_stage(stage: _Stage, check: Callable[[], None]) -> tuple[str, int]:
    digest = hashlib.sha256()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        _require_stage(stage)
        descriptor = os.open(stage.path, flags)
        if _identity(os.fstat(descriptor)) != stage.identity:
            raise SongBounceError("A bounce staging file changed unexpectedly.")
        while True:
            check()
            block = os.read(descriptor, _HASH_BLOCK_BYTES)
            if not block:
                break
            digest.update(block)
        final = os.fstat(descriptor)
        if _identity(final) != stage.identity or final.st_size <= 0:
            raise SongBounceError("Bounce output is empty or changed unexpectedly.")
        _require_stage(stage)
        return digest.hexdigest(), int(final.st_size)
    except SongBounceError:
        raise
    except OSError as exc:
        raise SongBounceError("Bounce output could not be verified.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _adapter_capability(
    adapter: Mp3EncoderAdapter | None,
) -> Mp3EncoderCapability:
    if adapter is None:
        return Mp3EncoderCapability(
            available=False,
            self_tested=False,
            detail="No tested MP3 encoder is installed.",
        )
    try:
        capability = adapter.probe()
    except Exception as exc:
        raise SongBounceError("The MP3 encoder self-test failed.") from exc
    if not isinstance(capability, Mp3EncoderCapability):
        raise SongBounceError("The MP3 encoder returned an invalid capability result.")
    for value in (
        capability.available,
        capability.self_tested,
    ):
        if not isinstance(value, bool):
            raise SongBounceError(
                "The MP3 encoder returned an invalid capability result."
            )
    for value in (
        capability.adapter_id,
        capability.license_spdx,
        capability.detail,
    ):
        if (
            not isinstance(value, str)
            or len(value) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise SongBounceError(
                "The MP3 encoder returned an invalid capability result."
            )
    if capability.available:
        if (
            not capability.self_tested
            or not _MP3_ADAPTER_ID.fullmatch(capability.adapter_id)
            or not capability.license_spdx.strip()
            or len(capability.license_spdx) > 256
            or "/" in capability.license_spdx
            or "\\" in capability.license_spdx
        ):
            raise SongBounceError(
                "The MP3 encoder is not a fully identified tested capability."
            )
        upper_license = capability.license_spdx.upper()
        if _DENIED_ENCODER_LICENSE.search(upper_license):
            raise SongBounceError(
                "The available MP3 encoder uses a disallowed license."
            )
    return capability


def mp3_bounce_capability(
    adapter: Mp3EncoderAdapter | None = None,
) -> Mp3EncoderCapability:
    """Return the tested MP3 capability; unavailable is the safe default."""

    return _adapter_capability(adapter)


def _fsync_parent(parent: Path) -> None:
    if os.name == "nt":
        # Windows does not provide a portable directory handle that fsync()
        # accepts. Every staged file is flushed before ReplaceFile semantics;
        # same-directory os.replace() remains the publication boundary.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = -1
    try:
        descriptor = os.open(parent, flags)
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in {
            errno.EBADF,
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        }:
            raise SongBounceError(
                "Bounce destination directory could not be synchronized."
            ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _regular_file_identity(path: Path) -> tuple[int, int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SongBounceError("An existing bounce output changed unexpectedly.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SongBounceError("An existing bounce output is not a regular file.")
    return _identity(info)


def _backup_existing(path: Path) -> tuple[Path | None, tuple[int, int, int] | None]:
    try:
        path.lstat()
    except FileNotFoundError:
        return None, None
    identity = _regular_file_identity(path)
    backup = path.parent / f".webjam-bounce-backup-{uuid.uuid4().hex}"
    try:
        os.link(path, backup, follow_symlinks=False)
        if (
            _regular_file_identity(path) != identity
            or _regular_file_identity(backup) != identity
        ):
            raise SongBounceError("An existing bounce output changed unexpectedly.")
        return backup, identity
    except SongBounceError:
        try:
            backup.unlink()
        except OSError:
            pass
        raise
    except (OSError, NotImplementedError, TypeError) as exc:
        raise SongBounceError(
            "Could not prepare the existing bounce output for atomic replacement."
        ) from exc


def _restore_publications(publications: Sequence[_Published]) -> None:
    failure = False
    for item in reversed(publications):
        try:
            if item.backup_path is None:
                item.stage.final_path.unlink(missing_ok=True)
            else:
                os.replace(item.backup_path, item.stage.final_path)
                if (
                    item.backup_identity is not None
                    and _regular_file_identity(item.stage.final_path)
                    != item.backup_identity
                ):
                    failure = True
        except (OSError, SongBounceError):
            failure = True
    if failure:
        raise SongBounceError(
            "Bounce publication failed and an earlier output could not be restored."
        )


def _publish(stages: Sequence[_Stage], check: Callable[[], None]) -> None:
    publications: list[_Published] = []
    committed = False
    try:
        for stage in stages:
            check()
            _require_stage(stage)
            backup, backup_identity = _backup_existing(stage.final_path)
            publication = _Published(stage, backup, backup_identity)
            try:
                os.replace(stage.path, stage.final_path)
            except OSError as exc:
                if backup is not None:
                    backup.unlink(missing_ok=True)
                raise SongBounceError(
                    "Bounce output could not be published atomically."
                ) from exc
            publications.append(publication)
            try:
                if (
                    _regular_file_identity(stage.final_path) != stage.identity
                    or _hash_regular(stage.final_path, check) != stage.checksum
                ):
                    raise SongBounceError(
                        "Published bounce output failed verification."
                    )
                check()
            except Exception:
                _restore_publications(publications)
                publications.clear()
                raise
        check()
        _fsync_parent(stages[0].final_path.parent)
        committed = True
        for item in publications:
            if item.backup_path is not None:
                try:
                    item.backup_path.unlink()
                except OSError:
                    # The verified outputs are already durable. A failed unlink
                    # may leave a private hard-link backup, but must not turn a
                    # successful publication into a false failure after the
                    # rollback evidence has begun to be retired.
                    pass
        try:
            _fsync_parent(stages[0].final_path.parent)
        except SongBounceError:
            # As above, publication is already durable; this sync covers only
            # cleanup of rollback hard links.
            pass
    except Exception:
        if publications and not committed:
            _restore_publications(publications)
        raise


def _hash_regular(
    path: Path,
    check: Callable[[], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    descriptor = -1
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        expected = _regular_file_identity(path)
        descriptor = os.open(path, flags)
        if _identity(os.fstat(descriptor)) != expected:
            raise SongBounceError("Published bounce output changed unexpectedly.")
        while True:
            if check is not None:
                check()
            block = os.read(descriptor, _HASH_BLOCK_BYTES)
            if not block:
                break
            digest.update(block)
        if _identity(os.fstat(descriptor)) != expected:
            raise SongBounceError("Published bounce output changed unexpectedly.")
        return digest.hexdigest()
    except SongBounceError:
        raise
    except OSError as exc:
        raise SongBounceError("Published bounce output could not be verified.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class SongBounceEngine:
    """Synchronous bounce worker with generation-based stale-task rejection.

    Call :meth:`begin` before dispatching :meth:`bounce` to a worker. Starting
    another generation immediately makes the older worker stale at its next
    bounded cancellation checkpoint.
    """

    def __init__(self, *, mp3_encoder: Mp3EncoderAdapter | None = None) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._mp3_encoder = mp3_encoder

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def begin(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def cancel(self, generation: int | None = None) -> int:
        with self._lock:
            if generation is None or generation == self._generation:
                self._generation += 1
            return self._generation

    def _check(
        self,
        generation: int,
        cancel_event: CancellationSignal | None,
    ) -> None:
        with self._lock:
            current = self._generation
        if generation != current:
            raise SongBounceStale("A newer bounce superseded this bounce.")
        if cancel_event is None:
            return
        checker = getattr(cancel_event, "is_set", None)
        if not callable(checker):
            raise SongBounceError("cancel_event must provide is_set().")
        try:
            cancelled = checker()
        except Exception as exc:
            raise SongBounceError(
                "Could not read the bounce cancellation state."
            ) from exc
        if not isinstance(cancelled, bool):
            raise SongBounceError(
                "cancel_event.is_set() must return true or false."
            )
        if cancelled:
            raise SongBounceCancelled("Bounce was cancelled.")

    def bounce(
        self,
        renderer: StudioRenderer,
        request: SongBounceRequest,
        *,
        generation: int,
        cancel_event: CancellationSignal | None = None,
    ) -> SongBounceResult:
        """Render, verify, and atomically publish one song bounce."""

        if not isinstance(renderer, StudioRenderer):
            raise SongBounceError("Bounce requires a prepared StudioRenderer.")
        if not isinstance(request, SongBounceRequest):
            raise SongBounceError("Bounce request is invalid.")
        token = _strict_int(
            generation,
            "generation",
            minimum=1,
            maximum=(1 << 63) - 1,
        )

        def check() -> None:
            self._check(token, cancel_event)

        check()
        destination = _destination(request, renderer)
        selected = _selected_tracks(renderer, request)
        selected_ids = tuple(item.track_id for item in selected)
        try:
            mix_renderer = StudioRenderer(
                renderer.project,
                renderer.document,
                renderer.take_root,
                block_frames=request.block_frames,
                track_ids=selected_ids,
                respect_export_included=False,
                apply_master=True,
                source_catalog=renderer.source_catalog,
            )
        except StudioRenderError as exc:
            raise SongBounceError(
                "The selected Studio arrangement failed bounce preflight."
            ) from exc
        start_frame, end_frame = _range(mix_renderer, request)
        frames = end_frame - start_frame
        if (
            request.audio_format is BounceFormat.WAV
            and frames > MAX_PCM24_WAV_FRAMES
        ):
            raise SongBounceError(
                "This PCM24 WAV range exceeds the RIFF size limit; use FLAC."
            )
        final_paths = [destination]
        if request.create_stems:
            final_paths.extend(_stem_paths(destination, selected))
        for path in final_paths:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SongBounceError(
                    "A bounce output could not be inspected."
                ) from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise SongBounceError(
                    "A bounce output must not be a link, folder, or special file."
                )
            if not request.replace_existing:
                raise SongBounceError("A bounce output already exists.")

        output_count = len(final_paths)
        pcm_bytes = frames * 2 * 3
        if request.audio_format is BounceFormat.WAV:
            per_output_bytes = pcm_bytes + 1024 * 1024
        elif request.audio_format is BounceFormat.FLAC:
            per_output_bytes = pcm_bytes + max(1024 * 1024, pcm_bytes // 16)
        else:
            # One PCM24 intermediate plus a conservative encoded-output bound.
            per_output_bytes = pcm_bytes * 2 + 2 * 1024 * 1024
        estimated = per_output_bytes * output_count
        try:
            free = shutil.disk_usage(destination.parent).free
        except OSError as exc:
            raise SongBounceError(
                "Bounce destination free space could not be checked."
            ) from exc
        if free < estimated + request.disk_reserve_bytes:
            raise SongBounceError("Not enough free space for this bounce.")

        capability: Mp3EncoderCapability | None = None
        if request.audio_format is BounceFormat.MP3:
            capability = _adapter_capability(self._mp3_encoder)
            if not capability.available:
                raise SongBounceError("No tested MP3 encoder is available.")

        stages: list[_Stage] = []
        intermediate_paths: list[Path] = []
        try:
            _catalog_check(mix_renderer, check)
            try:
                # iter_blocks() remains the single render path. Validate its
                # immutable source receipts first so multi-gigabyte checksum
                # work also observes generation/user cancellation.
                mix_renderer.validate_media(check)
            except StudioRenderError as exc:
                raise SongBounceError(
                    "Project media failed bounce validation."
                ) from exc
            render_plans: list[
                tuple[Path, BounceArtifactKind, StudioTrack | None, StudioRenderer]
            ] = [(destination, BounceArtifactKind.MIX, None, mix_renderer)]
            if request.create_stems:
                for path, track in zip(final_paths[1:], selected, strict=True):
                    stem_renderer = StudioRenderer(
                        renderer.project,
                        renderer.document,
                        renderer.take_root,
                        block_frames=request.block_frames,
                        track_ids=(track.track_id,),
                        respect_export_included=False,
                        apply_master=False,
                        source_catalog=renderer.source_catalog,
                    )
                    render_plans.append(
                        (path, BounceArtifactKind.STEM, track, stem_renderer)
                    )

            for final_path, kind, track, planned_renderer in render_plans:
                check()
                stage, descriptor = _reserve_stage(final_path, kind, track)
                stages.append(stage)
                if planned_renderer is not mix_renderer:
                    try:
                        planned_renderer.reuse_media_validation(
                            mix_renderer,
                            cancel_check=check,
                        )
                    except StudioRenderError as exc:
                        raise SongBounceError(
                            "Project media changed between bounce passes."
                        ) from exc
                try:
                    if request.audio_format is BounceFormat.MP3:
                        intermediate, intermediate_fd = _reserve_stage(
                            final_path.with_suffix(".wav"),
                            kind,
                            track,
                        )
                        intermediate_paths.append(intermediate.path)
                        try:
                            analysis = _write_pcm24(
                                intermediate_fd,
                                intermediate,
                                planned_renderer,
                                start_frame=start_frame,
                                end_frame=end_frame,
                                block_frames=request.block_frames,
                                file_format=BounceFormat.WAV,
                                check=check,
                            )
                        finally:
                            os.close(intermediate_fd)
                        _verify_pcm24(
                            intermediate,
                            audio_format=BounceFormat.WAV,
                            sample_rate=renderer.sample_rate,
                            frame_count=frames,
                        )
                        os.close(descriptor)
                        descriptor = -1
                        assert self._mp3_encoder is not None
                        try:
                            self._mp3_encoder.encode_pcm24_wav(
                                intermediate.path,
                                stage.path,
                                sample_rate=renderer.sample_rate,
                                channels=2,
                                cancel_check=check,
                            )
                            check()
                            _require_stage(stage)
                            self._mp3_encoder.verify_output(
                                stage.path,
                                sample_rate=renderer.sample_rate,
                                channels=2,
                                frame_count=frames,
                            )
                        except (
                            SongBounceError,
                            SongBounceCancelled,
                            SongBounceStale,
                        ):
                            raise
                        except Exception as exc:
                            raise SongBounceError(
                                "The MP3 encoder failed output verification."
                            ) from exc
                    else:
                        analysis = _write_pcm24(
                            descriptor,
                            stage,
                            planned_renderer,
                            start_frame=start_frame,
                            end_frame=end_frame,
                            block_frames=request.block_frames,
                            file_format=request.audio_format,
                            check=check,
                        )
                        os.close(descriptor)
                        descriptor = -1
                        _verify_pcm24(
                            stage,
                            audio_format=request.audio_format,
                            sample_rate=renderer.sample_rate,
                            frame_count=frames,
                        )
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
                stage.analysis = analysis
                stage.checksum, stage.size_bytes = _hash_stage(stage, check)

            _catalog_check(mix_renderer, check)
            check()
            _publish(stages, check)
            artifacts = tuple(
                BounceArtifact(
                    kind=stage.kind,
                    path=stage.final_path,
                    sha256=stage.checksum,
                    size_bytes=stage.size_bytes,
                    frame_count=frames,
                    track_id=stage.track.track_id if stage.track is not None else None,
                    track_name=stage.track.name if stage.track is not None else None,
                    analysis=stage.analysis
                    if stage.analysis is not None
                    else _AnalysisAccumulator().result(),
                )
                for stage in stages
            )
            return SongBounceResult(
                generation=token,
                audio_format=request.audio_format,
                sample_rate=renderer.sample_rate,
                start_frame=start_frame,
                end_frame=end_frame,
                selected_track_ids=selected_ids,
                included_backing=any(
                    item.kind is StudioTrackKind.BACKING for item in selected
                ),
                artifacts=artifacts,
                mp3_encoder_id=(
                    capability.adapter_id if capability is not None else None
                ),
            )
        except (SongBounceError, SongBounceCancelled, SongBounceStale):
            raise
        except StudioRenderError as exc:
            raise SongBounceError(
                "The Studio arrangement could not be rendered safely."
            ) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise SongBounceError("Bounce failed before publication.") from exc
        finally:
            for path in (*intermediate_paths, *(item.path for item in stages)):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass


__all__ = [
    "DEFAULT_BOUNCE_DISK_RESERVE_BYTES",
    "MAX_BOUNCE_DURATION_SECONDS",
    "MAX_PCM24_WAV_FRAMES",
    "BounceAnalysis",
    "BounceArtifact",
    "BounceArtifactKind",
    "BounceFormat",
    "CancellationSignal",
    "Mp3EncoderAdapter",
    "Mp3EncoderCapability",
    "SongBounceCancelled",
    "SongBounceEngine",
    "SongBounceError",
    "SongBounceRequest",
    "SongBounceResult",
    "SongBounceStale",
    "mp3_bounce_capability",
]
