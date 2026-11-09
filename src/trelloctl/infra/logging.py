"""Rotating log file with one line per invocation, secret masking and stderr mirroring."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "trelloctl"
LOG_FILE = "trelloctl.log"
MASK = "***"
_QUERY_SECRET = re.compile(r"((?:key|token)=)[^&\s\"']+", re.IGNORECASE)
_FORMAT = "%(asctime)s %(levelname)s pid=%(process)d %(message)s"

SecretsProvider = Callable[[], Iterable[str]]


def mask_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Replace known secret values and `key=`/`token=` query values with a mask."""
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, MASK)
    return _QUERY_SECRET.sub(rf"\1{MASK}", text)


class MaskingFilter(logging.Filter):
    """Masks the API key and token in the message, its arguments and the traceback."""

    def __init__(self, secrets: SecretsProvider | None = None) -> None:
        super().__init__()
        self._secrets: SecretsProvider = secrets or (lambda: ())

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = tuple(self._secrets())
        record.msg = mask_text(record.getMessage(), secrets)
        record.args = None
        if record.exc_info:
            formatted = logging.Formatter().formatException(record.exc_info)
            record.exc_text = mask_text(formatted, secrets)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = mask_text(record.exc_text, secrets)
        return True


class _IsoUtcFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created, UTC)
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating handler that keeps writing when the rotation fails (locked file)."""

    _reporting_failure = False

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        # The failure warning goes through this same handler; without this guard
        # it would try to rotate again, fail again and recurse without end.
        if self._reporting_failure:
            return False
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError as exc:
            if self.stream is None:
                self.stream = self._open()
            self._reporting_failure = True
            try:
                logging.getLogger(LOGGER_NAME).warning(
                    "Log rotation failed (%s); continuing without rotating.", exc
                )
            finally:
                self._reporting_failure = False


def _close_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def configure_logging(
    logs_dir: Path,
    *,
    level: str = "info",
    max_bytes: int = 1_048_576,
    backup_count: int = 5,
    verbose: bool = False,
    secrets: SecretsProvider | None = None,
) -> logging.Logger:
    """Configure the `trelloctl` logger: rotating file, plus stderr when `verbose`.

    No handler ever writes to stdout (AC-3.2). Reconfiguring replaces earlier handlers.
    """
    logger = logging.getLogger(LOGGER_NAME)
    _close_handlers(logger)
    logger.setLevel(level.upper())
    logger.propagate = False
    formatter = _IsoUtcFormatter(_FORMAT)
    handlers: list[logging.Handler] = [
        SafeRotatingFileHandler(
            logs_dir / LOG_FILE, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
    ]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(MaskingFilter(secrets))
        logger.addHandler(handler)
    return logger


def shutdown_logging() -> None:
    """Close and remove the handlers of the `trelloctl` logger."""
    _close_handlers(logging.getLogger(LOGGER_NAME))


def log_invocation(
    logger: logging.Logger, command: str, duration_ms: int, result: str, *, ok: bool = True
) -> None:
    """Write the closing line of an invocation: command, duration and result."""
    logger.log(
        logging.INFO if ok else logging.ERROR,
        'command="%s" duration_ms=%d result=%s',
        command,
        duration_ms,
        result,
    )
