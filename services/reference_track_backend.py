"""Platform audio bridge for a Jamulus-routed Reference Track.

The shipping implementation is deliberately macOS-only and fail-closed.  The
pilot implementation accepts only the official BlackHole 16ch or 64ch device
contract (exact UID, name, channel counts, and 48-kHz nominal rate):

* BlackHole channels 0/1 carry WebJam's decoded song into a second Jamulus
  client's inputs.
* BlackHole channels 2/3 receive that client's return mix, keeping it
  physically separate from the song input.

The client has its own profile, UDP port, authenticated loopback RPC port, and
secret.  Before the output stream opens, every current return fader must accept
the pinned client's exact zero-level result.  A monitor repeats that proof for
late joiners; loss of RPC, the route, or the owned process immediately makes
the callback emit silence.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Callable, Mapping
from xml.etree import ElementTree

import numpy as np

from core.audio_route_profile import (
    AudioRoutePlatform,
    AudioRouteProfile,
    Jamulus3122AudioRouteAdapter,
    JamulusChannelMode,
    RouteConfirmationLevel,
)
from core.component_lock import (
    ComponentLockError,
    ComponentLockTimeout,
)
from core.coreaudio_devices import CoreAudioScan
from core.coreaudio_process_route import (
    CoreAudioProcessRouteError,
    CoreAudioProcessRouteProbe,
    CoreAudioProcessRouteSnapshot,
)
from core.jamulus_endpoint import parse_jamulus_endpoint
from core.jamulus_child_environment import (
    JamulusChildEnvironmentError,
    sanitized_jamulus_child_environment,
)
from core.process_socket_identity import (
    JAMULUS_CLIENT_MAX_BASE_PORT,
    exact_jamulus_client_udp_port,
)
from core.reference_track import (
    REFERENCE_BLOCK_FRAMES,
    REFERENCE_MAX_DIAGNOSTIC_COUNTER,
    REFERENCE_MAX_DECODE_FRAMES,
    REFERENCE_PARTICIPANT_NAME as _CORE_REFERENCE_PARTICIPANT_NAME,
    REFERENCE_SAMPLE_RATE,
    ReferenceAudioBridgeSession,
    ReferenceTrackCapability,
    ReferenceTrackError,
    ReferenceTrackLaunchContext,
    ReferenceTrackOwnershipClaim,
)
from core.secure_runtime import (
    SecureRuntimeDirectory,
    SecureRuntimeError,
)
REFERENCE_PROFILE_FILENAME = "WebJam-reference-track-v1.ini"
REFERENCE_SECRET_FILENAME = ".WebJam-reference-track-v1.rpc-secret"
# Defined in core so the take builder can recognise the stem without services.
REFERENCE_PARTICIPANT_NAME = _CORE_REFERENCE_PARTICIPANT_NAME
_PINNED_JAMULUS_VERSION = "3.12.2"
_RPC_MAX_LINE_BYTES = 1024 * 1024
_RPC_READY_TIMEOUT_S = 12.0
_RPC_CALL_TIMEOUT_S = 1.5
_FADER_RECHECK_SECONDS = 0.4
_ROUTE_RECHECK_SECONDS = 0.4
# The proof-freshness budget must absorb the monitor's worst honest cycle:
# a 0.4 s wait plus one fader-proof RPC batch (2 + roster RPC calls, each
# bounded by the 1.5 s socket timeout) plus two CoreAudio scans. At 1.2 s a
# single slow RPC round or a busy CoreAudio scan mid-jam latched a permanent
# fault and silenced the band's song even though every proof still succeeded.
# 3.0 s tolerates one full timeout-bounded round while still silencing the
# stream within three seconds of a monitor stall; actual route *failures*
# still stop audio immediately via the safety epoch, not this budget.
_ROUTE_PROOF_MAX_AGE_SECONDS = 3.0
_MAX_CLIENT_ROWS = 64
_MAX_OWNED_PROFILE_BYTES = 2 * 1024 * 1024
_MAX_OWNED_SECRET_BYTES = 512
_PRIVATE_FILE_TOKEN_BYTES = 16
_REFERENCE_LIFECYCLE_PORT = 47_623
_UNCERTIFIED_ROUTE_DETAIL = (
    "The Reference Track engine is included, but playback is locked in this "
    "private test candidate until the physical macOS pilot proves route, "
    "direct-monitor isolation, and device-switch behavior. Pilot setup "
    "requires official BlackHole 16ch or 64ch at 48 kHz; BlackHole 2ch and "
    "WebJam Bridge are not safe substitutes."
)
_ROUTE_OWNERS_LOCK = threading.Lock()
_ROUTE_OWNERS: dict[str, object] = {}
_ROUTE_OWNER_KEY = "reference-track-global"
_OFFICIAL_BLACKHOLE_ROUTES: dict[str, tuple[str, int]] = {
    "BlackHole16ch_UID": ("BlackHole 16ch", 16),
    "BlackHole64ch_UID": ("BlackHole 64ch", 64),
}
_REFERENCE_LIFECYCLE_LOCK_NAME = ".reference-track-v1.lifecycle.lock"
_PRIVATE_LAUNCH_CHANGED = (
    "Reference Track's private launch profile changed during startup."
)


def reference_track_runtime_directory(home: Path | None = None) -> Path:
    """Return WebJam's private, permissionless second-client runtime.

    The packaged ``JamulusHeadlessClient`` is a distinct, non-sandboxed
    companion and accepts absolute ``--inifile``/``--jsonrpcsecretfile``
    paths.  Keeping both files under WebJam's own Application Support tree
    avoids macOS Other Application Data entirely; the interactive Jamulus
    container and every regular Jamulus profile remain untouched.
    """

    root = Path.home() if home is None else Path(home)
    return (
        root
        / "Library"
        / "Application Support"
        / "WebJam"
        / "runtime"
        / "reference-track"
    )


def _directory_owned_by_current_user(details: os.stat_result) -> bool:
    """Whether a directory descriptor belongs to this effective user."""

    geteuid = getattr(os, "geteuid", None)
    return not callable(geteuid) or int(details.st_uid) == int(geteuid())


def _open_webjam_runtime_directory(
    home: Path,
    *,
    reference_track: bool,
) -> SecureRuntimeDirectory:
    """Open the shared, full-chain-verified WebJam runtime boundary."""

    directory = reference_track_runtime_directory(home)
    if not reference_track:
        directory = directory.parent
    try:
        return SecureRuntimeDirectory.open(
            home=Path(home),
            directory=directory,
            mode=0o700,
        )
    except SecureRuntimeError:
        raise ReferenceTrackError(
            "WebJam couldn't establish its private Reference Track directory."
        ) from None


class _ReferenceLifecycleLock:
    """A no-follow advisory lock opened relative to a pinned runtime dirfd."""

    def __init__(
        self,
        directory: SecureRuntimeDirectory,
        *,
        timeout: float = 0.0,
        poll_interval: float = 0.05,
    ) -> None:
        self._directory = directory
        self._timeout = float(timeout)
        self._poll_interval = float(poll_interval)
        self._descriptor: int | None = None

    def __enter__(self) -> "_ReferenceLifecycleLock":
        if self._descriptor is not None:
            raise ComponentLockError("Reference Track lock is not re-entrant")
        if not self._directory.path_matches():
            raise ComponentLockError("Reference Track runtime directory changed")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise ComponentLockError("Reference Track lock cannot reject links")
        flags |= nofollow
        try:
            descriptor = os.open(
                _REFERENCE_LIFECYCLE_LOCK_NAME,
                flags,
                0o600,
                dir_fd=self._directory.descriptor,
            )
        except (NotImplementedError, OSError) as exc:
            raise ComponentLockError(
                "could not open the Reference Track lifecycle lock"
            ) from exc
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or int(details.st_nlink) != 1
                or not _directory_owned_by_current_user(details)
            ):
                raise ComponentLockError(
                    "Reference Track lifecycle lock is unsafe"
                )
            entry = os.stat(
                _REFERENCE_LIFECYCLE_LOCK_NAME,
                dir_fd=self._directory.descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(entry.st_mode)
                or int(entry.st_dev) != int(details.st_dev)
                or int(entry.st_ino) != int(details.st_ino)
            ):
                raise ComponentLockError(
                    "Reference Track lifecycle lock changed"
                )
            os.fchmod(descriptor, 0o600)
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or int(details.st_nlink) != 1
                or stat.S_IMODE(details.st_mode) != 0o600
                or not _directory_owned_by_current_user(details)
            ):
                raise ComponentLockError(
                    "Reference Track lifecycle lock is unsafe"
                )
            deadline = time.monotonic() + self._timeout
            while True:
                try:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise ComponentLockError(
                            "could not acquire the Reference Track lifecycle lock"
                        ) from exc
                    if time.monotonic() >= deadline:
                        raise ComponentLockTimeout(
                            "timed out waiting for the Reference Track lifecycle lock"
                        ) from exc
                    time.sleep(
                        min(
                            self._poll_interval,
                            max(0.0, deadline - time.monotonic()),
                        )
                    )
            if not self._directory.path_matches():
                raise ComponentLockError(
                    "Reference Track runtime directory changed"
                )
            current = os.stat(
                _REFERENCE_LIFECYCLE_LOCK_NAME,
                dir_fd=self._directory.descriptor,
                follow_symlinks=False,
            )
            if (
                int(current.st_dev) != int(details.st_dev)
                or int(current.st_ino) != int(details.st_ino)
                or not stat.S_ISREG(current.st_mode)
                or int(current.st_nlink) != 1
                or stat.S_IMODE(current.st_mode) != 0o600
                or not _directory_owned_by_current_user(current)
            ):
                raise ComponentLockError(
                    "Reference Track lifecycle lock changed"
                )
            self._descriptor = descriptor
            return self
        except Exception:
            try:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            os.close(descriptor)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class _UnavailableReferenceBackend:
    def __init__(self, platform: str) -> None:
        self._platform = platform

    def capability(
        self, audience_bridge_active: bool = False
    ) -> ReferenceTrackCapability:
        del audience_bridge_active
        if self._platform.startswith("win"):
            detail = (
                "Reference Track is not available on Windows yet. Its "
                "VB-CABLE/JACK isolation backend still needs physical proof."
            )
            platform = "windows"
            backend = "vb-cable-jack"
            reason_code = "windows_backend_unavailable"
        elif self._platform.startswith("linux"):
            detail = (
                "Reference Track is not available on Linux yet. Its JACK "
                "isolation backend still needs physical proof."
            )
            platform = "linux"
            backend = "jack"
            reason_code = "linux_backend_unavailable"
        else:
            detail = "Reference Track routing is not available on this platform."
            platform = self._platform or "unknown"
            backend = "unavailable"
            reason_code = "unsupported_platform"
        return ReferenceTrackCapability(
            False,
            platform,
            detail,
            backend=backend,
            reason_code=reason_code,
        )

    def prepare(
        self, context: ReferenceTrackLaunchContext
    ) -> ReferenceAudioBridgeSession:
        del context
        raise ReferenceTrackError(self.capability().detail)

    def retry_cleanup(self) -> None:
        return


@dataclass(frozen=True, slots=True)
class _BlackHoleRoute:
    uid: str
    name: str
    object_id: int
    sounddevice_index: int
    channels: int
    generation: str


class _BlackHoleRouteLease:
    """One local and cross-process claim on the Reference Track lifecycle."""

    def __init__(
        self,
        token: object,
        interprocess: _ReferenceLifecycleLock,
        lifecycle_socket: socket.socket,
        runtime_directory: SecureRuntimeDirectory,
    ) -> None:
        self._token = token
        self._interprocess = interprocess
        self._lifecycle_socket = lifecycle_socket
        self._runtime_directory = runtime_directory
        self._released = False

    @property
    def child_pass_fds(self) -> tuple[int, ...]:
        descriptor = self._lifecycle_socket.fileno()
        if self._released or descriptor < 0:
            return ()
        return (descriptor,)

    def release(self) -> None:
        if self._released:
            return
        try:
            try:
                self._interprocess.__exit__(None, None, None)
            except OSError:
                # The descriptor is closed in the lock's finally block, which
                # releases the kernel lease even when an explicit unlock
                # reports an error.
                pass
        finally:
            try:
                self._lifecycle_socket.close()
            except OSError:
                pass
            try:
                self._runtime_directory.close()
            except (OSError, SecureRuntimeError):
                pass
            with _ROUTE_OWNERS_LOCK:
                if _ROUTE_OWNERS.get(_ROUTE_OWNER_KEY) is self._token:
                    del _ROUTE_OWNERS[_ROUTE_OWNER_KEY]
                self._released = True


def _reference_track_lock_path(home: Path) -> Path:
    # All eligible BlackHole routes share one Reference Track lifecycle. Keep
    # this lock global rather than UID-specific, and never unlink it while a
    # descriptor may still carry ownership.
    return reference_track_runtime_directory(home).parent / (
        _REFERENCE_LIFECYCLE_LOCK_NAME
    )


def _claim_blackhole_route(uid: str, *, home: Path) -> _BlackHoleRouteLease:
    del uid  # The lifecycle is global across every eligible BlackHole route.
    token = object()
    with _ROUTE_OWNERS_LOCK:
        if _ROUTE_OWNER_KEY in _ROUTE_OWNERS:
            raise ReferenceTrackError(
                "Another WebJam Reference Track already owns this BlackHole route."
            )
        _ROUTE_OWNERS[_ROUTE_OWNER_KEY] = token
    runtime_directory: SecureRuntimeDirectory | None = None
    interprocess: _ReferenceLifecycleLock | None = None
    lifecycle_socket: socket.socket | None = None
    try:
        runtime_directory = _open_webjam_runtime_directory(
            Path(home),
            reference_track=False,
        )
        interprocess = _ReferenceLifecycleLock(
            runtime_directory,
            timeout=0.0,
        )
        lifecycle_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lifecycle_socket.set_inheritable(False)
        lifecycle_socket.bind(("127.0.0.1", _REFERENCE_LIFECYCLE_PORT))
        interprocess.__enter__()
    except ComponentLockTimeout:
        if lifecycle_socket is not None:
            lifecycle_socket.close()
        with _ROUTE_OWNERS_LOCK:
            if _ROUTE_OWNERS.get(_ROUTE_OWNER_KEY) is token:
                del _ROUTE_OWNERS[_ROUTE_OWNER_KEY]
        if runtime_directory is not None:
            runtime_directory.close()
        raise ReferenceTrackError(
            "Another WebJam window is already using Reference Track."
        ) from None
    except OSError as exc:
        if lifecycle_socket is not None:
            lifecycle_socket.close()
        with _ROUTE_OWNERS_LOCK:
            if _ROUTE_OWNERS.get(_ROUTE_OWNER_KEY) is token:
                del _ROUTE_OWNERS[_ROUTE_OWNER_KEY]
        if runtime_directory is not None:
            runtime_directory.close()
        if exc.errno == errno.EADDRINUSE:
            raise ReferenceTrackError(
                "Another WebJam window is already using Reference Track."
            ) from None
        raise ReferenceTrackError(
            "WebJam couldn't reserve the private Reference Track lifecycle."
        ) from None
    except ComponentLockError:
        if lifecycle_socket is not None:
            lifecycle_socket.close()
        with _ROUTE_OWNERS_LOCK:
            if _ROUTE_OWNERS.get(_ROUTE_OWNER_KEY) is token:
                del _ROUTE_OWNERS[_ROUTE_OWNER_KEY]
        if runtime_directory is not None:
            runtime_directory.close()
        raise ReferenceTrackError(
            "WebJam couldn't reserve the private Reference Track lifecycle."
        ) from None
    except Exception:
        if lifecycle_socket is not None:
            lifecycle_socket.close()
        with _ROUTE_OWNERS_LOCK:
            if _ROUTE_OWNERS.get(_ROUTE_OWNER_KEY) is token:
                del _ROUTE_OWNERS[_ROUTE_OWNER_KEY]
        if runtime_directory is not None:
            runtime_directory.close()
        raise ReferenceTrackError(
            "WebJam couldn't reserve the private Reference Track lifecycle."
        ) from None
    assert lifecycle_socket is not None
    assert interprocess is not None
    assert runtime_directory is not None
    return _BlackHoleRouteLease(
        token,
        interprocess,
        lifecycle_socket,
        runtime_directory,
    )


class _ReferencePrivateFiles:
    """Dirfd-pinned, single-session Jamulus profile and RPC secret.

    The display path is used only to launch Jamulus. Every mutation and cleanup
    stays relative to the retained directory descriptor, so a renamed or
    replaced pathname cannot redirect WebJam into another directory.
    """

    def __init__(
        self,
        runtime_directory: SecureRuntimeDirectory,
        *,
        profile_name: str,
        secret_name: str,
    ) -> None:
        self._runtime_directory = runtime_directory
        self.directory_path = runtime_directory.path
        self._directory_fd = runtime_directory.descriptor
        self.profile_name = profile_name
        self.secret_name = secret_name
        self._profile_device: int | None = None
        self._profile_inode: int | None = None
        self._profile_sha256 = ""
        self._secret_device: int | None = None
        self._secret_inode: int | None = None
        self._secret_sha256 = ""
        self._expected_selector = ""
        self._provision_complete = False
        self._profile_quarantine = ""
        self._secret_quarantine = ""
        self._closed = False

    @classmethod
    def open(
        cls,
        directory_path: Path,
        *,
        home: Path,
    ) -> "_ReferencePrivateFiles":
        directory = Path(directory_path).expanduser()
        expected = reference_track_runtime_directory(home)
        if directory != expected:
            raise ReferenceTrackError(
                "WebJam refused an unexpected Reference Track profile directory."
            )
        runtime_directory: SecureRuntimeDirectory | None = None
        try:
            runtime_directory = _open_webjam_runtime_directory(
                Path(home),
                reference_track=True,
            )
            if (
                runtime_directory.path != directory
                or not runtime_directory.path_matches()
            ):
                runtime_directory.close()
                raise ReferenceTrackError(
                    "WebJam refused a changed Reference Track profile directory."
                )
            directory_fd = runtime_directory.descriptor
            opened = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != 0o700
                or not _directory_owned_by_current_user(opened)
            ):
                raise ReferenceTrackError(
                    "WebJam refused a changed Reference Track profile directory."
                )
            for _attempt in range(8):
                token = secrets.token_hex(_PRIVATE_FILE_TOKEN_BYTES)
                profile_name = (
                    f"{Path(REFERENCE_PROFILE_FILENAME).stem}-{token}"
                    f"{Path(REFERENCE_PROFILE_FILENAME).suffix}"
                )
                secret_name = (
                    f"{Path(REFERENCE_SECRET_FILENAME).stem}-{token}"
                    f"{Path(REFERENCE_SECRET_FILENAME).suffix}"
                )
                if not cls._entry_exists(directory_fd, profile_name) and not cls._entry_exists(
                    directory_fd, secret_name
                ):
                    return cls(
                        runtime_directory,
                        profile_name=profile_name,
                        secret_name=secret_name,
                    )
            raise ReferenceTrackError(
                "WebJam couldn't reserve unique private Reference Track files."
            )
        except ReferenceTrackError:
            if runtime_directory is not None:
                try:
                    runtime_directory.close()
                except SecureRuntimeError:
                    pass
            raise
        except (NotImplementedError, OSError, SecureRuntimeError):
            if runtime_directory is not None:
                try:
                    runtime_directory.close()
                except SecureRuntimeError:
                    pass
            raise ReferenceTrackError(
                "WebJam couldn't establish its private Reference Track files."
            ) from None

    @property
    def profile_path(self) -> Path:
        return self.directory_path / self.profile_name

    @property
    def secret_path(self) -> Path:
        return self.directory_path / self.secret_name

    def provision(
        self,
        adapter: Jamulus3122AudioRouteAdapter,
        profile: AudioRouteProfile,
        *,
        secret: str,
    ) -> None:
        if self._closed:
            raise ReferenceTrackError(
                "Reference Track private-file ownership was already closed."
            )
        payload = adapter.render_inifile(
            profile,
            musician_name=REFERENCE_PARTICIPANT_NAME,
        )
        secret_payload = (str(secret) + "\n").encode("utf-8")
        if not 1 <= len(secret_payload) <= _MAX_OWNED_SECRET_BYTES:
            raise ReferenceTrackError(
                "WebJam refused an invalid Reference Track control secret."
            )
        self._expected_selector = str(
            adapter.jamulus_device_selector(profile) or ""
        )
        self._profile_sha256 = hashlib.sha256(payload).hexdigest()
        self._secret_sha256 = hashlib.sha256(secret_payload).hexdigest()
        self._write_new(self.profile_name, payload, kind="profile")
        self._write_new(self.secret_name, secret_payload, kind="secret")
        os.fsync(self._directory_fd)
        self._provision_complete = True

    def path_matches_directory(self) -> bool:
        if self._closed:
            return False
        return self._runtime_directory.path_matches()

    def launch_files_are_exact(self) -> bool:
        """Revalidate both immutable launch inputs immediately before Popen."""

        if (
            not self._provision_complete
            or self._closed
            or not self.path_matches_directory()
        ):
            return False
        profile = self._read_regular(
            self.profile_name,
            limit=_MAX_OWNED_PROFILE_BYTES,
        )
        secret = self._read_regular(
            self.secret_name,
            limit=_MAX_OWNED_SECRET_BYTES,
        )
        if profile is None or secret is None:
            return False
        profile_details, profile_payload = profile
        secret_details, secret_payload = secret
        return (
            int(profile_details.st_dev) == self._profile_device
            and int(profile_details.st_ino) == self._profile_inode
            and hashlib.sha256(profile_payload).hexdigest()
            == self._profile_sha256
            and int(secret_details.st_dev) == self._secret_device
            and int(secret_details.st_ino) == self._secret_inode
            and hashlib.sha256(secret_payload).hexdigest()
            == self._secret_sha256
        )

    def cleanup(self) -> bool:
        """Remove only this session's reserved entries; retain fd on failure."""

        if self._closed:
            return True
        secret_clean = self._cleanup_reserved_entry("secret")
        profile_clean = self._cleanup_reserved_entry("profile")
        clean = secret_clean and profile_clean
        if not clean:
            return False
        try:
            os.fsync(self._directory_fd)
        except OSError:
            return False
        try:
            self._runtime_directory.close()
        except SecureRuntimeError:
            return False
        self._directory_fd = -1
        self._closed = True
        self._secret_sha256 = ""
        return True

    def _cleanup_reserved_entry(self, kind: str) -> bool:
        if kind == "secret":
            original_name = self.secret_name
            quarantine_name = self._secret_quarantine
            expected_inode = self._secret_inode
            limit = _MAX_OWNED_SECRET_BYTES
        elif kind == "profile":
            original_name = self.profile_name
            quarantine_name = self._profile_quarantine
            expected_inode = self._profile_inode
            limit = _MAX_OWNED_PROFILE_BYTES
        else:
            raise ValueError("unknown private-file kind")

        if expected_inode is None and not quarantine_name:
            return not self._entry_exists(self._directory_fd, original_name)
        if not quarantine_name:
            quarantine_name = self._quarantine_entry(original_name)
            if not quarantine_name:
                return not self._entry_exists(self._directory_fd, original_name)
            if kind == "secret":
                self._secret_quarantine = quarantine_name
            else:
                self._profile_quarantine = quarantine_name

        owned = self._read_regular(quarantine_name, limit=limit)
        if owned is None:
            if not self._entry_exists(self._directory_fd, quarantine_name):
                if kind == "secret":
                    self._secret_quarantine = ""
                else:
                    self._profile_quarantine = ""
                return not self._entry_exists(
                    self._directory_fd,
                    original_name,
                )
            self._restore_quarantine(quarantine_name, original_name)
            return False

        details, payload = owned
        if kind == "secret":
            valid = (
                int(details.st_dev) == self._secret_device
                and int(details.st_ino) == self._secret_inode
                and (
                    not self._provision_complete
                    or hashlib.sha256(payload).hexdigest()
                    == self._secret_sha256
                )
            )
        else:
            valid = (
                (
                    int(details.st_dev) == self._profile_device
                    and int(details.st_ino) == self._profile_inode
                )
                or self._valid_jamulus_rewrite(payload)
            )
        if not valid:
            self._restore_quarantine(quarantine_name, original_name)
            return False
        if not self._unlink_quarantine(quarantine_name, details):
            return False
        if kind == "secret":
            self._secret_quarantine = ""
        else:
            self._profile_quarantine = ""
        return not self._entry_exists(self._directory_fd, original_name)

    def _quarantine_entry(self, original_name: str) -> str:
        """Atomically move one reserved name aside before validation."""

        for _attempt in range(8):
            quarantine_name = (
                f".WebJam-reference-track-cleanup-"
                f"{secrets.token_hex(_PRIVATE_FILE_TOKEN_BYTES)}"
            )
            if self._entry_exists(self._directory_fd, quarantine_name):
                continue
            try:
                os.rename(
                    original_name,
                    quarantine_name,
                    src_dir_fd=self._directory_fd,
                    dst_dir_fd=self._directory_fd,
                )
            except FileNotFoundError:
                return ""
            except OSError:
                return ""
            return quarantine_name
        return ""

    def _restore_quarantine(
        self,
        quarantine_name: str,
        original_name: str,
    ) -> bool:
        """Restore a non-directory rejected entry without overwriting a race."""

        try:
            details = os.stat(
                quarantine_name,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
        except OSError:
            return False
        if stat.S_ISDIR(details.st_mode):
            return False
        try:
            os.link(
                quarantine_name,
                original_name,
                src_dir_fd=self._directory_fd,
                dst_dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
        except OSError:
            return False
        try:
            os.unlink(quarantine_name, dir_fd=self._directory_fd)
        except OSError:
            # The rejected bytes remain at both private names. Keep the
            # quarantine token so a later retry can finish without guessing.
            return False
        if quarantine_name == self._secret_quarantine:
            self._secret_quarantine = ""
        if quarantine_name == self._profile_quarantine:
            self._profile_quarantine = ""
        return True

    def _unlink_quarantine(
        self,
        quarantine_name: str,
        expected: os.stat_result,
    ) -> bool:
        """Delete only the unguessable tombstone that was just validated."""

        try:
            current = os.stat(
                quarantine_name,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            if (
                int(current.st_dev) != int(expected.st_dev)
                or int(current.st_ino) != int(expected.st_ino)
                or not stat.S_ISREG(current.st_mode)
            ):
                self._restore_quarantine(
                    quarantine_name,
                    (
                        self.secret_name
                        if quarantine_name == self._secret_quarantine
                        else self.profile_name
                    ),
                )
                return False
            os.unlink(quarantine_name, dir_fd=self._directory_fd)
            return True
        except OSError:
            return False

    def _valid_jamulus_rewrite(self, payload: bytes) -> bool:
        """Recognize the pinned client's rewrite without trusting arbitrary bytes."""

        if (
            not payload
            or hashlib.sha256(payload).hexdigest() == self._profile_sha256
        ):
            return bool(payload)
        try:
            root = ElementTree.fromstring(payload)
        except (ElementTree.ParseError, LookupError, RecursionError, ValueError):
            return False
        if root.tag != "client":
            return False
        required = {
            "name_base64",
            "auddev_base64",
            "sndcrdinlch",
            "sndcrdinrch",
            "sndcrdoutlch",
            "sndcrdoutrch",
            "audiochannels",
        }
        values: dict[str, str] = {}
        for child in root:
            if child.tag not in required:
                continue
            if child.tag in values or len(child):
                return False
            values[child.tag] = child.text or ""
        if set(values) != required:
            return False
        try:
            name = base64.b64decode(
                values["name_base64"],
                validate=True,
            ).decode("utf-8")
            selector = base64.b64decode(
                values["auddev_base64"],
                validate=True,
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        return (
            name == REFERENCE_PARTICIPANT_NAME
            and selector == self._expected_selector
            and values["sndcrdinlch"] == "0"
            and values["sndcrdinrch"] == "1"
            and values["sndcrdoutlch"] == "2"
            and values["sndcrdoutrch"] == "3"
            and values["audiochannels"] == str(int(JamulusChannelMode.STEREO))
        )

    def _write_new(self, name: str, payload: bytes, *, kind: str) -> None:
        self._validate_name(name)
        if kind not in {"profile", "secret"}:
            raise ValueError("unknown private-file kind")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        created: os.stat_result | None = None
        try:
            descriptor = os.open(
                name,
                flags,
                0o600,
                dir_fd=self._directory_fd,
            )
            created = os.fstat(descriptor)
            if (
                not stat.S_ISREG(created.st_mode)
                or (
                    hasattr(os, "geteuid")
                    and int(created.st_uid) != int(os.geteuid())
                )
            ):
                raise OSError("unsafe private file")
            if kind == "profile":
                self._profile_device = int(created.st_dev)
                self._profile_inode = int(created.st_ino)
            else:
                self._secret_device = int(created.st_dev)
                self._secret_inode = int(created.st_ino)
            os.fchmod(descriptor, 0o600)
            view = memoryview(payload)
            written = 0
            while written < len(view):
                amount = os.write(descriptor, view[written:])
                if amount <= 0:
                    raise OSError("short private-file write")
                written += amount
            os.fsync(descriptor)
            final = os.fstat(descriptor)
            if (
                not stat.S_ISREG(final.st_mode)
                or int(final.st_dev) != int(created.st_dev)
                or int(final.st_ino) != int(created.st_ino)
                or int(final.st_size) != len(payload)
                or int(final.st_nlink) != 1
                or stat.S_IMODE(final.st_mode) != 0o600
                or not _directory_owned_by_current_user(final)
            ):
                raise OSError("private-file verification failed")
        except Exception:
            if created is not None:
                try:
                    current = os.stat(
                        name,
                        dir_fd=self._directory_fd,
                        follow_symlinks=False,
                    )
                    if (
                        int(current.st_dev) == int(created.st_dev)
                        and int(current.st_ino) == int(created.st_ino)
                        and not stat.S_ISDIR(current.st_mode)
                    ):
                        os.unlink(name, dir_fd=self._directory_fd)
                except OSError:
                    pass
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _read_regular(
        self,
        name: str,
        *,
        limit: int,
    ) -> tuple[os.stat_result, bytes] | None:
        self._validate_name(name)
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = -1
        try:
            descriptor = os.open(name, flags, dir_fd=self._directory_fd)
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or int(details.st_size) < 0
                or int(details.st_size) > limit
                or int(details.st_nlink) != 1
                or stat.S_IMODE(details.st_mode) != 0o600
                or not _directory_owned_by_current_user(details)
            ):
                return None
            remaining = int(details.st_size) + 1
            chunks: list[bytes] = []
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != int(details.st_size):
                return None
            final = os.fstat(descriptor)
            try:
                visible = os.stat(
                    name,
                    dir_fd=self._directory_fd,
                    follow_symlinks=False,
                )
            except OSError:
                return None
            if (
                not stat.S_ISREG(final.st_mode)
                or not stat.S_ISREG(visible.st_mode)
                or int(final.st_dev) != int(details.st_dev)
                or int(final.st_ino) != int(details.st_ino)
                or int(final.st_size) != int(details.st_size)
                or int(final.st_nlink) != 1
                or stat.S_IMODE(final.st_mode) != 0o600
                or not _directory_owned_by_current_user(final)
                or int(visible.st_dev) != int(final.st_dev)
                or int(visible.st_ino) != int(final.st_ino)
                or int(visible.st_size) != int(final.st_size)
                or int(visible.st_nlink) != 1
                or stat.S_IMODE(visible.st_mode) != 0o600
                or not _directory_owned_by_current_user(visible)
            ):
                return None
            return final, payload
        finally:
            os.close(descriptor)

    @staticmethod
    def _entry_exists(directory_fd: int, name: str) -> bool:
        _ReferencePrivateFiles._validate_name(name)
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            # An unreadable reserved entry is not safely absent.
            return True

    @staticmethod
    def _validate_name(name: str) -> None:
        if (
            not name
            or name in {".", ".."}
            or "\x00" in name
            or "/" in name
            or (os.altsep and os.altsep in name)
        ):
            raise ReferenceTrackError(
                "WebJam refused an invalid private Reference Track filename."
            )


@dataclass(slots=True)
class _PendingReferenceCleanup:
    process: subprocess.Popen | None
    rpc: _ReferenceRpcControl | None
    files: _ReferencePrivateFiles | None
    route_lease: _BlackHoleRouteLease

    def retry(self) -> bool:
        if not MacOSBlackHoleReferenceBackend._terminate_process(self.process):
            return False
        if self.rpc is not None:
            try:
                self.rpc.close()
            except Exception:  # noqa: BLE001 - retry must retain ownership
                return False
        if self.files is not None:
            try:
                if not self.files.cleanup():
                    return False
            except Exception:  # noqa: BLE001 - retry must retain ownership
                return False
        self.route_lease.release()
        return True


def _default_version_probe(binary: str) -> str:
    platform_name = (
        "darwin"
        if sys.platform.startswith("darwin")
        else "win32"
        if sys.platform.startswith("win")
        else "linux"
    )
    try:
        environment = sanitized_jamulus_child_environment(
            os.environ,
            platform_name=platform_name,
            executable=binary,
        )
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
            shell=False,
            env=environment,
            cwd=str(Path(binary).parent if platform_name == "win32" else Path("/")),
        )
    except (
        JamulusChildEnvironmentError,
        OSError,
        subprocess.SubprocessError,
    ):
        return ""
    import re

    match = re.search(
        r"(?:version\s+)?(\d+\.\d+\.\d+)",
        f"{result.stdout}\n{result.stderr}",
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _default_headless_client_probe(binary: str) -> bool:
    """Require a true headless client, not a GUI build run with ``--nogui``.

    In pinned Jamulus 3.12.2, the JSON-RPC fader handler applies gains directly
    only in a compile-time HEADLESS client.  A GUI build launched with the
    runtime ``--nogui`` flag returns ``"ok"`` but has no mixer dialog connected
    to apply the command.  On macOS the GUI build is unambiguously linked
    against QtWidgets; a separately packaged headless client must not be.
    """

    if not sys.platform.startswith("darwin"):
        return False
    try:
        environment = sanitized_jamulus_child_environment(
            os.environ,
            platform_name="darwin",
            executable="/usr/bin/otool",
        )
        result = subprocess.run(
            ["/usr/bin/otool", "-L", binary],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
            shell=False,
            env=environment,
            cwd="/",
        )
    except (
        JamulusChildEnvironmentError,
        OSError,
        subprocess.SubprocessError,
    ):
        return False
    return result.returncode == 0 and "QtWidgets.framework" not in result.stdout


def _default_port_allocator(kind: str, excluded: set[int]) -> int:
    socket_type = socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM
    for _attempt in range(32):
        probe = socket.socket(socket.AF_INET, socket_type)
        try:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        finally:
            probe.close()
        if (
            port not in excluded
            and (kind != "udp" or port <= JAMULUS_CLIENT_MAX_BASE_PORT)
        ):
            return port
    raise ReferenceTrackError(
        "WebJam couldn't reserve a separate local port for Reference Track."
    )


class _ReferenceRpcControl:
    """Small synchronous owner for the dedicated Jamulus client's RPC socket."""

    _ALLOWED = frozenset(
        {
            "jamulus/apiAuth",
            "jamulus/getMode",
            "jamulusclient/getClientList",
            "jamulusclient/setFaderLevel",
        }
    )

    def __init__(
        self,
        port: int,
        secret: str,
        *,
        socket_factory: Callable[..., socket.socket] = socket.create_connection,
    ) -> None:
        self._port = int(port)
        self._secret = str(secret)
        self._socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._request_id = 0
        self._closed = False

    def connect(self) -> None:
        if self._closed:
            raise ReferenceTrackError("Reference Track control was already closed.")
        self._disconnect_socket()
        try:
            sock = self._socket_factory(
                ("127.0.0.1", self._port), timeout=_RPC_CALL_TIMEOUT_S
            )
            sock.settimeout(_RPC_CALL_TIMEOUT_S)
            self._socket = sock
            result = self.call("jamulus/apiAuth", {"secret": self._secret})
            if result != "ok":
                raise ReferenceTrackError(
                    "Reference Track couldn't authenticate its private "
                    "Jamulus control."
                )
            mode = self.call("jamulus/getMode", {})
            if not isinstance(mode, Mapping) or mode.get("mode") != "client":
                raise ReferenceTrackError(
                    "Reference Track control did not reach its owned "
                    "Jamulus client."
                )
        except ReferenceTrackError:
            self._disconnect_socket()
            raise
        except Exception:  # noqa: BLE001 - socket factory boundary
            self._disconnect_socket()
            raise ReferenceTrackError(
                "Reference Track's private Jamulus control is not ready."
            ) from None

    def call(self, method: str, params: Mapping[str, object]) -> object:
        if method not in self._ALLOWED:
            raise ReferenceTrackError(
                "Reference Track refused an unsupported Jamulus command."
            )
        sock = self._socket
        if sock is None or self._closed:
            raise ReferenceTrackError("Reference Track control is unavailable.")
        self._request_id += 1
        request_id = self._request_id
        payload = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": dict(params),
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        try:
            sock.sendall(payload)
            while True:
                response = self._read_object()
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise ReferenceTrackError(
                        "Reference Track's Jamulus client refused a safety command."
                    )
                if "result" not in response:
                    raise ReferenceTrackError(
                        "Reference Track received an invalid Jamulus response."
                    )
                return response["result"]
        except ReferenceTrackError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise ReferenceTrackError(
                "Reference Track lost its private Jamulus control connection."
            ) from exc

    def client_rows(self) -> tuple[int, ...]:
        """Return the ordered, validated client-local mixer channel IDs.

        Jamulus rewrites each server channel ID through ``FindClientChannel``
        before publishing ``getClientList``.  The resulting IDs index the
        client's local fader array and can contain gaps after disconnects;
        their JSON array positions are not valid mixer identities.
        """

        result = self.call("jamulusclient/getClientList", {})
        if not isinstance(result, Mapping) or not isinstance(
            result.get("clients"), list
        ):
            raise ReferenceTrackError(
                "Reference Track couldn't verify the Jamulus return mix."
            )
        raw_clients = result["clients"]
        if not 1 <= len(raw_clients) <= _MAX_CLIENT_ROWS:
            raise ReferenceTrackError(
                "Reference Track is waiting for its Jamulus participant to connect."
            )
        rows: list[int] = []
        seen_ids: set[int] = set()
        for raw in raw_clients:
            if not isinstance(raw, Mapping):
                raise ReferenceTrackError(
                    "Reference Track couldn't verify the Jamulus return mix."
                )
            value = raw.get("id")
            if not isinstance(value, int) or isinstance(value, bool):
                raise ReferenceTrackError(
                    "Reference Track couldn't verify the Jamulus return mix."
                )
            client_local_id = value
            if client_local_id < 0 or client_local_id in seen_ids:
                raise ReferenceTrackError(
                    "Reference Track couldn't verify the Jamulus return mix."
                )
            seen_ids.add(client_local_id)
            rows.append(client_local_id)
        return tuple(rows)

    def prove_all_faders_zero(self) -> int:
        rows = self.client_rows()
        for client_local_id in rows:
            result = self.call(
                "jamulusclient/setFaderLevel",
                {"channelIndex": client_local_id, "level": 0},
            )
            # The pinned Jamulus 3.12.2 JSON-RPC contract returns exact "ok".
            # Null, boolean, and the server-recorder token "acknowledged" are
            # not evidence that the client accepted the command.
            if result != "ok":
                raise ReferenceTrackError(
                    "Reference Track couldn't prove that every return fader is zero."
                )
        # Refuse a successful-looking proof if the roster changed while its
        # position-based commands were being applied.
        if self.client_rows() != rows:
            raise ReferenceTrackError(
                "Reference Track's Jamulus roster changed during route proof."
            )
        return len(rows)

    def _read_object(self) -> dict:
        sock = self._socket
        if sock is None:
            raise ReferenceTrackError("Reference Track control is unavailable.")
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ReferenceTrackError(
                        "Reference Track received an invalid Jamulus response."
                    ) from exc
                if not isinstance(value, dict):
                    raise ReferenceTrackError(
                        "Reference Track received an invalid Jamulus response."
                    )
                return value
            chunk = sock.recv(16_384)
            if not chunk:
                raise ReferenceTrackError(
                    "Reference Track's Jamulus control connection closed."
                )
            self._buffer.extend(chunk)
            if len(self._buffer) > _RPC_MAX_LINE_BYTES:
                raise ReferenceTrackError(
                    "Reference Track refused an oversized Jamulus response."
                )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._secret = ""
        self._disconnect_socket()

    def _disconnect_socket(self) -> None:
        sock = self._socket
        self._socket = None
        self._buffer.clear()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


class MacOSBlackHoleReferenceBackend:
    """Capability-gated owner of the macOS second-client topology."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        scanner: Callable[[], CoreAudioScan] | None = None,
        sounddevice_module: object | None = None,
        version_probe: Callable[[str], str] = _default_version_probe,
        headless_client_probe: Callable[
            [str], bool
        ] = _default_headless_client_probe,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        port_allocator: Callable[[str, set[int]], int] = _default_port_allocator,
        rpc_factory: Callable[[int, str], _ReferenceRpcControl] | None = None,
        udp_port_resolver: Callable[[int, int], int] = (
            exact_jamulus_client_udp_port
        ),
        process_route_probe: CoreAudioProcessRouteProbe | None = None,
        home: Path | None = None,
        physical_route_certified: bool | None = None,
    ) -> None:
        self._platform = str(platform or sys.platform).lower()
        # Route authority is earned on the musician's own machine, never
        # asserted by a constant.  ``None`` (production) means "prove it here"
        # via :meth:`_route_certified`; an explicit boolean is a test-only
        # override so focused tests can pin either state.  There is still no
        # environment variable, setting, CLI flag, or UI action that can grant
        # authority the hardware has not demonstrated.
        if physical_route_certified is not None and not isinstance(
            physical_route_certified, bool
        ):
            raise TypeError("physical_route_certified must be a boolean")
        self._physical_route_certified_override = physical_route_certified
        if scanner is None:
            from core.coreaudio_devices import scan_coreaudio_devices

            scanner = scan_coreaudio_devices
        self._scanner = scanner
        self._sounddevice = sounddevice_module
        self._version_probe = version_probe
        self._headless_client_probe = headless_client_probe
        self._popen_factory = popen_factory
        self._port_allocator = port_allocator
        self._rpc_factory = rpc_factory or (
            lambda port, secret: _ReferenceRpcControl(port, secret)
        )
        self._udp_port_resolver = udp_port_resolver
        self._process_route_probe = (
            process_route_probe
            if process_route_probe is not None
            else CoreAudioProcessRouteProbe(platform_name=self._platform)
        )
        self._home = Path.home() if home is None else Path(home)
        self._lock = threading.RLock()
        self._active: _MacReferenceSession | None = None
        self._pending_cleanup: _PendingReferenceCleanup | None = None

    def capability(
        self, audience_bridge_active: bool = False
    ) -> ReferenceTrackCapability:
        if not self._platform.startswith("darwin"):
            return _UnavailableReferenceBackend(self._platform).capability()
        certified, route_error = self._route_certification()
        if not certified:
            return self._uncertified_capability(route_error)
        with self._lock:
            if self._pending_cleanup is not None:
                return ReferenceTrackCapability(
                    False,
                    "macos",
                    "A previous Reference Track startup stopped, but private "
                    "cleanup is still pending. Choose Play again to retry cleanup.",
                    backend="blackhole",
                    reason_code="cleanup_pending",
                )
        if audience_bridge_active:
            return ReferenceTrackCapability(
                False,
                "macos",
                "Reference Track can't share BlackHole with the Webex audience "
                "bridge. Switch Webex to talkback or video-only first.",
                backend="blackhole",
                reason_code="audience_bridge_conflict",
            )
        live_route_error = self._process_route_probe.capability_error()
        if live_route_error:
            return ReferenceTrackCapability(
                False,
                "macos",
                live_route_error,
                backend="blackhole",
                reason_code="live_route_unavailable",
            )
        try:
            route = self._resolve_route()
        except ReferenceTrackError as exc:
            return ReferenceTrackCapability(
                False,
                "macos",
                str(exc),
                backend="blackhole",
                reason_code="blackhole_unavailable",
            )
        return ReferenceTrackCapability(
            True,
            "macos",
            "BlackHole is ready at 48 kHz. WebJam will verify the primary "
            "Jamulus process's live route before playback.",
            route.name,
            backend="blackhole",
            reason_code="ready",
        )

    def prepare(
        self, context: ReferenceTrackLaunchContext
    ) -> ReferenceAudioBridgeSession:
        # Do not rely on a prior capability check.  The mutation boundary has
        # its own release lock so alternate callers cannot launch the backing
        # client or open BlackHole from an uncertified production backend.
        certified, route_error = self._route_certification()
        if not certified:
            raise ReferenceTrackError(route_error or _UNCERTIFIED_ROUTE_DETAIL)
        if context.audience_bridge_active:
            raise ReferenceTrackError(self.capability(True).detail)
        if not self._platform.startswith("darwin"):
            raise ReferenceTrackError(self.capability().detail)
        with self._lock:
            self._retry_pending_cleanup()
            if self._active is not None:
                if not self._active.health_error():
                    raise ReferenceTrackError(
                        "A Reference Track route is already active."
                    )
                self._active.stop()
                self._active = None

            route = self._resolve_route()
            primary_route = self._prove_primary_route(context, route)
            binary = Path(context.jamulus_binary).expanduser()
            if (
                not binary.is_file()
                or not os.access(binary, os.X_OK)
                or self._version_probe(str(binary)) != _PINNED_JAMULUS_VERSION
            ):
                raise ReferenceTrackError(
                    "Reference Track needs the included Jamulus 3.12.2 component."
                )
            if not self._headless_client_probe(str(binary)):
                raise ReferenceTrackError(
                    "Reference Track needs a packaged headless Jamulus client. "
                    "This build's interactive Jamulus client cannot prove zero "
                    "return faders while hidden."
                )
            server = self._normalized_endpoint(context.server_address)
            excluded = {
                int(context.primary_udp_port),
                int(context.primary_rpc_port),
            }
            udp_port = self._port_allocator("udp", excluded)
            if (
                isinstance(udp_port, bool)
                or not 1 <= int(udp_port) <= JAMULUS_CLIENT_MAX_BASE_PORT
            ):
                raise ReferenceTrackError(
                    "Reference Track couldn't reserve a safe Jamulus audio port."
                )
            udp_port = int(udp_port)
            excluded.add(udp_port)
            rpc_port = self._port_allocator("tcp", excluded)
            if (
                isinstance(rpc_port, bool)
                or not 1 <= int(rpc_port) <= 65_535
            ):
                raise ReferenceTrackError(
                    "Reference Track couldn't reserve a separate control port."
                )
            rpc_port = int(rpc_port)
            excluded.add(rpc_port)
            if len({udp_port, rpc_port, *excluded}) < 4:
                raise ReferenceTrackError(
                    "Reference Track couldn't reserve separate local ports."
                )

            session = self._prepare_session(
                route=route,
                binary=str(binary),
                server=server,
                udp_port=udp_port,
                rpc_port=rpc_port,
                context=context,
                primary_route=primary_route,
            )
            self._active = session
            return session

    def _retry_pending_cleanup(self) -> None:
        pending = self._pending_cleanup
        if pending is None:
            return
        if not pending.retry():
            raise ReferenceTrackError(
                "A previous Reference Track startup stopped, but its private "
                "cleanup is still pending. Resolve the reported file or process "
                "issue, then choose Play again."
            )
        self._pending_cleanup = None

    def retry_cleanup(self) -> None:
        """Retry a failed startup teardown without recreating WebJam."""

        with self._lock:
            self._retry_pending_cleanup()

    def _route_certification(self) -> tuple[bool, str]:
        """Decide whether this machine has proven the isolated route.

        Returns ``(certified, reason)`` where ``reason`` is empty when the
        route is proven and otherwise carries the exact, musician-readable
        cause from :meth:`_resolve_route`.

        An explicit constructor override wins so focused tests can pin either
        state.  Otherwise WebJam proves the prerequisite the route actually
        depends on: an official BlackHole device at 48 kHz with enough
        channels to keep the song and the return mix physically separate.
        Machines without it stay locked, so this remains fail-closed — what
        changed is that hardware which *does* satisfy the requirement is no
        longer refused by a constant nothing could set.

        Only read-only CoreAudio inspection happens here.  The subprocess,
        file, and PortAudio boundaries stay in :meth:`prepare`.
        """

        override = self._physical_route_certified_override
        if override is not None:
            return override, "" if override else _UNCERTIFIED_ROUTE_DETAIL
        if not self._platform.startswith("darwin"):
            return False, _UNCERTIFIED_ROUTE_DETAIL
        try:
            self._resolve_route()
        except ReferenceTrackError as exc:
            return False, str(exc)
        return True, ""

    def _uncertified_capability(
        self, route_error: str = ""
    ) -> ReferenceTrackCapability:
        # Name the prerequisite the musician can actually act on. Falling back
        # to the static detail keeps non-macOS and overridden paths truthful.
        return ReferenceTrackCapability(
            False,
            "macos",
            route_error or _UNCERTIFIED_ROUTE_DETAIL,
            backend="blackhole",
            reason_code="physical_certification_required",
        )

    def _prove_primary_route(
        self,
        context: ReferenceTrackLaunchContext,
        route: _BlackHoleRoute,
    ) -> CoreAudioProcessRouteSnapshot:
        try:
            scan = self._scanner()
        except Exception as exc:  # noqa: BLE001 - native hot-plug boundary
            raise ReferenceTrackError(
                "Reference Track couldn't read a fresh CoreAudio device snapshot."
            ) from exc
        try:
            current_route = self._resolve_route_from_scan(scan)
        except ReferenceTrackError:
            raise ReferenceTrackError(
                "Reference Track stopped because its BlackHole route changed."
            ) from None
        try:
            proof = self._process_route_probe.snapshot(
                context.primary_process_id, scan
            )
        except CoreAudioProcessRouteError as exc:
            raise ReferenceTrackError(str(exc)) from None
        if current_route != route:
            raise ReferenceTrackError(
                "Reference Track stopped because its BlackHole route changed."
            )

        live_devices = (proof.input_device, proof.output_device)
        if any(
            device.object_id == route.object_id
            or device.uid == route.uid
            or "blackhole" in device.name.casefold()
            for device in live_devices
        ):
            raise ReferenceTrackError(
                "Reference Track can't start while the primary Jamulus client "
                "is using BlackHole."
            )
        expected_names = (
            str(context.primary_input_device_name or "").strip(),
            str(context.primary_output_device_name or "").strip(),
        )
        live_names = (proof.input_device.name, proof.output_device.name)
        if any(
            expected and expected.casefold() != live.casefold()
            for expected, live in zip(expected_names, live_names, strict=True)
        ):
            raise ReferenceTrackError(
                "The primary Jamulus live route does not match its current "
                "launch profile. Reconnect band audio, then try again."
            )
        return proof

    def _prove_owned_reference_route(
        self,
        process: subprocess.Popen,
        route: _BlackHoleRoute,
    ) -> CoreAudioProcessRouteSnapshot:
        try:
            pid = int(process.pid)
        except (AttributeError, TypeError, ValueError):
            raise ReferenceTrackError(
                "Reference Track couldn't identify its owned Jamulus client."
            ) from None
        try:
            scan = self._scanner()
        except Exception as exc:  # noqa: BLE001 - native hot-plug boundary
            raise ReferenceTrackError(
                "Reference Track couldn't read a fresh CoreAudio device snapshot."
            ) from exc
        try:
            current_route = self._resolve_route_from_scan(scan)
            proof = self._process_route_probe.snapshot(pid, scan)
        except (ReferenceTrackError, CoreAudioProcessRouteError) as exc:
            raise ReferenceTrackError(
                "Reference Track couldn't prove its owned Jamulus client's "
                "live BlackHole route."
            ) from exc
        if current_route != route:
            raise ReferenceTrackError(
                "Reference Track stopped because its BlackHole route changed."
            )
        for device in (proof.input_device, proof.output_device):
            if (
                device.uid != route.uid
                or device.object_id != route.object_id
                or device.name != route.name
            ):
                raise ReferenceTrackError(
                    "Reference Track stopped because its owned Jamulus client "
                    "did not use the exact isolated BlackHole route."
                )
        return proof

    def _prepare_session(
        self,
        *,
        route: _BlackHoleRoute,
        binary: str,
        server: str,
        udp_port: int,
        rpc_port: int,
        context: ReferenceTrackLaunchContext,
        primary_route: CoreAudioProcessRouteSnapshot,
    ) -> "_MacReferenceSession":
        config_dir = reference_track_runtime_directory(self._home)
        profile = AudioRouteProfile(
            platform=AudioRoutePlatform.MACOS_COREAUDIO,
            input_device_id=route.uid,
            output_device_id=route.uid,
            input_device_name=route.name,
            output_device_name=route.name,
            input_channels=(0, 1),
            output_channels=(2, 3),
            channel_mode=JamulusChannelMode.STEREO,
            sample_rate=REFERENCE_SAMPLE_RATE,
            requested_buffer_frames=128,
            device_generation=route.generation,
            app_version=self._app_version(),
            confirmation_level=RouteConfirmationLevel.PREFLIGHTED,
            last_verified_at="runtime",
            verification_method="coreaudio_blackhole_split_channels",
        )
        adapter = Jamulus3122AudioRouteAdapter()
        owned_files: _ReferencePrivateFiles | None = None
        secret_value = ""
        ownership_generation = ""
        process: subprocess.Popen | None = None
        rpc: _ReferenceRpcControl | None = None
        route_lease = _claim_blackhole_route(route.uid, home=self._home)
        try:
            owned_files = _ReferencePrivateFiles.open(
                config_dir,
                home=self._home,
            )
            secret_value = secrets.token_urlsafe(32)
            # Generate every fallible ownership input before process spawn and
            # within the cleanup guard. A failed entropy source must not leave
            # a live second Jamulus client outside backend ownership.
            ownership_generation = secrets.token_hex(16)
            owned_files.provision(adapter, profile, secret=secret_value)
            sounddevice_module = self._load_sounddevice()
            rpc = self._rpc_factory(rpc_port, secret_value)
            command = [
                binary,
                "--nogui",
                "--mutemyown",
                "--inifile",
                str(owned_files.profile_path),
                "--clientname",
                REFERENCE_PARTICIPANT_NAME,
                "--connect",
                server,
                "--port",
                str(udp_port),
                "--jsonrpcbindip",
                "127.0.0.1",
                "--jsonrpcport",
                str(rpc_port),
                "--jsonrpcsecretfile",
                str(owned_files.secret_path),
            ]
            if (
                not owned_files.path_matches_directory()
                or not owned_files.launch_files_are_exact()
            ):
                raise ReferenceTrackError(_PRIVATE_LAUNCH_CHANGED)
            process = self._popen_factory(
                command,
                cwd=str(config_dir),
                env=self._child_environment(binary),
                pass_fds=route_lease.child_pass_fds,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if (
                not owned_files.path_matches_directory()
                or not owned_files.launch_files_are_exact()
            ):
                raise ReferenceTrackError(_PRIVATE_LAUNCH_CHANGED)
        except Exception as exc:
            pending = _PendingReferenceCleanup(
                process=process,
                rpc=rpc,
                files=owned_files,
                route_lease=route_lease,
            )
            # Publish the recovery owner before calling any fallible teardown
            # boundary. Cleanup exceptions must never strand the descriptor,
            # process, or lifecycle lock in unreachable locals.
            self._pending_cleanup = pending
            cleaned = pending.retry()
            try:
                terminated = process is None or process.poll() is not None
            except Exception:  # noqa: BLE001 - subprocess evidence boundary
                terminated = False
            if cleaned:
                self._pending_cleanup = None
            secret_value = ""
            if not terminated:
                raise ReferenceTrackError(
                    "Reference Track couldn't confirm that its owned Jamulus "
                    "client stopped after startup failed."
                ) from None
            if not cleaned:
                raise ReferenceTrackError(
                    "Reference Track stopped after startup failed, but its "
                    "private cleanup could not be confirmed."
                ) from None
            if (
                isinstance(exc, ReferenceTrackError)
                and str(exc) == _PRIVATE_LAUNCH_CHANGED
            ):
                raise ReferenceTrackError(_PRIVATE_LAUNCH_CHANGED) from None
            raise ReferenceTrackError(
                "WebJam couldn't prepare a safe Reference Track route."
            ) from None
        secret_value = ""
        assert owned_files is not None

        def prove_routes() -> CoreAudioProcessRouteSnapshot:
            return self._prove_primary_route(context, route)

        def prove_owned_route() -> CoreAudioProcessRouteSnapshot:
            return self._prove_owned_reference_route(process, route)

        return _MacReferenceSession(
            route=route,
            process=process,
            udp_port=udp_port,
            udp_port_resolver=self._udp_port_resolver,
            ownership_generation=ownership_generation,
            rpc=rpc,
            sounddevice_module=sounddevice_module,
            route_proof=primary_route,
            prove_routes=prove_routes,
            prove_owned_route=prove_owned_route,
            owned_files=owned_files,
            route_lease=route_lease,
            on_stopped=self._session_stopped,
        )

    def _resolve_route(self) -> _BlackHoleRoute:
        try:
            scan = self._scanner()
        except Exception as exc:  # noqa: BLE001
            raise ReferenceTrackError(
                "WebJam couldn't inspect BlackHole safely."
            ) from exc
        if not isinstance(scan, CoreAudioScan) or scan.error:
            raise ReferenceTrackError("WebJam couldn't inspect BlackHole safely.")
        return self._resolve_route_from_scan(scan)

    def _resolve_route_from_scan(self, scan: CoreAudioScan) -> _BlackHoleRoute:
        if not isinstance(scan, CoreAudioScan) or scan.error:
            raise ReferenceTrackError("WebJam couldn't inspect BlackHole safely.")
        candidates = []
        for device in scan.devices:
            contract = _OFFICIAL_BLACKHOLE_ROUTES.get(device.uid)
            if contract is None:
                continue
            expected_name, expected_channels = contract
            if (
                device.name == expected_name
                and device.input_channels == expected_channels
                and device.output_channels == expected_channels
                and device.nominal_rate is not None
                and abs(float(device.nominal_rate) - REFERENCE_SAMPLE_RATE) < 0.5
            ):
                candidates.append(device)
        if not candidates:
            raise ReferenceTrackError(
                "Reference Track needs the official BlackHole 16ch or 64ch "
                "device with its exact UID, name, channel count, and 48 kHz "
                "rate. BlackHole 2ch cannot isolate the return; renamed/custom "
                "devices and aggregates such as WebJam Bridge are not accepted "
                "as the owned route."
            )
        candidates.sort(key=lambda value: (value.input_channels, value.name, value.uid))
        chosen = candidates[0]
        duplicate_names = [
            device for device in scan.devices if device.name == chosen.name
        ]
        if len(duplicate_names) != 1:
            raise ReferenceTrackError(
                "More than one CoreAudio device has the selected BlackHole name. "
                "Rename or remove the duplicate before using Reference Track."
            )
        sd = self._load_sounddevice()
        try:
            devices = tuple(sd.query_devices())
        except Exception as exc:
            raise ReferenceTrackError(
                "WebJam couldn't open BlackHole for Reference Track."
            ) from exc
        matches = [
            (index, raw)
            for index, raw in enumerate(devices)
            if str(raw.get("name", "")) == chosen.name
            and int(raw.get("max_input_channels", 0)) == chosen.input_channels
            and int(raw.get("max_output_channels", 0)) == chosen.output_channels
            and abs(
                float(raw.get("default_samplerate", 0.0))
                - REFERENCE_SAMPLE_RATE
            )
            < 0.5
        ]
        if len(matches) != 1:
            raise ReferenceTrackError(
                "WebJam couldn't match one unambiguous 48-kHz BlackHole output."
            )
        generation_payload = "|".join(
            f"{device.uid}:{device.name}:{device.input_channels}:"
            f"{device.output_channels}:{device.nominal_rate}"
            for device in sorted(scan.devices, key=lambda item: item.uid)
        ).encode("utf-8")
        return _BlackHoleRoute(
            uid=chosen.uid,
            name=chosen.name,
            object_id=chosen.object_id,
            sounddevice_index=matches[0][0],
            channels=min(chosen.input_channels, chosen.output_channels),
            generation=hashlib.sha256(generation_payload).hexdigest(),
        )

    def _load_sounddevice(self):
        if self._sounddevice is not None:
            return self._sounddevice
        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise ReferenceTrackError(
                "Reference Track audio support is unavailable in this build."
            ) from exc
        self._sounddevice = sd
        return sd

    @staticmethod
    def _normalized_endpoint(value: str) -> str:
        try:
            endpoint = parse_jamulus_endpoint(value)
        except (TypeError, ValueError) as exc:
            raise ReferenceTrackError(
                "Reference Track needs the current Jamulus server address."
            ) from exc
        host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
        return f"{host}:{endpoint.port}"

    @staticmethod
    def _wait_for_rpc(
        process: subprocess.Popen, rpc: _ReferenceRpcControl
    ) -> None:
        deadline = time.monotonic() + _RPC_READY_TIMEOUT_S
        last_error: ReferenceTrackError | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ReferenceTrackError(
                    "The Reference Track Jamulus client exited during startup."
                )
            try:
                rpc.connect()
                return
            except ReferenceTrackError as exc:
                last_error = exc
                time.sleep(0.15)
        raise ReferenceTrackError(
            "Reference Track's private Jamulus control did not become ready."
        ) from last_error

    @staticmethod
    def _child_environment(executable: str | Path) -> dict[str, str]:
        environment = sanitized_jamulus_child_environment(
            os.environ,
            platform_name=sys.platform,
            executable=executable,
        )
        # Inherited Qt controls were removed by the shared native-child
        # boundary. Add only this reviewed literal logging rule.
        environment["QT_LOGGING_RULES"] = "default.warning=false"
        return environment

    @staticmethod
    def _app_version() -> str:
        try:
            from webjam_qt import __version__

            return str(__version__)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _terminate_process(process: subprocess.Popen | None) -> bool:
        if process is None:
            return True

        def stopped() -> bool:
            try:
                return process.poll() is not None
            except Exception:  # noqa: BLE001 - unknown state fails closed
                return False

        try:
            if stopped():
                return True
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        except Exception:  # noqa: BLE001
            return stopped()
        return stopped()

    def _session_stopped(self, session: "_MacReferenceSession") -> None:
        with self._lock:
            if self._active is session:
                self._active = None


class _MacReferenceSession:
    def __init__(
        self,
        *,
        route: _BlackHoleRoute,
        process: subprocess.Popen,
        udp_port: int,
        udp_port_resolver: Callable[[int, int], int],
        ownership_generation: str,
        rpc: _ReferenceRpcControl,
        sounddevice_module: object,
        route_proof: CoreAudioProcessRouteSnapshot,
        prove_routes: Callable[[], CoreAudioProcessRouteSnapshot],
        prove_owned_route: Callable[[], CoreAudioProcessRouteSnapshot],
        owned_files: _ReferencePrivateFiles,
        route_lease: _BlackHoleRouteLease,
        on_stopped: Callable[["_MacReferenceSession"], None],
    ) -> None:
        self._route = route
        self._process = process
        self._udp_port = int(udp_port)
        self._udp_port_resolver = udp_port_resolver
        self._ownership_generation = str(ownership_generation)
        self._rpc = rpc
        self._sounddevice = sounddevice_module
        self._route_proof = route_proof
        self._prove_routes = prove_routes
        self._prove_owned_route = prove_owned_route
        self._owned_route_proof: CoreAudioProcessRouteSnapshot | None = None
        self._owned_files = owned_files
        self._route_lease = route_lease
        self._on_stopped = on_stopped
        self._lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitor: threading.Thread | None = None
        self._stream = None
        self._pull_into: Callable[[np.ndarray], int] | None = None
        self._health_error = ""
        self._realtime_fault = ""
        self._callback_faults = 0
        self._safety_epoch = 0
        self._teardown_started = False
        self._cleanup_complete = False
        self._control_ready = False
        self._combined_route_authorized = False
        self._route_proof_monotonic = 0.0
        self._route_proof_wall = 0.0

    @property
    def route_name(self) -> str:
        return self._route.name

    def recording_ownership_claim(self) -> ReferenceTrackOwnershipClaim | None:
        """Bind recorder evidence to this exact live private process."""

        with self._lock:
            if (
                self._teardown_started
                or self._cleanup_complete
                or self._health_error
                or self._realtime_fault
                or not self._control_ready
                or not self._combined_route_authorized
            ):
                return None
        try:
            if self._process.poll() is not None:
                return None
            process_id = int(self._process.pid)
            # Jamulus 3.12.x deliberately randomizes its client bind away from
            # the configured --port base.  Resolve the real socket from the
            # exact child process; the configured base is never ownership.
            resolved_port = self._udp_port_resolver(process_id, self._udp_port)
            if isinstance(resolved_port, bool):
                return None
            udp_port = int(resolved_port)
            if not self._udp_port <= udp_port <= self._udp_port + 199:
                return None
            if self._process.poll() is not None:
                return None
        except (AttributeError, TypeError, ValueError):
            return None
        except Exception:  # noqa: BLE001 - native socket proof fails absent
            return None
        with self._lock:
            if (
                self._teardown_started
                or self._cleanup_complete
                or self._health_error
                or self._realtime_fault
                or not self._control_ready
                or not self._combined_route_authorized
            ):
                return None
        return ReferenceTrackOwnershipClaim(
            udp_port=udp_port,
            process_id=process_id,
            generation=self._ownership_generation,
        )

    def start(self, pull_into: Callable[[np.ndarray], int]) -> None:
        if not callable(pull_into):
            raise TypeError("pull_into must be callable")
        with self._lock:
            if self._teardown_started:
                raise ReferenceTrackError("Reference Track route was already stopped.")
            if self._stream is not None:
                return
            self._pull_into = pull_into
        stream = None
        try:
            self._recheck_primary_route()
            self._prepare_control()
            # RPC startup can take several seconds. A proof gathered before
            # that wait is stale and cannot authorize opening the song stream.
            self._recheck_routes()
            stream = self._sounddevice.OutputStream(
                device=self._route.sounddevice_index,
                samplerate=REFERENCE_SAMPLE_RATE,
                channels=2,
                dtype="float32",
                blocksize=REFERENCE_BLOCK_FRAMES,
                latency="low",
                callback=self._audio_callback,
            )
            self._prove_open_stream(stream)
            self._combined_route_authorized = False
            stream.start()
            self._prove_open_stream(stream)
            # Catch a device switch racing the stream open before any decoded
            # song frames are permitted through the callback.
            self._recheck_routes()
        except Exception as exc:  # noqa: BLE001
            close_error = ""
            if stream is not None:
                close_error = self._close_stream_with_proof(stream)
                if close_error:
                    with self._lock:
                        self._stream = stream
                        self._pull_into = None
                        self._teardown_started = True
                        self._advance_safety_epoch()
                        self._stop_event.set()
            message = (
                close_error
                or (
                    str(exc)
                    if isinstance(exc, ReferenceTrackError)
                    else "WebJam couldn't open the isolated BlackHole song channels."
                )
            )
            self._set_health_error(message)
            raise ReferenceTrackError(message) from None
        with self._lock:
            self._stream = stream
        self._monitor = threading.Thread(
            target=self._monitor_safety,
            name="WebJam reference-track safety monitor",
            daemon=True,
        )
        self._monitor.start()

    def _prove_open_stream(self, stream: object) -> None:
        """Verify PortAudio opened the exact fixed-format route requested."""

        try:
            actual_device = int(getattr(stream, "device"))
            actual_rate = float(getattr(stream, "samplerate"))
            actual_channels = int(getattr(stream, "channels"))
            actual_blocksize = int(getattr(stream, "blocksize"))
            actual_dtype = np.dtype(getattr(stream, "dtype"))
        except (AttributeError, TypeError, ValueError):
            raise ReferenceTrackError(
                "Reference Track couldn't prove the opened BlackHole stream."
            ) from None
        if (
            actual_device != self._route.sounddevice_index
            or abs(actual_rate - REFERENCE_SAMPLE_RATE) >= 0.5
            or actual_channels != 2
            or actual_blocksize != REFERENCE_BLOCK_FRAMES
            or actual_dtype != np.dtype(np.float32)
        ):
            raise ReferenceTrackError(
                "Reference Track stopped because PortAudio opened a different "
                "device or stream format."
            )

    def _prepare_control(self) -> None:
        if self._control_ready:
            return
        MacOSBlackHoleReferenceBackend._wait_for_rpc(self._process, self._rpc)
        deadline = time.monotonic() + _RPC_READY_TIMEOUT_S
        while True:
            if self._process.poll() is not None:
                raise ReferenceTrackError(
                    "The Reference Track Jamulus client exited during startup."
                )
            try:
                self._rpc.prove_all_faders_zero()
                self._control_ready = True
                return
            except ReferenceTrackError:
                if time.monotonic() >= deadline:
                    raise ReferenceTrackError(
                        "Reference Track couldn't prove a connected, zero-return "
                        "Jamulus mix."
                    )
                time.sleep(0.15)

    def health_error(self) -> str:
        with self._lock:
            return self._health_error or self._realtime_fault

    def realtime_stats(self) -> dict[str, int]:
        """Return bounded, path-free callback fault evidence."""

        return {
            "callback_faults": min(
                REFERENCE_MAX_DIAGNOSTIC_COUNTER,
                max(0, int(self._callback_faults)),
            )
        }

    def stop(self) -> None:
        with self._stop_lock:
            with self._lock:
                if self._cleanup_complete:
                    return
                if not self._teardown_started:
                    self._teardown_started = True
                    self._advance_safety_epoch()
                self._stop_event.set()
                stream = self._stream
                self._pull_into = None
            if stream is not None:
                close_error = self._close_stream_with_proof(stream)
                if close_error:
                    self._set_health_error(close_error)
                    raise ReferenceTrackError(close_error)
                with self._lock:
                    if self._stream is stream:
                        self._stream = None
            monitor = self._monitor
            if monitor is not None and monitor is not threading.current_thread():
                monitor.join(timeout=2.0)
            self._rpc.close()
            if not MacOSBlackHoleReferenceBackend._terminate_process(self._process):
                message = (
                    "Reference Track couldn't confirm that its owned Jamulus "
                    "client stopped."
                )
                self._set_health_error(message)
                raise ReferenceTrackError(message)
            if not self._owned_files.cleanup():
                message = (
                    "Reference Track stopped, but its private profile and control "
                    "cleanup could not be confirmed."
                )
                self._set_health_error(message)
                raise ReferenceTrackError(message)
            with self._lock:
                self._cleanup_complete = True
            self._route_lease.release()
            self._on_stopped(self)

    @staticmethod
    def _close_stream_with_proof(stream: object) -> str:
        """Stop then close one stream; only ``closed is True`` proves release."""

        try:
            stream.stop()
        except Exception:  # noqa: BLE001 - close can recover a stop failure
            pass
        try:
            stream.close()
        except Exception:  # noqa: BLE001 - retain owner for a later retry
            return (
                "Reference Track couldn't close its BlackHole audio stream. "
                "Choose Stop again; its route remains reserved."
            )
        try:
            closed = getattr(stream, "closed")
        except Exception:  # noqa: BLE001 - native property boundary
            closed = False
        if closed is not True:
            return (
                "Reference Track couldn't prove that its BlackHole audio stream "
                "closed. Choose Stop again; its route remains reserved."
            )
        return ""

    def _audio_callback(self, outdata, frames, _time_info, status) -> None:
        outdata.fill(0.0)
        if status:
            self._latch_realtime_fault(
                "Reference Track's BlackHole stream reported an audio fault."
            )
            return
        # The callback reads single-object latches only. It never acquires the
        # session mutex or waits behind route/RPC/teardown work.
        safety_epoch = self._safety_epoch
        pull_into = self._pull_into
        unhealthy = bool(
            self._health_error
            or self._realtime_fault
            or self._teardown_started
            or not self._combined_route_authorized
        )
        proof_monotonic = self._route_proof_monotonic
        proof_wall = self._route_proof_wall
        now_monotonic = time.monotonic()
        now_wall = time.time()
        proof_stale = (
            proof_monotonic <= 0.0
            or proof_wall <= 0.0
            or max(
                max(0.0, now_monotonic - proof_monotonic),
                max(0.0, now_wall - proof_wall),
            )
            > _ROUTE_PROOF_MAX_AGE_SECONDS
        )
        if proof_stale:
            self._latch_realtime_fault(
                "Reference Track stopped because its live primary Jamulus "
                "route proof became stale."
            )
            return
        if unhealthy or pull_into is None:
            return
        try:
            amount = int(frames)
            if (
                not 1 <= amount <= REFERENCE_MAX_DECODE_FRAMES
                or not isinstance(outdata, np.ndarray)
                or outdata.dtype != np.float32
                or outdata.shape != (amount, 2)
            ):
                raise ValueError("unexpected callback size")
            delivered = pull_into(outdata)
            if (
                isinstance(delivered, bool)
                or not isinstance(delivered, int)
                or not 0 <= delivered <= amount
            ):
                raise ValueError("invalid callback delivery")
            if (
                self._safety_epoch != safety_epoch
                or self._health_error
                or self._realtime_fault
                or self._teardown_started
                or not self._combined_route_authorized
            ):
                # Route/fader monitors and Stop publish safety invalidation
                # without waiting for this callback. Never release a block
                # copied under superseded safety authority.
                outdata.fill(0.0)
        except Exception:  # noqa: BLE001 - real-time boundary
            outdata.fill(0.0)
            self._latch_realtime_fault(
                "Reference Track stopped because its bounded audio stream failed."
            )

    def _monitor_safety(self) -> None:
        next_route_probe = 0.0
        while not self._stop_event.wait(_FADER_RECHECK_SECONDS):
            if self._realtime_fault:
                return
            if self._process.poll() is not None:
                self._set_health_error(
                    "Reference Track's owned Jamulus client stopped."
                )
                return
            try:
                self._rpc.prove_all_faders_zero()
            except ReferenceTrackError:
                self._set_health_error(
                    "Reference Track stopped because zero return faders could "
                    "no longer be proved."
                )
                return
            now = time.monotonic()
            if now >= next_route_probe:
                next_route_probe = now + _ROUTE_RECHECK_SECONDS
                try:
                    self._recheck_routes()
                except ReferenceTrackError as exc:
                    self._set_health_error(
                        str(exc)
                        or (
                            "Reference Track stopped because its live audio "
                            "route could no longer be proved."
                        )
                    )
                    return

    def _recheck_routes(self) -> None:
        # Keep the last combined proof authoritative only for its short,
        # bounded freshness window. Publish a new timestamp after both routes
        # pass; the monitor invalidates authority immediately on any failure.
        self._recheck_primary_route()
        try:
            owned = self._prove_owned_route()
        except ReferenceTrackError:
            raise
        except Exception as exc:  # noqa: BLE001 - native proof boundary
            raise ReferenceTrackError(
                "Reference Track stopped because its owned Jamulus live route "
                "could no longer be proved."
            ) from exc
        with self._lock:
            if self._owned_route_proof is None:
                self._owned_route_proof = owned
            elif owned != self._owned_route_proof:
                raise ReferenceTrackError(
                    "Reference Track stopped because its owned Jamulus live "
                    "audio route changed."
                )
            if self._teardown_started:
                raise ReferenceTrackError(
                    "Reference Track route was already stopped."
                )
            self._route_proof_monotonic = time.monotonic()
            self._route_proof_wall = time.time()
            self._combined_route_authorized = True

    def _recheck_primary_route(self) -> None:
        try:
            current = self._prove_routes()
        except ReferenceTrackError:
            raise
        except Exception as exc:  # noqa: BLE001 - native proof boundary
            raise ReferenceTrackError(
                "Reference Track stopped because its live audio route could "
                "no longer be proved."
            ) from exc
        if current != self._route_proof:
            raise ReferenceTrackError(
                "Reference Track stopped because the primary Jamulus live "
                "audio route changed."
            )
        with self._lock:
            if self._teardown_started:
                raise ReferenceTrackError(
                    "Reference Track route was already stopped."
                )

    def _set_health_error(self, message: str) -> None:
        with self._lock:
            if not self._health_error:
                self._health_error = str(message)
                self._combined_route_authorized = False
                self._advance_safety_epoch()

    def _latch_realtime_fault(self, message: str) -> None:
        """Latch one path-free callback fault without a mutex or side effect."""

        self._callback_faults = min(
            REFERENCE_MAX_DIAGNOSTIC_COUNTER,
            self._callback_faults + 1,
        )
        if not self._realtime_fault:
            self._realtime_fault = str(message)
            self._combined_route_authorized = False
            self._advance_safety_epoch()

    def _advance_safety_epoch(self) -> None:
        """Invalidate in-flight callback authority without locking or waiting."""

        self._safety_epoch = min(
            REFERENCE_MAX_DIAGNOSTIC_COUNTER,
            self._safety_epoch + 1,
        )


def create_reference_audio_backend():
    """Return the honest production backend for the current platform."""

    if sys.platform == "darwin":
        return MacOSBlackHoleReferenceBackend()
    return _UnavailableReferenceBackend(sys.platform)


__all__ = [
    "MacOSBlackHoleReferenceBackend",
    "REFERENCE_PARTICIPANT_NAME",
    "REFERENCE_PROFILE_FILENAME",
    "REFERENCE_SECRET_FILENAME",
    "create_reference_audio_backend",
    "reference_track_runtime_directory",
]
