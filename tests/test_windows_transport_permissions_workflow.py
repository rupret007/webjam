"""CI wiring controls; passing these is not native Windows API evidence."""

import importlib.util
import json
import os
from pathlib import Path
import re
import stat
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def control_job():
    match = re.search(
        r"(?ms)^  windows-diagnostic-control:\n.*?(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        WORKFLOW,
    )
    assert match is not None
    return match.group(0)


def step(name):
    job = control_job()
    marker = f"      - name: {name}\n"
    start = job.index(marker)
    end = job.find("\n      - name:", start + len(marker))
    return job[start : end if end >= 0 else len(job)]


def test_actual_acl_diagnostic_uses_existing_unconditional_windows_control_job():
    job = control_job()
    assert "    runs-on: windows-2025\n" in job
    assert "    steps:\n" in job
    assert "    if:" not in job.partition("    steps:\n")[0]
    assert "Prove native process ownership and startup failure cleanup" in job
    assert "Prove native debugger control independently of product gates" in job
    run = step("Prove actual Windows transport ACL and share controls")
    assert "        if: always()\n" in run
    assert "        timeout-minutes: 3\n" in run
    assert (
        "python -X dev -m tests.support.windows_transport_permissions_native_control"
        in run
    )
    assert "continue-on-error" not in run
    assert "workflow_dispatch" not in run


def test_native_acl_results_are_retained_even_when_controls_fail():
    retained = step("Retain independent native control evidence")
    assert "        if: always()\n" in retained
    assert "out/windows-diagnostic-control/" in retained
    assert "out/windows-diagnostic-process-tests/" in retained
    run = step("Prove actual Windows transport ACL and share controls")
    assert "out/windows-transport-acl-native/" in retained
    assert "--junitxml" not in run and "--basetemp" not in run


def native_control():
    source = ROOT / "tests/support/windows_transport_permissions_native_control.py"
    spec = importlib.util.spec_from_file_location("native_acl_control_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_cli_refuses_unsupported_hosts_without_running_or_skipping_cases(
    monkeypatch, capsys
):
    control = native_control()
    monkeypatch.setattr(control, "os", SimpleNamespace(name="posix"))
    calls = []
    monkeypatch.setattr(
        control, "pytest", SimpleNamespace(main=lambda args: calls.append(args))
    )
    assert control.main([]) == 2
    assert "requires Windows; no native API tests executed" in capsys.readouterr().err
    assert calls == []


def windows_control(monkeypatch, tmp_path):
    control = native_control()
    source = tmp_path / control._NATIVE_PATH
    source.parent.mkdir(parents=True)
    source.write_bytes(Path(control.__file__).read_bytes())
    guard = tmp_path / "services/windows_transport_permissions.py"
    guard.parent.mkdir()
    guard.write_text("Benign owned source-hash sentinel")
    proxy = SimpleNamespace(**vars(os))
    proxy.name = "nt"
    monkeypatch.setattr(control, "os", proxy)
    monkeypatch.setattr(control, "ROOT", tmp_path)
    monkeypatch.setattr(control, "__file__", str(source))
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.delenv("PYTEST_PLUGINS", raising=False)
    return control


def record_reports(control, proof, mode="complete"):
    ids = list(control._EXPECTED_CASES)
    if mode == "wrong-case":
        ids[-1] = "tests/foreign.py::test_unrelated"
    proof.pytest_collection_finish(
        SimpleNamespace(items=[SimpleNamespace(nodeid=i) for i in ids])
    )
    if mode == "collect-only":
        return
    for nodeid in ids:
        for phase in ("setup", "call", "teardown"):
            if mode == "missing-call" and nodeid == ids[-1] and phase == "call":
                continue
            details = {"nodeid": nodeid, "when": phase, "outcome": "passed"}
            if nodeid == ids[-1] and (
                mode == "skip"
                and phase == "call"
                or mode == "teardown-failure"
                and phase == "teardown"
            ):
                details["outcome"] = "skipped" if mode == "skip" else "failed"
            if mode == "xfail" and nodeid == ids[-1] and phase == "call":
                details["wasxfail"] = "Synthetic classification"
            report = SimpleNamespace(**details)
            proof.pytest_runtest_logreport(report)
            if mode == "duplicate-call" and nodeid == ids[-1] and phase == "call":
                proof.pytest_runtest_logreport(report)


