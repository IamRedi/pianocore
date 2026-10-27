"""V2 tests: canonical MIDI output foundation (PIANO-P007).

All tests run against a scripted fake output backend: no mido import, no
hardware, no sound. Deterministic, state-based assertions. The injected
``message_factory`` on the real backend serialization path is exercised
with a recording factory to prove canonical->backend channel conversion
(1-based -> 0-based) without mido.
"""

import threading
import unittest

from piano_core.app import Application
from piano_core.config import AppConfig
from piano_core.midi.backends import MidoOutputBackend
from piano_core.midi.output import MidiOutputAdapter, OutputNotOpenError
from piano_core.midi.output_events import (
    ALL_NOTES_OFF_CC,
    ALL_SOUND_OFF_CC,
    SUSTAIN_CC,
    note_off,
    note_on,
    panic,
    stop_automated_notes,
    sustain,
)
from tests.test_midi_input import FakeBackend, msg, wait_for


class FakeOutputPort:
    """Records every backend-level send as a plain tuple."""

    def __init__(self, backend, port):
        self.backend = backend
        self.port = port
        self.closed = False
        self.sent: list[tuple] = []


class FakeOutputBackend:
    """Scripted OUTPUT-ONLY backend (no mido, no hardware)."""

    def __init__(self, ports):
        self.ports = list(ports)
        self.opened: list[str] = []
        self.outputs: list[FakeOutputPort] = []
        self.fail_open_once: Exception | None = None

    def list_output_ports(self):
        return list(self.ports)

    def open_output(self, port):
        if self.fail_open_once is not None:
            exc, self.fail_open_once = self.fail_open_once, None
            raise exc
        self.opened.append(port)
        output = FakeOutputPort(self, port)
        self.outputs.append(output)
        return output

    def send_note_on(self, output, channel, note, velocity):
        output.sent.append(("note_on", channel, note, velocity))

    def send_note_off(self, output, channel, note, release_velocity):
        output.sent.append(("note_off", channel, note, release_velocity))

    def send_control_change(self, output, channel, control, value):
        output.sent.append(("control_change", channel, control, value))

    def close_output(self, output):
        output.closed = True


class SelectiveReleaseFailureBackend(FakeOutputBackend):
    """Backend that fails selected cleanup sends after earlier sends succeed."""

    def __init__(
        self,
        ports,
        *,
        fail_notes=(),
        fail_voices=(),
        fail_sustain_channels=(),
    ):
        super().__init__(ports)
        self.fail_notes = set(fail_notes)
        self.fail_voices = set(fail_voices)
        self.fail_sustain_channels = set(fail_sustain_channels)

    def send_note_off(self, output, channel, note, release_velocity):
        if note in self.fail_notes or (channel, note) in self.fail_voices:
            raise RuntimeError(f"simulated note release failure: {note}")
        super().send_note_off(output, channel, note, release_velocity)

    def send_control_change(self, output, channel, control, value):
        if (
            control == SUSTAIN_CC
            and value == 0
            and channel in self.fail_sustain_channels
        ):
            raise RuntimeError(f"simulated sustain release failure: {channel}")
        super().send_control_change(output, channel, control, value)


def opened(adapter: MidiOutputAdapter, backend: FakeOutputBackend):
    adapter.open()
    return backend.outputs[-1]


