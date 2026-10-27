"""PIANO-UI-005: canonical note-span score projection tests.

Deterministic pairing semantics (build_score_projection), the bounded
projection model, and the ScoreService cache/warming behavior — all
pure/in-memory, no mido required (timelines are built with the pure
duck-typed builder).
"""

import unittest

from piano_core.midi.output_events import note_off, note_on, sustain
from piano_core.playback.score import (
    MAX_SCORE_SPANS,
    ScoreProjection,
    ScoreService,
    ScoreSpan,
    ScoreTooLargeError,
    build_score_projection,
)
from piano_core.playback.timeline import Timeline, TimelineEvent


def _timeline(*pairs, duration=None, source="score.mid"):
    """Build a canonical timeline from (time, command) pairs."""
    events = tuple(TimelineEvent(time, command) for time, command in pairs)
    if duration is None:
        duration = events[-1].time if events else 0.0
    return Timeline(events=events, duration=duration, source=source)


def _seq(*times_notes, duration=None):
    """Convenience: NOTE_ON/NOTE_OFF pairs (t_on, note, vel, t_off)."""
    pairs = []
    for t_on, note, vel, t_off in times_notes:
        pairs.append((t_on, note_on(1, note, vel)))
        pairs.append((t_off, note_off(1, note, 0)))
    pairs.sort(key=lambda item: item[0])
    return _timeline(*pairs, duration=duration)


