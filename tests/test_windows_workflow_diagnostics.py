"""Evidence retention, identity and private full-workflow diagnostic boundaries."""
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import zipfile

import pytest

from tests.support import diagnose_windows_reference_studio as diagnostic


def test_quoted_dump_commands_preserve_absolute_windows_paths():
    output = PureWindowsPath(r'D:\a\webjam\webjam\out\windows-diagnostic-control\control')
    handlers = [line for line in diagnostic.commands(output).splitlines()
                if line.startswith(('bu /1 ', 'sxd -c2 '))]
    assert len(handlers) == 3
    for line, name in zip(handlers, ('abort', 'access-violation', 'fast-fail')):
        # Decode the C-compatible quoted-string subset, as the debugger does
        # before running the handler. Unescaped Windows separators are lost.
        command = json.loads(line[line.index('"'):line.rindex('"') + 1])
        assert command.split(';', 1)[0] == (
            f'.dump /m D:/a/webjam/webjam/out/windows-diagnostic-control/control/{name}.dmp')


def test_standalone_control_does_not_require_or_validate_a_package(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostic, 'os', SimpleNamespace(name='nt', environ=os.environ))
    monkeypatch.setattr(sys, 'argv', ['diagnostic', '--mode', 'control', '--output', str(tmp_path/'owned'),
                                    '--source-head', 'a'*40])
    monkeypatch.setattr(diagnostic, 'source_binding', lambda head: {'checkout_head': head})
    monkeypatch.setattr(diagnostic, 'debugger_identity', lambda: (tmp_path/'cdb.exe', {'sha256': 'test'}))
    monkeypatch.setattr(diagnostic, 'run_control', lambda *args: {'control': 'test boundary'})
    monkeypatch.setattr(diagnostic, 'package_binding', lambda *args: pytest.fail('Control must not bind a package'))
    assert diagnostic.main() == 0
    record = json.loads((tmp_path/'owned/diagnostic.json').read_text())
    assert record['status'] == 'NATIVE_DIAGNOSTIC_CONTROL_PASSED'
    assert record['original_gate'] == 'NOT_RUN'
    assert 'package' not in record


