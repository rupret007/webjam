"""Host-clocked reference video for the Studio Visit creator profile.

Studio Visit is a shared room where artists talk and work at their own tables.
An optional reference video is the visual analog of Shared Track: the host owns
the transport, everyone else follows.  The two features deliberately differ in
one respect, and that difference drives this whole module.

Shared Track sends decoded audio through Jamulus, so a guest never needs the
host's file and the host's content fingerprint stays a private
controller-to-recorder seam.  A reference video is **not** routed anywhere.
Each participant plays their own local copy, clocked by the host.  Same-file
identity therefore has to be comparable across machines, so this module
publishes a *session-scoped* digest instead of the raw content hash: enrolled
peers can prove they opened the same bytes, while the projection stays
meaningless to anyone outside the session.

What this module does not claim:

* It is not frame-accurate review and carries no media timecode.  The host
  publishes play/pause/stop/seek plus a position; a follower corrects local
  drift on a tolerance.  Sync is bounded by the peer poll interval, not by a
  media clock.
* It never taps a meeting app, a browser, or system output, and it never
  downloads, bundles, or ships media.  The only source is a local file the
  user already has the right to play.
* A follower that cannot prove it holds the same file does not play anything.

Every failure path is closed: a missing, changed, unreadable, or mismatched
file stops playback and says so instead of showing the wrong picture.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

# Identity context string.  It is versioned so a future identity scheme cannot
# be confused with this one, and it is domain-separated from the participant
# token derivation in ``core.session_transfer``.
REFERENCE_VIDEO_IDENTITY_CONTEXT = "webjam-reference-video-v1"

REFERENCE_VIDEO_SUFFIXES: frozenset[str] = frozenset(
    {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
)
MAX_REFERENCE_VIDEO_BYTES = 8 * 1024 * 1024 * 1024
MAX_REFERENCE_VIDEO_DURATION_S = 24.0 * 60.0 * 60.0
MAX_REFERENCE_VIDEO_NAME_CHARS = 255

# The private peer plane is polled roughly every 0.75s, so a follower cannot
# prove it is closer than that to the host.  Correcting below the interval
# would fight the poll rate instead of removing real drift.
DEFAULT_SYNC_TOLERANCE_S = 0.75
# Six consecutive missed polls is a lost host clock, not a hiccup.  A follower
# stops rather than extrapolating a position it can no longer prove.
DEFAULT_STALE_AFTER_S = 5.0

HOST_ONLY_TRANSPORT_MESSAGE = (
    "Only the session host can play, pause, stop, or move the reference video."
)
NO_VIDEO_MESSAGE = "No reference video is shared. Talk and work as usual."
HIDDEN_MESSAGE = (
    "The reference video is hidden on this computer. You are still in the room."
)
NEEDS_FILE_MESSAGE = (
    "The host is sharing a reference video. Open your own copy of the same "
    "file to follow along, or keep it hidden and stay in the room."
)
MISMATCHED_FILE_MESSAGE = (
    "That is not the same file the host is playing, so WebJam will not follow "
    "it. Open the host's exact file, or keep the video hidden."
)
FILE_UNAVAILABLE_MESSAGE = (
    "Your copy of the reference video moved, changed, or became unreadable. "
    "Open it again to follow the host."
)
HOST_ATTENTION_MESSAGE = (
    "The host's reference video needs attention on their computer. Nothing is "
    "playing here until the host recovers it."
)
STALLED_MESSAGE = (
    "WebJam stopped following because the host's position is out of date. "
    "Playback resumes when the host's transport is heard from again."
)
FOLLOWING_MESSAGE = "Following the host's reference video."


class ReferenceVideoError(RuntimeError):
    """A bounded, path-free reference-video failure safe to show a person."""


class ReferenceVideoPlayerError(ReferenceVideoError):
    """The local player could not honor a load or transport request."""


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", round(info.st_mtime * 1_000_000_000))),
        int(getattr(info, "st_ctime_ns", round(info.st_ctime * 1_000_000_000))),
    )


def _display_name(path: Path) -> str:
    name = " ".join(path.name.split())
    if not name or any(character in name for character in ("/", "\\", "\0")):
        raise ReferenceVideoError("That file name cannot be shared.")
    if not all(character.isprintable() for character in name):
        raise ReferenceVideoError("That file name cannot be shared.")
    if len(name) > MAX_REFERENCE_VIDEO_NAME_CHARS:
        name = name[:MAX_REFERENCE_VIDEO_NAME_CHARS]
    return name


def _seconds(value: object, label: str, *, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReferenceVideoError(f"{label} must be a finite non-negative number.")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > maximum:
        raise ReferenceVideoError(f"{label} is outside the supported range.")
    return parsed


@dataclass(frozen=True, slots=True, repr=False)
class ReferenceVideoSource:
    """One descriptor-verified local video file and its content identity.

    ``content_sha256`` is the raw file bytes, never a path or a name.  It stays
    on the machine that computed it; only the session-scoped digest derived
    from it is safe to publish.  ``__repr__`` omits the path so a stray log
    line cannot leak the user's directory layout.
    """

    path: Path
    display_name: str
    content_sha256: str
    byte_size: int

    def __repr__(self) -> str:
        return (
            "ReferenceVideoSource("
            f"display_name={self.display_name!r}, bytes={self.byte_size})"
        )


def load_reference_video_source(path: str | os.PathLike[str]) -> ReferenceVideoSource:
    """Hash one regular local video file, failing closed on any substitution.

    The pathname is opened without following symlinks and the descriptor's
    identity is compared again after EOF, so a file replaced or mutated while
    it was being read produces an error rather than an identity for bytes that
    were never fully seen.
    """

    try:
        candidate = Path(path).expanduser()
    except (TypeError, ValueError, OSError) as exc:
        raise ReferenceVideoError("That is not a usable video file.") from exc
    if not candidate.is_absolute():
        candidate = candidate.resolve()
    if candidate.suffix.lower() not in REFERENCE_VIDEO_SUFFIXES:
        supported = ", ".join(sorted(REFERENCE_VIDEO_SUFFIXES))
        raise ReferenceVideoError(
            f"WebJam shares local video files ending in {supported}."
        )

    display_name = _display_name(candidate)
    descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(candidate, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("not a regular file")
        if opened.st_size <= 0:
            raise OSError("empty file")
        if opened.st_size > MAX_REFERENCE_VIDEO_BYTES:
            raise ReferenceVideoError(
                "That video is larger than WebJam can share in a session."
            )
        bound = _identity(opened)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        current_path = candidate.lstat()
        current_descriptor = os.fstat(descriptor)
        if (
            stat.S_ISLNK(current_path.st_mode)
            or not stat.S_ISREG(current_path.st_mode)
            or _identity(current_path) != bound
            or _identity(current_descriptor) != bound
        ):
            raise OSError("source changed while it was being read")
    except ReferenceVideoError:
        raise
    except Exception as exc:
        raise ReferenceVideoError(
            "WebJam couldn't read that video file. Check that it is a regular "
            "local video file that has not moved or changed."
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:  # pragma: no cover - descriptor already reclaimed
                pass

    return ReferenceVideoSource(
        path=candidate,
        display_name=display_name,
        content_sha256=digest.hexdigest(),
        byte_size=int(opened.st_size),
    )


def file_identity_token(path: str | os.PathLike[str]) -> tuple[int, int, int, int, int]:
    """Return the cheap stat identity used to detect a swapped local file."""

    return _identity(Path(path).expanduser().lstat())


#: Maps a private content hash to the session-scoped digest peers compare.
IdentitySigner = Callable[[str], str]

_SHA256_HEX_CHARS = 64


def _require_content_hash(content_sha256: object) -> str:
    text = str(content_sha256 or "").lower()
    if len(text) != _SHA256_HEX_CHARS or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ReferenceVideoError("A reference video needs a proven content hash.")
    return text


def session_identity_signer(*, session_id: str, session_key: str) -> IdentitySigner:
    """Build the signer that turns a content hash into a publishable digest.

    A raw content hash is a global identifier for a specific media file, so
    publishing one would let anyone holding a state blob confirm exactly which
    video a room is watching.  Keying the digest with the session's invite
    token keeps the comparison available to enrolled peers -- who already hold
    that token -- and meaningless to everyone else.
    """

    identifier = " ".join(str(session_id or "").split())
    key = str(session_key or "")
    if not identifier or not key:
        raise ReferenceVideoError(
            "A reference video can only be shared inside a started session."
        )
    key_bytes = key.encode("utf-8")

    def sign(content_sha256: str) -> str:
        message = (
            f"{REFERENCE_VIDEO_IDENTITY_CONTEXT}:{identifier}:"
            f"{_require_content_hash(content_sha256)}"
        )
        return hmac.new(key_bytes, message.encode("utf-8"), hashlib.sha256).hexdigest()

    return sign


def identities_match(left: object, right: object) -> bool:
    """Compare two published identity digests in constant time.

    Anything that is not a pair of non-empty ASCII digests is a mismatch. One
    side of this comparison arrives from another computer, so a malformed
    value must fail closed rather than raise out of the follow path.
    """

    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if not left or not right or not left.isascii() or not right.isascii():
        return False
    return hmac.compare_digest(left, right)


@runtime_checkable
class ReferenceVideoPlayer(Protocol):
    """The local rendering seam a real Qt player and test fakes both satisfy."""

    def load(self, path: Path) -> float:
        """Open ``path`` and return its duration in seconds."""

    def play(self) -> None: ...

    def pause(self) -> None: ...

    def stop(self) -> None: ...

    def seek(self, position_s: float) -> None: ...

    def position_s(self) -> float: ...

    def close(self) -> None: ...


@runtime_checkable
class HostVideoProjection(Protocol):
    """Host-published truth a follower may render.

    ``core.session_transfer.ReferenceVideoSessionSnapshot`` satisfies this
    structurally.  Declaring it here keeps the wire schema out of this module
    and this module out of the transfer layer's import graph.
    """

    @property
    def shared(self) -> bool: ...

    @property
    def state(self) -> object: ...

    @property
    def source_display_name(self) -> str: ...

    @property
    def identity_digest(self) -> str: ...

    @property
    def position_s(self) -> float: ...

    @property
    def duration_s(self) -> float: ...

    @property
    def playback_generation(self) -> int: ...

    @property
    def needs_attention(self) -> bool: ...


class ReferenceVideoState(str, Enum):
    """Host-owned reference video lifecycle."""

    #: Nothing is shared.  This is the first-class "just talk and work" path,
    #: not a degraded player.
    IDLE = "idle"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    FAILED = "failed"
    CLOSED = "closed"


_HOST_ACTIVE_STATES = frozenset({ReferenceVideoState.PLAYING})
_HOST_LOADED_STATES = frozenset(
    {ReferenceVideoState.READY, ReferenceVideoState.PLAYING, ReferenceVideoState.PAUSED}
)


@dataclass(frozen=True, slots=True)
class ReferenceVideoSnapshot:
    """Host-local reference video truth, safe to render and to project."""

    state: ReferenceVideoState = ReferenceVideoState.IDLE
    shared: bool = False
    source_display_name: str = ""
    identity_digest: str = ""
    position_s: float = 0.0
    duration_s: float = 0.0
    playback_generation: int = 0
    error: str = ""

    @property
    def active(self) -> bool:
        return self.state in _HOST_ACTIVE_STATES

    @property
    def needs_attention(self) -> bool:
        return self.state is ReferenceVideoState.FAILED or bool(self.error)


class ReferenceVideoHostController:
    """Host-only transport over one local video file.

    The controller owns no network and no Jamulus route.  It drives a local
    player, keeps a monotonic playback generation so followers can tell one
    play attempt from the next, and refuses every transport request that does
    not come from the host.
    """

    def __init__(
        self,
        player: ReferenceVideoPlayer,
        *,
        identity_signer: IdentitySigner,
        is_host: Callable[[], bool],
        on_change: Callable[[ReferenceVideoSnapshot], None] | None = None,
    ) -> None:
        self._player = player
        self._identity_signer = identity_signer
        self._is_host = is_host
        self._on_change = on_change
        self._lock = threading.RLock()
        self._state = ReferenceVideoState.IDLE
        self._source: ReferenceVideoSource | None = None
        self._identity_digest = ""
        self._position_s = 0.0
        self._duration_s = 0.0
        self._playback_generation = 0
        self._error = ""

    # -- reads ---------------------------------------------------------

    @property
    def snapshot(self) -> ReferenceVideoSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def content_sha256(self) -> str:
        """Return the private content hash for host-local checks only.

        This is deliberately absent from snapshots and from every peer
        projection; only the session-scoped digest crosses the wire.
        """

        with self._lock:
            return self._source.content_sha256 if self._source is not None else ""

    def _snapshot_locked(self) -> ReferenceVideoSnapshot:
        loaded = self._state in _HOST_LOADED_STATES and self._source is not None
        return ReferenceVideoSnapshot(
            state=self._state,
            shared=loaded,
            source_display_name=self._source.display_name if loaded else "",
            identity_digest=self._identity_digest if loaded else "",
            position_s=self._position_s if loaded else 0.0,
            duration_s=self._duration_s if loaded else 0.0,
            playback_generation=self._playback_generation,
            error=self._error,
        )

    def _notify(self, snapshot: ReferenceVideoSnapshot) -> ReferenceVideoSnapshot:
        if self._on_change is not None:
            self._on_change(snapshot)
        return snapshot

    def _require_host(self) -> None:
        if not self._is_host():
            raise ReferenceVideoError(HOST_ONLY_TRANSPORT_MESSAGE)

    def _require_loaded(self) -> None:
        if self._state not in _HOST_LOADED_STATES or self._source is None:
            raise ReferenceVideoError("No reference video is shared yet.")

    def _fail_locked(self, message: str) -> ReferenceVideoSnapshot:
        self._state = ReferenceVideoState.FAILED
        self._error = str(message).strip() or "The reference video couldn't continue."
        self._position_s = 0.0
        self._duration_s = 0.0
        self._identity_digest = ""
        self._source = None
        return self._notify(self._snapshot_locked())

    # -- host transport ------------------------------------------------

    def share(self, path: str | os.PathLike[str]) -> ReferenceVideoSnapshot:
        """Load, fingerprint, and cue a local file for the room."""

        self._require_host()
        with self._lock:
            if self._state is ReferenceVideoState.CLOSED:
                raise ReferenceVideoError("This reference video session has ended.")
            try:
                source = load_reference_video_source(path)
                digest = self._identity_signer(source.content_sha256)
                duration = _seconds(
                    self._player.load(source.path),
                    "duration",
                    maximum=MAX_REFERENCE_VIDEO_DURATION_S,
                )
            except ReferenceVideoError as exc:
                return self._fail_locked(str(exc))
            except Exception:
                return self._fail_locked(
                    "WebJam couldn't open that video on this computer."
                )
            if duration <= 0.0:
                return self._fail_locked(
                    "That video reports no duration, so it cannot be shared."
                )
            self._source = source
            self._identity_digest = digest
            self._duration_s = duration
            self._position_s = 0.0
            self._error = ""
            self._state = ReferenceVideoState.READY
            return self._notify(self._snapshot_locked())

    def withdraw(self) -> ReferenceVideoSnapshot:
        """Stop sharing and return the room to the no-video path."""

        self._require_host()
        with self._lock:
            if self._state is ReferenceVideoState.CLOSED:
                return self._snapshot_locked()
            try:
                self._player.stop()
            except Exception:
                # Reporting "not shared" while this computer keeps playing
                # would be the one lie this feature must not tell.
                return self._fail_locked(
                    "WebJam couldn't stop that video on this computer, so it "
                    "needs attention before sharing again."
                )
            self._source = None
            self._identity_digest = ""
            self._position_s = 0.0
            self._duration_s = 0.0
            self._error = ""
            self._state = ReferenceVideoState.IDLE
            return self._notify(self._snapshot_locked())

    def play(self) -> ReferenceVideoSnapshot:
        self._require_host()
        with self._lock:
            self._require_loaded()
            try:
                self._player.play()
            except Exception:
                return self._fail_locked(
                    "WebJam couldn't start that video on this computer."
                )
            if self._state is not ReferenceVideoState.PLAYING:
                self._playback_generation += 1
            self._state = ReferenceVideoState.PLAYING
            self._error = ""
            return self._notify(self._snapshot_locked())

    def pause(self) -> ReferenceVideoSnapshot:
        self._require_host()
        with self._lock:
            self._require_loaded()
            try:
                self._player.pause()
                self._position_s = self._clamp(self._player.position_s())
            except Exception:
                return self._fail_locked(
                    "WebJam couldn't pause that video on this computer."
                )
            self._state = ReferenceVideoState.PAUSED
            return self._notify(self._snapshot_locked())

    def stop(self) -> ReferenceVideoSnapshot:
        """Stop playback and rewind, keeping the file shared with the room."""

        self._require_host()
        with self._lock:
            self._require_loaded()
            try:
                self._player.stop()
            except Exception:
                return self._fail_locked(
                    "WebJam couldn't stop that video on this computer."
                )
            self._position_s = 0.0
            self._state = ReferenceVideoState.READY
            return self._notify(self._snapshot_locked())

    def seek(self, position_s: float) -> ReferenceVideoSnapshot:
        self._require_host()
        with self._lock:
            self._require_loaded()
            try:
                target = self._clamp(
                    _seconds(
                        position_s,
                        "position",
                        maximum=MAX_REFERENCE_VIDEO_DURATION_S,
                    )
                )
                self._player.seek(target)
            except ReferenceVideoError:
                raise
            except Exception:
                return self._fail_locked(
                    "WebJam couldn't move that video on this computer."
                )
            self._position_s = target
            return self._notify(self._snapshot_locked())

    def refresh(self) -> ReferenceVideoSnapshot:
        """Sample the local player's position without changing transport."""

        with self._lock:
            if self._state is not ReferenceVideoState.PLAYING:
                return self._snapshot_locked()
            try:
                self._position_s = self._clamp(self._player.position_s())
            except Exception:
                return self._fail_locked(
                    "WebJam lost track of that video on this computer."
                )
            return self._notify(self._snapshot_locked())

    def close(self) -> ReferenceVideoSnapshot:
        """Release the player for good at the end of a session."""

        with self._lock:
            if self._state is ReferenceVideoState.CLOSED:
                return self._snapshot_locked()
            self._safe_player_call("stop")
            self._safe_player_call("close")
            self._source = None
            self._identity_digest = ""
            self._position_s = 0.0
            self._duration_s = 0.0
            self._error = ""
            self._state = ReferenceVideoState.CLOSED
            return self._notify(self._snapshot_locked())

    def _clamp(self, value: object) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(parsed) or parsed < 0.0:
            return 0.0
        if self._duration_s > 0.0:
            return min(parsed, self._duration_s)
        return min(parsed, MAX_REFERENCE_VIDEO_DURATION_S)

    def _safe_player_call(self, name: str) -> None:
        method = getattr(self._player, name, None)
        if method is None:
            return
        try:
            method()
        except Exception:  # noqa: BLE001 - teardown must not mask the reason
            pass


