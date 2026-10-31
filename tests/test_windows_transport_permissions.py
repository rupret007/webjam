"""OS-isolated Windows policy/launch tests, never native Windows qualification."""

from dataclasses import replace
import ctypes
from types import SimpleNamespace
import hashlib
import struct

import pytest

from services import transport_runtime as runtime
from services import windows_transport_permissions as permissions
from services.windows_transport_permissions import (
    WindowsAce,
    WindowsBinaryGuard,
    WindowsPathSecurity,
    WindowsTransportPermissionError,
    _NativeWindowsApi,
    _path_chain,
    _sid_text,
    validate_windows_acl,
)

USER = "S-1-5-21-1-2-3-1001"
OTHER = "S-1-5-21-1-2-3-1002"
INSTALLER = "S-1-5-80-1-2-3-4-5"
TRUSTED = frozenset({USER, INSTALLER, "S-1-5-18", "S-1-5-32-544"})
TARGET = r"C:\Users\Example\WebJam\webjam-fabric.exe"


def security(path=TARGET, *, directory=False, owner=USER, aces=()):
    return WindowsPathSecurity(
        (123, b"1" * 16),
        directory,
        0x10 if directory else 0x20,
        1,
        4096,
        owner,
        (WindowsAce(0, 0, 0x1F01FF, USER), *aces),
        path,
    )


class FakeWindowsApi:
    def __init__(self):
        self.opened, self.closed, self.overrides = [], [], {}
        self.volume_ok = True

    def current_user_sid(self):
        return USER

    def installer_sid(self):
        return INSTALLER

    def require_local_acl_volume(self, root):
        if not self.volume_ok:
            raise WindowsTransportPermissionError("Synthetic unsupported volume")

    def open_pinned(self, path, *, directory):
        # Native sharing flags are independently asserted below; this fake
        # models stable OS objects, not a real Windows sharing guarantee.
        self.opened.append((path, directory))
        return path

    def inspect(self, handle):
        directory = dict(self.opened)[handle]
        return self.overrides.get(handle, security(handle, directory=directory))

    def close(self, handle):
        self.closed.append(handle)


@pytest.mark.parametrize("owner", sorted(TRUSTED))
def test_only_current_user_and_installation_administrators_own_the_path(owner):
    validate_windows_acl(security(owner=owner), TRUSTED)
    with pytest.raises(WindowsTransportPermissionError):
        validate_windows_acl(security(owner=OTHER), TRUSTED)


@pytest.mark.parametrize(
    "mask", [0x2, 0x4, 0x10, 0x100, 0x10000, 0x40000, 0x80000, 0x40000000, 0x10000000]
)
def test_file_modification_deletion_and_security_changes_are_not_granted_to_other_principals(
    mask,
):
    with pytest.raises(WindowsTransportPermissionError):
        validate_windows_acl(security(aces=(WindowsAce(0, 0, mask, OTHER),)), TRUSTED)


@pytest.mark.parametrize(
    "mask", [0x2, 0x10, 0x40, 0x100, 0x10000, 0x40000, 0x80000, 0x40000000, 0x10000000]
)
def test_each_ancestor_denies_other_users_replacement_and_security_control(mask):
    with pytest.raises(WindowsTransportPermissionError):
        validate_windows_acl(
            security(directory=True, aces=(WindowsAce(0, 0, mask, OTHER),)), TRUSTED
        )


def test_read_execute_and_unrelated_subdirectory_creation_are_not_misclassified():
    validate_windows_acl(security(aces=(WindowsAce(0, 0, 0xA0000000, OTHER),)), TRUSTED)
    validate_windows_acl(
        security(directory=True, aces=(WindowsAce(0, 0, 0x4, OTHER),)), TRUSTED
    )


def test_inherit_only_does_not_apply_but_inherited_dangerous_rights_do_and_deny_never_manufactures_a_pass():
    validate_windows_acl(
        security(aces=(WindowsAce(0, 0x08, 0x10000000, OTHER),)), TRUSTED
    )
    for aces in [
        (WindowsAce(0, 0x10, 0x2, OTHER),),
        (WindowsAce(1, 0, 0x10000000, OTHER), WindowsAce(0, 0, 0x2, OTHER)),
    ]:
        with pytest.raises(WindowsTransportPermissionError):
            validate_windows_acl(security(aces=aces), TRUSTED)


