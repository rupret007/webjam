from __future__ import annotations

import logging
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.logging_config import configure_logging
from core.settings import AppSettings

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Live desktop code whose log records must pass through the redaction filter
# that ``configure_logging`` attaches to the ``webjam`` logger's handlers.
# ``legacy/`` and ``tests/`` are deliberately excluded.
_LIVE_SOURCE_ROOTS = (
    "core",
    "services",
    "storage",
    "api",
    "utils",
    "ui",
    "webjam_qt",
)
_LIVE_ROOT_MODULES = (
    "jamulus_controller.py",
    "jamulus_state_manager.py",
    "webex_integration.py",
    "webjam_qt_main.py",
)

_GET_LOGGER_RE = re.compile(
    r"logging\.getLogger\(\s*(?P<argument>[^)]*?)\s*\)"
)


class TestLoggingConfigEdge(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("webjam")
        self.original_handlers = list(self.logger.handlers)
        self.original_level = self.logger.level
        self.original_propagate = self.logger.propagate
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)

    def tearDown(self):
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            if handler not in self.original_handlers:
                try:
                    handler.close()
                except Exception:
                    pass
        for handler in self.original_handlers:
            self.logger.addHandler(handler)
        self.logger.setLevel(self.original_level)
        self.logger.propagate = self.original_propagate

    def test_configure_logging_creates_missing_parent_directory(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            log_path = temp_dir / "nested" / "logs" / "webjam.log"
            logger = configure_logging(AppSettings(log_file=str(log_path)))
            logger.info("logging smoke test")
            for handler in logger.handlers:
                flush = getattr(handler, "flush", None)
                if callable(flush):
                    flush()

            self.assertTrue(log_path.exists())
            self.assertGreaterEqual(len(logger.handlers), 2)
            for handler in list(logger.handlers):
                if isinstance(handler, logging.FileHandler):
                    logger.removeHandler(handler)
                    handler.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_live_modules_only_construct_webjam_namespace_loggers(self):
        """Every live logger must live under the ``webjam`` root logger.

        The redaction filter is attached to the ``webjam`` logger's handlers
        and ``webjam`` does not propagate.  A module logging under
        ``__name__`` (for example ``storage.repository``) propagates to the
        root logger instead and can reach stderr unredacted — including
        credential-file paths, usernames, and take titles.
        """

        sources: list[Path] = []
        for root in _LIVE_SOURCE_ROOTS:
            sources.extend(sorted((_REPO_ROOT / root).rglob("*.py")))
        for name in _LIVE_ROOT_MODULES:
            candidate = _REPO_ROOT / name
            if candidate.exists():
                sources.append(candidate)

        self.assertGreater(len(sources), 50)
        offenders: list[str] = []
        for source in sources:
            text = source.read_text(encoding="utf-8")
            for match in _GET_LOGGER_RE.finditer(text):
                argument = match.group("argument")
                if not argument:
                    # ``logging.getLogger()`` is the root logger: never safe.
                    offenders.append(f"{source}: bare getLogger()")
                    continue
                literal = re.match(r"""^["'](?P<name>[^"']*)["']$""", argument)
                if literal is not None:
                    if not literal.group("name").startswith("webjam"):
                        offenders.append(f"{source}: {argument}")
                    continue
                offenders.append(f"{source}: {argument}")
        self.assertEqual(offenders, [])

    def test_configure_logging_falls_back_to_stream_handler_when_file_handler_fails(self):
        class _ExplodingRotatingHandler:
            def __init__(self, *_args, **_kwargs):
                raise OSError("disk blocked")

        with patch("core.logging_config.RotatingFileHandler", new=_ExplodingRotatingHandler):
            logger = configure_logging(AppSettings(log_file="C:/invalid/webjam.log"))

        self.assertEqual(len(logger.handlers), 1)
        self.assertIsInstance(logger.handlers[0], logging.StreamHandler)


if __name__ == "__main__":
    unittest.main()
