"""Mandatory FR-01 regression gate: real adapter, controlled transport faults.

Condition/Event/Queue barriers synchronize lifecycle; no retry-until-pass,
physical devices, patched musical state, or arbitrary sleeps.
"""
import queue
import threading
import time
import unittest

from piano_core.midi.input import MidiInputAdapter, ConnectionState
from tests.test_midi_input import msg


class RetryGate:
    """Test Event proxy: exposes each reconnect wait and releases it explicitly."""
    def __init__(self, delay):
        self.delay = delay
        self.event = threading.Event()
        self.condition = threading.Condition()
        self.permits = 0
        self.waits = queue.Queue()

    def is_set(self):
        return self.event.is_set()

    def set(self):
        with self.condition:
            self.event.set()
            self.condition.notify_all()

    def clear(self):
        self.event.clear()

    def wait(self, timeout=None):
        if timeout != self.delay:
            return self.event.wait(timeout)
        with self.condition:
            self.waits.put(timeout)
            if not self.condition.wait_for(lambda: self.permits or self.is_set(), 3):
                raise AssertionError('test did not release reconnect barrier')
            if self.permits:
                self.permits -= 1
            return self.is_set()

    def release(self):
        with self.condition:
            self.permits += 1
            self.condition.notify_all()


class Handle:
    def __init__(self, backend):
        self.backend = backend
        self.pending = queue.Queue()
        self.closed = threading.Event()
        self.close_count = 0
        self.close_error = False
        self.close_entered = threading.Event()
        self.close_release = None

    def close(self):
        self.close_count += 1
        self.close_entered.set()
        if self.close_release is not None and not self.close_release.wait(5):
            raise AssertionError('cleanup barrier not released')
        self.closed.set()
        if self.close_error:
            raise OSError('close transport fault')


