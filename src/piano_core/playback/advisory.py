"""Appliance-wide single-playback advisory lock (PIANO-P012).

P009 established the invariant "at most ONE automated playback per
appliance" with a cross-process ``flock`` at the CLI layer. PIANO-P012
extends that invariant to the service: the canonical PlaybackManager
(``piano_core.playback.manager``) and ``piano-core-play`` (CLI) share ONE
advisory lock so a CLI playback and an API playback can never run
concurrently on the same Roland.

The lock lives in the SHARED ``/run/lock`` directory — never the
per-process ``/tmp`` (the systemd unit sets ``PrivateTmp=true``, which
would silently decouple the service from the CLI).

Cross-user cooperation: the CLI runs as the login user while the service
runs as ``piano-core``. The lock file may therefore have been created by
the other identity. Opening falls back to READ-ONLY when the file is not
writable by us: ``flock`` works on read-only descriptors, so mutual
exclusion still holds (only the cosmetic version stamp is skipped).
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

from piano_core import __version__

__all__ = ["LOCK_FILENAME", "LOCK_PATH", "PlaybackLock", "acquire_playback_lock"]

#: Advisory-lock file name (in the shared /run/lock directory).
LOCK_FILENAME = "piano-core-playback.lock"

#: Default lock path: the shared, sticky, world-writable /run/lock.
LOCK_PATH = "/run/lock/" + LOCK_FILENAME


def default_lock_path() -> str:
    """Return the shared lock path (with a portable fallback)."""
    if os.path.isdir("/run/lock"):
        return LOCK_PATH
    import tempfile

    return tempfile.gettempdir() + "/" + LOCK_FILENAME


class PlaybackLock:
    """Held advisory lock; release via :meth:`release` (idempotent)."""

    def __init__(self, path: str, fd: int, logger: logging.Logger | None) -> None:
        self._path = path
        self._fd = fd
        self._log = logger or logging.getLogger("piano_core.playback.lock")
        self._released = False

    def release(self) -> None:
        """Release the lock and close the descriptor. Idempotent."""
        if self._released:
            return
        self._released = True
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError as exc:  # pragma: no cover - defensive
            self._log.error("playback lock unlock failed path=%s: %s", self._path, exc)
        finally:
            os.close(self._fd)
            self._log.info("playback lock released path=%s", self._path)


def acquire_playback_lock(
    path: str, logger: logging.Logger | None = None
) -> PlaybackLock:
    """Acquire the non-blocking exclusive playback lock (fail closed).

    Raises :class:`BlockingIOError` (an :class:`OSError`) when another
    automated playback already holds the lock.
    """
    log = logger or logging.getLogger("piano_core.playback.lock")
    fd = _open_lock_fd(path, log)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise
    log.info("playback lock acquired path=%s", path)
    return PlaybackLock(path, fd, log)


def _open_lock_fd(path: str, log: logging.Logger) -> int:
    """Open (or create) the lock file, falling back to read-only."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    except PermissionError:
        # Created by the other identity (CLI user vs service user): flock
        # on a read-only descriptor still enforces mutual exclusion.
        fd = os.open(path, os.O_RDONLY)
        log.debug("playback lock opened read-only (cross-user) path=%s", path)
        return fd
    try:
        os.write(fd, f"{__version__}\n".encode())
    except OSError:
        pass  # cosmetic version stamp only; exclusion is the flock
    return fd
