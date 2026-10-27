"""Practice manager: composition and appliance ownership (PIANO-P015).

The single control/observation point for guided practice inside the
running application. It owns NO MIDI surface. Wait is input-driven; Flow uses a monotonic
heartbeat, with a separate result writer:

    song_id -> LibraryService lookup -> validated resolve -> canonical
    P009 load_timeline -> practice plan derivation -> appliance activity
    ownership -> WAITING on the engine

Ownership (ADR-PRACTICE-V1 D6): guided practice participates in the
EXISTING appliance-wide advisory lock (``piano_core.playback.advisory``)
whose meaning is generalized to "at most ONE appliance activity
{automated playback, guided practice} at a time". The lock path/name is
deliberately unchanged. Because it is an ``flock`` on an open
descriptor, a crashed process can never permanently lock the appliance.

Input observation (D4): the Application calls
:meth:`observe_input_event` on the canonical input path AFTER the
StateStore reduced the event. The manager reads the authoritative
post-event snapshot (the StateStore is never touched for writes) and
delegates to the engine. The whole path is bounded and fast (pure
in-memory transitions; display changes enqueue into the LED adapter's
bounded drop-oldest queue); ANY internal error fails the session
visibly instead of pretending a note was processed.

Silent practice (D7): this manager holds NO reference to the MIDI
output adapter and never emits audio — guided practice never sounds the
song, and stopping practice is NOT a P009 STOP/PANIC.
"""

from __future__ import annotations

import logging
import threading
import queue
from pathlib import Path
from typing import Callable

from piano_core.midi.events import MidiEventType
from piano_core.playback.advisory import (
    PlaybackLock,
    acquire_playback_lock,
    default_lock_path,
)
from piano_core.playback.timeline import load_timeline
from piano_core.practice.engine import PracticeEngine
from piano_core.practice.model import (
    LearningMode,
    PracticeActivityConflictError,
    PracticeAlreadyActiveError,
    PracticeNotAvailableError,
    PracticePlanError,
    PracticeSession,
    PracticeSection,
    PracticeState,
)
from piano_core.practice.plan import build_practice_plan, select_practice_section

__all__ = ["PracticeManager"]

#: Engine states from which a new session may start (ADR D3).
_STARTABLE = (PracticeState.IDLE, PracticeState.COMPLETED, PracticeState.FAILED)


