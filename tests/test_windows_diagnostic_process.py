"""Native Windows controls for diagnostic tree ownership and startup failure."""
import ctypes
import json
import os
import sys

import pytest

from tests.support.windows_owned_process import run_owned

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Requires real Windows process/job APIs')


def test_deadline_reaps_a_real_owned_descendant(tmp_path):
    child = "import os,time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid())); time.sleep(300)"
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(300)"
    record = run_owned([sys.executable, '-c', parent, child], cwd=tmp_path,
                       environment=os.environ.copy(), console=tmp_path/'control.log', timeout=5)
    child_pid = int((tmp_path/'child.pid').read_text())
    assert record['timed_out'] and record['cleanup_verified']
    assert {record['pid'], child_pid} <= set(record['members_before_cleanup'])
    assert record['members_after_cleanup'] == []
    assert record['final_cleanup']['remaining_members'] == []


@pytest.mark.parametrize('failing_api', ['AssignProcessToJobObject', 'ResumeThread', 'QueryInformationJobObject'])
def test_native_startup_or_query_failure_retains_primary_error_and_cleanup(monkeypatch, tmp_path, failing_api):
    original = ctypes.WinDLL
    invoked = []

    class Function:
        def __init__(self, name, actual):
            self.name, self.actual = name, actual

        def __setattr__(self, name, value):
            if name in {'argtypes', 'restype'}:
                setattr(self.actual, name, value)
            else:
                object.__setattr__(self, name, value)

        def __call__(self, *args):
            if self.name == failing_api and not invoked:
                invoked.append(self.name)
                ctypes.set_last_error(5)
                return 0xFFFFFFFF if self.name == 'ResumeThread' else 0
            return self.actual(*args)

    class Library:
        def __init__(self, *args, **kwargs):
            self.actual = original(*args, **kwargs)

        def __getattr__(self, name):
            return Function(name, getattr(self.actual, name))

    monkeypatch.setattr(ctypes, 'WinDLL', Library)
    with pytest.raises(OSError):
        run_owned([sys.executable, '-c', "from pathlib import Path; Path('started').write_text('yes')"],
                  cwd=tmp_path, environment=os.environ.copy(), console=tmp_path/'failed.log', timeout=10)
    assert invoked == [failing_api]
    record = json.loads((tmp_path/'failed-process.json').read_text())
    assert record['error'].startswith(('OSError:', 'PermissionError:'))
    assert record['cleanup_verified'] and record['final_cleanup']['root_wait'] == 0
    assert record['final_cleanup']['remaining_members'] == []
    assert record['final_cleanup']['handles_closed']
    if failing_api != 'QueryInformationJobObject':
        assert not (tmp_path/'started').exists()  # First thread never ran.
