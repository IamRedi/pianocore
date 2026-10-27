"""Typed practice model (PIANO-P015, ADR-PRACTICE-V1).

Frozen, validated records for the guided-practice subsystem plus its
typed error hierarchy. Practice owns NO MIDI surface of its own:

- a :class:`PracticeTarget` is one human play step derived from the
  canonical P009 timeline (a single note or one same-onset chord);
- a :class:`PracticePlan` is the deterministic ordered target sequence;
- a :class:`PracticeSession` is the immutable observable snapshot the
  API/UI read (never raw MIDI internals).

Timing is observational metadata only: practice is self-paced, so the
source onset is carried for future modes but never schedules anything.
"""

from __future__ import annotations

import enum
import math

__all__ = [
    "KEYBOARD_MAX",
    "KEYBOARD_MIN",
    "PERCUSSION_CHANNEL",
    "PracticeAlreadyActiveError",
    "PracticeActivityConflictError",
    "PracticeError",
    "PracticeInvalidStateError",
    "LearningMode",
    "LearningOutcome",
    "PracticeSection",
    "PracticeNotAvailableError",
    "PracticePlanError",
    "PracticeSession",
    "PracticeStaleCommandError",
    "PracticeState",
    "PracticeTarget",
    "note_name",
]

#: Inclusive range of the verified physical keyboard (88 keys, A0..C8).
KEYBOARD_MIN = 21
KEYBOARD_MAX = 108

#: Canonical 1-based GM percussion channel: never a learning target.
PERCUSSION_CHANNEL = 10

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(note: int) -> str:
    """MIDI note number -> human name (e.g. 60 -> ``C4``)."""
    return f"{_NOTE_NAMES[note % 12]}{note // 12 - 1}"


class PracticeError(RuntimeError):
    """A practice request violated its contract. Fail closed."""


class PracticeNotAvailableError(PracticeError):
    """Guided practice is unavailable (the canonical library is down)."""


class PracticeAlreadyActiveError(PracticeError):
    """A practice session is already WAITING; stop it before restarting."""


class PracticeActivityConflictError(PracticeError):
    """Another appliance activity (automated playback) holds ownership."""


class PracticePlanError(PracticeError):
    """The song yields no playable keyboard targets for guided practice."""


class PracticeInvalidStateError(PracticeError):
    """The requested control is invalid for the current practice state."""


class PracticeStaleCommandError(PracticeInvalidStateError):
    """A guarded learning command targets obsolete session truth."""


class PracticeState(enum.Enum):
    """Typed practice lifecycle states (ADR-PRACTICE-V1 D3).

    There is deliberately no PREPARING state: session start is
    synchronous within one API request (library lookup -> canonical
    timeline load -> plan derivation -> ownership), exactly like
    playback start since P012, so no preparing phase is observable.
    """

    IDLE = "IDLE"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def is_active(self) -> bool:
        """True while a session owns appliance activity (WAITING only)."""
        return self is PracticeState.WAITING


class LearningMode(enum.Enum):
    """Learning strategies supported by current authoritative data."""

    WAIT_FOR_NOTE = "WAIT_FOR_NOTE"
    SECTION_PRACTICE = "SECTION_PRACTICE"
    REPEAT_UNTIL_CORRECT = "REPEAT_UNTIL_CORRECT"
    FLOW = "FLOW"


class LearningOutcome(enum.Enum):
    """Latest Core-derived attempt/input outcome for presentation."""

    NONE = "NONE"
    EARLY = "EARLY"
    LATE = "LATE"
    MISSED = "MISSED"
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"