@pytest.mark.parametrize(
    "ace",
    [
        WindowsAce(5, 0, 0, USER),
        WindowsAce(9, 0, 0, USER),
        WindowsAce(0, 0x20, 0, USER),
        WindowsAce(0, 0, 0x02000000, USER),
        WindowsAce(0, 0, 0x01000000, USER),
        WindowsAce(0, 0, -1, USER),
        WindowsAce(0, 0, 0, "unresolved-account"),
    ],
)
def test_unsupported_conditional_object_flags_masks_or_sid_forms_fail_closed(ace):
    with pytest.raises(WindowsTransportPermissionError):
        validate_windows_acl(security(aces=(ace,)), TRUSTED)


def test_null_unreadable_or_oversized_dacl_cannot_qualify():
    for dacl in (None, (WindowsAce(0, 0, 0, USER),) * 1025):
        with pytest.raises(WindowsTransportPermissionError):
            validate_windows_acl(replace(security(), dacl=dacl), TRUSTED)


@pytest.mark.parametrize(
    "path",
    [
        r"\\server\share\webjam.exe",
        r"\\?\C:\safe\webjam.exe",
        r"C:relative.exe",
        r"C:\safe\..\webjam.exe",
        r"C:\safe\webjam.exe:stream",
        r"C:\safe\NUL.exe",
        "C:\\safe\\tail.\\webjam.exe",
        "C:\\safe\\tail \\webjam.exe",
    ],
)
def test_device_remote_stream_reserved_and_ambiguous_paths_are_refused(path):
    with pytest.raises(WindowsTransportPermissionError):
        _path_chain(path)


def test_pins_every_ancestor_and_rechecks_identity_owner_dacl_and_reparse_before_launch():
    api = FakeWindowsApi()
    with WindowsBinaryGuard(TARGET, api=api) as guard:
        assert [path for path, _ in api.opened] == list(_path_chain(TARGET))
        guard.check_current(TARGET)
        for changed in [
            replace(security(), identity=(123, b"2" * 16)),
            replace(security(), owner=OTHER),
            replace(security(), attributes=0x420),
            replace(security(), links=2),
            replace(security(), path=r"C:\elsewhere.exe"),
            replace(security(), dacl=(WindowsAce(0, 0, 0x2, OTHER),)),
        ]:
            api.overrides[TARGET] = changed
            with pytest.raises(WindowsTransportPermissionError):
                guard.check_current(TARGET)
            api.overrides.clear()
    assert api.closed == list(reversed([path for path, _ in api.opened]))
    with pytest.raises(WindowsTransportPermissionError):
        guard.check_current(TARGET)


@pytest.mark.parametrize(
    "problem",
    [
        "ancestor-owner",
        "ancestor-delete-child",
        "ancestor-reparse",
        "no-acl-volume",
        "unreadable",
    ],
)
def test_partial_open_failure_closes_all_handles_and_never_returns_a_grant(problem):
    api = FakeWindowsApi()
    parent = _path_chain(TARGET)[-2]
    if problem == "ancestor-owner":
        api.overrides[parent] = security(parent, directory=True, owner=OTHER)
    if problem == "ancestor-delete-child":
        api.overrides[parent] = security(
            parent, directory=True, aces=(WindowsAce(0, 0, 0x40, OTHER),)
        )
    if problem == "ancestor-reparse":
        api.overrides[parent] = replace(
            security(parent, directory=True), attributes=0x410
        )
    if problem == "no-acl-volume":
        api.volume_ok = False
    if problem == "unreadable":
        api.inspect = lambda _: (_ for _ in ()).throw(
            OSError("Synthetic private descriptor error")
        )
    with pytest.raises(WindowsTransportPermissionError):
        with WindowsBinaryGuard(TARGET, api=api):
            pytest.fail("Unsafe guard entered")
    assert api.closed == list(reversed([path for path, _ in api.opened]))


