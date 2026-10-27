"""Canonical note-span score projection (PIANO-UI-005, ADR-016).

The P009 :class:`~piano_core.playback.timeline.Timeline` is the one
musical truth: ordered output commands at song-time seconds. Everything
a visualization needs musically is either already there or derivable
from it deterministically. This module performs exactly one derivation
at the Core boundary (ADR-014 §4 "one canonical musical timeline; many
projections"): it pairs NOTE_ON/NOTE_OFF commands into immutable note
spans (onset, duration, pitch, channel, velocity) so browsers can render
falling notes, sheet-oriented views and hybrid layouts WITHOUT ever
parsing raw MIDI.

What this projection honestly contains (and nothing more):

- note onsets and durations in canonical song-time seconds;
- MIDI pitch 0..127 and canonical channel 1..16;
- attack velocity;
- deterministic pairing diagnostics (retriggered, unpaired,
  unterminated attacks, percussion-channel and out-of-keyboard counts).

What it deliberately does NOT contain (no canonical source exists — see
ADR-014 §1.1 and ADR-015 §1): track identity, tempo map, ticks, time
signatures, measures, beats, hand assignment, fingering, voices,
phrasing or enharmonic spelling. Renderers must not invent them.

Pairing rules (deterministic, consistent with the P009 ordering rule
"releases sort before attacks at the same tick"):

- a NOTE_ON opens one span per ``(channel, note)``;
- a NOTE_ON for a key whose span is still open closes the previous span
  AT the new onset (same-tick re-strike ⇒ previous duration clipped,
  possibly zero) and counts ``retriggered_note_ons``;
- a NOTE_OFF closes the open span (zero duration is legal and preserved);
- a NOTE_OFF with no open span counts ``unpaired_note_offs`` and is
  dropped;
- spans still open at the end of the timeline are closed at the song
  duration (that is how long they would sound under the engine's
  completion cleanup) and counted as ``unterminated_note_ons``.

Timing never schedules anything: this projection is read-only
observability for presentation, exactly like the practice plan.
"""

from __future__ import annotations

import logging
import math
import threading
import hashlib
import json
from collections import OrderedDict
from typing import Callable

from piano_core.midi.output_events import OutputCommandType
from piano_core.playback.timeline import Timeline, load_timeline

__all__ = [
    "MAX_SCORE_SPANS",
    "ScoreProjection",
    "ScoreService",
    "ScoreSpan",
    "ScoreTooLargeError",
    "build_score_projection",
]


#: Upper bound of spans one projection (and therefore one API payload)
#: may carry. Larger songs fail with a typed bounded error instead of an
#: unbounded response; chunked transfer (ADR-014 §4) stays a separately
#: authorized future extension.
MAX_SCORE_SPANS = 20_000


class ScoreTooLargeError(RuntimeError):
    """The score projection exceeds the bounded payload limit. Fail closed."""


class ScoreSpan:
    """One immutable paired note span (zero duration is legal)."""

    __slots__ = ("index", "note", "channel", "onset_seconds", "duration_seconds", "velocity")

    def __init__(
        self,
        index: int,
        note: int,
        channel: int,
        onset_seconds: float,
        duration_seconds: float,
        velocity: int,
    ) -> None:
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError(f"span index must be a non-negative int: {index!r}")
        if not isinstance(note, int) or isinstance(note, bool) or not 0 <= note <= 127:
            raise ValueError(f"span note must be an int in 0..127: {note!r}")
        if (
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or not 1 <= channel <= 16
        ):
            raise ValueError(f"span channel must be an int in 1..16: {channel!r}")
        for name, value in (
            ("onset_seconds", onset_seconds),
            ("duration_seconds", duration_seconds),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")
        if (
            not isinstance(velocity, int)
            or isinstance(velocity, bool)
            or not 1 <= velocity <= 127
        ):
            raise ValueError(f"span velocity must be an int in 1..127: {velocity!r}")
        self.index = index
        self.note = note
        self.channel = channel
        self.onset_seconds = float(onset_seconds)
        self.duration_seconds = float(duration_seconds)
        self.velocity = velocity

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ScoreSpan):
            return NotImplemented
        return (
            self.index == other.index
            and self.note == other.note
            and self.channel == other.channel
            and self.onset_seconds == other.onset_seconds
            and self.duration_seconds == other.duration_seconds
            and self.velocity == other.velocity
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.index,
                self.note,
                self.channel,
                self.onset_seconds,
                self.duration_seconds,
                self.velocity,
            )
        )

    def __repr__(self) -> str:
        return (
            f"ScoreSpan(#{self.index} note={self.note} ch={self.channel} "
            f"t={self.onset_seconds:.3f}s dur={self.duration_seconds:.3f}s "
            f"vel={self.velocity})"
        )


