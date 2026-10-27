"""Practice state machine (PIANO-P015, ADR-PRACTICE-V1 D3).

Pure, in-memory, deterministic lesson engine. It owns NO MIDI surface,
NO StateStore, NO LED hardware, and NO threads: the manager feeds it one
canonical input event plus the authoritative post-event StateStore
pressed set, and it emits bounded display hooks (target/clear) that the
manager wires to the P014 LED adapter's non-blocking queue.

Completion semantics (exactly as documented in the ADR):

- SINGLE NOTE: the required note must be physically held in the
  authoritative pressed set.
- CHORD: ALL required notes must be simultaneously held; press order
  never matters; extra/wrong held notes are tolerated (they never
  advance a step by themselves and never prevent completion); a NOTE_ON
  for a non-target note only increments the observational
  ``incorrect_attempts`` counter.
- RE-ARM (repeated notes): when a step becomes active, every required
  note that is ALREADY held is disarmed. It must be physically released
  (a canonical NOTE_OFF) before it can satisfy the step — a fresh press
  is then required. Holding a note through between repeated targets can
  never satisfy the second target. The rule is per-note, so a repeated
  chord requires re-arming exactly its overlapping notes.

Any internal error while a session is WAITING fails the session
VISIBLY (``FAILED``): a correctness-critical note event is never
silently pretended processed.
"""

from __future__ import annotations

import logging
import time
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable

from piano_core.midi.events import MidiEventType, MidiInputEvent
from piano_core.practice.model import (
    LearningMode,
    LearningOutcome,
    PracticeAlreadyActiveError,
    PracticeInvalidStateError,
    PracticePlan,
    PracticeSession,
    PracticeSection,
    PracticeStaleCommandError,
    PracticeState,
    PracticeTarget,
)

__all__ = ["PracticeEngine"]

