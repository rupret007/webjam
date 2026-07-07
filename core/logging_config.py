from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.redaction import redact_text
from core.settings import AppSettings


class _RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def configure_logging(settings: AppSettings) -> logging.Logger:
    logger = logging.getLogger("webjam")
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    redaction_filter = _RedactionFilter()

    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redaction_filter)
        logger.addHandler(stream_handler)

    if not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        log_path = Path(settings.log_file).expanduser()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path, maxBytes=1_500_000, backupCount=3, encoding="utf-8"
            )
        except (OSError, ValueError) as exc:
            logger.warning("File logging disabled for %s: %s", log_path, exc)
        else:
            file_handler.setFormatter(formatter)
            file_handler.addFilter(redaction_filter)
            logger.addHandler(file_handler)

    return logger


def configure_sentry(settings: AppSettings) -> None:
    if not settings.enable_sentry or not settings.sentry_dsn:
        return
    try:
        import sentry_sdk  # type: ignore
    except Exception:
        return

    sentry_sdk.init(dsn=settings.sentry_dsn)