def test_native_adapter_refuses_unsupported_os_before_loading_windows_api(monkeypatch):
    # Exercise the real unsupported-platform branch on every test host. Replace
    # only this module's OS view; never mutate the process-wide os module.
    monkeypatch.setattr(permissions, "os", SimpleNamespace(name="posix"))
    loads = []

    def unexpected_native_load(*args, **kwargs):
        loads.append((args, kwargs))
        raise AssertionError("Unsupported OS attempted to load a Windows DLL")

    monkeypatch.setattr(ctypes, "WinDLL", unexpected_native_load, raising=False)
    with pytest.raises(WindowsTransportPermissionError):
        _NativeWindowsApi()
    with pytest.raises(WindowsTransportPermissionError):
        with WindowsBinaryGuard(TARGET):
            pytest.fail("Native Windows guard must not be simulated implicitly")
    assert loads == []


@pytest.mark.parametrize("directory", [False, True])
def test_native_open_arguments_are_read_only_noninheritable_and_deny_write_delete_sharing(
    directory,
):
    # Inspect API arguments without loading WinDLL or claiming Windows sharing
    # semantics have been exercised on this host.
    api = _NativeWindowsApi.__new__(_NativeWindowsApi)
    api.c = ctypes
    calls = []
    api.create = lambda *args: calls.append(args) or 123
    assert api.open_pinned(TARGET, directory=directory) == 123
    assert calls == [
        (
            TARGET,
            0x20000 | 0x80 | (0x1 if directory else 0x80000000),
            1,
            None,
            3,
            0x00200000 | 0x02000000,
            None,
        )
    ]


def test_sid_codec_rejects_truncation_and_bad_revision():
    assert (
        _sid_text(b"\x01\x01\x00\x00\x00\x00\x00\x05" + struct.pack("<I", 18))
        == "S-1-5-18"
    )
    for value in (b"", b"\x02\x00" + b"\x00" * 6, b"\x01\x10" + b"\x00" * 70):
        with pytest.raises(WindowsTransportPermissionError):
            _sid_text(value)


def _sid_bytes(sid):
    _, revision, authority, *values = sid.split("-")
    return (
        bytes([int(revision), len(values)])
        + int(authority).to_bytes(6, "big")
        + b"".join(struct.pack("<I", int(value)) for value in values)
    )


def _raw_descriptor(*, kind=0, flags=0, mask=0x120089, bad=None):
    """Actual SID/DACL/ACE bytes behind fake descriptor-query callbacks.

    No Windows DLL executes here; native API ownership/allocation and ABI still
    require a Windows runner. The real Python bounds/decoder code is exercised.
    """
    c = ctypes
    owner = _sid_bytes(USER)
    trustee = _sid_bytes(OTHER)
    ace = struct.pack("<BBHI", kind, flags, 8 + len(trustee), mask) + trustee
    dacl = struct.pack("<BBHHH", 2, 0, 8 + len(ace), 1, 0) + ace
    data = c.create_string_buffer(b"\x00" * 20 + owner + dacl)
    start, end = c.addressof(data), c.addressof(data) + len(data) - 1
    owner_address, dacl_address = start + 20, start + 20 + len(owner)
    api = _NativeWindowsApi.__new__(_NativeWindowsApi)
    api.c = c
    api.w = SimpleNamespace(BOOL=c.c_int32)
    freed = []

    def pointer(output, value):
        c.cast(output, c.POINTER(c.c_void_p))[0] = value

    def get_security(handle, object_type, flags, owner, group, dacl, sacl, output):
        assert object_type == 1 and flags == 5
        pointer(output, start)
        return 0

    def owner_query(descriptor, output, defaulted):
        pointer(output, owner_address)
        return True

    def dacl_query(descriptor, present, output, defaulted):
        c.cast(present, c.POINTER(c.c_int32))[0] = 1
        pointer(output, dacl_address)
        return True

    def get_ace(dacl, index, output):
        assert index == 0
        pointer(output, dacl_address + 8)
        return True

    api.get_security = get_security
    api.sd_length = lambda _: end - start
    api.sd_owner = owner_query
    api.sd_dacl = dacl_query
    api.valid_acl = lambda _: True
    api.get_ace = get_ace
    api.local_free = lambda address: freed.append(address.value)
    if bad == "owner-pointer":
        api.sd_owner = lambda descriptor, output, defaulted: (
            pointer(output, end + 1) or True
        )
    if bad == "ace-pointer":
        api.get_ace = lambda dacl, index, output: pointer(output, end + 1) or True
    if bad == "null-dacl":
        api.sd_dacl = lambda descriptor, present, output, defaulted: True
    if bad == "unreadable":
        api.get_security = lambda *args: 5
    if bad == "acl-size":
        c.memmove(dacl_address + 2, struct.pack("<H", 65535), 2)
    if bad == "ace-size":
        c.memmove(dacl_address + 8 + 2, struct.pack("<H", 65535), 2)
    if bad == "sid-size":
        c.memmove(dacl_address + 8 + 8 + 1, b"\x0f", 1)
    if bad == "sid-revision":
        c.memmove(owner_address, b"\x02", 1)
    if bad == "extra-ace-bytes":
        c.memmove(dacl_address + 8 + 2, struct.pack("<H", len(ace) - 4), 2)
    if bad == "free-failure":
        api.local_free = lambda address: freed.append(address.value) or address.value
    return api, data, freed, start


