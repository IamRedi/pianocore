"""V2 tests: canonical PianoState StateStore (PIANO-P006).

Deterministic, hardware-free: canonical events are constructed directly
through the validated canonical constructors. The revision policy under
test is Option B (documented in piano_core.state and
docs/operations/STATESTORE.md): revision tracks *material* state
(pressed notes, sustain value/pressed, MIDI connection); ``last_input``
is observational metadata excluded from materiality.
"""

import dataclasses
import logging
import threading
import unittest

from piano_core.midi.events import MidiEventType, MidiInputEvent, note_off, note_on, sustain
from piano_core.state import LastInput, MidiConnectionState, PianoState, StateStore


class ListLogHandler(logging.Handler):
    """Minimal in-memory log capture (no I/O, deterministic)."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class InitialStateTests(unittest.TestCase):
    def test_initial_empty_state(self):
        store = StateStore()
        snapshot = store.snapshot()
        self.assertEqual(snapshot, PianoState.initial())
        self.assertEqual(snapshot.pressed_notes, frozenset())
        self.assertEqual(snapshot.sustain_value, 0)
        self.assertFalse(snapshot.sustain_pressed)
        self.assertIsNone(snapshot.last_input)
        self.assertIs(
            snapshot.midi_connection, MidiConnectionState.DISCONNECTED
        )
        self.assertEqual(snapshot.revision, 0)

    def test_invalid_construction_fails_closed(self):
        for kwargs in (
            {"pressed_notes": {60}},  # set, not frozenset
            {"pressed_notes": frozenset({128})},  # out of range
            {"sustain_value": 128},
            {"sustain_pressed": "yes"},
            {"last_input": "note_on"},
            {"midi_connection": "CONNECTED"},
            {"revision": -1},
        ):
            with self.assertRaises((TypeError, ValueError)):
                PianoState(**kwargs)
        with self.assertRaises(TypeError):
            StateStore(initial="empty")


class ReducerTests(unittest.TestCase):
    def setUp(self):
        self.store = StateStore()

    def test_note_on_adds_note(self):
        updated = self.store.apply_event(note_on(1, 60, 99, 1.0))
        self.assertEqual(updated.pressed_notes, frozenset({60}))
        self.assertEqual(updated.revision, 1)
        self.assertEqual(
            updated.last_input,
            LastInput("note_on", 1, 1.0, note=60, value=0),
        )

    def test_note_off_removes_note(self):
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        updated = self.store.apply_event(note_off(1, 60, 104, 2.0))
        self.assertEqual(updated.pressed_notes, frozenset())
        self.assertEqual(updated.revision, 2)

    def test_note_off_absent_note_is_safe(self):
        updated = self.store.apply_event(note_off(1, 60, 104, 1.0))
        self.assertEqual(updated.pressed_notes, frozenset())
        self.assertEqual(updated.revision, 0)
        self.assertEqual(updated.last_input.event_type, "note_off")

    def test_two_simultaneous_held_notes(self):
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        updated = self.store.apply_event(note_on(1, 62, 80, 1.5))
        self.assertEqual(updated.pressed_notes, frozenset({60, 62}))
        updated = self.store.apply_event(note_off(1, 60, 100, 2.0))
        self.assertEqual(updated.pressed_notes, frozenset({62}))

    def test_repeated_note_on_is_idempotent_for_pressed_notes(self):
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        updated = self.store.apply_event(note_on(1, 60, 40, 2.0))
        self.assertEqual(updated.pressed_notes, frozenset({60}))
        self.assertEqual(updated.revision, 1, "repeat NOTE_ON is not material")
        self.assertEqual(updated.last_input.timestamp, 2.0)

    def test_velocity_zero_normalization_stays_upstream(self):
        """The store never sees raw velocity-0 NOTE_ONs: the canonical
        constructors refuse them (the adapter normalizes before delivery)."""
        with self.assertRaises(ValueError):
            note_on(1, 60, 0, 1.0)
        # The store itself applies whatever canonical event it receives and
        # introduces no second normalization layer:
        event = MidiInputEvent(
            type=MidiEventType.NOTE_ON, channel=1, timestamp=1.0, note=60
        )
        self.assertEqual(event.velocity, 0)
        updated = self.store.apply_event(event)
        self.assertIn(60, updated.pressed_notes)

    def test_sustain_press_updates_raw_value_and_pressed(self):
        updated = self.store.apply_event(sustain(1, 90, 1.0))
        self.assertEqual(updated.sustain_value, 90)
        self.assertTrue(updated.sustain_pressed)
        self.assertEqual(updated.revision, 1)

    def test_sustain_release_resets(self):
        self.store.apply_event(sustain(1, 127, 1.0))
        updated = self.store.apply_event(sustain(1, 0, 2.0))
        self.assertEqual(updated.sustain_value, 0)
        self.assertFalse(updated.sustain_pressed)
        self.assertEqual(updated.revision, 2)

    def test_sustain_does_not_retain_released_notes(self):
        """Sustain is NOT sounding-note state: released keys leave
        pressed_notes even while the pedal is held."""
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        self.store.apply_event(sustain(1, 127, 1.5))
        updated = self.store.apply_event(note_off(1, 60, 100, 2.0))
        self.assertEqual(updated.pressed_notes, frozenset())
        self.assertTrue(updated.sustain_pressed)

    def test_same_value_sustain_is_not_material(self):
        self.store.apply_event(sustain(1, 90, 1.0))
        updated = self.store.apply_event(sustain(1, 90, 2.0))
        self.assertEqual(updated.revision, 1)
        self.assertEqual(updated.last_input.timestamp, 2.0)

    def test_non_canonical_event_fails_closed(self):
        with self.assertRaises(TypeError):
            self.store.apply_event("note_on 60")

    def test_apply_event_returns_state_and_store_stays_consistent(self):
        returned = self.store.apply_event(note_on(1, 60, 99, 1.0))
        self.assertEqual(returned, self.store.snapshot())


class RevisionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = StateStore()

    def test_revision_increments_on_material_changes(self):
        self.assertEqual(self.store.snapshot().revision, 0)
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        self.assertEqual(self.store.snapshot().revision, 1)
        self.store.apply_event(note_on(1, 62, 99, 1.1))
        self.assertEqual(self.store.snapshot().revision, 2)
        self.store.apply_event(note_off(1, 60, 100, 1.2))
        self.assertEqual(self.store.snapshot().revision, 3)
        self.store.apply_event(sustain(1, 64, 1.3))
        self.assertEqual(self.store.snapshot().revision, 4)

    def test_no_ops_never_bump_revision(self):
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        revision = self.store.snapshot().revision
        # Repeated NOTE_ON for a held note:
        self.store.apply_event(note_on(1, 60, 33, 1.1))
        # NOTE_OFF for an absent note:
        self.store.apply_event(note_off(1, 61, 100, 1.2))
        # Same-value SUSTAIN:
        self.store.apply_event(sustain(1, 0, 1.3))
        self.store.apply_event(sustain(1, 0, 1.4))
        self.assertEqual(self.store.snapshot().revision, revision)
        # The next material change bumps by exactly one:
        self.store.apply_event(note_off(1, 60, 100, 1.5))
        self.assertEqual(self.store.snapshot().revision, revision + 1)

    def test_last_input_is_observational_option_b(self):
        """Documented Option B: last_input changes while revision does not."""
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        before = self.store.snapshot()
        after = self.store.apply_event(note_on(1, 60, 55, 9.9))
        self.assertEqual(before.revision, after.revision)
        self.assertNotEqual(before.last_input, after.last_input)
        self.assertEqual(after.last_input.timestamp, 9.9)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.store = StateStore()

    def test_connection_transitions(self):
        self.store.set_midi_connection(MidiConnectionState.CONNECTING)
        self.assertEqual(
            self.store.snapshot().midi_connection,
            MidiConnectionState.CONNECTING,
        )
        self.assertEqual(self.store.snapshot().revision, 1)
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.assertEqual(self.store.snapshot().revision, 2)
        self.store.set_midi_connection(MidiConnectionState.DISCONNECTED)
        self.assertEqual(self.store.snapshot().revision, 3)
        self.assertEqual(
            self.store.snapshot().midi_connection,
            MidiConnectionState.DISCONNECTED,
        )

    def test_connection_set_to_current_value_is_noop(self):
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        updated = self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.assertEqual(updated.revision, 1)
        self.assertEqual(updated, self.store.snapshot())

    def test_connection_events_are_separate_from_note_material(self):
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        self.assertEqual(self.store.snapshot().revision, 2)

    def test_disconnect_clears_keys_and_pedal_once(self):
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        self.store.apply_event(sustain(1, 100, 1.1))
        before = self.store.snapshot().revision
        disconnected = self.store.set_midi_connection(
            MidiConnectionState.DISCONNECTED
        )
        self.assertEqual(disconnected.pressed_notes, frozenset())
        self.assertEqual(disconnected.sustain_value, 0)
        self.assertFalse(disconnected.sustain_pressed)
        self.assertEqual(disconnected.revision, before + 1)
        self.assertEqual(
            self.store.set_midi_connection(
                MidiConnectionState.DISCONNECTED
            ).revision,
            disconnected.revision,
        )

    def test_reconnect_starts_empty_and_accepts_only_new_input(self):
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.store.apply_event(note_on(1, 60, 99, 1.0))
        self.store.set_midi_connection(MidiConnectionState.DISCONNECTED)
        reconnected = self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.assertEqual(reconnected.pressed_notes, frozenset())
        updated = self.store.apply_event(note_on(1, 64, 88, 2.0))
        self.assertEqual(updated.pressed_notes, frozenset({64}))

    def test_invalid_connection_type_fails_closed(self):
        with self.assertRaises(TypeError):
            self.store.set_midi_connection("CONNECTED")


class SnapshotContractTests(unittest.TestCase):
    def test_snapshot_is_immutable(self):
        store = StateStore()
        store.apply_event(note_on(1, 60, 99, 1.0))
        snapshot = store.snapshot()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.revision = 99  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            snapshot.pressed_notes.add(61)  # type: ignore[attr-defined]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.last_input.timestamp = 42.0  # type: ignore[misc]

    def test_snapshot_is_isolated_from_later_updates(self):
        store = StateStore()
        store.apply_event(note_on(1, 60, 99, 1.0))
        first = store.snapshot()
        store.apply_event(note_on(1, 62, 99, 1.1))
        self.assertEqual(first.pressed_notes, frozenset({60}))
        self.assertEqual(store.snapshot().pressed_notes, frozenset({60, 62}))

    def test_store_cannot_be_mutated_via_snapshot_fields(self):
        store = StateStore()
        store.set_midi_connection(MidiConnectionState.CONNECTED)
        snapshot = store.snapshot()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.midi_connection = MidiConnectionState.DISCONNECTED  # type: ignore[misc]
        self.assertIs(
            store.snapshot().midi_connection, MidiConnectionState.CONNECTED
        )


class LoggingTests(unittest.TestCase):
    def _store_with_capture(self):
        store = StateStore()
        logger = logging.getLogger("piano_core.state")
        handler = ListLogHandler()
        original_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, original_level)
        return store, handler, logger

    def test_per_note_traffic_is_quiet_at_info(self):
        store, handler, logger = self._store_with_capture()
        store.apply_event(note_on(1, 60, 99, 1.0))
        store.apply_event(note_off(1, 60, 100, 1.1))
        store.apply_event(sustain(1, 90, 1.2))
        self.assertEqual(handler.messages, [])
        logger.setLevel(10)  # DEBUG
        store.apply_event(note_on(1, 62, 99, 2.0))
        self.assertEqual(len(handler.messages), 1)

    def test_connection_transitions_log_info_once(self):
        store, handler, _ = self._store_with_capture()
        store.set_midi_connection(MidiConnectionState.CONNECTING)
        store.set_midi_connection(MidiConnectionState.CONNECTED)
        store.set_midi_connection(MidiConnectionState.CONNECTED)  # no-op
        self.assertEqual(
            handler.messages,
            ["midi connection state=CONNECTING", "midi connection state=CONNECTED"],
        )


class ThreadSafetyTests(unittest.TestCase):
    def test_concurrent_writers_and_readers(self):
        store = StateStore()
        writers = 4
        notes_per_writer = 50
        errors: list[Exception] = []
        revisions_seen: list[list[int]] = [[] for _ in range(4)]
        start = threading.Barrier(writers + len(revisions_seen))

        def writer(base_note: int):
            try:
                start.wait()
                for offset in range(notes_per_writer):
                    note = base_note + offset
                    store.apply_event(note_on(1, note, 99, float(note)))
                    store.apply_event(note_off(1, note, 100, float(note) + 0.5))
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        def reader(index: int):
            try:
                start.wait()
                last = 0
                for _ in range(400):
                    revision = store.snapshot().revision
                    if revision < last:  # pragma: no cover - monotonicity
                        errors.append(AssertionError("revision decreased"))
                    last = revision
                    revisions_seen[index].append(revision)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(base,))
            for base in (0, 20, 40, 60)
        ] + [
            threading.Thread(target=reader, args=(index,))
            for index in range(len(revisions_seen))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        self.assertEqual(errors, [])
        final = store.snapshot()
        self.assertEqual(final.pressed_notes, frozenset())
        self.assertEqual(store.events_received(), writers * notes_per_writer * 2)
        self.assertGreater(final.revision, 0)
        self.assertLessEqual(final.revision, writers * notes_per_writer * 2)

    def test_concurrent_connection_updates_and_events(self):
        store = StateStore()
        errors: list[Exception] = []
        states = [
            MidiConnectionState.DISCONNECTED,
            MidiConnectionState.CONNECTING,
            MidiConnectionState.CONNECTED,
        ]

        def flipper():
            try:
                for index in range(200):
                    store.set_midi_connection(states[index % 3])
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def applier():
            try:
                for index in range(200):
                    store.apply_event(note_on(1, index % 128, 99, float(index)))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=flipper) for _ in range(2)] + [
            threading.Thread(target=applier) for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        self.assertEqual(errors, [])
        final = store.snapshot()
        self.assertIsInstance(final, PianoState)
        self.assertGreater(final.revision, 0)


class BoundaryTests(unittest.TestCase):
    def test_statestore_public_surface_has_no_output_members(self):
        for name in dir(StateStore):
            lowered = name.lower()
            for token in ("send", "output", "play", "write"):
                self.assertFalse(
                    token in lowered,
                    f"StateStore.{name} looks like an output surface",
                )

    def test_state_module_imports_no_raw_midi(self):
        import inspect

        import piano_core.state as state_module

        source = inspect.getsource(state_module)
        for forbidden in ("import mido", "import rtmidi", "open_input", "open_output"):
            self.assertNotIn(forbidden, source)

    def test_piano_state_has_no_playback_fields(self):
        names = {f.name for f in dataclasses.fields(PianoState)}
        self.assertEqual(
            names,
            {
                "pressed_notes",
                "sustain_value",
                "sustain_pressed",
                "last_input",
                "midi_connection",
                "revision",
            },
        )


if __name__ == "__main__":
    unittest.main()
