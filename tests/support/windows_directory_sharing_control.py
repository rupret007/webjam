"""Explicit native Windows directory-share regression; no simulated API grants."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DirectorySharing(unittest.TestCase):
    def setUp(self):
        self.api = permissions._NativeWindowsApi()
        parent = Path(os.environ['LOCALAPPDATA']).resolve(strict=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='webjam-directory-share-', dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Change only our new lab's ACL. Never repair existing ancestry.
        system = ctypes.create_unicode_buffer(32768)
        kernel = self.api.kernel
        kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        kernel.GetSystemDirectoryW.restype = wintypes.UINT
        length = kernel.GetSystemDirectoryW(system, len(system))
        self.assertTrue(0 < length < len(system))
        completed = subprocess.run(
            [str(Path(system.value) / 'icacls.exe'), str(self.root), '/inheritance:r',
             '/grant:r', f'*{self.api.current_user_sid()}:(OI)(CI)F',
             '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
            capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0, 'Owned lab ACL setup failed')
        self.parent = self.root / 'owned-parent'
        self.parent.mkdir()
        self.target = self.parent / 'transport.exe'
        self.target.write_bytes(b'Owned native directory sharing control')
        kernel.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        kernel.MoveFileExW.restype = wintypes.BOOL

    def delete_open(self):
        ctypes.set_last_error(0)
        handle = self.api.create(str(self.parent), 0x10000, 7, None, 3,
                                 0x02000000 | 0x00200000, None)
        return handle, ctypes.get_last_error()

    def invalid(self, handle):
        return handle in {None, ctypes.c_void_p(-1).value}

    def can_delete_open(self):
        handle, _ = self.delete_open()
        self.assertFalse(self.invalid(handle), 'Native DELETE-open positive control failed')
        self.assertTrue(self.api.close(handle))

    def refuses_delete_open(self):
        handle, error = self.delete_open()
        try:
            self.assertTrue(self.invalid(handle), 'Pinned directory admitted DELETE access')
            self.assertEqual(error, 32, 'Refusal must be ERROR_SHARING_VIOLATION')
        finally:
            if not self.invalid(handle):
                self.assertTrue(self.api.close(handle))

    def test_directory_only_fence_without_leaf_handle(self):
        self.can_delete_open()
        handle = self.api.open_pinned(str(self.parent), directory=True)
        try:
            self.refuses_delete_open()
        finally:
            self.assertTrue(self.api.close(handle))
        self.can_delete_open()

    def test_preexisting_delete_refuses_directory_pin(self):
        handle, _ = self.delete_open()
        self.assertFalse(self.invalid(handle))
        try:
            with self.assertRaises(permissions.WindowsTransportPermissionError):
                pinned = self.api.open_pinned(str(self.parent), directory=True)
                # Unexpected success must not leave a handle pinned on failure.
                self.assertTrue(self.api.close(pinned))
        finally:
            self.assertTrue(self.api.close(handle))
        self.can_delete_open()

    def test_full_guard_fences_ancestor_delete_and_rename_until_release(self):
        renamed = self.root / 'renamed-parent'
        with permissions.WindowsBinaryGuard(str(self.target)) as guard:
            self.refuses_delete_open()
            ctypes.set_last_error(0)
            moved = self.api.kernel.MoveFileExW(str(self.parent), str(renamed), 0)
            error = ctypes.get_last_error()
            if moved:
                self.assertTrue(self.api.kernel.MoveFileExW(str(renamed), str(self.parent), 0))
            self.assertFalse(moved, 'Guard admitted ancestor rename')
            self.assertEqual(error, 32)
            guard.check_current(str(self.target))
        self.can_delete_open()
        self.assertTrue(self.api.kernel.MoveFileExW(str(self.parent), str(renamed), 0))
        self.assertTrue(self.api.kernel.MoveFileExW(str(renamed), str(self.parent), 0))

    def test_full_guard_refuses_existing_ancestor_delete_and_cleans_up(self):
        # Prove this ancestry is admissible before introducing the conflict.
        with permissions.WindowsBinaryGuard(str(self.target)):
            pass
        handle, _ = self.delete_open()
        self.assertFalse(self.invalid(handle))
        guard = permissions.WindowsBinaryGuard(str(self.target))
        try:
            with self.assertRaises(permissions.WindowsTransportPermissionError):
                with guard:
                    self.fail('Guard admitted an existing ancestor DELETE handle')
            self.assertEqual(guard._handles, [])
        finally:
            self.assertTrue(self.api.close(handle))
        with permissions.WindowsBinaryGuard(str(self.target)) as recovered:
            recovered.check_current(str(self.target))
        self.can_delete_open()


def main():
    if os.name != 'nt' or sys.argv[1:]:
        print('Explicit native Windows control required; no selectors or skips.', file=sys.stderr)
        return 2
    global permissions
    source = ROOT / 'services/windows_transport_permissions.py'
    name = 'webjam_directory_sharing_source'
    spec = importlib.util.spec_from_file_location(name, source)
    permissions = importlib.util.module_from_spec(spec)
    sys.modules[name] = permissions
    spec.loader.exec_module(permissions)
    paths = {'guard': source, 'control': Path(__file__)}
    before = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DirectorySharing)
    expected = {case.id() for case in suite}
    result = unittest.TestResult()
    suite.run(result)
    after = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}
    qualified = result.wasSuccessful() and not result.skipped and result.testsRun == len(expected) == 4 and before == after
    print(json.dumps({'qualified': qualified, 'tests_run': result.testsRun,
                      'expected': sorted(expected), 'failures': [case.id() for case, _ in result.failures],
                      'errors': [case.id() for case, _ in result.errors], 'skips': len(result.skipped),
                      'source_hashes_before': before, 'source_hashes_after': after,
                      'python': sys.version.split()[0], 'api_mocked': False}))
    return 0 if qualified else 1


if __name__ == '__main__':
    raise SystemExit(main())
