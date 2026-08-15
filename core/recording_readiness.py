"""Fail-safe recording-storage readiness for real session takes.

The server recorder and optional local originals can both produce audio for a
long time before a filesystem error becomes visible.  This small, GUI-free
check gives Band Check and the Record action the same answer *before* any
recorder is armed.  It intentionally returns musician-safe text and never
includes the private Takes path in the public result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import shutil
import tempfile
from typing import Callable

from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS


_GIB = 1024**3
_PCM24_48K_MONO_BYTES_PER_SECOND = 48_000 * 3
_MAX_LOCAL_ORIGINAL_CHANNELS = 32 * (MAX_JAMULUS_ROSTER_ROWS + 1)
_MINIMUM_FREE_BYTES = 1 * _GIB
_WARNING_FREE_BYTES = 5 * _GIB
_MINIMUM_RESERVE_SECONDS = 15 * 60
_WARNING_RESERVE_SECONDS = 2 * 60 * 60


class RecordingStorageStatus(str, Enum):
    """The only states an ordinary recording start needs to distinguish."""

    READY = "ready"
    WARNING = "warning"
    ACTION_NEEDED = "action_needed"


@dataclass(frozen=True)
class RecordingStorageCheck:
    """A bounded, path-free result for musician UI and diagnostics."""

    status: RecordingStorageStatus
    detail: str
    free_bytes: int | None = None
    required_bytes: int = 0

    @property
    def can_start(self) -> bool:
        return self.status is not RecordingStorageStatus.ACTION_NEEDED


def _display_gib(value: int) -> str:
    return f"{max(0.0, value / _GIB):.1f} GB"


def recording_storage_budget(
    *,
    expected_server_tracks: int,
    local_originals_enabled: bool,
    local_original_tracks: int | None = None,
) -> tuple[int, int]:
    """Return conservative start and warning reserves for PCM24/48-kHz takes.

    Jamulus server tracks may be stereo. ``local_original_tracks`` retains its
    historical API name, but an explicit value is the total mono-equivalent
    channel count across host and guest Local Originals; a stereo logical track
    therefore contributes two. The start reserve protects a useful initial
    segment of a take, while the warning reserve represents a normal two-hour
    rehearsal. Both values deliberately stay bounded so a roster spike does
    not make the product demand an unrealistic amount of free space.
    """

    server_tracks = max(1, int(expected_server_tracks))
    if local_original_tracks is None:
        local_channels = 2 if local_originals_enabled else 0
    else:
        local_channels = (
            max(
                0,
                min(_MAX_LOCAL_ORIGINAL_CHANNELS, int(local_original_tracks)),
            )
            if local_originals_enabled
            else 0
        )
    channels = server_tracks * 2 + local_channels
    bytes_per_second = channels * _PCM24_48K_MONO_BYTES_PER_SECOND
    minimum = max(
        _MINIMUM_FREE_BYTES,
        bytes_per_second * _MINIMUM_RESERVE_SECONDS,
    )
    warning = max(
        _WARNING_FREE_BYTES,
        bytes_per_second * _WARNING_RESERVE_SECONDS,
    )
    return min(minimum, 8 * _GIB), min(warning, 48 * _GIB)


def check_recording_storage(
    takes_directory: str | Path,
    *,
    expected_server_tracks: int,
    local_originals_enabled: bool,
    local_original_tracks: int | None = None,
    disk_usage: Callable[[str | Path], object] = shutil.disk_usage,
) -> RecordingStorageCheck:
    """Check whether storage is safe enough to arm a new real-session take.

    ``disk_usage`` is injectable so every branch is deterministic in tests.
    The result never exposes a source path or raw OS exception to musicians.
    """

    raw_path = str(takes_directory or "").strip()
    if not raw_path:
        return RecordingStorageCheck(
            RecordingStorageStatus.ACTION_NEEDED,
            "WebJam needs a recording folder before a take can start. Open Recording Setup, then try again.",
        )

    try:
        root = Path(raw_path).expanduser()
        if not root.is_dir():
            return RecordingStorageCheck(
                RecordingStorageStatus.ACTION_NEEDED,
                "The recording folder is unavailable. Reopen Recording Setup, choose a usable folder, then try again.",
            )
        # Verify a real zero-byte create/close before an hours-long recorder is
        # allowed to claim it owns this directory. ``os.access`` alone is only a
        # hint, especially on removable or network volumes.
        with tempfile.TemporaryFile(
            prefix=".webjam-storage-check-",
            dir=str(root),
        ):
            pass
        usage = disk_usage(root)
        free_bytes = max(0, int(getattr(usage, "free")))
    except Exception:  # noqa: BLE001 - OS errors must stay private in UI
        return RecordingStorageCheck(
            RecordingStorageStatus.ACTION_NEEDED,
            "WebJam cannot verify free storage for this take. Check the recording drive, then try again.",
        )

    minimum, warning = recording_storage_budget(
        expected_server_tracks=expected_server_tracks,
        local_originals_enabled=local_originals_enabled,
        local_original_tracks=local_original_tracks,
    )
    if free_bytes < minimum:
        return RecordingStorageCheck(
            RecordingStorageStatus.ACTION_NEEDED,
            (
                "There isn't enough free storage to safely start this take. "
                f"Free up space or choose another recording folder, then try again. "
                f"About {_display_gib(free_bytes)} is free."
            ),
            free_bytes=free_bytes,
            required_bytes=minimum,
        )
    if free_bytes < warning:
        return RecordingStorageCheck(
            RecordingStorageStatus.WARNING,
            (
                "Recording storage is running low. This take can start, but "
                "free up space before a long rehearsal. "
                f"About {_display_gib(free_bytes)} is free."
            ),
            free_bytes=free_bytes,
            required_bytes=warning,
        )
    return RecordingStorageCheck(
        RecordingStorageStatus.READY,
        "Recording storage is ready for this take.",
        free_bytes=free_bytes,
        required_bytes=warning,
    )
