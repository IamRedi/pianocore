"""Close failure at the native boundary: no real MIDI device operations."""
import json
import importlib.metadata
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from piano_core.app import Application
from piano_core.midi.backends import MidoOutputBackend
from piano_core.midi.output import MidiOutputAdapter, OutputNotOpenError
from piano_core.midi.output_events import note_on, sustain
from tests.test_midi_physical_output import NativeBoundary
from tests.test_midi_input import wait_for
import rtmidi
from tests.test_midi_output_recovery import DisconnectableOutput
from tests.test_playback_engine import GatedClock, make_timeline
from piano_core.playback.engine import PlaybackEngine, PlaybackState
from tests import test_midi_output_recovery_api as api_fixture


class CloseBoundary(NativeBoundary):
    def __init__(self):
        super().__init__(["Other 31:0", "Roland OUT 48:7"])
        self.failures = 0
        self.phase = "before"
        self.close_attempts = 0
        self.max_active = 0
        self.close_entered = threading.Event()
        self.close_gate = None
        self.close_times = []

    def active(self):
        return [x for x in self.outputs if any(c[0]=='open_port' for c in x.calls)
                and not x.closed and not x.deleted]

    def output(self, **kwargs):
        native = super().output(**kwargs)
        type(native).is_deleted = property(lambda x:x.deleted)
        opening, closing, deleting = native.open_port, native.close_port, native.delete
        def open_port(index):
            opening(index)
            self.max_active = max(self.max_active, len(self.active()))
        def close_port():
            if native.deleted:
                raise AssertionError("deleted native pointer used")
            self.close_attempts += 1
            self.close_times.append(time.monotonic())
            self.close_entered.set()
            if self.close_gate is not None:
                self.close_gate.wait(2)
            failing = self.failures > 0
            if failing and self.phase != "deleted":
                self.failures -= 1
                if self.phase == "after":
                    closing()
                raise rtmidi.SystemError("native close failure " + self.phase)
            closing()
        def delete():
            deleting()
            if native.calls and self.failures and self.phase == "deleted":
                self.failures -= 1
                raise rtmidi.SystemError("native close failure after deletion")
        native.open_port, native.close_port, native.delete = open_port, close_port, delete
        return native

    def snapshot(self, path, *args, **kwargs):
        if str(path) != '/proc/asound/seq/clients':
            raise AssertionError('unexpected kernel snapshot path')
        return ''.join(f'Client {128+i} : "{x.client_name}" [User Legacy]\n'
                       + ('    Connecting To: 48:7\n' if x.connected else '')
                       for i,x in enumerate(self.outputs) if x.calls and not x.closed)