def test_raw_descriptor_decodes_owner_and_applying_ace_without_external_data():
    api, buffer, freed, start = _raw_descriptor()
    owner, aces = api._descriptor(123)
    assert owner == USER and aces == (WindowsAce(0, 0, 0x120089, OTHER),)
    assert freed == [start]
    validate_windows_acl(replace(security(), owner=owner, dacl=aces), TRUSTED)


@pytest.mark.parametrize(
    "bad",
    [
        "owner-pointer",
        "ace-pointer",
        "null-dacl",
        "unreadable",
        "acl-size",
        "ace-size",
        "sid-size",
        "sid-revision",
        "extra-ace-bytes",
        "free-failure",
    ],
)
def test_raw_descriptor_bounds_or_cleanup_failure_never_qualifies(bad):
    api, buffer, freed, start = _raw_descriptor(bad=bad)
    with pytest.raises(WindowsTransportPermissionError):
        api._descriptor(123)
    assert freed == ([] if bad == "unreadable" else [start])


@pytest.mark.parametrize("kind", [5, 9])
def test_raw_object_or_callback_ace_is_refused_before_it_can_supply_a_grant(kind):
    api, buffer, freed, start = _raw_descriptor(kind=kind)
    with pytest.raises(WindowsTransportPermissionError):
        api._descriptor(123)
    assert freed == [start]


def test_native_inspect_consumes_explicit_bounded_metadata_layouts_and_handle_path():
    api, buffer, freed, start = _raw_descriptor()
    calls = []

    def metadata(handle, kind, destination, size):
        values = {
            0: struct.pack("<qqqqI4x", 1, 2, 3, 4, 0x20),
            1: struct.pack("<qqIBB2x", 4096, 128, 1, 0, 0),
            18: struct.pack("<Q16s", 123, b"1" * 16),
        }
        calls.append((kind, size))
        assert size == len(values[kind])
        ctypes.memmove(destination, values[kind], size)
        return True

    def final_path(handle, output, size, flags):
        assert size == 32768 and flags == 0
        output.value = "\\\\?\\" + TARGET
        return len(output.value)

    api.info = metadata
    api.final_path = final_path
    details = api.inspect(123)
    assert calls == [(0, 40), (1, 24), (18, 24)]
    assert (
        details.identity == (123, b"1" * 16)
        and details.size == 128
        and not details.directory
    )
    assert details.path == "\\\\?\\" + TARGET and details.owner == USER


@pytest.mark.parametrize("outcome", ["false", "raise"])
def test_handle_cleanup_attempts_every_handle_and_exit_never_acknowledges_failure(
    outcome,
):
    api = FakeWindowsApi()

    def close(handle):
        api.closed.append(handle)
        if handle == TARGET:
            if outcome == "raise":
                raise OSError("Synthetic private handle-close error")
            return False

    api.close = close
    with pytest.raises(WindowsTransportPermissionError):
        with WindowsBinaryGuard(TARGET, api=api):
            pass
    assert api.closed == list(reversed([path for path, _ in api.opened]))


def _synthetic_platform(monkeypatch):
    proxy = SimpleNamespace(**vars(runtime.os))
    proxy.name = "nt"
    proxy.environ = {}
    monkeypatch.setattr(runtime, "os", proxy)
    monkeypatch.setattr(runtime, "sys", SimpleNamespace(platform="win32"))