class PracticeManager:
    """Single-session guided practice over the canonical subsystems."""

    def __init__(
        self,
        library,
        state_store,
        *,
        lock_path: str | None = None,
        led=None,
        timeline_loader: Callable[..., object] | None = None,
        plan_builder: Callable[[object], object] | None = None,
        conflict_probe: Callable[[], str | None] | None = None,
        logger: logging.Logger | None = None,
        session_recorder=None,
    ) -> None:
        from piano_core.state import StateStore

        if not isinstance(state_store, StateStore):
            raise TypeError(
                f"state_store must be a StateStore, got "
                f"{type(state_store).__name__}"
            )
        self._library = library
        self._store = state_store
        self._lock_path = lock_path or default_lock_path()
        self._led = led
        self._load_timeline = timeline_loader or load_timeline
        self._build_plan = plan_builder or build_practice_plan
        # Optional application-provided probe: returns a human reason
        # when the service-side playback session is active, else None.
        self._conflict_probe = conflict_probe
        self._log = logger or logging.getLogger("piano_core.practice.manager")
        self._lock = threading.RLock()
        self._advisory: PlaybackLock | None = None
        self._recorder = session_recorder
        self.records_sessions = session_recorder is not None
        self._records = queue.Queue(maxsize=32)
        self._worker_stop = threading.Event()
        self._worker = None
        self._persist_worker = None
        self.persistence_error = None
        self._failed_results = []
        self._records_lock = threading.Lock()
        self._engine = PracticeEngine(
            logger=logging.getLogger("piano_core.practice.engine"),
            on_target=self._display_target,
            on_clear=self._display_clear,
            on_release=self._on_engine_release,
            on_result=self._queue_record,
        )

    # ------------------------------------------------------------------
    # observability
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Guided practice can START (the canonical library is ready).

        An unavailable library degrades start only: an already-running
        session keeps progressing on physical input, and the state
        remains honestly observable.
        """
        return self._library is not None

    def snapshot(self) -> PracticeSession:
        """Current practice snapshot incl. canonical MIDI input state."""
        return self._engine.snapshot(
            available=self.available,
            midi_connection=self._store.snapshot().midi_connection.value,
        )

    # ------------------------------------------------------------------
    # control API
    # ------------------------------------------------------------------
    def start(
        self,
        song_id: str,
        *,
        mode: LearningMode = LearningMode.WAIT_FOR_NOTE,
        section: PracticeSection | None = None,
        required_repetitions: int = 1,
        plan_override: "object | None" = None,
        hand_selection: str | None = None,
        speed: float = 1.0,
        scope_section_id: str | None = None,
        full_lesson: bool = False,
    ) -> PracticeSession:
        """Resolve, project, own, and start one guided practice session.

        M5 (ADR-PACKAGE-PRACTICE-V1): ``plan_override`` supplies a
        package-derived plan (imported Practice Package with hand
        evidence). When it is given the canonical timeline load and the
        default plan builder are skipped — the package's own hash-bound
        practice projection IS the canonical material for that session,
        and the caller has validated its section bounds. Ownership,
        conflict probing, guards and engine semantics are identical.
        """
        if not isinstance(song_id, str) or not song_id.strip():
            raise ValueError("song_id must be a non-empty string")
        if not isinstance(mode, LearningMode):
            raise TypeError("mode must be a LearningMode")
        if mode is LearningMode.SECTION_PRACTICE and section is None:
            raise ValueError("SECTION_PRACTICE requires a section")
        if mode is not LearningMode.SECTION_PRACTICE and section is not None:
            raise ValueError("section is only valid for SECTION_PRACTICE")
        if (
            not isinstance(required_repetitions, int)
            or isinstance(required_repetitions, bool)
            or not 1 <= required_repetitions <= 16
        ):
            raise ValueError("required_repetitions must be an integer in [1..16]")
        if mode is not LearningMode.REPEAT_UNTIL_CORRECT and required_repetitions != 1:
            raise ValueError(
                "required_repetitions is only configurable for "
                "REPEAT_UNTIL_CORRECT"
            )
        song_id = song_id.strip()
        with self._lock:
            if self.persistence_error or self._records.full():
                raise PracticeNotAvailableError("progress persistence unavailable")
            if not self.available:
                raise PracticeNotAvailableError(
                    "guided practice is unavailable (library is not ready)"
                )
            if self._engine.state is PracticeState.WAITING:
                raise PracticeAlreadyActiveError(
                    "a practice session is already active; stop it first"
                )
            self._require_activity_free()
            if plan_override is not None:
                record = self._library.get(song_id)
                if record is None:
                    from piano_core.library.model import LibraryPathError

                    raise LibraryPathError(f"unknown song_id: {song_id}")
                plan = plan_override
                if section is not None:
                    if section.end_seconds > plan.duration_seconds:
                        raise ValueError("section end_seconds exceeds canonical song duration")
                    plan = select_practice_section(plan, section)
            else:
                record = self._library.get(song_id)
                if record is None:
                    from piano_core.library.model import LibraryPathError

                    raise LibraryPathError(f"unknown song_id: {song_id}")
                path: Path = self._library.resolve(song_id)
                timeline = self._load_timeline(path)
                plan = self._build_plan(timeline)
                if section is not None:
                    if section.end_seconds > timeline.duration:
                        raise ValueError(
                            "section end_seconds exceeds canonical song duration"
                        )
                    plan = select_practice_section(plan, section)
            if plan.total_steps < 1:
                raise PracticePlanError(
                    "song contains no playable keyboard targets in the "
                    f"{21}..{108} range (nothing to practice)"
                )
            self._acquire_advisory()
            try:
                snapshot = self._store.snapshot()
                pressed = frozenset(snapshot.pressed_notes)
                from piano_core.state import MidiConnectionState

                self._engine.start_session(
                    plan,
                    song_id,
                    record.title or record.filename,
                    pressed,
                    mode=mode,
                    section=section,
                    required_repetitions=required_repetitions,
                    input_available=(
                        snapshot.midi_connection is MidiConnectionState.CONNECTED
                    ),
                    hand_selection=hand_selection,
                    speed=speed,
                    scope_section_id=scope_section_id,
                    full_lesson=full_lesson,
                )
            except Exception:
                self._release_advisory()
                raise
            if mode is LearningMode.FLOW or self._recorder is not None:
                self._ensure_worker()
            return self.snapshot()


    def stop(
        self,
        *,
        expected_session_id: str | None = None,
        expected_version: int | None = None,
    ) -> tuple[PracticeSession, bool]:
        """Stop/reset the session; release ownership. Idempotent.

        This is NOT a P009 STOP/PANIC: practice owns no sounding notes
        and no MIDI output is ever touched. The stop only clears the
        practice LED surface and the appliance activity ownership.

        When a guard is supplied, the expected session/version check and
        the stop mutation are ONE atomic engine critical section
        (U4-F2), so a physical input transition can never validate at
        version N and mutate after the state moved to N+1.
        """
        with self._lock:
            stopped = self._engine.guarded_stop(
                expected_session_id=expected_session_id,
                expected_version=expected_version,
            )
            self._release_advisory()
            return self.snapshot(), stopped

    def restart_target(
        self, *, expected_session_id: str, expected_version: int
    ) -> PracticeSession:
        """Restart the active target under an exact session/version guard.

        Guard validation and mutation are atomic against physical
        input progression (U4-F2).
        """
        with self._lock:
            pressed = frozenset(self._store.snapshot().pressed_notes)
            self._engine.guarded_restart_target(
                pressed,
                expected_session_id=expected_session_id,
                expected_version=expected_version,
            )
            return self.snapshot()

    def restart_section(
        self, *, expected_session_id: str, expected_version: int
    ) -> PracticeSession:
        """Restart the active plan/section under an exact revision guard.

        Guard validation and mutation are atomic against physical
        input progression (U4-F2).
        """
        with self._lock:
            pressed = frozenset(self._store.snapshot().pressed_notes)
            self._engine.guarded_restart_section(
                pressed,
                expected_session_id=expected_session_id,
                expected_version=expected_version,
            )
            return self.snapshot()

    def shutdown(self) -> None:
        """Application-shutdown cleanup: stop + release. Never raises."""
        try:
            self.stop()
            self._worker_stop.set()
            if self._worker is not None:
                self._worker.join(timeout=3)
                if self._worker.is_alive():
                    self._log.error("practice heartbeat did not stop")
            if self._persist_worker is not None:
                self._persist_worker.join(timeout=3)
                if self._persist_worker.is_alive():
                    self._log.error("practice result writer did not stop")
        except Exception as exc:  # cleanup must never break shutdown
            self._log.error("practice shutdown failed: %s", exc)

    def flow_pause(self, paused, *, expected_session_id, expected_version):
        with self._lock:
            self._engine.flow_pause(
                paused, frozenset(self._store.snapshot().pressed_notes),
                expected_session_id=expected_session_id, expected_version=expected_version,
            )
            return self.snapshot()

    def _queue_record(self, session):
        if self._recorder is not None and session.session_id:
            try:
                self._records.put_nowait(session)
            except queue.Full:
                with self._records_lock:
                    self._failed_results.append(session)
                    self.persistence_error = "progress queue is full; result retained for recovery"
                self._log.error(self.persistence_error)

    @property
    def pending_results(self):
        with self._records_lock:
            return self._records.unfinished_tasks + len(self._failed_results)

    def retry_failed_results(self):
        """Explicit recovery: same session ids make writes idempotent."""
        with self._records_lock:
            while self._failed_results and not self._records.full():
                self._records.put_nowait(self._failed_results.pop(0))
            if not self._failed_results:
                self.persistence_error = None
        self._ensure_worker()

    def _ensure_worker(self):
        if self._worker is None:
            self._worker = threading.Thread(target=self._heartbeat, name="piano-practice-clock", daemon=True)
            self._worker.start()
        if self._recorder is not None and self._persist_worker is None:
            self._persist_worker = threading.Thread(target=self._persist_results, name="piano-practice-results", daemon=True)
            self._persist_worker.start()

    def _heartbeat(self):
        while not self._worker_stop.is_set():
            try:
                with self._lock:
                    self._engine.tick_flow()
            except Exception as exc:
                self._engine.fail(f"Flow clock failure: {exc}")
            self._worker_stop.wait(0.01)

    def _persist_results(self):
        while not self._worker_stop.is_set() or not self._records.empty():
            try:
                session = self._records.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._recorder(session)
            except Exception as exc:
                with self._records_lock:
                    self._failed_results.append(session)
                    self.persistence_error = str(exc)
                self._log.exception("practice result persistence failed")
            finally:
                self._records.task_done()

    # ------------------------------------------------------------------
    # input observation (canonical path; bounded and fast)
    # ------------------------------------------------------------------
    def observe_input_event(self, event) -> None:
        """Observe one canonical physical input event (read-only).

        Called by the Application after the StateStore reduced the event
        (the StateStore remains THE physical-input authority and is
        never mutated here). Contract: never performs filesystem or
        network I/O, never blocks on LED hardware (display changes
        enqueue into the bounded LED worker queue), never raises into
        the MIDI reader thread. An internal error fails the session
        VISIBLY rather than silently pretending the note was processed.
        """
        try:
            if event.type not in (MidiEventType.NOTE_ON, MidiEventType.NOTE_OFF):
                return
            pressed = frozenset(self._store.snapshot().pressed_notes)
            self._engine.observe_event(event, pressed)
        except Exception as exc:
            self._log.exception("practice observation failed")
            try:
                self._engine.fail(f"internal practice error: {exc}")
            except Exception:  # absolute last resort: never raise upward
                self._log.exception("practice visible-failure path failed")

    def observe_connection_state(self, connection) -> None:
        """Observe the canonical StateStore connection/reset boundary."""
        from piano_core.state import MidiConnectionState

        try:
            if not isinstance(connection, MidiConnectionState):
                raise TypeError("connection must be a MidiConnectionState")
            snapshot = self._store.snapshot()
            with self._lock:
                self._engine.observe_input_availability(
                    connection is MidiConnectionState.CONNECTED,
                    frozenset(snapshot.pressed_notes),
                )
                if connection is not MidiConnectionState.CONNECTED and self.snapshot().flow is not None:
                    self._display_clear()
        except Exception as exc:
            self._log.exception("practice connection observation failed")
            try:
                self._engine.fail(f"internal practice connection error: {exc}")
            except Exception:
                self._log.exception("practice connection failure path failed")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _require_activity_free(self) -> None:
        """Fast service-side conflict probe (ADR D6).

        The authoritative cross-process check is the advisory flock
        acquired after plan derivation; this probe only fails fast when
        the service-side playback session is visibly active.
        """
        if self._conflict_probe is not None:
            reason = self._conflict_probe()
            if reason:
                raise PracticeActivityConflictError(
                    f"another appliance activity is active ({reason}); "
                    "stop it before starting guided practice"
                )

    def _acquire_advisory(self) -> PlaybackLock:
        try:
            self._advisory = acquire_playback_lock(self._lock_path, self._log)
        except OSError as exc:
            raise PracticeActivityConflictError(
                "another appliance activity already holds the playback "
                f"lock ({self._lock_path})"
            ) from exc
        return self._advisory

    def _release_advisory(self) -> None:
        if self._advisory is not None:
            try:
                self._advisory.release()
            finally:
                self._advisory = None

    def _on_engine_release(self) -> None:
        """Engine hook: the session reached COMPLETED/FAILED on its own.

        F-R1 (ADR-PRACTICE-V1 D6): appliance activity ownership is
        released AT the terminal transition itself, so automated playback
        or a fresh practice session can start immediately — no Stop call
        is required after a natural completion or failure. Idempotent
        with stop()/shutdown(): _release_advisory() is a no-op once
        ownership is already released.

        F-R1b hardening: the advisory cleanup runs under the manager
        RLock (matching the sibling PlaybackManager pattern), so a
        terminal notification racing a concurrent start()/stop() can
        neither double-enter the release nor tear the handle swap. The
        WAITING guard closes the orphan interleaving: if a NEW session
        already started (start() re-acquired ownership after this
        session terminated), the stale notification must leave the new
        session's advisory untouched. Lock ordering is safe: the engine
        fires this hook strictly AFTER releasing the engine lock, and
        the manager never calls into the engine while a hook is running,
        so no engine<->manager inversion exists.
        """
        with self._lock:
            if self._engine.state is PracticeState.WAITING:
                return
            self._log.info(
                "practice session terminal; appliance activity ownership released"
            )
            self._release_advisory()

    def _display_target(self, notes: tuple[int, ...]) -> None:
        """Engine hook: show the current target (never blocks/raises)."""
        if self._led is None:
            return
        try:
            self._led.practice_target(notes)
        except Exception:  # display problems degrade, never fail a lesson
            self._log.exception("practice LED target display failed")

    def _display_clear(self) -> None:
        """Engine hook: clear the practice LED surface (never blocks)."""
        if self._led is None:
            return
        try:
            self._led.practice_cleared()
        except Exception:  # display problems degrade, never fail a lesson
            self._log.exception("practice LED clear failed")
