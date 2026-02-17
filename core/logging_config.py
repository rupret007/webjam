from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from core.settings import AppSettings


def configure_logging(settings: AppSettings) -> logging.Logger:
    logger = logging.getLogger("webjam")
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        file_handler = RotatingFileHandler(
            settings.log_file, maxBytes=1_500_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
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

