"""Playback timeline: Standard MIDI File -> canonical output commands (P009).

The timeline layer is the pure, deterministic bridge between a SMF on
disk and the canonical :mod:`~piano_core.midi.output_events` command
model. It knows nothing about threads, hardware, or the engine.

Two stages:

- :func:`load_timeline` — the ONLY place in the playback package that
  touches mido (lazy import, same proven ``mido==1.3.3`` stack; no new
  dependency). Reads the file, rejects what it cannot interpret honestly
  (missing/garbage files, SMF format 2, SMPTE time division), validates
  the tempo scale, and hands the parsed tracks to the pure builder. The
  file is opened strictly read-only and is never modified.
- :func:`build_timeline` — pure and mido-free: merges all tracks to
  absolute ticks, walks the tempo map incrementally (default tempo
  500,000 µs/beat), and emits frozen :class:`TimelineEvent` records of
  validated canonical :class:`~piano_core.midi.output_events.OutputCommand`
  payloads at song-time seconds. Channels are converted from SMF
  0-based to the canonical 1-based convention.

Ordering rules (deterministic):

- events sort by ``(absolute_tick, phase, track_index, in-track order)``;
- ``phase`` orders releases before attacks at the same tick
  (NOTE_OFF / velocity-0 NOTE_ON / control changes / tempo markers are
  phase 0, real NOTE_ONs are phase 1) so a same-tick re-strike can never
  orphan a sounding note as unowned;
- tempo markers advance the time base before taking effect, so every
  event at tick T is positioned with the tempo segment in force *before*
  T (standard SMF semantics).

The tempo multiplier is NEVER baked into the timeline: timelines live in
song-time seconds; the engine scales wall durations at scheduling time.
Source files are never mutated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from piano_core.midi.output_events import (
    note_off as build_note_off,
    note_on as build_note_on,
    sustain as build_sustain,
)

__all__ = [
    "MidiFileError",
    "TEMPO_SCALE_MAX",
    "TEMPO_SCALE_MIN",
    "TempoScaleError",
    "Timeline",
    "TimelineEvent",
    "build_timeline",
    "load_timeline",
    "load_timeline_if_playable",
    "timeline_loadable",
    "validate_tempo_scale",
]

#: Inclusive bounds of the playback tempo multiplier (1.0 = original tempo).
TEMPO_SCALE_MIN = 0.25
TEMPO_SCALE_MAX = 4.0

#: Default SMF tempo (µs per beat) when a file contains no set_tempo: 120 BPM.
_DEFAULT_TEMPO = 500_000

#: CC number of the sustain pedal — the only control change playback emits.
_SUSTAIN_CC = 64


class MidiFileError(RuntimeError):
    """A Standard MIDI File could not be loaded or interpreted. Fail closed."""


class TempoScaleError(ValueError):
    """The playback tempo multiplier is outside the bounded valid range."""


def validate_tempo_scale(value: object) -> float:
    """Validate and normalize the tempo multiplier (finite, 0.25..4.0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TempoScaleError(
            f"tempo scale must be a number, got {type(value).__name__}"
        )
    scale = float(value)
    if not math.isfinite(scale):
        raise TempoScaleError(f"tempo scale must be finite, got {value!r}")
    if not TEMPO_SCALE_MIN <= scale <= TEMPO_SCALE_MAX:
        raise TempoScaleError(
            f"tempo scale out of range "
            f"[{TEMPO_SCALE_MIN}..{TEMPO_SCALE_MAX}]: {value!r}"
        )
    return scale


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One playable output command at a song-time (seconds from start)."""

    time: float
    command: object  # canonical OutputCommand (kept loose to avoid a cycle)

    def __post_init__(self) -> None:
        if not isinstance(self.time, (int, float)) or isinstance(self.time, bool):
            raise TypeError(
                f"time must be a number, got {type(self.time).__name__}"
            )
        if self.time < 0:
            raise ValueError(f"time must be non-negative: {self.time!r}")


@dataclass(frozen=True, slots=True)
class Timeline:
    """Immutable, sorted sequence of :class:`TimelineEvent` records.

    ``duration`` is the song time of the last event (0.0 when empty) —
    the moment the song is considered complete.
    """

    events: tuple[TimelineEvent, ...]
    duration: float = 0.0
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple):
            raise TypeError(
                f"events must be a tuple, got {type(self.events).__name__}"
            )
        for event in self.events:
            if not isinstance(event, TimelineEvent):
                raise TypeError(
                    f"events must contain TimelineEvent, got {type(event).__name__}"
                )
        times = [event.time for event in self.events]
        if times != sorted(times):
            raise ValueError("timeline events must be sorted by time")
        if not isinstance(self.duration, (int, float)) or isinstance(
            self.duration, bool
        ):
            raise TypeError(
                f"duration must be a number, got {type(self.duration).__name__}"
            )
        if not isinstance(self.source, str):
            raise TypeError(
                f"source must be a string, got {type(self.source).__name__}"
            )


def load_timeline_if_playable(path: object):
    """Loaded canonical :class:`Timeline`, or ``None`` when unplayable (M5).

    Same sanctioned-interpretation boundary as :func:`timeline_loadable`
    for callers that also need the loaded events (package consistency
    checks) without referencing the file-interpretation error surface.
    """
    try:
        return load_timeline(path)
    except MidiFileError:
        return None


def timeline_loadable(path: object) -> bool:
    """True when ``path`` loads as a playable canonical timeline (M5).

    Sanctioned interpretation boundary: other subsystems (imported
    practice packages) ask a yes/no question instead of interpreting
    MIDI themselves, keeping file interpretation confined to this
    package per the P009/P010 containment invariant.
    """
    try:
        load_timeline(path)
    except MidiFileError:
        return False
    return True


def _delta_ticks(msg: object) -> int:
    """Extract a valid non-negative delta tick count from a message."""
    raw = getattr(msg, "time", None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise MidiFileError(
            f"invalid delta time {raw!r} ({type(raw).__name__}); "
            "expected a non-negative number of ticks"
        )
    if raw != int(raw) or raw < 0:
        raise MidiFileError(f"invalid delta time: {raw!r}")
    return int(raw)


def _phase(msg_type: str, msg: object) -> int:
    """Same-tick ordering phase: releases/pedal/tempo (0) before attacks (1)."""
    if msg_type == "note_on" and int(getattr(msg, "velocity", 0)) > 0:
        return 1
    return 0


def _emit(msg_type: str, msg: object, time: float) -> TimelineEvent | None:
    """Convert one SMF message into a canonical timeline event (or None)."""
    try:
        channel = int(getattr(msg, "channel", 0)) + 1  # SMF 0-based -> canonical
        if msg_type == "note_on":
            velocity = int(msg.velocity)
            if velocity > 0:
                return TimelineEvent(time, build_note_on(channel, int(msg.note), velocity))
            # velocity-0 note_on is a NOTE_OFF per the MIDI spec
            return TimelineEvent(time, build_note_off(channel, int(msg.note), 0))
        if msg_type == "note_off":
            return TimelineEvent(
                time, build_note_off(channel, int(msg.note), int(msg.velocity))
            )
        if msg_type == "control_change" and int(msg.control) == _SUSTAIN_CC:
            return TimelineEvent(time, build_sustain(channel, int(msg.value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise MidiFileError(f"invalid {msg_type} message: {exc}") from exc
    return None


def build_timeline(ticks_per_beat: int, tracks) -> Timeline:
    """Pure SMF-agnostic timeline builder (no mido required).

    ``tracks`` is a sequence of iterables of duck-typed messages with a
    delta-tick ``time`` attribute (the mido MidiFile surface). See the
    module docstring for the exact ordering and tempo semantics.
    """
    if (
        not isinstance(ticks_per_beat, int)
        or isinstance(ticks_per_beat, bool)
        or ticks_per_beat <= 0
    ):
        raise MidiFileError(
            f"unsupported time division: {ticks_per_beat!r} "
            "(SMPTE-timed files are not supported)"
        )

    merged: list[tuple[int, int, int, int, object]] = []
    for track_index, track in enumerate(tracks):
        absolute_tick = 0
        order = 0
        for msg in track:
            absolute_tick += _delta_ticks(msg)
            msg_type = getattr(msg, "type", None)
            if msg_type in ("note_on", "note_off", "control_change", "set_tempo"):
                merged.append(
                    (
                        absolute_tick,
                        _phase(msg_type, msg),
                        track_index,
                        order,
                        msg,
                    )
                )
            order += 1
    merged.sort(key=lambda entry: entry[:4])

    events: list[TimelineEvent] = []
    tempo = _DEFAULT_TEMPO
    last_tick = 0
    seconds = 0.0
    for tick, _phase_value, _track_index, _order, msg in merged:
        seconds += (tick - last_tick) * (tempo / 1_000_000.0) / ticks_per_beat
        last_tick = tick
        msg_type = msg.type
        if msg_type == "set_tempo":
            tempo = int(msg.tempo)
            continue
        event = _emit(msg_type, msg, seconds)
        if event is not None:
            events.append(event)

    duration = events[-1].time if events else 0.0
    return Timeline(events=tuple(events), duration=duration)


def _validate_smf_header(file_path: Path) -> None:
    """Read-only header sanity check before any parsing (fail closed).

    Rejects non-SMF content, SMF format 2 (independent tracks have no
    meaningful merged timeline) and SMPTE time division up front — the
    full parse then happens on the canonical mido stack.
    """
    try:
        with open(file_path, "rb") as handle:
            header = handle.read(14)
    except OSError as exc:
        raise MidiFileError(f"cannot read MIDI file {file_path}: {exc}") from exc
    if len(header) < 14 or header[:4] != b"MThd":
        raise MidiFileError(
            f"{file_path}: not a Standard MIDI File (missing MThd header)"
        )
    fmt = int.from_bytes(header[8:10], "big")
    division = int.from_bytes(header[12:14], "big")
    if fmt == 2:
        raise MidiFileError(
            f"{file_path}: SMF format 2 (independent tracks) is not supported"
        )
    if division & 0x8000:
        raise MidiFileError(
            f"{file_path}: SMPTE-timed files are not supported"
        )


def load_timeline(path: object, tempo_scale: float = 1.0) -> Timeline:
    """Load a Standard MIDI File (read-only) and build its timeline.

    Validates ``tempo_scale`` first (fail closed before any I/O), then
    the raw SMF header, then parses via the canonical mido stack (lazy
    import — the only mido import outside ``piano_core.midi``). The file
    is never modified.
    """
    scale = validate_tempo_scale(tempo_scale)
    del scale  # validated only; scaling happens at scheduling time
    file_path = Path(str(path))
    _validate_smf_header(file_path)
    try:
        import mido  # lazy: keeps mido off the local-test path
    except ImportError as exc:
        raise MidiFileError(
            f"mido is required to read MIDI files and is not installed "
            f"(cannot parse {file_path})"
        ) from exc
    try:
        midi_file = mido.MidiFile(str(file_path))
    except Exception as exc:
        raise MidiFileError(f"cannot load MIDI file {file_path}: {exc}") from exc
    if getattr(midi_file, "type", 0) == 2:
        raise MidiFileError(
            f"{file_path}: SMF format 2 (independent tracks) is not supported"
        )
    try:
        tracks = list(midi_file.tracks)
    except Exception as exc:  # pragma: no cover - defensive
        raise MidiFileError(f"{file_path}: cannot read tracks: {exc}") from exc
    timeline = build_timeline(getattr(midi_file, "ticks_per_beat", 0), tracks)
    return Timeline(
        events=timeline.events,
        duration=timeline.duration,
        source=str(file_path),
    )


# ----------------------------------------------------------------------
# PIANO-LEARN-001: Learn source analysis (additive; same raw-mido
# boundary as load_timeline). Produces plain note dicts + structural
# facts for the Learn adapters; changes nothing in the accepted
# timeline functions above.

KEYBOARD_MIN_ANALYSIS = 21
KEYBOARD_MAX_ANALYSIS = 108
PERCUSSION_CHANNEL = 9

#: GM programs considered keyboard-family for Learn source content.
_KEYBOARD_PROGRAMS = frozenset(range(0, 12))

#: Note count at which a non-keyboard program rejects a Learn source.
_NON_PIANO_NOTE_FLOOR = 8


class MidiAnalysisError(RuntimeError):
    """A MIDI source file could not be analyzed for Learn content."""


def _ticks_to_seconds_factory(tempo_events, ticks_per_beat):
    points = sorted(tempo_events, key=lambda e: e[0])

    def convert(tick):
        seconds = 0.0
        last_tick = 0
        last_tempo = 500_000
        for point_tick, tempo in points:
            if point_tick >= tick:
                break
            seconds += (point_tick - last_tick) * last_tempo / (ticks_per_beat * 1_000_000)
            last_tick = point_tick
            last_tempo = tempo
        seconds += (tick - last_tick) * last_tempo / (ticks_per_beat * 1_000_000)
        return seconds

    return convert


def analyze_midi_file(path):
    """Analyze one MIDI file into note dicts plus structural facts.

    Returns ``(notes, facts, problems)``. Hanging note_ons, true orphan
    note_offs, non-keyboard program content and out-of-range notes are
    reported as problems; redundant terminations for already-closed
    notes (engraver artifact, no sounding change) are tolerated and
    counted as evidence.
    """
    import mido  # raw boundary, sanctioned site (PIANO-P009 loader module)

    try:
        mid = MidiFile(str(Path(path)))
    except Exception as exc:  # mido raises several exception types
        raise MidiAnalysisError(f"MIDI parse failure: {exc}") from exc

    if mid.type == 2:
        raise MidiAnalysisError("MIDI format 2 (asynchronous tracks) is unsupported")

    ticks_per_beat = mid.ticks_per_beat or 480
    tempo_events = []
    time_signature = None
    programs_by_track = {}

    for index, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                tempo_events.append((tick, int(msg.tempo)))
            elif msg.type == "time_signature":
                if time_signature is None:
                    time_signature = (int(msg.numerator), int(msg.denominator))
            elif msg.type == "program_change":
                programs_by_track.setdefault(index, set()).add(int(msg.program))
    if not tempo_events:
        tempo_events = [(0, 500_000)]
    to_seconds = _ticks_to_seconds_factory(tempo_events, ticks_per_beat)

    track_notes = {}
    problems = []
    redundant_offs = 0
    for index, track in enumerate(mid.tracks):
        tick = 0
        open_notes = {}
        seen_notes = set()
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                key = (int(msg.channel), int(msg.note))
                seen_notes.add(key)
                open_notes.setdefault(key, []).append((tick, int(msg.velocity)))
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (int(msg.channel), int(msg.note))
                queue = open_notes.get(key)
                if not queue:
                    if key in seen_notes:
                        redundant_offs += 1
                        continue
                    problems.append(
                        f"track {index}: note_off for note {msg.note} on channel "
                        f"{msg.channel} without any matching note_on"
                    )
                    continue
                onset_tick, velocity = queue.pop(0)
                if tick <= onset_tick:
                    problems.append(
                        f"track {index}: non-positive duration for note {msg.note}"
                    )
                    continue
                track_notes.setdefault(index, []).append(
                    (onset_tick, tick, int(msg.note), velocity, int(msg.channel))
                )
        for (channel, note), queue in open_notes.items():
            for _onset_tick, _velocity in queue:
                problems.append(
                    f"track {index}: note {note} on channel {channel} never "
                    "receives note_off (hanging note)"
                )

    facts = {
        "tempo_bpm": round(60_000_000 / min(t for _, t in tempo_events), 3),
        "time_signature": time_signature,
        "redundant_note_offs": redundant_offs,
    }
    if not track_notes:
        facts["content_track_count"] = 0
        return [], facts, ["file contains no notes"] + problems

    content_tracks = sorted(
        track
        for track, events in track_notes.items()
        if any(channel != PERCUSSION_CHANNEL for _, _, _, _, channel in events)
    )
    facts["content_track_count"] = len(content_tracks)

    hand_for_track = {}
    if len(content_tracks) == 2:
        medians = {}
        for track in content_tracks:
            pitches = sorted(e[2] for e in track_notes[track] if e[4] != PERCUSSION_CHANNEL)
            medians[track] = pitches[len(pitches) // 2]
        low_track, high_track = sorted(medians, key=lambda t: medians[t])
        hand_for_track[low_track] = "LH"
        hand_for_track[high_track] = "RH"
        facts["hands_evidence"] = (
            f"two content tracks split by register (track {low_track} median "
            f"midi {medians[low_track]} -> LH, track {high_track} median midi "
            f"{medians[high_track]} -> RH)"
        )
    else:
        facts["hands_evidence"] = (
            f"{len(content_tracks)} content tracks; hand split not derivable"
        )

    for track in content_tracks:
        for program in programs_by_track.get(track, set()):
            if program in _KEYBOARD_PROGRAMS:
                continue
            count = sum(1 for e in track_notes[track] if e[4] != PERCUSSION_CHANNEL)
            if count >= _NON_PIANO_NOTE_FLOOR:
                problems.append(
                    f"track {track} uses non-keyboard GM program {program} "
                    f"with {count} notes (multi-instrument arrangement)"
                )

    notes = []
    for track in content_tracks:
        for onset_tick, off_tick, note, velocity, channel in track_notes[track]:
            if channel == PERCUSSION_CHANNEL:
                continue  # percussion is not piano practice material
            if not (KEYBOARD_MIN_ANALYSIS <= note <= KEYBOARD_MAX_ANALYSIS):
                problems.append(f"track {track}: note {note} outside 88-key range")
                continue
            notes.append(
                {
                    "onset": round(to_seconds(onset_tick), 6),
                    "offset": round(to_seconds(off_tick), 6),
                    "midi": int(note),
                    "velocity": int(velocity),
                    "hand": hand_for_track.get(track),
                    "track": int(track),
                }
            )
    if not notes:
        problems.append("no keyboard notes after percussion and range filtering")

    notes.sort(key=lambda n: (n["onset"], n["midi"]))
    return notes, facts, problems