class ReferenceVideoFollowState(str, Enum):
    """What a follower can honestly say about the shared video right now."""

    #: The host is not sharing anything.  The room is still a room.
    NO_VIDEO = "no_video"
    #: This artist chose to ignore the video and keep working.
    HIDDEN = "hidden"
    #: The host is sharing, but this computer has not opened a copy yet.
    NEEDS_FILE = "needs_file"
    #: The opened copy is a different file than the host's.
    MISMATCHED_FILE = "mismatched_file"
    #: The opened copy moved, changed, or became unreadable.
    FILE_UNAVAILABLE = "file_unavailable"
    #: The host's own player failed.
    HOST_ATTENTION = "host_attention"
    #: The host's position is too old to follow honestly.
    STALLED = "stalled"
    FOLLOWING = "following"


_FOLLOW_MESSAGES: dict[ReferenceVideoFollowState, str] = {
    ReferenceVideoFollowState.NO_VIDEO: NO_VIDEO_MESSAGE,
    ReferenceVideoFollowState.HIDDEN: HIDDEN_MESSAGE,
    ReferenceVideoFollowState.NEEDS_FILE: NEEDS_FILE_MESSAGE,
    ReferenceVideoFollowState.MISMATCHED_FILE: MISMATCHED_FILE_MESSAGE,
    ReferenceVideoFollowState.FILE_UNAVAILABLE: FILE_UNAVAILABLE_MESSAGE,
    ReferenceVideoFollowState.HOST_ATTENTION: HOST_ATTENTION_MESSAGE,
    ReferenceVideoFollowState.STALLED: STALLED_MESSAGE,
    ReferenceVideoFollowState.FOLLOWING: FOLLOWING_MESSAGE,
}


