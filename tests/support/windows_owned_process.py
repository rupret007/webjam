"""Run only an owned diagnostic process tree in a Windows kill-on-close job.

The first thread stays suspended until job assignment succeeds, so no child
can escape between CreateProcess and AssignProcessToJobObject. No global
debugger, registry, or process settings are changed.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import time


def run_owned(argv: list[str], *, cwd: Path, environment: dict[str, str],
              console: Path, timeout: float) -> dict:
    if os.name != "nt":
        raise RuntimeError("An owned Windows diagnostic job requires Windows")
    import msvcrt
    from ctypes import wintypes as w

    class Limits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", w.DWORD), ("minimum_working_set", ctypes.c_size_t),
                    ("maximum_working_set", ctypes.c_size_t), ("active_limit", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", Limits), ("io", ctypes.c_ulonglong * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

    class Startup(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                    ("title", w.LPWSTR), ("x", w.DWORD), ("y", w.DWORD),
                    ("x_size", w.DWORD), ("y_size", w.DWORD), ("x_chars", w.DWORD),
                    ("y_chars", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                    ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", w.LPBYTE),
                    ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE),
                    ("pid", w.DWORD), ("tid", w.DWORD)]

    class ProcessList(ctypes.Structure):
        _fields_ = [("assigned", w.DWORD), ("count", w.DWORD),
                    ("pids", ctypes.c_size_t * 1024)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    def api(name, args, result=w.BOOL):
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
        return function

    create_job = api("CreateJobObjectW", [w.LPVOID, w.LPCWSTR], w.HANDLE)
    set_job = api("SetInformationJobObject", [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD])
    query_job = api("QueryInformationJobObject", [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID])
    assign = api("AssignProcessToJobObject", [w.HANDLE, w.HANDLE])
    terminate_job = api("TerminateJobObject", [w.HANDLE, w.UINT])
    terminate_process = api("TerminateProcess", [w.HANDLE, w.UINT])
    close = api("CloseHandle", [w.HANDLE])
    create = api("CreateProcessW", [w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL,
                                    w.DWORD, w.LPVOID, w.LPCWSTR,
                                    ctypes.POINTER(Startup), ctypes.POINTER(ProcessInfo)])
    resume = api("ResumeThread", [w.HANDLE], w.DWORD)
    wait = api("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)
    exit_code = api("GetExitCodeProcess", [w.HANDLE, ctypes.POINTER(w.DWORD)])

    def checked(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def members():
        found = ProcessList()
        checked(query_job(job, 3, ctypes.byref(found), ctypes.sizeof(found), None))
        if found.count > 1024 or found.assigned != found.count:
            raise RuntimeError("Owned job process inventory is incomplete")
        return list(found.pids[:found.count])

    if not argv or not 0 < timeout <= 180:
        raise ValueError("Invalid owned diagnostic command or deadline")
    for name, value in environment.items():
        if not name or "=" in name or "\0" in name + value:
            raise ValueError("Invalid diagnostic environment")
    block = ctypes.create_unicode_buffer("\0".join(
        f"{key}={value}" for key, value in sorted(environment.items(), key=lambda item: item[0].upper())
    ) + "\0\0")
    job = create_job(None, None)
    checked(job)
    process = ProcessInfo()
    assigned = False
    record = {"argv": argv, "timed_out": False, "cleanup_verified": False}
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        checked(set_job(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        with console.open("xb") as log, open(os.devnull, "rb") as null:
            os.set_inheritable(log.fileno(), True)
            os.set_inheritable(null.fileno(), True)
            startup = Startup()
            startup.cb, startup.flags = ctypes.sizeof(startup), 0x100
            startup.stdin = msvcrt.get_osfhandle(null.fileno())
            startup.stdout = startup.stderr = msvcrt.get_osfhandle(log.fileno())
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            checked(create(argv[0], command, None, None, True, 0x08000404,
                           block, str(cwd), ctypes.byref(startup), ctypes.byref(process)))
            record.update(pid=process.pid, initial_thread_id=process.tid)
            checked(assign(job, process.process))
            assigned = True
            deadline = time.monotonic() + timeout
            checked(resume(process.thread) != 0xFFFFFFFF)
            outcome = wait(process.process, max(0, int((deadline - time.monotonic()) * 1000)))
            if outcome not in (0, 258):
                raise ctypes.WinError(ctypes.get_last_error())
            record["timed_out"] = outcome == 258
            code = w.DWORD()
            checked(exit_code(process.process, ctypes.byref(code)))
            record["returncode_before_cleanup"] = code.value
            remaining = members()
            record["members_after_root_wait"] = remaining
            drain_started = time.monotonic()
            # A debugger's exit does not imply that its debuggee has finished
            # kernel shutdown. Allow the owned tree to retire naturally, using
            # only the time left on the original deadline, before killing it.
            while remaining and not record["timed_out"]:
                available = deadline - time.monotonic()
                if available <= 0:
                    record["timed_out"] = True
                    break
                time.sleep(min(0.05, available))
                remaining = members()
            record["natural_drain_seconds"] = time.monotonic() - drain_started
            record["timed_out"] = record["timed_out"] or time.monotonic() >= deadline
            record["members_before_cleanup"] = remaining
            record["forced_tree_termination"] = bool(remaining)
            if remaining:
                checked(terminate_job(job, 1))
            deadline = time.monotonic() + 10
            remaining = members()
            while remaining and time.monotonic() < deadline:
                time.sleep(0.05)
                remaining = members()
            record["members_after_cleanup"] = remaining
            if remaining or wait(process.process, 1000) != 0:
                raise RuntimeError("Owned diagnostic processes did not terminate")
            checked(exit_code(process.process, ctypes.byref(code)))
            record.update(returncode=code.value, cleanup_verified=True)
        return record
    except BaseException as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # Even startup, assignment, wait or inventory failure cannot leave the
        # suspended first thread/debuggee behind. The caller retains scratch
        # evidence on an exception instead of claiming successful cleanup.
        cleanup = {}
        try:
            if process.process:
                cleanup["termination_returned"] = bool(
                    terminate_job(job, 1) if assigned else terminate_process(process.process, 1))
                cleanup["root_wait"] = wait(process.process, 10000)
                remaining = members() if assigned else []
                deadline = time.monotonic() + 10
                while remaining and time.monotonic() < deadline:
                    time.sleep(0.05)
                    remaining = members()
                cleanup["remaining_members"] = remaining
                record["cleanup_verified"] = cleanup["root_wait"] == 0 and not remaining
            else:
                record["cleanup_verified"] = True  # No process was created.
        except Exception as exc:
            cleanup["error"] = f"{type(exc).__name__}: {exc}"
            record["cleanup_verified"] = False
        finally:
            cleanup["handles_closed"] = all([
                bool(close(process.thread)) if process.thread else True,
                bool(close(process.process)) if process.process else True,
                bool(close(job)),
            ])
            record["final_cleanup"] = cleanup
        try:
            console.with_name(console.stem + "-process.json").write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8")
        except OSError:
            if "error" not in record:
                raise
        if "error" not in record and (not record["cleanup_verified"] or not cleanup["handles_closed"]):
            raise RuntimeError("Owned process cleanup could not be verified")