class BuildScoreProjectionTests(unittest.TestCase):
    def test_overlapping_spans_publish_onset_order_without_timeline_mutation(self):
        timeline = _seq((0, 60, 90, 30), (10, 64, 80, 11), (20, 67, 70, 21))
        before = timeline.events
        spans = build_score_projection(timeline, "s", "Overlap").spans
        self.assertEqual([(s.index, s.note, s.onset_seconds, s.duration_seconds) for s in spans],
                         [(0, 60, 0, 30), (1, 64, 10, 1), (2, 67, 20, 1)])
        self.assertEqual(timeline.events, before)

    def test_simultaneous_spans_have_channel_pitch_tie_breakers(self):
        timeline = _timeline((0, note_on(2, 60, 90)), (0, note_on(1, 67, 90)),
                             (0, note_on(1, 60, 90)), (1, note_off(1, 67, 0)),
                             (2, note_off(2, 60, 0)), (3, note_off(1, 60, 0)))
        spans = build_score_projection(timeline, "s", "Chord").spans
        self.assertEqual([(s.channel, s.note) for s in spans], [(1, 60), (1, 67), (2, 60)])

    def test_reattack_and_unterminated_long_note_are_onset_ordered(self):
        timeline = _timeline((0, note_on(1, 60, 90)), (1, note_on(1, 64, 80)),
                             (2, note_on(1, 64, 100)), (3, note_off(1, 64, 0)), duration=10)
        p = build_score_projection(timeline, "s", "Held")
        self.assertEqual([(s.onset_seconds, s.duration_seconds) for s in p.spans], [(0, 10), (1, 1), (2, 1)])
        self.assertEqual((p.retriggered_note_ons, p.unterminated_note_ons), (1, 1))

    def test_oversize_stops_span_construction_at_limit(self):
        from unittest.mock import patch
        import piano_core.playback.score as module
        original = module.ScoreSpan
        constructed = []
        class CountingSpan(original):
            def __init__(self, *args, **kwargs):
                constructed.append(1)
                super().__init__(*args, **kwargs)
        timeline = _seq(*[(i, 60, 90, i + 0.5) for i in range(25000)])
        with patch.object(module, "ScoreSpan", CountingSpan):
            with self.assertRaises(ScoreTooLargeError):
                build_score_projection(timeline, "s", "Large")
        self.assertEqual(len(constructed), MAX_SCORE_SPANS)

    def test_single_note_span(self):
        projection = build_score_projection(
            _seq((0.0, 60, 90, 1.5)), "s1", "Song"
        )
        self.assertEqual(projection.span_count, 1)
        span = projection.spans[0]
        self.assertEqual(
            (span.index, span.note, span.channel, span.velocity),
            (0, 60, 1, 90),
        )
        self.assertEqual(span.onset_seconds, 0.0)
        self.assertEqual(span.duration_seconds, 1.5)
        self.assertEqual(projection.duration_seconds, 1.5)
        self.assertEqual(projection.max_span_duration_seconds, 1.5)

    def test_chord_same_onset_distinct_pitches(self):
        projection = build_score_projection(
            _seq((1.0, 60, 80, 2.0), (1.0, 64, 70, 2.0), (1.0, 67, 60, 2.0)),
            "s1",
            "Chord",
        )
        self.assertEqual(projection.span_count, 3)
        self.assertEqual({span.note for span in projection.spans}, {60, 64, 67})
        for span in projection.spans:
            self.assertEqual(span.onset_seconds, 1.0)
            self.assertEqual(span.duration_seconds, 1.0)

    def test_zero_duration_note_is_preserved_not_dropped(self):
        projection = build_score_projection(
            _seq((0.0, 60, 90, 0.0)), "s1", "Zero"
        )
        self.assertEqual(projection.span_count, 1)
        self.assertEqual(projection.spans[0].duration_seconds, 0.0)

    def test_same_tick_restrike_clips_previous_span(self):
        # NOTE_ON at t=0, then ANOTHER NOTE_ON at t=1 without an intervening
        # NOTE_OFF: the first span closes at the new onset (clipped).
        timeline = _timeline(
            (0.0, note_on(1, 60, 90)),
            (1.0, note_on(1, 60, 100)),
            (2.0, note_off(1, 60, 0)),
        )
        projection = build_score_projection(timeline, "s1", "Restrike")
        self.assertEqual(projection.span_count, 2)
        self.assertEqual(projection.spans[0].onset_seconds, 0.0)
        self.assertEqual(projection.spans[0].duration_seconds, 1.0)
        self.assertEqual(projection.spans[1].onset_seconds, 1.0)
        self.assertEqual(projection.spans[1].duration_seconds, 1.0)
        self.assertEqual(projection.retriggered_note_ons, 1)

    def test_unpaired_note_off_is_counted_and_dropped(self):
        timeline = _timeline(
            (0.5, note_off(1, 72, 0)),
            (1.0, note_on(1, 60, 80)),
            (2.0, note_off(1, 60, 0)),
        )
        projection = build_score_projection(timeline, "s1", "Loose")
        self.assertEqual(projection.span_count, 1)
        self.assertEqual(projection.spans[0].note, 60)
        self.assertEqual(projection.unpaired_note_offs, 1)

    def test_unterminated_note_on_closes_at_song_duration(self):
        timeline = _timeline(
            (0.0, note_on(1, 60, 80)),
            (1.0, note_on(1, 64, 80)),
            (2.0, note_off(1, 60, 0)),
            (2.0, note_off(1, 64, 0)),
            (4.0, note_on(1, 67, 80)),  # never released
        )
        projection = build_score_projection(timeline, "s1", "Held")
        self.assertEqual(projection.span_count, 3)
        held = projection.spans[2]
        self.assertEqual(held.note, 67)
        self.assertEqual(held.onset_seconds, 4.0)
        self.assertEqual(held.duration_seconds, 0.0)  # onset == duration
        self.assertEqual(projection.unterminated_note_ons, 1)

    def test_unterminated_at_last_event_gets_remaining_time(self):
        timeline = _timeline(
            (0.0, note_on(1, 60, 80)),
            (4.0, note_on(1, 67, 80)),  # never released, duration 6.0
            (4.0, note_off(1, 60, 0)),
        )
        timeline = Timeline(
            events=timeline.events, duration=6.0, source="x.mid"
        )
        projection = build_score_projection(timeline, "s1", "Held2")
        self.assertEqual(projection.spans[1].duration_seconds, 2.0)
        self.assertEqual(projection.unterminated_note_ons, 1)

    def test_percussion_and_out_of_range_are_counted_and_kept(self):
        timeline = _timeline(
            (0.0, note_on(10, 36, 100)),   # GM percussion
            (0.5, note_off(10, 36, 0)),
            (1.0, note_on(1, 20, 90)),    # below keyboard (A0 is 21)
            (1.5, note_off(1, 20, 0)),
            (2.0, note_on(1, 109, 90)),   # above keyboard (C8 is 108)
            (2.5, note_off(1, 109, 0)),
            (3.0, note_on(1, 108, 90)),   # last key
            (3.5, note_off(1, 108, 0)),
            (4.0, note_on(1, 21, 90)),    # first key
            (4.5, note_off(1, 21, 0)),
        )
        projection = build_score_projection(timeline, "s1", "Edges")
        self.assertEqual(projection.span_count, 5)
        self.assertEqual(projection.percussion_note_ons, 1)
        self.assertEqual(projection.outside_keyboard_note_ons, 2)

    def test_empty_timeline_is_a_valid_empty_projection(self):
        projection = build_score_projection(
            Timeline(events=(), duration=0.0, source="e.mid"), "s1", "Empty"
        )
        self.assertEqual(projection.span_count, 0)
        self.assertEqual(projection.duration_seconds, 0.0)
        self.assertEqual(projection.max_span_duration_seconds, 0.0)

    def test_sustain_commands_are_ignored(self):
        timeline = _timeline(
            (0.0, sustain(1, 127)),
            (1.0, note_on(1, 60, 80)),
            (1.5, sustain(1, 0)),
            (2.0, note_off(1, 60, 0)),
        )
        projection = build_score_projection(timeline, "s1", "Pedal")
        self.assertEqual(projection.span_count, 1)
        self.assertEqual(projection.spans[0].duration_seconds, 1.0)

    def test_deterministic_indices_and_order(self):
        timeline = _seq(
            (1.0, 64, 80, 2.0),
            (0.0, 60, 90, 0.5),
            (2.0, 67, 70, 3.0),
        )
        one = build_score_projection(timeline, "s1", "T")
        two = build_score_projection(timeline, "s1", "T")
        self.assertEqual(one.spans, two.spans)
        self.assertEqual(
            [span.index for span in one.spans], [0, 1, 2]
        )
        self.assertEqual(
            [span.onset_seconds for span in one.spans], [0.0, 1.0, 2.0]
        )

    def test_requires_canonical_timeline(self):
        with self.assertRaises(TypeError):
            build_score_projection([("not", "a timeline")], "s1", "T")

    def test_oversized_projection_fails_closed(self):
        events = []
        for index in range(MAX_SCORE_SPANS + 1):
            time = index * 0.001
            events.append(TimelineEvent(time, note_on(1, 60, 90)))
            events.append(TimelineEvent(time, note_off(1, 60, 0)))
        events.sort(key=lambda event: event.time)
        timeline = Timeline(events=tuple(events), duration=100.0, source="big.mid")
        with self.assertRaises(ScoreTooLargeError):
            build_score_projection(timeline, "s1", "Big")


