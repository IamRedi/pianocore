"""Pure deterministic hand and finger planning over a canonical score.

This module never parses MIDI and never grades performance. It consumes the
accepted score projection, labels every playable target as deterministic
inference, and fails closed when a simultaneous shape exceeds two human hands.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass

from piano_core.fingering.model import FingerTarget, FingeringConfig, FingeringPlan
from piano_core.playback.score import ScoreProjection, ScoreSpan

PIANO_MIN = 21
PIANO_MAX = 108
PERCUSSION_CHANNEL = 10
PROVENANCE = "DETERMINISTIC_INFERENCE"
FINGERING_SCHEMA_VERSION = 1
FINGERING_PLAN_VERSION = 1
FINGERING_GENERATION_VERSION = 1
FINGERING_ENGINE_VERSION = "balanced-engine-v1"


class FingeringUnsupportedError(RuntimeError):
    """Canonical material cannot be represented without inventing a solution."""


@dataclass(frozen=True, slots=True)
class _Choice:
    cost: float
    fingers: tuple[int, ...]


def _black(note: int) -> bool:
    return note % 12 in {1, 3, 6, 8, 10}


def _finger_choices(
    notes: tuple[int, ...],
    hand: str,
    previous: dict[int, int],
    direction: int,
    fixed: dict[int, int] | None = None,
) -> list[_Choice]:
    """Rank every physically ordered chord fingering with explicit costs."""
    if not notes:
        return [_Choice(0.0, ())]
    fixed = fixed or {}
    ranked: list[_Choice] = []
    for selected in itertools.combinations(range(1, 6), len(notes)):
        fingers = selected if hand == "RIGHT" else tuple(reversed(selected))
        if any(fingers[notes.index(note)] != finger for note, finger in fixed.items()):
            continue
        cost = 0.0
        for note, finger in zip(notes, fingers):
            if _black(note) and finger == 1:
                cost += 4.0
            old = previous.get(note)
            if old is not None:
                cost += abs(old - finger) * 2.5
        for i in range(1, len(notes)):
            semitones = notes[i] - notes[i - 1]
            finger_steps = abs(fingers[i] - fingers[i - 1])
            cost += abs(semitones - finger_steps * 2.0) * 0.55
            if semitones > 7 and finger_steps < 3:
                cost += 3.0
        if len(notes) == 1 and direction:
            # Leave fingers available in the direction of travel.
            desired = 2 if (hand == "RIGHT") == (direction > 0) else 4
            cost += abs(fingers[0] - desired) * 0.6
        ranked.append(_Choice(round(cost, 6), tuple(fingers)))
    ranked.sort(key=lambda item: (item.cost, item.fingers))
    return ranked


def _valid_hand(notes: tuple[int, ...], config: FingeringConfig) -> bool:
    return len(notes) <= 5 and (
        len(notes) < 2 or notes[-1] - notes[0] <= config.max_hand_span_semitones
    )


def _split_chord(
    notes: tuple[int, ...],
    previous_centers: dict[str, float],
    config: FingeringConfig,
    held_hands: dict[int, str] | None = None,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    held_hands = held_hands or {}
    if len(notes) > 10:
        raise FingeringUnsupportedError(
            f"onset contains {len(notes)} distinct pitches; two hands support at most 10"
        )
    candidates: list[tuple[float, int, tuple[int, ...], tuple[int, ...]]] = []
    for split in range(len(notes) + 1):
        left, right = notes[:split], notes[split:]
        if any(note not in left for note, hand in held_hands.items() if hand == "LEFT"):
            continue
        if any(note not in right for note, hand in held_hands.items() if hand == "RIGHT"):
            continue
        if not _valid_hand(left, config) or not _valid_hand(right, config):
            continue
        cost = 0.0
        if left:
            center = sum(left) / len(left)
            cost += abs(center - previous_centers["LEFT"])
            cost += max(0, center - 64) * 1.75
        if right:
            center = sum(right) / len(right)
            cost += abs(center - previous_centers["RIGHT"])
            cost += max(0, 56 - center) * 1.75
        if len(notes) > 1 and (not left or not right):
            cost += 3.0
        cost += abs(len(left) - len(right)) * 0.15
        candidates.append((round(cost, 6), split, left, right))
    if not candidates:
        raise FingeringUnsupportedError(
            "simultaneous pitches exceed the configured hand span"
        )
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2], candidates[0][3]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _alternatives(
    ranked: list[_Choice], finger_index: int, primary: int, limit: int
) -> tuple[int, ...]:
    """Return up to limit distinct candidates, excluding the primary."""
    result: list[int] = []
    for choice in ranked:
        candidate = choice.fingers[finger_index]
        if candidate == primary or candidate in result:
            continue
        result.append(candidate)
        if len(result) == limit:
            break
    return tuple(result)


def generate_plan(
    score: ScoreProjection, config: FingeringConfig | None = None
) -> FingeringPlan:
    """Generate the same complete plan for the same score and configuration."""
    if not isinstance(score, ScoreProjection):
        raise TypeError("score must be a ScoreProjection")
    config = config or FingeringConfig()
    if not isinstance(config, FingeringConfig):
        raise TypeError("config must be a FingeringConfig")

    playable = tuple(sorted(
        (
            span
            for span in score.spans
            if PIANO_MIN <= span.note <= PIANO_MAX and span.channel != PERCUSSION_CHANNEL
        ),
        key=lambda span: (
            span.onset_seconds,
            span.index,
            span.note,
            span.channel,
            span.duration_seconds,
            span.velocity,
        ),
    ))
    excluded = len(score.spans) - len(playable)
    warnings: list[str] = []
    if excluded:
        warnings.append(f"excluded_non_keyboard_or_percussion_notes:{excluded}")
    warnings.append("source_contains_no_authored_hand_or_fingering_metadata")

    groups: list[tuple[float, list[ScoreSpan]]] = []
    for span in playable:
        if not groups or groups[-1][0] != span.onset_seconds:
            groups.append((span.onset_seconds, [span]))
        else:
            groups[-1][1].append(span)

    centers = {"LEFT": 48.0, "RIGHT": 72.0}
    prior_fingers: dict[str, dict[int, int]] = {"LEFT": {}, "RIGHT": {}}
    # One physical key has one held assignment regardless of duplicate score
    # aliases. Lifetimes are canonical half-open intervals [onset, end).
    active: dict[int, tuple[str, int, float]] = {}
    targets: list[FingerTarget] = []
    for group_index, (onset, spans) in enumerate(groups):
        active = {
            note: held for note, held in active.items() if held[2] > onset
        }
        attack_notes = tuple(sorted({span.note for span in spans}))
        sounding_notes = tuple(sorted(set(active).union(attack_notes)))
        held_hands = {note: held[0] for note, held in active.items()}
        left, right = _split_chord(sounding_notes, centers, config, held_hands)
        next_notes = (
            tuple(sorted({span.note for span in groups[group_index + 1][1]}))
            if group_index + 1 < len(groups)
            else ()
        )
        direction = 0
        if next_notes and attack_notes:
            delta = sum(next_notes) / len(next_notes) - sum(attack_notes) / len(attack_notes)
            direction = 1 if delta > 0 else -1 if delta < 0 else 0
        assignment: dict[int, tuple[str, int, tuple[int, ...]]] = {}
        for hand, hand_notes in (("LEFT", left), ("RIGHT", right)):
            fixed = {
                note: active[note][1]
                for note in hand_notes
                if note in active
            }
            ranked = _finger_choices(
                hand_notes, hand, prior_fingers[hand], direction, fixed
            )
            if not ranked:
                raise FingeringUnsupportedError(
                    "held fingers leave no valid assignment for the new attack"
                )
            chosen = ranked[0].fingers
            for note, finger_index, finger in zip(
                hand_notes, range(len(hand_notes)), chosen
            ):
                alternatives = _alternatives(
                    ranked, finger_index, finger, config.alternative_count
                )
                assignment[note] = (hand, finger, alternatives)
            if hand_notes:
                centers[hand] = sum(hand_notes) / len(hand_notes)
                prior_fingers[hand] = dict(zip(hand_notes, chosen))
        for span in spans:
            hand, finger, alternatives = assignment[span.note]
            targets.append(
                FingerTarget(
                    score_index=span.index,
                    target_id=f"score:{span.index}",
                    midi=span.note,
                    channel=span.channel,
                    onset_seconds=span.onset_seconds,
                    duration_seconds=span.duration_seconds,
                    hand=hand,
                    finger=finger,
                    alternatives=alternatives,
                )
            )
        # Duplicate aliases share the attack assignment and contribute only
        # the longest end time. Zero-duration attacks never create occupancy.
        for note in attack_notes:
            end = max(
                onset + span.duration_seconds
                for span in spans
                if span.note == note
            )
            if end > onset:
                hand, finger, _ = assignment[note]
                previous_end = active.get(note, (hand, finger, onset))[2]
                active[note] = (hand, finger, max(previous_end, end))

    identity = {
        "schema_version": FINGERING_SCHEMA_VERSION,
        "plan_version": FINGERING_PLAN_VERSION,
        "generation_version": FINGERING_GENERATION_VERSION,
        "engine_version": FINGERING_ENGINE_VERSION,
        "strategy": config.strategy,
        "provenance": PROVENANCE,
        "config": config.identity(),
        "source_version": score.source_version,
        "targets": [
            [
                target.score_index,
                target.target_id,
                target.midi,
                target.channel,
                0.0 if target.onset_seconds == 0 else target.onset_seconds,
                0.0 if target.duration_seconds == 0 else target.duration_seconds,
                target.hand,
                target.finger,
                list(target.alternatives),
            ]
            for target in targets
        ],
    }
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()
    return FingeringPlan(
        schema_version=FINGERING_SCHEMA_VERSION,
        engine_version=FINGERING_ENGINE_VERSION,
        plan_id=f"fingering-sha256:{digest}",
        song_id=score.song_id,
        source_version=score.source_version,
        version=FINGERING_PLAN_VERSION,
        generation=FINGERING_GENERATION_VERSION,
        strategy=config.strategy,
        provenance=PROVENANCE,
        targets=tuple(targets),
        warnings=tuple(warnings),
        excluded_note_count=excluded,
        config=config,
    )
