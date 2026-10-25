"""Read-only Windows transport-path qualification, not a signature substitute.

Only the current user and the operating system's installation administrators are
trusted writers. Unknown ACL forms/filesystems fail closed. The guard retains
noninheritable handles without write/delete sharing until process creation.
Native API/package behavior still requires qualification on actual Windows.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import ntpath
import os
from pathlib import PureWindowsPath
import re
import struct


class WindowsTransportPermissionError(RuntimeError):
    """The installation cannot be proven safe for this Windows launch."""


_ERROR = "Transport installation permissions could not be verified. Reinstall WebJam in a private local folder or contact the administrator."
_ADMIN_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544"})
_SID = re.compile(r"S-1-\d+(?:-\d+){1,15}\Z")
_ALL = 0x001F01FF
_GENERIC = {
    0x10000000: _ALL,
    0x40000000: 0x00120116,
    0x80000000: 0x00120089,
    0x20000000: 0x001200A0,
}
_FILE_MUTATION = 0x0002 | 0x0004 | 0x0010 | 0x0100 | 0x10000 | 0x40000 | 0x80000
# FILE_ADD_SUBDIRECTORY alone cannot replace an existing nonempty ancestor.
# FILE_ADD_FILE/WRITE_DATA can also authorize reparse changes; retain it here.
_DIRECTORY_MUTATION = (_FILE_MUTATION & ~0x0004) | 0x0040


@dataclass(frozen=True)
class WindowsAce:
    kind: int
    flags: int
    mask: int
    sid: str


@dataclass(frozen=True)
class WindowsPathSecurity:
    identity: tuple[int, bytes]
    directory: bool
    attributes: int
    links: int
    size: int
    owner: str
    dacl: tuple[WindowsAce, ...] | None
    path: str


def _deny() -> None:
    raise WindowsTransportPermissionError(_ERROR)


def _sid_text(data: bytes) -> str:
    if len(data) < 8 or data[0] != 1 or data[1] > 15 or len(data) != 8 + 4 * data[1]:
        _deny()
    authority = int.from_bytes(data[2:8], "big")
    values = struct.unpack_from(f"<{data[1]}I", data, 8) if data[1] else ()
    return "S-1-" + str(authority) + "".join(f"-{value}" for value in values)


def validate_windows_acl(details: WindowsPathSecurity, trusted: frozenset[str]) -> None:
    """Conservative allow-ACE ceiling; never infer safety by subtracting denies."""
    if details.owner not in trusted or details.dacl is None or len(details.dacl) > 1024:
        _deny()
    dangerous = _DIRECTORY_MUTATION if details.directory else _FILE_MUTATION
    for ace in details.dacl:
        if (
            type(ace) is not WindowsAce
            or ace.kind not in {0, 1}
            or type(ace.flags) is not int
            or ace.flags & ~0x1F
            or type(ace.mask) is not int
            or not 0 <= ace.mask <= 0xFFFFFFFF
            or not isinstance(ace.sid, str)
            or not _SID.fullmatch(ace.sid)
        ):
            _deny()
        mask = ace.mask
        for generic, concrete in _GENERIC.items():
            if mask & generic:
                mask = (mask & ~generic) | concrete
        # Includes MAXIMUM_ALLOWED, SACL/system-security and future unknown bits.
        if mask & ~_ALL:
            _deny()
        if ace.flags & 0x08:  # INHERIT_ONLY_ACE does not apply to this object.
            continue
        if ace.kind == 0 and ace.sid not in trusted and mask & dangerous:
            _deny()


def _path_chain(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or len(path) > 32767 or "\x00" in path:
        _deny()
    candidate = PureWindowsPath(path)
    if (
        not re.fullmatch(r"[A-Za-z]:", candidate.drive)
        or candidate.root != "\\"
        or len(candidate.parts) > 64
    ):
        _deny()
    if len(candidate.parts) < 2:
        _deny()
    for part in candidate.parts[1:]:
        if (
            part in {".", ".."}
            or part[-1:] in {".", " "}
            or re.search(r'[<>:"|?*\x00-\x1f]', part)
            or PureWindowsPath(part).is_reserved()
        ):
            _deny()
    return tuple(
        str(PureWindowsPath(*candidate.parts[:index]))
        for index in range(1, len(candidate.parts) + 1)
    )


def _same_path(left: str, right: str) -> bool:
    if left.startswith("\\\\?\\"):
        left = left[4:]
    return ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(
        ntpath.normpath(right)
    )


class WindowsBinaryGuard(AbstractContextManager):
    """Pin every lexical ancestor before inspecting the final executable.

    The internal api parameter is an OS-isolated test seam, not a public grant.
    A descriptor alone is not a fence; handles stay open through Popen.
    """

    def __init__(self, path: str, *, api=None):
        self.path = str(PureWindowsPath(path))
        self._chain = _path_chain(path)
        self._api = api
        self._handles = []
        self._identities = []
        self._trusted = frozenset()
        self._active = False

    def __enter__(self):
        try:
            if self._api is None:
                self._api = _NativeWindowsApi()
            self._trusted = _ADMIN_SIDS | frozenset(
                {self._api.current_user_sid(), self._api.installer_sid()}
            )
            if any(
                not isinstance(sid, str) or not _SID.fullmatch(sid)
                for sid in self._trusted
            ):
                _deny()
            self._api.require_local_acl_volume(self._chain[0])
            for index, path in enumerate(self._chain):
                directory = index < len(self._chain) - 1
                handle = self._api.open_pinned(path, directory=directory)
                self._handles.append(handle)
                details = self._api.inspect(handle)
                self._validate(details, path, directory)
                self._identities.append(details.identity)
            self._active = True
            return self
        except BaseException as error:
            self.close()
            if isinstance(
                error, (WindowsTransportPermissionError, KeyboardInterrupt, SystemExit)
            ):
                raise
            raise WindowsTransportPermissionError(_ERROR) from error

    def _validate(self, details, path, directory):
        if (
            type(details) is not WindowsPathSecurity
            or details.directory is not directory
            or details.attributes & 0x400
            or not _same_path(details.path, path)
            or len(details.identity[1]) != 16
            or not any(details.identity[1])
            or not directory
            and (details.links != 1 or not 0 <= details.size <= 128 * 1024 * 1024)
        ):
            _deny()
        validate_windows_acl(details, self._trusted)

    def check_current(self, path: str) -> None:
        if not self._active or not _same_path(path, self.path):
            _deny()
        try:
            for index, (handle, original) in enumerate(
                zip(self._handles, self._identities)
            ):
                details = self._api.inspect(handle)
                self._validate(
                    details, self._chain[index], index < len(self._chain) - 1
                )
                if details.identity != original:
                    _deny()
        except WindowsTransportPermissionError:
            raise
        except Exception as error:
            raise WindowsTransportPermissionError(_ERROR) from error

    def close(self):
        self._active = False
        closed = True
        while self._handles:
            try:
                if self._api.close(self._handles.pop()) is False:
                    closed = False
            except Exception:
                closed = False
        return closed

    def __exit__(self, error_type, *_):
        if not self.close() and error_type is None:
            _deny()


class _NativeWindowsApi:
    """Small read-only Win32 adapter. Unsupported results never become grants."""

    def __init__(self):
        if os.name != "nt":
            _deny()
        import ctypes as c
        from ctypes import wintypes as w

        self.c, self.w = c, w
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.security = c.WinDLL("advapi32", use_last_error=True)

        def bind(dll, name, args, result):
            fn = getattr(dll, name)
            fn.argtypes = args
            fn.restype = result
            return fn

        pointer = c.c_void_p
        self.create = bind(
            self.kernel,
            "CreateFileW",
            [w.LPCWSTR, w.DWORD, w.DWORD, pointer, w.DWORD, w.DWORD, w.HANDLE],
            w.HANDLE,
        )
        self.close_handle = bind(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)
        self.info = bind(
            self.kernel,
            "GetFileInformationByHandleEx",
            [w.HANDLE, c.c_int, pointer, w.DWORD],
            w.BOOL,
        )
        self.final_path = bind(
            self.kernel,
            "GetFinalPathNameByHandleW",
            [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD],
            w.DWORD,
        )
        self.drive_type = bind(self.kernel, "GetDriveTypeW", [w.LPCWSTR], w.UINT)
        self.volume = bind(
            self.kernel,
            "GetVolumeInformationW",
            [
                w.LPCWSTR,
                w.LPWSTR,
                w.DWORD,
                pointer,
                pointer,
                pointer,
                w.LPWSTR,
                w.DWORD,
            ],
            w.BOOL,
        )
        self.get_security = bind(
            self.security,
            "GetSecurityInfo",
            [
                w.HANDLE,
                c.c_int,
                w.DWORD,
                pointer,
                pointer,
                pointer,
                pointer,
                c.POINTER(pointer),
            ],
            w.DWORD,
        )
        self.sd_length = bind(
            self.security, "GetSecurityDescriptorLength", [pointer], w.DWORD
        )
        self.sd_owner = bind(
            self.security,
            "GetSecurityDescriptorOwner",
            [pointer, c.POINTER(pointer), c.POINTER(w.BOOL)],
            w.BOOL,
        )
        self.sd_dacl = bind(
            self.security,
            "GetSecurityDescriptorDacl",
            [pointer, c.POINTER(w.BOOL), c.POINTER(pointer), c.POINTER(w.BOOL)],
            w.BOOL,
        )
        self.valid_acl = bind(self.security, "IsValidAcl", [pointer], w.BOOL)
        self.get_ace = bind(
            self.security, "GetAce", [pointer, w.DWORD, c.POINTER(pointer)], w.BOOL
        )
        self.local_free = bind(self.kernel, "LocalFree", [pointer], pointer)
        self.process = bind(self.kernel, "GetCurrentProcess", [], w.HANDLE)
        self.open_token = bind(
            self.security,
            "OpenProcessToken",
            [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)],
            w.BOOL,
        )
        self.token_info = bind(
            self.security,
            "GetTokenInformation",
            [w.HANDLE, c.c_int, pointer, w.DWORD, c.POINTER(w.DWORD)],
            w.BOOL,
        )
        self.lookup = bind(
            self.security,
            "LookupAccountNameW",
            [
                w.LPCWSTR,
                w.LPCWSTR,
                pointer,
                c.POINTER(w.DWORD),
                w.LPWSTR,
                c.POINTER(w.DWORD),
                pointer,
            ],
            w.BOOL,
        )

    def require_local_acl_volume(self, root):
        c, w = self.c, self.w
        flags = w.DWORD()
        # FileIdInfo supports 128-bit IDs on NTFS/ReFS. Do not infer protection
        # from a filesystem name: require actual persistent ACL support instead.
        if (
            self.drive_type(root) != 3
            or not self.volume(root, None, 0, None, None, c.byref(flags), None, 0)
            or not flags.value & 8
        ):
            _deny()

    def open_pinned(self, path, *, directory):
        access = 0x20000 | 0x80 | (0 if directory else 0x80000000)
        handle = self.create(path, access, 1, None, 3, 0x00200000 | 0x02000000, None)
        if handle in {None, self.c.c_void_p(-1).value}:
            _deny()
        return handle

    def close(self, handle):
        return bool(self.close_handle(handle))

    def _bounded_sid(self, address, start, end):
        if not address or not start <= address <= end - 8:
            _deny()
        header = self.c.string_at(address, 8)
        length = 8 + header[1] * 4
        if length > 68 or address + length > end:
            _deny()
        return _sid_text(self.c.string_at(address, length))

    def current_user_sid(self):
        c, w = self.c, self.w
        token = w.HANDLE()
        if not self.open_token(self.process(), 8, c.byref(token)):
            _deny()
        try:
            size = w.DWORD()
            self.token_info(token, 1, None, 0, c.byref(size))
            if not 8 <= size.value <= 4096:
                _deny()
            data = c.create_string_buffer(size.value)
            if not self.token_info(token, 1, data, size, c.byref(size)):
                _deny()
            address = c.cast(data, c.POINTER(c.c_void_p))[0]
            return self._bounded_sid(
                address, c.addressof(data), c.addressof(data) + len(data)
            )
        finally:
            if not self.close(token):
                _deny()

    def installer_sid(self):
        c, w = self.c, self.w
        size, domain_size, use = w.DWORD(), w.DWORD(), w.DWORD()
        self.lookup(
            None,
            "NT SERVICE\\TrustedInstaller",
            None,
            c.byref(size),
            None,
            c.byref(domain_size),
            c.byref(use),
        )
        if not 8 <= size.value <= 68 or not 1 <= domain_size.value <= 256:
            _deny()
        data, domain = (
            c.create_string_buffer(size.value),
            c.create_unicode_buffer(domain_size.value),
        )
        if not self.lookup(
            None,
            "NT SERVICE\\TrustedInstaller",
            data,
            c.byref(size),
            domain,
            c.byref(domain_size),
            c.byref(use),
        ):
            _deny()
        sid = _sid_text(data.raw[: size.value])
        if not sid.startswith("S-1-5-80-"):
            _deny()
        return sid

    def _descriptor(self, handle):
        c, w = self.c, self.w
        descriptor = c.c_void_p()
        if (
            self.get_security(
                handle, 1, 1 | 4, None, None, None, None, c.byref(descriptor)
            )
            or not descriptor.value
        ):
            _deny()
        try:
            length = self.sd_length(descriptor)
            if not 20 <= length <= 65536:
                _deny()
            start, end = descriptor.value, descriptor.value + length
            owner, dacl, present, defaulted = (
                c.c_void_p(),
                c.c_void_p(),
                w.BOOL(),
                w.BOOL(),
            )
            if not self.sd_owner(descriptor, c.byref(owner), c.byref(defaulted)):
                _deny()
            owner_sid = self._bounded_sid(owner.value, start, end)
            if (
                not self.sd_dacl(
                    descriptor, c.byref(present), c.byref(dacl), c.byref(defaulted)
                )
                or not present.value
                or not dacl.value
            ):
                _deny()
            if not start <= dacl.value <= end - 8 or not self.valid_acl(dacl):
                _deny()
            revision, _, acl_size, count, _ = struct.unpack(
                "<BBHHH", c.string_at(dacl, 8)
            )
            if (
                revision not in {2, 4}
                or not 8 <= acl_size <= end - dacl.value
                or count > 1024
            ):
                _deny()
            entries = []
            for index in range(count):
                address = c.c_void_p()
                if (
                    not self.get_ace(dacl, index, c.byref(address))
                    or not dacl.value + 8
                    <= (address.value or 0)
                    <= dacl.value + acl_size - 4
                ):
                    _deny()
                kind, flags, size = struct.unpack("<BBH", c.string_at(address, 4))
                if (
                    kind not in {0, 1}
                    or size < 16
                    or address.value + size > dacl.value + acl_size
                ):
                    _deny()
                mask = struct.unpack("<I", c.string_at(address.value + 4, 4))[0]
                sid = self._bounded_sid(
                    address.value + 8, address.value + 8, address.value + size
                )
                if size != 8 + 8 + 4 * c.string_at(address.value + 9, 1)[0]:
                    _deny()
                entries.append(WindowsAce(kind, flags, mask, sid))
            return owner_sid, tuple(entries)
        finally:
            if self.local_free(descriptor):
                _deny()

    def inspect(self, handle):
        c = self.c

        # Native layouts use fixed-width fields, with explicit padding to match
        # documented Win64/Win32 structure sizes, not POSIX ctypes long widths.
        class Basic(c.Structure):
            _fields_ = [
                (name, c.c_int64)
                for name in ("created", "accessed", "written", "changed")
            ] + [("attributes", c.c_uint32), ("padding", c.c_uint32)]

        class Standard(c.Structure):
            _fields_ = [
                ("allocation", c.c_int64),
                ("size", c.c_int64),
                ("links", c.c_uint32),
                ("delete_pending", c.c_ubyte),
                ("directory", c.c_ubyte),
            ]

        class Identity(c.Structure):
            _fields_ = [("volume", c.c_uint64), ("file_id", c.c_ubyte * 16)]

        basic, standard, identity = Basic(), Standard(), Identity()
        if (c.sizeof(basic), c.sizeof(standard), c.sizeof(identity)) != (40, 24, 24):
            _deny()
        for kind, data in ((0, basic), (1, standard), (18, identity)):
            if not self.info(handle, kind, c.byref(data), c.sizeof(data)):
                _deny()
        if standard.delete_pending:
            _deny()
        path = c.create_unicode_buffer(32768)
        length = self.final_path(handle, path, len(path), 0)
        if not 0 < length < len(path):
            _deny()
        owner, dacl = self._descriptor(handle)
        return WindowsPathSecurity(
            (identity.volume, bytes(identity.file_id)),
            bool(standard.directory),
            basic.attributes,
            standard.links,
            standard.size,
            owner,
            dacl,
            path.value,
        )
