"""Collect native evidence after a failed gate; this never replaces that gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from tests.support.run_frozen_reference_studio_smoke import SUCCESS_MARKER


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
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
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
    return {"binary_sha256": binary_hash, "package_sha256": digest(package),
            "packaged_and_extracted_source_head": head}


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
        return f".dump /m {output / (name + '.dmp')}; kv 0n80; ~* kv 0n40; lm; q"

    return (
        f'bu /1 ucrtbase!abort "{capture("abort")}"\n'
        f'sxd -c2 "{capture("access-violation")}" av\n'
        f'sxd -c2 "{capture("fast-fail")}" 0xc0000409\n'
        "g\n"
    )


def capture(debugger: Path, argv: list[str], output: Path, *, environment: dict,
            cwd: Path, timeout: int) -> dict:
    output.mkdir()
    script = output / "capture.cmd"
    script.write_text(commands(output), encoding="ascii")
    debugger_argv = [str(debugger), "-G", "-hd", "-y", str(output),
                     "-logo", str(output / "native.log"), "-cf", str(script), *argv]
    timed_out = False
    with (output / "console.log").open("xb") as console:
        process = subprocess.Popen(debugger_argv, cwd=cwd, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=console,
                                   stderr=subprocess.STDOUT)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Kill only this owned debugger and its descendants. Killing just
            # the Python wrapper would leave the debuggee suspended.
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=console, stderr=subprocess.STDOUT,
                               timeout=15, check=False)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
    dumps = [path.name for path in output.glob("*.dmp") if has_native_stack_data(path)]
    return {"debugger_argv": debugger_argv, "debugger_returncode": process.returncode,
            "timed_out": timed_out, "native_dumps": sorted(dumps)}


def classify(record: dict, marker: str) -> str:
    if record["timed_out"]:
        return "DIAGNOSTIC_TIMEOUT"
    if record["native_dumps"]:
        return "NATIVE_FAILURE_CAPTURED"
    if record["debugger_returncode"] == 0 and marker == SUCCESS_MARKER + "\n":
        return "FAILURE_NOT_REPRODUCED_UNDER_DEBUGGER"
    return "DIAGNOSTIC_FAILED_WITHOUT_NATIVE_DUMP"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-head", required=True)
    arguments = parser.parse_args()
    if os.name != "nt" or not re.fullmatch(r"[a-f0-9]{40}", arguments.source_head):
        raise SystemExit("Windows and an exact source commit are required")
    binary, package = arguments.binary.resolve(), arguments.package.resolve()
    output = arguments.output.resolve()
    commands(output)  # Validate before creating the exclusive destination.
    output.mkdir(parents=True)
    debugger = Path(os.environ["ProgramFiles(x86)"]) / "Windows Kits/10/Debuggers/x64/cdb.exe"
    record = {"source_head": arguments.source_head, "binary": str(binary),
              "package": str(package), "debugger": str(debugger),
              "qt_fatal_environment": {name: os.environ.get(name) for name in
                                       ("QT_FATAL_WARNINGS", "QT_FATAL_CRITICALS")},
              "status": "DIAGNOSTIC_STARTED", "original_gate": "FAILED"}
    try:
        record.update(package_binding(binary, package, arguments.source_head))
        if not debugger.is_file():
            raise RuntimeError("Runner's x64 Windows debugger is missing")
        record["debugger_sha256"] = digest(debugger)
        environment = os.environ.copy()
        environment.pop("_NT_SYMBOL_PATH", None)
        environment.pop("_NT_ALT_SYMBOL_PATH", None)
        control = capture(debugger, [sys.executable, "-c", "import os; os.abort()"],
                          output / "control", environment=environment,
                          cwd=output, timeout=45)
        record["control"] = control
        if control["timed_out"] or control["native_dumps"] != ["abort.dmp"]:
            raise RuntimeError("Native debugger did not capture the intentional abort control")
        with tempfile.TemporaryDirectory(prefix="webjam-reference-studio-smoke-") as temporary:
            owned = Path(temporary)
            result = owned / "result.txt"
            environment.update(QT_QPA_PLATFORM="offscreen",
                               WEBJAM_SMOKE_REFERENCE_STUDIO_RUNTIME="1",
                               WEBJAM_SMOKE_REFERENCE_STUDIO_RESULT=str(result),
                               HOME=str(owned), USERPROFILE=str(owned),
                               WEBJAM_LOG_FILE=str(owned / "app.log"))
            for name in ("WEBJAM_SMOKE_POCKET_STAGE_RUNTIME", "WEBJAM_SMOKE_LAUNCH_ONLY",
                         "WEBJAM_SMOKE_AUTOSTART_AUDIO"):
                environment.pop(name, None)
            native = capture(debugger, [str(binary)], output / "application",
                             environment=environment, cwd=binary.parent, timeout=120)
            record["application"] = native
            marker = result.read_text(encoding="utf-8") if result.is_file() else ""
            record["status"] = classify(native, marker)
            for name in ("diagnostics.log", "result.txt", "app.log"):
                source = owned / name
                if source.is_file() and not source.is_symlink():
                    shutil.copyfile(source, output / "application" / name)
    except Exception as exc:
        record["status"] = "DIAGNOSTIC_ERROR"
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["evidence"] = {
            path.relative_to(output).as_posix(): {
                "size_bytes": path.stat().st_size, "sha256": digest(path)}
            for path in output.rglob("*") if path.is_file() and not path.is_symlink()
        }
        (output / "diagnostic.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record, indent=2))
        for path in output.glob("*/native.log"):
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 64 * 1024))
                print(stream.read().decode("utf-8", errors="replace"))
    # Even non-reproduction under a debugger cannot turn the original gate green.
    return 0 if record["status"] == "FAILURE_NOT_REPRODUCED_UNDER_DEBUGGER" else 1


if __name__ == "__main__":
    raise SystemExit(main())