def test_native_cli_runs_complete_own_file_with_private_config_and_propagates_failure(
    monkeypatch, tmp_path
):
    control = windows_control(monkeypatch, tmp_path)
    calls = []

    def runner(args, plugins):
        calls.append((args, plugins))
        assert os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        return 7

    monkeypatch.setattr(control, "pytest", SimpleNamespace(main=runner))
    previous = os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
    assert control.main([]) == 7
    assert os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == previous
    source = Path(control.__file__).resolve()
    owned = tmp_path / "out/windows-transport-acl-native"
    args, plugins = calls[0]
    assert args == [
        str(source),
        "-q",
        "-c",
        str(owned / "pytest.ini"),
        "--rootdir",
        str(tmp_path),
        "--noconftest",
        "--confcutdir",
        str(source.parent),
        "--override-ini",
        f"cache_dir={owned / 'cache'}",
        "--junitxml",
        str(owned / "results.xml"),
        "--basetemp",
        str(owned / "pytest"),
    ]
    assert len(plugins) == 1 and isinstance(plugins[0], control.NativeExecutionProof)
    assert json.loads((owned / "execution.json").read_text())["qualified"] is False


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["--collect-only"],
        ["-k", "one"],
        ["--basetemp", "services"],
        ["--junitxml", "source.py"],
    ],
)
def test_native_cli_rejects_all_options_before_any_output_or_runner(
    monkeypatch, tmp_path, args
):
    control = windows_control(monkeypatch, tmp_path)
    before = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    monkeypatch.setattr(
        control,
        "pytest",
        SimpleNamespace(
            main=lambda *a, **k: pytest.fail("Rejected input started pytest")
        ),
    )
    assert control.main(args) == 2
    assert {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    } == before
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "variable,value",
    [
        ("PYTEST_ADDOPTS", "--collect-only -p no:cacheprovider"),
        ("PYTEST_PLUGINS", "unexpected_plugin"),
    ],
)
def test_native_cli_refuses_inherited_selection_before_reserving_outputs(
    monkeypatch, tmp_path, variable, value
):
    control = windows_control(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, value)
    monkeypatch.setattr(
        control,
        "pytest",
        SimpleNamespace(
            main=lambda *a, **k: pytest.fail("Inherited selection started pytest")
        ),
    )
    assert control.main([]) == 2
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "collision", ["out-file", "existing-directory", "existing-file", "reparse-parent"]
)
def test_native_cli_never_cleans_existing_or_reparse_output_paths(
    monkeypatch, tmp_path, collision
):
    control = windows_control(monkeypatch, tmp_path)
    parent = tmp_path / "out"
    if collision == "out-file":
        parent.write_text("Keep existing output parent")
        sentinel = parent
    else:
        parent.mkdir()
        if collision == "reparse-parent":
            original = Path.lstat

            def metadata(path):
                if path == parent:
                    return SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400
                    )
                return original(path)

            monkeypatch.setattr(Path, "lstat", metadata)
            sentinel = parent / "keep.txt"
        elif collision == "existing-directory":
            target = parent / "windows-transport-acl-native"
            target.mkdir()
            sentinel = target / "keep.txt"
        else:
            sentinel = parent / "windows-transport-acl-native"
        sentinel.write_text("Keep existing output")
    before = sentinel.read_bytes()
    monkeypatch.setattr(
        control,
        "pytest",
        SimpleNamespace(
            main=lambda *a, **k: pytest.fail("Unsafe output started pytest")
        ),
    )
    assert control.main([]) == 2
    assert sentinel.read_bytes() == before


@pytest.mark.parametrize(
    "mode",
    [
        "complete",
        "collect-only",
        "wrong-case",
        "missing-call",
        "skip",
        "xfail",
        "duplicate-call",
        "teardown-failure",
        "source-change",
        "system-exit-zero",
    ],
)
def test_native_cli_requires_exact_real_pass_reports_and_unchanged_source(
    monkeypatch, tmp_path, mode
):
    control = windows_control(monkeypatch, tmp_path)

    def runner(args, plugins):
        record_reports(control, plugins[0], mode)
        if mode == "system-exit-zero":
            raise SystemExit(0)
        if mode == "source-change":
            Path(control.__file__).write_text("Changed owned synthetic source")
        return 0

    monkeypatch.setattr(control, "pytest", SimpleNamespace(main=runner))
    assert control.main([]) == (0 if mode == "complete" else 2)
    receipt = json.loads(
        (tmp_path / "out/windows-transport-acl-native/execution.json").read_text()
    )
    assert receipt["qualified"] is (mode == "complete")
    assert receipt["pytest_exit"] == (2 if mode == "system-exit-zero" else 0)