class Backend:
    def __init__(self):
        self.ports = ['Roland test']
        self.handles = []
        self.opened = queue.Queue()
        self.discovery_errors = queue.Queue()
        self.open_errors = queue.Queue()
        self.discovery_entered = None
        self.discovery_release = None
        self.open_entered = None
        self.open_release = None
        self.list_count = 0
        self.readers = 0
        self.max_readers = 0
        self.reader_threads = set()

    def list_input_ports(self):
        self.list_count += 1
        if self.discovery_entered:
            self.discovery_entered.set()
            if not self.discovery_release.wait(3):
                raise AssertionError('discovery barrier not released')
        if not self.discovery_errors.empty():
            raise self.discovery_errors.get_nowait()
        return list(self.ports)

    def open_input(self, port):
        if self.open_entered:
            self.open_entered.set()
            if not self.open_release.wait(3):
                raise AssertionError('open barrier not released')
        if not self.open_errors.empty():
            raise self.open_errors.get_nowait()
        if any(not h.closed.is_set() for h in self.handles):
            raise AssertionError('two active handles')
        handle = Handle(self)
        self.handles.append(handle)
        self.opened.put(handle)
        return handle

    def iter_pending(self, handle):
        self.readers += 1
        self.max_readers = max(self.readers, self.max_readers)
        self.reader_threads.add(threading.get_ident())
        try:
            if handle.closed.is_set():
                raise AssertionError('read from closed connection')
            while not handle.pending.empty():
                item = handle.pending.get_nowait()
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            self.readers -= 1


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.received = queue.Queue()
        self.states = queue.Queue()
        self.adapter = MidiInputAdapter(self.backend, event_callback=self.received.put,
            on_connection_state=self.states.put, reconnect_delay=.05,
            poll_interval=.001, port_check_interval=60)
        self.gate = RetryGate(.05)
        self.adapter._stop_requested = self.gate
        self.addCleanup(self.adapter.stop)

    def start(self):
        self.adapter.start()
        h = self.backend.opened.get(timeout=2)
        self.assertEqual(self.states.get(timeout=2), ConnectionState.CONNECTING)
        self.assertEqual(self.states.get(timeout=2), ConnectionState.CONNECTED)
        return h

    def fault(self, handle):
        handle.pending.put(OSError('read transport fault'))
        self.assertTrue(handle.closed.wait(2))
        self.assertEqual(self.states.get(timeout=2), ConnectionState.DISCONNECTED)
        self.assertEqual(self.gate.waits.get(timeout=1), .05)
        self.assertIsNone(self.adapter.connected_port)
        self.assertTrue(self.adapter._thread.is_alive())

    def reconnect(self):
        self.gate.release()
        h = self.backend.opened.get(timeout=2)
        self.assertEqual(self.states.get(timeout=2), ConnectionState.CONNECTING)
        self.assertEqual(self.states.get(timeout=2), ConnectionState.CONNECTED)
        return h

    def test_read_failure_recovers_and_delivers_new_event_once(self):
        first = self.start()
        first.pending.put(msg('note_on', note=60))
        self.assertEqual(self.received.get(timeout=2).note, 60)
        self.fault(first)
        # Stale queue remains attached to failed handle, never to its successor.
        first.pending.put(msg('note_on', note=99))
        second = self.reconnect()
        second.pending.put(msg('note_on', note=64))
        self.assertEqual(self.received.get(timeout=2).note, 64)
        self.adapter.stop()
        self.assertTrue(self.received.empty())
        self.assertEqual(self.adapter.events_received, 2)
        self.assertEqual(first.close_count, 1)
        self.assertEqual(second.close_count, 1)

    def test_consecutive_failures_keep_one_reader_and_bounded_retries(self):
        handle = self.start()
        for note in range(60,65):
            handle.pending.put(msg('note_on', note=note))
            self.assertEqual(self.received.get(timeout=2).note, note)
            self.fault(handle)
            before = len(self.backend.handles)
            self.assertEqual(self.backend.readers, 0)
            self.assertEqual(len(self.backend.handles), before)
            handle = self.reconnect()
        self.adapter.stop()
        self.assertEqual(self.backend.max_readers, 1)
        self.assertEqual(len(self.backend.reader_threads), 1)
        self.assertTrue(self.received.empty())
        self.assertEqual(self.adapter.events_received, 5)

    def test_missing_device_then_return_uses_selection_and_retry(self):
        h = self.start()
        self.backend.ports = ['Unrelated synth']
        self.fault(h)
        self.gate.release()
        self.assertEqual(self.gate.waits.get(timeout=2), .05)
        self.assertEqual(len(self.backend.handles), 1)
        self.assertEqual(self.states.get(timeout=1), ConnectionState.CONNECTING)
        self.assertEqual(self.states.get(timeout=1), ConnectionState.DISCONNECTED)
        self.backend.ports = ['Roland test']
        self.reconnect()

    def test_discovery_and_open_transport_errors_retry(self):
        self.backend.discovery_errors.put(OSError('discovery fault'))
        self.backend.open_errors.put(OSError('open fault'))
        self.adapter.start()
        self.gate.waits.get(timeout=2)
        self.assertEqual(len(self.backend.handles), 0)
        self.gate.release()
        self.gate.waits.get(timeout=2)
        self.assertEqual(len(self.backend.handles), 0)
        self.gate.release()
        h = self.backend.opened.get(timeout=2)
        h.pending.put(msg('note_on'))
        self.assertEqual(self.received.get(timeout=2).note, 60)

    def test_close_error_does_not_hide_read_fault_or_block_recovery(self):
        h = self.start()
        h.close_error = True
        with self.assertLogs('piano_core.midi.input', level='WARNING') as logs:
            self.fault(h)
            self.reconnect()
        self.assertTrue(any('read transport fault' in x for x in logs.output))
        self.assertTrue(any('close transport fault' in x for x in logs.output))
        self.assertEqual(h.close_count, 1)

    def test_stop_interrupts_backoff_and_no_open_after_stop(self):
        self.fault(self.start())
        before = len(self.backend.handles)
        self.adapter.stop(.5)
        self.assertFalse(self.adapter._thread.is_alive())
        self.gate.release()
        self.adapter.stop(.5)
        self.assertEqual(len(self.backend.handles), before)

    def test_real_event_backoff_is_interruptible(self):
        self.adapter._stop_requested = threading.Event()
        self.adapter._reconnect_delay = 30
        h = self.start()
        h.pending.put(OSError('read transport fault'))
        self.assertTrue(h.closed.wait(2))
        started = time.monotonic()
        self.adapter.stop(.5)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertLess(time.monotonic()-started, .5)
        self.assertEqual(len(self.backend.handles), 1)

    def test_stop_during_discovery_prevents_open(self):
        self.backend.discovery_entered = threading.Event()
        self.backend.discovery_release = threading.Event()
        self.adapter.start()
        self.assertTrue(self.backend.discovery_entered.wait(2))
        self.adapter.stop(.01)
        self.backend.discovery_release.set()
        self.adapter.stop(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertEqual(self.backend.handles, [])

    def test_open_racing_stop_is_closed_without_connected_or_events(self):
        self.backend.open_entered = threading.Event()
        self.backend.open_release = threading.Event()
        self.adapter.start()
        self.assertTrue(self.backend.open_entered.wait(2))
        self.adapter.stop(.01)
        with self.assertRaises(RuntimeError):
            self.adapter.start()  # Still owns blocked worker; no replacement thread.
        self.backend.open_release.set()
        self.adapter.stop(1)
        self.assertTrue(self.backend.handles[0].closed.is_set())
        self.assertNotIn(ConnectionState.CONNECTED, list(self.states.queue))
        self.assertTrue(self.received.empty())

    def test_concurrent_start_and_repeated_start_stop_no_duplicate(self):
        for _ in range(3):
            barrier = threading.Barrier(3)
            outcomes = queue.Queue()
            def start():
                barrier.wait()
                try:
                    self.adapter.start()
                    outcomes.put('started')
                except RuntimeError:
                    outcomes.put('refused')
            threads = [threading.Thread(target=start) for _ in range(2)]
            for t in threads:t.start()
            barrier.wait()
            for t in threads:t.join(1)
            self.assertCountEqual([outcomes.get(timeout=1),outcomes.get(timeout=1)], ['started','refused'])
            self.backend.opened.get(timeout=2)
            self.adapter.stop()
            self.assertFalse(self.adapter._thread.is_alive())
        self.assertTrue(all(h.close_count==1 for h in self.backend.handles))

    def test_ambiguity_after_fault_fails_closed(self):
        self.fault(self.start())
        self.backend.ports = ['Roland A','Roland B']
        self.gate.release()
        self.adapter._thread.join(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertIn('ambiguous', self.adapter.last_error)
        self.assertEqual(len(self.backend.handles),1)

    def test_non_transport_read_bug_is_fatal_and_reported(self):
        h = self.start()
        with self.assertLogs('piano_core.midi.input', level='ERROR'):
            h.pending.put(ValueError('programming defect'))
            self.assertTrue(h.closed.wait(2))
            self.adapter._thread.join(1)
        self.assertIn('programming defect', self.adapter.last_error)
        self.assertEqual(len(self.backend.handles),1)
        self.assertTrue(self.gate.waits.empty())

    def test_callback_oserror_is_not_a_transport_retry(self):
        def fail(event):
            raise OSError('consumer defect')
        self.adapter._event_callback = fail
        h = self.start()
        with self.assertLogs('piano_core.midi.input', level='ERROR'):
            h.pending.put(msg('note_on'))
            self.assertTrue(h.closed.wait(2))
            self.adapter._thread.join(1)
        self.assertIn('consumer defect', self.adapter.last_error)
        self.assertTrue(self.gate.waits.empty())

    def test_invalid_open_configuration_is_fatal_not_retry(self):
        self.backend.open_errors.put(ValueError('invalid configuration'))
        with self.assertLogs('piano_core.midi.input', level='ERROR'):
            self.adapter.start()
            self.adapter._thread.join(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertIn('invalid configuration', self.adapter.last_error)
        self.assertTrue(self.gate.waits.empty())

    def test_immediate_read_oserror_recovers_too(self):
        original = self.backend.iter_pending
        first_call = True
        def read(handle):
            nonlocal first_call
            if first_call:
                first_call = False
                raise OSError('iterator creation fault')
            return original(handle)
        self.backend.iter_pending = read
        h = self.start()
        self.assertTrue(h.closed.wait(2))
        self.assertEqual(self.states.get(timeout=2), ConnectionState.DISCONNECTED)
        self.gate.waits.get(timeout=2)
        h = self.reconnect()
        h.pending.put(msg('note_on', note=69))
        self.assertEqual(self.received.get(timeout=2).note, 69)

    def test_invalid_intervals_fail_before_start(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MidiInputAdapter(self.backend, reconnect_delay=value)

    def test_stop_during_read_discards_late_event_and_refuses_second_worker(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.iter_pending
        def blocked_read(handle):
            entered.set()
            if not release.wait(3):
                raise AssertionError('read barrier not released')
            yield msg('note_on', note=99)
        self.backend.iter_pending = blocked_read
        self.addCleanup(release.set)
        first = self.start()
        self.assertTrue(entered.wait(2))
        self.adapter.stop(.01)
        with self.assertRaises(RuntimeError):
            self.adapter.start()
        release.set()
        self.adapter.stop(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertTrue(first.closed.is_set())
        self.assertTrue(self.received.empty())
        self.assertEqual(self.states.get(timeout=1),ConnectionState.DISCONNECTED)
        self.backend.iter_pending = original
        second = self.start()
        first.pending.put(OSError('late old connection fault'))
        second.pending.put(msg('note_on',note=65))
        self.assertEqual(self.received.get(timeout=2).note,65)
        self.assertEqual(self.adapter.state,ConnectionState.CONNECTED)
        self.assertFalse(second.closed.is_set())

    def test_second_discovery_call_transport_failure_retries(self):
        original = self.backend.list_input_ports
        count = 0
        def discover():
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('selection discovery fault')
            return original()
        self.backend.list_input_ports = discover
        self.adapter.start()
        self.gate.waits.get(timeout=2)
        self.assertEqual(self.backend.handles,[])
        self.gate.release()
        h=self.backend.opened.get(timeout=2)
        h.pending.put(msg('note_on'))
        self.assertEqual(self.received.get(timeout=2).note,60)

    def test_discovery_programming_bug_is_fatal_not_retry(self):
        self.backend.discovery_errors.put(TypeError('discovery bug'))
        with self.assertLogs('piano_core.midi.input',level='ERROR'):
            self.adapter.start()
            self.adapter._thread.join(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertIn('discovery bug',self.adapter.last_error)
        self.assertTrue(self.gate.waits.empty())
