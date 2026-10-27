"""Versioned value model for deterministic fingering plans."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FingeringConfig:
    """Bounded ergonomic strategy inputs; every field affects plan identity."""

    max_hand_span_semitones: int = 12
    alternative_count: int = 3
    strategy: str = "balanced_v1"

    def __post_init__(self) -> None:
        if not 5 <= self.max_hand_span_semitones <= 24:
            raise ValueError("max_hand_span_semitones must be in 5..24")
        if not 1 <= self.alternative_count <= 5:
            raise ValueError("alternative_count must be in 1..5")
        if self.strategy != "balanced_v1":
            raise ValueError("strategy must be balanced_v1")

    def identity(self) -> dict:
        return {
            "alternative_count": self.alternative_count,
            "max_hand_span_semitones": self.max_hand_span_semitones,
            "strategy": self.strategy,
        }


@dataclass(frozen=True, slots=True)
class FingerTarget:
    score_index: int
    target_id: str
    midi: int
    channel: int
    onset_seconds: float
    duration_seconds: float
    hand: str
    finger: int
    alternatives: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FingeringPlan:
    schema_version: int
    engine_version: str
    plan_id: str
    song_id: str
    source_version: str
    version: int
    generation: int
    strategy: str
    provenance: str
    targets: tuple[FingerTarget, ...]
    warnings: tuple[str, ...]
    excluded_note_count: int
    config: FingeringConfig
