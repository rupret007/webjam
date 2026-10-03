"""Windows handles and ACLs for private recording journals.

Create protected current-user/SYSTEM permissions before any data is written.
Directory guards deny rename/deletion while journal pathnames are in use.
This is deliberately separate from the POSIX native-child runtime helper.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from functools import lru_cache
import os
from pathlib import Path
import stat

DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
BYTE = ctypes.c_ubyte
HANDLE = ctypes.c_void_p
POINTER = ctypes.c_void_p
BOOL = ctypes.c_int32


class PrivateJournalError(OSError):
    """Windows could not establish private, stable journal storage."""


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", DWORD), ("descriptor", POINTER), ("inherit", BOOL)]


class _FileInfo(ctypes.Structure):
    _fields_ = [("attributes", DWORD), ("created", DWORD * 2),
                ("accessed", DWORD * 2), ("written", DWORD * 2),
                ("volume", DWORD), ("size_high", DWORD), ("size_low", DWORD),
                ("links", DWORD), ("index_high", DWORD), ("index_low", DWORD)]


class _Acl(ctypes.Structure):
    _fields_ = [("revision", BYTE), ("reserved", BYTE), ("size", WORD),
                ("count", WORD), ("reserved2", WORD)]


class _Ace(ctypes.Structure):
    _fields_ = [("kind", BYTE), ("flags", BYTE), ("size", WORD), ("mask", DWORD)]


class _Api:
    def __init__(self):
        if os.name != "nt":
            raise PrivateJournalError("Windows journal handles are unavailable.")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        self._bind(self.kernel, "CloseHandle", [HANDLE], BOOL)
        self._bind(self.kernel, "LocalFree", [POINTER], POINTER)
        self._bind(self.kernel, "GetCurrentProcess", [], HANDLE)
        self._bind(self.kernel, "GetFileType", [HANDLE], DWORD)
        self._bind(self.kernel, "GetFileInformationByHandle", [HANDLE, ctypes.POINTER(_FileInfo)], BOOL)
        self._bind(self.kernel, "CreateDirectoryW", [ctypes.c_wchar_p, ctypes.POINTER(_SecurityAttributes)], BOOL)
        self._bind(self.kernel, "CreateFileW", [ctypes.c_wchar_p, DWORD, DWORD,
                   ctypes.POINTER(_SecurityAttributes), DWORD, DWORD, HANDLE], HANDLE)
        self._bind(self.security, "OpenProcessToken", [HANDLE, DWORD, ctypes.POINTER(HANDLE)], BOOL)
        self._bind(self.security, "GetTokenInformation", [HANDLE, DWORD, POINTER, DWORD, ctypes.POINTER(DWORD)], BOOL)
        self._bind(self.security, "ConvertSidToStringSidW", [POINTER, ctypes.POINTER(POINTER)], BOOL)
        self._bind(self.security, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                   [ctypes.c_wchar_p, DWORD, ctypes.POINTER(POINTER), POINTER], BOOL)
        self._bind(self.security, "GetSecurityInfo", [HANDLE, DWORD, DWORD,
                   ctypes.POINTER(POINTER), POINTER, ctypes.POINTER(POINTER), POINTER,
                   ctypes.POINTER(POINTER)], DWORD)
        self._bind(self.security, "GetSecurityDescriptorControl", [POINTER, ctypes.POINTER(WORD), ctypes.POINTER(DWORD)], BOOL)
        self._bind(self.security, "GetAce", [POINTER, DWORD, ctypes.POINTER(POINTER)], BOOL)
        self.user = self._current_user()

    @staticmethod
    def _bind(library, name, args, result):
        function = getattr(library, name)
        function.argtypes, function.restype = args, result

    @staticmethod
    def _check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())

    def _sid(self, value):
        text = POINTER()
        self._check(self.security.ConvertSidToStringSidW(value, ctypes.byref(text)))
        try:
            return ctypes.wstring_at(text)
        finally:
            self.kernel.LocalFree(text)

    def _current_user(self):
        token = HANDLE()
        self._check(self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)))
        try:
            length = DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
            if not 0 < length.value <= 65536:
                raise PrivateJournalError("The journal owner could not be identified.")
            buffer = ctypes.create_string_buffer(length.value)
            self._check(self.security.GetTokenInformation(token, 1, buffer, length, ctypes.byref(length)))
            return self._sid(ctypes.cast(buffer, ctypes.POINTER(POINTER)).contents)
        finally:
            self.kernel.CloseHandle(token)

    @contextmanager
    def attributes(self, *, directory):
        flags = "OICI" if directory else ""
        # Explicit owner also works for an elevated process whose default
        # security descriptor might otherwise name the Administrators group.
        sddl = f"O:{self.user}D:P(A;{flags};FA;;;{self.user})(A;{flags};FA;;;SY)"
        descriptor = POINTER()
        self._check(self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None))
        try:
            yield _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        finally:
            self.kernel.LocalFree(descriptor)

    def validate(self, handle, path, *, directory):
        if self.kernel.GetFileType(handle) != 1:
            raise PrivateJournalError("Journal storage is not a disk object.")
        info = _FileInfo()
        self._check(self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)))
        if bool(info.attributes & 0x10) != directory or info.attributes & 0x400:
            raise PrivateJournalError("Journal storage is not a regular, unlinked path.")
        visible = path.lstat()
        index = (info.index_high << 32) | info.index_low
        if (stat.S_ISLNK(visible.st_mode)
                or bool(stat.S_ISDIR(visible.st_mode)) != directory
                or (not directory and not stat.S_ISREG(visible.st_mode))
                or visible.st_ino & ((1 << 64) - 1) != index
                or visible.st_dev & 0xffffffff != info.volume
                or (not directory and info.links != 1)):
            raise PrivateJournalError("Journal path identity changed.")
        owner, acl, descriptor = POINTER(), POINTER(), POINTER()
        error = self.security.GetSecurityInfo(handle, 1, 1 | 4, ctypes.byref(owner), None,
                                             ctypes.byref(acl), None, ctypes.byref(descriptor))
        if error:
            raise ctypes.WinError(error)
        try:
            control, revision = WORD(), DWORD()
            self._check(self.security.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)))
            if (not owner or self._sid(owner) != self.user or not acl
                    or control.value & 0x1004 != 0x1004):
                raise PrivateJournalError("Journal owner or protected permissions are invalid.")
            count = ctypes.cast(acl, ctypes.POINTER(_Acl)).contents.count
            if not 1 <= count <= 8:
                raise PrivateJournalError("Journal access rules are invalid.")
            allowed = {self.user, "S-1-5-18"}
            observed = set()
            for position in range(count):
                entry = POINTER()
                self._check(self.security.GetAce(acl, position, ctypes.byref(entry)))
                ace = ctypes.cast(entry, ctypes.POINTER(_Ace)).contents
                if ace.kind != 0 or ace.flags != (3 if directory else 0) or ace.size < 16 or ace.mask != 0x1f01ff:
                    raise PrivateJournalError("Journal access rules are not private.")
                sid = self._sid(entry.value + ctypes.sizeof(_Ace))
                if sid not in allowed:
                    raise PrivateJournalError("Journal access includes another account.")
                observed.add(sid)
            if observed != allowed:
                raise PrivateJournalError("Journal access rules are incomplete.")
        finally:
            self.kernel.LocalFree(descriptor)
        return visible.st_dev, visible.st_ino

    def open(self, path, *, directory=False, create=False):
        # READ_CONTROL + FILE_READ_ATTRIBUTES, plus content access for files.
        access = 0x20000 | 0x80 | (0 if directory else 0x80000000)
        if create:
            access |= 0x40000000
        flags = 0x00200000 | (0x02000000 if directory else 0x80)
        with self.attributes(directory=directory) as attributes:
            handle = self.kernel.CreateFileW(str(path), access, 3 if directory else 1,
                ctypes.byref(attributes) if create else None, 1 if create else 3, flags, None)
            error = ctypes.get_last_error() if handle == ctypes.c_void_p(-1).value else 0
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(error)
        try:
            self.validate(handle, path, directory=directory)
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise
        return handle


@lru_cache(maxsize=1)
def _api():
    return _Api()


@contextmanager
def private_directory(path: Path, *, create=False):
    """Pin a protected journal directory until all pathname operations finish."""
    api = _api()
    if create:
        with api.attributes(directory=True) as attributes:
            if not api.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                error = ctypes.get_last_error()
                if error != 183:  # Existing entries still require full validation.
                    raise ctypes.WinError(error)
    handle = api.open(path, directory=True)
    try:
        yield path
        api.validate(handle, path, directory=True)
    finally:
        api.kernel.CloseHandle(handle)


def private_file_descriptor(path: Path, *, create=False) -> int:
    """Return a non-inheritable CRT descriptor after native identity/ACL checks."""
    import msvcrt

    api = _api()
    handle = api.open(path, create=create)
    try:
        return msvcrt.open_osfhandle(handle, (os.O_RDWR if create else os.O_RDONLY) | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        api.kernel.CloseHandle(handle)
        raise


def assert_private_file(path: Path) -> None:
    descriptor = private_file_descriptor(path)
    os.close(descriptor)
