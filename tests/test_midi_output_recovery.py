"""Output USB recovery: synthetic ports only; no real MIDI/ALSA mutation."""
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from piano_core.app import Application
from piano_core.midi.backends import MidoOutputBackend, _alsa_output_connected
from piano_core.midi.output import MidiOutputAdapter, OutputNotOpenError
from piano_core.midi.output_events import note_on, sustain
from piano_core.playback.engine import PlaybackEngine, PlaybackState
from tests.test_midi_input import wait_for
from tests.test_midi_output import FakeOutputBackend
from tests.test_playback_engine import GatedClock, make_timeline


class DisconnectableOutput(FakeOutputBackend):
    def __init__(self):
        super().__init__(["Roland OUT 20:0"])
        self.open_calls = 0
        self.release_failure = False
        self.open_entered = threading.Event()
        self.open_gate = None
        self.max_handles = 0

    def open_output(self, port):
        self.open_calls += 1
        self.open_entered.set()
        if self.open_gate is not None:
            self.open_gate.wait(2)
        output = super().open_output(port)
        output.connected = True
        self.max_handles = max(self.max_handles, sum(not p.closed for p in self.outputs))
        return output

    def is_output_connected(self, output):
        return output.connected

    def send_note_off(self, output, channel, note, release_velocity):
        if self.release_failure and note == 64:
            self.release_failure = False
            raise OSError("cleanup transport loss")
        super().send_note_off(output, channel, note, release_velocity)


class ALSAHealthTests(unittest.TestCase):
    def test_only_owned_client_and_exact_destination_count(self):
        snapshot = '''Client 128 : "ours" [User Legacy]
  Port 0 : "ours" (R-e-) [In]
    Connecting To: 20:0, 31:0
Client 129 : "other" [User Legacy]
    Connecting To: 77:0
'''
        self.assertTrue(_alsa_output_connected(snapshot, "ours", "20:0"))
        self.assertFalse(_alsa_output_connected(snapshot, "ours", "77:0"))
        self.assertFalse(_alsa_output_connected(snapshot, "ours", "120:0"))
        self.assertFalse(_alsa_output_connected(snapshot, "absent", "20:0"))
        self.assertFalse(_alsa_output_connected(snapshot + snapshot, "ours", "20:0"))

    def test_open_handle_and_present_endpoint_without_subscription_is_unhealthy(self):
        backend = MidoOutputBackend()
        port = SimpleNamespace(closed=False, api="LINUX_ALSA", name="Roland 20:0")
        body = f'Client 128 : "{backend._client_name}" [User Legacy]\n  Port 0 : "output" (R-e-) [In]\n'
        with patch.object(Path, "read_text", return_value=body):
            self.assertFalse(backend.is_output_connected(port))
        with patch.object(Path, "read_text", return_value=body + '    Connecting To: 20:0\n'):
            self.assertTrue(backend.is_output_connected(port))

    def test_native_open_binds_unique_backend_client_names(self):
        from tests.test_midi_physical_output import NativeBoundary
        one, two = MidoOutputBackend(), MidoOutputBackend()
        boundary = NativeBoundary(["Roland 20:0"])
        with boundary.installed():
            first = one.open_output("Roland 20:0")
            second = two.open_output("Roland 20:0")
            first.close()
            second.close()
        self.assertNotEqual(one._client_name, two._client_name)
        self.assertEqual(boundary.outputs[0].calls,
                         [("open_port", 0), ("set_client_name", one._client_name)])
        self.assertEqual(boundary.outputs[1].calls,
                         [("open_port", 0), ("set_client_name", two._client_name)])


class OutputRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.backend = DisconnectableOutput()
        self.adapter = MidiOutputAdapter(self.backend)
        self.adapter.open()
        self.old = self.backend.outputs[0]
        self.transitions = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.backend.open_gate is not None:
            self.backend.open_gate.set()
        self.adapter.stop_monitor()
        self.adapter.close()

    def monitor(self, notify=None):
        self.adapter.start_monitor(notify or self.transitions.append, interval=0.01)

    def recover(self):
        before = len(self.backend.outputs)
        self.old.connected = False  # Endpoint NAME remains present, subscription is gone.
        self.assertTrue(wait_for(lambda: len(self.backend.outputs) > before and self.adapter.is_open, 2))
        return self.backend.outputs[-1]

    def test_same_name_return_reopens_then_fresh_note_exactly_once(self):
        self.monitor()
        fresh = self.recover()
        self.assertTrue(self.old.closed)
        self.assertEqual(self.transitions, [False, True])
        self.adapter.apply(note_on(1, 67, 80))
        self.assertEqual(fresh.sent, [("note_on", 1, 67, 80)])
        self.assertEqual(self.old.sent, [])
        self.assertEqual(self.backend.max_handles, 1)

    def test_owned_only_cleanup_precedes_availability_and_no_replay(self):
        self.adapter.apply(note_on(1, 60, 80))
        self.adapter.apply(sustain(1, 127))
        self.monitor()
        fresh = self.recover()
        self.assertEqual(fresh.sent, [("note_off",1,60,0),("control_change",1,64,0)])
        self.assertEqual(self.adapter.owned_notes, ())
        self.assertEqual(self.adapter.owned_sustain_channels, ())
        self.assertTrue(all(x[0] != 'note_on' for x in fresh.sent))

    def test_partial_cleanup_retries_remaining_ownership_only(self):
        self.adapter.apply(note_on(1,60,80))
        self.adapter.apply(note_on(1,64,80))
        self.adapter.apply(sustain(1,127))
        self.backend.release_failure = True
        self.monitor()
        self.old.connected = False
        self.assertTrue(wait_for(lambda: len(self.backend.outputs) >= 3 and self.adapter.is_open, 2))
        first, second = self.backend.outputs[1:3]
        self.assertTrue(first.closed)
        self.assertEqual(first.sent, [('note_off',1,60,0)])
        self.assertEqual(second.sent, [('note_off',1,64,0),('control_change',1,64,0)])
        self.assertEqual(self.adapter.owned_notes, ())
        self.assertEqual(self.transitions, [False,True])

    def test_old_generation_cannot_send_after_fast_reopen(self):
        generation = self.adapter.generation
        self.monitor()
        fresh = self.recover()
        with self.assertRaises(OutputNotOpenError):
            self.adapter.apply_for_generation(note_on(1,99,80), generation)
        self.assertEqual(fresh.sent, [])

    def test_real_invalid_port_error_only_at_selected_open_retries(self):
        from rtmidi import InvalidPortError
        self.backend.fail_open_once = InvalidPortError('port disappeared')
        self.monitor()
        self.recover()
        self.assertEqual(self.backend.open_calls, 3)
        self.assertIsNone(self.adapter.last_error)

    def test_plain_valueerror_and_runtimeerror_at_open_are_fatal(self):
        for error in [ValueError('ordinary configuration'),RuntimeError('programming fault')]:
            with self.subTest(error=error):
                self.adapter.stop_monitor()
                if not self.adapter.is_open:self.adapter.open()
                self.old = self.backend.outputs[-1]
                self.backend.fail_open_once = error
                before = self.backend.open_calls
                self.monitor()
                self.old.connected = False
                self.assertTrue(wait_for(lambda:self.adapter.last_error is not None,2))
                self.assertEqual(self.backend.open_calls, before+1)
                self.assertFalse(self.adapter.is_open)
                self.assertIn(str(error), self.adapter.last_error)

    def test_invalidport_in_health_probe_is_fatal_not_open_recovery(self):
        from rtmidi import InvalidPortError
        self.backend.is_output_connected = lambda output: (_ for _ in ()).throw(InvalidPortError('probe fault'))
        self.monitor()
        self.assertTrue(wait_for(lambda:self.adapter.last_error is not None,2))
        self.assertEqual(self.backend.open_calls,1)
        self.assertFalse(self.adapter.is_open)

    def test_callback_failure_is_visible_and_not_retried(self):
        calls=[]
        def broken(connected):
            calls.append(connected)
            raise ValueError('observer fault')
        self.monitor(broken)
        self.old.connected=False
        self.assertTrue(wait_for(lambda:self.adapter.last_error is not None,2))
        self.assertEqual(calls,[False])
        self.assertEqual(self.backend.open_calls,1)
        self.assertFalse(self.adapter.is_open)

    def test_ambiguous_endpoints_never_open_arbitrary_port(self):
        self.monitor()
        self.backend.ports=['Roland A','Roland B']
        self.assertTrue(wait_for(lambda: not self.adapter.is_open,2))
        self.assertEqual(self.backend.open_calls,1)
        self.backend.ports=['Roland OUT 20:0']
        self.assertTrue(wait_for(lambda:self.adapter.is_open,2))
        self.assertEqual(self.backend.max_handles,1)

    def test_shutdown_blocked_open_retains_worker_and_no_late_publish(self):
        self.backend.open_entered.clear()
        self.backend.open_gate=threading.Event()
        self.monitor()
        self.old.connected=False
        self.assertTrue(self.backend.open_entered.wait(2))
        with self.assertRaisesRegex(RuntimeError,'did not stop'):
            self.adapter.stop_monitor(timeout=0.01)
        with self.assertRaisesRegex(RuntimeError,'already running'):
            self.monitor()
        self.backend.open_gate.set()
        self.adapter.stop_monitor()
        self.assertFalse(self.adapter.is_open)
        self.assertEqual(self.transitions,[False])
        self.assertEqual(self.backend.max_handles,1)

    def test_concurrent_open_and_duplicate_monitor_create_one_output(self):
        self.adapter.close()
        before=len(self.backend.outputs)
        workers=[threading.Thread(target=self.adapter.open) for _ in range(8)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(2)
        self.assertEqual(len(self.backend.outputs),before+1)
        self.assertEqual(self.backend.max_handles,1)
        self.monitor()
        with self.assertRaisesRegex(RuntimeError,'already running'):self.monitor()

    def test_slow_close_serializes_reopen_without_two_live_handles(self):
        entered, release, opened = threading.Event(), threading.Event(), threading.Event()
        close = self.backend.close_output
        def slow_close(output):
            entered.set()
            release.wait(2)
            close(output)
        self.backend.close_output = slow_close
        closing = threading.Thread(target=self.adapter.close)
        opening = threading.Thread(target=lambda:(self.adapter.open(),opened.set()))
        closing.start()
        self.assertTrue(entered.wait(2))
        opening.start()
        try:
            self.assertFalse(opened.wait(0.03))
        finally:
            release.set()
            closing.join(2)
            opening.join(2)
        self.assertTrue(opened.is_set())
        self.assertEqual(self.backend.max_handles,1)

    def test_invalid_intervals_rejected_without_worker(self):
        for value in [0,-1,float('inf'),float('nan')]:
            with self.assertRaises(ValueError):self.adapter.start_monitor(self.transitions.append,value)

    def test_multiple_cycles_release_owned_state_without_duplicate_attacks(self):
        self.monitor()
        for pitch in [60, 64, 67]:
            self.old = self.backend.outputs[-1]
            self.adapter.apply(note_on(2, pitch, 80))
            self.adapter.apply(sustain(2, 127))
            fresh = self.recover()
            self.assertEqual(fresh.sent, [("note_off", 2, pitch, 0),
                                          ("control_change", 2, 64, 0)])
            self.assertEqual(self.adapter.owned_notes, ())
            self.assertEqual(self.adapter.owned_sustain_channels, ())
            self.assertTrue(self.old.closed)
        self.assertEqual(self.backend.max_handles, 1)
        self.assertEqual(self.transitions, [False, True] * 3)

    def test_retry_cadence_and_monitor_stop_no_background_reopen(self):
        attempts = []
        self.adapter.close()
        def unavailable(port):
            attempts.append(time.monotonic())
            raise OSError("still unplugged")
        self.backend.open_output = unavailable
        with self.assertLogs("piano_core.midi.output", level="DEBUG") as logs:
            self.adapter.start_monitor(self.transitions.append, interval=0.04)
            self.assertTrue(wait_for(lambda:len(attempts) >= 4, 2))
            self.adapter.stop_monitor()
        count = len(attempts)
        self.assertFalse(self.adapter._monitor_thread.is_alive())
        self.assertFalse(threading.Event().wait(0.09))
        self.assertEqual(len(attempts), count)
        self.assertTrue(all(b - a >= 0.035 for a, b in zip(attempts, attempts[1:])))
        self.assertTrue(all(x.startswith("DEBUG:") for x in logs.output))
        self.assertEqual(len(logs.output), count)
        self.assertEqual(self.transitions, [])


class PlaybackLossTests(unittest.TestCase):
    def test_engine_stop_during_retry_ends_worker_and_cannot_resume_on_reopen(self):
        backend = DisconnectableOutput()
        adapter = MidiOutputAdapter(backend)
        adapter.open()
        clock = GatedClock()
        engine = PlaybackEngine(adapter, clock=clock)
        self.addCleanup(adapter.close)
        self.addCleanup(adapter.stop_monitor)
        self.addCleanup(engine.stop)
        engine.play(make_timeline((0,note_on(1,60,80)), (100,note_on(1,62,80))))
        self.assertTrue(clock.wait_parked())
        backend.ports = []
        disconnected = threading.Event()
        adapter.start_monitor(lambda connected:disconnected.set() if not connected else None,
                              interval=0.01)
        self.assertTrue(disconnected.wait(2))
        self.assertFalse(adapter.is_open)
        self.assertTrue(engine.stop())
        self.assertTrue(engine.wait(2))
        # Detached cleanup cannot claim physical delivery; ownership is
        # retained until a fresh physical handle accepts note-off.
        self.assertEqual(engine.state, PlaybackState.FAILED)
        self.assertEqual(adapter.owned_notes, ((1,60),))
        backend.ports = ['Roland OUT 20:0']
        self.assertTrue(wait_for(lambda:len(backend.outputs)==2 and adapter.is_open,2))
        self.assertEqual(backend.outputs[-1].sent, [('note_off',1,60,0)])
        self.assertEqual(adapter.owned_notes, ())
        self.assertEqual(engine.state, PlaybackState.FAILED)
        self.assertEqual(engine.events_sent, 1)

    def test_playing_and_paused_loss_wake_worker_without_resume(self):
        for paused in [False,True]:
            with self.subTest(paused=paused):
                backend=DisconnectableOutput()
                adapter=MidiOutputAdapter(backend)
                adapter.open()
                clock=GatedClock()
                engine=PlaybackEngine(adapter,clock=clock)
                self.addCleanup(engine.stop)
                timeline=make_timeline((0,note_on(1,60,80)),(100,note_on(1,62,80)))
                engine.play(timeline)
                self.assertTrue(clock.wait_parked())
                if paused:
                    engine.pause()
                    self.assertTrue(wait_for(lambda:not adapter.owned_notes,2))
                adapter.close()
                engine.output_disconnected()
                self.assertTrue(engine.wait(2))
                self.assertEqual(engine.state,PlaybackState.FAILED)
                adapter.open()
                sent=backend.outputs[-1].sent
                self.assertFalse(any(x[0]=='note_on' for x in sent))
                self.assertEqual(engine.events_sent,1)
                adapter.close()


class ApplicationRecoveryProbe(unittest.TestCase):
    def test_application_shutdown_during_retry_joins_worker_without_late_open(self):
        backend = DisconnectableOutput()
        app = Application.from_env({'PIANO_CORE_MODE':'test','PIANO_CORE_LOG_LEVEL':'ERROR',
              'PIANO_CORE_MIDI_OUTPUT_ENABLED':'true'}, midi_output_backend_factory=lambda:backend)
        self.addCleanup(app.shutdown)
        app.start()
        attempted = threading.Event()
        def unavailable(port):
            attempted.set()
            raise OSError('unplugged')
        backend.open_output = unavailable
        backend.outputs[0].connected = False
        self.assertTrue(attempted.wait(2))
        worker = app.midi_output._monitor_thread
        app.shutdown()
        self.assertFalse(worker.is_alive())
        self.assertFalse(app.midi_output.is_open)
        self.assertTrue(all(x.closed for x in backend.outputs))
        backend.ports = ['Roland OUT 20:0']
        self.assertFalse(threading.Event().wait(0.25))
        self.assertEqual(len(backend.outputs), 1)

    def test_application_recovers_same_name_without_input_restart(self):
        backend=DisconnectableOutput()
        app=Application.from_env({'PIANO_CORE_MODE':'test','PIANO_CORE_LOG_LEVEL':'ERROR',
             'PIANO_CORE_MIDI_OUTPUT_ENABLED':'true'},midi_output_backend_factory=lambda:backend)
        self.addCleanup(app.shutdown)
        app.start()
        backend.outputs[0].connected=False
        self.assertTrue(wait_for(lambda:len(backend.outputs)==2 and app.midi_output.is_open,1))
        app.midi_output.apply(note_on(1,65,70))
        self.assertEqual(backend.outputs[1].sent,[('note_on',1,65,70)])
        self.assertIsNone(app.midi_adapter)
