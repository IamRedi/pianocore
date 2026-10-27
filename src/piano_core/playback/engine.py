"""Playback engine: typed lifecycle and deterministic scheduling (P009).

Turns a :class:`~piano_core.playback.timeline.Timeline` into owned,
time-ordered canonical output commands through the existing
:class:`~piano_core.midi.output.MidiOutputAdapter`. The engine owns NO
MIDI surface of its own: every note/sustain flows through the adapter,
so Piano Core's ownership tracking and the canonical
``STOP_AUTOMATED_NOTES`` semantics apply unchanged.

Lifecycle (typed :class:`PlaybackState`):

    IDLE --play--> PLAYING --pause--> PAUSED --resume--> PLAYING
    PLAYING/PAUSED --stop--> STOPPING --(cleanup)--> IDLE
    PLAYING --(song end)--> COMPLETED          (terminal for the session)
    PLAYING --(error)--> FAILED                (terminal for the session)

``play()`` is legal only from ``IDLE``/``COMPLETED``/``FAILED`` — one
engine holds at most one active session, which enforces single-active
playback in-process (the CLI adds a cross-process advisory lock).

Timing: an injected monotonic clock advances song position at the
tempo scale; every wait is a bounded, cancellable ``Event`` wait —
pause/stop/resume wake the worker immediately (no polling, no busy
loop). Release-on-pause: pausing freezes the position and releases
Piano Core-owned notes/sustain via STOP (owned-only) so nothing keeps
sounding while paused; resume continues from the frozen position.

Safety invariants (P009 contract):

- cleanup on stop/complete/failure uses ``STOP_AUTOMATED_NOTES`` ONLY
  (releases Piano Core-owned notes + sustain; never PANIC, never a
  global CC123/CC120 sweep);
- the engine has no input surface whatsoever: physical MIDI input can
  never reach playback, and the P006 StateStore is never imported or
  touched (it remains the physical-input authority);
- a worker failure releases owned state best-effort and lands in
  ``FAILED`` — errors never escape the worker thread as exceptions.
"""

from __future__ import annotations

import enum
import logging
import threading
import time

from piano_core.midi.output_events import stop_automated_notes
from piano_core.playback.timeline import (
    Timeline,
    validate_tempo_scale,
)

__all__ = [
    "MonotonicClock",
    "PlaybackAlreadyActiveError",
    "PlaybackEngine",
    "PlaybackError",
    "PlaybackState",
]

#: How long ``stop()`` waits for the worker to reach its terminal state.
_STOP_JOIN_TIMEOUT = 5.0

#: Upper bound for one pause wait chunk (the wait itself wakes early).
_PAUSE_WAIT_CHUNK = 0.5


class PlaybackError(RuntimeError):
    """A playback request violated the engine contract. Fail closed."""


class PlaybackAlreadyActiveError(PlaybackError):
    """``play()`` was called while a session was already active."""