@dataclass(frozen=True, slots=True)
class ReferenceVideoFollowSnapshot:
    """One follower's bounded, path-free view of the shared video."""

    state: ReferenceVideoFollowState = ReferenceVideoFollowState.NO_VIDEO
    can_follow: bool = False
    should_play: bool = False
    target_position_s: float = 0.0
    duration_s: float = 0.0
    source_display_name: str = ""
    playback_generation: int = 0
    message: str = NO_VIDEO_MESSAGE

    @property
    def blocked(self) -> bool:
        """True when the host is sharing but this computer cannot follow."""

        return self.state in {
            ReferenceVideoFollowState.NEEDS_FILE,
            ReferenceVideoFollowState.MISMATCHED_FILE,
            ReferenceVideoFollowState.FILE_UNAVAILABLE,
            ReferenceVideoFollowState.HOST_ATTENTION,
            ReferenceVideoFollowState.STALLED,
        }


class ReferenceVideoFollower:
    """Guest-side follower for a host-clocked reference video.

    The type intentionally exposes no play, pause, stop, or seek method.  A
    guest's only inputs are which local file to open and whether to hide the
    video; the transport belongs to the host.
    """

    def __init__(
        self,
        *,
        identity_signer: IdentitySigner,
        player: ReferenceVideoPlayer | None = None,
        tolerance_s: float = DEFAULT_SYNC_TOLERANCE_S,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
    ) -> None:
        self._identity_signer = identity_signer
        self._player = player
        self._tolerance_s = max(0.0, float(tolerance_s))
        self._stale_after_s = max(0.0, float(stale_after_s))
        self._lock = threading.RLock()
        self._local_identity = ""
        self._local_path: Path | None = None
        self._local_token: tuple[int, int, int, int, int] | None = None
        self._hidden = False
        self._projection: HostVideoProjection | None = None
        self._received_monotonic_s = 0.0
        self._applied_generation = -1
        self._playing_locally = False

    # -- guest inputs --------------------------------------------------

    def set_player(self, player: ReferenceVideoPlayer | None) -> None:
        """Attach the local surface before the first copy is opened.

        Followers exist from the moment a room starts so hiding and host
        observation work immediately, but a real player is only built when
        someone actually opens a file.
        """

        with self._lock:
            if self._local_identity:
                raise ReferenceVideoError(
                    "Close the current reference video before changing players."
                )
            self._player = player

    def open_local_copy(
        self, path: str | os.PathLike[str]
    ) -> ReferenceVideoFollowSnapshot:
        """Open this artist's own copy and prove whether it is the host's file.

        A file that does not match is rejected here rather than played, so a
        follower never shows the wrong picture while claiming to be in sync.
        """

        with self._lock:
            source = load_reference_video_source(path)
            identity = self._identity_signer(source.content_sha256)
            projection = self._projection
            expected = str(getattr(projection, "identity_digest", "") or "")
            if expected and not identities_match(identity, expected):
                self._clear_local_locked()
                raise ReferenceVideoError(MISMATCHED_FILE_MESSAGE)
            if self._player is not None:
                try:
                    self._player.load(source.path)
                except Exception as exc:
                    self._clear_local_locked()
                    raise ReferenceVideoPlayerError(
                        "WebJam couldn't open that video on this computer."
                    ) from exc
            self._local_identity = identity
            self._local_path = source.path
            self._local_token = file_identity_token(source.path)
            self._applied_generation = -1
            self._playing_locally = False
            return self._resolve_locked(self._received_monotonic_s)

    def close_local_copy(self) -> ReferenceVideoFollowSnapshot:
        with self._lock:
            self._clear_local_locked()
            return self._resolve_locked(self._received_monotonic_s)

    def set_hidden(self, hidden: bool) -> ReferenceVideoFollowSnapshot:
        """Ignore the video without leaving the room."""

        with self._lock:
            self._hidden = bool(hidden)
            return self._resolve_locked(self._received_monotonic_s)

    @property
    def hidden(self) -> bool:
        with self._lock:
            return self._hidden

    # -- host truth ----------------------------------------------------

    def observe(
        self,
        projection: HostVideoProjection | None,
        *,
        received_monotonic_s: float,
    ) -> None:
        """Record the newest host projection and when this computer saw it.

        The receipt time is measured locally, so followers never need the
        host's wall clock and no clock synchronization is claimed.
        """

        with self._lock:
            self._projection = projection
            self._received_monotonic_s = float(received_monotonic_s)

    def resolve(self, now_monotonic_s: float) -> ReferenceVideoFollowSnapshot:
        """Derive what this computer may honestly do right now.

        This performs one cheap ``lstat`` on the opened copy so a file swapped
        after it was proven fails closed instead of playing on.
        """

        with self._lock:
            return self._resolve_locked(now_monotonic_s)

    def apply(self, now_monotonic_s: float) -> ReferenceVideoFollowSnapshot:
        """Resolve, then drive the local player to the host's position."""

        with self._lock:
            snapshot = self._resolve_locked(now_monotonic_s)
            player = self._player
            if player is None:
                return snapshot
            if not snapshot.can_follow:
                if self._playing_locally:
                    self._playing_locally = False
                    try:
                        player.pause()
                    except Exception:  # noqa: BLE001 - stopping must not raise
                        pass
                return snapshot
            try:
                if snapshot.playback_generation != self._applied_generation:
                    player.seek(snapshot.target_position_s)
                    self._applied_generation = snapshot.playback_generation
                elif (
                    abs(float(player.position_s()) - snapshot.target_position_s)
                    > self._tolerance_s
                ):
                    player.seek(snapshot.target_position_s)
                if snapshot.should_play and not self._playing_locally:
                    player.play()
                    self._playing_locally = True
                elif not snapshot.should_play and self._playing_locally:
                    player.pause()
                    self._playing_locally = False
            except Exception as exc:
                self._playing_locally = False
                raise ReferenceVideoPlayerError(
                    "WebJam couldn't follow the host on this computer."
                ) from exc
            return snapshot

    # -- derivation ----------------------------------------------------

    def _clear_local_locked(self) -> None:
        self._local_identity = ""
        self._local_path = None
        self._local_token = None
        self._applied_generation = -1
        self._playing_locally = False

    def _local_copy_is_current(self) -> bool:
        if self._local_path is None or self._local_token is None:
            return False
        try:
            return file_identity_token(self._local_path) == self._local_token
        except OSError:
            return False

    def _decide(
        self,
        state: ReferenceVideoFollowState,
        *,
        can_follow: bool = False,
        should_play: bool = False,
        target_position_s: float = 0.0,
        duration_s: float = 0.0,
        source_display_name: str = "",
        playback_generation: int = 0,
    ) -> ReferenceVideoFollowSnapshot:
        return ReferenceVideoFollowSnapshot(
            state=state,
            can_follow=can_follow,
            should_play=should_play,
            target_position_s=target_position_s,
            duration_s=duration_s,
            source_display_name=source_display_name,
            playback_generation=playback_generation,
            message=_FOLLOW_MESSAGES[state],
        )

    def _resolve_locked(
        self, now_monotonic_s: float
    ) -> ReferenceVideoFollowSnapshot:
        projection = self._projection
        if projection is None or not bool(getattr(projection, "shared", False)):
            # Deliberately leaves ``_playing_locally`` alone: ``apply`` owns
            # that flag and still has to pause a player that was running when
            # the host withdrew the video.
            return self._decide(ReferenceVideoFollowState.NO_VIDEO)

        name = str(getattr(projection, "source_display_name", "") or "")
        duration = max(0.0, float(getattr(projection, "duration_s", 0.0) or 0.0))
        generation = int(getattr(projection, "playback_generation", 0) or 0)

        def decide(
            state: ReferenceVideoFollowState,
            *,
            can_follow: bool = False,
            should_play: bool = False,
            target_position_s: float = 0.0,
        ) -> ReferenceVideoFollowSnapshot:
            return self._decide(
                state,
                can_follow=can_follow,
                should_play=should_play,
                target_position_s=target_position_s,
                duration_s=duration,
                source_display_name=name,
                playback_generation=generation,
            )

        if self._hidden:
            return decide(ReferenceVideoFollowState.HIDDEN)
        if bool(getattr(projection, "needs_attention", False)):
            return decide(ReferenceVideoFollowState.HOST_ATTENTION)
        if not self._local_identity:
            return decide(ReferenceVideoFollowState.NEEDS_FILE)
        if not self._local_copy_is_current():
            return decide(ReferenceVideoFollowState.FILE_UNAVAILABLE)
        if not identities_match(
            self._local_identity,
            str(getattr(projection, "identity_digest", "") or ""),
        ):
            return decide(ReferenceVideoFollowState.MISMATCHED_FILE)

        host_state = str(getattr(getattr(projection, "state", ""), "value", "") or "")
        if not host_state:
            host_state = str(getattr(projection, "state", "") or "")
        host_playing = host_state == ReferenceVideoState.PLAYING.value
        age = max(0.0, float(now_monotonic_s) - self._received_monotonic_s)
        host_position = max(0.0, float(getattr(projection, "position_s", 0.0) or 0.0))

        if host_playing and age > self._stale_after_s:
            # The host's position can no longer be proven, so the follower
            # holds instead of drifting while claiming to be in sync.
            return decide(
                ReferenceVideoFollowState.STALLED,
                can_follow=True,
                target_position_s=min(host_position, duration) if duration else 0.0,
            )

        # A playing host's published position is already ``age`` seconds old.
        # Advancing it by the locally measured age is the closest estimate a
        # follower can make without a shared clock, and it is only accurate to
        # within the poll interval.
        target = host_position + age if host_playing else host_position
        if duration > 0.0:
            target = min(target, duration)
        return decide(
            ReferenceVideoFollowState.FOLLOWING,
            can_follow=True,
            should_play=host_playing,
            target_position_s=max(0.0, target),
        )