def test_windows_0777_reaches_mandatory_signature_only_after_acl_acceptance_and_pins_through_spawn(
    tmp_path, monkeypatch
):
    target = tmp_path / "webjam-fabric.exe"
    header = bytearray(128)
    header[:2] = b"MZ"
    header[60:64] = (64).to_bytes(4, "little")
    header[64:70] = b"PE\x00\x00\x64\x86"
    target.write_bytes(header)
    target.chmod(0o777)
    _synthetic_platform(monkeypatch)
    events = []

    class SyntheticGuard:
        def __init__(self, path):
            self.active = False

        def __enter__(self):
            self.active = True
            events.append("pin")
            return self

        def __exit__(self, *_):
            self.active = False
            events.append("release")

        def check_current(self, path):
            assert self.active
            events.append("acl")

    monkeypatch.setattr(runtime, "WindowsBinaryGuard", SyntheticGuard)
    monkeypatch.setattr(
        runtime, "_verify_platform_signature", lambda path: events.append("signature")
    )
    monkeypatch.setattr(
        runtime.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
    )
    monkeypatch.setattr(
        runtime.subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False
    )

    def spawn(argv, **options):
        assert events[-1] == "acl" and "release" not in events
        assert (
            argv == [str(target)]
            and options["shell"] is False
            and options["env"] == {}
            and options["close_fds"] is True
        )
        events.append("spawn")
        return object()

    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    process = runtime.TransportProcess(
        target,
        expected_build="synthetic",
        expected_sha256=hashlib.sha256(header).hexdigest(),
        expected_machine="x86_64",
        require_platform_signature=True,
    )
    process._spawn_validated()
    assert events == ["pin", "acl", "signature", "acl", "spawn", "release"]


@pytest.mark.parametrize("stage", ["permissions", "hash", "architecture", "signature"])
def test_windows_refusal_never_spawns_and_releases_the_guard(
    tmp_path, monkeypatch, stage
):
    target = tmp_path / "webjam.exe"
    target.write_bytes(b"Synthetic untrusted binary")
    target.chmod(0o777)
    _synthetic_platform(monkeypatch)
    events = []

    class SyntheticGuard:
        def __init__(self, path):
            pass

        def __enter__(self):
            events.append("pin")
            return self

        def __exit__(self, *_):
            events.append("release")

        def check_current(self, path):
            if stage == "permissions":
                raise WindowsTransportPermissionError("Synthetic permission refusal")

    monkeypatch.setattr(runtime, "WindowsBinaryGuard", SyntheticGuard)

    def signature(path):
        if stage == "signature":
            raise runtime.TransportLaunchError("Synthetic signature refusal")

    monkeypatch.setattr(runtime, "_verify_platform_signature", signature)
    monkeypatch.setattr(
        runtime.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("Refused image must not spawn"),
    )
    process = runtime.TransportProcess(
        target,
        expected_build="synthetic",
        require_platform_signature=True,
        expected_sha256="0" * 64 if stage == "hash" else None,
        expected_machine="x86_64" if stage == "architecture" else None,
    )
    with pytest.raises(runtime.TransportLaunchError):
        process._spawn_validated()
    assert events == ["pin", "release"]


def test_guard_exit_failure_after_process_creation_terminates_child_instead_of_orphaning_it(
    tmp_path, monkeypatch
):
    target = tmp_path / "webjam.exe"
    target.write_bytes(b"Synthetic transport")
    target.chmod(0o777)
    _synthetic_platform(monkeypatch)
    events = []
    child = object()

    class SyntheticGuard:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def check_current(self, path):
            pass

        def __exit__(self, *_):
            raise WindowsTransportPermissionError("Synthetic close failure")

    monkeypatch.setattr(runtime, "WindowsBinaryGuard", SyntheticGuard)
    monkeypatch.setattr(
        runtime, "_verify_platform_signature", lambda path: events.append("signature")
    )
    monkeypatch.setattr(
        runtime.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
    )
    monkeypatch.setattr(
        runtime.subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False
    )
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: child)
    process = runtime.TransportProcess(
        target, expected_build="synthetic", require_platform_signature=True,
        expected_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        process,
        "_terminate_process",
        lambda created: events.append(("terminated", created)),
    )
    monkeypatch.setattr(
        process,
        "_close_process_streams",
        lambda created: events.append(("closed", created)),
    )
    with pytest.raises(runtime.TransportLaunchError):
        process._spawn_validated()
    assert events == ["signature", ("terminated", child), ("closed", child)]
