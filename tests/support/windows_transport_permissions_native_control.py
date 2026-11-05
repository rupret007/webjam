"""Explicit real Windows ACL/share diagnostic, separate from product acceptance.

The Windows-only CLI runs this file with --confcutdir tests/support. Its support
filename stays outside default portable pytest discovery; unsupported CLI hosts
exit 2 instead of skipping or passing. The actual guard loads directly from its
source file; no API, metadata, ancestry or case assertion is substituted.
Only unique newly created files/directories below the lab root have ACLs changed.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

_UNSUPPORTED = "Native Windows transport ACL diagnostic requires Windows; no native API tests executed."
# Make the CLI's unsupported-host result independent of pytest availability.
if __name__ == "__main__" and os.name != "nt":
    print(_UNSUPPORTED, file=sys.stderr)
    raise SystemExit(2)

import pytest  # noqa: E402 - unsupported CLI hosts must exit before loading pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def native_source():
    source = ROOT / "services/windows_transport_permissions.py"
    name = "webjam_native_acl_qualification"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    # No API substitution: this class loads the actual advapi32/kernel32 DLLs.
    output = ROOT / "out/windows-transport-acl-native"
    output.mkdir(parents=True, exist_ok=True)
    metadata = {
        "intended_coverage": "Actual Windows ACL, handle sharing and owned Python process controls; not WebJam/signing/audio acceptance",
        "result_authority": "Retained pytest exit status and JUnit results; this source/environment receipt is not a passing verdict",
        "python": sys.version,
        "windows": list(sys.getwindowsversion()),
        "guard_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "api_mocked": False,
        "existing_acl_modified": False,
        "lab_location": "Unique new directory under existing LOCALAPPDATA; real ancestry is checked unchanged",
    }
    (output / "source-and-environment.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return module


class NativeLab:
    def __init__(self, root, module):
        self.root, self.module = root, module
        self.api = module._NativeWindowsApi()
        self.user = self.api.current_user_sid()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        self.kernel.GetSystemDirectoryW.restype = wintypes.UINT
        directory = ctypes.create_unicode_buffer(32768)
        length = self.kernel.GetSystemDirectoryW(directory, len(directory))
        assert 0 < length < len(directory), "GetSystemDirectoryW failed"
        self.system_directory = Path(directory.value)
        self.icacls = self.system_directory / "icacls.exe"
        assert self.icacls.is_file()
        self.kernel.DeleteFileW.argtypes = [wintypes.LPCWSTR]
        self.kernel.DeleteFileW.restype = wintypes.BOOL
        self.kernel.MoveFileExW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        self.kernel.MoveFileExW.restype = wintypes.BOOL

    def acl(self, target, *arguments):
        target = Path(target)
        assert target == self.root or target.parent.resolve(strict=True).is_relative_to(
            self.root
        )
        assert target.exists() and not target.is_symlink(), (
            "Only existing owned lab nodes can receive test ACLs"
        )
        result = subprocess.run(
            [str(self.icacls), str(target), *arguments],
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={"SystemRoot": str(self.system_directory.parent)},
        )
        assert result.returncode == 0, (
            f"Owned lab icacls failed ({result.returncode}): {result.stderr[-2000:]}"
        )

    def private(self, target):
        # All targets are newly created by this fixture. No parent, source
        # runtime, system file or existing ACL is repaired to make a test pass.
        self.acl(
            target,
            "/inheritance:r",
            "/grant:r",
            f"*{self.user}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        )

    def file(self, name="transport.exe", content=b"Synthetic owned transport metadata"):
        target = self.root / name
        target.write_bytes(content)
        return target

    def writer(self, target):
        # Native create API, exact file path, no shell. Sharing all access here
        # lets the guard's own narrower sharing policy decide the outcome.
        return self.api.create(str(target), 0x40000000, 7, None, 3, 0, None)

    def assert_sharing_refusal(self, operation):
        ctypes.set_last_error(0)
        result = operation()
        error = ctypes.get_last_error()
        assert not result and error == 32, (
            f"Expected native sharing violation, got Win32 error {error}"
        )


@pytest.fixture
def lab(native_source):
    # Shared CI workspace/TEMP directories are not silently trusted. Use an
    # existing per-user location, still inspecting every real ancestor unchanged.
    parent = Path(os.environ["LOCALAPPDATA"]).resolve(strict=True)
    assert parent.is_dir()
    with tempfile.TemporaryDirectory(
        prefix="webjam-acl-native-", dir=parent
    ) as temporary:
        root = Path(temporary).resolve(strict=True)
        value = NativeLab(root, native_source)
        value.private(root)
        yield value


@pytest.mark.parametrize("readonly", [False, True])
def test_private_file_attributes_do_not_substitute_for_actual_acl_acceptance(
    lab, readonly, record_property
):
    target = lab.file()
    target.chmod(stat.S_IREAD if readonly else stat.S_IWRITE)
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
        record_property("actual_windows_mode", oct(mode))
        if readonly:
            assert not mode & 0o222
        else:
            assert mode & 0o022, (
                "Control must reproduce ordinary Windows group/other mode write bits"
            )
        with lab.module.WindowsBinaryGuard(str(target)) as guard:
            guard.check_current(str(target))
    finally:
        target.chmod(stat.S_IWRITE)


def test_actual_handle_metadata_sid_and_dacl_decode(lab):
    content = b"Real Win32 descriptor and metadata control"
    target = lab.file(content=content)
    handle = lab.api.open_pinned(str(target), directory=False)
    try:
        details = lab.api.inspect(handle)
        # Elevated runners can default newly created objects to TokenOwner's
        # Administrators SID rather than TokenUser. Do not change ownership to
        # make qualification pass; retain the production trusted-owner boundary.
        trusted = {lab.user, "S-1-5-18", "S-1-5-32-544", lab.api.installer_sid()}
        assert details.owner in trusted
        assert (
            details.size == len(content)
            and details.links == 1
            and not details.directory
        )
        assert len(details.identity[1]) == 16 and any(details.identity[1])
        assert details.dacl and any(
            ace.sid == lab.user and ace.kind == 0 for ace in details.dacl
        )
        assert lab.module._same_path(details.path, str(target))
    finally:
        assert lab.api.close(handle)


@pytest.mark.parametrize("permissive", ["file", "parent"])
def test_actual_permissive_file_or_delete_child_parent_ace_denies_the_guard(
    lab, permissive
):
    parent = lab.root / "owned-parent"
    parent.mkdir()
    target = parent / "transport.exe"
    target.write_bytes(b"Owned unsafe-ACL negative control")
    with lab.module.WindowsBinaryGuard(str(target)):
        pass
    lab.acl(
        target if permissive == "file" else parent,
        "/grant",
        "*S-1-1-0:W" if permissive == "file" else "*S-1-1-0:(DC)",
    )
    with pytest.raises(lab.module.WindowsTransportPermissionError):
        with lab.module.WindowsBinaryGuard(str(target)):
            pytest.fail("Permissive actual DACL was accepted")


def test_actual_write_delete_and_rename_are_fenced_until_guard_release(lab):
    target = lab.file()
    renamed = lab.root / "renamed.exe"
    invalid = ctypes.c_void_p(-1).value
    with lab.module.WindowsBinaryGuard(str(target)) as guard:
        ctypes.set_last_error(0)
        writer = lab.writer(target)
        error = ctypes.get_last_error()
        try:
            assert writer in {None, invalid} and error == 32
        finally:
            if writer not in {None, invalid}:
                assert lab.api.close(writer)
        lab.assert_sharing_refusal(lambda: lab.kernel.DeleteFileW(str(target)))
        lab.assert_sharing_refusal(
            lambda: lab.kernel.MoveFileExW(str(target), str(renamed), 0)
        )
        guard.check_current(str(target))
    writer = lab.writer(target)
    assert writer not in {None, invalid}, (
        "Guard cleanup left the owned file write-locked"
    )
    assert lab.api.close(writer)
    assert lab.kernel.MoveFileExW(str(target), str(renamed), 0)
    assert lab.kernel.DeleteFileW(str(renamed))


def test_actual_preexisting_writer_prevents_guard_and_failure_cleanup_releases_ancestors(
    lab,
):
    parent = lab.root / "owned-parent"
    parent.mkdir()
    target = parent / "transport.exe"
    target.write_bytes(b"Owned preexisting writer and ancestor cleanup control")
    writer = lab.writer(target)
    assert writer not in {None, ctypes.c_void_p(-1).value}
    try:
        with pytest.raises(lab.module.WindowsTransportPermissionError):
            with lab.module.WindowsBinaryGuard(str(target)):
                pytest.fail(
                    "Existing actual writer did not conflict with guard sharing"
                )
    finally:
        assert lab.api.close(writer)
    with lab.module.WindowsBinaryGuard(str(target)) as guard:
        guard.check_current(str(target))
    # Both names remain within the owned TemporaryDirectory, even if restoring
    # the original name fails. Fixture cleanup never follows a moved lab root.
    renamed = lab.root / "renamed-parent"
    assert lab.kernel.MoveFileExW(str(parent), str(renamed), 0), (
        "Failure cleanup left a lab ancestor pinned"
    )
    assert lab.kernel.MoveFileExW(str(renamed), str(parent), 0)


def test_actual_python_createprocess_succeeds_while_image_guard_is_held(
    lab, record_property
):
    source = Path(sys.executable).resolve(strict=True)
    target = lab.root / "python-probe.exe"
    shutil.copyfile(source, target)
    # The interpreter's runtime DLL and optional adjacent C runtime are copied
    # read-only from this CI Python installation into our new private tree.
    # No compiler, installer, package download or signing operation is involved.
    names = [
        f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        "python3.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ]
    copied = []
    for name in names:
        dependency = source.parent / name
        if dependency.is_file():
            shutil.copyfile(dependency, lab.root / name)
            copied.append(name)
    assert names[0] in copied, (
        "The actual CI Python runtime DLL is required for this owned copy"
    )
    with lab.module.WindowsBinaryGuard(str(target)) as guard:
        result = subprocess.run(
            [str(target), "--version"],
            cwd=lab.root,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={"SystemRoot": str(lab.system_directory.parent)},
            close_fds=True,
        )
        assert result.returncode == 0, (
            f"Owned Python CreateProcess failed ({result.returncode}): {result.stderr[-2000:]}"
        )
        assert (
            result.stdout.strip()
            == f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )
        guard.check_current(str(target))
    record_property(
        "owned_python_sha256", hashlib.sha256(target.read_bytes()).hexdigest()
    )
    record_property("copied_runtime_dlls", ",".join(copied))


_NATIVE_PATH = "tests/support/windows_transport_permissions_native_control.py"
_EXPECTED_CASES = tuple(
    f"{_NATIVE_PATH}::{name}"
    for name in (
        "test_private_file_attributes_do_not_substitute_for_actual_acl_acceptance[False]",
        "test_private_file_attributes_do_not_substitute_for_actual_acl_acceptance[True]",
        "test_actual_handle_metadata_sid_and_dacl_decode",
        "test_actual_permissive_file_or_delete_child_parent_ace_denies_the_guard[file]",
        "test_actual_permissive_file_or_delete_child_parent_ace_denies_the_guard[parent]",
        "test_actual_write_delete_and_rename_are_fenced_until_guard_release",
        "test_actual_preexisting_writer_prevents_guard_and_failure_cleanup_releases_ancestors",
        "test_actual_python_createprocess_succeeds_while_image_guard_is_held",
    )
)


class NativeExecutionProof:
    """A zero pytest status alone cannot qualify collection-only/subset runs."""

    def __init__(self):
        self.collected, self.reports, self.invalid = [], {}, False

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items[:9]]
        if len(session.items) != 8 or set(self.collected) != set(_EXPECTED_CASES):
            self.invalid = True

    def pytest_runtest_logreport(self, report):
        if report.nodeid not in _EXPECTED_CASES or report.when not in {
            "setup",
            "call",
            "teardown",
        }:
            self.invalid = True
            return
        phases = self.reports.setdefault(report.nodeid, {})
        if (
            report.when in phases
            or report.outcome != "passed"
            or hasattr(report, "wasxfail")
        ):
            self.invalid = True
        phases[report.when] = report.outcome

    def qualified(self):
        return (
            not self.invalid
            and len(self.collected) == 8
            and set(self.collected) == set(_EXPECTED_CASES)
            and set(self.reports) == set(_EXPECTED_CASES)
            and all(
                phases == {"setup": "passed", "call": "passed", "teardown": "passed"}
                for phases in self.reports.values()
            )
        )


def _safe_directory(path):
    details = path.lstat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or getattr(details, "st_file_attributes", 0) & 0x400
        or path.resolve(strict=True) != path
    ):
        raise ValueError("Output directory is not an ordinary canonical directory")


def _reserve_output():
    root = Path(ROOT)
    _safe_directory(root)
    parent = root / "out"
    try:
        parent.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _safe_directory(parent)
    owned = parent / "windows-transport-acl-native"
    owned.mkdir(mode=0o700)  # Never reuse or clean an existing directory/target.
    _safe_directory(owned)
    for name, text in (
        ("pytest.ini", "[pytest]\n"),
        ("results.xml", ""),
        ("execution.json", ""),
    ):
        with (owned / name).open("x", encoding="utf-8") as output:
            output.write(text)
    return owned


def _source_hashes():
    source = Path(__file__).resolve()
    if source != Path(ROOT) / _NATIVE_PATH:
        raise ValueError("Diagnostic source does not match its checkout root")
    return {
        "control": hashlib.sha256(source.read_bytes()).hexdigest(),
        "guard": hashlib.sha256(
            (Path(ROOT) / "services/windows_transport_permissions.py").read_bytes()
        ).hexdigest(),
    }


def main(argv=None):
    if os.name != "nt":
        print(_UNSUPPORTED, file=sys.stderr)
        return 2
    if list(sys.argv[1:] if argv is None else argv) or any(
        os.environ.get(key) for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")
    ):
        print(
            "Native diagnostic refuses CLI options and inherited pytest selectors/plugins.",
            file=sys.stderr,
        )
        return 2
    try:
        before = _source_hashes()
        owned = _reserve_output()
        proof = NativeExecutionProof()
        aborted = False
        previous = os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        try:
            try:
                code = int(
                    pytest.main(
                        [
                            str(Path(__file__).resolve()),
                            "-q",
                            "-c",
                            str(owned / "pytest.ini"),
                            "--rootdir",
                            str(ROOT),
                            "--noconftest",
                            "--confcutdir",
                            str(Path(__file__).resolve().parent),
                            "--override-ini",
                            f"cache_dir={owned / 'cache'}",
                            "--junitxml",
                            str(owned / "results.xml"),
                            "--basetemp",
                            str(owned / "pytest"),
                        ],
                        plugins=[proof],
                    )
                )
            except SystemExit:
                code, aborted = 2, True
        finally:
            if previous is None:
                os.environ.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
            else:
                os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = previous
        after = _source_hashes()
        qualified = not aborted and code == 0 and proof.qualified() and before == after
        (owned / "execution.json").write_text(
            json.dumps(
                {
                    "qualified": qualified,
                    "pytest_exit": code,
                    "runner_aborted": aborted,
                    "expected": list(_EXPECTED_CASES),
                    "collected": proof.collected,
                    "reports": proof.reports,
                    "source_hashes_before": before,
                    "source_hashes_after": after,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if qualified:
            return 0
        print(
            "Native diagnostic did not establish eight exact executed passes with unchanged sources.",
            file=sys.stderr,
        )
        return code or 2
    except (Exception, SystemExit):
        print(
            "Native diagnostic could not reserve fresh outputs or confirm execution; no qualification recorded.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
