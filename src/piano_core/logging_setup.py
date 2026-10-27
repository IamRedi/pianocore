"""Deterministic logging setup for Piano Core.

Single-line structured records with UTC ISO-8601 timestamps, emitted to a
stream (stderr by default). No network, no external telemetry, no secrets.
Only the ``piano_core`` logger tree is configured; library and root loggers
are left untouched.

Re-running :func:`configure_logging` replaces only handlers installed by a
previous :func:`configure_logging` call, so re-initialization never
duplicates output and never steals foreign handlers (e.g. diagnostic
handlers attached by an embedder or test harness).
"""

from __future__ import annotations

import logging
import sys
import time

from piano_core.config import LogLevel

#: Root name of the application logger tree.
LOGGER_NAME = "piano_core"

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class _UTCFormatter(logging.Formatter):
    """Formatter with deterministic UTC ISO-8601 timestamps (millisecond)."""

    converter = staticmethod(time.gmtime)

    def formatTime(self, record: logging.LogRecord, datefmt=None) -> str:  # noqa: N802 (logging API)
        base = time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created))
        return f"{base}.{int(record.msecs):03d}Z"


def configure_logging(
    level: LogLevel | int = LogLevel.INFO,
    stream=None,
) -> logging.Logger:
    """Configure the ``piano_core`` logger tree and return it.

    ``level`` is a :class:`~piano_core.config.LogLevel` member or a numeric
    :mod:`logging` level. ``stream`` defaults to ``sys.stderr``.
    """
    if isinstance(level, LogLevel):
        std_level: int = level.to_logging_level()
    elif isinstance(level, int):
        std_level = level
    else:
        raise TypeError(
            f"level must be a LogLevel or int, got {type(level).__name__}"
        )

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(std_level)
    logger.propagate = False
    for existing in list(logger.handlers):
        if getattr(existing, "_piano_core_owned", False):
            logger.removeHandler(existing)

    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(_UTCFormatter(_FORMAT))
    handler._piano_core_owned = True
    logger.addHandler(handler)
    return logger
