"""Flushed, owned diagnostics for a frozen smoke process with no console."""
from contextlib import contextmanager
from contextvars import ContextVar
import faulthandler
import os
from pathlib import Path
import sys
import tempfile


_OUTPUT = ContextVar("packaged_smoke_diagnostic_output", default=None)


def checkpoint(phase: str) -> None:
    output = _OUTPUT.get()
    if output is not None:
        output.write(phase + "\n")
        output.flush()


@contextmanager
def diagnostic_trace(result_path: Path):
    """Use the runner's private directory, surviving logging/env isolation."""
    parent = result_path.resolve().parent
    if (parent.parent != Path(tempfile.gettempdir()).resolve()
            or not parent.name.startswith("webjam-reference-studio-smoke-")):
        raise RuntimeError("Reference Studio runtime smoke result path is invalid.")
    descriptor = os.open(parent / "diagnostics.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        token = _OUTPUT.set(output)
        # A windowed frozen executable has no reliable stderr. Do not replace
        # pytest's handler when this same proof runs in a source interpreter.
        own_handler = bool(getattr(sys, "frozen", False)) and not faulthandler.is_enabled()
        try:
            if own_handler:
                faulthandler.enable(file=output, all_threads=True)
                faulthandler.dump_traceback_later(5, repeat=True, file=output)
            yield
        finally:
            if own_handler:
                faulthandler.cancel_dump_traceback_later()
                faulthandler.disable()
            _OUTPUT.reset(token)
