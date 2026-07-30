"""Descriptor-anchored private runtime files for supervised native children.

The macOS Jamulus roles consume profiles, credentials, and recording
directories by pathname after ``exec``.  These POSIX helpers create those
paths below a retained, user-owned home descriptor without following
intermediate or leaf symlinks.  They keep the complete path chain and exact
permissions re-verifiable before and after a child launch.

Public failures deliberately contain no filesystem path and suppress raw
``OSError`` causes so support logs cannot disclose a private home path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import stat
from typing import Callable


_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600


class SecureRuntimeError(RuntimeError):
    """A path-free private-runtime validation failure."""


def _owned(details: os.stat_result) -> bool:
    return hasattr(os, "geteuid") and int(details.st_uid) == int(os.geteuid())


def _safe_ancestor(details: os.stat_result) -> bool:
    return bool(
        stat.S_ISDIR(details.st_mode)
        and _owned(details)
        and not stat.S_IMODE(details.st_mode) & 0o022
    )


def _safe_component(value: str) -> str:
    component = str(value or "")
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or "\0" in component
    ):
        raise SecureRuntimeError("WebJam refused an unsafe private runtime path.")
    return component


def _directory_flags() -> int:
    if os.name != "posix":
        raise SecureRuntimeError(
            "WebJam cannot establish a private runtime on this platform."
        )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise SecureRuntimeError(
            "WebJam could not establish its private runtime directory."
        )
    return (
        os.O_RDONLY
        | nofollow
        | directory
        | getattr(os, "O_CLOEXEC", 0)
    )


def _file_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if os.name != "posix" or not nofollow:
        raise SecureRuntimeError(
            "WebJam could not establish its private runtime credential."
        )
    return os.O_RDWR | os.O_CREAT | nofollow | getattr(os, "O_CLOEXEC", 0)


def _close_quietly(descriptor: int) -> None:
    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


@dataclass(frozen=True, slots=True)
class RuntimePathProof:
    """Exact identity, permissions, and retained-chain validation for one path."""

    path: Path
    device: int
    inode: int
    mode_kind: str
    expected_mode: int
    expected_links: int | None
    _matcher: Callable[[], bool] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mode_kind not in {"directory", "file"}:
            raise ValueError("mode_kind must be directory or file")
        if int(self.device) <= 0 or int(self.inode) <= 0:
            raise ValueError("runtime identity must be positive")
        if self.expected_mode not in {
            _PRIVATE_DIRECTORY_MODE,
            _PRIVATE_FILE_MODE,
        }:
            raise ValueError("runtime proof mode must be private")
        if self.mode_kind == "file" and self.expected_links != 1:
            raise ValueError("private file proof must require one link")
        if self.mode_kind == "directory" and self.expected_links is not None:
            raise ValueError("directory proof cannot require a link count")
        if not callable(self._matcher):
            raise TypeError("runtime proof matcher must be callable")

    def matches(self) -> bool:
        try:
            return bool(self._matcher())
        except Exception:  # noqa: BLE001 - proof checks fail closed
            return False


class SecureRuntimeDirectory:
    """A retained, component-wise no-follow directory below a resolved home."""

    def __init__(
        self,
        *,
        home: Path,
        relative_parts: tuple[str, ...],
        root_descriptor: int,
        path: Path,
        descriptor: int,
        details: os.stat_result,
    ) -> None:
        self._home = Path(home)
        self._relative_parts = tuple(relative_parts)
        self.path = Path(path)
        self._root_descriptor = int(root_descriptor)
        self._descriptor = int(descriptor)
        self._proof = RuntimePathProof(
            path=self.path,
            device=int(details.st_dev),
            inode=int(details.st_ino),
            mode_kind="directory",
            expected_mode=_PRIVATE_DIRECTORY_MODE,
            expected_links=None,
            _matcher=self.path_matches,
        )

    @classmethod
    def open(
        cls,
        *,
        home: Path,
        directory: Path,
        mode: int = _PRIVATE_DIRECTORY_MODE,
    ) -> "SecureRuntimeDirectory":
        if mode != _PRIVATE_DIRECTORY_MODE:
            raise SecureRuntimeError(
                "WebJam refused non-private runtime directory permissions."
            )
        if os.name != "posix":
            raise SecureRuntimeError(
                "WebJam cannot establish a private runtime on this platform."
            )
        requested_home = Path(home).expanduser()
        try:
            safe_home = requested_home.resolve(strict=True)
            relative = Path(directory).expanduser().relative_to(requested_home)
        except (OSError, ValueError):
            raise SecureRuntimeError(
                "WebJam refused an unsafe private runtime path."
            ) from None
        if not relative.parts or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise SecureRuntimeError(
                "WebJam refused an unsafe private runtime path."
            )
        parts = tuple(_safe_component(part) for part in relative.parts)

        root_descriptor = -1
        descriptor = -1
        try:
            root_descriptor = os.open(safe_home, _directory_flags())
            root_details = os.fstat(root_descriptor)
            visible_root = safe_home.lstat()
            if (
                not _safe_ancestor(root_details)
                or stat.S_ISLNK(visible_root.st_mode)
                or not stat.S_ISDIR(visible_root.st_mode)
                or int(visible_root.st_dev) != int(root_details.st_dev)
                or int(visible_root.st_ino) != int(root_details.st_ino)
            ):
                raise SecureRuntimeError(
                    "WebJam refused an unsafe private runtime directory."
                )
            descriptor = os.dup(root_descriptor)
            candidate = safe_home
            for component in parts:
                try:
                    os.mkdir(
                        component,
                        mode=_PRIVATE_DIRECTORY_MODE,
                        dir_fd=descriptor,
                    )
                except FileExistsError:
                    pass
                next_descriptor = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=descriptor,
                )
                try:
                    child_details = os.fstat(next_descriptor)
                    if not _safe_ancestor(child_details):
                        raise SecureRuntimeError(
                            "WebJam refused an unsafe private runtime directory."
                        )
                except Exception:
                    _close_quietly(next_descriptor)
                    raise
                _close_quietly(descriptor)
                descriptor = next_descriptor
                candidate /= component

            details = os.fstat(descriptor)
            if not _safe_ancestor(details):
                raise SecureRuntimeError(
                    "WebJam refused an unsafe private runtime directory."
                )
            os.fchmod(descriptor, _PRIVATE_DIRECTORY_MODE)
            details = os.fstat(descriptor)
            entry = candidate.lstat()
            if (
                stat.S_IMODE(details.st_mode) != _PRIVATE_DIRECTORY_MODE
                or stat.S_ISLNK(entry.st_mode)
                or not stat.S_ISDIR(entry.st_mode)
                or int(entry.st_dev) != int(details.st_dev)
                or int(entry.st_ino) != int(details.st_ino)
            ):
                raise SecureRuntimeError(
                    "WebJam refused a changed private runtime directory."
                )
            runtime = cls(
                home=safe_home,
                relative_parts=parts,
                root_descriptor=root_descriptor,
                path=candidate,
                descriptor=descriptor,
                details=details,
            )
            root_descriptor = -1
            descriptor = -1
            if not runtime.path_matches():
                runtime.close()
                raise SecureRuntimeError(
                    "WebJam refused a changed private runtime directory."
                )
            return runtime
        except SecureRuntimeError:
            raise
        except (NotImplementedError, OSError):
            raise SecureRuntimeError(
                "WebJam could not establish its private runtime directory."
            ) from None
        finally:
            _close_quietly(descriptor)
            _close_quietly(root_descriptor)

    @property
    def descriptor(self) -> int:
        if self._descriptor < 0:
            raise SecureRuntimeError("WebJam's private runtime directory is closed.")
        return self._descriptor

    @property
    def proof(self) -> RuntimePathProof:
        return self._proof

    def _retained_root_matches(self) -> bool:
        if self._root_descriptor < 0:
            return False
        try:
            opened = os.fstat(self._root_descriptor)
            visible = self._home.lstat()
        except OSError:
            return False
        return bool(
            _safe_ancestor(opened)
            and stat.S_ISDIR(visible.st_mode)
            and not stat.S_ISLNK(visible.st_mode)
            and int(visible.st_dev) == int(opened.st_dev)
            and int(visible.st_ino) == int(opened.st_ino)
        )

    def path_matches(self) -> bool:
        """Rewalk the whole visible chain and compare it with retained fds."""

        if (
            self._descriptor < 0
            or self._root_descriptor < 0
            or not self._retained_root_matches()
        ):
            return False
        current = -1
        try:
            retained = os.fstat(self._descriptor)
            if (
                not _safe_ancestor(retained)
                or stat.S_IMODE(retained.st_mode) != _PRIVATE_DIRECTORY_MODE
                or int(retained.st_dev) != self._proof.device
                or int(retained.st_ino) != self._proof.inode
            ):
                return False
            current = os.dup(self._root_descriptor)
            for component in self._relative_parts:
                next_descriptor = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=current,
                )
                next_details = os.fstat(next_descriptor)
                if not _safe_ancestor(next_details):
                    _close_quietly(next_descriptor)
                    return False
                _close_quietly(current)
                current = next_descriptor
            visible = os.fstat(current)
            return bool(
                stat.S_IMODE(visible.st_mode) == _PRIVATE_DIRECTORY_MODE
                and int(visible.st_dev) == self._proof.device
                and int(visible.st_ino) == self._proof.inode
            )
        except (NotImplementedError, OSError, SecureRuntimeError):
            return False
        finally:
            _close_quietly(current)

    def _child_matches(
        self,
        component: str,
        *,
        device: int,
        inode: int,
        mode_kind: str,
        expected_mode: int,
        expected_links: int | None,
    ) -> bool:
        if not self.path_matches():
            return False
        try:
            details = os.stat(
                component,
                dir_fd=self.descriptor,
                follow_symlinks=False,
            )
        except (NotImplementedError, OSError, SecureRuntimeError):
            return False
        kind_matches = (
            stat.S_ISDIR(details.st_mode)
            if mode_kind == "directory"
            else stat.S_ISREG(details.st_mode)
        )
        return bool(
            kind_matches
            and not stat.S_ISLNK(details.st_mode)
            and _owned(details)
            and int(details.st_dev) == int(device)
            and int(details.st_ino) == int(inode)
            and stat.S_IMODE(details.st_mode) == expected_mode
            and (
                expected_links is None
                or int(details.st_nlink) == int(expected_links)
            )
        )

    def ensure_child_directory(
        self,
        name: str,
        *,
        mode: int = _PRIVATE_DIRECTORY_MODE,
    ) -> RuntimePathProof:
        if mode != _PRIVATE_DIRECTORY_MODE:
            raise SecureRuntimeError(
                "WebJam refused non-private runtime directory permissions."
            )
        component = _safe_component(name)
        descriptor = -1
        try:
            if not self.path_matches():
                raise SecureRuntimeError(
                    "WebJam refused a changed private runtime directory."
                )
            try:
                os.mkdir(
                    component,
                    mode=_PRIVATE_DIRECTORY_MODE,
                    dir_fd=self.descriptor,
                )
            except FileExistsError:
                pass
            descriptor = os.open(
                component,
                _directory_flags(),
                dir_fd=self.descriptor,
            )
            details = os.fstat(descriptor)
            if not _safe_ancestor(details):
                raise SecureRuntimeError(
                    "WebJam refused an unsafe private runtime directory."
                )
            os.fchmod(descriptor, _PRIVATE_DIRECTORY_MODE)
            details = os.fstat(descriptor)
            device = int(details.st_dev)
            inode = int(details.st_ino)
            proof = RuntimePathProof(
                path=self.path / component,
                device=device,
                inode=inode,
                mode_kind="directory",
                expected_mode=_PRIVATE_DIRECTORY_MODE,
                expected_links=None,
                _matcher=lambda: self._child_matches(
                    component,
                    device=device,
                    inode=inode,
                    mode_kind="directory",
                    expected_mode=_PRIVATE_DIRECTORY_MODE,
                    expected_links=None,
                ),
            )
            if not proof.matches():
                raise SecureRuntimeError(
                    "WebJam refused a changed private runtime directory."
                )
            return proof
        except SecureRuntimeError:
            raise
        except (NotImplementedError, OSError):
            raise SecureRuntimeError(
                "WebJam could not prepare its private runtime directory."
            ) from None
        finally:
            _close_quietly(descriptor)

    def write_private_file(
        self,
        name: str,
        payload: bytes,
        *,
        mode: int = _PRIVATE_FILE_MODE,
    ) -> RuntimePathProof:
        if mode != _PRIVATE_FILE_MODE:
            raise SecureRuntimeError(
                "WebJam refused non-private runtime credential permissions."
            )
        component = _safe_component(name)
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("payload must be non-empty bytes")
        descriptor = -1
        try:
            if not self.path_matches():
                raise SecureRuntimeError(
                    "WebJam refused a changed private runtime directory."
                )
            descriptor = os.open(
                component,
                _file_flags(),
                _PRIVATE_FILE_MODE,
                dir_fd=self.descriptor,
            )
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or int(details.st_nlink) != 1
                or not _owned(details)
                or stat.S_IMODE(details.st_mode) & 0o077
            ):
                raise SecureRuntimeError(
                    "WebJam refused an unsafe private runtime credential."
                )
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            os.ftruncate(descriptor, 0)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise SecureRuntimeError(
                        "WebJam could not prepare its private runtime credential."
                    )
                offset += written
            os.fsync(descriptor)
            details = os.fstat(descriptor)
            device = int(details.st_dev)
            inode = int(details.st_ino)
            proof = RuntimePathProof(
                path=self.path / component,
                device=device,
                inode=inode,
                mode_kind="file",
                expected_mode=_PRIVATE_FILE_MODE,
                expected_links=1,
                _matcher=lambda: self._child_matches(
                    component,
                    device=device,
                    inode=inode,
                    mode_kind="file",
                    expected_mode=_PRIVATE_FILE_MODE,
                    expected_links=1,
                ),
            )
            if not proof.matches():
                raise SecureRuntimeError(
                    "WebJam refused a changed private runtime credential."
                )
            return proof
        except SecureRuntimeError:
            raise
        except (NotImplementedError, OSError):
            raise SecureRuntimeError(
                "WebJam could not prepare its private runtime credential."
            ) from None
        finally:
            _close_quietly(descriptor)

    def remove_owned_file(self, proof: RuntimePathProof) -> bool:
        """Quarantine, revalidate, and remove exactly one owned private file.

        Renaming the visible name into a newly created private quarantine
        directory closes the former validation-to-unlink race: a replacement
        that appears at the original name is never an unlink target. If the
        quarantined entry is not the proved inode, restore it without
        clobbering a newer original name, or leave it quarantined when a
        no-clobber restoration cannot be proven.
        """

        if (
            not isinstance(proof, RuntimePathProof)
            or proof.mode_kind != "file"
            or proof.path.parent != self.path
            or not proof.matches()
        ):
            return False
        try:
            component = _safe_component(proof.path.name)
        except SecureRuntimeError:
            return False

        quarantine_name = ""
        quarantine_descriptor = -1
        quarantine_created = False
        entry_quarantined = False
        try:
            if not self.path_matches():
                return False
            for _attempt in range(8):
                candidate = _safe_component(
                    f".webjam-quarantine-{secrets.token_hex(16)}"
                )
                try:
                    os.mkdir(
                        candidate,
                        mode=_PRIVATE_DIRECTORY_MODE,
                        dir_fd=self.descriptor,
                    )
                except FileExistsError:
                    continue
                quarantine_name = candidate
                quarantine_created = True
                break
            if not quarantine_created:
                return False

            quarantine_descriptor = os.open(
                quarantine_name,
                _directory_flags(),
                dir_fd=self.descriptor,
            )
            quarantine_details = os.fstat(quarantine_descriptor)
            visible_quarantine = os.stat(
                quarantine_name,
                dir_fd=self.descriptor,
                follow_symlinks=False,
            )
            if (
                not _safe_ancestor(quarantine_details)
                or stat.S_ISLNK(visible_quarantine.st_mode)
                or not stat.S_ISDIR(visible_quarantine.st_mode)
                or int(visible_quarantine.st_dev)
                != int(quarantine_details.st_dev)
                or int(visible_quarantine.st_ino)
                != int(quarantine_details.st_ino)
            ):
                return False
            os.fchmod(quarantine_descriptor, _PRIVATE_DIRECTORY_MODE)
            quarantine_details = os.fstat(quarantine_descriptor)
            visible_quarantine = os.stat(
                quarantine_name,
                dir_fd=self.descriptor,
                follow_symlinks=False,
            )
            if (
                stat.S_IMODE(quarantine_details.st_mode)
                != _PRIVATE_DIRECTORY_MODE
                or stat.S_IMODE(visible_quarantine.st_mode)
                != _PRIVATE_DIRECTORY_MODE
                or int(visible_quarantine.st_dev)
                != int(quarantine_details.st_dev)
                or int(visible_quarantine.st_ino)
                != int(quarantine_details.st_ino)
            ):
                return False

            try:
                os.stat(
                    "owned",
                    dir_fd=quarantine_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                return False

            os.rename(
                component,
                "owned",
                src_dir_fd=self.descriptor,
                dst_dir_fd=quarantine_descriptor,
            )
            entry_quarantined = True
            if not self.path_matches():
                return False

            details = os.stat(
                "owned",
                dir_fd=quarantine_descriptor,
                follow_symlinks=False,
            )
            if not self._quarantined_file_matches(details, proof):
                return False

            file_descriptor = -1
            try:
                file_descriptor = os.open(
                    "owned",
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=quarantine_descriptor,
                )
                opened = os.fstat(file_descriptor)
                visible = os.stat(
                    "owned",
                    dir_fd=quarantine_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not self._quarantined_file_matches(opened, proof)
                    or not self._quarantined_file_matches(visible, proof)
                ):
                    return False
            finally:
                _close_quietly(file_descriptor)

            os.unlink("owned", dir_fd=quarantine_descriptor)
            entry_quarantined = False
            os.fsync(quarantine_descriptor)
            _close_quietly(quarantine_descriptor)
            quarantine_descriptor = -1
            os.rmdir(quarantine_name, dir_fd=self.descriptor)
            quarantine_created = False
            os.fsync(self.descriptor)
            return True
        except (NotImplementedError, OSError, SecureRuntimeError):
            return False
        finally:
            if entry_quarantined and quarantine_descriptor >= 0:
                _restored, consumed = self._restore_quarantined_entry(
                    quarantine_descriptor=quarantine_descriptor,
                    component=component,
                )
                if consumed:
                    entry_quarantined = False
            _close_quietly(quarantine_descriptor)
            if quarantine_created and not entry_quarantined:
                try:
                    os.rmdir(quarantine_name, dir_fd=self.descriptor)
                    os.fsync(self.descriptor)
                except (NotImplementedError, OSError, SecureRuntimeError):
                    pass

    @staticmethod
    def _quarantined_file_matches(
        details: os.stat_result,
        proof: RuntimePathProof,
    ) -> bool:
        return bool(
            stat.S_ISREG(details.st_mode)
            and not stat.S_ISLNK(details.st_mode)
            and _owned(details)
            and int(details.st_nlink) == 1
            and stat.S_IMODE(details.st_mode) == _PRIVATE_FILE_MODE
            and int(details.st_dev) == proof.device
            and int(details.st_ino) == proof.inode
        )

    def _restore_quarantined_entry(
        self,
        *,
        quarantine_descriptor: int,
        component: str,
    ) -> tuple[bool, bool]:
        """No-clobber restore; return ``(durable, quarantine_consumed)``."""

        try:
            os.link(
                "owned",
                component,
                src_dir_fd=quarantine_descriptor,
                dst_dir_fd=self.descriptor,
                follow_symlinks=False,
            )
        except (FileExistsError, NotImplementedError, OSError, SecureRuntimeError):
            return False, False
        try:
            os.unlink("owned", dir_fd=quarantine_descriptor)
        except (NotImplementedError, OSError, SecureRuntimeError):
            # Both names now preserve the same entry; never delete either
            # through a less constrained fallback.
            return False, False
        durable = True
        try:
            os.fsync(quarantine_descriptor)
            os.fsync(self.descriptor)
        except (NotImplementedError, OSError, SecureRuntimeError):
            durable = False
        return durable, True

    def close(self) -> None:
        descriptors = (self._descriptor, self._root_descriptor)
        self._descriptor = -1
        self._root_descriptor = -1
        failed = False
        for descriptor in descriptors:
            if descriptor < 0:
                continue
            try:
                os.close(descriptor)
            except OSError:
                failed = True
        if failed:
            raise SecureRuntimeError(
                "WebJam could not close its private runtime directory."
            ) from None

    def __enter__(self) -> "SecureRuntimeDirectory":
        return self

    def __del__(self) -> None:
        if self._descriptor < 0 and self._root_descriptor < 0:
            return
        try:
            self.close()
        except Exception:
            pass

    def __exit__(
        self,
        exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        try:
            self.close()
        except SecureRuntimeError:
            if exc_type is None:
                raise


__all__ = [
    "RuntimePathProof",
    "SecureRuntimeDirectory",
    "SecureRuntimeError",
]