class ScoreProjection:
    """Immutable paired-note projection of one canonical timeline."""

    __slots__ = (
        "song_id",
        "title",
        "source_version",
        "duration_seconds",
        "spans",
        "percussion_note_ons",
        "outside_keyboard_note_ons",
        "unpaired_note_offs",
        "retriggered_note_ons",
        "unterminated_note_ons",
        "max_span_duration_seconds",
    )

    def __init__(
        self,
        song_id: str,
        title: str,
        duration_seconds: float,
        spans: tuple[ScoreSpan, ...],
        *,
        percussion_note_ons: int = 0,
        outside_keyboard_note_ons: int = 0,
        unpaired_note_offs: int = 0,
        retriggered_note_ons: int = 0,
        unterminated_note_ons: int = 0,
    ) -> None:
        if not isinstance(song_id, str) or not song_id:
            raise ValueError("song_id must be a non-empty string")
        if not isinstance(title, str):
            raise ValueError("title must be a string")
        if (
            not isinstance(duration_seconds, (int, float))
            or isinstance(duration_seconds, bool)
            or not math.isfinite(duration_seconds)
            or duration_seconds < 0
        ):
            raise ValueError("duration_seconds must be a non-negative number")
        if not isinstance(spans, tuple):
            raise TypeError("spans must be a tuple of ScoreSpan")
        for span in spans:
            if not isinstance(span, ScoreSpan):
                raise TypeError("spans must contain ScoreSpan")
        for name, value in (
            ("percussion_note_ons", percussion_note_ons),
            ("outside_keyboard_note_ons", outside_keyboard_note_ons),
            ("unpaired_note_offs", unpaired_note_offs),
            ("retriggered_note_ons", retriggered_note_ons),
            ("unterminated_note_ons", unterminated_note_ons),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        if len(spans) > MAX_SCORE_SPANS:
            raise ScoreTooLargeError(
                f"score projection exceeds the bounded limit "
                f"({len(spans)} spans > {MAX_SCORE_SPANS})"
            )
        self.song_id = song_id
        self.title = title
        self.duration_seconds = float(duration_seconds)
        self.spans = spans
        self.percussion_note_ons = percussion_note_ons
        self.outside_keyboard_note_ons = outside_keyboard_note_ons
        self.unpaired_note_offs = unpaired_note_offs
        self.retriggered_note_ons = retriggered_note_ons
        self.unterminated_note_ons = unterminated_note_ons
        self.max_span_duration_seconds = (
            max((span.duration_seconds for span in spans), default=0.0)
        )
        def canonical_number(value: float) -> float:
            if not math.isfinite(value):
                raise ValueError("score fingerprint values must be finite")
            return 0.0 if value == 0 else value

        source_document = {
            "duration_seconds": canonical_number(self.duration_seconds),
            "spans": [
                [
                    span.index,
                    span.note,
                    span.channel,
                    canonical_number(span.onset_seconds),
                    canonical_number(span.duration_seconds),
                    span.velocity,
                ]
                for span in sorted(
                    spans,
                    key=lambda item: (
                        item.onset_seconds,
                        item.index,
                        item.note,
                        item.channel,
                        item.duration_seconds,
                        item.velocity,
                    ),
                )
            ],
        }
        encoded = json.dumps(
            source_document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.source_version = f"score-sha256:{hashlib.sha256(encoded).hexdigest()}"

    @property
    def span_count(self) -> int:
        return len(self.spans)

    def __repr__(self) -> str:
        return (
            f"ScoreProjection(song={self.song_id!r} spans={self.span_count} "
            f"duration={self.duration_seconds:.3f}s)"
        )


#: GM percussion channel in the canonical 1..16 convention.
_PERCUSSION_CHANNEL = 10

#: Verified physical keyboard range (same constants as practice).
_KEYBOARD_MIN = 21
_KEYBOARD_MAX = 108


def build_score_projection(
    timeline: Timeline, song_id: str, title: str
) -> ScoreProjection:
    """Pair one canonical timeline into deterministic note spans.

    Deterministic for a given timeline: same input -> same spans, same
    indices, same diagnostics. The timeline is read-only and never
    mutated. Percussion-channel and out-of-keyboard attacks are still
    paired and included (they are real output events; presentation
    filters by range), but they are counted for honest observability.
    """
    if not isinstance(timeline, Timeline):
        raise TypeError(
            f"build_score_projection requires a Timeline, got {type(timeline).__name__}"
        )

    open_spans: dict[tuple[int, int], tuple[int, int, int, float]] = {}
    closed: list[ScoreSpan] = []
    percussion = 0
    outside = 0
    unpaired_offs = 0
    retriggered = 0
    unterminated = 0

    def close(key: tuple[int, int], end_seconds: float) -> None:
        if len(closed) >= MAX_SCORE_SPANS:
            raise ScoreTooLargeError(
                f"score projection exceeds the bounded limit ({MAX_SCORE_SPANS} spans)"
            )
        note, channel, velocity, onset = open_spans.pop(key)
        closed.append(
            ScoreSpan(
                len(closed), note, channel, onset, max(0.0, end_seconds - onset), velocity
            )
        )

    for event in timeline.events:
        command = event.command
        ctype = command.type
        if ctype is OutputCommandType.NOTE_ON:
            note = command.note
            channel = command.channel
            if channel == _PERCUSSION_CHANNEL:
                percussion += 1
            if not _KEYBOARD_MIN <= note <= _KEYBOARD_MAX:
                outside += 1
            key = (channel, note)
            if key in open_spans:
                # Same-key re-strike: the previous span can no longer end
                # later than this onset (P009 already ordered the release
                # before the attack when they share a tick).
                close(key, event.time)
                retriggered += 1
            open_spans[key] = (note, channel, command.velocity, event.time)
        elif ctype is OutputCommandType.NOTE_OFF:
            key = (command.channel, command.note)
            if key in open_spans:
                close(key, event.time)
            else:
                unpaired_offs += 1

    # Spans still open at the end sound until the engine's completion
    # cleanup at song end; the visual truth is "held to the end".
    for key in sorted(open_spans):
        close(key, timeline.duration)
        unterminated += 1

    # Publish onset order, never release order. Original close index is
    # the final stable tie-breaker for otherwise identical spans.
    closed.sort(key=lambda span: (span.onset_seconds, span.channel, span.note, span.index))
    for index, span in enumerate(closed):
        span.index = index
    spans = tuple(closed)
    if len(spans) > MAX_SCORE_SPANS:
        raise ScoreTooLargeError(
            f"score projection exceeds the bounded limit "
            f"({len(spans)} spans > {MAX_SCORE_SPANS})"
        )
    return ScoreProjection(
        song_id,
        title,
        timeline.duration,
        spans,
        percussion_note_ons=percussion,
        outside_keyboard_note_ons=outside,
        unpaired_note_offs=unpaired_offs,
        retriggered_note_ons=retriggered,
        unterminated_note_ons=unterminated,
    )


class ScoreService:
    """Bounded read-only projection cache over the canonical library.

    Owns no MIDI surface and no scheduling: it resolves ``song_id``
    through the canonical :class:`~piano_core.library.service.LibraryService`,
    loads the canonical P009 timeline and projects it. Because playback
    and practice already load the same timeline when they start,
    :meth:`timeline_loader` can be injected as their loader so the cache
    is warm exactly when a song becomes active — with zero changes to
    the accepted managers. The loader parks loaded timelines by resolved
    path (no reverse song-ID derivation exists); ``get`` then projects
    without re-parsing whenever the resolved paths match.
    """

    def __init__(
        self,
        library,
        *,
        loader: Callable[..., Timeline] = load_timeline,
        builder: Callable[[Timeline, str, str], ScoreProjection] = build_score_projection,
        max_cached: int = 8,
        max_pending: int = 8,
        logger: logging.Logger | None = None,
    ) -> None:
        if not isinstance(max_cached, int) or isinstance(max_cached, bool) or max_cached < 1:
            raise ValueError("max_cached must be a positive int")
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending < 1:
            raise ValueError("max_pending must be a positive int")
        self._library = library
        self._load = loader
        self._build = builder
        self._max_cached = max_cached
        self._max_pending = max_pending
        self._log = logger or logging.getLogger("piano_core.playback.score")
        self._lock = threading.Lock()
        self._timeline_generation = 0
        self._cache: "OrderedDict[str, ScoreProjection]" = OrderedDict()
        self._pending: "OrderedDict[str, Timeline]" = OrderedDict()

    @property
    def available(self) -> bool:
        """True when the canonical library is wired."""
        return self._library is not None

    def get(self, song_id: str) -> ScoreProjection:
        """Return the (cached) canonical score projection for one song.

        Raises the library's own typed errors (unknown song, missing
        source, unplayable file) and :class:`ScoreTooLargeError` for
        oversized material — the API layer translates them.
        """
        if not isinstance(song_id, str) or not song_id.strip():
            raise ValueError("song_id must be a non-empty string")
        if self._library is None:
            from piano_core.library.model import LibraryConfigError

            raise LibraryConfigError(
                "score projection is unavailable (canonical library is not ready)"
            )
        song_id = song_id.strip()
        path = self._library.resolve(song_id)
        cache_key = str(path)
        record = self._library.get(song_id)
        title = (record.title or record.filename) if record is not None else song_id
        while True:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    self._cache.move_to_end(cache_key)
                    return cached
                generation = self._timeline_generation
                timeline = self._pending.get(cache_key)
            if timeline is None:
                timeline = self._load(path)
            projection = self._build(timeline, song_id, title)
            with self._lock:
                # A loader may have supplied new truth while projection
                # ran outside the lock. Never publish/cache that old result.
                if generation != self._timeline_generation:
                    continue
                self._pending.pop(cache_key, None)
                self._cache[cache_key] = projection
                self._cache.move_to_end(cache_key)
                while len(self._cache) > self._max_cached:
                    self._cache.popitem(last=False)
                return projection

    def timeline_loader(self):
        """Wrap the canonical loader so active songs warm the cache.

        Returns a drop-in ``load_timeline`` replacement: it loads the
        timeline exactly as before (same arguments, same semantics) and
        best-effort parks it for the next ``get`` on the same resolved
        path. Parking/projection failures never fail playback/practice.
        """

        def loader(path, *args, **kwargs):
            timeline = self._load(path, *args, **kwargs)
            try:
                with self._lock:
                    self._timeline_generation += 1
                    self._pending[str(path)] = timeline
                    # A newly loaded canonical timeline supersedes every
                    # prior projection for this source, even at the same path.
                    self._cache.pop(str(path), None)
                    self._pending.move_to_end(str(path))
                    while len(self._pending) > self._max_pending:
                        self._pending.popitem(last=False)
            except Exception:  # pragma: no cover - defensive
                self._log.exception("score cache parking failed (non-fatal)")
            return timeline

        return loader

    def _ingest(self, cache_key: str, projection: ScoreProjection) -> None:
        with self._lock:
            self._cache[cache_key] = projection
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._max_cached:
                self._cache.popitem(last=False)
