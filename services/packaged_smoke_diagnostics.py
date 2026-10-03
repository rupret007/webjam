"""Flushed, owned diagnostics for source and frozen workflow smoke checks."""
from contextlib import contextmanager
from contextvars import ContextVar
import faulthandler
import os
from pathlib import Path
import sys
import tempfile
from threading import Lock

from PySide6.QtCore import qFormatLogMessage, qInstallMessageHandler


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
        # A windowed frozen executable has no reliable stderr. Source pytest
        # already owns faulthandler; keep that handler while forwarding Qt
        # messages so an abort cannot lose them inside pytest's capture.
        frozen = bool(getattr(sys, "frozen", False))
        own_handler = frozen and not faulthandler.is_enabled()
        message_lock = Lock()
        capture_active = True
        previous_qt_handler = None
        qt_handler_installed = False

        def qt_message(kind, context, message):
            # Qt can report a fatal error from a worker without this context's
            # ContextVar. Flush its reason before Qt aborts or forwarding runs.
            with message_lock:
                if capture_active:
                    output.write(f"Qt {kind.name}: {message}\n")
                    output.flush()
            if previous_qt_handler is not None:
                previous_qt_handler(kind, context, message)
            elif sys.stderr is not None:
                sys.stderr.write(qFormatLogMessage(kind, context, message) + "\n")
                sys.stderr.flush()

        try:
            previous_qt_handler = qInstallMessageHandler(qt_message)
            qt_handler_installed = True
            if own_handler:
                faulthandler.enable(file=output, all_threads=True)
                # CPython 3.11's watchdog reads live frames without the GIL
                # (python/cpython#116008). Periodic dumps can crash a healthy
                # worker; retain fatal capture and flushed phase checkpoints.
            yield
        finally:
            if qt_handler_installed:
                qInstallMessageHandler(previous_qt_handler)
                with message_lock:
                    capture_active = False
            if own_handler:
                faulthandler.disable()
            _OUTPUT.reset(token)
