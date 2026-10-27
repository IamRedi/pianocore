"""PIANO-UI-003 realtime publication tests (hardware-free)."""

import threading
import unittest

from piano_core.live_notes import LiveNoteState, LiveNoteSubscriberLimitError
from piano_core.midi.events import note_on, sustain
from piano_core.state import MidiConnectionState, StateStore


class LiveNoteStateTests(unittest.TestCase):
    def setUp(self):
        self.channel = LiveNoteState(
            runtime_epoch="runtime-test",
            heartbeat_seconds=0.01,
        )

    def test_initial_snapshot_is_empty_and_immutable(self):
        snapshot = self.channel.snapshot()
        self.assertEqual(snapshot.schema_version, 1)
        self.assertEqual(snapshot.runtime_epoch, "runtime-test")
        self.assertEqual(snapshot.sequence, 0)
        self.assertEqual(snapshot.physical_notes, ())
        self.assertEqual(snapshot.playback_voices, ())
        self.assertFalse(snapshot.playback_available)

    def test_physical_publication_copies_state_and_is_change_only(self):
        store = StateStore()
        store.set_midi_connection(MidiConnectionState.CONNECTED)
        first = self.channel.publish_physical(store.snapshot())
        store.apply_event(note_on(1, 64, 90, 1.0))
        store.apply_event(sustain(1, 100, 1.1))
        second = self.channel.publish_physical(store.snapshot())
        duplicate = self.channel.publish_physical(store.snapshot())
        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.physical_notes, (64,))
        self.assertTrue(second.physical_sustain_pressed)
        self.assertEqual(duplicate.sequence, second.sequence)

    def test_playback_preserves_voice_identity_and_repeated_pitch(self):
        snapshot = self.channel.publish_playback(
            available=True,
            owned_notes=((2, 60), (1, 60), (1, 64)),
            sustain_channels=(2, 1),
        )
        self.assertEqual(snapshot.playback_voices, ((1, 60), (1, 64), (2, 60)))
        self.assertEqual(snapshot.playback_sustain_channels, (1, 2))
        cleared = self.channel.publish_playback(available=True)
        self.assertEqual(cleared.playback_voices, ())

    def test_subscription_starts_with_full_snapshot_then_change_and_heartbeat(self):
        subscription = self.channel.subscribe()
        stream = iter(subscription)
        self.assertEqual(next(stream).sequence, 0)
        self.channel.publish_playback(available=True, owned_notes=((1, 60),))
        self.assertEqual(next(stream).playback_voices, ((1, 60),))
        self.assertIsNone(next(stream))
        subscription.close()

    def test_subscriber_capacity_is_bounded_and_released(self):
        channel = LiveNoteState(max_subscribers=1)
        first = channel.subscribe()
        with self.assertRaises(LiveNoteSubscriberLimitError):
            channel.subscribe()
        first.close()
        second = channel.subscribe()
        second.close()

    def test_default_capacity_accepts_eight_and_rejects_ninth(self):
        subscriptions = [self.channel.subscribe() for _ in range(8)]
        with self.assertRaises(LiveNoteSubscriberLimitError):
            self.channel.subscribe()
        for subscription in subscriptions:
            subscription.close()

    def test_slow_subscriber_reads_latest_snapshot_without_queue(self):
        subscription = self.channel.subscribe()
        stream = iter(subscription)
        self.assertEqual(next(stream).sequence, 0)
        self.channel.publish_playback(
            available=True, owned_notes=((1, 60),)
        )
        latest = self.channel.publish_playback(
            available=True, owned_notes=((1, 64),)
        )
        received = next(stream)
        self.assertEqual(received.sequence, latest.sequence)
        self.assertEqual(received.playback_voices, ((1, 64),))
        subscription.close()

    def test_idle_slow_subscriber_does_not_block_publication(self):
        subscription = self.channel.subscribe()
        finished = threading.Event()

        def publish():
            self.channel.publish_playback(
                available=True, owned_notes=((1, 60),)
            )
            finished.set()

        thread = threading.Thread(target=publish)
        thread.start()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(finished.is_set())
        subscription.close()

    def test_close_ends_subscriptions(self):
        subscription = self.channel.subscribe()
        stream = iter(subscription)
        next(stream)
        self.channel.close()
        with self.assertRaises(StopIteration):
            next(stream)


if __name__ == "__main__":
    unittest.main()