__all__ = [
    "DEFAULT_STALE_AFTER_S",
    "DEFAULT_SYNC_TOLERANCE_S",
    "FILE_UNAVAILABLE_MESSAGE",
    "FOLLOWING_MESSAGE",
    "HIDDEN_MESSAGE",
    "HOST_ATTENTION_MESSAGE",
    "HOST_ONLY_TRANSPORT_MESSAGE",
    "MAX_REFERENCE_VIDEO_BYTES",
    "MAX_REFERENCE_VIDEO_DURATION_S",
    "MISMATCHED_FILE_MESSAGE",
    "NEEDS_FILE_MESSAGE",
    "NO_VIDEO_MESSAGE",
    "REFERENCE_VIDEO_IDENTITY_CONTEXT",
    "REFERENCE_VIDEO_SUFFIXES",
    "STALLED_MESSAGE",
    "HostVideoProjection",
    "IdentitySigner",
    "ReferenceVideoError",
    "ReferenceVideoFollowSnapshot",
    "ReferenceVideoFollowState",
    "ReferenceVideoFollower",
    "ReferenceVideoHostController",
    "ReferenceVideoPlayer",
    "ReferenceVideoPlayerError",
    "ReferenceVideoSnapshot",
    "ReferenceVideoSource",
    "ReferenceVideoState",
    "file_identity_token",
    "identities_match",
    "load_reference_video_source",
    "session_identity_signer",
]