class PracticeSection:
    """Validated half-open song-time range ``[start, end)``."""

    __slots__ = ("start_seconds", "end_seconds")

    def __init__(self, start_seconds: float, end_seconds: float) -> None:
        for name, value in (
            ("start_seconds", start_seconds),
            ("end_seconds", end_seconds),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative number")
        if float(end_seconds) <= float(start_seconds):
            raise ValueError("section end_seconds must be greater than start_seconds")
        self.start_seconds = float(start_seconds)
        self.end_seconds = float(end_seconds)

    def contains(self, onset_seconds: float) -> bool:
        return self.start_seconds <= onset_seconds < self.end_seconds

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PracticeSection):
            return NotImplemented
        return (
            self.start_seconds == other.start_seconds
            and self.end_seconds == other.end_seconds
        )

    def __hash__(self) -> int:
        return hash((self.start_seconds, self.end_seconds))


class PracticeTarget:
    """One human play step: a single note or one same-onset chord.

    ``notes`` are deduplicated, sorted ascending, and always inside the
    verified keyboard range. ``onset_seconds`` is source timing metadata
    (practice is self-paced; nothing schedules from it). ``ordinal`` is
    the target's canonical onset-group position in the FULL song
    projection (``None`` for directly constructed targets); it anchors
    collision-free deterministic identity for plan-derived targets and
    survives section filtering unrenumbered.
    """

    __slots__ = ("notes", "onset_seconds", "ordinal")

    def __init__(
        self,
        notes: tuple[int, ...],
        onset_seconds: float,
        ordinal: int | None = None,
    ) -> None:
        if not isinstance(notes, tuple):
            raise TypeError(
                f"notes must be a tuple of ints, got {type(notes).__name__}"
            )
        for note in notes:
            if not isinstance(note, int) or isinstance(note, bool):
                raise TypeError(f"target note must be an int, got {note!r}")
            if not KEYBOARD_MIN <= note <= KEYBOARD_MAX:
                raise ValueError(f"target note out of keyboard range: {note!r}")
        if len(set(notes)) != len(notes):
            raise ValueError(f"duplicate target notes: {notes!r}")
        if notes != tuple(sorted(notes)):
            raise ValueError(f"target notes must be sorted ascending: {notes!r}")
        if (
            not isinstance(onset_seconds, (int, float))
            or isinstance(onset_seconds, bool)
            or onset_seconds < 0
        ):
            raise ValueError(
                f"onset_seconds must be a non-negative number: {onset_seconds!r}"
            )
        if ordinal is not None and (
            not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0
        ):
            raise ValueError(
                f"target ordinal must be a non-negative int or None: {ordinal!r}"
            )
        self.notes = notes
        self.onset_seconds = float(onset_seconds)
        self.ordinal = ordinal

    @property
    def chord(self) -> bool:
        """True when the target expects several simultaneous notes."""
        return len(self.notes) > 1

    @property
    def names(self) -> tuple[str, ...]:
        """Human-readable note names (e.g. ``("C4", "E4", "G4")``)."""
        return tuple(note_name(note) for note in self.notes)

    @property
    def target_id(self) -> str:
        """Collision-free deterministic identity from canonical content.

        Plan-derived targets carry their canonical onset-group ordinal in
        the full song projection. Directly constructed targets fall back
        to the exact binary onset representation (``float.hex``). Neither
        form uses float-display rounding: distinct canonical onsets can
        differ beyond nine decimal places, so a ``:.9f`` identity could
        collide. No random UUIDs, no ``hash()``, no process-dependent
        values — equivalent timeline reconstruction yields equal IDs.
        """
        notes = "-".join(str(note) for note in self.notes)
        if self.ordinal is None:
            return f"target:{float(self.onset_seconds).hex()}:{notes}"
        return f"target:{self.ordinal}:{notes}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PracticeTarget):
            return NotImplemented
        return (
            self.notes == other.notes
            and self.onset_seconds == other.onset_seconds
        )

    def __hash__(self) -> int:
        return hash((self.notes, self.onset_seconds))

    def __repr__(self) -> str:
        return (
            f"PracticeTarget(notes={self.notes!r}, "
            f"onset_seconds={self.onset_seconds!r})"
        )