#: Bound for one stored error message (payload hygiene).
_ERROR_MAX = 200


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class PracticeEngine:
    """Single-session guided-practice engine (IDLE/WAITING/COMPLETED/FAILED)."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        on_target: Callable[[tuple[int, ...]], None] | None = None,
        on_clear: Callable[[], None] | None = None,
        on_release: Callable[[], None] | None = None,
        now_fn: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_result=None,
    ) -> None:
        self._log = logger or logging.getLogger("piano_core.practice.engine")
        self._on_target = on_target
        self._on_clear = on_clear
        self._on_release = on_release
        self._now = now_fn or _utc_now
        self._clock = clock
        self._flow = None
        self._flow_final = None
        self._started_monotonic = None
        self._scope_section_id = None
        self._full_lesson = False
        self._on_result = on_result
        self._lock = threading.Lock()
        self._state = PracticeState.IDLE
        self._plan: PracticePlan | None = None
        self._song_id: str | None = None
        self._title: str | None = None
        self._step_index = 0
        self._completed_steps = 0
        self._incorrect_attempts = 0
        self._armed: frozenset[int] = frozenset()
        self._error: str | None = None
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._session_id: str | None = None
        self._mode = LearningMode.WAIT_FOR_NOTE
        self._section: PracticeSection | None = None
        self._accepted_notes: tuple[int, ...] = ()
        self._latest_outcome = LearningOutcome.NONE
        self._target_repetition_streak = 0
        self._required_repetitions = 1
        self._successful_repetitions = 0
        self._failed_repetitions = 0
        self._generation = 0
        self._version = 0
        self._updated_at: str | None = None
        self._input_available = True
        self._hand_selection: str | None = None

    # ------------------------------------------------------------------
    # session control
    # ------------------------------------------------------------------
    def start_session(
        self,
        plan: PracticePlan,
        song_id: str,
        title: str | None,
        pressed_now: frozenset[int] = frozenset(),
        *,
        mode: LearningMode = LearningMode.WAIT_FOR_NOTE,
        section: PracticeSection | None = None,
        required_repetitions: int = 1,
        session_id: str | None = None,
        input_available: bool = True,
        hand_selection: str | None = None,
        speed: float = 1.0,
        scope_section_id: str | None = None,
        full_lesson: bool = False,
    ) -> None:
        """Activate the first target of ``plan`` (single-active, fail closed).

        ``pressed_now`` is the authoritative currently-held set from the
        StateStore: required notes already held at entry are DISARMED and
        must be released before they can satisfy the first step.

        ``input_available`` mirrors the authoritative StateStore
        connection boundary at start. When it is ``False`` the whole
        first target is disarmed (held truth cannot be trusted) and the
        session reports INPUT_UNAVAILABLE until a reconnect transition.
        """
        if not isinstance(plan, PracticePlan):
            raise TypeError(
                f"start_session requires a PracticePlan, "
                f"got {type(plan).__name__}"
            )
        if not isinstance(song_id, str) or not song_id.strip():
            raise ValueError("song_id must be a non-empty string")
        if plan.total_steps < 1:
            raise ValueError("cannot start a session with an empty practice plan")
        if not isinstance(mode, LearningMode):
            raise TypeError("mode must be a LearningMode")
        if section is not None and not isinstance(section, PracticeSection):
            raise TypeError("section must be a PracticeSection or None")
        if (
            not isinstance(required_repetitions, int)
            or isinstance(required_repetitions, bool)
            or not 1 <= required_repetitions <= 16
        ):
            raise ValueError("required_repetitions must be an integer in [1..16]")
        if mode is not LearningMode.REPEAT_UNTIL_CORRECT:
            required_repetitions = 1
        if session_id is not None and (
            not isinstance(session_id, str) or not session_id.strip()
        ):
            raise ValueError("session_id must be a non-empty string or None")
        if not isinstance(input_available, bool):
            raise TypeError("input_available must be a bool")
        if hand_selection is not None and hand_selection not in ("RH", "LH"):
            raise ValueError(
                "hand_selection must be 'RH', 'LH' or None"
            )
        first = plan.targets[0]
        with self._lock:
            if self._state is PracticeState.WAITING:
                raise PracticeAlreadyActiveError(
                    "a practice session is already active; stop it first"
                )
            from piano_core.practice.flow import FlowEvaluation
            flow = FlowEvaluation(plan, self._clock(), pressed_now, speed=speed) if mode is LearningMode.FLOW else None
            self._flow = flow
            if flow is not None and not input_available:
                flow.pause(self._clock())
            self._flow_final = None
            self._plan = plan
            self._song_id = song_id
            self._title = title
            self._step_index = 0
            self._completed_steps = 0
            self._incorrect_attempts = 0
            self._input_available = input_available
            if input_available:
                self._armed = frozenset(
                    note for note in first.notes if note not in pressed_now
                )
                self._latest_outcome = LearningOutcome.NONE
            else:
                # Authoritative held-key truth is unavailable: no required
                # note can be trusted as freshly pressed (U4-F1).
                self._armed = frozenset()
                self._latest_outcome = LearningOutcome.INPUT_UNAVAILABLE
            self._error = None
            self._started_monotonic = self._clock()
            self._scope_section_id = scope_section_id
            self._full_lesson = bool(
                full_lesson
                and mode in (LearningMode.WAIT_FOR_NOTE, LearningMode.FLOW)
                and hand_selection is None
                and section is None
                and scope_section_id is None
            )
            self._started_at = self._now()
            self._ended_at = None
            self._session_id = session_id or uuid.uuid4().hex
            self._mode = mode
            self._section = section
            self._hand_selection = hand_selection
            self._accepted_notes = ()
            self._target_repetition_streak = 0
            self._required_repetitions = required_repetitions
            self._successful_repetitions = 0
            self._failed_repetitions = 0
            self._generation += 1
            self._version = 1
            self._updated_at = self._started_at
            self._state = PracticeState.WAITING
        self._log.info(
            "practice session started song_id=%s steps=%d first=%s",
            song_id,
            plan.total_steps,
            "+".join(first.names),
        )
        if flow is not None and not input_available:
            self._emit_clear()
        else:
            self._emit_target(first.notes)

    def observe_event(
        self, event: MidiInputEvent, pressed_now: frozenset[int]
    ) -> None:
        """Reduce one canonical physical input event (read-only observer).

        Only physical NOTE_ON/NOTE_OFF participate; sustain is ignored.
        ``pressed_now`` is the authoritative StateStore pressed set AFTER
        the event was reduced. Never advances on wrong notes; never
        advances from anything except a fresh physical press (re-armed
        when required).

        U4-F1 durable barrier: while authoritative physical input is
        unavailable (after a disconnect boundary), NO observation may
        grade, re-arm a target, advance ``accepted_notes`` or repetition
        state, count an incorrect attempt, or complete a target. Stale
        NOTE_ON/NOTE_OFF observations that still arrive after the
        disconnect can never satisfy a target.
        """
        if not isinstance(event, MidiInputEvent):
            raise TypeError(
                f"observe_event requires a canonical MidiInputEvent, "
                f"got {type(event).__name__}"
            )
        if not isinstance(pressed_now, frozenset):
            raise TypeError(
                f"pressed_now must be a frozenset, "
                f"got {type(pressed_now).__name__}"
            )
        emit_target: tuple[int, ...] | None = None
        emit_clear = False
        emit_release = False
        flow_observed = False
        with self._lock:
            if self._state is not PracticeState.WAITING or self._plan is None:
                return
            if not self._input_available:
                return
            # Strategy selection and mutation share the session lock. A Stop /
            # Start boundary cannot route a new Flow through the old Wait path.
            if self._mode is LearningMode.FLOW:
                self._flow.observe(event, pressed_now, self._clock())
                self._latest_outcome = LearningOutcome(self._flow.latest)
                self._incorrect_attempts = self._flow.wrong
                self._touch_locked()
                flow_observed = True
            else:
                target = self._plan.targets[self._step_index]
                if event.type is MidiEventType.NOTE_ON:
                    note = event.note
                    if note not in target.notes:
                        # Wrong note: learner-friendly deterministic policy —
                        # it never advances the step by itself.
                        self._incorrect_attempts += 1
                        self._latest_outcome = LearningOutcome.INCORRECT
                        self._accepted_notes = tuple(
                            required
                            for required in target.notes
                            if required in pressed_now and required in self._armed
                        )
                        if (
                            self._mode is LearningMode.REPEAT_UNTIL_CORRECT
                            and self._target_repetition_streak > 0
                        ):
                            self._failed_repetitions += 1
                            self._target_repetition_streak = 0
                        self._touch_locked()
                    elif note in self._armed and all(
                        required in pressed_now and required in self._armed
                        for required in target.notes
                    ):
                        self._accepted_notes = target.notes
                        self._latest_outcome = LearningOutcome.CORRECT
                        if self._mode is LearningMode.REPEAT_UNTIL_CORRECT:
                            self._successful_repetitions += 1
                            self._target_repetition_streak += 1
                            if self._target_repetition_streak < self._required_repetitions:
                                # The same target remains active and every required
                                # held note must be released before another success.
                                # Held notes are disarmed here, so their accepted
                                # contribution to the NEXT attempt is empty until
                                # they are physically released and re-pressed.
                                self._armed = frozenset(
                                    required
                                    for required in target.notes
                                    if required not in pressed_now
                                )
                                self._accepted_notes = ()
                                self._touch_locked()
                            else:
                                self._target_repetition_streak = 0
                                emit_target, emit_clear, emit_release = (
                                    self._advance_locked(pressed_now)
                                )
                        else:
                            emit_target, emit_clear, emit_release = self._advance_locked(
                                pressed_now
                            )
                    elif note in target.notes:
                        # A required press that does not complete the target:
                        # refresh the accepted contribution only when the
                        # serialized truth actually changed (U4-F4).
                        accepted = tuple(
                            required
                            for required in target.notes
                            if required in pressed_now and required in self._armed
                        )
                        if accepted != self._accepted_notes:
                            self._accepted_notes = accepted
                            self._touch_locked()
                elif event.type is MidiEventType.NOTE_OFF:
                    if event.note in target.notes:
                        # Physical release of a required note always re-arms it
                        # for this step and ends its accepted contribution to
                        # the current attempt (U4-F4). The version advances only
                        # when the serialized accepted_notes actually change.
                        if event.note not in self._armed:
                            self._armed = self._armed | {event.note}
                        accepted = tuple(
                            required
                            for required in target.notes
                            if required in pressed_now and required in self._armed
                        )
                        if accepted != self._accepted_notes:
                            self._accepted_notes = accepted
                            self._touch_locked()
                # SUSTAIN: ignored — the pedal never advances a lesson.
        if flow_observed:
            self.tick_flow()
            return
        if emit_clear:
            self._emit_clear()
        if emit_release:
            self._notify_release()
        if emit_target is not None:
            self._emit_target(emit_target)

    def _capture_result_locked(self):
        if self._on_result is None or self._session_id is None:
            return
        flow = self._flow.snapshot(self._clock(), include_evidence=True) if self._flow is not None else None
        session = PracticeSession(
            state=self._state, available=True, song_id=self._song_id,
            title=self._title, total_steps=self._plan.total_steps if self._plan else 0,
            completed_steps=self._completed_steps, incorrect_attempts=self._incorrect_attempts,
            started_at=self._started_at, ended_at=self._ended_at, session_id=self._session_id,
            mode=self._mode, hand_selection=self._hand_selection, flow=flow,
            section=self._section, scope_section_id=self._scope_section_id,
            full_lesson=self._full_lesson,
            elapsed_seconds=max(0.0, self._clock() - self._started_monotonic),
        )
        self._on_result(session)

    def tick_flow(self):
        notes = None
        terminal = False
        with self._lock:
            if self._state is not PracticeState.WAITING or self._flow is None:
                return
            flow = self._flow
            old = (len(flow.results), flow.target_index)
            terminal = flow.tick(self._clock())
            self._completed_steps = flow.correct
            self._latest_outcome = LearningOutcome(flow.latest) if self._input_available else LearningOutcome.INPUT_UNAVAILABLE
            index = min(flow.target_index, self._plan.total_steps - 1)
            if index != self._step_index:
                self._step_index = index
                notes = self._plan.targets[index].notes
            self._accepted_notes = tuple(sorted(flow.attempts.get(index, {})))
            if terminal:
                self._state = PracticeState.COMPLETED
                self._ended_at = self._now()
                self._flow_final = flow.snapshot(self._clock())
                self._capture_result_locked()
            if terminal or old != (len(flow.results), flow.target_index):
                self._touch_locked()
            # LED hooks enqueue only. Keep them ordered with the session
            # transaction; release ownership outside to avoid lock inversion.
            if terminal:
                self._emit_clear()
            elif notes is not None and flow.paused_at is None:
                self._emit_target(notes)
        if terminal:
            self._notify_release()

    def flow_pause(self, paused, pressed_now, *, expected_session_id, expected_version):
        with self._lock:
            self._require_guard_locked(expected_session_id, expected_version, required=True)
            if self._state is not PracticeState.WAITING or self._flow is None:
                raise PracticeInvalidStateError("pause/resume requires active Flow")
            if paused:
                self._flow.pause(self._clock())
            else:
                if not self._input_available:
                    raise PracticeInvalidStateError("MIDI input is unavailable")
                self._flow.resume(self._clock(), pressed_now)
            self._accepted_notes = ()
            self._touch_locked()
            if paused:
                self._emit_clear()
            else:
                self._emit_target(self._plan.targets[self._step_index].notes)

    def stop(self) -> bool:
        """Stop/reset the session. Idempotent; always lands in IDLE.

        Returns True when an observable session (WAITING/COMPLETED/
        FAILED) was reset. The practice LED surface is cleared whenever
        the display may still hold a target.
        """
        with self._lock:
            stopped, had_display = self._stop_locked()
        self._log.info("practice session stopped")
        if had_display:
            self._emit_clear()
        return stopped

    def guarded_stop(
        self,
        *,
        expected_session_id: str | None = None,
        expected_version: int | None = None,
    ) -> bool:
        """Atomic guarded stop (U4-F2).

        The expected session/version check and the stop mutation share
        ONE critical section, so a physical input transition can never
        slip between validation and mutation.
        """
        with self._lock:
            self._require_guard_locked(
                expected_session_id, expected_version, required=False
            )
            stopped, had_display = self._stop_locked()
        self._log.info("practice session stopped")
        if had_display:
            self._emit_clear()
        return stopped

    def _stop_locked(self) -> tuple[bool, bool]:
        """Stop/reset internals (engine lock held).

        Returns ``(stopped, had_display)``: ``stopped`` is True when an
        observable session (WAITING/COMPLETED/FAILED) was reset;
        ``had_display`` is True when the LED surface may still hold a
        target (WAITING) and must be cleared after the lock is released.
        """
        if self._state is PracticeState.IDLE:
            return False, False
        had_display = self._state is PracticeState.WAITING
        if self._state is PracticeState.WAITING:
            self._capture_result_locked()
        self._state = PracticeState.IDLE
        self._plan = None
        self._song_id = None
        self._title = None
        self._step_index = 0
        self._completed_steps = 0
        self._incorrect_attempts = 0
        self._armed = frozenset()
        self._error = None
        self._started_at = None
        self._ended_at = None
        self._session_id = None
        self._mode = LearningMode.WAIT_FOR_NOTE
        self._section = None
        self._hand_selection = None
        self._accepted_notes = ()
        self._latest_outcome = LearningOutcome.NONE
        self._target_repetition_streak = 0
        self._required_repetitions = 1
        self._successful_repetitions = 0
        self._failed_repetitions = 0
        self._version += 1
        self._updated_at = self._now()
        return True, had_display

    def fail(self, message: str) -> None:
        """Fail the WAITING session visibly (never silently swallow)."""
        with self._lock:
            if self._state is not PracticeState.WAITING:
                return
            self._state = PracticeState.FAILED
            self._error = str(message)[:_ERROR_MAX]
            self._ended_at = self._now()
            if self._flow is not None:
                self._flow_final = self._flow.snapshot(self._clock())
            self._armed = frozenset()
            self._accepted_notes = ()
            self._version += 1
            self._updated_at = self._ended_at
            self._capture_result_locked()
        self._log.error("practice session failed: %s", message)
        self._emit_clear()
        self._notify_release()

    def observe_input_availability(
        self, connected: bool, pressed_now: frozenset[int]
    ) -> None:
        """Apply the canonical connection/reset boundary to an active attempt.

        This transition is the ONLY place the availability barrier flips
        (U4-F1). Disconnect never completes or fails a target: it marks
        input unavailable, removes all accepted attempt state, disarms
        the target and reports INPUT_UNAVAILABLE, while completed
        progress is preserved. Reconnect re-arms only notes not already
        held in authoritative StateStore truth, so stale held state can
        never advance the lesson — held target notes require a physical
        release and a fresh press before they can satisfy the target.
        """
        if not isinstance(connected, bool):
            raise TypeError("connected must be a bool")
        if not isinstance(pressed_now, frozenset):
            raise TypeError("pressed_now must be a frozenset")
        with self._lock:
            self._input_available = connected
            if self._state is not PracticeState.WAITING or self._plan is None:
                return
            if self._flow is not None:
                if not connected:
                    self._flow.pause(self._clock())
                self._flow.held = pressed_now
            target = self._plan.targets[self._step_index]
            self._accepted_notes = ()
            self._target_repetition_streak = 0
            if connected:
                self._armed = frozenset(
                    note for note in target.notes if note not in pressed_now
                )
                self._latest_outcome = LearningOutcome.NONE
            else:
                self._armed = frozenset()
                self._latest_outcome = LearningOutcome.INPUT_UNAVAILABLE
            self._touch_locked()

    def restart_target(self, pressed_now: frozenset[int]) -> None:
        """Reset the current attempt without changing session progress."""
        if not isinstance(pressed_now, frozenset):
            raise TypeError("pressed_now must be a frozenset")
        with self._lock:
            notes = self._restart_target_locked(pressed_now)
        self._emit_target(notes)

    def guarded_restart_target(
        self,
        pressed_now: frozenset[int],
        *,
        expected_session_id: str,
        expected_version: int,
    ) -> None:
        """Atomic guarded target restart (U4-F2): guard + mutation in ONE
        engine critical section, atomic against physical progression."""
        if not isinstance(pressed_now, frozenset):
            raise TypeError("pressed_now must be a frozenset")
        with self._lock:
            self._require_guard_locked(
                expected_session_id, expected_version, required=True
            )
            notes = self._restart_target_locked(pressed_now)
        self._emit_target(notes)

    def _restart_target_locked(self, pressed_now: frozenset[int]) -> tuple[int, ...]:
        """Restart internals (engine lock held); returns target notes."""
        if self._state is not PracticeState.WAITING or self._plan is None:
            raise PracticeInvalidStateError(
                "restart target requires an active learning session"
            )
        if self._flow is not None:
            raise PracticeInvalidStateError("Flow restarts require a fresh session")
        target = self._plan.targets[self._step_index]
        self._armed = frozenset(
            note for note in target.notes if note not in pressed_now
        )
        self._accepted_notes = ()
        self._latest_outcome = LearningOutcome.NONE
        self._target_repetition_streak = 0
        self._touch_locked()
        return target.notes

    def restart_section(self, pressed_now: frozenset[int]) -> None:
        """Restart the current plan from its first target deterministically."""
        if not isinstance(pressed_now, frozenset):
            raise TypeError("pressed_now must be a frozenset")
        with self._lock:
            notes = self._restart_section_locked(pressed_now)
        self._emit_target(notes)

    def guarded_restart_section(
        self,
        pressed_now: frozenset[int],
        *,
        expected_session_id: str,
        expected_version: int,
    ) -> None:
        """Atomic guarded section restart (U4-F2): guard + mutation in ONE
        engine critical section, atomic against physical progression."""
        if not isinstance(pressed_now, frozenset):
            raise TypeError("pressed_now must be a frozenset")
        with self._lock:
            self._require_guard_locked(
                expected_session_id, expected_version, required=True
            )
            notes = self._restart_section_locked(pressed_now)
        self._emit_target(notes)

    def _restart_section_locked(self, pressed_now: frozenset[int]) -> tuple[int, ...]:
        """Restart-section internals (engine lock held); returns notes."""
        if self._state is not PracticeState.WAITING or self._plan is None:
            raise PracticeInvalidStateError(
                "restart section requires an active learning session"
            )
        if self._flow is not None:
            raise PracticeInvalidStateError("Flow restarts require a fresh session")
        self._step_index = 0
        self._completed_steps = 0
        self._incorrect_attempts = 0
        self._successful_repetitions = 0
        self._failed_repetitions = 0
        self._target_repetition_streak = 0
        target = self._plan.targets[0]
        self._armed = frozenset(
            note for note in target.notes if note not in pressed_now
        )
        self._accepted_notes = ()
        self._latest_outcome = LearningOutcome.NONE
        self._touch_locked()
        return target.notes

    # ------------------------------------------------------------------
    # guarded-mutation primitives (U4-F2)
    # ------------------------------------------------------------------
    def _require_guard_locked(
        self,
        expected_session_id: str | None,
        expected_version: int | None,
        *,
        required: bool,
    ) -> None:
        """Validate the stale-command guard against current engine truth.

        MUST run in the SAME engine critical section as the mutation it
        authorizes (U4-F2): a guard validated at version N can never
        authorize a mutation after physical input advanced the state.
        """
        if required and (expected_session_id is None or expected_version is None):
            raise PracticeStaleCommandError(
                "expected_session_id and expected_version are required"
            )
        if expected_session_id is None and expected_version is None:
            return
        if not isinstance(expected_session_id, str) or not expected_session_id:
            raise PracticeStaleCommandError("expected_session_id is invalid")
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise PracticeStaleCommandError("expected_version is invalid")
        if expected_session_id != self._session_id or expected_version != self._version:
            raise PracticeStaleCommandError(
                "stale learning command; refresh authoritative state"
            )

    # ------------------------------------------------------------------
    # observability
    # ------------------------------------------------------------------
    @property
    def state(self) -> PracticeState:
        """Current typed practice state."""
        with self._lock:
            return self._state

    def snapshot(
        self, *, available: bool, midi_connection: str | None
    ) -> PracticeSession:
        """Build the immutable API/UI snapshot (manager injects context)."""
        with self._lock:
            state = self._state
            plan = self._plan
            target: PracticeTarget | None = None
            step_index: int | None = None
            if state is PracticeState.WAITING and plan is not None:
                step_index = self._step_index
                target = plan.targets[step_index]
            return PracticeSession(
                state=state,
                flow=(self._flow_final or self._flow.snapshot(self._clock())) if self._flow is not None and state is not PracticeState.IDLE else None,
                available=available,
                song_id=self._song_id if state is not PracticeState.IDLE else None,
                title=self._title if state is not PracticeState.IDLE else None,
                step_index=step_index,
                total_steps=plan.total_steps if plan is not None else None,
                completed_steps=(
                    self._completed_steps
                    if state is not PracticeState.IDLE
                    else 0
                ),
                incorrect_attempts=(
                    self._incorrect_attempts
                    if state is not PracticeState.IDLE
                    else 0
                ),
                current_target=target,
                started_at=self._started_at,
                ended_at=self._ended_at,
                error=self._error,
                midi_connection=midi_connection,
                session_id=self._session_id,
                mode=self._mode,
                hand_selection=self._hand_selection,
                section=self._section,
                target_tempo=None,
                accepted_notes=self._accepted_notes,
                latest_outcome=self._latest_outcome,
                current_repetition=self._target_repetition_streak,
                required_repetitions=self._required_repetitions,
                successful_repetitions=self._successful_repetitions,
                failed_repetitions=self._failed_repetitions,
                generation=self._generation,
                version=self._version,
                updated_at=self._updated_at,
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _advance_locked(
        self, pressed_now: frozenset[int]
    ) -> tuple[tuple[int, ...] | None, bool, bool]:
        """Complete the current step (lock held).

        Returns ``(next_target_notes_or_None, clear_display,
        release_ownership)``. On final completion the display clears and
        the manager is notified to release appliance activity ownership
        (F-R1: the WAITING -> COMPLETED transition itself must free the
        lock — no Stop call required); otherwise the next target is
        emitted immediately (LEDs never wait for browser polling).
        """
        self._completed_steps += 1
        plan = self._plan
        assert plan is not None  # WAITING invariant
        if self._step_index + 1 >= plan.total_steps:
            self._state = PracticeState.COMPLETED
            self._ended_at = self._now()
            self._armed = frozenset()
            self._version += 1
            self._updated_at = self._ended_at
            self._capture_result_locked()
            self._log.info(
                "practice session completed steps=%d incorrect=%d",
                self._completed_steps,
                self._incorrect_attempts,
            )
            return None, True, True
        self._step_index += 1
        target = plan.targets[self._step_index]
        self._accepted_notes = ()
        self._armed = frozenset(
            note for note in target.notes if note not in pressed_now
        )
        self._touch_locked()
        self._log.info(
            "practice step %d/%d target=%s",
            self._step_index + 1,
            plan.total_steps,
            "+".join(target.names),
        )
        return target.notes, False, False

    def _touch_locked(self) -> None:
        """Advance the learning snapshot revision (engine lock held)."""
        self._version += 1
        self._updated_at = self._now()

    def _emit_target(self, notes: tuple[int, ...]) -> None:
        if self._on_target is None:
            return
        try:
            self._on_target(notes)
        except Exception:  # display problems degrade, never fail a lesson
            self._log.exception("practice target display hook failed")

    def _emit_clear(self) -> None:
        if self._on_clear is None:
            return
        try:
            self._on_clear()
        except Exception:  # display problems degrade, never fail a lesson
            self._log.exception("practice clear display hook failed")

    def _notify_release(self) -> None:
        """F-R1: report the terminal transition to the owner (manager).

        Fires exactly once per WAITING -> COMPLETED / WAITING -> FAILED
        transition and never on stop() (the manager releases ownership
        explicitly there, so a stop after a terminal transition is a
        no-op). Never raises: an ownership-release problem degrades the
        post-session state, never the lesson.
        """
        if self._on_release is None:
            return
        try:
            self._on_release()
        except Exception:
            self._log.exception("practice release-notification hook failed")
