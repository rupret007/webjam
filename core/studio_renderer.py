"""Deterministic, streaming rendering for Studio arrangements.

``TakeProject``/``StudioSourceCatalog`` and standalone
``SongProject``/``SongMediaCatalog`` pairs are immutable source inventories;
``StudioDocument`` is the non-destructive edit and mix list.  This module is
the boundary where those forms of truth become audio. It deliberately has no
playback-device or export-file policy: both callers consume the same
:class:`StudioRenderStream` blocks.

The renderer resolves every active region through durable take, track, and
segment/media IDs before opening media.  Source files are opened read-only, checked
against their declared facts (and checksum when one is present), and sampled
in bounded blocks.  No operation rewrites a recorder file or materializes a
whole song in memory. Schema-3 automation, buses, sends, built-in DSP, and
master routing share this exact stream for playback and bounce.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import os
import stat
import warnings
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from typing_extensions import Self

from core.song_media_catalog import (
    SongMediaCatalog,
    SongMediaCatalogError,
)
from core.song_project import SongMedia, SongProject
from core.studio_mixer import (
    StudioMixEngine,
    StudioMixerError,
    StudioMixResult,
    studio_effect_tail_frames,
    studio_mixer_capability,
)
from core.studio_project import (
    MAX_PROJECT_FRAMES,
    STUDIO_PROJECT_SCHEMA_VERSION,
    STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    FadeCurve,
    StudioCompRange,
    StudioCrossfade,
    StudioDocument,
    StudioMaster,
    StudioRegion,
    StudioTakeLane,
    StudioTrack,
    StudioTrackKind,
)
from core.studio_source_catalog import (
    StudioSourceCatalog,
    StudioSourceCatalogError,
    StudioSourceKey,
)
from core.take_project import (
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    TakeProject,
    TakeProjectError,
)

DEFAULT_RENDER_BLOCK_FRAMES = 4_096
MAX_RENDER_BLOCK_FRAMES = 1_048_576
MAX_OPEN_SOURCE_READERS = 32
_HASH_BLOCK_BYTES = 1_048_576
_USABLE_MEDIA = frozenset({MediaStatus.AVAILABLE, MediaStatus.RECOVERED})
_USABLE_SOURCE_PROJECTS = frozenset({ProjectStatus.COMPLETE, ProjectStatus.RECOVERED})


class StudioRenderError(RuntimeError):
    """Raised when an arrangement cannot be rendered without inventing audio."""


def _integer_frame(value: object, field_name: str, *, signed: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudioRenderError(f"{field_name} must be an integer frame.")
    minimum = -MAX_PROJECT_FRAMES if signed else 0
    if value < minimum or value > MAX_PROJECT_FRAMES:
        raise StudioRenderError(
            f"{field_name} must be between {minimum} and {MAX_PROJECT_FRAMES}."
        )
    return value


def _block_count(value: object, field_name: str) -> int:
    result = _integer_frame(value, field_name, signed=False)
    if result <= 0 or result > MAX_RENDER_BLOCK_FRAMES:
        raise StudioRenderError(
            f"{field_name} must be between 1 and {MAX_RENDER_BLOCK_FRAMES}."
        )
    return result


def _source_fingerprint(info: os.stat_result) -> tuple[int, ...]:
    """Return the stable identity and mutation facts for one open source."""

    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _same_source_inode(left: os.stat_result, right: os.stat_result) -> bool:
    """Compare inode identity with a conservative fallback for weak platforms."""

    left_identity = (
        int(getattr(left, "st_dev", 0)),
        int(getattr(left, "st_ino", 0)),
    )
    right_identity = (
        int(getattr(right, "st_dev", 0)),
        int(getattr(right, "st_ino", 0)),
    )
    if left_identity[1] or right_identity[1]:
        return left_identity == right_identity
    return _source_fingerprint(left) == _source_fingerprint(right)


def _open_unrooted_regular_source(path: Path) -> tuple[int, os.stat_result]:
    """Open one exact regular source without following its final component."""

    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise StudioRenderError("Studio source media is missing.") from exc
    except OSError as exc:
        raise StudioRenderError("Studio source media could not be inspected.") from exc
    if stat.S_ISLNK(before.st_mode):
        raise StudioRenderError("Studio source media must not be a symbolic link.")
    if not stat.S_ISREG(before.st_mode):
        raise StudioRenderError("Studio source media must be a regular file.")

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
        except FileNotFoundError:
            current = None
        except OSError:
            current = None
        if current is not None and stat.S_ISLNK(current.st_mode):
            raise StudioRenderError(
                "Studio source media must not be a symbolic link."
            ) from exc
        raise StudioRenderError("Studio source media could not be opened.") from exc

    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise StudioRenderError("Studio source media must be a regular file.")
        try:
            current = path.lstat()
        except OSError as exc:
            raise StudioRenderError(
                "Studio source media changed while it was being opened."
            ) from exc
        if (
            stat.S_ISLNK(current.st_mode)
            or not _same_source_inode(before, info)
            or not _same_source_inode(current, info)
        ):
            raise StudioRenderError(
                "Studio source media changed while it was being opened."
            )
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(stat.S_IFMT(info.st_mode)),
    )


_SECURE_DIRFD_AVAILABLE = bool(
    os.open in getattr(os, "supports_dir_fd", ())
    and os.stat in getattr(os, "supports_dir_fd", ())
    and os.stat in getattr(os, "supports_follow_symlinks", ())
    and getattr(os, "O_NOFOLLOW", 0)
    and getattr(os, "O_DIRECTORY", 0)
)


@dataclass
class _BoundSource:
    """One source descriptor plus bindings for its published relative path."""

    path: Path
    descriptor: int
    info: os.stat_result
    take_root: Path
    root_identity: tuple[int, int, int]
    relative_parts: tuple[str, ...]
    directory_descriptors: tuple[int, ...] = ()
    portable_directory_identities: tuple[tuple[int, int, int], ...] = ()

    @property
    def uses_dirfd(self) -> bool:
        return bool(self.directory_descriptors)

    def close(self) -> None:
        if self.descriptor >= 0:
            try:
                os.close(self.descriptor)
            except OSError:
                pass
            self.descriptor = -1
        for descriptor in reversed(self.directory_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.directory_descriptors = ()


def _relative_source_parts(relative_path: str) -> tuple[str, ...]:
    parts = tuple(str(relative_path).split("/"))
    if not parts or any(
        part in {"", ".", ".."} or "\\" in part or "\x00" in part for part in parts
    ):
        raise StudioRenderError("Studio source media path is not a safe relative path.")
    return parts


def _require_directory(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StudioRenderError(
            "Studio source media path contains a symbolic link or non-directory."
        )


def _require_regular(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise StudioRenderError(
            "Studio source media must be a regular file, not a symbolic link."
        )


def _open_bound_root(
    take_root: Path,
    expected_identity: tuple[int, int, int] | None,
) -> tuple[int, os.stat_result]:
    try:
        before = take_root.lstat()
        _require_directory(before)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(take_root, flags)
    except StudioRenderError:
        raise
    except OSError as exc:
        raise StudioRenderError(
            "Studio source take root is missing or contains a symbolic link."
        ) from exc
    try:
        opened = os.fstat(descriptor)
        current = take_root.lstat()
        _require_directory(opened)
        _require_directory(current)
        identity = _directory_identity(opened)
        if (
            _directory_identity(before) != identity
            or _directory_identity(current) != identity
            or (expected_identity is not None and expected_identity != identity)
        ):
            raise StudioRenderError(
                "Studio source take root changed while it was being opened."
            )
        return descriptor, opened
    except Exception:
        os.close(descriptor)
        raise


def _open_bound_source_dirfd(
    take_root: Path,
    relative_parts: tuple[str, ...],
    expected_root_identity: tuple[int, int, int] | None,
) -> _BoundSource:
    directory_descriptors: list[int] = []
    source_descriptor = -1
    try:
        root_descriptor, root_info = _open_bound_root(
            take_root,
            expected_root_identity,
        )
        directory_descriptors.append(root_descriptor)
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for part in relative_parts[:-1]:
            parent_descriptor = directory_descriptors[-1]
            published = os.stat(
                part,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            _require_directory(published)
            child_descriptor = os.open(
                part,
                directory_flags,
                dir_fd=parent_descriptor,
            )
            try:
                child_info = os.fstat(child_descriptor)
                _require_directory(child_info)
                current = os.stat(
                    part,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                _require_directory(current)
                if not _same_source_inode(
                    published, child_info
                ) or not _same_source_inode(current, child_info):
                    raise StudioRenderError(
                        "Studio source media directory changed while it was opened."
                    )
            except Exception:
                os.close(child_descriptor)
                raise
            directory_descriptors.append(child_descriptor)

        final_name = relative_parts[-1]
        parent_descriptor = directory_descriptors[-1]
        before = os.stat(
            final_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        _require_regular(before)
        source_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        source_descriptor = os.open(
            final_name,
            source_flags,
            dir_fd=parent_descriptor,
        )
        info = os.fstat(source_descriptor)
        _require_regular(info)
        current = os.stat(
            final_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        _require_regular(current)
        if not _same_source_inode(before, info) or not _same_source_inode(
            current, info
        ):
            raise StudioRenderError(
                "Studio source media changed while it was being opened."
            )
        binding = _BoundSource(
            path=take_root.joinpath(*relative_parts),
            descriptor=source_descriptor,
            info=info,
            take_root=take_root,
            root_identity=_directory_identity(root_info),
            relative_parts=relative_parts,
            directory_descriptors=tuple(directory_descriptors),
        )
        source_descriptor = -1
        directory_descriptors = []
        try:
            _require_bound_source_current(binding)
            return binding
        except Exception:
            binding.close()
            raise
    except StudioRenderError:
        raise
    except OSError as exc:
        raise StudioRenderError(
            "Studio source media is missing or its path contains a symbolic link."
        ) from exc
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


def _open_bound_source_portable(
    take_root: Path,
    relative_parts: tuple[str, ...],
    expected_root_identity: tuple[int, int, int] | None,
) -> _BoundSource:
    path = take_root.joinpath(*relative_parts)
    descriptor = -1
    directory_paths = [take_root]
    for index in range(1, len(relative_parts)):
        directory_paths.append(take_root.joinpath(*relative_parts[:index]))
    try:
        before_directories = tuple(item.lstat() for item in directory_paths)
        for info in before_directories:
            _require_directory(info)
        root_identity = _directory_identity(before_directories[0])
        if (
            expected_root_identity is not None
            and expected_root_identity != root_identity
        ):
            raise StudioRenderError("Studio source take root changed before opening.")
        before_source = path.lstat()
        _require_regular(before_source)
        descriptor, info = _open_unrooted_regular_source(path)
        after_directories = tuple(item.lstat() for item in directory_paths)
        after_source = path.lstat()
        for before, after in zip(before_directories, after_directories):
            _require_directory(after)
            if _directory_identity(before) != _directory_identity(after):
                raise StudioRenderError(
                    "Studio source media directory changed while it was opened."
                )
        _require_regular(after_source)
        if not _same_source_inode(before_source, info) or not _same_source_inode(
            after_source, info
        ):
            raise StudioRenderError(
                "Studio source media changed while it was being opened."
            )
        binding = _BoundSource(
            path=path,
            descriptor=descriptor,
            info=info,
            take_root=take_root,
            root_identity=root_identity,
            relative_parts=relative_parts,
            portable_directory_identities=tuple(
                _directory_identity(item) for item in after_directories
            ),
        )
        descriptor = -1
        try:
            _require_bound_source_current(binding)
            return binding
        except Exception:
            binding.close()
            raise
    except StudioRenderError:
        raise
    except OSError as exc:
        raise StudioRenderError(
            "Studio source media is missing or its path contains a symbolic link."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _open_bound_source(
    take_root: Path,
    relative_path: str,
    expected_root_identity: tuple[int, int, int] | None,
) -> _BoundSource:
    parts = _relative_source_parts(relative_path)
    if _SECURE_DIRFD_AVAILABLE:
        return _open_bound_source_dirfd(take_root, parts, expected_root_identity)
    return _open_bound_source_portable(take_root, parts, expected_root_identity)


def _require_bound_source_current(binding: _BoundSource) -> os.stat_result:
    """Require the bound source to remain published below its exact root."""

    try:
        info = os.fstat(binding.descriptor)
        _require_regular(info)
        root_current = binding.take_root.lstat()
        _require_directory(root_current)
        if _directory_identity(root_current) != binding.root_identity:
            raise StudioRenderError("Studio source take root changed while rendering.")

        if binding.uses_dirfd:
            root_opened = os.fstat(binding.directory_descriptors[0])
            _require_directory(root_opened)
            if _directory_identity(root_opened) != binding.root_identity:
                raise StudioRenderError(
                    "Studio source take root changed while rendering."
                )
            for index, part in enumerate(binding.relative_parts[:-1]):
                published = os.stat(
                    part,
                    dir_fd=binding.directory_descriptors[index],
                    follow_symlinks=False,
                )
                child = os.fstat(binding.directory_descriptors[index + 1])
                _require_directory(published)
                _require_directory(child)
                if not _same_source_inode(published, child):
                    raise StudioRenderError(
                        "Studio source media directory changed while rendering."
                    )
            published_source = os.stat(
                binding.relative_parts[-1],
                dir_fd=binding.directory_descriptors[-1],
                follow_symlinks=False,
            )
        else:
            directory_paths = [binding.take_root]
            for index in range(1, len(binding.relative_parts)):
                directory_paths.append(
                    binding.take_root.joinpath(*binding.relative_parts[:index])
                )
            for path, expected in zip(
                directory_paths,
                binding.portable_directory_identities,
            ):
                current = path.lstat()
                _require_directory(current)
                if _directory_identity(current) != expected:
                    raise StudioRenderError(
                        "Studio source media directory changed while rendering."
                    )
            published_source = binding.path.lstat()

        _require_regular(published_source)
        if not _same_source_inode(published_source, info):
            raise StudioRenderError("Studio source media changed while rendering.")
        return info
    except StudioRenderError:
        raise
    except OSError as exc:
        raise StudioRenderError(
            "Studio source media path changed or contains a symbolic link."
        ) from exc


def _sha256_descriptor(
    descriptor: int,
    cancel_check: Callable[[], None] | None = None,
) -> str:
    """Hash a regular source through the descriptor that owns its identity."""

    digest = hashlib.sha256()
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            if cancel_check is not None:
                cancel_check()
            block = os.read(descriptor, _HASH_BLOCK_BYTES)
            if not block:
                break
            digest.update(block)
    except OSError as exc:
        raise StudioRenderError("Studio source media could not be read.") from exc
    finally:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError:
            pass
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    """Compatibility helper that hashes one safely opened regular source."""

    descriptor = -1
    try:
        descriptor, _info = _open_unrooted_regular_source(path)
        return _sha256_descriptor(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _curve_gain(
    positions: np.ndarray,
    length: int,
    curve: FadeCurve,
    *,
    fade_in: bool,
) -> np.ndarray:
    """Vector form of ``studio_project.fade_gain`` for integer positions.

    A fade length is a count of rendered samples.  Therefore positions zero
    and ``length - 1`` are the exact endpoints; a one-sample fade is already
    at its ending gain.
    """

    if length == 1:
        value = 1.0 if fade_in else 0.0
        return np.full(positions.shape, value, dtype=np.float32)

    progress = np.clip(
        positions.astype(np.float64, copy=False) / float(length - 1),
        0.0,
        1.0,
    )
    if curve is FadeCurve.LINEAR:
        gain = progress
    elif curve is FadeCurve.EQUAL_POWER:
        gain = np.sin(progress * (math.pi / 2.0))
    else:
        gain = progress * progress * (3.0 - 2.0 * progress)
    if not fade_in:
        if curve is FadeCurve.EQUAL_POWER:
            gain = np.cos(progress * (math.pi / 2.0))
        else:
            gain = 1.0 - gain

    # Do not leave a cosine residue at an explicitly represented endpoint.
    gain = np.asarray(gain, dtype=np.float32)
    gain[positions <= 0] = 0.0 if fade_in else 1.0
    gain[positions >= length - 1] = 1.0 if fade_in else 0.0
    return gain


def studio_delivery_block(block: np.ndarray) -> tuple[np.ndarray, int]:
    """Return the deterministic speaker/PCM24 safety stage and clip count.

    The internal render bus may exceed full scale when the master limiter is
    bypassed so meters can report overload honestly. Physical playback and
    fixed-point delivery cannot represent that range; both use this exact hard
    saturation boundary rather than diverging or wrapping.
    """

    if not isinstance(block, np.ndarray) or block.ndim != 2 or block.shape[1] != 2:
        raise StudioRenderError("Studio delivery audio must be a stereo array.")
    if block.dtype != np.float32 or not np.all(np.isfinite(block)):
        raise StudioRenderError("Studio delivery audio must be finite float32.")
    clipped_samples = int(np.count_nonzero((block < -1.0) | (block > 1.0)))
    if not clipped_samples:
        return block, 0
    delivered = block.copy()
    np.clip(delivered, -1.0, 1.0, out=delivered)
    return delivered, clipped_samples


@dataclass(frozen=True)
class _SourcePlan:
    key: StudioSourceKey
    track: ProjectTrack | _SongSourceTrack
    segment: MediaSegment | _SongSourceSegment
    take_root: Path
    relative_path: str
    expected_root_identity: tuple[int, int, int] | None

    @property
    def path(self) -> Path:
        return self.take_root.joinpath(*self.relative_path.split("/"))


@dataclass(frozen=True)
class _SongSourceTrack:
    """Minimal immutable source facts consumed by the shared renderer."""

    track_id: str
    media_status: MediaStatus = MediaStatus.AVAILABLE


@dataclass(frozen=True)
class _SongSourceSegment:
    """SongMedia adapter; never masquerades as a recorder TakeProject."""

    segment_id: str
    path: str
    sha256: str
    size_bytes: int
    sample_rate: int
    channels: int
    frame_count: int
    media_status: MediaStatus = MediaStatus.AVAILABLE
    gaps: tuple[object, ...] = ()

    @classmethod
    def from_media(cls, media: SongMedia) -> _SongSourceSegment:
        return cls(
            segment_id=media.media_id,
            path=media.path,
            sha256=media.sha256,
            size_bytes=media.size_bytes,
            sample_rate=media.sample_rate,
            channels=media.channels,
            frame_count=media.frame_count,
        )


@dataclass(frozen=True)
class _ValidatedSource:
    fingerprint: tuple[int, ...]
    sha256: str
    root_identity: tuple[int, int, int]


@dataclass
class _OpenSourceReader:
    binding: _BoundSource
    reader: object
    validated: _ValidatedSource

    @property
    def path(self) -> Path:
        return self.binding.path

    @property
    def descriptor(self) -> int:
        return self.binding.descriptor

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            self.binding.close()


@dataclass(frozen=True)
class _RegionPlan:
    region: StudioRegion
    track: StudioTrack
    source: _SourcePlan
    lane_id: str = ""


@dataclass(frozen=True)
class _TrackPlan:
    track: StudioTrack
    regions: tuple[_RegionPlan, ...]
    comp_ranges: tuple[StudioCompRange, ...]


def _active_region(region: StudioRegion) -> bool:
    return region.enabled and not region.deleted


def _active_lane(lane: StudioTakeLane) -> bool:
    return lane.enabled and not lane.deleted


def _active_comp(comp_range: StudioCompRange) -> bool:
    return comp_range.enabled and not comp_range.deleted


def _active_crossfade(crossfade: StudioCrossfade) -> bool:
    return not crossfade.deleted


class StudioRenderer:
    """Prepared Studio arrangement shared by playback and export.

    ``track_ids`` can restrict the resulting bus (for example, to render one
    processed stem). Catalog resolution still covers the complete active edit
    graph, while descriptor/checksum validation is limited to sources that can
    actually contribute to this render (inactive take lanes are not read).
    ``respect_export_included`` applies the document's export switches;
    playback normally leaves it false.

    Media validation occurs whenever :meth:`open` creates a stream.  That
    keeps construction side-effect free while ensuring no audio block can be
    returned from a missing, replaced, or structurally changed source.
    """

    def __init__(
        self,
        project: TakeProject | SongProject,
        document: StudioDocument,
        take_root: str | Path,
        *,
        block_frames: int = DEFAULT_RENDER_BLOCK_FRAMES,
        track_ids: Sequence[str] | None = None,
        respect_export_included: bool = False,
        apply_master: bool = True,
        verify_checksums: bool = True,
        source_catalog: StudioSourceCatalog | SongMediaCatalog | None = None,
    ) -> None:
        if not isinstance(document, StudioDocument):
            raise StudioRenderError("Studio rendering requires a StudioDocument.")
        self._song_project = isinstance(project, SongProject)
        if isinstance(project, TakeProject):
            if document.schema_version != STUDIO_PROJECT_SCHEMA_VERSION:
                raise StudioRenderError(
                    "A recorded-take renderer requires a schema-2 Studio document."
                )
            if project.session_id != document.session_id:
                raise StudioRenderError(
                    "Studio document belongs to a different session."
                )
            if project.take_id != document.take_id:
                raise StudioRenderError("Studio document belongs to a different take.")
        elif isinstance(project, SongProject):
            if document.schema_version != STUDIO_SONG_PROJECT_SCHEMA_VERSION:
                raise StudioRenderError(
                    "A song-project renderer requires a schema-3 Studio document."
                )
            if project.project_id != document.project_id:
                raise StudioRenderError(
                    "Studio document belongs to a different song project."
                )
        else:
            raise StudioRenderError(
                "Studio rendering requires a TakeProject or SongProject."
            )
        if project.project_sample_rate != document.project_sample_rate:
            raise StudioRenderError(
                "Studio document and source catalog use different sample rates."
            )
        if not isinstance(respect_export_included, bool):
            raise StudioRenderError("respect_export_included must be true or false.")
        if not isinstance(apply_master, bool):
            raise StudioRenderError("apply_master must be true or false.")
        if not isinstance(verify_checksums, bool):
            raise StudioRenderError("verify_checksums must be true or false.")
        if not verify_checksums:
            warnings.warn(
                "verify_checksums=False is deprecated and ignored; declared "
                "Studio source checksums are always enforced.",
                DeprecationWarning,
                stacklevel=2,
            )
        if isinstance(project, TakeProject):
            if (
                source_catalog is not None
                and type(source_catalog) is not StudioSourceCatalog
            ):
                raise StudioRenderError(
                    "Recorded takes require a trusted StudioSourceCatalog."
                )
        elif type(source_catalog) is not SongMediaCatalog:
            raise StudioRenderError(
                "Song projects require a trusted SongMediaCatalog."
            )

        self.project = project
        self.document = document
        self.take_root = Path(take_root).expanduser().resolve()
        self.source_catalog = source_catalog
        if type(source_catalog) is StudioSourceCatalog:
            try:
                source_catalog.require_primary(project, take_root)
            except StudioSourceCatalogError as exc:
                raise StudioRenderError(str(exc)) from exc
        elif type(source_catalog) is SongMediaCatalog:
            try:
                source_catalog.require_project(project, take_root)
            except SongMediaCatalogError as exc:
                raise StudioRenderError(str(exc)) from exc
        self.block_frames = _block_count(block_frames, "block_frames")
        self.apply_master = apply_master
        # Kept as a compatibility attribute while the obsolete opt-out is
        # deprecated. Immutable catalog checksums are an unconditional trust
        # boundary and can no longer be disabled.
        self.verify_checksums = True
        self._validated_sources: dict[StudioSourceKey, _ValidatedSource] = {}
        self._media_validated = False

        requested: frozenset[str] | None = None
        if track_ids is not None:
            if isinstance(track_ids, (str, bytes)):
                raise StudioRenderError("track_ids must be a sequence of track IDs.")
            requested_values = tuple(str(item) for item in track_ids)
            if len(requested_values) != len(set(requested_values)):
                raise StudioRenderError("track_ids contains a duplicate ID.")
            known = {item.track_id for item in document.tracks}
            unknown = set(requested_values).difference(known)
            if unknown:
                raise StudioRenderError(
                    "A requested render track is not in the Studio document."
                )
            if self._song_project:
                by_id = {item.track_id: item for item in document.tracks}
                if any(
                    by_id[item].kind
                    in {StudioTrackKind.BUS, StudioTrackKind.MASTER}
                    for item in requested_values
                ):
                    raise StudioRenderError(
                        "Schema-3 stems select source tracks, not shared bus or "
                        "master tracks."
                    )
            requested = frozenset(requested_values)

        self._sources, all_regions, lane_by_region, comps_by_track = self._catalog()
        ordered_tracks = tuple(
            sorted(document.tracks, key=lambda item: (item.order, item.track_id))
        )
        if self._song_project:
            selected_source_tracks = tuple(
                track
                for track in ordered_tracks
                if track.kind in {StudioTrackKind.AUDIO, StudioTrackKind.BACKING}
                and (requested is None or track.track_id in requested)
                and (not respect_export_included or track.export_included)
            )
            source_ids = {item.track_id for item in selected_source_tracks}
            selected_tracks = tuple(
                track
                for track in ordered_tracks
                if track.track_id in source_ids
                or track.kind in {StudioTrackKind.BUS, StudioTrackKind.MASTER}
            )
            reported_tracks = selected_source_tracks
        else:
            selected_tracks = tuple(
                track
                for track in ordered_tracks
                if (requested is None or track.track_id in requested)
                and (not respect_export_included or track.export_included)
            )
            reported_tracks = selected_tracks
        selected_ids = {item.track_id for item in reported_tracks}
        plans: list[_TrackPlan] = []
        for track in selected_tracks:
            comp_ranges = comps_by_track.get(track.track_id, ())
            candidate_regions = tuple(
                _RegionPlan(
                    region=region,
                    track=track,
                    source=self._sources[self._region_source_key(region)],
                    lane_id=lane_by_region.get(region.region_id, ""),
                )
                for region in all_regions
                if region.track_id == track.track_id
            )
            regions = tuple(
                plan
                for plan in candidate_regions
                if not plan.lane_id
                or any(
                    comp_range.lane_id == plan.lane_id
                    and comp_range.timeline_start_frame < plan.region.timeline_end_frame
                    and comp_range.timeline_end_frame > plan.region.timeline_start_frame
                    for comp_range in comp_ranges
                )
            )
            plans.append(
                _TrackPlan(
                    track=track,
                    regions=regions,
                    comp_ranges=comp_ranges,
                )
            )
        self._track_plans = tuple(plans)
        self._required_source_keys = frozenset(
            plan.source.key
            for track_plan in self._track_plans
            for plan in track_plan.regions
        )
        self._selected_track_ids = frozenset(selected_ids)
        self._crossfades_by_region = self._prepare_crossfades(all_regions)

        rendered_regions = tuple(
            plan.region for track in self._track_plans for plan in track.regions
        )
        self.timeline_start_frame = min(
            (item.timeline_start_frame for item in rendered_regions), default=0
        )
        self.timeline_end_frame = max(
            (item.timeline_end_frame for item in rendered_regions), default=0
        )
        if self._song_project and rendered_regions:
            tail_frames = studio_effect_tail_frames(document)
            if self.timeline_end_frame > MAX_PROJECT_FRAMES - tail_frames:
                raise StudioRenderError(
                    "Studio effect tail exceeds the supported timeline."
                )
            self.timeline_end_frame += tail_frames
        self.total_frames = max(0, self.timeline_end_frame)

    def _region_source_key(self, region: StudioRegion) -> StudioSourceKey:
        if self._song_project:
            return ("", "", region.source_media_id)
        return (
            region.source_take_id,
            region.source_track_id,
            region.source_segment_id,
        )

    @property
    def sample_rate(self) -> int:
        return self.document.project_sample_rate

    @property
    def track_ids(self) -> tuple[str, ...]:
        return tuple(
            item.track.track_id
            for item in self._track_plans
            if item.track.track_id in self._selected_track_ids
        )

    @property
    def stem_semantics(self) -> str:
        """Describe exactly where requested track stems are tapped."""

        if self._song_project:
            return "selected-source-through-shared-routing-and-master"
        return "selected-track-post-fader-through-master"

    def _require_catalog_current(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        if type(self.source_catalog) is StudioSourceCatalog:
            try:
                self.source_catalog.assert_current(cancel_check)
            except StudioSourceCatalogError as exc:
                raise StudioRenderError(str(exc)) from exc
        elif type(self.source_catalog) is SongMediaCatalog:
            try:
                self.source_catalog.assert_current(cancel_check=cancel_check)
            except SongMediaCatalogError as exc:
                raise StudioRenderError(str(exc)) from exc

    def _same_project_identity(self, other: StudioRenderer) -> bool:
        if self._song_project != other._song_project:
            return False
        if self._song_project:
            return (
                isinstance(self.project, SongProject)
                and isinstance(other.project, SongProject)
                and self.project.project_id == other.project.project_id
            )
        return (
            isinstance(self.project, TakeProject)
            and isinstance(other.project, TakeProject)
            and self.project.session_id == other.project.session_id
            and self.project.take_id == other.project.take_id
        )

    def _catalog(
        self,
    ) -> tuple[
        dict[StudioSourceKey, _SourcePlan],
        tuple[StudioRegion, ...],
        dict[str, str],
        dict[str, tuple[StudioCompRange, ...]],
    ]:
        """Resolve the complete active edit list against immutable catalog IDs."""

        if self._song_project:
            return self._catalog_song()

        project_tracks = {item.track_id: item for item in self.project.tracks}
        segments: dict[StudioSourceKey, tuple[ProjectTrack, MediaSegment]] = {}
        for track in self.project.tracks:
            for segment in track.segments:
                segments[(self.project.take_id, track.track_id, segment.segment_id)] = (
                    track,
                    segment,
                )

        active_regions = tuple(
            sorted(
                (item for item in self.document.regions if _active_region(item)),
                key=lambda item: (
                    item.track_id,
                    item.timeline_start_frame,
                    item.timeline_end_frame,
                    item.region_id,
                ),
            )
        )
        source_plans: dict[StudioSourceKey, _SourcePlan] = {}
        for region in active_regions:
            source_key = (
                region.source_take_id,
                region.source_track_id,
                region.source_segment_id,
            )
            if self.source_catalog is None:
                if region.source_take_id != self.project.take_id:
                    raise StudioRenderError(
                        "Studio region references media from a different take; "
                        "a trusted source catalog is required."
                    )
                source_track = project_tracks.get(region.source_track_id)
                found = segments.get(source_key)
                if (
                    source_track is None
                    or found is None
                    or found[0] is not source_track
                ):
                    raise StudioRenderError(
                        "Studio region does not match the source catalog."
                    )
                segment = found[1]
                source_root = self.take_root
                relative_path = segment.path
                expected_root_identity = None
            else:
                try:
                    catalog_source = self.source_catalog.resolve(*source_key)
                    source_track = catalog_source.track
                    segment = catalog_source.segment
                    source_root = catalog_source.take_root
                    relative_path = catalog_source.segment.path
                    expected_root_identity = catalog_source.take_root_identity
                except StudioSourceCatalogError as exc:
                    raise StudioRenderError(str(exc)) from exc
            try:
                source_track.channel_count
            except TakeProjectError as exc:
                raise StudioRenderError(
                    "Studio cannot render a source whose reconnect segments "
                    "change or exceed a mono/stereo channel layout."
                ) from exc
            if region.source_end_frame > segment.frame_count:
                raise StudioRenderError(
                    "Studio region extends beyond its cataloged source segment."
                )
            mapping_source_start = int(region.mapping_source_start_frame)
            mapping_source_count = int(region.mapping_source_frame_count)
            if (
                mapping_source_start < 0
                or mapping_source_start + mapping_source_count > segment.frame_count
            ):
                raise StudioRenderError(
                    "Studio region's affine map escapes its source segment."
                )
            source_plans.setdefault(
                source_key,
                _SourcePlan(
                    key=source_key,
                    track=source_track,
                    segment=segment,
                    take_root=source_root,
                    relative_path=relative_path,
                    expected_root_identity=expected_root_identity,
                ),
            )

        owned_lanes = {
            item.lane_id: item for item in self.document.take_lanes if not item.deleted
        }
        active_lanes = {
            lane_id: lane for lane_id, lane in owned_lanes.items() if _active_lane(lane)
        }
        region_by_id = {item.region_id: item for item in active_regions}
        lane_by_region: dict[str, str] = {}
        for lane in sorted(owned_lanes.values(), key=lambda item: item.lane_id):
            if any(region_id in region_by_id for region_id in lane.region_ids):
                self._require_cross_take_lane_eligible(lane, project_tracks)
            for region_id in lane.region_ids:
                region = region_by_id.get(region_id)
                if region is None:
                    continue
                previous = lane_by_region.setdefault(region_id, lane.lane_id)
                if previous != lane.lane_id:
                    raise StudioRenderError(
                        "An active Studio region belongs to more than one take lane."
                    )
                if lane.source_take_id and (
                    region.source_take_id != lane.source_take_id
                    or region.source_track_id != lane.source_track_id
                ):
                    raise StudioRenderError(
                        "Take-lane media does not match its declared source."
                    )

        comps_by_track: dict[str, list[StudioCompRange]] = {}
        for comp_range in sorted(
            (item for item in self.document.comp_ranges if _active_comp(item)),
            key=lambda item: (
                item.track_id,
                item.timeline_start_frame,
                item.comp_range_id,
            ),
        ):
            lane = active_lanes.get(comp_range.lane_id)
            if lane is None:
                raise StudioRenderError("Comp range references an inactive take lane.")
            if (
                comp_range.fade_in_frames + comp_range.fade_out_frames
                > comp_range.frame_count
            ):
                raise StudioRenderError(
                    "Comp fade ranges overlap and cannot be rendered safely."
                )
            lane_regions = tuple(
                region_by_id[region_id]
                for region_id in lane.region_ids
                if region_id in region_by_id
            )
            self._require_comp_coverage(comp_range, lane_regions)
            comps_by_track.setdefault(comp_range.track_id, []).append(comp_range)

        return (
            source_plans,
            active_regions,
            lane_by_region,
            {key: tuple(value) for key, value in comps_by_track.items()},
        )

    def _catalog_song(
        self,
    ) -> tuple[
        dict[StudioSourceKey, _SourcePlan],
        tuple[StudioRegion, ...],
        dict[str, str],
        dict[str, tuple[StudioCompRange, ...]],
    ]:
        """Resolve schema-3 media IDs through one sealed song catalog."""

        if (
            not isinstance(self.project, SongProject)
            or type(self.source_catalog) is not SongMediaCatalog
        ):
            raise StudioRenderError(
                "Song Studio rendering requires a trusted project media catalog."
            )
        active_regions = tuple(
            sorted(
                (item for item in self.document.regions if _active_region(item)),
                key=lambda item: (
                    item.track_id,
                    item.timeline_start_frame,
                    item.timeline_end_frame,
                    item.region_id,
                ),
            )
        )
        source_plans: dict[StudioSourceKey, _SourcePlan] = {}
        for region in active_regions:
            source_key = self._region_source_key(region)
            try:
                catalog_source = self.source_catalog.resolve(region.source_media_id)
            except SongMediaCatalogError as exc:
                raise StudioRenderError(str(exc)) from exc
            media = catalog_source.media
            if media.media_id not in {item.media_id for item in self.project.media}:
                raise StudioRenderError(
                    "Studio region does not match the song media catalog."
                )
            if region.source_end_frame > media.frame_count:
                raise StudioRenderError(
                    "Studio region extends beyond its cataloged source media."
                )
            mapping_source_start = int(region.mapping_source_start_frame)
            mapping_source_count = int(region.mapping_source_frame_count)
            if (
                mapping_source_start < 0
                or mapping_source_start + mapping_source_count > media.frame_count
            ):
                raise StudioRenderError(
                    "Studio region's affine map escapes its source media."
                )
            source_plans.setdefault(
                source_key,
                _SourcePlan(
                    key=source_key,
                    track=_SongSourceTrack(track_id=region.track_id),
                    segment=_SongSourceSegment.from_media(media),
                    take_root=catalog_source.bundle_root,
                    relative_path=media.path,
                    expected_root_identity=catalog_source.bundle_identity,
                ),
            )

        owned_lanes = {
            item.lane_id: item for item in self.document.take_lanes if not item.deleted
        }
        active_lanes = {
            lane_id: lane for lane_id, lane in owned_lanes.items() if _active_lane(lane)
        }
        region_by_id = {item.region_id: item for item in active_regions}
        lane_by_region: dict[str, str] = {}
        for lane in sorted(owned_lanes.values(), key=lambda item: item.lane_id):
            for region_id in lane.region_ids:
                region = region_by_id.get(region_id)
                if region is None:
                    continue
                previous = lane_by_region.setdefault(region_id, lane.lane_id)
                if previous != lane.lane_id:
                    raise StudioRenderError(
                        "An active Studio region belongs to more than one take lane."
                    )
                if (
                    region.track_id != lane.track_id
                    or region.source_media_id != lane.source_media_id
                ):
                    raise StudioRenderError(
                        "Take-lane media does not match its declared song source."
                    )

        comps_by_track: dict[str, list[StudioCompRange]] = {}
        for comp_range in sorted(
            (item for item in self.document.comp_ranges if _active_comp(item)),
            key=lambda item: (
                item.track_id,
                item.timeline_start_frame,
                item.comp_range_id,
            ),
        ):
            lane = active_lanes.get(comp_range.lane_id)
            if lane is None:
                raise StudioRenderError("Comp range references an inactive take lane.")
            if (
                comp_range.fade_in_frames + comp_range.fade_out_frames
                > comp_range.frame_count
            ):
                raise StudioRenderError(
                    "Comp fade ranges overlap and cannot be rendered safely."
                )
            lane_regions = tuple(
                region_by_id[region_id]
                for region_id in lane.region_ids
                if region_id in region_by_id
            )
            self._require_comp_coverage(comp_range, lane_regions)
            comps_by_track.setdefault(comp_range.track_id, []).append(comp_range)

        return (
            source_plans,
            active_regions,
            lane_by_region,
            {key: tuple(value) for key, value in comps_by_track.items()},
        )

    def _require_cross_take_lane_eligible(
        self,
        lane: StudioTakeLane,
        project_tracks: dict[str, ProjectTrack],
    ) -> None:
        """Re-establish comp eligibility at the mutable-document trust boundary."""

        if not lane.source_take_id or lane.source_take_id == self.project.take_id:
            return
        if self.source_catalog is None:
            raise StudioRenderError(
                "A cross-take lane requires a trusted Studio source catalog."
            )
        try:
            source_project = self.source_catalog.project_for_take(lane.source_take_id)
            source_root = self.source_catalog.root_for_take(lane.source_take_id)
        except StudioSourceCatalogError as exc:
            raise StudioRenderError(str(exc)) from exc
        if source_project.status not in _USABLE_SOURCE_PROJECTS:
            raise StudioRenderError(
                "A cross-take lane source must be complete or explicitly recovered."
            )
        destination_track = project_tracks.get(lane.track_id)
        source_track = next(
            (
                item
                for item in source_project.tracks
                if item.track_id == lane.source_track_id
            ),
            None,
        )
        if destination_track is None or source_track is None:
            raise StudioRenderError(
                "A cross-take lane does not match its trusted track catalogs."
            )

        # Import locally to keep the comp-construction layer independent of the
        # renderer while applying exactly the same durable musician/source rule.
        from core.studio_comping import (
            _timing_ready_source_track,
            compatible_source_tracks,
        )

        if not _timing_ready_source_track(
            source_track,
            source_project,
            take_root=source_root,
        ):
            raise StudioRenderError(
                "A cross-take local original has no verified timeline alignment. "
                "Keep its Jamulus server track, or align and verify the local "
                "original before comping it."
            )

        compatible_ids = {
            item.track_id
            for item in compatible_source_tracks(destination_track, source_project)
        }
        if source_track.track_id not in compatible_ids:
            raise StudioRenderError(
                "A cross-take lane source is not a safe match for this musician."
            )

    @staticmethod
    def _require_comp_coverage(
        comp_range: StudioCompRange,
        regions: Sequence[StudioRegion],
    ) -> None:
        cursor = comp_range.timeline_start_frame
        for region in sorted(
            regions,
            key=lambda item: (
                item.timeline_start_frame,
                item.timeline_end_frame,
                item.region_id,
            ),
        ):
            if region.timeline_end_frame <= cursor:
                continue
            if region.timeline_start_frame > cursor:
                break
            cursor = max(cursor, region.timeline_end_frame)
            if cursor >= comp_range.timeline_end_frame:
                return
        raise StudioRenderError(
            "Comp range is not fully covered by its selected take lane."
        )

    def _prepare_crossfades(
        self, active_regions: Sequence[StudioRegion]
    ) -> dict[str, tuple[tuple[StudioCrossfade, bool], ...]]:
        regions = {item.region_id: item for item in active_regions}
        by_region: dict[str, list[tuple[StudioCrossfade, bool]]] = {}
        for crossfade in sorted(
            (item for item in self.document.crossfades if _active_crossfade(item)),
            key=lambda item: (
                item.start_frame,
                item.end_frame,
                item.crossfade_id,
            ),
        ):
            left = regions.get(crossfade.left_region_id)
            right = regions.get(crossfade.right_region_id)
            if left is None or right is None or left.track_id != right.track_id:
                raise StudioRenderError(
                    "Crossfade does not reference two active regions on one track."
                )
            overlap_start = max(left.timeline_start_frame, right.timeline_start_frame)
            overlap_end = min(left.timeline_end_frame, right.timeline_end_frame)
            if (
                overlap_end <= overlap_start
                or crossfade.start_frame < overlap_start
                or crossfade.end_frame > overlap_end
            ):
                raise StudioRenderError(
                    "Crossfade extends outside its regions' overlap."
                )
            by_region.setdefault(left.region_id, []).append((crossfade, False))
            by_region.setdefault(right.region_id, []).append((crossfade, True))

        for region_id, values in by_region.items():
            ordered = sorted(values, key=lambda item: item[0].start_frame)
            for previous, following in itertools.pairwise(ordered):
                if following[0].start_frame < previous[0].end_frame:
                    raise StudioRenderError(
                        "A region has overlapping crossfade envelopes."
                    )
            by_region[region_id] = ordered
        return {key: tuple(value) for key, value in by_region.items()}

    def validate_media(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        """Validate each renderable source with bounded reads and no writes."""

        if cancel_check is not None and not callable(cancel_check):
            raise StudioRenderError("cancel_check must be callable or null.")
        if not self.take_root.is_dir():
            raise StudioRenderError("The take folder is missing.")
        self._require_catalog_current(cancel_check)
        try:
            import soundfile as sf  # type: ignore
        except ImportError as exc:  # pragma: no cover - packaged dependency
            raise StudioRenderError("Studio audio support is unavailable.") from exc

        validated: dict[StudioSourceKey, _ValidatedSource] = {}
        self._media_validated = False
        try:
            for source in sorted(
                (
                    self._sources[source_key]
                    for source_key in self._required_source_keys
                ),
                key=lambda item: item.key,
            ):
                track = source.track
                segment = source.segment
                if (
                    track.media_status not in _USABLE_MEDIA
                    or segment.media_status not in _USABLE_MEDIA
                ):
                    raise StudioRenderError(
                        "Studio source media is unavailable or requires review."
                    )

                binding: _BoundSource | None = None
                try:
                    binding = _open_bound_source(
                        source.take_root,
                        source.relative_path,
                        source.expected_root_identity,
                    )
                    descriptor = binding.descriptor
                    info = binding.info
                    if segment.size_bytes and info.st_size != segment.size_bytes:
                        raise StudioRenderError("Studio source media changed size.")
                    # Hash the descriptor that will own validation. Checksums
                    # remain mandatory when the immutable catalog declares one;
                    # a pathname-only precheck cannot safely waive this binding.
                    digest = _sha256_descriptor(descriptor, cancel_check)
                    if segment.sha256 and digest != segment.sha256:
                        raise StudioRenderError("Studio source media checksum changed.")
                    try:
                        with sf.SoundFile(
                            descriptor,
                            mode="r",
                            closefd=False,
                        ) as reader:
                            observed = (
                                int(reader.samplerate),
                                int(reader.channels),
                                len(reader),
                            )
                    except Exception as exc:
                        raise StudioRenderError(
                            "Studio source media is corrupt."
                        ) from exc
                    declared = (
                        int(segment.sample_rate),
                        int(segment.channels),
                        int(segment.frame_count),
                    )
                    if observed != declared:
                        raise StudioRenderError(
                            "Studio source media facts do not match the catalog."
                        )
                    if segment.channels > 2:
                        raise StudioRenderError(
                            "Studio cannot safely infer a stereo layout for this source."
                        )
                    final_info = os.fstat(descriptor)
                    if _source_fingerprint(final_info) != _source_fingerprint(info):
                        raise StudioRenderError(
                            "Studio source media changed during validation."
                        )
                    final_info = _require_bound_source_current(binding)
                    validated[source.key] = _ValidatedSource(
                        fingerprint=_source_fingerprint(final_info),
                        sha256=digest,
                        root_identity=binding.root_identity,
                    )
                except OSError as exc:
                    raise StudioRenderError(
                        "Studio source media changed during validation."
                    ) from exc
                finally:
                    if binding is not None:
                        binding.close()
        except Exception:
            self._validated_sources = {}
            raise
        self._validated_sources = validated
        self._media_validated = True

    def reuse_media_validation(
        self,
        validated_renderer: StudioRenderer,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        """Reuse exact source receipts from a compatible prepared renderer.

        Export needs an edited document and a default-original document over
        the same immutable catalog. Reusing descriptor-bound fingerprints
        avoids hashing long sources once per document while every later open
        still verifies the published inode before returning audio.
        """

        if not isinstance(validated_renderer, StudioRenderer):
            raise StudioRenderError(
                "Media validation can be reused only from a StudioRenderer."
            )
        if cancel_check is not None and not callable(cancel_check):
            raise StudioRenderError("cancel_check must be callable or null.")
        if not validated_renderer._media_validated:
            raise StudioRenderError("The source renderer has not validated media.")
        if (
            not self._same_project_identity(validated_renderer)
            or self.take_root != validated_renderer.take_root
            or self.source_catalog is not validated_renderer.source_catalog
        ):
            raise StudioRenderError(
                "Media validation belongs to a different Studio source catalog."
            )
        receipts: dict[StudioSourceKey, _ValidatedSource] = {}
        for source_key in self._required_source_keys:
            source = self._sources[source_key]
            other = validated_renderer._sources.get(source_key)
            receipt = validated_renderer._validated_sources.get(source_key)
            if (
                other is None
                or receipt is None
                or other.segment != source.segment
                or other.track.track_id != source.track.track_id
                or other.take_root != source.take_root
                or other.relative_path != source.relative_path
                or other.expected_root_identity != source.expected_root_identity
            ):
                raise StudioRenderError(
                    "Media validation does not cover this Studio source catalog."
                )
            receipts[source_key] = receipt
        self._validated_sources = receipts
        self._media_validated = True
        if not self._media_validation_is_current(cancel_check):
            self._validated_sources = {}
            self._media_validated = False
            raise StudioRenderError("Studio source media changed after validation.")

    def _media_validation_is_current(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> bool:
        if cancel_check is not None and not callable(cancel_check):
            raise StudioRenderError("cancel_check must be callable or null.")
        try:
            self._require_catalog_current(cancel_check)
        except StudioRenderError:
            return False
        if not self._media_validated or len(self._validated_sources) != len(
            self._required_source_keys
        ):
            return False
        for source_key in self._required_source_keys:
            source = self._sources[source_key]
            if cancel_check is not None:
                cancel_check()
            validated = self._validated_sources.get(source.key)
            if validated is None:
                return False
            binding: _BoundSource | None = None
            try:
                binding = _open_bound_source(
                    source.take_root,
                    source.relative_path,
                    validated.root_identity,
                )
                info = _require_bound_source_current(binding)
            except StudioRenderError:
                return False
            finally:
                if binding is not None:
                    binding.close()
            if validated.fingerprint != _source_fingerprint(info):
                return False
        return True

    def open(
        self,
        *,
        start_frame: int = 0,
        end_frame: int | None = None,
        cancel_check: Callable[[], None] | None = None,
        realtime_safe: bool = False,
    ) -> StudioRenderStream:
        """Validate media and return a bounded, seekable render stream.

        ``realtime_safe`` eagerly binds every source descriptor before this
        method returns. Its later reads use descriptor-only checks, keeping
        pathname traversal and publication validation off an audio callback.
        """

        start = _integer_frame(start_frame, "start_frame")
        if end_frame is None:
            end = max(start, self.timeline_end_frame)
        else:
            end = _integer_frame(end_frame, "end_frame")
            if end < start:
                raise StudioRenderError("end_frame must not precede start_frame.")
        if cancel_check is not None and not callable(cancel_check):
            raise StudioRenderError("cancel_check must be callable or null.")
        if not isinstance(realtime_safe, bool):
            raise StudioRenderError("realtime_safe must be true or false.")
        if (
            realtime_safe
            and self._song_project
            and not studio_mixer_capability(
                self.document
            ).realtime_playback_supported
        ):
            raise StudioRenderError(
                "This effect graph is available for offline bounce but exceeds "
                "the tested interactive playback budget."
            )
        if not self._media_validated:
            self.validate_media(cancel_check)
        elif not self._media_validation_is_current(cancel_check):
            raise StudioRenderError(
                "Studio source media was replaced after validation."
            )
        stream = StudioRenderStream(
            self,
            start_frame=start,
            end_frame=end,
            realtime_safe=realtime_safe,
            cancel_check=cancel_check,
        )
        if realtime_safe:
            try:
                stream.prepare_sources(cancel_check)
            except Exception:
                stream.close()
                raise
        return stream

    def iter_blocks(
        self,
        *,
        start_frame: int = 0,
        end_frame: int | None = None,
        block_frames: int | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> Iterator[np.ndarray]:
        """Yield consecutive stereo float32 blocks from the authoritative path."""

        count = (
            self.block_frames
            if block_frames is None
            else _block_count(block_frames, "block_frames")
        )
        with self.open(
            start_frame=start_frame,
            end_frame=end_frame,
            cancel_check=cancel_check,
        ) as stream:
            while True:
                block = stream.read(count)
                if not len(block):
                    return
                yield block

    def render_block(
        self,
        start_frame: int,
        frame_count: int,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> np.ndarray:
        """Render one random-access block through the same streaming mixer."""

        start = _integer_frame(start_frame, "start_frame")
        count = _block_count(frame_count, "frame_count")
        end = start + count
        if end > MAX_PROJECT_FRAMES:
            raise StudioRenderError("Requested render block is outside the timeline.")
        with self.open(
            start_frame=start,
            end_frame=end,
            cancel_check=cancel_check,
        ) as stream:
            return stream.read(count)

    def _region_envelope(
        self, region: StudioRegion, positions: np.ndarray
    ) -> np.ndarray:
        offsets = positions - region.timeline_start_frame
        gain = np.ones(positions.shape, dtype=np.float32)
        if region.fade_in_frames:
            mask = offsets < region.fade_in_frames
            gain[mask] *= _curve_gain(
                offsets[mask],
                region.fade_in_frames,
                region.fade_in_curve,
                fade_in=True,
            )
        if region.fade_out_frames:
            fade_start = region.timeline_frame_count - region.fade_out_frames
            mask = offsets >= fade_start
            gain[mask] *= _curve_gain(
                offsets[mask] - fade_start,
                region.fade_out_frames,
                region.fade_out_curve,
                fade_in=False,
            )
        for crossfade, incoming in self._crossfades_by_region.get(region.region_id, ()):
            mask = (positions >= crossfade.start_frame) & (
                positions < crossfade.end_frame
            )
            if np.any(mask):
                gain[mask] *= _curve_gain(
                    positions[mask] - crossfade.start_frame,
                    crossfade.frame_count,
                    crossfade.curve,
                    fade_in=incoming,
                )
        return gain

    @staticmethod
    def _comp_pair(
        comp_range: StudioCompRange, positions: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return equal-power ``(base, selected)`` gains inside a comp range."""

        offsets = positions - comp_range.timeline_start_frame
        selected = np.ones(positions.shape, dtype=np.float32)
        base = np.zeros(positions.shape, dtype=np.float32)
        if comp_range.fade_in_frames:
            mask = offsets < comp_range.fade_in_frames
            selected[mask] = _curve_gain(
                offsets[mask],
                comp_range.fade_in_frames,
                FadeCurve.EQUAL_POWER,
                fade_in=True,
            )
            base[mask] = _curve_gain(
                offsets[mask],
                comp_range.fade_in_frames,
                FadeCurve.EQUAL_POWER,
                fade_in=False,
            )
        if comp_range.fade_out_frames:
            fade_start = comp_range.frame_count - comp_range.fade_out_frames
            mask = offsets >= fade_start
            selected[mask] = _curve_gain(
                offsets[mask] - fade_start,
                comp_range.fade_out_frames,
                FadeCurve.EQUAL_POWER,
                fade_in=False,
            )
            base[mask] = _curve_gain(
                offsets[mask] - fade_start,
                comp_range.fade_out_frames,
                FadeCurve.EQUAL_POWER,
                fade_in=True,
            )
        return base, selected

    def _comp_envelope(
        self,
        plan: _RegionPlan,
        comp_ranges: Sequence[StudioCompRange],
        positions: np.ndarray,
    ) -> np.ndarray:
        if plan.lane_id:
            gain = np.zeros(positions.shape, dtype=np.float32)
        else:
            gain = np.ones(positions.shape, dtype=np.float32)
        for comp_range in comp_ranges:
            mask = (positions >= comp_range.timeline_start_frame) & (
                positions < comp_range.timeline_end_frame
            )
            if not np.any(mask):
                continue
            base, selected = self._comp_pair(comp_range, positions[mask])
            if not plan.lane_id:
                gain[mask] = base
            elif plan.lane_id == comp_range.lane_id:
                gain[mask] = selected
            else:
                gain[mask] = 0.0
        return gain