class ScoreSpanModelTests(unittest.TestCase):
    def test_span_validates_fields(self):
        with self.assertRaises(ValueError):
            ScoreSpan(0, 128, 1, 0.0, 1.0, 90)  # note out of MIDI range
        with self.assertRaises(ValueError):
            ScoreSpan(0, 60, 0, 0.0, 1.0, 90)  # channel out of range
        with self.assertRaises(ValueError):
            ScoreSpan(0, 60, 1, -1.0, 1.0, 90)  # negative onset
        with self.assertRaises(ValueError):
            ScoreSpan(0, 60, 1, 0.0, -0.5, 90)  # negative duration
        with self.assertRaises(ValueError):
            ScoreSpan(0, 60, 1, 0.0, 1.0, 0)  # velocity zero
        with self.assertRaises(ValueError):
            ScoreSpan(-1, 60, 1, 0.0, 1.0, 90)  # negative index

    def test_projection_validates_fields(self):
        span = ScoreSpan(0, 60, 1, 0.0, 1.0, 90)
        with self.assertRaises(ValueError):
            ScoreProjection("", "T", 1.0, (span,))
        with self.assertRaises(TypeError):
            ScoreProjection("s1", "T", 1.0, [span])  # list, not tuple
        with self.assertRaises(ValueError):
            ScoreProjection("s1", "T", -1.0, (span,))
        with self.assertRaises(ValueError):
            ScoreProjection("s1", "T", 1.0, (span,), percussion_note_ons=-1)


class _FakeLibrary:
    """Minimal LibraryService double: path resolution + records."""

    def __init__(self, songs):
        # songs: {song_id: (path_str, title)}
        self.songs = songs
        self.loads = []

    def resolve(self, song_id):
        from piano_core.library.model import LibraryPathError

        entry = self.songs.get(song_id)
        if entry is None:
            raise LibraryPathError(f"unknown song_id: {song_id}")
        from pathlib import Path

        return Path(entry[0])

    def get(self, song_id):
        entry = self.songs.get(song_id)
        if entry is None:
            return None

        class _Record:
            title = entry[1]
            filename = entry[1]

        return _Record()


