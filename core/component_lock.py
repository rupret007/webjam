"""Small cross-platform inter-process lock for the component updater."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import time


class ComponentLockError(RuntimeError):
    pass


class ComponentLockTimeout(ComponentLockError):
    pass


RUNTIME_ACTIVE_LOCK_NAME = ".runtime-active.lock"
"""Shared updater/runtime exclusion lock below the component-store root."""


class InterProcessComponentLock:
    """Advisory exclusive file lock with bounded waiting.

    The descriptor remains open for the lifetime of the context manager.
    Lock files are never used as truth-bearing state; they only serialize
    atomic state transitions.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = 5.0,
        poll_interval: float = 0.05,
    ) -> None:
        self.path = Path(path)
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self._descriptor: int | None = None
        if self.timeout < 0 or self.timeout > 300:
            raise ValueError("lock timeout must be between 0 and 300 seconds")
        if self.poll_interval <= 0 or self.poll_interval > 1:
            raise ValueError("lock poll interval must be between 0 and 1 second")

    def __enter__(self) -> "InterProcessComponentLock":
        if self._descriptor is not None:
            raise ComponentLockError("component lock is not re-entrant")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ComponentLockError("component lock path cannot be a symlink")
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise ComponentLockError("could not open the component lock") from exc
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise ComponentLockError("component lock is not a regular file")
            if os.name == "nt" and details.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._try_lock(descriptor)
                    break
                except OSError as exc:
                    if not self._would_block(exc):
                        raise ComponentLockError(
                            "could not acquire the component lock"
                        ) from exc
                    if time.monotonic() >= deadline:
                        raise ComponentLockTimeout(
                            "timed out waiting for the component lock"
                        ) from exc
                    time.sleep(
                        min(self.poll_interval, max(0.0, deadline - time.monotonic()))
                    )
            self._descriptor = descriptor
            return self
        except Exception:
            os.close(descriptor)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        try:
            self._unlock(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _try_lock(descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)

    @staticmethod
    def _would_block(exc: OSError) -> bool:
        if os.name == "nt":
            return getattr(exc, "winerror", None) in {32, 33, 36} or exc.errno in {
                errno.EACCES,
                errno.EAGAIN,
            }
        return exc.errno in {errno.EACCES, errno.EAGAIN}


__all__ = [
    "ComponentLockError",
    "ComponentLockTimeout",
    "InterProcessComponentLock",
    "RUNTIME_ACTIVE_LOCK_NAME",
]
