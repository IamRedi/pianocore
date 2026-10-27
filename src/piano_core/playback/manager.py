"""Service-side playback session manager (PIANO-P012).

Single composition point between the canonical library and the canonical
P009 Playback Engine inside the running application. The manager owns NO
MIDI surface and NO scheduling of its own: every command flows through
the existing :class:`~piano_core.midi.output.MidiOutputAdapter` (via the
:class:`~piano_core.playback.engine.PlaybackEngine`), and every song
enters through the canonical
:class:`~piano_core.library.service.LibraryService`:

    song_id -> LibraryService lookup -> validated resolve -> P009
    load_timeline -> PlaybackEngine.play

A client-supplied FILESYSTEM PATH is never accepted: the only path that
exists here is the one :meth:`LibraryService.resolve` validated under the
canonical source root.

The appliance-wide single-playback invariant (P009) is preserved by
participating in the shared advisory lock
(:mod:`piano_core.playback.advisory`): a session holds the lock for its
whole lifetime, so ``piano-core-play`` and API playback can never run at
the same time, and vice versa.

One internal lock serializes control operations (play/pause/resume/stop)
so concurrent API requests resolve deterministically: exactly one play
wins, losers receive the typed already-active error; stop/pause/resume
against the engine keep the P009 semantics unchanged. A small watcher
releases activity ownership as soon as the engine reaches a terminal
state. Natural completion/failure retains the canonical song identity for
honest terminal-state observability; explicit stop clears it.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from piano_core.midi.output import MidiOutputAdapter
from piano_core.playback.advisory import (
    PlaybackLock,
    acquire_playback_lock,
    default_lock_path,
)
from piano_core.playback.engine import (
    MonotonicClock,
    PlaybackAlreadyActiveError,
    PlaybackEngine,
    PlaybackError,
    PlaybackState,
)
from piano_core.playback.timeline import load_timeline

__all__ = [
    "PlaybackLockBusyError",
    "PlaybackManager",
    "PlaybackNotAvailableError",
    "PlaybackSession",
]


class PlaybackNotAvailableError(PlaybackError):
    """Automated playback is unavailable (no open canonical output)."""


class PlaybackLockBusyError(PlaybackError):
    """Another automated playback (e.g. the CLI) holds the appliance lock."""


@dataclass(frozen=True, slots=True)
class PlaybackSession:
    """Immutable playback snapshot for observability surfaces."""

    state: str
    tempo_multiplier: float
    position_seconds: float
    duration_seconds: float
    events_sent: int
    error: str | None
    song_id: str | None
    title: str | None
    available: bool


class PlaybackManager:
    """Single-session playback control over the canonical output adapter."""

    def __init__(
        self,
        output_adapter: MidiOutputAdapter,
        library,
        clock: MonotonicClock | None = None,
        timeline_loader: Callable[..., object] | None = None,
        lock_path: str | None = None,
        logger: logging.Logger | None = None,
        event_observer: Callable[[object], None] | None = None,
        ownership_observer: Callable[[], None] | None = None,
        on_session_end: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(output_adapter, MidiOutputAdapter):
            raise TypeError(
                "output_adapter must be a MidiOutputAdapter, "
                f"got {type(output_adapter).__name__}"
            )
        self._adapter = output_adapter
        self._library = library
        self._engine = PlaybackEngine(
            output_adapter,
            clock=clock,
            logger=logger,
            event_observer=event_observer,
            ownership_observer=ownership_observer,
        )
        # PIANO-P014: invoked once when the engine reaches a terminal state
        # (stop/completion/failure/cancellation). Must be safe/non-blocking.
        self._on_session_end = on_session_end
        self._load_timeline = timeline_loader or load_timeline
        self._lock_path = lock_path or default_lock_path()
        self._log = logger or logging.getLogger("piano_core.playback.manager")
        self._lock = threading.RLock()
        self._advisory: PlaybackLock | None = None
        self._session_song_id: str | None = None
        self._session_title: str | None = None
        self._watcher: threading.Thread | None = None

    # ------------------------------------------------------------------
    # observability
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """True when the canonical output adapter is open."""
        return self._adapter.is_open

    def snapshot(self) -> PlaybackSession:
        """Current playback snapshot (engine state + session identity)."""
        with self._lock:
            return PlaybackSession(
                state=self._engine.state.value,
                tempo_multiplier=self._engine.tempo_scale,
                position_seconds=self._engine.position_seconds,
                duration_seconds=self._engine.duration,
                events_sent=self._engine.events_sent,
                error=self._engine.error,
                song_id=self._session_song_id,
                title=self._session_title,
                available=self.available,
            )

    # ------------------------------------------------------------------
    # control API
    # ------------------------------------------------------------------
    def play(self, song_id: str, tempo_multiplier: float = 1.0) -> PlaybackSession:
        """Resolve ``song_id`` through the library and start playback."""
        if not isinstance(song_id, str) or not song_id.strip():
            raise ValueError("song_id must be a non-empty string")
        with self._lock:
            self._require_available()
            if not self._engine.state.is_terminal:
                raise PlaybackAlreadyActiveError(
                    f"playback is {self._engine.state.value}; stop it before "
                    "starting another session"
                )
            advisory = self._acquire_advisory()
            try:
                if self._library is None:
                    from piano_core.library.model import LibraryConfigError

                    raise LibraryConfigError(
                        "library is not available; playback by song_id "
                        "requires the canonical LibraryService"
                    )
                record = self._library.get(song_id)
                if record is None:
                    from piano_core.library.model import LibraryPathError

                    raise LibraryPathError(f"unknown song_id: {song_id}")
                path: Path = self._library.resolve(song_id)
                timeline = self._load_timeline(path, tempo_scale=tempo_multiplier)
                self._engine.play(timeline, tempo_scale=tempo_multiplier)
            except Exception:
                self._release_advisory()
                raise
            self._session_song_id = song_id
            title = record.title or record.filename
            self._session_title = title
            self._start_watcher()
            session = self.snapshot()
        self._log.info(
            "playback session started song_id=%s title=%r tempo=%.2f",
            song_id,
            title,
            tempo_multiplier,
        )
        return session

    def pause(self) -> PlaybackSession:
        """Pause the active session (P009 release-on-pause semantics)."""
        with self._lock:
            self._require_available()
            self._engine.pause()
            return self.snapshot()

    def output_disconnected(self) -> None:
        """Serialize connection loss with session creation; never auto-resume."""
        with self._lock:
            self._engine.output_disconnected()

    def resume(self) -> PlaybackSession:
        """Resume the paused session from its frozen position."""
        with self._lock:
            self._require_available()
            self._engine.resume()
            return self.snapshot()

    def stop(self) -> tuple[PlaybackSession, bool]:
        """Stop the active session (owned-only cleanup). Idempotent.

        Returns the post-stop snapshot and whether a session was actually
        stopped. STOP releases ONLY Piano Core-owned notes/sustain via the
        engine's canonical STOP path — never a global sweep, never PANIC.
        """
        with self._lock:
            self._require_available()
            stopped = self._engine.stop()
            self._clear_session_locked()
            return self.snapshot(), stopped

    def shutdown(self) -> None:
        """Stop any active session and release the lock. Idempotent."""
        with self._lock:
            try:
                self._engine.stop()
            except Exception as exc:  # cleanup must never break shutdown
                self._log.error("playback shutdown stop failed: %s", exc)
            self._clear_session_locked()
        watcher = self._watcher
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(2.0)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _require_available(self) -> None:
        if not self.available:
            raise PlaybackNotAvailableError(
                "midi output is not open; automated playback is unavailable"
            )

    def _acquire_advisory(self) -> PlaybackLock:
        try:
            self._advisory = acquire_playback_lock(self._lock_path, self._log)
        except OSError as exc:
            raise PlaybackLockBusyError(
                "another automated playback already holds the appliance "
                f"playback lock ({self._lock_path})"
            ) from exc
        return self._advisory

    def _release_advisory(self) -> None:
        if self._advisory is not None:
            try:
                self._advisory.release()
            finally:
                self._advisory = None

    def _clear_session_locked(self) -> None:
        """Clear session bookkeeping + release the lock (idempotent)."""
        self._release_advisory()
        self._session_song_id = None
        self._session_title = None
        self._watcher = None

    def _start_watcher(self) -> None:
        """Release ownership when the engine reaches a terminal state."""
        watcher = threading.Thread(
            target=self._watch_session, name="piano-core-playback-watch", daemon=True
        )
        self._watcher = watcher
        watcher.start()

    def _watch_session(self) -> None:
        self._engine.wait()
        owns_session = False
        with self._lock:
            # An explicit stop can invalidate this watcher before it obtains
            # the manager lock. It must never release a successor session's
            # advisory lock or erase that session's watcher reference.
            if self._watcher is threading.current_thread():
                owns_session = True
                # UI-002: COMPLETED/FAILED must remain attributable to the
                # song that produced the terminal state. Release appliance
                # activity immediately, but retain song_id/title until
                # explicit stop or the next play replaces them.
                self._release_advisory()
                self._watcher = None
        if owns_session:
            self._log.info(
                "playback session ended state=%s", self._engine.state.value
            )
        if self._on_session_end is not None:
            try:
                self._on_session_end()
            except Exception:  # observers never break session bookkeeping
                self._log.exception("playback session-end observer failed")