class PracticePlan:
    """Immutable ordered practice projection of one song.

    ``targets`` are sorted by (onset, notes); ``skipped_note_ons`` counts
    the note-ons that were deliberately NOT made targets (percussion
    channel, out-of-keyboard range) for honest observability.
    """

    __slots__ = ("targets", "source", "skipped_note_ons", "duration_seconds")

    def __init__(
        self,
        targets: tuple[PracticeTarget, ...],
        source: str,
        skipped_note_ons: int,
        duration_seconds: float,
    ) -> None:
        if not isinstance(targets, tuple):
            raise TypeError(
                f"targets must be a tuple, got {type(targets).__name__}"
            )
        for target in targets:
            if not isinstance(target, PracticeTarget):
                raise TypeError(
                    f"targets must contain PracticeTarget, "
                    f"got {type(target).__name__}"
                )
        if targets:
            onsets = [(t.onset_seconds, t.notes) for t in targets]
            if onsets != sorted(onsets):
                raise ValueError("practice plan targets must be sorted")
        if not isinstance(source, str):
            raise TypeError(f"source must be a string, got {type(source).__name__}")
        if (
            not isinstance(skipped_note_ons, int)
            or isinstance(skipped_note_ons, bool)
            or skipped_note_ons < 0
        ):
            raise ValueError(
                f"skipped_note_ons must be a non-negative int: "
                f"{skipped_note_ons!r}"
            )
        if (
            not isinstance(duration_seconds, (int, float))
            or isinstance(duration_seconds, bool)
            or duration_seconds < 0
        ):
            raise ValueError(
                f"duration_seconds must be a non-negative number: "
                f"{duration_seconds!r}"
            )
        self.targets = targets
        self.source = source
        self.skipped_note_ons = skipped_note_ons
        self.duration_seconds = float(duration_seconds)

    @property
    def total_steps(self) -> int:
        """Number of human steps in the plan (>= 0)."""
        return len(self.targets)

    def __repr__(self) -> str:
        return (
            f"PracticePlan(total_steps={self.total_steps}, "
            f"skipped={self.skipped_note_ons}, source={self.source!r})"
        )


