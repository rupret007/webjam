from __future__ import annotations

import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.logging_config import configure_logging
from core.settings import AppSettings


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