class LifecycleTests(unittest.TestCase):
    def test_initial_adapter_closed_no_owned_notes(self):
        adapter = MidiOutputAdapter(FakeOutputBackend(["Roland OUT"]))
        self.assertFalse(adapter.is_open)
        self.assertIsNone(adapter.port_name)
        self.assertEqual(adapter.owned_notes, ())
        self.assertEqual(adapter.owned_sustain_channels, ())
        self.assertEqual(adapter.commands_sent, 0)

    def test_open_selects_stable_endpoint_and_is_idempotent(self):
        backend = FakeOutputBackend(["System Timer", "Roland Digital Piano MIDI 1"])
        adapter = MidiOutputAdapter(backend, matcher="roland")
        self.assertEqual(adapter.open(), "Roland Digital Piano MIDI 1")
        self.assertEqual(adapter.open(), "Roland Digital Piano MIDI 1")
        self.assertEqual(backend.opened, ["Roland Digital Piano MIDI 1"])

    def test_clean_close_and_repeated_close_idempotent(self):
        backend = FakeOutputBackend(["Roland OUT"])
        adapter = MidiOutputAdapter(backend)
        port = opened(adapter, backend)
        adapter.close()
        self.assertTrue(port.closed)
        self.assertFalse(adapter.is_open)
        adapter.close()
        adapter.close()
        self.assertEqual(len(backend.outputs), 1)

    def test_endpoint_not_found_fails_safely(self):
        adapter = MidiOutputAdapter(FakeOutputBackend(["System Timer"]))
        from piano_core.midi.input import EndpointNotFoundError

        with self.assertRaises(EndpointNotFoundError):
            adapter.open()
        self.assertFalse(adapter.is_open)

    def test_endpoint_ambiguity_fails_closed(self):
        from piano_core.midi.input import AmbiguousEndpointError

        adapter = MidiOutputAdapter(
            FakeOutputBackend(["Roland A", "Roland B"]), matcher="roland"
        )
        with self.assertRaises(AmbiguousEndpointError):
            adapter.open()
        self.assertFalse(adapter.is_open)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeOutputBackend(["Roland OUT"])
        self.adapter = MidiOutputAdapter(self.backend)
        self.port = opened(self.adapter, self.backend)

    def test_commands_require_open_port(self):
        closed = MidiOutputAdapter(FakeOutputBackend(["Roland OUT"]))
        with self.assertRaises(OutputNotOpenError):
            closed.apply(note_on(1, 60, 40))

    def test_non_canonical_command_fails_closed(self):
        with self.assertRaises(TypeError):
            self.adapter.apply("note_on 60")

    def test_note_on_sends_and_takes_ownership(self):
        self.adapter.apply(note_on(1, 60, 40))
        self.assertEqual(
            self.port.sent, [("note_on", 1, 60, 40)]
        )
        self.assertEqual(self.adapter.owned_notes, ((1, 60),))
        self.assertEqual(self.adapter.commands_sent, 1)

    def test_note_off_sends_and_clears_ownership(self):
        self.adapter.apply(note_on(1, 60, 40))
        self.adapter.apply(note_off(1, 60, 100))
        self.assertEqual(
            self.port.sent[-1], ("note_off", 1, 60, 100)
        )
        self.assertEqual(self.adapter.owned_notes, ())

    def test_duplicate_note_on_policy_is_retrigger_single_ownership(self):
        self.adapter.apply(note_on(1, 60, 40))
        self.adapter.apply(note_on(1, 60, 55))
        self.assertEqual(len(self.port.sent), 2)
        self.assertEqual(self.adapter.owned_notes, ((1, 60),))
        self.assertEqual(self.adapter.commands_sent, 2)

    def test_note_off_unowned_forwards_without_ownership_effect(self):
        self.adapter.apply(note_on(1, 60, 40))
        self.adapter.apply(note_off(1, 62, 0))
        self.assertEqual(self.adapter.owned_notes, ((1, 60),))
        self.assertEqual(self.port.sent[-1], ("note_off", 1, 62, 0))

    def test_multiple_owned_notes_tracked(self):
        self.adapter.apply(note_on(1, 60, 40))
        self.adapter.apply(note_on(1, 62, 40))
        self.adapter.apply(note_on(2, 72, 45))
        self.assertEqual(
            self.adapter.owned_notes, ((1, 60), (1, 62), (2, 72))
        )

    def test_command_bounds_fail_closed(self):
        for builder in (
            lambda: note_on(0, 60, 40),  # channel low
            lambda: note_on(17, 60, 40),  # channel high
            lambda: note_on(1, 128, 40),  # note high
            lambda: note_on(1, 60, 0),  # velocity 0 forbidden
            lambda: note_on(1, 60, 128),  # velocity high
            lambda: note_off(1, 60, 128),  # release velocity high
            lambda: sustain(1, 128),  # value high
            lambda: note_on(1, 60, True),  # bool is not an int here
            lambda: stop_automated_notes().__class__(  # payload on global op
                type=stop_automated_notes().type, note=60
            ),
            lambda: panic().__class__(  # channel override on global op
                type=panic().type, channel=5
            ),
        ):
            with self.assertRaises((ValueError, TypeError)):
                builder()
        self.assertEqual(self.port.sent, [])


class StopTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeOutputBackend(["Roland OUT"])
        self.adapter = MidiOutputAdapter(self.backend)
        self.port = opened(self.adapter, self.backend)

    def test_stop_releases_all_and_only_owned_notes(self):
        self.adapter.apply(note_on(1, 60, 40))
        self.adapter.apply(note_on(1, 62, 40))
        self.adapter.apply(note_on(2, 72, 45))
        sent_before = len(self.port.sent)
        self.adapter.apply(stop_automated_notes())
        released = [m for m in self.port.sent[sent_before:]]
        self.assertEqual(
            released,
            [
                ("note_off", 1, 60, 0),
                ("note_off", 1, 62, 0),
                ("note_off", 2, 72, 0),
            ],
        )
        self.assertEqual(self.adapter.owned_notes, ())

    def test_stop_is_noop_when_nothing_owned(self):
        self.adapter.apply(stop_automated_notes())
        self.assertEqual(self.port.sent, [])
        self.assertEqual(self.adapter.commands_sent, 0)

    def test_stop_does_not_send_global_sweep(self):
        self.adapter.apply(note_on(1, 60, 40))
        self.adapter.apply(stop_automated_notes())
        types = {message[0] for message in self.port.sent}
        self.assertNotIn("control_change", types)

    def test_stop_releases_owned_sustain_on_its_channel(self):
        self.adapter.apply(sustain(3, 100))
        self.adapter.apply(note_on(3, 60, 40))
        self.adapter.apply(stop_automated_notes())
        self.assertIn(("control_change", 3, SUSTAIN_CC, 0), self.port.sent)
        self.assertEqual(self.adapter.owned_sustain_channels, ())
        self.assertEqual(self.adapter.owned_notes, ())

    def test_partial_note_release_keeps_only_output_that_may_remain(self):
        backend = SelectiveReleaseFailureBackend(
            ["Roland OUT"], fail_notes={62}
        )
        adapter = MidiOutputAdapter(backend)
        port = opened(adapter, backend)
        adapter.apply(note_on(1, 60, 40))
        adapter.apply(note_on(1, 62, 40))
        adapter.apply(sustain(1, 100))
        with self.assertRaisesRegex(RuntimeError, "note release failure"):
            adapter.apply(stop_automated_notes())
        self.assertIn(("note_off", 1, 60, 0), port.sent)
        self.assertEqual(adapter.owned_notes, ((1, 62),))
        self.assertEqual(adapter.owned_sustain_channels, (1,))

    def test_partial_sustain_release_preserves_failed_channel_only(self):
        backend = SelectiveReleaseFailureBackend(
            ["Roland OUT"], fail_sustain_channels={2}
        )
        adapter = MidiOutputAdapter(backend)
        opened(adapter, backend)
        adapter.apply(note_on(1, 60, 40))
        adapter.apply(sustain(1, 100))
        adapter.apply(sustain(2, 100))
        with self.assertRaisesRegex(RuntimeError, "sustain release failure"):
            adapter.apply(stop_automated_notes())
        self.assertEqual(adapter.owned_notes, ())
        self.assertEqual(adapter.owned_sustain_channels, (2,))


class PanicTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeOutputBackend(["Roland OUT"])
        self.adapter = MidiOutputAdapter(self.backend)
        self.port = opened(self.adapter, self.backend)

    def test_panic_is_distinct_from_stop_and_global(self):
        self.adapter.apply(note_on(1, 60, 40))
        self.adapter.apply(panic())
        controls = [
            m for m in self.port.sent if m[0] == "control_change"
        ]
        # 16 channels x (sustain off + CC123 + CC120):
        self.assertEqual(len(controls), 48)
        self.assertEqual(controls[0], ("control_change", 1, SUSTAIN_CC, 0))
        self.assertEqual(
            controls[1], ("control_change", 1, ALL_NOTES_OFF_CC, 0)
        )
        self.assertEqual(
            controls[2], ("control_change", 1, ALL_SOUND_OFF_CC, 0)
        )
        self.assertEqual(controls[-1][1], 16)
        self.assertEqual(self.adapter.owned_notes, ())

    def test_panic_clears_ownership_and_owned_sustain(self):
        self.adapter.apply(sustain(2, 127))
        self.adapter.apply(note_on(2, 60, 40))
        self.adapter.apply(panic())
        self.assertEqual(self.adapter.owned_notes, ())
        self.assertEqual(self.adapter.owned_sustain_channels, ())

    def test_panic_on_idle_port_still_sweeps(self):
        self.adapter.apply(panic())
        self.assertEqual(len(self.port.sent), 48)


class SerializationTests(unittest.TestCase):
    def test_real_backend_serialization_converts_channels(self):
        """Canonical 1-based channels serialize to backend 0-based messages
        via the injectable message factory (no mido needed)."""
        sent: list[tuple] = []

        def factory(msg_type, **parameters):
            sent.append((msg_type, parameters))
            return (msg_type, parameters)

        backend = MidoOutputBackend(message_factory=factory)

        class RecordingPort:
            def send(self, message):
                pass  # the factory already recorded the message

        output = RecordingPort()  # opaque handle; the factory records all
        backend.send_note_on(output, 1, 60, 40)
        backend.send_note_off(output, 16, 21, 5)
        backend.send_control_change(output, 3, SUSTAIN_CC, 90)
        self.assertEqual(
            sent,
            [
                ("note_on", {"channel": 0, "note": 60, "velocity": 40}),
                ("note_off", {"channel": 15, "note": 21, "velocity": 5}),
                ("control_change", {"channel": 2, "control": 64, "value": 90}),
            ],
        )

    def test_sustain_output_serialization_through_adapter(self):
        backend = FakeOutputBackend(["Roland OUT"])
        adapter = MidiOutputAdapter(backend)
        port = opened(adapter, backend)
        adapter.apply(sustain(1, 90))
        self.assertEqual(
            port.sent, [("control_change", 1, SUSTAIN_CC, 90)]
        )
        self.assertEqual(adapter.owned_sustain_channels, (1,))
        adapter.apply(sustain(1, 0))
        self.assertEqual(adapter.owned_sustain_channels, ())


