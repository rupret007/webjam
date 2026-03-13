from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.settings import AppSettings


def configure_logging(settings: AppSettings) -> logging.Logger:
    logger = logging.getLogger("webjam")
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
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

