"""
Atomic file-write helpers.

Most of WebJam's persistent state lives in user-home dotfiles
(``~/.webjam_config.json``, ``~/.webjam_mix.json``, ``~/.webjam_notes.md``,
``~/.webjam_session.json``).  A direct ``Path.write_text()`` is **not
atomic**: a crash mid-write can leave a half-written file that the next
launch fails to parse, corrupting the user's saved state.

``atomic_write_text()`` writes to a sibling ``*.tmp`` file in the same
directory, fsyncs it, then ``os.replace()``s onto the target — which is an
atomic rename on every supported OS.  On POSIX it also fsyncs the parent
directory after the rename, so a successful return includes the directory
entry needed to recover that replacement after power loss. Optional ``mode``
sets file permissions before the rename (use ``0o600`` for files containing
secrets).

Errors are surfaced to the caller; this module does not log or swallow.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(
    path: str | Path,
    data: bytes,
    *,
    mode: int | None = None,
) -> None:
    """Atomically replace ``path`` with exact bytes.

    This is the binary companion to :func:`atomic_write_text`.  It is used
    when recovery must preserve the byte-for-byte contents of a prior or
    corrupt metadata file instead of decoding and re-encoding it.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    _atomic_write(path, data, mode=mode)


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Atomically write ``text`` to ``path``.

    Args:
        path: Destination file path.
        text: Content to write.
        encoding: Text encoding (default UTF-8).
        mode: Optional POSIX file mode (e.g. 0o600 for secrets).  Set
            *before* the rename so the final file never exists with
            the umask-default mode.

    Raises:
        OSError: if the temp file cannot be created/written, or the
                 final rename fails.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    _atomic_write(path, text.encode(encoding), mode=mode)


def _atomic_write(
    path: str | Path,
    data: bytes,
    *,
    mode: int | None,
) -> None:
    """Write already-encoded bytes through one fsynced sibling replacement."""
    target = Path(path)
    parent = target.parent or Path(".")
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(parent),
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            with os.fdopen(fd, "wb") as f:
                if mode is not None and hasattr(os, "fchmod"):
                    # Apply private permissions to the already-open temporary
                    # inode.  A path-based chmod after close would leave a
                    # needless name-swap window before publication.
                    os.fchmod(f.fileno(), mode)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

        if mode is not None and not hasattr(os, "fchmod"):
            os.chmod(tmp_path, mode)

        # os.replace is atomic on POSIX and Windows (Python 3.3+).
        os.replace(tmp_path, target)
        _fsync_parent_directory(parent)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _fsync_parent_directory(parent: Path) -> None:
    """Commit a successful POSIX rename's directory entry before returning.

    Windows does not expose a portable directory-handle fsync through
    ``os.open``. Its replacement still stays atomic, while the POSIX paths
    used for take/recovery metadata receive the stronger crash-durability
    guarantee.
    """
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