class CloseSafetyTests(unittest.TestCase):
    def test_application_output_start_cannot_replace_uncertain_or_shutdown_owner(self):
        boundary = CloseBoundary()
        with boundary.installed():
            app = Application.from_env({'PIANO_CORE_MODE':'test','PIANO_CORE_LOG_LEVEL':'ERROR',
                 'PIANO_CORE_MIDI_OUTPUT_ENABLED':'true'},midi_output_backend_factory=MidoOutputBackend)
            app._start_midi_output()
            owner = app.midi_output
            boundary.failures = 1000
            with self.assertRaises(RuntimeError):owner.close()
            app._start_midi_output()
            self.assertIs(app.midi_output,owner)
            self.assertTrue(owner.close_pending)
            self.assertEqual(len(boundary.active()),1)
            app.shutdown()
            app._start_midi_output()
            self.assertIs(app.midi_output,owner)
            self.assertTrue(owner.close_pending)
            self.assertEqual(boundary.max_active,1)
            boundary.failures = 0
            owner.close()
            self.assertEqual(len(boundary.active()),0)

    def test_active_stop_races_close_failure_without_replay_or_second_handle(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        engine = PlaybackEngine(adapter,clock=GatedClock())
        notify_entered, notify_release = threading.Event(), threading.Event()
        def notify(connected):
            if not connected:
                notify_entered.set()
                notify_release.wait(2)
                engine.output_disconnected()
        with boundary.installed(), patch.object(Path,'read_text',lambda path,*a,**kw:boundary.snapshot(path,*a,**kw)):
            adapter.open()
            old = boundary.outputs[-1]
            engine.play(make_timeline((0,note_on(1,60,80)),(100,note_on(1,62,80))))
            self.assertTrue(engine._clock.wait_parked())
            boundary.failures = 1000
            old.connected = False
            adapter.start_monitor(notify,interval=0.01)
            try:
                self.assertTrue(notify_entered.wait(2))
                self.assertTrue(adapter.close_pending)
                self.assertTrue(engine.stop())
                self.assertTrue(engine.wait(2))
                self.assertEqual(engine.state,PlaybackState.FAILED)
                self.assertEqual(adapter.owned_notes,((1,60),))
                notify_release.set()
                boundary.failures = 0
                self.assertTrue(wait_for(lambda:adapter.is_open,2))
                fresh = next(x for x in reversed(boundary.outputs) if x.calls and not x.closed)
                self.assertEqual(fresh.sent,[[0x80,60,0]])
                self.assertEqual(engine.state,PlaybackState.FAILED)
                self.assertEqual(engine.events_sent,1)
                self.assertEqual(boundary.max_active,1)
            finally:
                notify_release.set()
                engine.stop()
                adapter.stop_monitor()
                boundary.failures = 0
                adapter.close()

    def test_pinned_native_contract_has_safe_deleted_property(self):
        self.assertEqual(importlib.metadata.version('python-rtmidi'),'1.5.8')
        self.assertTrue(hasattr(rtmidi.MidiOut,'is_deleted'))

    def test_shutdown_during_fresh_cleanup_never_publishes_late_handle(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        entered, release = threading.Event(), threading.Event()
        errors = []
        with boundary.installed():
            adapter.open()
            adapter.apply(note_on(1,60,80))
            adapter.close()
            original = boundary.output
            def gated_output(**kwargs):
                native = original(**kwargs)
                sending = native.send_message
                def send(message):
                    if message[0] == 0x80:
                        entered.set()
                        release.wait(2)
                    sending(message)
                native.send_message = send
                return native
            with patch.object(rtmidi,'MidiOut',gated_output):
                def opening():
                    try:adapter.open()
                    except Exception as exc:errors.append(exc)
                thread = threading.Thread(target=opening)
                thread.start()
                self.assertTrue(entered.wait(2))
                adapter.stop_monitor(shutdown=True)
                release.set()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors),1)
                self.assertIsInstance(errors[0],OutputNotOpenError)
                self.assertFalse(adapter.is_open)
                self.assertFalse(adapter.close_pending)
                self.assertEqual(len(boundary.active()),0)
                self.assertEqual(boundary.max_active,1)
                self.assertEqual(adapter.owned_notes,())

    def test_callback_error_with_close_failure_remains_fatal_and_owned(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        calls = []
        def broken(connected):
            calls.append(connected)
            raise ValueError('observer programming error')
        with boundary.installed(), patch.object(Path,'read_text',lambda path,*a,**kw:boundary.snapshot(path,*a,**kw)):
            adapter.open()
            old = boundary.outputs[-1]
            boundary.failures = 1000
            old.connected = False
            adapter.start_monitor(broken,interval=0.01)
            self.assertTrue(wait_for(lambda:not adapter._monitor_thread.is_alive(),2))
            self.assertIn('observer programming error',adapter.last_error)
            self.assertIn('retained close failure',adapter.last_error)
            self.assertEqual(calls,[False])
            self.assertTrue(adapter.close_pending)
            self.assertFalse(adapter.is_open)
            self.assertEqual(boundary.active(),[old])
            boundary.failures = 0
            adapter.close()

    def test_application_shutdown_retains_failed_close_and_never_reopens(self):
        for disconnected in [False, True]:
            with self.subTest(disconnected=disconnected):
                boundary = CloseBoundary()
                with boundary.installed(), patch.object(Path,'read_text',lambda path,*a,**kw:boundary.snapshot(path,*a,**kw)):
                    app = Application.from_env({'PIANO_CORE_MODE':'test','PIANO_CORE_LOG_LEVEL':'ERROR',
                         'PIANO_CORE_MIDI_OUTPUT_ENABLED':'true'},midi_output_backend_factory=MidoOutputBackend)
                    app.start()
                    adapter = app.midi_output
                    old = boundary.active()[0]
                    boundary.failures = 1000
                    if disconnected:
                        old.connected = False
                        self.assertTrue(wait_for(lambda:adapter.close_pending,2))
                    app.shutdown()
                    self.assertFalse(adapter._monitor_thread.is_alive())
                    self.assertFalse(adapter.is_open)
                    self.assertTrue(adapter.close_pending)
                    self.assertIs(adapter._retired_output._rt,old)
                    self.assertEqual(boundary.active(),[old])
                    attempts = boundary.close_attempts
                    self.assertFalse(threading.Event().wait(0.25))
                    self.assertEqual(boundary.close_attempts,attempts)
                    with self.assertRaises(OutputNotOpenError):adapter.open()
                    with self.assertRaises(OutputNotOpenError):adapter.start_monitor(lambda _:None)
                    boundary.failures = 0
                    adapter.close()  # Explicit post-shutdown resource reconciliation only.
                    self.assertFalse(adapter.close_pending)
                    self.assertEqual(boundary.active(),[])
                    self.assertEqual(boundary.max_active,1)

    def test_application_failed_initialization_keeps_cleanup_owner_without_retrying_config(self):
        boundary = CloseBoundary()
        with boundary.installed():
            boundary.naming_error = ValueError('invalid identity configuration')
            boundary.failures = 1000
            app = Application.from_env({'PIANO_CORE_MODE':'test','PIANO_CORE_LOG_LEVEL':'ERROR',
                 'PIANO_CORE_MIDI_OUTPUT_ENABLED':'true'},midi_output_backend_factory=MidoOutputBackend)
            app.start()
            self.assertIsNotNone(app.midi_output)
            self.assertTrue(app.midi_output.close_pending)
            self.assertFalse(app.midi_output.is_open)
            self.assertIsNone(app.midi_output._monitor_thread)
            self.assertEqual(len(boundary.active()),1)
            app.shutdown()
            self.assertTrue(app.midi_output.close_pending)
            boundary.failures = 0
            app.midi_output.close()
            self.assertEqual(len(boundary.active()),0)
            self.assertEqual(boundary.max_active,1)

    def test_close_once_then_reconcile_and_fresh_send_once(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        with boundary.installed():
            adapter.open()
            old = boundary.outputs[-1]
            adapter.apply(note_on(2,64,90))
            adapter.apply(sustain(2,127))
            generation = adapter.generation
            boundary.failures = 1
            with self.assertRaisesRegex(RuntimeError,'close unresolved'):
                adapter.close()
            self.assertTrue(adapter.close_pending)
            self.assertFalse(adapter.is_open)
            self.assertEqual(adapter.owned_notes, ((2,64),))
            self.assertEqual(adapter.owned_sustain_channels, (2,))
            with self.assertRaises(OutputNotOpenError):
                adapter.apply_for_generation(note_on(2,70,90),generation)
            adapter.open()
            fresh = boundary.outputs[-1]
            self.assertTrue(old.closed and old.deleted)
            self.assertEqual(fresh.sent, [[0x81,64,0],[0xB1,64,0]])
            adapter.apply(note_on(2,67,90))
            self.assertEqual(fresh.sent.count([0x91,67,90]),1)
            self.assertEqual(boundary.max_active,1)
            self.assertFalse(adapter.close_pending)
            self.assertIsNone(adapter.last_error)
            adapter.stop_automated_notes()
            adapter.close()

    def test_exception_after_release_still_requires_confirmed_close(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        with boundary.installed():
            adapter.open()
            old = boundary.outputs[-1]
            boundary.failures, boundary.phase = 1, 'after'
            with self.assertRaises(RuntimeError):adapter.close()
            self.assertTrue(old.closed)
            self.assertFalse(old.deleted)
            self.assertTrue(adapter.close_pending)
            adapter.open()
            self.assertTrue(old.deleted)
            self.assertEqual(boundary.max_active,1)
            adapter.close()

    def test_exception_after_delete_never_dereferences_deleted_native(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        with boundary.installed():
            adapter.open()
            old = boundary.outputs[-1]
            boundary.failures, boundary.phase = 1, 'deleted'
            with self.assertRaises(RuntimeError):adapter.close()
            self.assertTrue(old.deleted)
            self.assertTrue(adapter.close_pending)
            attempts = boundary.close_attempts
            adapter.open()  # Reconciles via safe native is_deleted property.
            self.assertEqual(boundary.close_attempts, attempts)
            self.assertEqual(boundary.max_active,1)
            adapter.close()

    def test_repeated_monitor_ticks_quarantine_without_log_storm_then_recover(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        transitions = []
        with boundary.installed(), patch.object(Path,'read_text',lambda path,*a,**kw:boundary.snapshot(path,*a,**kw)):
            adapter.open()
            old = boundary.outputs[-1]
            boundary.failures = 1000
            old.connected = False
            with self.assertLogs('piano_core.midi.output',level='DEBUG') as logs:
                adapter.start_monitor(transitions.append,interval=0.04)
                try:
                    self.assertTrue(wait_for(lambda:boundary.close_attempts>=4,2))
                    self.assertFalse(adapter.is_open)
                    self.assertTrue(adapter.close_pending)
                    self.assertEqual(transitions,[False])
                    self.assertEqual(boundary.active(),[old])
                    self.assertEqual(boundary.max_active,1)
                    self.assertTrue(all(b-a>=0.035 for a,b in zip(boundary.close_times,boundary.close_times[1:])))
                    boundary.failures = 0
                    self.assertTrue(wait_for(lambda:adapter.is_open,2))
                    self.assertTrue(wait_for(lambda:transitions==[False,True],2))
                finally:
                    adapter.stop_monitor()
                    boundary.failures = 0
                    adapter.close()
            self.assertEqual(sum(x.startswith('WARNING:') for x in logs.output),1)
            self.assertFalse(any(x.startswith('ERROR:') for x in logs.output))
            self.assertLessEqual(len(logs.output),boundary.close_attempts+5)
            self.assertEqual(boundary.max_active,1)

    def test_late_close_shutdown_serializes_and_forbids_queued_reopen(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        with boundary.installed():
            adapter.open()
            boundary.failures = 1
            with self.assertRaises(RuntimeError):adapter.close()
            boundary.close_entered.clear()
            boundary.close_gate = threading.Event()
            errors = []
            def reopen():
                try:adapter.open()
                except Exception as exc:errors.append(exc)
            thread = threading.Thread(target=reopen)
            thread.start()
            self.assertTrue(boundary.close_entered.wait(2))
            adapter.stop_monitor(shutdown=True)
            boundary.close_gate.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors),1)
            self.assertIsInstance(errors[0],OutputNotOpenError)
            self.assertFalse(adapter.is_open)
            self.assertFalse(adapter.close_pending)
            self.assertEqual(len(boundary.active()),0)
            self.assertEqual(boundary.max_active,1)
            with self.assertRaises(OutputNotOpenError):adapter.open()
            with self.assertRaises(OutputNotOpenError):adapter.start_monitor(lambda _:None)

    def test_open_failure_after_close_success_never_revives_old_handle(self):
        boundary = CloseBoundary()
        backend = MidoOutputBackend()
        adapter = MidiOutputAdapter(backend)
        with boundary.installed():
            adapter.open()
            old = boundary.outputs[-1]
            boundary.failures = 1
            with self.assertRaises(RuntimeError):adapter.close()
            with patch.object(backend,'open_output',side_effect=OSError('new open unavailable')):
                with self.assertRaises(OSError):adapter.open()
            self.assertTrue(old.deleted)
            self.assertFalse(adapter.close_pending)
            self.assertFalse(adapter.is_open)
            self.assertEqual(len(boundary.active()),0)
            adapter.open()
            self.assertEqual(boundary.max_active,1)
            adapter.close()

    def test_initialization_close_failure_retains_unreturned_handle(self):
        boundary = CloseBoundary()
        backend = MidoOutputBackend()
        adapter = MidiOutputAdapter(backend)
        with boundary.installed():
            boundary.naming_error = ValueError('invalid identity configuration')
            boundary.failures = 1000
            with self.assertRaisesRegex(RuntimeError,'initialization failed'):
                adapter.open()
            self.assertFalse(adapter.is_open)
            self.assertTrue(adapter.close_pending)
            old = boundary.active()[0]
            with self.assertRaisesRegex(RuntimeError,'close unresolved'):adapter.open()
            self.assertEqual(boundary.active(),[old])
            boundary.failures = 0
            boundary.naming_error = None
            adapter.open()
            self.assertTrue(old.deleted)
            self.assertEqual(boundary.max_active,1)
            adapter.close()

    def test_normal_return_without_release_is_not_close_confirmation(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        with boundary.installed():
            adapter.open()
            handle = adapter._output
            closing = handle.close
            with patch.object(handle,'close',return_value=None):
                with self.assertRaises(RuntimeError):adapter.close()
                with self.assertRaises(RuntimeError):adapter.open()
                self.assertTrue(adapter.close_pending)
                self.assertFalse(adapter.is_open)
                self.assertEqual(boundary.max_active,1)
            adapter.close()
            self.assertTrue(handle.closed)

    def test_fresh_owned_cleanup_and_close_both_fail_without_orphan(self):
        backend = DisconnectableOutput()
        adapter = MidiOutputAdapter(backend)
        adapter.open()
        adapter.apply(note_on(1,64,80))
        adapter.apply(sustain(1,127))
        adapter.close()
        closing = backend.close_output
        backend.close_output = lambda _:(_ for _ in ()).throw(OSError('failed candidate close'))
        backend.release_failure = True
        with self.assertRaises(RuntimeError):adapter.open()
        self.assertTrue(adapter.close_pending)
        self.assertFalse(adapter.is_open)
        self.assertEqual(adapter.owned_notes,((1,64),))
        with self.assertRaises(RuntimeError):adapter.open()
        self.assertEqual(len(backend.outputs),2)
        self.assertEqual(backend.max_handles,1)
        backend.close_output = closing
        adapter.open()
        self.assertTrue(backend.outputs[1].closed)
        self.assertEqual(backend.outputs[-1].sent,[('note_off',1,64,0),('control_change',1,64,0)])
        self.assertEqual(adapter.owned_notes,())
        self.assertEqual(adapter.owned_sustain_channels,())
        self.assertEqual(backend.max_handles,1)
        adapter.close()

    def test_close_failure_faults_playback_and_fresh_play_is_explicit_once(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        engine = PlaybackEngine(adapter,clock=GatedClock())
        transitions = []
        def notify(connected):
            transitions.append(connected)
            if not connected:engine.output_disconnected()
        with boundary.installed(), patch.object(Path,'read_text',lambda path,*a,**kw:boundary.snapshot(path,*a,**kw)):
            adapter.open()
            old = boundary.outputs[-1]
            timeline = make_timeline((0,note_on(1,60,80)),(100,note_on(1,62,80)))
            engine.play(timeline)
            self.assertTrue(engine._clock.wait_parked())
            boundary.failures = 1000
            old.connected = False
            adapter.start_monitor(notify,interval=0.01)
            try:
                self.assertTrue(engine.wait(2))
                self.assertEqual(engine.state,PlaybackState.FAILED)
                self.assertFalse(engine.stop())
                self.assertTrue(adapter.close_pending)
                self.assertEqual(adapter.owned_notes,((1,60),))
                boundary.failures = 0
                self.assertTrue(wait_for(lambda:adapter.is_open,2))
                fresh = next(x for x in reversed(boundary.outputs) if x.calls and not x.closed)
                self.assertEqual(fresh.sent,[[0x80,60,0]])
                self.assertEqual(engine.state,PlaybackState.FAILED)
                engine.play(timeline)
                self.assertTrue(engine._clock.wait_parked())
                self.assertEqual(fresh.sent.count([0x90,60,80]),1)
                engine.stop()
                self.assertEqual(adapter.owned_notes,())
                self.assertEqual(boundary.max_active,1)
            finally:
                engine.stop()
                adapter.stop_monitor()
                boundary.failures = 0
                adapter.close()

    def test_reviewer_close_failure_never_allows_second_active_output(self):
        boundary = CloseBoundary()
        adapter = MidiOutputAdapter(MidoOutputBackend())
        with boundary.installed():
            adapter.open()
            old_handle = adapter._output  # Keep probe lifetime deterministic even on broken source.
            old = old_handle._rt
            boundary.failures = 2
            try:
                adapter.close()
            except Exception:
                pass  # The probe measures retained native ownership, not error syntax.
            self.assertFalse(old.closed)
            self.assertFalse(old.deleted)
            try:
                adapter.open()
            except Exception:
                pass
            observed = {'old_active':not old.closed and not old.deleted,
                        'adapter_available':adapter.is_open,
                        'active_handles':len(boundary.active()),
                        'max_active_handles':boundary.max_active}
            print(json.dumps(observed), flush=True)
            try:
                self.assertLessEqual(len(boundary.active()),1)
                self.assertEqual(boundary.max_active,1)
                self.assertFalse(adapter.is_open)
                self.assertTrue(adapter.close_pending)
                self.assertIs(adapter._retired_output._rt, old)
            finally:
                boundary.failures = 0
                adapter.close()
                old_handle.close()


class APICloseFailureTests(unittest.TestCase):
    setUp = api_fixture.APIRecoveryTests.setUp
    api = api_fixture.APIRecoveryTests.api

    def test_close_failure_releases_activity_and_preserves_silent_practice_before_fresh_play(self):
        closing = self.output.close_output
        unresolved = threading.Event()
        unresolved.set()
        def failing_close(output):
            if unresolved.is_set():raise OSError('native close before release')
            closing(output)
        self.output.close_output = failing_close
        self.addCleanup(unresolved.clear)
        self.api('/playback/play',{'song_id':self.song})
        self.assertTrue(self.output.attack.wait(2))
        old = self.output.outputs[0]
        old.connected = False
        self.assertTrue(wait_for(lambda:self.api('/playback')['state']=='FAILED',2))
        self.assertTrue(self.app.midi_output.close_pending)
        self.assertFalse(old.closed)
        self.assertFalse(self.api('/playback')['available'])
        self.assertTrue(wait_for(lambda:self.app.playback._advisory is None,2))
        self.api('/learning/start',{'song_id':self.song})
        before = self.api('/learning')
        unresolved.clear()
        self.assertTrue(wait_for(lambda:len(self.output.outputs)==2 and self.app.midi_output.is_open,2))
        fresh = self.output.outputs[1]
        self.assertTrue(old.closed)
        self.assertEqual(fresh.sent,[('note_off',1,72,0)])
        after = self.api('/learning')
        for field in ['session_id','song_id','state','target','completed_steps','version']:
            self.assertEqual(before[field],after[field])
        self.api('/learning/stop',{})
        self.output.attack.clear()
        self.api('/playback/play',{'song_id':self.song})
        self.assertTrue(self.output.attack.wait(2))
        self.assertEqual([x for x in fresh.sent if x[0]=='note_on'],[('note_on',1,72,80)])
        self.api('/playback/stop',{})
        self.assertEqual(self.app.midi_output.owned_notes,())
        self.assertEqual(self.output.max_handles,1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