class ScoreServiceTests(unittest.TestCase):
    def test_new_canonical_load_replaces_same_path_cached_score(self):
        first = _seq((0, 60, 90, 1))
        second = _seq((0, 72, 100, 2))
        current = [first]
        service = ScoreService(_FakeLibrary({"s": ("/a/song.mid", "Song")}),
                               loader=lambda *a, **k: current[0])
        self.assertEqual(service.get("s").spans[0].note, 60)
        current[0] = second
        self.assertIs(service.timeline_loader()("/a/song.mid"), second)
        self.assertEqual(service.get("s").spans[0].note, 72)

    def test_new_load_during_projection_cannot_publish_old_score(self):
        first, second = _seq((0, 60, 90, 1)), _seq((0, 72, 100, 2))
        current = [first]
        calls = []
        def builder(timeline, *args):
            calls.append(timeline)
            if len(calls) == 1:
                current[0] = second
                service.timeline_loader()("/a/song.mid")
            return build_score_projection(timeline, *args)
        service = ScoreService(_FakeLibrary({"s": ("/a/song.mid", "Song")}),
                               loader=lambda *a, **k: current[0], builder=builder)
        self.assertEqual(service.get("s").spans[0].note, 72)
        self.assertIs(service.get("s"), service.get("s"))
        self.assertEqual(calls, [first, second])

    def _service(self, library, timeline):
        def loader(path, *args, **kwargs):
            library.loads.append(str(path))
            return timeline

        return ScoreService(library, loader=loader)

    def test_get_projects_and_caches_by_resolved_path(self):
        timeline = _seq((0.0, 60, 90, 1.0))
        library = _FakeLibrary({"s1": ("/a/song.mid", "Song One")})
        service = self._service(library, timeline)
        first = service.get("s1")
        self.assertEqual(first.span_count, 1)
        self.assertEqual(first.title, "Song One")
        again = service.get("s1")
        self.assertIs(first, again)  # cached, no second parse
        self.assertEqual(library.loads, ["/a/song.mid"])

    def test_unknown_song_raises_library_error(self):
        service = self._service(_FakeLibrary({}), _seq())
        from piano_core.library.model import LibraryPathError

        with self.assertRaises(LibraryPathError):
            service.get("missing")

    def test_no_library_raises_config_error(self):
        service = self._service(None, _seq())
        from piano_core.library.model import LibraryConfigError

        with self.assertRaises(LibraryConfigError):
            service.get("s1")

    def test_timeline_loader_warms_without_reparse(self):
        timeline = _seq((0.0, 60, 90, 1.0))
        library = _FakeLibrary({"s1": ("/a/song.mid", "Song One")})
        service = self._service(library, timeline)
        loader = service.timeline_loader()
        # Playback/practice path: the loader returns the timeline unchanged...
        loaded = loader("/a/song.mid", tempo_scale=2.0)
        self.assertIs(loaded, timeline)
        # ...and the next get() projects WITHOUT a second parse.
        projection = service.get("s1")
        self.assertEqual(projection.span_count, 1)
        self.assertEqual(library.loads, ["/a/song.mid"])

    def test_loader_failures_never_break_playback(self):
        library = _FakeLibrary({"s1": ("/a/song.mid", "S")})
        timeline = _seq((0.0, 60, 90, 1.0))
        service = ScoreService(library, loader=lambda path, *a, **k: timeline)
        broken = service.timeline_loader()

        def _boom(*args, **kwargs):
            raise RuntimeError("projection ingest exploded")

        service._build = _boom  # simulate an ingest-side failure
        loaded = broken("/a/song.mid")
        self.assertIs(loaded, timeline)  # playback still got its timeline

    def test_cache_is_bounded_lru(self):
        timeline = _seq((0.0, 60, 90, 1.0))
        library = _FakeLibrary(
            {f"s{i}": (f"/a/{i}.mid", f"T{i}") for i in range(6)}
        )
        service = self._service(library, timeline)
        service._max_cached = 2
        service.get("s0")
        service.get("s1")
        service.get("s2")  # evicts s0
        self.assertEqual(list(service._cache.keys()), ["/a/1.mid", "/a/2.mid"])

    def test_invalid_song_id_rejected(self):
        service = self._service(_FakeLibrary({}), _seq())
        with self.assertRaises(ValueError):
            service.get("   ")


if __name__ == "__main__":
    unittest.main()