class StudioRenderStream:
    """Context-managed readers and transport position for one renderer."""

    def __init__(
        self,
        renderer: StudioRenderer,
        *,
        start_frame: int,
        end_frame: int,
        realtime_safe: bool = False,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        self.renderer = renderer
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.position_frame = start_frame
        self._readers: OrderedDict[StudioSourceKey, _OpenSourceReader] = OrderedDict()
        visible_ids = {
            item.track.track_id for item in renderer._track_plans
        }
        if renderer._song_project:
            self._track_states = {}
            for track in renderer.document.tracks:
                if (
                    track.track_id not in visible_ids
                    and track.kind
                    in {StudioTrackKind.AUDIO, StudioTrackKind.BACKING}
                ):
                    self._track_states[track.track_id] = replace(
                        track,
                        muted=True,
                        solo=False,
                    )
                else:
                    self._track_states[track.track_id] = track
            try:
                self._mix_engine: StudioMixEngine | None = StudioMixEngine(
                    renderer.document
                )
            except StudioMixerError as exc:
                raise StudioRenderError(str(exc)) from exc
        else:
            self._track_states = {
                item.track.track_id: item.track for item in renderer._track_plans
            }
            self._mix_engine = None
        self._visible_track_ids = frozenset(visible_ids)
        self._master = renderer.document.master
        self._closed = False
        self._realtime_safe = bool(realtime_safe)
        self._preparing_sources = False
        self._cancel_check = cancel_check
        self._cancel_exception: Exception | None = None

    @property
    def sample_rate(self) -> int:
        return self.renderer.sample_rate

    @property
    def remaining_frames(self) -> int:
        return max(0, self.end_frame - self.position_frame)

    @property
    def closed(self) -> bool:
        return self._closed

    @staticmethod
    def _require_reader_current(opened: _OpenSourceReader) -> None:
        if opened.binding.root_identity != opened.validated.root_identity:
            raise StudioRenderError("Studio source media changed while rendering.")
        info = _require_bound_source_current(opened.binding)
        if _source_fingerprint(info) != opened.validated.fingerprint:
            raise StudioRenderError("Studio source media changed while rendering.")

    def _require_reader_usable(self, opened: _OpenSourceReader) -> None:
        if self._realtime_safe and not self._preparing_sources:
            # Preparation owns pathname and descriptor validation. The open FD
            # pins that inode; libsndfile read/seek failures below are translated
            # without putting any open/stat/fstat syscall in the audio callback.
            return
        self._require_reader_current(opened)

    def prepare_sources(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        """Eagerly open every source needed by a realtime playback stream."""

        if self._closed:
            raise StudioRenderError("Studio render stream is closed.")
        if not self._realtime_safe:
            raise StudioRenderError("Only realtime streams can prepare sources.")
        if cancel_check is not None and not callable(cancel_check):
            raise StudioRenderError("cancel_check must be callable or null.")
        sources = {
            plan.source.key: plan.source
            for track_plan in self.renderer._track_plans
            for plan in track_plan.regions
        }
        if len(sources) > MAX_OPEN_SOURCE_READERS:
            raise StudioRenderError(
                "Studio playback references too many source files at once."
            )
        self._preparing_sources = True
        try:
            for source_key in sorted(sources):
                if cancel_check is not None:
                    cancel_check()
                self._reader_for(sources[source_key])
            if cancel_check is not None:
                cancel_check()
            self._ensure_mix_ready(self.position_frame)
        finally:
            self._preparing_sources = False

    def checkpoint(self, frame: int, *, verify_checksum: bool = False) -> int:
        """Revalidate sources at one non-realtime producer boundary.

        Descriptor/path identity is cheap enough for every prepared playback
        block. Initial asynchronous preparation already hashes every immutable
        source; a caller may request another full checksum only from a
        cancellable/background audit, never from an interactive scrub.
        """

        if self._closed:
            raise StudioRenderError("Studio render stream is closed.")
        if not isinstance(verify_checksum, bool):
            raise StudioRenderError("verify_checksum must be true or false.")
        for opened in tuple(self._readers.values()):
            self._require_reader_current(opened)
            if (
                verify_checksum
                and _sha256_descriptor(opened.descriptor) != opened.validated.sha256
            ):
                raise StudioRenderError("Studio source media checksum changed.")
        return self.seek(frame)

    def _reader_for(self, source: _SourcePlan) -> _OpenSourceReader:
        source_key = source.key
        existing = self._readers.pop(source_key, None)
        if existing is not None:
            try:
                self._require_reader_usable(existing)
            except Exception:
                existing.close()
                raise
            self._readers[source_key] = existing
            return existing

        if self._realtime_safe and not self._preparing_sources:
            raise StudioRenderError("Studio realtime source readers were not prepared.")

        validated = self.renderer._validated_sources.get(source_key)
        if validated is None:
            raise StudioRenderError("Studio source media was not validated.")
        binding: _BoundSource | None = None
        reader: object | None = None
        try:
            import soundfile as sf  # type: ignore

            binding = _open_bound_source(
                source.take_root,
                source.relative_path,
                validated.root_identity,
            )
            info = binding.info
            if _source_fingerprint(info) != validated.fingerprint:
                raise StudioRenderError(
                    "Studio source media was replaced after validation."
                )
            reader = sf.SoundFile(
                binding.descriptor,
                mode="r",
                closefd=False,
            )
            observed = (
                int(reader.samplerate),
                int(reader.channels),
                len(reader),
            )
            declared = (
                source.segment.sample_rate,
                source.segment.channels,
                source.segment.frame_count,
            )
            if observed != declared:
                reader.close()
                reader = None
                raise StudioRenderError("Studio source media changed while opening.")
            opened = _OpenSourceReader(
                binding=binding,
                reader=reader,
                validated=validated,
            )
            binding = None
            reader = None
            try:
                self._require_reader_current(opened)
            except Exception:
                opened.close()
                raise
            self._readers[source_key] = opened
            while (
                not self._realtime_safe and len(self._readers) > MAX_OPEN_SOURCE_READERS
            ):
                _old_id, old_reader = self._readers.popitem(last=False)
                old_reader.close()
            return opened
        except Exception as exc:
            if isinstance(exc, StudioRenderError):
                raise
            raise StudioRenderError("Studio source media could not be opened.") from exc
        finally:
            if reader is not None:
                try:
                    reader.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
            if binding is not None:
                binding.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        for reader in self._readers.values():
            try:
                reader.close()
            except Exception:
                pass
        self._readers.clear()
        self._closed = True

    def seek(self, frame: int) -> int:
        if self._closed:
            raise StudioRenderError("Studio render stream is closed.")
        target = _integer_frame(frame, "frame")
        if target < self.start_frame or target > self.end_frame:
            raise StudioRenderError("Seek frame is outside this render stream.")
        self.position_frame = target
        if self._mix_engine is not None:
            self._mix_engine.reset()
        return target

    def set_track_mix(self, track_id: str, **changes: object) -> StudioTrack:
        """Update one stream-local mix state without rebuilding arrangement I/O."""

        if self._closed:
            raise StudioRenderError("Studio render stream is closed.")
        canonical = str(track_id)
        state = self._track_states.get(canonical)
        if state is None or canonical not in self._visible_track_ids:
            raise StudioRenderError("Render stream does not contain that track.")
        allowed = {
            "trim_gain",
            "fader_gain",
            "gain",
            "pan",
            "muted",
            "solo",
        }
        unknown = set(changes).difference(allowed)
        if unknown:
            raise StudioRenderError("Unsupported stream-local track mix setting.")
        values = dict(changes)
        if "gain" in values:
            if "fader_gain" in values:
                raise StudioRenderError("Specify gain or fader_gain, not both.")
            values["fader_gain"] = values.pop("gain")
        try:
            updated = replace(state, **values)
        except (TypeError, ValueError) as exc:
            raise StudioRenderError("Stream-local track mix is invalid.") from exc
        self._track_states[state.track_id] = updated
        return updated

    def set_master(self, master: StudioMaster) -> None:
        """Apply one validated stream-local master state."""

        if self._closed:
            raise StudioRenderError("Studio render stream is closed.")
        if not isinstance(master, StudioMaster):
            raise StudioRenderError("master must be a StudioMaster.")
        self._master = master

    def read(self, frame_count: int) -> np.ndarray:
        """Return up to ``frame_count`` stereo frames and advance transport."""

        mix, _tracks = self.read_with_tracks(frame_count)
        return mix

    def read_with_tracks(
        self, frame_count: int
    ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Return the mixed bus and its processed per-track contributions."""

        if self._closed:
            raise StudioRenderError("Studio render stream is closed.")
        requested = _block_count(frame_count, "frame_count")
        count = min(requested, self.remaining_frames)
        if count <= 0:
            return np.zeros((0, 2), dtype=np.float32), {
                track_id: np.zeros((0, 2), dtype=np.float32)
                for track_id in self._visible_track_ids
            }
        start = self.position_frame
        self._cancel_exception = None
        try:
            self._ensure_mix_ready(start)
            tracks = self._render_tracks(start, count)
            if self._mix_engine is None:
                block = self._mix_tracks(tracks, count)
            else:
                result = self._mix_song_tracks(tracks, start, count)
                block = result.master
                tracks = {
                    track_id: value
                    for track_id, value in result.tracks.items()
                    if track_id in self._visible_track_ids
                }
        except Exception as exc:
            if self._mix_engine is not None:
                # A cancelled/failed stateful block is never reusable because
                # some processors may have advanced before the exception.
                self._mix_engine.reset()
            cancellation = self._cancel_exception
            self._cancel_exception = None
            if cancellation is not None:
                raise cancellation
            if isinstance(exc, StudioRenderError):
                raise
            raise StudioRenderError(
                "Studio source media failed while rendering."
            ) from exc
        self.position_frame += count
        return block, tracks

    def _render_tracks(self, start: int, count: int) -> dict[str, np.ndarray]:
        rendered_tracks: dict[str, np.ndarray] = {}
        song_mix = self._mix_engine is not None
        any_solo = any(state.solo for state in self._track_states.values())
        for track_plan in self.renderer._track_plans:
            state = self._track_states[track_plan.track.track_id]
            audible = song_mix or (
                not state.muted and (state.solo or not any_solo)
            )
            track_mix = np.zeros((count, 2), dtype=np.float32)
            if not audible or (
                not song_mix
                and (state.trim_gain <= 0.0 or state.fader_gain <= 0.0)
            ):
                rendered_tracks[state.track_id] = track_mix
                continue
            for region_plan in track_plan.regions:
                rendered = self._read_region(region_plan, start, count)
                if rendered is None:
                    continue
                destination, positions, source = rendered
                gain = self.renderer._region_envelope(region_plan.region, positions)
                if track_plan.comp_ranges or region_plan.lane_id:
                    gain *= self.renderer._comp_envelope(
                        region_plan,
                        track_plan.comp_ranges,
                        positions,
                    )
                source *= gain[:, np.newaxis]
                stereo = self._to_stereo(source)
                track_mix[destination : destination + len(stereo)] += stereo

            if not song_mix:
                pan = np.float32(state.pan)
                if pan < 0.0:
                    track_mix[:, 1] *= np.float32(1.0) + pan
                elif pan > 0.0:
                    track_mix[:, 0] *= np.float32(1.0) - pan
                track_mix *= np.float32(state.trim_gain * state.fader_gain)
            rendered_tracks[state.track_id] = track_mix
        return rendered_tracks

    def _mix_song_tracks(
        self,
        tracks: dict[str, np.ndarray],
        start: int,
        count: int,
    ) -> StudioMixResult:
        assert self._mix_engine is not None
        try:
            return self._mix_engine.process_block(
                start_frame=start,
                frame_count=count,
                raw_tracks=tracks,
                track_states=self._track_states,
                master=self._master,
                apply_master=self.renderer.apply_master,
                cancel_check=(
                    self._check_cancel
                    if self._cancel_check is not None
                    else None
                ),
            )
        except StudioMixerError as exc:
            raise StudioRenderError(str(exc)) from exc

    def _ensure_mix_ready(self, target_frame: int) -> None:
        """Reset and boundedly pre-roll stateful schema-3 DSP after a seek."""

        engine = self._mix_engine
        if engine is None or engine.expected_frame == target_frame:
            return
        engine.reset()
        cursor = min(target_frame, self.renderer.timeline_start_frame)
        while cursor < target_frame:
            self._check_cancel()
            count = min(self.renderer.block_frames, target_frame - cursor)
            tracks = self._render_tracks(cursor, count)
            self._mix_song_tracks(tracks, cursor, count)
            cursor += count

    def _check_cancel(self) -> None:
        if self._cancel_check is None:
            return
        try:
            self._cancel_check()
        except Exception as exc:
            self._cancel_exception = exc
            raise

    def _mix_tracks(self, tracks: dict[str, np.ndarray], count: int) -> np.ndarray:
        mix = np.zeros((count, 2), dtype=np.float32)
        for track in tracks.values():
            mix += track
        if self.renderer.apply_master:
            mix *= np.float32(self._master.gain)
            if self._master.limiter_enabled:
                np.clip(mix, -1.0, 1.0, out=mix)
        if not np.all(np.isfinite(mix)):
            raise StudioRenderError("Studio render produced non-finite audio.")
        return mix

    @staticmethod
    def _to_stereo(source: np.ndarray) -> np.ndarray:
        if source.shape[1] == 1:
            return np.repeat(source, 2, axis=1)
        return source[:, :2].copy()

    def _read_region(
        self,
        plan: _RegionPlan,
        output_start: int,
        output_count: int,
    ) -> tuple[int, np.ndarray, np.ndarray] | None:
        region = plan.region
        overlap_start = max(output_start, region.timeline_start_frame)
        overlap_end = min(output_start + output_count, region.timeline_end_frame)
        if overlap_end <= overlap_start:
            return None

        positions = np.arange(overlap_start, overlap_end, dtype=np.int64)
        timeline_offsets = positions - int(region.mapping_timeline_start_frame)
        source_positions = float(
            region.mapping_source_start_frame
        ) + timeline_offsets.astype(np.float64) * (
            float(region.mapping_source_frame_count)
            / float(region.mapping_timeline_frame_count)
        )
        # The region's integer source bounds are edit boundaries, while the
        # preserved affine map owns sample positions.  A rounded split may
        # need one neighboring interpolation sample on either side to remain
        # bit-identical to its unsplit parent, so clamp only to the cataloged
        # segment rather than recomputing/clamping to each child's range.
        source_limit = float(plan.source.segment.frame_count - 1)
        np.clip(
            source_positions,
            0.0,
            source_limit,
            out=source_positions,
        )
        lower = np.floor(source_positions).astype(np.int64)
        upper = np.minimum(lower + 1, plan.source.segment.frame_count - 1).astype(
            np.int64
        )
        source_indices = np.unique(np.concatenate((lower, upper)))
        source = np.empty(
            (len(source_indices), plan.source.segment.channels), dtype=np.float32
        )
        opened_reader = self._reader_for(plan.source)
        self._require_reader_usable(opened_reader)
        reader = opened_reader.reader
        boundaries = np.flatnonzero(np.diff(source_indices) != 1) + 1
        run_starts = np.concatenate((np.array([0]), boundaries))
        run_ends = np.concatenate((boundaries, np.array([len(source_indices)])))
        for run_start, run_end in zip(run_starts, run_ends):
            first = int(source_indices[run_start])
            run_count = int(run_end - run_start)
            try:
                reader.seek(first)
                values = reader.read(run_count, dtype="float32", always_2d=True)
            except Exception as exc:
                raise StudioRenderError(
                    "Studio source media could not be read."
                ) from exc
            if len(values) != run_count:
                raise StudioRenderError(
                    "Studio source media ended before its cataloged range."
                )
            source[run_start:run_end] = values
        self._require_reader_usable(opened_reader)
        if not np.all(np.isfinite(source)):
            raise StudioRenderError("Studio source media contains non-finite samples.")

        for gap in plan.source.segment.gaps:
            targets = gap.channels or tuple(range(source.shape[1]))
            source_mask = (source_indices >= gap.start_frame) & (
                source_indices < gap.end_frame
            )
            if np.any(source_mask):
                for channel in targets:
                    source[source_mask, channel] = 0.0

        lower_values = source[np.searchsorted(source_indices, lower)]
        upper_values = source[np.searchsorted(source_indices, upper)]
        fraction = (source_positions - lower.astype(np.float64))[:, np.newaxis]
        rendered = (
            lower_values.astype(np.float64)
            + (upper_values.astype(np.float64) - lower_values.astype(np.float64))
            * fraction
        ).astype(np.float32)

        # The cataloged gap is authoritative even if neighboring interpolation
        # samples would otherwise leak into its fractional-frame boundaries.
        for gap in plan.source.segment.gaps:
            targets = gap.channels or tuple(range(rendered.shape[1]))
            gap_mask = (source_positions >= gap.start_frame) & (
                source_positions < gap.end_frame
            )
            if np.any(gap_mask):
                for channel in targets:
                    rendered[gap_mask, channel] = 0.0

        return overlap_start - output_start, positions, rendered


def iter_studio_blocks(
    project: TakeProject | SongProject,
    document: StudioDocument,
    take_root: str | Path,
    *,
    start_frame: int = 0,
    end_frame: int | None = None,
    block_frames: int = DEFAULT_RENDER_BLOCK_FRAMES,
    track_ids: Sequence[str] | None = None,
    respect_export_included: bool = False,
    apply_master: bool = True,
    verify_checksums: bool = True,
    source_catalog: StudioSourceCatalog | SongMediaCatalog | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> Iterator[np.ndarray]:
    """Convenience iterator over :class:`StudioRenderer`'s shared path."""

    renderer = StudioRenderer(
        project,
        document,
        take_root,
        block_frames=block_frames,
        track_ids=track_ids,
        respect_export_included=respect_export_included,
        apply_master=apply_master,
        verify_checksums=verify_checksums,
        source_catalog=source_catalog,
    )
    yield from renderer.iter_blocks(
        start_frame=start_frame,
        end_frame=end_frame,
        block_frames=block_frames,
        cancel_check=cancel_check,
    )


__all__ = [
    "DEFAULT_RENDER_BLOCK_FRAMES",
    "MAX_OPEN_SOURCE_READERS",
    "MAX_RENDER_BLOCK_FRAMES",
    "StudioRenderError",
    "StudioRenderStream",
    "StudioRenderer",
    "iter_studio_blocks",
    "studio_delivery_block",
]
