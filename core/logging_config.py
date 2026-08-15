from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.redaction import redact_log_text, redact_log_value, redact_telemetry_mapping
from core.settings import AppSettings


class _RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Sanitize typed arguments before interpolation.  Flattening first
            # loses the distinction between a harmless diagnostic string and a
            # private ``Path`` or an OS exception that embeds one.
            if isinstance(record.args, dict):
                record.args = {
                    str(field): redact_log_value(value)
                    for field, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(redact_log_value(value) for value in record.args)
            else:
                record.args = redact_log_value(record.args)
            message = redact_log_text(record.getMessage())
            if record.exc_info:
                exception_type = record.exc_info[0]
                category = getattr(exception_type, "__name__", "Exception")
                message = f"{message} [exception_type={category}]"
            record.msg = message
            record.args = ()
            # A standard traceback contains absolute source paths, and raw
            # exception text frequently contains musician-selected media paths.
            # Keep the category above; never serialize either path-bearing body.
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        except Exception:
            # Redaction must fail closed even if a hostile object's formatter or
            # ``__str__`` raises.  Retain only a bounded diagnostic category.
            record.msg = "[redacted-log-record]"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
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

    if not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in logger.handlers
    ):
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

    def before_send(event, _hint):
        # Sentry events can include argv, environment, local paths, socket
        # addresses, breadcrumbs, exception values, and arbitrary ``extra``
        # mappings.  Apply the same recursive privacy boundary used by support
        # bundles immediately before the SDK serializes the event.
        if not isinstance(event, dict):
            return None
        return redact_telemetry_mapping(event)

    def before_breadcrumb(crumb, _hint):
        if not isinstance(crumb, dict):
            return None
        return redact_telemetry_mapping(crumb)

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        send_default_pii=False,
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
    )
