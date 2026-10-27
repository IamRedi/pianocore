"""Guided Practice subsystem (PIANO-P015, ADR-PRACTICE-V1).

A canonical, silent, self-paced learning mode: the canonical P009
timeline is projected into human play targets, the P014 LED keyboard
shows the current target, the canonical StateStore observes what the
learner physically plays, and only a correct play advances the lesson.
Practice never sends automated MIDI output and is mutually exclusive
with automated playback through the appliance-wide advisory lock.
"""

from piano_core.practice.model import (
    LearningMode,
    LearningOutcome,
    PracticeActivityConflictError,
    PracticeAlreadyActiveError,
    PracticeError,
    PracticeInvalidStateError,
    PracticeNotAvailableError,
    PracticePlanError,
    PracticeSession,
    PracticeSection,
    PracticeState,
    PracticeStaleCommandError,
    PracticeTarget,
    note_name,
)
from piano_core.practice.plan import build_practice_plan, select_practice_section

__all__ = [
    "PracticeActivityConflictError",
    "LearningMode",
    "LearningOutcome",
    "PracticeAlreadyActiveError",
    "PracticeError",
    "PracticeInvalidStateError",
    "PracticeNotAvailableError",
    "PracticePlanError",
    "PracticeSession",
    "PracticeSection",
    "PracticeState",
    "PracticeStaleCommandError",
    "PracticeTarget",
    "build_practice_plan",
    "select_practice_section",
    "note_name",
]
