"""MIDI metadata extraction for the library catalog (PIANO-P010).

This module is the ONLY place in ``piano_core.library`` that imports
mido (lazily, same canonical ``mido==1.3.3`` stack — no new dependency).
Extraction is read-only and per-file: a malformed file raises
:class:`MidiParseError` and the scanner records the outcome without
touching its neighbors.

Supported: SMF formats 0 and 1 with metrical (non-SMPTE) division.
SMF format 2 / SMPTE division are reported as ``unsupported`` (the
catalog records the file; playback of such files is out of scope).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Default SMF tempo (µs per beat) when a file contains no set_tempo.
_DEFAULT_TEMPO = 500_000


class MidiParseError(RuntimeError):
    """A MIDI file could not be read/parsed (per-file, isolated)."""


class UnsupportedMidiError(MidiParseError):
    """The file is structurally MIDI but uses an unsupported variant."""


@dataclass(frozen=True, slots=True)
class SongMetadata:
    """Extracted metadata for one source file (all fields deterministic)."""

    midi_format: int
    track_count: int
    ticks_per_beat: int
    duration_seconds: float
    tempo_bpm_first: float | None
    title: str | None


def mido_available() -> bool:
    """True when the canonical mido stack is importable (enrichment needs it)."""
    try:
        import mido  # noqa: F401
        return True
    except ImportError:
        return False


def extract_metadata(path: object) -> SongMetadata:
    """Extract catalog metadata from one MIDI file (read-only).

    Raises :class:`UnsupportedMidiError` for structurally valid but
    unsupported variants (format 2, SMPTE division) and
    :class:`MidiParseError` for anything unreadable/malformed.
    """
    import mido  # lazy; sanctioned site (see boundary tests)

    file_path = Path(str(path))
    try:
        midi_file = mido.MidiFile(str(file_path))
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        raise MidiParseError(f"cannot parse MIDI file: {detail}") from exc

    midi_format = int(getattr(midi_file, "type", -1))
    if midi_format not in (0, 1):
        raise UnsupportedMidiError(f"SMF format {midi_format} is not supported")

    ticks_per_beat = int(getattr(midi_file, "ticks_per_beat", 0))
    if ticks_per_beat <= 0:
        raise UnsupportedMidiError("SMPTE-timed files are not supported")

    track_count = len(midi_file.tracks)
    title: str | None = None
    tempo_bpm_first: float | None = None
    for track in midi_file.tracks:
        for message in track:
            if title is None and message.type == "track_name":
                name = str(message.name).strip()
                title = name or None
            if tempo_bpm_first is None and message.type == "set_tempo":
                tempo = int(message.tempo)
                if tempo > 0:
                    tempo_bpm_first = round(60_000_000.0 / tempo, 3)
            if title is not None and tempo_bpm_first is not None:
                break
        if title is not None and tempo_bpm_first is not None:
            break

    try:
        duration_seconds = float(midi_file.length)
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        raise MidiParseError(f"cannot derive duration: {detail}") from exc

    return SongMetadata(
        midi_format=midi_format,
        track_count=track_count,
        ticks_per_beat=ticks_per_beat,
        duration_seconds=round(duration_seconds, 3),
        tempo_bpm_first=tempo_bpm_first,
        title=title,
    )
