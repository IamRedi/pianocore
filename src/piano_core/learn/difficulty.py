"""Deterministic difficulty evaluation for the Learn Library.

Computes reproducible difficulty metrics from canonical notes and maps
them to a curriculum level 0..4. Source-provided level hints act as a
FLOOR only: computed evidence can raise a placement, but computed
simplicity can never pull a piece below its evidence except through the
explicit Level-0 gate (which is itself evidence-based, not label-based).

Nothing here reads files, networks or clocks; identical notes always
yield identical metrics, levels and labels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from piano_core.learn.model import LearnNote

__all__ = [
    "CURRICULUM_LEVELS",
    "DifficultyMetrics",
    "LevelDecision",
    "compute_metrics",
    "evaluate_level",
    "level_label",
]

#: Canonical curriculum levels (Owner-approved structure, PIANO-LEARN-001).
CURRICULUM_LEVELS = (
    (0, "First Notes"),
    (1, "Beginner"),
    (2, "Elementary"),
    (3, "Intermediate"),
    (4, "Advanced"),
)


@dataclass(frozen=True, slots=True)
class DifficultyMetrics:
    """Deterministic physical/rhythmic difficulty evidence."""

    note_count: int
    duration_seconds: float
    notes_per_second: float
    max_simultaneous: int
    mean_simultaneous: float
    chord_ratio: float
    pitch_span_semitones: int
    max_leap_semitones: int
    max_hand_span_semitones: int | None
    max_chord_size: int
    hands_together: bool
    tempo_bpm: float | None

    def as_dict(self) -> dict:
        return {
            "note_count": self.note_count,
            "duration_seconds": round(self.duration_seconds, 3),
            "notes_per_second": round(self.notes_per_second, 4),
            "max_simultaneous": self.max_simultaneous,
            "mean_simultaneous": round(self.mean_simultaneous, 4),
            "chord_ratio": round(self.chord_ratio, 4),
            "pitch_span_semitones": self.pitch_span_semitones,
            "max_leap_semitones": self.max_leap_semitones,
            "max_hand_span_semitones": self.max_hand_span_semitones,
            "max_chord_size": self.max_chord_size,
            "hands_together": self.hands_together,
            "tempo_bpm": self.tempo_bpm,
        }


@dataclass(frozen=True, slots=True)
class LevelDecision:
    """Curriculum placement with the exact reasons behind it."""

    level: int
    difficulty: str  # "easy" | "medium" | "hard"
    score: float
    source_floor: int | None
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "level_name": level_label(self.level),
            "difficulty": self.difficulty,
            "score": round(self.score, 4),
            "source_floor": self.source_floor,
            "reasons": list(self.reasons),
        }


def level_label(level: int) -> str:
    for value, name in CURRICULUM_LEVELS:
        if value == level:
            return name
    raise ValueError(f"unknown curriculum level: {level!r}")


def compute_metrics(
    notes: tuple[LearnNote, ...], tempo_bpm: float | None = None
) -> DifficultyMetrics:
    """Compute difficulty evidence from canonical notes.

    Raises ``ValueError`` for empty input (callers must classify empty
    material as UNSUPPORTED before reaching this point).
    """
    if not notes:
        raise ValueError("cannot compute difficulty metrics for empty notes")

    ordered = sorted(notes, key=lambda n: (n.onset, n.midi))
    duration = max(n.offset for n in ordered)
    duration = duration if math.isfinite(duration) and duration > 0 else 0.0
    note_count = len(ordered)

    # Exact-onset grouping (chords), mirroring practice-plan semantics.
    groups: list[list[LearnNote]] = []
    for note in ordered:
        if groups and abs(note.onset - groups[-1][0].onset) < 1e-9:
            groups[-1].append(note)
        else:
            groups.append([note])
    sizes = [len(g) for g in groups]
    max_sim = max(sizes)
    mean_sim = note_count / len(groups)
    chord_notes = sum(s for s in sizes if s > 1)
    chord_ratio = chord_notes / note_count
    hands_together = bool(
        any(n.hand == "RH" for n in ordered) and any(n.hand == "LH" for n in ordered)
    )

    pitches = [n.midi for n in ordered]
    pitch_span = max(pitches) - min(pitches)

    leaps = [abs(b - a) for a, b in zip(pitches, pitches[1:])]
    max_leap = max(leaps) if leaps else 0

    hand_spans: list[int] = []
    for group in groups:
        for hand in ("RH", "LH"):
            pitches_h = [n.midi for n in group if n.hand == hand]
            if len(pitches_h) >= 2:
                hand_spans.append(max(pitches_h) - min(pitches_h))
    # Melodic physical reach is carried by max_leap; only simultaneous
    # same-hand spreads count as hand-span evidence.
    max_hand_span = max(hand_spans) if hand_spans else None

    nps = note_count / duration if duration > 0 else float(note_count)

    return DifficultyMetrics(
        note_count=note_count,
        duration_seconds=duration,
        notes_per_second=nps,
        max_simultaneous=max_sim,
        mean_simultaneous=mean_sim,
        chord_ratio=chord_ratio,
        pitch_span_semitones=pitch_span,
        max_leap_semitones=max_leap,
        max_hand_span_semitones=max_hand_span,
        max_chord_size=max_sim,
        hands_together=hands_together,
        tempo_bpm=tempo_bpm,
    )


#: Deterministic difficulty score weights (documented, reproducible).
_WEIGHTS = {
    "density": 26.0,   # notes per second, reference 8 nps
    "polyphony": 18.0,  # max simultaneous notes, reference 5
    "mean_poly": 14.0,  # mean simultaneous, reference 3
    "chords": 10.0,    # chord ratio, reference 0.5
    "span": 10.0,      # pitch span, reference 60 semitones
    "leap": 8.0,       # max melodic leap, reference 24 semitones
    "hand_span": 8.0,  # max same-hand chord span, reference 24 semitones
    "tempo": 6.0,      # tempo, reference 160 bpm
}
_DENSITY_REF = 8.0
_POLY_REF = 5.0
_MEAN_REF = 3.0
_CHORD_REF = 0.5
_SPAN_REF = 60.0
_LEAP_REF = 24.0
_HAND_SPAN_REF = 24.0
_TEMPO_REF = 160.0

#: Level thresholds over the 0..100 composite score: a score at or above
#: the threshold places the item at the NEXT level (score < 8 -> L0).
_LEVEL_THRESHOLDS = ((0, 8.0), (1, 24.0), (2, 42.0), (3, 62.0))


def difficulty_score(metrics: DifficultyMetrics) -> float:
    """Composite 0..100 difficulty score (deterministic, documented)."""
    parts = {
        "density": min(1.0, metrics.notes_per_second / _DENSITY_REF),
        "polyphony": min(1.0, metrics.max_simultaneous / _POLY_REF),
        "mean_poly": min(1.0, metrics.mean_simultaneous / _MEAN_REF),
        "chords": min(1.0, metrics.chord_ratio / _CHORD_REF),
        "span": min(1.0, metrics.pitch_span_semitones / _SPAN_REF),
        "leap": min(1.0, metrics.max_leap_semitones / _LEAP_REF),
        "tempo": min(1.0, (metrics.tempo_bpm or 0.0) / _TEMPO_REF),
    }
    if metrics.max_hand_span_semitones is not None:
        parts["hand_span"] = min(
            1.0, metrics.max_hand_span_semitones / _HAND_SPAN_REF
        )
    score = 0.0
    for name, weight in _WEIGHTS.items():
        score += weight * parts.get(name, 0.0)
    total_weight = sum(weight for name, weight in _WEIGHTS.items() if name in parts)
    return 100.0 * score / total_weight if total_weight else 0.0


def _computed_level(score: float) -> int:
    level = 0
    for threshold_level, threshold in _LEVEL_THRESHOLDS:
        if score >= threshold:
            level = threshold_level + 1
    return min(level, 4)


def _difficulty_label(level: int) -> str:
    return {0: "easy", 1: "easy", 2: "medium", 3: "medium", 4: "hard"}[level]


def evaluate_level(
    metrics: DifficultyMetrics,
    source_level: int | None = None,
) -> LevelDecision:
    """Place an item on the Level 0-4 curriculum.

    ``source_level`` is the SOURCE-provided hint already mapped to the
    0..4 curriculum by the adapter. Policy (PIANO-LEARN-001):

    - computed evidence is the baseline;
    - a reliable source hint acts only as a FLOOR (a piece is never
      presented as easier than its computed difficulty, and a source
      "beginner" label alone can never call an intermediate piece
      beginner);
    - Level 0 additionally requires strict first-notes evidence
      (monophonic, few notes, short, one hand): no hint can place a
      polyphonic piece at Level 0.
    """
    reasons: list[str] = []
    score = difficulty_score(metrics)
    computed = _computed_level(score)
    reasons.append(f"computed score {score:.1f} -> level {computed}")

    # Level-0 first-notes gate: strict evidence both ways. Ineligible
    # material can never sit at Level 0, and eligible weak-L1 material
    # (monophonic, few notes, short, one hand) is placed AT Level 0 —
    # that is exactly the "First Notes" curriculum band.
    level0_eligible = (
        metrics.note_count <= 12
        and metrics.max_simultaneous == 1
        and metrics.duration_seconds <= 45.0
        and not metrics.hands_together
    )
    if level0_eligible and computed == 1 and score < _LEVEL_THRESHOLDS[1][1]:
        computed = 0
        reasons.append("first-notes evidence gate met (monophonic, <=12 notes, <=45s, one hand)")
    elif not level0_eligible and computed == 0:
        computed = 1
        reasons.append(
            "level-0 evidence gate failed (monophonic, <=12 notes, <=45s, one hand required)"
        )

    floor = source_level if isinstance(source_level, int) and 0 <= source_level <= 4 else None
    level = max(computed, floor) if floor is not None else computed
    if floor is not None and floor > computed:
        reasons.append(f"source floor {floor} raised placement")
    if level == 0 and floor == 0 and computed == 0 and score >= _LEVEL_THRESHOLDS[1][1]:
        reasons.append("level 0 by score and hint agreement")
    if level == 4 and score < _LEVEL_THRESHOLDS[2][1]:
        reasons.append(
            "level 4 from source floor, computed score below intermediate threshold"
        )

    return LevelDecision(
        level=level,
        difficulty=_difficulty_label(level),
        score=score,
        source_floor=floor,
        reasons=tuple(reasons),
    )