class PracticeSession:
    """Immutable, serializable Practice/Learning V2 snapshot.

    The accepted P015 fields remain stable. UI-004 adds one session identity,
    strategy/configuration, target-attempt feedback, bounded repetition and
    monotonic generation/version fields; it does not create a second state
    machine or persistence layer.
    """

    __slots__ = (
        "flow",
        "elapsed_seconds",
        "scope_section_id",
        "full_lesson",
        "state",
        "available",
        "song_id",
        "title",
        "step_index",
        "total_steps",
        "completed_steps",
        "incorrect_attempts",
        "current_target",
        "started_at",
        "ended_at",
        "error",
        "midi_connection",
        "session_id",
        "mode",
        "hand_selection",
        "section",
        "target_tempo",
        "accepted_notes",
        "latest_outcome",
        "current_repetition",
        "required_repetitions",
        "successful_repetitions",
        "failed_repetitions",
        "generation",
        "version",
        "updated_at",
    )

    def __init__(
        self,
        state: PracticeState,
        *,
        flow: dict | None = None,
        elapsed_seconds: float = 0.0,
        scope_section_id: str | None = None,
        full_lesson: bool = False,
        available: bool = False,
        song_id: str | None = None,
        title: str | None = None,
        step_index: int | None = None,
        total_steps: int | None = None,
        completed_steps: int = 0,
        incorrect_attempts: int = 0,
        current_target: PracticeTarget | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        error: str | None = None,
        midi_connection: str | None = None,
        session_id: str | None = None,
        mode: LearningMode = LearningMode.WAIT_FOR_NOTE,
        hand_selection: str | None = None,
        section: PracticeSection | None = None,
        target_tempo: float | None = None,
        accepted_notes: tuple[int, ...] = (),
        latest_outcome: LearningOutcome = LearningOutcome.NONE,
        current_repetition: int = 0,
        required_repetitions: int = 1,
        successful_repetitions: int = 0,
        failed_repetitions: int = 0,
        generation: int = 0,
        version: int = 0,
        updated_at: str | None = None,
    ) -> None:
        if not isinstance(state, PracticeState):
            raise TypeError(
                f"state must be a PracticeState, got {type(state).__name__}"
            )
        if not isinstance(available, bool):
            raise TypeError("available must be a bool")
        if step_index is not None:
            _validate_non_negative_int(step_index, "step_index")
        if total_steps is not None:
            _validate_non_negative_int(total_steps, "total_steps")
        _validate_non_negative_int(completed_steps, "completed_steps")
        _validate_non_negative_int(incorrect_attempts, "incorrect_attempts")
        _validate_non_negative_int(current_repetition, "current_repetition")
        _validate_non_negative_int(successful_repetitions, "successful_repetitions")
        _validate_non_negative_int(failed_repetitions, "failed_repetitions")
        _validate_non_negative_int(generation, "generation")
        _validate_non_negative_int(version, "version")
        if (
            not isinstance(required_repetitions, int)
            or isinstance(required_repetitions, bool)
            or not 1 <= required_repetitions <= 16
        ):
            raise ValueError("required_repetitions must be an integer in [1..16]")
        if not isinstance(mode, LearningMode):
            raise TypeError("mode must be a LearningMode")
        if not isinstance(latest_outcome, LearningOutcome):
            raise TypeError("latest_outcome must be a LearningOutcome")
        if section is not None and not isinstance(section, PracticeSection):
            raise TypeError("section must be a PracticeSection or None")
        if target_tempo is not None:
            if (
                not isinstance(target_tempo, (int, float))
                or isinstance(target_tempo, bool)
                or not math.isfinite(target_tempo)
                or target_tempo <= 0
            ):
                raise ValueError("target_tempo must be a positive number or None")
            target_tempo = float(target_tempo)
        if not isinstance(accepted_notes, tuple):
            raise TypeError("accepted_notes must be a tuple")
        for note in accepted_notes:
            if not isinstance(note, int) or isinstance(note, bool):
                raise TypeError("accepted_notes must contain integers")
        if current_target is not None and not isinstance(
            current_target, PracticeTarget
        ):
            raise TypeError(
                f"current_target must be a PracticeTarget or None, "
                f"got {type(current_target).__name__}"
            )
        for name, value in (
            ("song_id", song_id),
            ("title", title),
            ("started_at", started_at),
            ("ended_at", ended_at),
            ("error", error),
            ("midi_connection", midi_connection),
            ("session_id", session_id),
            ("hand_selection", hand_selection),
            ("updated_at", updated_at),
        ):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or None")
        self.elapsed_seconds = elapsed_seconds
        self.scope_section_id = scope_section_id
        self.full_lesson = full_lesson
        self.flow = flow
        self.state = state
        self.available = available
        self.song_id = song_id
        self.title = title
        self.step_index = step_index
        self.total_steps = total_steps
        self.completed_steps = completed_steps
        self.incorrect_attempts = incorrect_attempts
        self.current_target = current_target
        self.started_at = started_at
        self.ended_at = ended_at
        self.error = error
        self.midi_connection = midi_connection
        self.session_id = session_id
        self.mode = mode
        self.hand_selection = hand_selection
        self.section = section
        self.target_tempo = target_tempo
        self.accepted_notes = accepted_notes
        self.latest_outcome = latest_outcome
        self.current_repetition = current_repetition
        self.required_repetitions = required_repetitions
        self.successful_repetitions = successful_repetitions
        self.failed_repetitions = failed_repetitions
        self.generation = generation
        self.version = version
        self.updated_at = updated_at

    @property
    def progress(self) -> float:
        """Completed fraction of the plan (0.0 when there is no plan)."""
        if not self.total_steps:
            return 0.0
        if self.flow is not None:
            if self.state is PracticeState.COMPLETED:
                return 1.0
            duration = self.flow["duration_seconds"]
            return round(max(0.0, min(1.0, self.flow["position_seconds"] / duration)), 4) if duration else 0.0
        return round(self.completed_steps / self.total_steps, 4)


def _validate_non_negative_int(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be non-negative: {value!r}")
