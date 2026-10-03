"""Prove native capture or diagnose an unchanged source/frozen workflow gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from tests.support.run_frozen_reference_studio_smoke import SUCCESS_MARKER
from tests.support.windows_owned_process import run_owned

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def package_binding(binary: Path, package: Path, head: str) -> dict:
    marker = binary.parent / "_internal/webjam-build-id.txt"
    if marker.stat().st_size > 128:
        raise ValueError("Extracted source marker is oversized")
    identity = marker.read_bytes()
    if identity.decode("utf-8-sig").strip() != head:
        raise ValueError("Extracted source marker differs from expected commit")
    binary_hash = digest(binary)
    inventory = {}
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if len(names) > 50_000 or len(set(names)) != len(names):
            raise ValueError("Package has oversized or duplicate inventory")
        if len({name.rstrip('/').casefold() for name in names}) != len(names):
            raise ValueError("Package has case-folded path aliases")
        for name in ("WebJam/WebJam.exe", "WebJam/_internal/webjam-build-id.txt"):
            if names.count(name) != 1:
                raise ValueError("Package omits or duplicates an identity member")
        if archive.getinfo("WebJam/_internal/webjam-build-id.txt").file_size > 128:
            raise ValueError("Packaged source marker is oversized")
        if archive.read("WebJam/_internal/webjam-build-id.txt") != identity:
            raise ValueError("Packaged and extracted source markers differ")
        if archive.getinfo("WebJam/WebJam.exe").file_size != binary.stat().st_size:
            raise ValueError("Packaged and extracted executable sizes differ")
        value = hashlib.sha256()
        with archive.open("WebJam/WebJam.exe") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
        if value.hexdigest() != binary_hash:
            raise ValueError("Packaged and extracted executable hashes differ")
        total = 0
        for member in archive.infolist():
            name = member.filename.rstrip("/") if member.is_dir() else member.filename
            relative = PurePosixPath(name)
            if (not relative.parts or relative.as_posix() != name or relative.parts[0] != "WebJam"
                    or "\\" in name or ":" in name or ".." in relative.parts):
                raise ValueError("Package has an unsafe inventory path")
            if member.is_dir():
                continue
            total += member.file_size
            if total > 8 * 1024**3 or len(relative.parts) < 2:
                raise ValueError("Package payload exceeds its diagnostic bound")
            target = binary.parent.joinpath(*relative.parts[1:])
            if (target.is_symlink() or not target.resolve().is_relative_to(binary.parent.resolve())
                    or not target.is_file() or target.stat().st_size != member.file_size):
                raise ValueError("Extracted package inventory differs")
            value = hashlib.sha256()
            with archive.open(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(block)
            observed = digest(target)
            if value.hexdigest() != observed:
                raise ValueError("Extracted runtime bytes differ from package")
            inventory[relative.as_posix()] = {"sha256": observed, "bytes": member.file_size}
    extracted = set()
    for target in binary.parent.rglob("*"):
        if target.is_symlink() or not target.resolve().is_relative_to(binary.parent.resolve()):
            raise ValueError("Extracted package contains an unsafe link")
        if target.is_file():
            extracted.add("WebJam/" + target.relative_to(binary.parent).as_posix())
            if len(extracted) > 50_000:
                raise ValueError("Extracted package has oversized inventory")
    if extracted != set(inventory):
        raise ValueError("Extracted package contains unbound files")
    return {"binary_sha256": binary_hash, "package_sha256": digest(package),
            "packaged_and_extracted_source_head": head, "verified_package_files": len(inventory),
            "package_inventory_sha256": hashlib.sha256(json.dumps(
                inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def has_native_stack_data(path: Path) -> bool:
    """Reject partial dumps before treating the intentional abort as captured."""
    size = path.stat().st_size
    if not 32 <= size <= 512 * 1024 * 1024:
        return False

    def in_file(offset, length):
        return offset >= 32 and length > 0 and offset + length <= size

    with path.open("rb") as stream:
        magic, _version, count, directory = struct.unpack("<4sIII", stream.read(16))
        if magic != b"MDMP" or not 1 <= count <= 128 or not in_file(directory, count * 12):
            return False
        stream.seek(directory)
        entries = list(struct.iter_unpack("<III", stream.read(count * 12)))
        required = {}
        for kind, length, offset in entries:
            if kind in (3, 4):  # MINIDUMP_THREAD_LIST / MINIDUMP_MODULE_LIST
                if kind in required or not in_file(offset, length) or length < 4:
                    return False
                required[kind] = (length, offset)
        if set(required) != {3, 4}:
            return False
        threads = b""
        for kind, entry_size in ((3, 48), (4, 108)):
            length, offset = required[kind]
            stream.seek(offset)
            entries_count, = struct.unpack("<I", stream.read(4))
            if not 1 <= entries_count <= 4096 or 4 + entries_count * entry_size > length:
                return False
            if kind == 3:
                threads = stream.read(entries_count * entry_size)
        # A nonempty, in-file stack and register context must accompany at
        # least one thread. Header magic or empty stream lists are not proof.
        for offset in range(0, len(threads), 48):
            stack_size, stack_rva, context_size, context_rva = struct.unpack_from(
                "<IIII", threads, offset + 32)
            if in_file(stack_rva, stack_size) and in_file(context_rva, context_size):
                return True
    return False


def commands(output: Path) -> str:
    # The dump paths occur inside CDB command strings. The CI output folder
    # has no spaces; reject metacharacters instead of nesting shell quoting.
    if not output.is_absolute() or not re.fullmatch(r"[A-Za-z0-9_./:\\-]+", str(output)):
        raise ValueError("Native diagnostic directory needs a simple absolute path")

    def capture(name):
        return f".dump /m {output / (name + '.dmp')}; kv 0n80; ~* kv 0n40; lmf; q"

    return (
        '.printf "WEBJAM_DEBUGGEE_PID=%d\\n", @$tpid\n'
        f'bu /1 ucrtbase!abort "{capture("abort")}"\n'
        f'sxd -c2 "{capture("access-violation")}" av\n'
        f'sxd -c2 "{capture("fast-fail")}" 0xc0000409\n'
        'sxe -c ".echo WEBJAM_DEBUGGEE_EXIT; .lastevent; q" epr\n'
        "g\n"
    )


def capture(debugger: Path, argv: list[str], output: Path, *, environment: dict,
            cwd: Path, timeout: int) -> dict:
    output.mkdir()
    script = output / "capture.cmd"
    script.write_text(commands(output), encoding="ascii")
    debugger_argv = [str(debugger), "-G", "-hd", "-y", str(output),
                     "-logo", str(output / "native.log"), "-cf", str(script), *argv]
    process = run_owned(debugger_argv, cwd=cwd, environment=environment,
                        console=output / "console.log", timeout=timeout)
    dumps, invalid_dumps = [], []
    for path in output.glob("*.dmp"):
        try:
            valid = has_native_stack_data(path)
        except (OSError, ValueError, struct.error):
            valid = False
        (dumps if valid else invalid_dumps).append(path.name)
    log = output / "native.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    identity = debuggee_exit(text)
    return {"debugger_argv": debugger_argv, "debugger_returncode": process["returncode"],
            "timed_out": process["timed_out"], "native_dumps": sorted(dumps),
            "invalid_native_dumps": sorted(invalid_dumps), "owned_process": process, **identity}


def debuggee_exit(text: str) -> dict:
    pids = re.findall(r"(?m)^WEBJAM_DEBUGGEE_PID=(\d+)\s*$", text)
    pid = int(pids[0]) if len(pids) == 1 else None
    events = re.findall(r"(?im)^Last event:\s+([0-9a-f]+)\.[0-9a-f]+:\s+"
                        r"Exit process\s+\d+:([0-9a-f]+),\s+code\s+(?:0x)?([0-9a-f]+)\s*$", text)
    code = None
    if (pid and len(events) == 1 and int(events[0][0], 16) == int(events[0][1], 16) == pid
            and text.splitlines().count("WEBJAM_DEBUGGEE_EXIT") == 1):
        code = int(events[0][2], 16)
    return {"debuggee_pid": pid, "debuggee_returncode": code}


def classify(record: dict, marker: str) -> str:
    if record["timed_out"]:
        return "DIAGNOSTIC_TIMEOUT"
    if record.get("invalid_native_dumps"):
        return "NATIVE_FAILURE_DUMP_INVALID"
    if record["native_dumps"]:
        return "NATIVE_FAILURE_CAPTURED"
    if (record["debugger_returncode"] == 0 and record.get("debuggee_returncode") == 0
            and marker == SUCCESS_MARKER + "\n"):
        return "FAILURE_NOT_REPRODUCED_UNDER_DEBUGGER"
    return "DIAGNOSTIC_FAILED_WITHOUT_NATIVE_DUMP"


def source_binding(head: str, root: Path = ROOT) -> dict:
    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=root, text=True, timeout=30).strip()

    if git("rev-parse", "HEAD") != head or git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Diagnostic checkout differs from the expected clean source")
    return {"checkout_head": head, "checkout_tree": git("rev-parse", "HEAD^{tree}"),
            "interpreter": str(Path(sys.executable).resolve()),
            "interpreter_sha256": digest(Path(sys.executable)), "python": sys.version}


def diagnostic_environment() -> dict:
    environment = os.environ.copy()
    # Symbols stay local; Qt fatal settings are recorded and never weakened.
    environment.pop("_NT_SYMBOL_PATH", None)
    environment.pop("_NT_ALT_SYMBOL_PATH", None)
    return environment


def debugger_identity() -> tuple[Path, dict]:
    debugger = Path(os.environ["ProgramFiles(x86)"]) / "Windows Kits/10/Debuggers/x64/cdb.exe"
    if not debugger.is_file():
        raise RuntimeError("Runner's x64 Windows debugger is missing")
    version = subprocess.run([str(debugger), "-version"], capture_output=True,
                             text=True, timeout=15, check=True)
    return debugger, {"path": str(debugger), "sha256": digest(debugger),
                      "version": (version.stdout + version.stderr).strip()}


def run_control(debugger: Path, output: Path, environment: dict) -> dict:
    control = capture(debugger, [sys.executable, "-c", "import os; os.abort()"],
                      output / "control", environment=environment, cwd=output, timeout=45)
    if control["timed_out"] or control["native_dumps"] != ["abort.dmp"]:
        raise RuntimeError("Native debugger did not capture the intentional abort control")
    # Reopen the actual dump with the native debugger. A plausible header is
    # not proof that its threads, module list and stack are readable.
    readback = output / "control" / "readback.log"
    replay = run_owned([str(debugger), "-y", str(output), "-z", str(output / "control/abort.dmp"),
                        "-c", ".echo WEBJAM_DUMP_READBACK_BEGIN; |; ~* kn 0n40; lmf; .echo WEBJAM_DUMP_READBACK_END; q"],
                       cwd=output, environment=environment, console=readback, timeout=45)
    text = readback.read_text(encoding="utf-8", errors="replace")
    if (replay["timed_out"] or replay["returncode"] != 0
            or not readable_control_stack(text)):
        raise RuntimeError("Native debugger could not read the control dump")
    control["readback"] = replay
    exit_control = capture(debugger, [sys.executable, "-c", "raise SystemExit(23)"],
                           output / "exit-control", environment=environment, cwd=output, timeout=30)
    if (exit_control["timed_out"] or exit_control["debuggee_returncode"] != 23
            or exit_control["native_dumps"] or exit_control["invalid_native_dumps"]):
        raise RuntimeError("Native debugger did not retain the debuggee's nonzero exit")
    control["expected_nonzero_exit"] = exit_control
    # A real child that sleeps past its parent's deadline proves tree cleanup,
    # independently of how CDB happens to terminate its own debuggee.
    child_code = "import os,time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid())); time.sleep(300)"
    parent_code = ("import subprocess,sys,time; from pathlib import Path; "
                   "subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
                   "time.sleep(300)")
    cleanup = run_owned([sys.executable, "-c", parent_code, child_code], cwd=output,
                        environment=environment, console=output / "cleanup-control.log", timeout=5)
    child_pid = int((output / "child.pid").read_text())
    if (not cleanup["timed_out"] or not cleanup["cleanup_verified"]
            or child_pid not in cleanup["members_before_cleanup"]
            or cleanup["members_after_cleanup"]):
        raise RuntimeError("Owned descendant cleanup control failed")
    control["expected_timeout_cleanup"] = cleanup
    return control


def readable_control_stack(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines()]
    return (lines.count("WEBJAM_DUMP_READBACK_BEGIN") == 1
            and lines.count("WEBJAM_DUMP_READBACK_END") == 1
            and bool(re.search(r"(?im)^\s*[0-9a-f]{2,3}\s+[0-9a-f`]+\s+[0-9a-f`]+\s+"
                               r"ucrtbase!abort(?:\+0x[0-9a-f]+)?(?:\s|$)", text)))


def workflow_capture(debugger: Path, mode: str, binary: Path | None, output: Path,
                     environment: dict) -> dict:
    owned = Path(tempfile.mkdtemp(prefix="webjam-reference-studio-smoke-"))
    application = output / "application"
    result = owned / "result.txt"
    environment = environment.copy()
    environment.update(QT_QPA_PLATFORM="offscreen", WEBJAM_SMOKE_REFERENCE_STUDIO_RUNTIME="1",
                       WEBJAM_SMOKE_REFERENCE_STUDIO_RESULT=str(result),
                       WEBJAM_LOG_FILE=str(owned / "app.log"))
    for name in ("WEBJAM_SMOKE_POCKET_STAGE_RUNTIME", "WEBJAM_SMOKE_LAUNCH_ONLY",
                 "WEBJAM_SMOKE_AUTOSTART_AUDIO"):
        environment.pop(name, None)
    if mode == "source":
        argv = [sys.executable, "-X", "dev", "-m", "tests.support.run_source_reference_studio_smoke",
                "--result", str(result)]
        cwd = ROOT
    else:
        argv, cwd = [str(binary)], binary.parent
    completed = False
    try:
        native = capture(debugger, argv, application, environment=environment, cwd=cwd, timeout=120)
        marker = result.read_text(encoding="utf-8") if result.is_file() else ""
        record = {"capture": native, "status": classify(native, marker), "private_root": str(owned)}
        completed = native["owned_process"]["cleanup_verified"]
        return record
    finally:
        # Never delete the only phase/result logs when capture itself raises.
        application.mkdir(exist_ok=True)
        for name in ("diagnostics.log", "result.txt", "app.log", "source-process.json", "gc-probe.jsonl"):
            source = owned / name
            if source.is_file() and not source.is_symlink():
                shutil.copyfile(source, application / name)
        if completed:
            shutil.rmtree(owned)
        else:
            (application / "retained-private-root.txt").write_text(str(owned) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("control", "source", "frozen"), default="frozen")
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--original-gate", choices=("FAILED", "NOT_RUN"), default="NOT_RUN")
    arguments = parser.parse_args()
    if os.name != "nt" or not re.fullmatch(r"[a-f0-9]{40}", arguments.source_head):
        raise SystemExit("Windows and an exact source commit are required")
    if arguments.mode == "frozen" and (arguments.binary is None or arguments.package is None):
        parser.error("Frozen diagnostics require --binary and --package")
    if arguments.mode != "frozen" and (arguments.binary is not None or arguments.package is not None):
        parser.error("Only frozen diagnostics accept package arguments")
    output = arguments.output.resolve()
    commands(output)
    output.mkdir(parents=True)
    record = {"source_head": arguments.source_head, "mode": arguments.mode,
              "qt_fatal_environment": {name: os.environ.get(name) for name in
                                       ("QT_FATAL_WARNINGS", "QT_FATAL_CRITICALS")},
              "status": "DIAGNOSTIC_STARTED", "original_gate": arguments.original_gate}
    try:
        record["source"] = source_binding(arguments.source_head)
        binary = arguments.binary.resolve() if arguments.binary else None
        if arguments.mode == "frozen":
            package = arguments.package.resolve()
            record.update(binary=str(binary), package=str(package))
            record.update(package_binding(binary, package, arguments.source_head))
        debugger, record["debugger"] = debugger_identity()
        environment = diagnostic_environment()
        record["control"] = run_control(debugger, output, environment)
        if arguments.mode == "control":
            record["status"] = "NATIVE_DIAGNOSTIC_CONTROL_PASSED"
        else:
            record["application"] = workflow_capture(debugger, arguments.mode, binary, output, environment)
            record["status"] = record["application"]["status"]
        if source_binding(arguments.source_head) != record["source"]:
            raise ValueError("Diagnostic source or interpreter changed during execution")
    except Exception as exc:
        record["status"] = "DIAGNOSTIC_ERROR"
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["evidence"] = {
            path.relative_to(output).as_posix(): {
                "size_bytes": path.stat().st_size, "sha256": digest(path)}
            for path in output.rglob("*") if path.is_file() and not path.is_symlink()
        }
        (output / "diagnostic.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, indent=2))
        for path in output.glob("*/native.log"):
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 64 * 1024))
                print(stream.read().decode("utf-8", errors="replace"))
    return 0 if record["status"] in {"NATIVE_DIAGNOSTIC_CONTROL_PASSED", "FAILURE_NOT_REPRODUCED_UNDER_DEBUGGER"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
