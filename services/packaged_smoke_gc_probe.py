"""Explicit worker-GC experiment confined to the private workflow smoke hook."""
from contextlib import contextmanager
import gc
import json
import os
from pathlib import Path
import threading


@contextmanager
def worker_gc_probe(result: Path):
    mode = os.environ.get("WEBJAM_SMOKE_WORKER_GC", "ordinary")
    if mode == "ordinary":
        yield
        return
    if mode not in {"before", "after"}:
        raise ValueError("Unknown private smoke worker-GC experiment")
    # The caller must validate its owned result path before entering this
    # context. No object enumeration/finalizers or background watchdogs are
    # added; those could themselves change the lifetime under investigation.
    from webjam_qt.windows.workspace_backup import WorkspaceJob

    original = WorkspaceJob.start
    was_enabled = gc.isenabled()
    events = 0
    active = True
    lock = threading.Lock()
    with (result.parent / "gc-probe.jsonl").open("x", encoding="utf-8") as output:
        def collect():
            nonlocal events
            with lock:
                if not active:
                    return
                events += 1
                event = {"event": events, "mode": mode, "pid": os.getpid(),
                         "thread_id": threading.get_ident(), "thread": threading.current_thread().name}
                output.write(json.dumps({**event, "phase": "before-collection"}) + "\n")
                output.flush()
                count = gc.collect()
                output.write(json.dumps({**event, "phase": "after-collection", "collected": count}) + "\n")
                output.flush()

        def start(self, function):
            def wrapped(progress, cancelled):
                if mode == "before":
                    collect()
                value = function(progress, cancelled)
                if mode == "after":
                    collect()
                return value
            return original(self, wrapped)

        gc.disable()
        WorkspaceJob.start = start
        try:
            yield
            if not events:
                raise RuntimeError("Requested worker-GC experiment did not execute")
        finally:
            WorkspaceJob.start = original
            with lock:
                active = False
            if was_enabled:
                gc.enable()
