"""Private, descriptor-backed streaming logs.

Native audio helpers can emit device names, network addresses, and other
diagnostic details.  These logs therefore must never be opened with the
process umask as their only protection.

On POSIX, publication is anchored to a verified parent-directory descriptor.
A fresh ``0600`` inode replaces an existing safe regular log, so no existing
file is truncated before its type, owner, and link count are known.  Symlinks,
hard links, unsafe parent directories, and changed directory entries fail
closed.  Other platforms use the same fresh-inode replacement pattern with
the strongest portable identity checks available.
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import TextIO

from core.secure_runtime import SecureRuntimeDirectory, SecureRuntimeError

_PRIVATE_FILE_MODE = 0o600


class PrivateLogError(OSError):
    """A path-free failure to establish a private diagnostic log."""


def _safe_leaf_name(path: Path) -> str:
    name = path.name
    if (
        not path.is_absolute()
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\0" in name
    ):
        raise PrivateLogError(
            "WebJam refused an unsafe diagnostic log location."
        )
    return name


def _owned_by_current_user(details: os.stat_result) -> bool:
    return not hasattr(os, "geteuid") or int(details.st_uid) == int(os.geteuid())


def _safe_existing_log(details: os.stat_result) -> bool:
    return bool(
        stat.S_ISREG(details.st_mode)
        and int(details.st_nlink) == 1
        and _owned_by_current_user(details)
    )


def _private_opened_log(details: os.stat_result) -> bool:
    return bool(
        _safe_existing_log(details)
        and stat.S_IMODE(details.st_mode) == _PRIVATE_FILE_MODE
    )


def _close_quietly(descriptor: int) -> None:
    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _open_private_posix_text_log(path: Path, name: str) -> TextIO:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise PrivateLogError(
            "WebJam cannot safely create its diagnostic log on this system."
        )

    parent_descriptor = -1
    descriptor = -1
    temporary_name: str | None = None
    created: os.stat_result | None = None
    published = False
    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | nofollow
            | directory
            | getattr(os, "O_CLOEXEC", 0),
        )
        parent_details = os.fstat(parent_descriptor)
        visible_parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent_details.st_mode)
            or not _owned_by_current_user(parent_details)
            or stat.S_IMODE(parent_details.st_mode) & 0o022
            or stat.S_ISLNK(visible_parent.st_mode)
            or not stat.S_ISDIR(visible_parent.st_mode)
            or int(visible_parent.st_dev) != int(parent_details.st_dev)
            or int(visible_parent.st_ino) != int(parent_details.st_ino)
        ):
            raise PrivateLogError(
                "WebJam refused an unsafe diagnostic log directory."
            )

        try:
            existing = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing = None
        if existing is not None and not _safe_existing_log(existing):
            raise PrivateLogError(
                "WebJam refused an unsafe existing diagnostic log."
            )

        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | nofollow
            | getattr(os, "O_CLOEXEC", 0)
        )
        for _attempt in range(32):
            candidate = f".{name}.{secrets.token_hex(12)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    flags,
                    _PRIVATE_FILE_MODE,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if descriptor < 0 or temporary_name is None:
            raise PrivateLogError(
                "WebJam could not allocate its private diagnostic log."
            )

        created = os.fstat(descriptor)
        if not _safe_existing_log(created):
            raise PrivateLogError(
                "WebJam could not verify its private diagnostic log."
            )
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        created = os.fstat(descriptor)
        if not _private_opened_log(created):
            raise PrivateLogError(
                "WebJam could not protect its diagnostic log."
            )

        current_parent = path.parent.lstat()
        if (
            stat.S_ISLNK(current_parent.st_mode)
            or not stat.S_ISDIR(current_parent.st_mode)
            or not _owned_by_current_user(current_parent)
            or stat.S_IMODE(current_parent.st_mode) & 0o022
            or int(current_parent.st_dev) != int(parent_details.st_dev)
            or int(current_parent.st_ino) != int(parent_details.st_ino)
        ):
            raise PrivateLogError(
                "WebJam refused a changed diagnostic log directory."
            )

        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        published = True
        visible = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        final = os.fstat(descriptor)
        final_parent = path.parent.lstat()
        if (
            not _private_opened_log(final)
            or not _private_opened_log(visible)
            or int(visible.st_dev) != int(final.st_dev)
            or int(visible.st_ino) != int(final.st_ino)
            or int(final.st_dev) != int(created.st_dev)
            or int(final.st_ino) != int(created.st_ino)
            or stat.S_ISLNK(final_parent.st_mode)
            or not stat.S_ISDIR(final_parent.st_mode)
            or not _owned_by_current_user(final_parent)
            or stat.S_IMODE(final_parent.st_mode) & 0o022
            or int(final_parent.st_dev) != int(parent_details.st_dev)
            or int(final_parent.st_ino) != int(parent_details.st_ino)
        ):
            raise PrivateLogError(
                "WebJam refused a changed private diagnostic log."
            )

        stream = os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            buffering=1,
        )
        descriptor = -1
        return stream
    except PrivateLogError:
        raise
    except (NotImplementedError, OSError):
        raise PrivateLogError(
            "WebJam could not establish its private diagnostic log."
        ) from None
    finally:
        if (
            not published
            and temporary_name is not None
            and parent_descriptor >= 0
            and created is not None
        ):
            try:
                visible = os.stat(
                    temporary_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    int(visible.st_dev) == int(created.st_dev)
                    and int(visible.st_ino) == int(created.st_ino)
                    and stat.S_ISREG(visible.st_mode)
                ):
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
            except OSError:
                pass
        _close_quietly(descriptor)
        _close_quietly(parent_descriptor)


def _open_private_portable_text_log(path: Path, name: str) -> TextIO:
    """Best portable fallback for platforms without POSIX dirfd semantics."""

    descriptor = -1
    temporary_path: Path | None = None
    published = False
    try:
        parent = path.parent
        parent_details = parent.lstat()
        if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(
            parent_details.st_mode
        ):
            raise PrivateLogError(
                "WebJam refused an unsafe diagnostic log directory."
            )
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and not _safe_existing_log(existing):
            raise PrivateLogError(
                "WebJam refused an unsafe existing diagnostic log."
            )

        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{name}.",
            suffix=".tmp",
            dir=str(parent),
        )
        temporary_path = Path(temporary)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        else:
            os.chmod(temporary_path, _PRIVATE_FILE_MODE)
        created = os.fstat(descriptor)
        if not _safe_existing_log(created):
            raise PrivateLogError(
                "WebJam could not verify its private diagnostic log."
            )
        # Windows commonly denies replacement while the source still has an
        # open CRT handle. Preserve the verified identity, close it, publish
        # the empty file, then reopen without O_TRUNC and revalidate before
        # any text can be written.
        _close_quietly(descriptor)
        descriptor = -1
        os.replace(temporary_path, path)
        published = True
        temporary_path = None
        descriptor = os.open(
            path,
            os.O_WRONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0),
        )
        final = os.fstat(descriptor)
        visible = path.lstat()
        if (
            not stat.S_ISREG(final.st_mode)
            or not stat.S_ISREG(visible.st_mode)
            or int(visible.st_dev) != int(final.st_dev)
            or int(visible.st_ino) != int(final.st_ino)
            or int(final.st_dev) != int(created.st_dev)
            or int(final.st_ino) != int(created.st_ino)
            or int(final.st_nlink) != 1
        ):
            raise PrivateLogError(
                "WebJam refused a changed private diagnostic log."
            )
        stream = os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            buffering=1,
        )
        descriptor = -1
        return stream
    except PrivateLogError:
        raise
    except OSError:
        raise PrivateLogError(
            "WebJam could not establish its private diagnostic log."
        ) from None
    finally:
        _close_quietly(descriptor)
        if not published and temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _open_private_portable_append_text_log(path: Path) -> TextIO:
    """Open one portable append log without truncating before validation."""

    descriptor = -1
    try:
        parent = path.parent
        parent_details = parent.lstat()
        if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(
            parent_details.st_mode
        ):
            raise PrivateLogError(
                "WebJam refused an unsafe diagnostic log directory."
            )
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and not _safe_existing_log(existing):
            raise PrivateLogError(
                "WebJam refused an unsafe existing diagnostic log."
            )
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0),
            _PRIVATE_FILE_MODE,
        )
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        opened = os.fstat(descriptor)
        visible = path.lstat()
        if (
            not _safe_existing_log(opened)
            or not stat.S_ISREG(visible.st_mode)
            or int(visible.st_dev) != int(opened.st_dev)
            or int(visible.st_ino) != int(opened.st_ino)
            or int(visible.st_nlink) != 1
        ):
            raise PrivateLogError(
                "WebJam refused a changed private diagnostic log."
            )
        stream = os.fdopen(
            descriptor,
            "a",
            encoding="utf-8",
            buffering=1,
        )
        descriptor = -1
        return stream
    except PrivateLogError:
        raise
    except OSError:
        raise PrivateLogError(
            "WebJam could not establish its private diagnostic log."
        ) from None
    finally:
        _close_quietly(descriptor)


def open_private_append_text_log(
    directory: SecureRuntimeDirectory,
    name: str,
) -> TextIO:
    """Append inside one fully verified private POSIX directory."""

    component = _safe_leaf_name(Path("/") / name)
    descriptor = -1
    created = False
    opened: os.stat_result | None = None
    try:
        if os.name != "posix" or not directory.path_matches():
            raise PrivateLogError(
                "WebJam refused a changed diagnostic log directory."
            )
        try:
            existing = os.stat(
                component,
                dir_fd=directory.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing = None
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise PrivateLogError(
                "WebJam cannot safely append its diagnostic log."
            )
        flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | nofollow
            | getattr(os, "O_CLOEXEC", 0)
        )
        if existing is None:
            try:
                descriptor = os.open(
                    component,
                    flags | os.O_EXCL,
                    _PRIVATE_FILE_MODE,
                    dir_fd=directory.descriptor,
                )
                created = True
            except FileExistsError:
                descriptor = os.open(
                    component,
                    flags,
                    _PRIVATE_FILE_MODE,
                    dir_fd=directory.descriptor,
                )
        else:
            descriptor = os.open(
                component,
                flags,
                _PRIVATE_FILE_MODE,
                dir_fd=directory.descriptor,
            )
        opened = os.fstat(descriptor)
        if not _safe_existing_log(opened):
            raise PrivateLogError(
                "WebJam refused an unsafe existing diagnostic log."
            )
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        final = os.fstat(descriptor)
        visible = os.stat(
            component,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
        if (
            not _private_opened_log(final)
            or not _private_opened_log(visible)
            or int(visible.st_dev) != int(final.st_dev)
            or int(visible.st_ino) != int(final.st_ino)
            or int(final.st_dev) != int(opened.st_dev)
            or int(final.st_ino) != int(opened.st_ino)
            or not directory.path_matches()
        ):
            raise PrivateLogError(
                "WebJam refused a changed private diagnostic log."
            )
        stream = os.fdopen(
            descriptor,
            "a",
            encoding="utf-8",
            buffering=1,
        )
        descriptor = -1
        return stream
    except PrivateLogError:
        raise
    except (NotImplementedError, OSError, SecureRuntimeError):
        raise PrivateLogError(
            "WebJam could not establish its private diagnostic log."
        ) from None
    finally:
        if created and descriptor >= 0 and opened is not None:
            try:
                visible = os.stat(
                    component,
                    dir_fd=directory.descriptor,
                    follow_symlinks=False,
                )
                if (
                    int(visible.st_dev) == int(opened.st_dev)
                    and int(visible.st_ino) == int(opened.st_ino)
                    and stat.S_ISREG(visible.st_mode)
                ):
                    os.unlink(component, dir_fd=directory.descriptor)
            except (OSError, SecureRuntimeError):
                pass
        _close_quietly(descriptor)


def open_private_text_log(
    path: str | Path,
    *,
    append: bool = False,
) -> TextIO:
    """Create an empty, line-buffered diagnostic log visible only to its user."""

    target = Path(path).expanduser()
    name = _safe_leaf_name(target)
    if os.name == "posix":
        if append:
            raise PrivateLogError(
                "WebJam requires a verified directory for append diagnostics."
            )
        return _open_private_posix_text_log(target, name)
    if append:
        return _open_private_portable_append_text_log(target)
    return _open_private_portable_text_log(target, name)