def test_capture_error_keeps_phase_evidence_before_scratch_cleanup(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        result = Path(kwargs['environment']['WEBJAM_SMOKE_REFERENCE_STUDIO_RESULT'])
        (result.parent/'diagnostics.log').write_text('owned phase before capture failure\n')
        raise RuntimeError('owned cleanup not proven')

    monkeypatch.setattr(diagnostic, 'capture', fail)
    with pytest.raises(RuntimeError, match='owned cleanup'):
        diagnostic.workflow_capture(tmp_path/'cdb.exe', 'source', None, tmp_path, os.environ.copy())
    root = Path((tmp_path/'application/retained-private-root.txt').read_text().strip())
    try:
        assert root.is_dir()
        assert (tmp_path/'application/diagnostics.log').read_text() == (root/'diagnostics.log').read_text()
    finally:
        shutil.rmtree(root)  # Fake capture launched no process.


def test_source_diagnostics_refuse_wrong_or_dirty_checkout(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, text=True).strip()
    git('init', '-q')
    (tmp_path/'source.py').write_text('original source\n')
    git('add', 'source.py')
    git('-c', 'user.name=Diagnostic test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
    head = git('rev-parse', 'HEAD')
    assert diagnostic.source_binding(head, tmp_path)['checkout_tree'] == git('rev-parse', 'HEAD^{tree}')
    with pytest.raises(ValueError, match='clean source'):
        diagnostic.source_binding('0'*40, tmp_path)
    (tmp_path/'source.py').write_text('changed source\n')
    with pytest.raises(ValueError, match='clean source'):
        diagnostic.source_binding(head, tmp_path)


def test_frozen_binding_rejects_changed_runtime_even_with_same_executable(tmp_path):
    app = tmp_path/'WebJam'
    internal = app/'_internal'
    internal.mkdir(parents=True)
    files = {'WebJam.exe': b'executable', '_internal/webjam-build-id.txt': b'a'*40,
             '_internal/Qt6Core.dll': b'exact runtime'}
    package = tmp_path/'package.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        for name, data in files.items():
            (app/name).write_bytes(data)
            archive.writestr('WebJam/'+name, data)
    assert diagnostic.package_binding(app/'WebJam.exe', package, 'a'*40)['verified_package_files'] == 3
    (internal/'Qt6Core.dll').write_bytes(b'other runtime')
    with pytest.raises(ValueError, match='runtime bytes'):
        diagnostic.package_binding(app/'WebJam.exe', package, 'a'*40)


@pytest.mark.parametrize('mode', ['ordinary', 'before', 'after'])
def test_fresh_source_child_runs_complete_workflow_with_explicit_gc_mode(mode):
    with tempfile.TemporaryDirectory(prefix='webjam-reference-studio-smoke-') as directory:
        root = Path(directory)
        environment = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'WEBJAM_SMOKE_WORKER_GC': mode}
        child = subprocess.run([sys.executable, '-X', 'dev', '-m', 'tests.support.run_source_reference_studio_smoke',
                                '--result', str(root/'result.txt')], cwd=diagnostic.ROOT,
                               env=environment, capture_output=True, text=True, timeout=60)
        assert child.returncode == 0, child.stdout + child.stderr
        assert (root/'result.txt').read_text() == diagnostic.SUCCESS_MARKER+'\n'
        assert (root/'diagnostics.log').read_text().endswith('Success marker: complete; hook return\n')
        identity = json.loads((root/'source-process.json').read_text())
        assert identity['pid'] != os.getpid() and identity['worker_gc'] == mode
        probe = root/'gc-probe.jsonl'
        if mode == 'ordinary':
            assert not probe.exists()
        else:
            events = [json.loads(line) for line in probe.read_text().splitlines()]
            before = [event for event in events if event['phase'] == 'before-collection']
            after = [event for event in events if event['phase'] == 'after-collection']
            assert len(before) == len(after) and len(after) >= 2
            assert all(event['pid'] == identity['pid'] and event['thread'] != 'MainThread' for event in events)


@pytest.mark.parametrize('text,expected', [
    ('WEBJAM_DUMP_READBACK_BEGIN\n00 00000012`12345678 00007ffa`12345678 ucrtbase!abort+0x4\nWEBJAM_DUMP_READBACK_END\n', True),
    ('WEBJAM_DUMP_READBACK_BEGIN\nucrtbase.dll python311.dll\nWEBJAM_DUMP_READBACK_END\n', False),
    ('0:000> .echo WEBJAM_DUMP_READBACK_BEGIN; lmf; .echo WEBJAM_DUMP_READBACK_END; q\nucrtbase python\n', False),
])
def test_control_readback_requires_an_emitted_native_abort_frame(text, expected):
    assert diagnostic.readable_control_stack(text) is expected


@pytest.mark.parametrize('initially_enabled', [True, False])
def test_gc_probe_restores_method_and_gc_after_exception(initially_enabled):
    import gc
    from services.packaged_smoke_gc_probe import worker_gc_probe
    from webjam_qt.windows.workspace_backup import WorkspaceJob
    original = WorkspaceJob.start
    prior_gc = gc.isenabled()
    prior_mode = os.environ.get('WEBJAM_SMOKE_WORKER_GC')
    try:
        (gc.enable if initially_enabled else gc.disable)()
        os.environ['WEBJAM_SMOKE_WORKER_GC'] = 'before'
        with tempfile.TemporaryDirectory(prefix='webjam-reference-studio-smoke-') as directory:
            with pytest.raises(RuntimeError, match='owned failure'):
                with worker_gc_probe(Path(directory)/'result.txt'):
                    assert WorkspaceJob.start is not original and not gc.isenabled()
                    raise RuntimeError('owned failure')
            assert WorkspaceJob.start is original
            assert gc.isenabled() is initially_enabled
    finally:
        (gc.enable if prior_gc else gc.disable)()
        if prior_mode is None:
            os.environ.pop('WEBJAM_SMOKE_WORKER_GC', None)
        else:
            os.environ['WEBJAM_SMOKE_WORKER_GC'] = prior_mode


def test_matrix_stops_after_first_failure_and_accounts_for_unexecuted_attempts(monkeypatch, tmp_path):
    from tests.support import run_windows_workflow_matrix as matrix
    plan = matrix.experiment_plan()
    assert len(plan) == 12
    for kind in ['source', 'frozen']:
        assert {(p['backend'], p['worker_gc']) for p in plan if p['kind'] == kind} == {
            (backend, mode) for backend in ['plain', 'cdb'] for mode in ['ordinary', 'before', 'after']}
    monkeypatch.setattr(matrix, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(sys, 'argv', ['matrix', '--binary', str(tmp_path/'WebJam.exe'),
                                    '--package', str(tmp_path/'package.zip'), '--output', str(tmp_path/'matrix'),
                                    '--source-head', 'a'*40])
    monkeypatch.setattr(diagnostic, 'source_binding', lambda *args: {'head': 'a'*40})
    monkeypatch.setattr(diagnostic, 'package_binding', lambda *args: {'package_sha256': 'test'})
    monkeypatch.setattr(diagnostic, 'debugger_identity', lambda: (tmp_path/'cdb.exe', {'sha256': 'test'}))
    monkeypatch.setattr(diagnostic, 'run_control', lambda *args: {'status': 'owned control fixture'})
    calls = []

    def execute(record, *args):
        calls.append(record['id'])
        if len(calls) == 3:
            raise RuntimeError('first unexpected failure')
        record['status'] = 'PASSED'
    monkeypatch.setattr(matrix, 'attempt', execute)
    assert matrix.main() == 1
    receipt = json.loads((tmp_path/'matrix/matrix.json').read_text())
    assert calls == ['source-1', 'frozen-1', 'source-2']
    assert [r['status'] for r in receipt['attempts']] == ['PASSED', 'PASSED', 'FAILED']
    assert receipt['unexecuted'] == plan[3:]
    assert receipt['original_gate'] == 'NOT_RUN_BY_MATRIX'
    assert receipt['status'] == 'STOPPED_FIRST_UNEXPECTED_FAILURE'


@pytest.mark.parametrize('entry', ['../outside', 'WebJam/../outside', '/absolute', 'WebJam/C:/bad', 'WebJam\\bad'])
def test_diagnostic_package_extraction_refuses_unowned_paths(tmp_path, entry):
    from tests.support.prepare_windows_diagnostic_artifact import extract_package
    archive = tmp_path/'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr(entry, b'unowned')
    with pytest.raises(ValueError, match='Unsafe'):
        extract_package(archive, tmp_path/'destination')
    assert not (tmp_path/'outside').exists()


def test_diagnostic_binding_rejects_unbound_extracted_runtime_and_case_aliases(tmp_path):
    app = tmp_path/'WebJam'
    (app/'_internal').mkdir(parents=True)
    files = {'WebJam.exe': b'executable', '_internal/webjam-build-id.txt': b'a'*40}
    archive = tmp_path/'input.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        for name, data in files.items():
            (app/name).write_bytes(data)
            package.writestr('WebJam/'+name, data)
    extra = app/'extra.dll'
    extra.write_bytes(b'unbound runtime')
    with pytest.raises(ValueError, match='unbound files'):
        diagnostic.package_binding(app/'WebJam.exe', archive, 'a'*40)
    extra.unlink()
    with zipfile.ZipFile(archive, 'a') as package:
        package.writestr('WebJam/WEBJAM.exe', b'executable')
    with pytest.raises(ValueError, match='case-folded'):
        diagnostic.package_binding(app/'WebJam.exe', archive, 'a'*40)


@pytest.mark.parametrize('code', [None, 23, 3221226505])
def test_cdb_success_marker_cannot_hide_nonzero_or_unproven_debuggee_exit(tmp_path, code):
    from tests.support.run_windows_workflow_matrix import verify_attempt
    (tmp_path/'result.txt').write_text(diagnostic.SUCCESS_MARKER+'\n')
    (tmp_path/'diagnostics.log').write_text('Success marker: complete; hook return\n')
    record = {'kind': 'frozen', 'backend': 'cdb', 'worker_gc': 'ordinary',
              'process': {'timed_out': False, 'cleanup_verified': True, 'returncode': 0,
                          'members_before_cleanup': []},
              'capture': {'debuggee_returncode': code, 'native_dumps': [], 'invalid_native_dumps': []}}
    with pytest.raises(RuntimeError, match='Debuggee exit'):
        verify_attempt(record, tmp_path, 123)
    record['capture'].update(timed_out=False, debugger_returncode=0)
    assert diagnostic.classify(record['capture'], diagnostic.SUCCESS_MARKER+'\n') == 'DIAGNOSTIC_FAILED_WITHOUT_NATIVE_DUMP'


@pytest.mark.parametrize('text,pid,code', [
    ('WEBJAM_DEBUGGEE_PID=123\nWEBJAM_DEBUGGEE_EXIT\nLast event: 7b.1d: Exit process 0:7b, code 17\n', 123, 23),
    ('WEBJAM_DEBUGGEE_PID=123\nWEBJAM_DEBUGGEE_EXIT\nLast event: 7b.1d: Exit process 0:7b, code 0\n', 123, 0),
    ('WEBJAM_DEBUGGEE_PID=123\nLast event: 7b.1d: Exit process 0:7b, code 0\n', 123, None),
    ('WEBJAM_DEBUGGEE_PID=123\nWEBJAM_DEBUGGEE_EXIT\nLast event: 7c.1d: Exit process 0:7c, code 0\n', 123, None),
])
def test_native_exit_event_is_bound_to_debuggee_pid(text, pid, code):
    assert diagnostic.debuggee_exit(text) == {'debuggee_pid': pid, 'debuggee_returncode': code}


def test_malformed_dump_remains_a_failed_capture(monkeypatch, tmp_path):
    def owned(argv, **kwargs):
        (kwargs['console'].parent/'abort.dmp').write_bytes(b'truncated dump')
        return {'timed_out': False, 'returncode': 0, 'cleanup_verified': True}
    monkeypatch.setattr(diagnostic, 'run_owned', owned)
    capture = diagnostic.capture(tmp_path/'cdb.exe', ['owned.exe'], tmp_path/'capture',
                                 environment={}, cwd=tmp_path, timeout=120)
    assert capture['native_dumps'] == []
    assert capture['invalid_native_dumps'] == ['abort.dmp']
    assert diagnostic.classify(capture, diagnostic.SUCCESS_MARKER+'\n') == 'NATIVE_FAILURE_DUMP_INVALID'


@pytest.mark.parametrize('change', [None, 'workflow', 'job', 'attempt', 'download_hash'])
def test_artifact_preparation_authenticates_producer_and_real_archive(monkeypatch, tmp_path, change):
    from tests.support import prepare_windows_diagnostic_artifact as prepare
    head = 'a'*40
    inner = tmp_path/'inner.zip'
    with zipfile.ZipFile(inner, 'w') as package:
        package.writestr('WebJam/WebJam.exe', b'owned executable')
        package.writestr('WebJam/_internal/webjam-build-id.txt', head)
    outer = tmp_path/'outer.zip'
    with zipfile.ZipFile(outer, 'w') as package:
        package.writestr(prepare.PACKAGE_NAME, inner.read_bytes())
    run = {'id': 123, 'head_sha': head, 'event': 'push', 'status': 'completed', 'conclusion': 'success',
           'path': '.github/workflows/ci.yml', 'workflow_id': 235479110, 'run_attempt': 1}
    job = {'name': 'Build Desktop (windows-x64)', 'status': 'completed', 'conclusion': 'success',
           'head_sha': head, 'run_id': 123, 'run_attempt': 1}
    artifact = {'id': 456, 'name': 'webjam-windows-x64', 'expired': False,
                'workflow_run': {'id': 123, 'head_sha': head}, 'size_in_bytes': outer.stat().st_size,
                'digest': 'sha256:'+diagnostic.digest(outer)}
    if change == 'workflow':
        run['path'] = '.github/workflows/another.yml'
    elif change == 'job':
        job['conclusion'] = 'failure'
    elif change == 'attempt':
        run['run_attempt'] = 2
    elif change == 'download_hash':
        artifact['digest'] = 'sha256:'+'0'*64
    responses = {'actions/runs/123': run,
                 'actions/runs/123/jobs?per_page=100': {'total_count': 1, 'jobs': [job]},
                 'actions/runs/123/artifacts?per_page=100': {'total_count': 1, 'artifacts': [artifact]}}
    monkeypatch.setattr(prepare, 'api', lambda path: responses[path])
    monkeypatch.setattr(prepare, 'source_binding', lambda value: {'checkout_head': value})
    monkeypatch.setattr(prepare.shutil, 'disk_usage', lambda path: SimpleNamespace(free=20*1024**3))
    downloads = []
    def download(argv, **kwargs):
        downloads.append(argv)
        kwargs['stdout'].write(outer.read_bytes())
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(prepare.subprocess, 'run', download)
    monkeypatch.setattr(sys, 'argv', ['prepare', '--run-id', '123', '--source-head', head,
                                    '--output', str(tmp_path/'input')])
    if change is None:
        assert prepare.main() == 0
        receipt = json.loads((tmp_path/'input/input.json').read_text())
        assert receipt['status'] == 'AUTHENTICATED_DIAGNOSTIC_INPUT'
        assert receipt['package_job'] == job
        assert receipt['binding']['verified_package_files'] == 2
        assert len(downloads) == 1
    else:
        with pytest.raises(ValueError):
            prepare.main()
        receipt = json.loads((tmp_path/'input/input.json').read_text())
        assert receipt['status'] == 'INPUT_FAILED'
        assert len(downloads) == (1 if change == 'download_hash' else 0)
        assert not (tmp_path/'input/extracted').exists()


def test_matrix_post_execution_identity_failure_updates_individual_receipt(monkeypatch, tmp_path):
    from tests.support import run_windows_workflow_matrix as matrix
    monkeypatch.setattr(matrix, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(sys, 'argv', ['matrix', '--binary', str(tmp_path/'app.exe'), '--package', str(tmp_path/'app.zip'),
                                    '--output', str(tmp_path/'matrix'), '--source-head', 'a'*40])
    sources = iter([{'head': 'original'}, {'head': 'changed'}])
    monkeypatch.setattr(diagnostic, 'source_binding', lambda *args: next(sources))
    monkeypatch.setattr(diagnostic, 'package_binding', lambda *args: {'package': 'original'})
    monkeypatch.setattr(diagnostic, 'debugger_identity', lambda: (tmp_path/'cdb.exe', {}))
    monkeypatch.setattr(diagnostic, 'run_control', lambda *args: {})

    def execute(record, binary, debugger, output):
        output.mkdir()
        record['status'] = 'PASSED'
        (output/'attempt.json').write_text(json.dumps(record))
    monkeypatch.setattr(matrix, 'attempt', execute)
    assert matrix.main() == 1
    root = json.loads((tmp_path/'matrix/matrix.json').read_text())
    individual = json.loads((tmp_path/'matrix/source-1/attempt.json').read_text())
    assert len(root['attempts']) == 1 and len(root['unexecuted']) == 11
    assert individual == root['attempts'][0]
    assert individual['status'] == 'FAILED' and 'Source changed' in individual['error']
