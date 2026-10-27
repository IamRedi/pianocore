"""MIDI source adapter for the Learn Library (Source B, PIANO-LEARN-001).

Maps the sanctioned raw MIDI analysis (``piano_core.midi.analysis`` —
the MIDI package is the raw-mido boundary) onto canonical Learn items.
The adapter is deterministic and fail-closed:

- hanging notes, true orphan note_offs, non-keyboard program content
  and out-of-range notes classify the file UNSUPPORTED with the exact
  reason; redundant engraver terminations are tolerated as recorded
  evidence (they change no sounding result);
- hand labels come ONLY from the analysis's two-track register split;
  anything else leaves hands unknown (never fabricated);
- a verified fallback engraving of the same work may be substituted
  when the primary file is UNSUPPORTED — the substitution is recorded
  in the item's provenance detail.

No raw mido tokens live in this module (architecture boundary).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from piano_core.learn.model import LearnItem, LearnNote
from piano_core.playback.timeline import MidiAnalysisError, analyze_midi_file

__all__ = ["iter_midi_items", "MidiSourceError"]

#: Pianovera published categories mapped onto the Learn 0..4 curriculum
#: (source hints act as floors inside the level evaluation).
_CATEGORY_LEVELS = {"beginner": 1, "intermediate": 3, "advanced": 4}

MidiSourceError = MidiAnalysisError


def load_midi_notes(
    path: str | Path,
) -> tuple[tuple[LearnNote, ...], dict, tuple[str, ...]]:
    """Analyze one MIDI file into canonical notes plus facts."""
    raw_notes, facts, problems = analyze_midi_file(path)
    notes = tuple(
        LearnNote(
            onset=n["onset"],
            offset=n["offset"],
            midi=n["midi"],
            velocity=n["velocity"],
            hand=n["hand"],
            track=n["track"],
        )
        for n in raw_notes
    )
    return notes, facts, tuple(problems)


def iter_midi_items(records: Iterator[dict]) -> Iterator[LearnItem]:
    """Yield canonical LearnItems for MIDI source records.

    Each record carries: ``lesson_key``, ``title``, ``path`` and optional
    ``fallback_path`` (tried only when the primary file is UNSUPPORTED;
    the substitution is recorded), ``composer``, ``source``,
    ``source_detail``, ``license_name``, ``license_url``, ``attribution``,
    ``category`` (source difficulty hint), ``midi_sha256`` (binds the
    exact ingested bytes) and ``summary``.
    """
    for record in records:
        path = Path(record["path"])
        fallback = record.get("fallback_path")
        problems: tuple[str, ...] = ()
        facts: dict = {}
        notes: tuple[LearnNote, ...] = ()
        used_fallback = False
        primary_problems: tuple[str, ...] = ()
        try:
            notes, facts, problems = load_midi_notes(path)
        except MidiSourceError as exc:
            problems = (str(exc),)
        if problems and fallback:
            primary_problems = problems
            try:
                notes, facts, problems = load_midi_notes(fallback)
                used_fallback = not problems
            except MidiSourceError as exc:
                problems = primary_problems + (f"fallback also failed: {exc}",)

        category = str(record.get("category") or "").lower()
        level_hint = _CATEGORY_LEVELS.get(category)

        hands_availability = "unknown"
        if notes and all(note.hand is not None for note in notes):
            hands_availability = "available"

        detail = {
            **(record.get("source_detail") or {}),
            "midi_sha256": record.get("midi_sha256"),
            "content_track_count": facts.get("content_track_count"),
            "hands_evidence": facts.get("hands_evidence"),
            "redundant_note_offs": facts.get("redundant_note_offs", 0),
        }
        if used_fallback:
            detail["source_substitution"] = (
                "primary engraving UNSUPPORTED ("
                + "; ".join(primary_problems[:3])
                + "); used the verified fallback engraving of the same work"
            )

        yield LearnItem(
            lesson_key=str(record["lesson_key"]),
            title=str(record.get("title") or path.stem),
            composer=record.get("composer"),
            source=str(record.get("source") or "midi"),
            source_detail=detail,
            license_name=str(record.get("license_name") or "Unknown"),
            license_url=record.get("license_url"),
            attribution=record.get("attribution"),
            notes=notes,
            tempo_bpm=facts.get("tempo_bpm"),
            time_signature=facts.get("time_signature"),
            level_hint=level_hint,
            hands_availability=hands_availability,
            summary=record.get("summary"),
            problems=problems,
        )
