"""Bounded waveform tiles for the Studio arrangement viewport.

Waveforms are a disposable view cache, never project truth.  A tile is keyed
by immutable catalog identity, an exact half-open source-frame range, and its
frames-per-peak resolution.  Source paths are deliberately excluded from the
key: paths are mutable locations while ``source_id`` and the declared media
facts identify the recording evidence.

The loader never materializes a complete source.  Catalog-backed paths are
walked relative to a trusted take-directory descriptor without following any
symlink component.  Declared checksums are verified in bounded chunks once per
exact physical receipt, and waveform reduction reads at most
``read_chunk_frames`` samples at a time into immutable float32 minimum/maximum
arrays.  Declared capture gaps are zeroed before reduction.

Viewport work is generation scoped.  Starting a new generation makes older
tokens stale, and cancellation is checked around every bounded read and again
before a computed tile can enter the shared LRU.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import os
import re
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import numpy as np

from core.song_media_catalog import (
    SongCatalogSource,
    SongMediaCatalog,
    SongMediaCatalogError,
)
from core.studio_source_catalog import (
    StudioCatalogSource,
    StudioSourceCatalog,
    StudioSourceCatalogError,
)
from core.take_project import MediaSegment


DEFAULT_WAVEFORM_TILE_PEAKS: Final = 512
DEFAULT_WAVEFORM_READ_CHUNK_FRAMES: Final = 4_096
DEFAULT_WAVEFORM_CACHE_ENTRIES: Final = 256
DEFAULT_WAVEFORM_CACHE_BYTES: Final = 32 * 1024 * 1024
DEFAULT_WAVEFORM_MAX_VIEWPORT_TILES: Final = 64
MAX_WAVEFORM_TILE_PEAKS: Final = 16_384
MAX_WAVEFORM_READ_CHUNK_FRAMES: Final = 1_048_576
MAX_WAVEFORM_CHANNELS: Final = 32
MAX_WAVEFORM_GAPS: Final = 65_536
MAX_WAVEFORM_VIEWPORT_TILES: Final = 512
_CHECKSUM_READ_BYTES: Final = 1_048_576
_GAP_CANCEL_INTERVAL: Final = 128

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WAV_FORMATS = frozenset({"WAV", "WAVEX", "RF64"})


class StudioWaveformError(RuntimeError):
    """Raised when a waveform cannot be produced from trusted bounded input."""


class StudioWaveformCancelled(StudioWaveformError):
    """Raised when viewport work belongs to a cancelled or stale generation."""


def _positive_int(value: object, field_name: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StudioWaveformError(f"{field_name} must be a positive integer.")
    if maximum is not None and value > maximum:
        raise StudioWaveformError(f"{field_name} must be no greater than {maximum}.")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StudioWaveformError(f"{field_name} must be a non-negative integer.")
    return value


@dataclass(frozen=True, order=True)
class WaveformGap:
    """A declared half-open source interval that must display as silence.

    An empty ``channels`` tuple means every channel.  Otherwise only the
    zero-based channels listed by the catalog are silent in this interval.
    """

    start_frame: int
    frame_count: int
    channels: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        start = _nonnegative_int(self.start_frame, "gap.start_frame")
        count = _positive_int(self.frame_count, "gap.frame_count")
        raw_channels = tuple(self.channels)
        channels = tuple(
            _nonnegative_int(item, "gap.channels") for item in raw_channels
        )
        if len(channels) != len(set(channels)):
            raise StudioWaveformError("gap.channels contains a duplicate channel.")
        object.__setattr__(self, "start_frame", start)
        object.__setattr__(self, "frame_count", count)
        object.__setattr__(self, "channels", tuple(sorted(channels)))

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count


@dataclass(frozen=True)
class WaveformSourceIdentity:
    """Path-free immutable facts used in every waveform tile key."""

    source_id: str
    catalog_key: tuple[str, str, str]
    frame_count: int
    sample_rate: int
    channels: int
    sample_format: str
    size_bytes: int
    sha256: str
    gap_signature: str


def _gap_signature(gaps: tuple[WaveformGap, ...]) -> str:
    digest = hashlib.sha256()
    for gap in gaps:
        digest.update(str(gap.start_frame).encode("ascii"))
        digest.update(b":")
        digest.update(str(gap.frame_count).encode("ascii"))
        digest.update(b":")
        digest.update(",".join(str(item) for item in gap.channels).encode("ascii"))
        digest.update(b";")
    return digest.hexdigest()


def _directory_identity(info: os.stat_result) -> tuple[int, ...]:
    """Return a stable directory identity without invalidating on child writes."""

    device = int(getattr(info, "st_dev", 0))
    inode = int(getattr(info, "st_ino", 0))
    file_type = int(stat.S_IFMT(info.st_mode))
    if inode:
        return (device, inode, file_type)
    return (
        device,
        inode,
        file_type,
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _trusted_directory_snapshot(
    value: str | Path,
) -> tuple[Path, tuple[int, ...]]:
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError) as exc:
        raise StudioWaveformError(
            "Waveform trusted root must be a filesystem path."
        ) from exc
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        before = path.lstat()
    except OSError as exc:
        raise StudioWaveformError("Waveform trusted root is missing.") from exc
    if stat.S_ISLNK(before.st_mode):
        raise StudioWaveformError("Waveform trusted root must not be a symbolic link.")
    if not stat.S_ISDIR(before.st_mode):
        raise StudioWaveformError("Waveform trusted root must be a directory.")
    try:
        resolved = path.resolve(strict=True)
        current = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise StudioWaveformError("Waveform trusted root could not be opened.") from exc
    if not stat.S_ISDIR(current.st_mode) or _directory_identity(
        before
    ) != _directory_identity(current):
        raise StudioWaveformError(
            "Waveform trusted root changed while it was being opened."
        )
    return resolved, _directory_identity(current)


def _inspect_trusted_ancestors(
    root: Path,
    root_identity: tuple[int, ...],
    relative_parts: tuple[str, ...],
) -> tuple[tuple[Path, tuple[int, ...]], ...]:
    """Reject a changed root or any symlink/non-directory source ancestor."""

    try:
        root_info = root.lstat()
    except OSError as exc:
        raise StudioWaveformError("Waveform trusted root changed.") from exc
    if (
        stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or _directory_identity(root_info) != root_identity
    ):
        raise StudioWaveformError("Waveform trusted root changed.")

    receipts: list[tuple[Path, tuple[int, ...]]] = []
    current = root
    for part in relative_parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise StudioWaveformError(
                "Waveform source ancestor is missing or changed."
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise StudioWaveformError(
                "Waveform source ancestors must not be symbolic links."
            )
        if not stat.S_ISDIR(info.st_mode):
            raise StudioWaveformError("Waveform source ancestors must be directories.")
        receipts.append((current, _directory_identity(info)))
    return tuple(receipts)


@dataclass(frozen=True)
class WaveformSource:
    """One immutable catalog source and its current filesystem location."""

    source_id: str
    path: Path
    frame_count: int
    sample_rate: int
    channels: int
    sample_format: str = ""
    size_bytes: int = 0
    sha256: str = ""
    gaps: tuple[WaveformGap, ...] = ()
    catalog_key: tuple[str, str, str] = ()
    trusted_root: Path | None = field(default=None, repr=False, compare=False)
    identity: WaveformSourceIdentity = field(init=False)
    _trusted_root_identity: tuple[int, ...] | None = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )
    _relative_parts: tuple[str, ...] = field(
        init=False,
        default=(),
        repr=False,
        compare=False,
    )
    _gap_starts: tuple[int, ...] = field(
        init=False,
        default=(),
        repr=False,
        compare=False,
    )
    _gap_prefix_max_ends: tuple[int, ...] = field(
        init=False,
        default=(),
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        source_id = str(self.source_id or "").strip()
        try:
            source_id_bytes = source_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise StudioWaveformError("source_id must be valid UTF-8 text.") from exc
        if not source_id or len(source_id_bytes) > 512:
            raise StudioWaveformError("source_id must be 1 to 512 UTF-8 bytes.")
        try:
            path = Path(self.path).expanduser()
        except (TypeError, ValueError) as exc:
            raise StudioWaveformError("source.path must be a filesystem path.") from exc
        if not path.is_absolute():
            raise StudioWaveformError("Waveform source paths must be absolute.")
        frame_count = _positive_int(self.frame_count, "source.frame_count")
        sample_rate = _positive_int(self.sample_rate, "source.sample_rate")
        channels = _positive_int(
            self.channels,
            "source.channels",
            maximum=MAX_WAVEFORM_CHANNELS,
        )
        sample_format = str(self.sample_format or "").strip().upper()
        if len(sample_format) > 40:
            raise StudioWaveformError("source.sample_format is too long.")
        size_bytes = _nonnegative_int(self.size_bytes, "source.size_bytes")
        checksum = str(self.sha256 or "").strip().lower()
        if checksum and not _SHA256_RE.fullmatch(checksum):
            raise StudioWaveformError(
                "source.sha256 must be a lowercase SHA-256 digest."
            )

        raw_catalog_key = tuple(self.catalog_key)
        if raw_catalog_key:
            if len(raw_catalog_key) != 3:
                raise StudioWaveformError(
                    "source.catalog_key must contain take, track, and segment IDs."
                )
            catalog_key = tuple(str(item or "").strip() for item in raw_catalog_key)
            if any(not item or len(item.encode("utf-8")) > 512 for item in catalog_key):
                raise StudioWaveformError(
                    "source.catalog_key contains an invalid durable ID."
                )
        else:
            catalog_key = ()

        raw_gap_values: list[object] = []
        try:
            for gap in self.gaps:
                if len(raw_gap_values) >= MAX_WAVEFORM_GAPS:
                    raise StudioWaveformError(
                        f"source.gaps supports at most {MAX_WAVEFORM_GAPS} intervals."
                    )
                raw_gap_values.append(gap)
        except TypeError as exc:
            raise StudioWaveformError(
                "source.gaps must be an iterable of WaveformGap values."
            ) from exc
        raw_gaps = tuple(raw_gap_values)
        for gap in raw_gaps:
            if not isinstance(gap, WaveformGap):
                raise StudioWaveformError(
                    "source.gaps must contain WaveformGap values."
                )
        gaps = tuple(sorted(raw_gaps))
        for gap in gaps:
            if gap.end_frame > frame_count:
                raise StudioWaveformError("A waveform gap extends beyond its source.")
            if any(channel >= channels for channel in gap.channels):
                raise StudioWaveformError(
                    "A waveform gap references an unavailable channel."
                )

        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "frame_count", frame_count)
        object.__setattr__(self, "sample_rate", sample_rate)
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "sample_format", sample_format)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(self, "sha256", checksum)
        object.__setattr__(self, "gaps", gaps)
        object.__setattr__(self, "catalog_key", catalog_key)
        object.__setattr__(
            self, "_gap_starts", tuple(item.start_frame for item in gaps)
        )
        prefix_max_ends: list[int] = []
        maximum_end = 0
        for gap in gaps:
            maximum_end = max(maximum_end, gap.end_frame)
            prefix_max_ends.append(maximum_end)
        object.__setattr__(self, "_gap_prefix_max_ends", tuple(prefix_max_ends))

        if self.trusted_root is not None:
            root, root_identity = _trusted_directory_snapshot(self.trusted_root)
            try:
                relative = path.relative_to(root)
            except ValueError as exc:
                raise StudioWaveformError(
                    "Waveform source path escapes its trusted take folder."
                ) from exc
            parts = tuple(relative.parts)
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise StudioWaveformError(
                    "Waveform source path escapes its trusted take folder."
                )
            _inspect_trusted_ancestors(root, root_identity, parts)
            object.__setattr__(self, "path", root.joinpath(*parts))
            object.__setattr__(self, "trusted_root", root)
            object.__setattr__(self, "_trusted_root_identity", root_identity)
            object.__setattr__(self, "_relative_parts", parts)
        else:
            object.__setattr__(self, "trusted_root", None)
        object.__setattr__(
            self,
            "identity",
            WaveformSourceIdentity(
                source_id=source_id,
                catalog_key=catalog_key,
                frame_count=frame_count,
                sample_rate=sample_rate,
                channels=channels,
                sample_format=sample_format,
                size_bytes=size_bytes,
                sha256=checksum,
                gap_signature=_gap_signature(gaps),
            ),
        )

    @classmethod
    def from_media_segment(
        cls,
        take_root: str | Path,
        segment: MediaSegment,
    ) -> "WaveformSource":
        """Build a source without resolving away the media file component."""

        if not isinstance(segment, MediaSegment):
            raise StudioWaveformError("segment must be a MediaSegment.")
        root, _root_identity = _trusted_directory_snapshot(take_root)
        candidate = root / segment.path
        gaps = tuple(
            WaveformGap(
                start_frame=item.start_frame,
                frame_count=item.frame_count,
                channels=tuple(item.channels),
            )
            for item in segment.gaps
        )
        return cls(
            source_id=segment.segment_id,
            path=candidate,
            frame_count=segment.frame_count,
            sample_rate=segment.sample_rate,
            channels=segment.channels,
            sample_format=segment.sample_format,
            size_bytes=segment.size_bytes,
            sha256=segment.sha256,
            gaps=gaps,
            trusted_root=root,
        )

    @classmethod
    def from_catalog_source(
        cls,
        catalog: StudioSourceCatalog,
        take_id: str,
        track_id: str,
        segment_id: str,
    ) -> "WaveformSource":
        """Bind one waveform source to a trusted repeated-take catalog key."""

        if type(catalog) is not StudioSourceCatalog:
            raise StudioWaveformError(
                "Waveform catalog sources require a trusted StudioSourceCatalog."
            )
        try:
            catalog.assert_current()
            catalog_source = catalog.resolve(take_id, track_id, segment_id)
        except StudioSourceCatalogError as exc:
            raise StudioWaveformError(str(exc)) from exc
        return cls._from_catalog_entry(catalog_source)

    @classmethod
    def _from_catalog_entry(
        cls,
        catalog_source: StudioCatalogSource,
    ) -> "WaveformSource":
        """Build from an entry after its owning catalog was checked once.

        This is an internal batch-activation hook. Public callers use
        :meth:`from_catalog_source`, which validates the catalog before
        resolving the entry.
        """

        if type(catalog_source) is not StudioCatalogSource:
            raise StudioWaveformError(
                "Waveform catalog entry must come from a trusted source catalog."
            )
        segment = catalog_source.segment
        gaps = tuple(
            WaveformGap(
                start_frame=item.start_frame,
                frame_count=item.frame_count,
                channels=tuple(item.channels),
            )
            for item in segment.gaps
        )
        return cls(
            source_id=segment.segment_id,
            path=catalog_source.take_root / segment.path,
            frame_count=segment.frame_count,
            sample_rate=segment.sample_rate,
            channels=segment.channels,
            sample_format=segment.sample_format,
            size_bytes=segment.size_bytes,
            sha256=segment.sha256,
            gaps=gaps,
            catalog_key=catalog_source.key,
            trusted_root=catalog_source.take_root,
        )

    @classmethod
    def from_song_catalog_source(
        cls,
        catalog: SongMediaCatalog,
        media_id: str,
    ) -> "WaveformSource":
        """Bind one waveform source to a sealed standalone project catalog."""

        if type(catalog) is not SongMediaCatalog:
            raise StudioWaveformError(
                "Song waveform sources require a trusted SongMediaCatalog."
            )
        try:
            catalog.assert_current()
            catalog_source = catalog.resolve(media_id)
        except SongMediaCatalogError as exc:
            raise StudioWaveformError(str(exc)) from exc
        return cls._from_song_catalog_entry(catalog_source)

    @classmethod
    def _from_song_catalog_entry(
        cls,
        catalog_source: SongCatalogSource,
    ) -> "WaveformSource":
        """Build from one entry after its song catalog was checked once."""

        if type(catalog_source) is not SongCatalogSource:
            raise StudioWaveformError(
                "Song waveform entry must come from a trusted project catalog."
            )
        media = catalog_source.media
        try:
            path = catalog_source.path
        except SongMediaCatalogError as exc:
            raise StudioWaveformError(str(exc)) from exc
        return cls(
            source_id=media.media_id,
            path=path,
            frame_count=media.frame_count,
            sample_rate=media.sample_rate,
            channels=media.channels,
            # SongMedia schema 1 records the container (WAV/AIFF/FLAC), not
            # libsndfile's sample subtype. Leave subtype validation unset
            # while checksum, size, rate, channels, and frames remain exact.
            sample_format="",
            size_bytes=media.size_bytes,
            sha256=media.sha256,
            gaps=(),
            catalog_key=(catalog_source.project_id, "media", media.media_id),
            trusted_root=catalog_source.bundle_root,
        )


@dataclass(frozen=True)
class WaveformTileKey:
    """Exact cache identity for one reduced source interval."""

    source: WaveformSourceIdentity
    start_frame: int
    frame_count: int
    frames_per_peak: int


@dataclass(frozen=True, eq=False)
class WaveformTile:
    """Immutable minimum/maximum envelopes shaped ``(peaks, channels)``."""

    key: WaveformTileKey
    minimum: np.ndarray
    maximum: np.ndarray

    @property
    def peak_count(self) -> int:
        return int(self.minimum.shape[0])

    @property
    def byte_size(self) -> int:
        return int(self.minimum.nbytes + self.maximum.nbytes)


@dataclass(frozen=True)
class WaveformCacheStats:
    """A stable snapshot of cache bounds and bounded-I/O instrumentation."""

    entries: int
    bytes: int
    max_entries: int
    max_bytes: int
    max_viewport_tiles: int
    hits: int
    misses: int
    evictions: int
    source_opens: int
    read_calls: int
    total_frames_read: int
    max_read_frames: int
    generation: int


@dataclass(frozen=True)
class _PhysicalFingerprint:
    device: int
    inode: int
    file_type: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _CacheEntry:
    tile: WaveformTile
    fingerprint: _PhysicalFingerprint


def _physical_fingerprint(info: os.stat_result) -> _PhysicalFingerprint:
    return _PhysicalFingerprint(
        device=int(getattr(info, "st_dev", 0)),
        inode=int(getattr(info, "st_ino", 0)),
        file_type=int(stat.S_IFMT(info.st_mode)),
        size=int(info.st_size),
        modified_ns=int(getattr(info, "st_mtime_ns", 0)),
        changed_ns=int(getattr(info, "st_ctime_ns", 0)),
    )


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    left_id = (int(getattr(left, "st_dev", 0)), int(getattr(left, "st_ino", 0)))
    right_id = (
        int(getattr(right, "st_dev", 0)),
        int(getattr(right, "st_ino", 0)),
    )
    if left_id[1] or right_id[1]:
        return left_id == right_id
    return _physical_fingerprint(left) == _physical_fingerprint(right)


def _inspect_regular_path(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise StudioWaveformError("Waveform source media is missing.") from exc
    except OSError as exc:
        raise StudioWaveformError(
            "Waveform source media could not be inspected."
        ) from exc
    if stat.S_ISLNK(info.st_mode):
        raise StudioWaveformError("Waveform source media must not be a symbolic link.")
    if not stat.S_ISREG(info.st_mode):
        raise StudioWaveformError("Waveform source media must be a regular file.")
    return info


def _open_unrooted_regular_source(path: Path) -> tuple[int, os.stat_result]:
    before = _inspect_regular_path(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        try:
            current = path.lstat()
        except OSError:
            current = None
        if current is not None and stat.S_ISLNK(current.st_mode):
            raise StudioWaveformError(
                "Waveform source media must not be a symbolic link."
            ) from exc
        raise StudioWaveformError("Waveform source media could not be opened.") from exc
    try:
        opened = os.fstat(descriptor)
        current = _inspect_regular_path(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_inode(before, opened)
            or not _same_inode(current, opened)
        ):
            raise StudioWaveformError(
                "Waveform source media changed while it was being opened."
            )
        return descriptor, opened
    except Exception:
        os.close(descriptor)
        raise


def _open_rooted_source_once(source: WaveformSource) -> tuple[int, os.stat_result]:
    root = source.trusted_root
    root_identity = source._trusted_root_identity
    parts = source._relative_parts
    if root is None or root_identity is None or not parts:
        raise StudioWaveformError("Waveform trusted source location is invalid.")

    # This preflight also supplies a portable fallback on platforms whose
    # ``os.open`` cannot walk relative to an already-open directory descriptor.
    before_ancestors = _inspect_trusted_ancestors(root, root_identity, parts)
    supports_dir_fd = os.open in getattr(os, "supports_dir_fd", ())
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if not supports_dir_fd or not directory_flag:
        descriptor, opened = _open_unrooted_regular_source(source.path)
        try:
            after_ancestors = _inspect_trusted_ancestors(root, root_identity, parts)
            if before_ancestors != after_ancestors:
                raise StudioWaveformError(
                    "Waveform source ancestors changed while media was opened."
                )
            return descriptor, opened
        except Exception:
            os.close(descriptor)
            raise

    directory_flags = (
        os.O_RDONLY
        | directory_flag
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_descriptors: list[int] = []
    descriptor = -1
    try:
        root_descriptor = os.open(root, directory_flags)
        directory_descriptors.append(root_descriptor)
        root_info = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or _directory_identity(root_info) != root_identity
        ):
            raise StudioWaveformError("Waveform trusted root changed.")

        parent_descriptor = root_descriptor
        for index, part in enumerate(parts[:-1]):
            next_descriptor = os.open(
                part,
                directory_flags,
                dir_fd=parent_descriptor,
            )
            directory_descriptors.append(next_descriptor)
            info = os.fstat(next_descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or _directory_identity(info) != before_ancestors[index][1]
            ):
                raise StudioWaveformError(
                    "Waveform source ancestors changed while media was opened."
                )
            parent_descriptor = next_descriptor

        descriptor = os.open(parts[-1], file_flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise StudioWaveformError("Waveform source media must be a regular file.")
        after_ancestors = _inspect_trusted_ancestors(root, root_identity, parts)
        if before_ancestors != after_ancestors:
            raise StudioWaveformError(
                "Waveform source ancestors changed while media was opened."
            )
        return descriptor, opened
    except StudioWaveformError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        # Existing links are named explicitly; racing replacements fail closed
        # under this broader message even when the platform reports only ELOOP.
        try:
            _inspect_trusted_ancestors(root, root_identity, parts)
        except StudioWaveformError as trusted_exc:
            raise trusted_exc from exc
        raise StudioWaveformError(
            "Waveform source media or an ancestor changed while it was opened."
        ) from exc
    finally:
        for directory_descriptor in reversed(directory_descriptors):
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def _open_regular_source(source: WaveformSource) -> tuple[int, os.stat_result]:
    if source.trusted_root is None:
        return _open_unrooted_regular_source(source.path)

    descriptor, opened = _open_rooted_source_once(source)
    probe_descriptor = -1
    try:
        probe_descriptor, current = _open_rooted_source_once(source)
        if not _same_inode(opened, current) or _physical_fingerprint(
            opened
        ) != _physical_fingerprint(current):
            raise StudioWaveformError(
                "Waveform source media changed while it was being opened."
            )
        return descriptor, opened
    except Exception:
        os.close(descriptor)
        raise
    finally:
        if probe_descriptor >= 0:
            os.close(probe_descriptor)


def _require_source_current(
    source: WaveformSource,
    descriptor: int,
    fingerprint: _PhysicalFingerprint,
) -> None:
    probe_descriptor = -1
    try:
        opened = os.fstat(descriptor)
        probe_descriptor, current = _open_regular_source(source)
    except OSError as exc:
        raise StudioWaveformError(
            "Waveform source media changed while it was being read."
        ) from exc
    try:
        if (
            _physical_fingerprint(opened) != fingerprint
            or _physical_fingerprint(current) != fingerprint
            or not _same_inode(current, opened)
        ):
            raise StudioWaveformError(
                "Waveform source media changed while it was being read."
            )
    finally:
        if probe_descriptor >= 0:
            os.close(probe_descriptor)


def _sha256_descriptor(
    descriptor: int,
    token: "WaveformRequestToken",
) -> str:
    digest = hashlib.sha256()
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            token.raise_if_cancelled()
            block = os.read(descriptor, _CHECKSUM_READ_BYTES)
            if not block:
                break
            digest.update(block)
        token.raise_if_cancelled()
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise StudioWaveformError(
            "Waveform source checksum could not be verified."
        ) from exc
    return digest.hexdigest()


class WaveformRequestToken:
    """Cancellation handle for exactly one cache generation."""

    def __init__(
        self,
        owner: "WaveformTileCache",
        generation: int,
        event: threading.Event,
    ) -> None:
        self._owner = owner
        self._event = event
        self.generation = generation

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or not self._owner.is_current(self)

    def cancel(self) -> None:
        self._owner._cancel_token(self)

    def raise_if_cancelled(self) -> None:
        self._owner._require_current(self)


class WaveformTileCache:
    """Thread-safe byte- and entry-bounded LRU for viewport waveform tiles."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_WAVEFORM_CACHE_ENTRIES,
        max_bytes: int = DEFAULT_WAVEFORM_CACHE_BYTES,
        read_chunk_frames: int = DEFAULT_WAVEFORM_READ_CHUNK_FRAMES,
        tile_peaks: int = DEFAULT_WAVEFORM_TILE_PEAKS,
        max_viewport_tiles: int = DEFAULT_WAVEFORM_MAX_VIEWPORT_TILES,
    ) -> None:
        self.max_entries = _positive_int(max_entries, "max_entries")
        self.max_bytes = _positive_int(max_bytes, "max_bytes")
        self.read_chunk_frames = _positive_int(
            read_chunk_frames,
            "read_chunk_frames",
            maximum=MAX_WAVEFORM_READ_CHUNK_FRAMES,
        )
        self.tile_peaks = _positive_int(
            tile_peaks,
            "tile_peaks",
            maximum=MAX_WAVEFORM_TILE_PEAKS,
        )
        self.max_viewport_tiles = _positive_int(
            max_viewport_tiles,
            "max_viewport_tiles",
            maximum=MAX_WAVEFORM_VIEWPORT_TILES,
        )
        self._lock = threading.RLock()
        self._entries: OrderedDict[WaveformTileKey, _CacheEntry] = OrderedDict()
        self._source_receipts: OrderedDict[
            tuple[WaveformSourceIdentity, _PhysicalFingerprint], str
        ] = OrderedDict()
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._source_opens = 0
        self._read_calls = 0
        self._total_frames_read = 0
        self._max_read_frames = 0
        self._generation = 0
        self._generation_event = threading.Event()
        self._generation_event.set()

    def begin_generation(self) -> WaveformRequestToken:
        """Cancel the previous viewport generation and return its successor."""

        with self._lock:
            self._generation_event.set()
            self._generation += 1
            self._generation_event = threading.Event()
            return WaveformRequestToken(
                self,
                self._generation,
                self._generation_event,
            )

    def is_current(self, token: WaveformRequestToken) -> bool:
        with self._lock:
            return (
                isinstance(token, WaveformRequestToken)
                and token._owner is self
                and token.generation == self._generation
                and token._event is self._generation_event
                and not token._event.is_set()
            )

    def _cancel_token(self, token: WaveformRequestToken) -> None:
        with self._lock:
            if token._owner is self:
                token._event.set()

    def _require_current(self, token: WaveformRequestToken) -> None:
        if not self.is_current(token):
            raise StudioWaveformCancelled(
                "Waveform request was cancelled or belongs to a stale generation."
            )

    def cancel_current(self) -> None:
        """Cancel current work without making a replacement generation."""

        with self._lock:
            self._generation_event.set()

    def clear(self) -> None:
        """Cancel in-flight work and remove every disposable cached tile."""

        with self._lock:
            self._generation_event.set()
            self._generation += 1
            self._generation_event = threading.Event()
            self._generation_event.set()
            self._entries.clear()
            self._source_receipts.clear()
            self._bytes = 0

    @property
    def stats(self) -> WaveformCacheStats:
        with self._lock:
            return WaveformCacheStats(
                entries=len(self._entries),
                bytes=self._bytes,
                max_entries=self.max_entries,
                max_bytes=self.max_bytes,
                max_viewport_tiles=self.max_viewport_tiles,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                source_opens=self._source_opens,
                read_calls=self._read_calls,
                total_frames_read=self._total_frames_read,
                max_read_frames=self._max_read_frames,
                generation=self._generation,
            )

    def _record_source_open(self) -> None:
        with self._lock:
            self._source_opens += 1

    def _record_read(self, frames: int) -> None:
        """Record a bounded read; kept separate for deterministic cancellation tests."""

        with self._lock:
            self._read_calls += 1
            self._total_frames_read += frames
            self._max_read_frames = max(self._max_read_frames, frames)

    def _remove_entry(self, key: WaveformTileKey) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.tile.byte_size

    def _validate_source_checksum(
        self,
        source: WaveformSource,
        descriptor: int,
        fingerprint: _PhysicalFingerprint,
        token: WaveformRequestToken,
    ) -> None:
        if not source.sha256:
            return
        receipt_key = (source.identity, fingerprint)
        with self._lock:
            self._require_current(token)
            cached = self._source_receipts.get(receipt_key)
            if cached is not None:
                self._source_receipts.move_to_end(receipt_key)
        if cached is None:
            digest = _sha256_descriptor(descriptor, token)
            _require_source_current(source, descriptor, fingerprint)
            if digest != source.sha256:
                raise StudioWaveformError("Waveform source media checksum changed.")
            with self._lock:
                self._require_current(token)
                self._source_receipts[receipt_key] = digest
                self._source_receipts.move_to_end(receipt_key)
                while len(self._source_receipts) > self.max_entries:
                    self._source_receipts.popitem(last=False)
                self._require_current(token)
            return
        if cached != source.sha256:
            raise StudioWaveformError("Waveform source media checksum changed.")

    def _lookup(
        self,
        source: WaveformSource,
        key: WaveformTileKey,
        token: WaveformRequestToken,
    ) -> WaveformTile | None:
        with self._lock:
            self._require_current(token)
            entry = self._entries.get(key)
        if entry is None:
            with self._lock:
                self._misses += 1
            return None

        # A pathname stat leaves a swap window before returning cached evidence.
        # Bind every hit check to an exact descriptor and re-open the trusted
        # root-relative publication before the cached tile can escape.
        descriptor = -1
        try:
            descriptor, current = _open_regular_source(source)
            fingerprint = _physical_fingerprint(current)
            if source.size_bytes and current.st_size != source.size_bytes:
                with self._lock:
                    self._remove_entry(key)
                raise StudioWaveformError("Waveform source media changed size.")
            _require_source_current(source, descriptor, fingerprint)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        with self._lock:
            self._require_current(token)
            current_entry = self._entries.get(key)
            if current_entry is not entry:
                self._misses += 1
                return None
            if fingerprint != entry.fingerprint:
                self._remove_entry(key)
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry.tile

    def _insert(
        self,
        tile: WaveformTile,
        fingerprint: _PhysicalFingerprint,
        token: WaveformRequestToken,
    ) -> None:
        with self._lock:
            self._require_current(token)
            if tile.byte_size > self.max_bytes:
                return
            self._remove_entry(tile.key)
            self._entries[tile.key] = _CacheEntry(tile, fingerprint)
            self._bytes += tile.byte_size
            while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                _old_key, old_entry = self._entries.popitem(last=False)
                self._bytes -= old_entry.tile.byte_size
                self._evictions += 1
            try:
                self._require_current(token)
            except StudioWaveformCancelled:
                current = self._entries.get(tile.key)
                if current is not None and current.tile is tile:
                    self._remove_entry(tile.key)
                raise

    def request_tile(
        self,
        source: WaveformSource,
        *,
        start_frame: int,
        frame_count: int,
        frames_per_peak: int,
        token: WaveformRequestToken,
    ) -> WaveformTile:
        """Return one exact tile, reducing it in bounded chunks on a miss."""

        self._require_current(token)
        if not isinstance(source, WaveformSource):
            raise StudioWaveformError("source must be a WaveformSource.")
        start = _nonnegative_int(start_frame, "start_frame")
        count = _positive_int(frame_count, "frame_count")
        resolution = _positive_int(frames_per_peak, "frames_per_peak")
        if start >= source.frame_count or count > source.frame_count - start:
            raise StudioWaveformError("Waveform tile extends beyond its source.")
        peak_count = math.ceil(count / resolution)
        if peak_count > MAX_WAVEFORM_TILE_PEAKS:
            raise StudioWaveformError(
                "Waveform tile exceeds the bounded peak-count limit."
            )
        key = WaveformTileKey(source.identity, start, count, resolution)
        cached = self._lookup(source, key, token)
        if cached is not None:
            return cached
        tile, fingerprint = self._load_tile(source, key, peak_count, token)
        self._insert(tile, fingerprint, token)
        return tile

    def plan_viewport(
        self,
        source: WaveformSource,
        *,
        start_frame: int,
        frame_count: int,
        frames_per_peak: int,
        token: WaveformRequestToken,
        tile_peaks: int | None = None,
    ) -> tuple[WaveformTileKey, ...]:
        """Plan a hard-bounded fixed grid without opening source media.

        Async UI owners can submit the returned keys one at a time through
        :meth:`request_tile`, coalesce identical in-flight keys, and paint each
        completed tile progressively without ever doing filesystem I/O in a
        Qt paint event.
        """

        self._require_current(token)
        if not isinstance(source, WaveformSource):
            raise StudioWaveformError("source must be a WaveformSource.")
        start = _nonnegative_int(start_frame, "start_frame")
        count = _positive_int(frame_count, "frame_count")
        resolution = _positive_int(frames_per_peak, "frames_per_peak")
        peaks = (
            self.tile_peaks
            if tile_peaks is None
            else _positive_int(
                tile_peaks,
                "tile_peaks",
                maximum=MAX_WAVEFORM_TILE_PEAKS,
            )
        )
        if start >= source.frame_count:
            return ()
        end = min(source.frame_count, start + count)
        tile_span = resolution * peaks
        tile_start = (start // tile_span) * tile_span
        required_tiles = ((end - 1 - tile_start) // tile_span) + 1
        if required_tiles > self.max_viewport_tiles:
            raise StudioWaveformError(
                "Waveform viewport exceeds the bounded tile-count limit."
            )
        result: list[WaveformTileKey] = []
        while tile_start < end:
            self._require_current(token)
            tile_count = min(tile_span, source.frame_count - tile_start)
            result.append(
                WaveformTileKey(
                    source=source.identity,
                    start_frame=tile_start,
                    frame_count=tile_count,
                    frames_per_peak=resolution,
                )
            )
            tile_start += tile_span
        self._require_current(token)
        return tuple(result)

    def request_viewport(
        self,
        source: WaveformSource,
        *,
        start_frame: int,
        frame_count: int,
        frames_per_peak: int,
        token: WaveformRequestToken,
        tile_peaks: int | None = None,
    ) -> tuple[WaveformTile, ...]:
        """Return fixed-grid tiles intersecting one half-open source viewport."""

        keys = self.plan_viewport(
            source,
            start_frame=start_frame,
            frame_count=frame_count,
            frames_per_peak=frames_per_peak,
            token=token,
            tile_peaks=tile_peaks,
        )
        result: list[WaveformTile] = []
        for key in keys:
            self._require_current(token)
            result.append(
                self.request_tile(
                    source,
                    start_frame=key.start_frame,
                    frame_count=key.frame_count,
                    frames_per_peak=key.frames_per_peak,
                    token=token,
                )
            )
        self._require_current(token)
        return tuple(result)

    def _load_tile(
        self,
        source: WaveformSource,
        key: WaveformTileKey,
        peak_count: int,
        token: WaveformRequestToken,
    ) -> tuple[WaveformTile, _PhysicalFingerprint]:
        descriptor = -1
        reader = None
        try:
            self._require_current(token)
            descriptor, info = _open_regular_source(source)
            fingerprint = _physical_fingerprint(info)
            if source.size_bytes and info.st_size != source.size_bytes:
                raise StudioWaveformError("Waveform source media changed size.")
            self._validate_source_checksum(
                source,
                descriptor,
                fingerprint,
                token,
            )
            try:
                import soundfile as sf  # type: ignore

                reader = sf.SoundFile(descriptor, mode="r", closefd=False)
            except Exception as exc:
                raise StudioWaveformError(
                    "Waveform source media is not a readable WAV file."
                ) from exc
            self._record_source_open()
            observed = (
                int(reader.samplerate),
                int(reader.channels),
                int(len(reader)),
            )
            declared = (source.sample_rate, source.channels, source.frame_count)
            if observed != declared:
                raise StudioWaveformError(
                    "Waveform source facts do not match the immutable catalog."
                )
            if str(reader.format).upper() not in _WAV_FORMATS:
                raise StudioWaveformError("Waveform source media must be a WAV file.")
            if source.sample_format and (
                str(reader.subtype).upper() != source.sample_format
            ):
                raise StudioWaveformError(
                    "Waveform source sample format changed from the catalog."
                )

            minimum = np.full(
                (peak_count, source.channels),
                np.inf,
                dtype=np.float32,
            )
            maximum = np.full(
                (peak_count, source.channels),
                -np.inf,
                dtype=np.float32,
            )
            reader.seek(key.start_frame)
            remaining = key.frame_count
            absolute = key.start_frame
            next_gap_index, active_gaps = self._initial_gap_cursor(
                source,
                key.start_frame,
                token,
            )
            while remaining:
                self._require_current(token)
                requested = min(self.read_chunk_frames, remaining)
                try:
                    block = reader.read(
                        requested,
                        dtype="float32",
                        always_2d=True,
                    )
                except Exception as exc:
                    raise StudioWaveformError(
                        "Waveform source media could not be read."
                    ) from exc
                frames = int(block.shape[0])
                self._record_read(frames)
                self._require_current(token)
                if frames != requested or block.shape != (frames, source.channels):
                    raise StudioWaveformError(
                        "Waveform source ended before its declared frame count."
                    )
                if block.dtype != np.float32 or not np.all(np.isfinite(block)):
                    raise StudioWaveformError(
                        "Waveform source contains invalid non-finite samples."
                    )
                next_gap_index, active_gaps = self._apply_gaps(
                    source,
                    absolute,
                    block,
                    next_gap_index=next_gap_index,
                    active_gaps=active_gaps,
                    token=token,
                )
                self._reduce_block(
                    block,
                    local_start=absolute - key.start_frame,
                    frames_per_peak=key.frames_per_peak,
                    minimum=minimum,
                    maximum=maximum,
                )
                absolute += frames
                remaining -= frames

            _require_source_current(source, descriptor, fingerprint)
            self._require_current(token)
            untouched = ~np.isfinite(minimum) | ~np.isfinite(maximum)
            minimum[untouched] = 0.0
            maximum[untouched] = 0.0
            # Arrays that own their allocation can have write access re-enabled
            # by a caller.  Bytes-backed views keep shared cached envelopes
            # structurally immutable for the lifetime of a tile.
            minimum = np.frombuffer(minimum.tobytes(), dtype=np.float32).reshape(
                minimum.shape
            )
            maximum = np.frombuffer(maximum.tobytes(), dtype=np.float32).reshape(
                maximum.shape
            )
            return WaveformTile(key, minimum, maximum), fingerprint
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @staticmethod
    def _initial_gap_cursor(
        source: WaveformSource,
        absolute_start: int,
        token: WaveformRequestToken,
    ) -> tuple[int, list[WaveformGap]]:
        """Seed a monotonic overlap cursor for a tile that starts mid-source."""

        if not source.gaps:
            return 0, []
        next_index = bisect.bisect_left(source._gap_starts, absolute_start)
        first_possible = bisect.bisect_right(
            source._gap_prefix_max_ends,
            absolute_start,
            hi=next_index,
        )
        active: list[WaveformGap] = []
        for offset, gap in enumerate(source.gaps[first_possible:next_index]):
            if offset % _GAP_CANCEL_INTERVAL == 0:
                token.raise_if_cancelled()
            if gap.end_frame > absolute_start:
                active.append(gap)
        token.raise_if_cancelled()
        return next_index, active

    @staticmethod
    def _apply_gaps(
        source: WaveformSource,
        absolute_start: int,
        block: np.ndarray,
        *,
        next_gap_index: int,
        active_gaps: list[WaveformGap],
        token: WaveformRequestToken,
    ) -> tuple[int, list[WaveformGap]]:
        """Zero overlaps and advance without rescanning already-ended gaps."""

        absolute_end = absolute_start + int(block.shape[0])

        def apply(gap: WaveformGap) -> None:
            local_start = max(0, gap.start_frame - absolute_start)
            local_end = min(block.shape[0], gap.end_frame - absolute_start)
            if gap.channels:
                block[local_start:local_end, gap.channels] = 0.0
            else:
                block[local_start:local_end, :] = 0.0

        surviving: list[WaveformGap] = []
        for index, gap in enumerate(active_gaps):
            if index % _GAP_CANCEL_INTERVAL == 0:
                token.raise_if_cancelled()
            if gap.end_frame <= absolute_start:
                continue
            apply(gap)
            surviving.append(gap)

        added = 0
        while (
            next_gap_index < len(source.gaps)
            and source.gaps[next_gap_index].start_frame < absolute_end
        ):
            if added % _GAP_CANCEL_INTERVAL == 0:
                token.raise_if_cancelled()
            gap = source.gaps[next_gap_index]
            next_gap_index += 1
            added += 1
            if gap.end_frame <= absolute_start:
                continue
            apply(gap)
            surviving.append(gap)
        token.raise_if_cancelled()
        return next_gap_index, surviving

    @staticmethod
    def _reduce_block(
        block: np.ndarray,
        *,
        local_start: int,
        frames_per_peak: int,
        minimum: np.ndarray,
        maximum: np.ndarray,
    ) -> None:
        offset = 0
        frame_count = int(block.shape[0])
        while offset < frame_count:
            local_frame = local_start + offset
            bucket = local_frame // frames_per_peak
            bucket_remaining = frames_per_peak - (local_frame % frames_per_peak)
            section_end = min(frame_count, offset + bucket_remaining)
            section = block[offset:section_end]
            minimum[bucket] = np.minimum(minimum[bucket], section.min(axis=0))
            maximum[bucket] = np.maximum(maximum[bucket], section.max(axis=0))
            offset = section_end


__all__ = [
    "DEFAULT_WAVEFORM_CACHE_BYTES",
    "DEFAULT_WAVEFORM_CACHE_ENTRIES",
    "DEFAULT_WAVEFORM_MAX_VIEWPORT_TILES",
    "DEFAULT_WAVEFORM_READ_CHUNK_FRAMES",
    "DEFAULT_WAVEFORM_TILE_PEAKS",
    "MAX_WAVEFORM_CHANNELS",
    "MAX_WAVEFORM_GAPS",
    "MAX_WAVEFORM_READ_CHUNK_FRAMES",
    "MAX_WAVEFORM_TILE_PEAKS",
    "MAX_WAVEFORM_VIEWPORT_TILES",
    "StudioWaveformCancelled",
    "StudioWaveformError",
    "WaveformCacheStats",
    "WaveformGap",
    "WaveformRequestToken",
    "WaveformSource",
    "WaveformSourceIdentity",
    "WaveformTile",
    "WaveformTileCache",
    "WaveformTileKey",
]
