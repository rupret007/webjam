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
from typing import Union


def atomic_write_text(
    path: Union[str, Path],
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
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

        if mode is not None:
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