class PlaybackState(enum.Enum):
    """Typed playback lifecycle states."""

    IDLE = "IDLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        """True when no session is active (play may start)."""
        return self in (PlaybackState.IDLE, PlaybackState.COMPLETED, PlaybackState.FAILED)


class MonotonicClock:
    """Production clock: wall monotonic time plus interruptible waits.

    ``wait`` drives schedule delays; ``wait_bounded`` is a defensive
    re-check wait (pause chunks) with identical production behavior —
    the split exists so deterministic test clocks can gate schedule
    delays while letting safety chunks self-advance.
    """

    def monotonic(self) -> float:
        return time.monotonic()

    def wait(self, event: threading.Event, timeout: float) -> bool:
        """Wait for ``event`` up to ``timeout`` seconds (bounded)."""
        return event.wait(timeout)

    def wait_bounded(self, event: threading.Event, timeout: float) -> bool:
        """Bounded re-check wait (same semantics as :meth:`wait`)."""
        return event.wait(timeout)


class PlaybackEngine:
    """Single-session playback scheduler over the canonical output adapter."""

    def __init__(
        self,
        output_adapter,
        clock: MonotonicClock | None = None,
        logger: logging.Logger | None = None,
        event_observer=None,
        ownership_observer=None,
    ) -> None:
        if output_adapter is None:
            raise TypeError("output_adapter is required")
        self._adapter = output_adapter
        self._clock = clock or MonotonicClock()
        self._log = logger or logging.getLogger("piano_core.playback")
        # PIANO-P014: optional safe observer invoked AFTER each scheduled
        # command was applied successfully. Must be fast/non-blocking and
        # must never raise into the scheduler (see _notify_observer).
        self._event_observer = event_observer
        # Authoritative-output reconciliation is separate from the applied
        # command observer (for example LEDs). It also runs after a partially
        # failed multi-send STOP.
        self._ownership_observer = ownership_observer
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop_requested = threading.Event()
        self._pause_requested = threading.Event()
        self._output_failed = threading.Event()
        self._output_generation = 0
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = PlaybackState.IDLE
        self._error: str | None = None
        # Session fields (valid while a session exists or its outcome is
        # still observable):
        self._timeline: Timeline | None = None
        self._tempo_scale = 1.0
        self._next_index = 0
        self._events_sent = 0
        self._origin_song = 0.0
        self._base_wall = 0.0

    # ------------------------------------------------------------------
    # observability
    # ------------------------------------------------------------------
    @property
    def state(self) -> PlaybackState:
        with self._lock:
            return self._state

    @property
    def tempo_scale(self) -> float:
        with self._lock:
            return self._tempo_scale

    @property
    def events_sent(self) -> int:
        with self._lock:
            return self._events_sent

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def duration(self) -> float:
        """Song-time duration of the current/last timeline (0.0 if none)."""
        with self._lock:
            return self._timeline.duration if self._timeline is not None else 0.0

    @property
    def position_seconds(self) -> float:
        """Current song position (frozen while paused; clamped to duration)."""
        with self._lock:
            if self._timeline is None:
                return 0.0
            position = self._origin_song
            if self._state is PlaybackState.PLAYING:
                position += (self._clock.monotonic() - self._base_wall) * self._tempo_scale
            return max(0.0, min(position, self._timeline.duration))

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until the session reaches a terminal state; True if terminal."""
        return self._done.wait(timeout)

    # ------------------------------------------------------------------
    # control API
    # ------------------------------------------------------------------
    def play(self, timeline: Timeline, tempo_scale: float = 1.0) -> None:
        """Start one playback session (single-active, fail closed)."""
        if not isinstance(timeline, Timeline):
            raise TypeError(
                f"play requires a Timeline, got {type(timeline).__name__}"
            )
        scale = validate_tempo_scale(tempo_scale)
        with self._lock:
            if not self._state.is_terminal:
                raise PlaybackAlreadyActiveError(
                    f"playback is {self._state.value}; stop it before starting another session"
                )
            if not self._adapter.is_open:
                raise PlaybackError(
                    "midi output is not open; open the adapter before playing"
                )
            self._timeline = timeline
            self._output_generation = self._adapter.generation
            self._tempo_scale = scale
            self._next_index = 0
            self._events_sent = 0
            self._origin_song = 0.0
            self._base_wall = self._clock.monotonic()
            self._error = None
            self._state = PlaybackState.PLAYING
            self._stop_requested.clear()
            self._pause_requested.clear()
            self._output_failed.clear()
            self._wake.clear()
            self._done.clear()
            thread = threading.Thread(
                target=self._run,
                name="piano-core-playback",
                daemon=False,
            )
            self._thread = thread
        self._log.info(
            "playback start source=%s events=%d duration=%.3fs tempo_scale=%.2f",
            timeline.source or "<memory>",
            len(timeline.events),
            timeline.duration,
            scale,
        )
        thread.start()

    def pause(self) -> None:
        """Pause the active session (releases owned notes, keeps position)."""
        with self._lock:
            if self._state is not PlaybackState.PLAYING:
                raise PlaybackError(
                    f"pause requires PLAYING, got {self._state.value}"
                )
            now = self._clock.monotonic()
            self._origin_song = min(
                self._origin_song
                + (now - self._base_wall) * self._tempo_scale,
                self._timeline.duration if self._timeline else 0.0,
            )
            self._base_wall = now
            self._state = PlaybackState.PAUSED
            self._pause_requested.set()
            self._wake.set()
        self._log.info("playback paused position=%.3fs", self.position_seconds)

    def resume(self) -> None:
        """Resume the paused session from its frozen position."""
        with self._lock:
            if self._state is not PlaybackState.PAUSED:
                raise PlaybackError(
                    f"resume requires PAUSED, got {self._state.value}"
                )
            self._base_wall = self._clock.monotonic()
            self._state = PlaybackState.PLAYING
            self._pause_requested.clear()
            self._wake.set()
        self._log.info("playback resumed position=%.3fs", self.position_seconds)

    def stop(self) -> bool:
        """Stop the active session (owned-only cleanup). Idempotent.

        Returns True when a session was stopped, False when the engine
        was already terminal. ``stop()`` returns only after the worker
        reached its terminal state (bounded join): on return the engine
        is normally ``IDLE`` with all owned notes/sustain released; only
        a join timeout leaves ``STOPPING`` (logged loudly, never
        reported as a clean stop).
        """
        with self._lock:
            if self._state.is_terminal:
                return False
            if self._state is not PlaybackState.STOPPING:
                self._state = PlaybackState.STOPPING
            self._stop_requested.set()
            self._wake.set()
            thread = self._thread
        self._log.info("playback stop requested position=%.3fs", self.position_seconds)
        if thread is not None:
            thread.join(_STOP_JOIN_TIMEOUT)
            if thread.is_alive():
                # Honest degradation: never claim a clean stop that did
                # not happen; the worker remains STOPPING.
                self._log.error(
                    "playback worker did not stop within %.1fs", _STOP_JOIN_TIMEOUT
                )
        return True

    # ------------------------------------------------------------------
    # worker (runs on the dedicated playback thread)
    # ------------------------------------------------------------------
    def output_disconnected(self) -> None:
        """Wake an active session; failure/cleanup stays on its own worker."""
        with self._lock:
            if self._state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                self._output_failed.set()
                self._wake.set()

    def _run(self) -> None:
        try:
            self._session_loop()
        except Exception as exc:  # pragma: no cover - defensive net
            self._fail(f"internal playback error: {exc!r}")

    def _session_loop(self) -> None:
        while True:
            # One locked look: stop/pause/completion are decided
            # atomically against the control APIs (pause() and stop()
            # mutate state and flags under the same lock).
            with self._lock:
                stop = self._stop_requested.is_set()
                paused = self._pause_requested.is_set()
                complete = not stop and not paused and (
                    self._timeline is None
                    or self._next_index >= len(self._timeline.events)
                )
                timeline = self._timeline
                index = self._next_index
            if stop:
                self._finish_stopped()
                return
            if self._output_failed.is_set():
                self._fail("MIDI output disconnected; start a fresh playback after reconnect")
                return
            if paused:
                # Position bookkeeping already happened in pause(). The
                # owned-note release happens HERE on the worker — after
                # any in-flight send completed — so a NOTE_ON racing the
                # pause can never end up owned-but-unreleased. Release-on-
                # pause: nothing keeps sounding while paused (resume
                # continues from the frozen position without re-attacks).
                cleanup_error = self._release_owned("pause")
                if cleanup_error is not None:
                    self._finish_failed(
                        f"playback cleanup (pause) failed: {cleanup_error}"
                    )
                    return
                # Wait in bounded chunks until resume/stop wakes us
                # (chunks are defensive re-checks; wake is immediate).
                while True:
                    self._wake.clear()
                    if self._stop_requested.is_set():
                        self._finish_stopped()
                        return
                    if self._output_failed.is_set():
                        self._fail("MIDI output disconnected while paused")
                        return
                    if not self._pause_requested.is_set():
                        break
                    self._clock.wait_bounded(self._wake, _PAUSE_WAIT_CHUNK)
                continue
            if complete:
                self._finish_completed()
                return
            event = timeline.events[index]
            delay = self._seconds_until(event.time)
            if delay > 0.0:
                self._clock.wait(self._wake, delay)
                continue
            try:
                self._adapter.apply_for_generation(event.command, self._output_generation)
            except Exception as exc:
                self._fail(f"output command failed ({event.command.describe()}): {exc}")
                return
            self._notify_ownership_observer()
            self._notify_observer(event.command)
            with self._lock:
                self._next_index += 1
                self._events_sent += 1

    def _notify_observer(self, command) -> None:
        """Best-effort observer notification (PIANO-P014).

        Observer exceptions are swallowed and logged: a failing observer
        (e.g. the LED integration) can never fail, stall, or deschedule
        playback. The observer contract is non-blocking; blocking work
        belongs in the observer's own worker.
        """
        if self._event_observer is None:
            return
        try:
            self._event_observer(command)
        except Exception as exc:  # pragma: no cover - defensive net
            self._log.warning("playback event observer failed: %r", exc)

    def _notify_ownership_observer(self) -> None:
        """Reconcile consumers from actual adapter ownership, best effort."""
        if self._ownership_observer is None:
            return
        try:
            self._ownership_observer()
        except Exception as exc:  # pragma: no cover - defensive net
            self._log.warning("playback ownership observer failed: %r", exc)

    def _seconds_until(self, target_song_time: float) -> float:
        """Wall seconds until the given song time (>= 0)."""
        with self._lock:
            song_position = self._origin_song + (
                self._clock.monotonic() - self._base_wall
            ) * self._tempo_scale
        return max(0.0, (target_song_time - song_position) / self._tempo_scale)

    # ------------------------------------------------------------------
    # terminal transitions (worker context)
    # ------------------------------------------------------------------
    def _release_owned(self, reason: str) -> str | None:
        """Release owned output and always reconcile actual ownership.

        Returns an error string so the caller can make a terminal cleanup
        failure explicit. A failed STOP is not sent to the applied-command
        observer because it did not complete successfully.
        """
        owned = self._adapter.owned_notes
        sustain = self._adapter.owned_sustain_channels
        error = None
        try:
            if owned or sustain:
                command = stop_automated_notes()
                self._adapter.apply(command)
                self._notify_observer(command)
                self._log.info(
                    "playback cleanup (%s) released notes=%d sustain_channels=%d",
                    reason,
                    len(owned),
                    len(sustain),
                )
        except Exception as exc:
            error = str(exc)
            self._log.error("playback cleanup (%s) failed: %s", reason, exc)
        finally:
            # One post-attempt path publishes the adapter's actual remaining
            # ownership, including the result of a partially successful STOP.
            self._notify_ownership_observer()
        return error

    def _finish_stopped(self) -> None:
        # Freeze the song position at the stop moment (only PLAYING
        # still advances; PAUSED is already frozen by pause()).
        with self._lock:
            if self._state is PlaybackState.PLAYING and self._timeline is not None:
                now = self._clock.monotonic()
                self._origin_song = min(
                    self._origin_song
                    + (now - self._base_wall) * self._tempo_scale,
                    self._timeline.duration,
                )
                self._base_wall = now
        cleanup_error = self._release_owned("stop")
        with self._lock:
            if cleanup_error is None:
                self._state = PlaybackState.IDLE
            else:
                self._error = f"playback cleanup (stop) failed: {cleanup_error}"
                self._state = PlaybackState.FAILED
            self._thread = None
            self._done.set()
        if cleanup_error is None:
            self._log.info("playback stopped events_sent=%d", self._events_sent)

    def _finish_completed(self) -> None:
        # Stop wins over a concurrent natural completion: if a stop was
        # requested while the last event was being sent, the session is
        # recorded as stopped (IDLE), never as COMPLETED.
        with self._lock:
            stopped = self._stop_requested.is_set()
        # Defensive: a well-formed file owns nothing here; a malformed
        # one (missing final NOTE_OFF / pedal left down) must not leak
        # hanging notes into the terminal state.
        reason = "stop" if stopped else "completion"
        cleanup_error = self._release_owned(reason)
        with self._lock:
            if not stopped and self._timeline is not None:
                self._origin_song = self._timeline.duration
            if cleanup_error is None:
                self._state = (
                    PlaybackState.IDLE if stopped else PlaybackState.COMPLETED
                )
            else:
                self._error = f"playback cleanup ({reason}) failed: {cleanup_error}"
                self._state = PlaybackState.FAILED
            self._thread = None
            self._done.set()
        if cleanup_error is not None:
            return
        if stopped:
            self._log.info("playback stopped events_sent=%d", self._events_sent)
        else:
            self._log.info(
                "playback completed events_sent=%d duration=%.3fs",
                self._events_sent,
                self.duration,
            )

    def _fail(self, message: str) -> None:
        self._log.error("playback failed: %s", message)
        cleanup_error = self._release_owned("failure")
        if cleanup_error is not None:
            message = f"{message}; cleanup failed: {cleanup_error}"
        self._finish_failed(message)

    def _finish_failed(self, message: str) -> None:
        """Enter FAILED without attempting a second cleanup."""
        with self._lock:
            self._error = message
            self._state = PlaybackState.FAILED
            self._thread = None
            self._done.set()