class ApplicationIntegrationTests(unittest.TestCase):
    def _app(self, output_enabled=True):
        out_backend = FakeOutputBackend(["Roland OUT"])
        in_backend = FakeBackend(["Roland Digital Piano MIDI 1 28:0"])
        app = Application(
            AppConfig(
                midi_input_enabled=True,
                midi_output_enabled=output_enabled,
                midi_input_port_matcher="roland",
            ),
            midi_backend_factory=lambda: in_backend,
            midi_output_backend_factory=lambda: out_backend,
        )
        self.addCleanup(app.shutdown)
        return app, in_backend, out_backend

    def test_startup_emits_no_note(self):
        app, in_backend, out_backend = self._app()
        app.start()
        self.assertIsNotNone(app.midi_output)
        self.assertTrue(app.midi_output.is_open)
        self.assertEqual(out_backend.outputs[-1].sent, [])
        self.assertEqual(app.midi_output.owned_notes, ())

    def test_disabled_output_opens_no_output_backend(self):
        app, in_backend, out_backend = self._app(output_enabled=False)
        app.start()
        self.assertIsNone(app.midi_output)
        self.assertEqual(out_backend.opened, [])

    def test_shutdown_releases_owned_automated_notes(self):
        app, in_backend, out_backend = self._app()
        app.start()
        app.midi_output.apply(note_on(1, 60, 45))
        app.midi_output.apply(note_on(1, 64, 45))
        app.shutdown()
        sent = out_backend.outputs[-1].sent
        self.assertEqual(
            sent[-2:], [("note_off", 1, 60, 0), ("note_off", 1, 64, 0)]
        )
        self.assertEqual(app.midi_output.owned_notes, ())
        self.assertFalse(app.midi_output.is_open)
        self.assertTrue(out_backend.outputs[-1].closed)

    def test_shutdown_without_owned_notes_emits_nothing(self):
        app, in_backend, out_backend = self._app()
        app.start()
        app.shutdown()
        self.assertEqual(out_backend.outputs[-1].sent, [])
        self.assertFalse(app.midi_output.is_open)

    def test_input_never_routes_to_output(self):
        app, in_backend, out_backend = self._app()
        app.start()
        # Human plays keys: events flow to the StateStore only.
        in_backend.pending = [
            msg("note_on", note=60, velocity=88),
            msg("note_on", note=64, velocity=88),
        ]
        self.assertTrue(
            wait_for(
                lambda: app.state_store.snapshot().pressed_notes
                == frozenset({60, 64})
            )
        )
        app.shutdown()
        # No echo/thru/loopback: the output port never sent anything.
        self.assertEqual(out_backend.outputs[-1].sent, [])
        self.assertEqual(app.midi_output.owned_notes, ())

    def test_output_commands_do_not_mutate_pressed_notes(self):
        app, in_backend, out_backend = self._app()
        app.start()
        in_backend.pending = [msg("note_on", note=62, velocity=88)]
        self.assertTrue(
            wait_for(
                lambda: app.state_store.snapshot().pressed_notes
                == frozenset({62})
            )
        )
        # Automated output on a different note must not touch the
        # canonical physical-input state.
        app.midi_output.apply(note_on(1, 60, 45))
        self.assertEqual(
            app.state_store.snapshot().pressed_notes, frozenset({62})
        )
        app.midi_output.apply(stop_automated_notes())
        self.assertEqual(
            app.state_store.snapshot().pressed_notes, frozenset({62})
        )
        app.shutdown()


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_command_threads_serialize_deterministically(self):
        backend = FakeOutputBackend(["Roland OUT"])
        adapter = MidiOutputAdapter(backend)
        adapter.open()
        port = backend.outputs[-1]
        errors: list[Exception] = []
        start = threading.Barrier(4)

        def worker(base: int):
            try:
                start.wait()
                for offset in range(50):
                    adapter.apply(note_on(1, base + offset, 40))
                    adapter.apply(note_off(1, base + offset, 0))
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(base,)) for base in (0, 20, 40, 60)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(adapter.owned_notes, ())
        self.assertEqual(len(port.sent), 400)
        adapter.close()


if __name__ == "__main__":
    unittest.main()
