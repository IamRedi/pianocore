"""R4 deterministic SSE shutdown races; real loopback sockets, no hardware."""
import http.client
import io
import json
import logging
import os
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from piano_core.api.server import ApiServer
from piano_core.live_notes import LiveNoteState


class SSEShutdownTests(unittest.TestCase):
    def setUp(self):
        self.channel = LiveNoteState(heartbeat_seconds=60)
        self.server = ApiServer(SimpleNamespace(live_notes=self.channel), '127.0.0.1', 0)
        self.server.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.channel.close)
        self.logs = io.StringIO()
        handler = logging.StreamHandler(self.logs)
        logger = logging.getLogger('piano_core.api')
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

    def connection(self):
        c = http.client.HTTPConnection(*self.server.bound_address, timeout=3)
        self.addCleanup(c.close)
        return c

    def test_subscribe_close_race_returns_stable_unavailable(self):
        entered, release = threading.Event(), threading.Event()
        original = self.channel.subscribe
        def paused():
            entered.set()
            if not release.wait(2):
                raise AssertionError('subscribe barrier not released')
            return original()
        self.channel.subscribe = paused
        result, errors = [], []
        def client():
            try:
                c = self.connection()
                c.request('GET', '/api/v1/live-notes/stream')
                response = c.getresponse()
                result.append((response.status, json.loads(response.read())))
            except BaseException as exc:
                errors.append(exc)
        t = threading.Thread(target=client)
        t.start()
        try:
            self.assertTrue(entered.wait(2))
            self.channel.close()
        finally:
            release.set()
            t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result[0][0], 503, self.logs.getvalue())
        self.assertEqual(result[0][1]['error']['code'], 'live_notes_unavailable')
        self.assertNotIn('unhandled', self.logs.getvalue())

    def stream(self, channel=None):
        c = self.connection()
        c.request('GET', '/api/v1/live-notes/stream')
        response = c.getresponse()
        self.addCleanup(response.close)
        self.assertEqual(response.status, 200)
        frame = b''
        while not frame.endswith(b'\n\n'):
            frame += response.readline()
        self.assertIn(b'event: snapshot', frame)
        return c, response

    def assert_drained(self):
        self.assertEqual(self.server._httpd._requests, {})
        self.assertEqual(self.channel._subscribers, 0)
        self.assertNotIn('unhandled', self.logs.getvalue())

    def test_multiple_active_streams_end_cleanly_on_server_shutdown(self):
        streams = [self.stream() for _ in range(4)]
        self.server.shutdown(timeout=1)
        for c, response in streams:
            self.assertEqual(response.read(), b'')
            response.close()
            c.close()
        self.assert_drained()

    def test_publication_close_ends_established_stream_without_second_status(self):
        c, response = self.stream()
        self.channel.close()
        self.assertEqual(response.read(), b'')
        self.server.shutdown(timeout=1)
        self.assert_drained()

    def test_individual_subscription_cancel_wakes_blocked_iterator(self):
        subscription = self.channel.subscribe()
        stream = iter(subscription)
        next(stream)
        entered, ended = threading.Event(), threading.Event()
        original_wait = self.channel._condition.wait_for
        def waiting(*args, **kwargs):
            entered.set()
            return original_wait(*args, **kwargs)
        self.channel._condition.wait_for = waiting
        def reader():
            self.assertEqual(list(stream), [])
            ended.set()
        thread = threading.Thread(target=reader)
        thread.start()
        self.assertTrue(entered.wait(1))
        subscription.close()
        self.assertTrue(ended.wait(1))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.channel._subscribers, 0)

    def test_concurrent_duplicate_close_does_not_release_another_reservation(self):
        from piano_core.live_notes import LiveNoteSubscriberLimitError
        channel = LiveNoteState(max_subscribers=2)
        first, second = channel.subscribe(), channel.subscribe()
        gate = threading.Barrier(9)
        def close():
            gate.wait()
            first.close()
        threads = [threading.Thread(target=close) for _ in range(8)]
        for t in threads: t.start()
        gate.wait()
        for t in threads: t.join(1)
        third = channel.subscribe()
        with self.assertRaises(LiveNoteSubscriberLimitError): channel.subscribe()
        second.close(); third.close(); channel.close()
        self.assertEqual(channel._subscribers, 0)

    def test_subscription_and_close_are_linearized_at_publication_lock(self):
        from piano_core.live_notes import LiveNotePublicationClosedError
        arrived, finished = threading.Event(), threading.Event()
        result = []
        def subscribe():
            arrived.set()
            try: self.channel.subscribe()
            except LiveNotePublicationClosedError: result.append('closed')
            finally: finished.set()
        with self.channel._condition:
            t = threading.Thread(target=subscribe)
            t.start()
            self.assertTrue(arrived.wait(1))
            self.channel.close()
        self.assertTrue(finished.wait(1)); t.join(1)
        self.assertEqual(result, ['closed'])
        self.assertEqual(self.channel._subscribers, 0)

    def test_stop_with_accepted_inflight_subscribe_drains_typed_rejection(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        original, original_close = self.channel.subscribe, self.channel.close
        def paused():
            entered.set()
            if not release.wait(2): raise AssertionError('release missing')
            return original()
        def close():
            original_close(); closed.set()
        self.channel.subscribe, self.channel.close = paused, close
        result, errors = [], []
        def client():
            try:
                c = self.connection(); c.request('GET', '/api/v1/live-notes/stream')
                response = c.getresponse(); result.append((response.status, json.loads(response.read())))
                response.close(); c.close()
            except BaseException as exc: errors.append(exc)
        def stop():
            try: self.server.shutdown(timeout=1)
            except BaseException as exc: errors.append(exc)
        t = threading.Thread(target=client); t.start()
        self.assertTrue(entered.wait(1))
        stopper = threading.Thread(target=stop); stopper.start()
        try: self.assertTrue(closed.wait(1))
        finally: release.set(); t.join(2); stopper.join(2)
        self.assertFalse(t.is_alive()); self.assertFalse(stopper.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result[0][0], 503)
        self.assertEqual(result[0][1]['error']['code'], 'live_notes_unavailable')
        self.assert_drained()

    def test_draining_post_rejects_without_reading_or_reusing_body(self):
        self.server._httpd.begin_shutdown()
        c = self.connection()
        c.request('POST', '/api/v1/playback/play', body=b'x' * 32)
        response = c.getresponse()
        self.assertEqual(response.status, 503)
        self.assertEqual(response.getheader('Connection'), 'close')
        self.assertEqual(json.loads(response.read())['error']['code'], 'api_stopping')
        # Socket reaches EOF; the unread body cannot become another request.
        response.close(); c.close(); self.server.shutdown(timeout=1)
        self.assert_drained()

    def test_partial_request_and_idle_keepalive_sockets_are_bounded(self):
        accepted, lock = threading.Event(), threading.Lock()
        original = self.server._httpd.process_request_thread
        count = 0
        def tracked(*args):
            nonlocal count
            with lock:
                count += 1
                if count == 2: accepted.set()
            return original(*args)
        self.server._httpd.process_request_thread = tracked
        first = socket.create_connection(self.server.bound_address, timeout=2)
        second = socket.create_connection(self.server.bound_address, timeout=2)
        self.addCleanup(first.close); self.addCleanup(second.close)
        first.sendall(b'GET /api/v1/live-notes/stream HTTP/1.1\r\nHost: local\r\n')
        self.assertTrue(accepted.wait(1))
        start = time.monotonic(); self.server.shutdown(timeout=1)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(second.recv(1), b'')
        self.assert_drained()

    def test_client_reset_releases_subscription_without_shutdown(self):
        import struct
        released = threading.Event()
        original = self.channel.subscribe
        def observed():
            sub = original(); close = sub.close
            def release(): close(); released.set()
            sub.close = release
            return sub
        self.channel.subscribe = observed
        c, response = self.stream()
        peer = response.fp.raw._sock
        peer.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        response.close(); c.close()
        self.channel.publish_playback(available=True, owned_notes=((1, 60),))
        self.assertTrue(released.wait(2))
        self.assertEqual(self.channel._subscribers, 0)
        self.assertNotIn('unhandled', self.logs.getvalue())

    def test_unexpected_subscribe_runtime_error_is_still_logged_and_500(self):
        with patch.object(self.channel, 'subscribe', side_effect=RuntimeError('live-note publication is closed')):
            c = self.connection(); c.request('GET', '/api/v1/live-notes/stream')
            response = c.getresponse(); self.assertEqual(response.status, 500)
            self.assertEqual(json.loads(response.read())['error']['code'], 'internal_error')
            response.close()
        self.assertEqual(self.logs.getvalue().count('unhandled API failure'), 1)

    def test_unexpected_iterator_errors_log_without_second_http_response(self):
        from piano_core.api.handlers import Route
        from piano_core.api.streaming import ApiStream
        for error in [RuntimeError('iterator fault'), OSError('iterator IO fault'), ConnectionError('iterator connection fault')]:
            entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()
            def frames():
                yield b'data: first\n\n'
                entered.set()
                if not release.wait(2): raise AssertionError('iterator release missing')
                raise error
            self.server._httpd.routes = (Route('GET', '/fault', lambda *_: (200, ApiStream(frames(), 'text/event-stream', on_close=cleaned.set))),)
            c = self.connection(); c.request('GET', '/fault'); response = c.getresponse()
            self.assertTrue(entered.wait(1)); release.set()
            body = response.read(); self.assertEqual(response.status, 200)
            self.assertEqual(body, b'data: first\n\n'); self.assertTrue(cleaned.wait(1))
            response.close(); c.close()
        self.assertEqual(self.logs.getvalue().count('unhandled API stream failure'), 3)

    def test_cleanup_fault_still_releases_capacity_and_is_logged(self):
        from piano_core.api.handlers import Route
        from piano_core.api.streaming import ApiStream
        cleaned = threading.Event()
        class FaultyClose:
            def __iter__(self): return iter([b'data: first\n\n'])
            def close(self): raise RuntimeError('close fault')
        self.server._httpd.routes = (Route('GET', '/fault', lambda *_: (200, ApiStream(FaultyClose(), 'text/event-stream', on_close=cleaned.set))),)
        c = self.connection(); c.request('GET', '/fault'); response = c.getresponse()
        self.assertEqual(response.status, 200); self.assertEqual(response.read(), b'data: first\n\n')
        self.assertTrue(cleaned.wait(1)); self.assertEqual(self.logs.getvalue().count('API stream cleanup failed'), 1)
        response.close()

    def test_concurrent_repeated_shutdown_calls_join_same_lifecycle(self):
        c, response = self.stream()
        gate, errors = threading.Barrier(5), []
        def stop():
            gate.wait()
            try: self.server.shutdown(timeout=1)
            except BaseException as exc: errors.append(exc)
        threads = [threading.Thread(target=stop) for _ in range(4)]
        for t in threads: t.start()
        gate.wait()
        for t in threads: t.join(2); self.assertFalse(t.is_alive())
        self.assertEqual(errors, []); self.assertEqual(response.read(), b'')
        self.assert_drained()

    def test_thread_start_failure_releases_registered_socket(self):
        a, b = socket.socketpair(); self.addCleanup(b.close)
        with patch('piano_core.api.server.threading.Thread.start', side_effect=RuntimeError('start fault')):
            with self.assertRaisesRegex(RuntimeError, 'start fault'):
                self.server._httpd.process_request(a, ('127.0.0.1', 0))
        self.assertEqual(a.fileno(), -1); self.assertEqual(self.server._httpd._requests, {})

    def test_unstarted_shutdown_closes_bound_socket_and_is_idempotent(self):
        channel = LiveNoteState()
        server = ApiServer(SimpleNamespace(live_notes=channel), '127.0.0.1', 0)
        server.shutdown(timeout=1); server.shutdown(timeout=1)
        self.assertEqual(server._httpd.fileno(), -1)
        with self.assertRaises(RuntimeError): server.start()

    def test_accept_loop_shutdown_has_bounded_failure_not_false_stopped(self):
        entered, release = threading.Event(), threading.Event()
        original = self.server._httpd.shutdown
        def stalled():
            entered.set()
            if not release.wait(2): raise AssertionError('stop release missing')
            original()
        self.server._httpd.shutdown = stalled
        try:
            start = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, 'accept loop'):
                self.server.shutdown(timeout=0.15)
            self.assertLess(time.monotonic() - start, .5)
            self.assertTrue(entered.is_set())
            self.assertNotIn('api server stopped', self.logs.getvalue())
        finally:
            release.set()
            self.server.shutdown(timeout=1)

    def test_fresh_runtime_subscribes_after_prior_server_stop(self):
        c, response = self.stream(); old_epoch = self.channel.runtime_epoch
        self.server.shutdown(timeout=1); self.assertEqual(response.read(), b'')
        channel = LiveNoteState(); self.assertNotEqual(channel.runtime_epoch, old_epoch)
        server = ApiServer(SimpleNamespace(live_notes=channel), '127.0.0.1', 0); server.start()
        try:
            conn = http.client.HTTPConnection(*server.bound_address, timeout=2)
            conn.request('GET', '/api/v1/live-notes/stream'); fresh = conn.getresponse()
            self.assertEqual(fresh.status, 200); self.assertTrue(fresh.readline().startswith(b'id:'))
            server.shutdown(timeout=1); fresh.close(); conn.close()
            self.assertEqual(channel._subscribers, 0)
        finally: server.shutdown()

    @unittest.skipUnless(os.path.isdir('/proc/self/fd'), 'Linux descriptor measurement')
    def test_repeated_real_stream_lifecycles_do_not_leak_fds_or_threads(self):
        self.server.shutdown()
        before_fd = len(os.listdir('/proc/self/fd'))
        before_threads = {t.ident for t in threading.enumerate()}
        for _ in range(12):
            channel = LiveNoteState(heartbeat_seconds=60)
            server = ApiServer(SimpleNamespace(live_notes=channel), '127.0.0.1', 0); server.start()
            conns = []
            try:
                for _ in range(3):
                    conn = http.client.HTTPConnection(*server.bound_address, timeout=2)
                    conn.request('GET', '/api/v1/live-notes/stream'); response = conn.getresponse()
                    self.assertEqual(response.status, 200); response.readline()
                    conns.append((conn, response))
                server.shutdown(timeout=1)
            finally:
                channel.close(); server.shutdown()
                for conn, response in conns: response.close(); conn.close()
            self.assertEqual(server._httpd._requests, {}); self.assertEqual(channel._subscribers, 0)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before_fd)
        self.assertEqual({t.ident for t in threading.enumerate()}, before_threads)

    def test_inflight_handler_is_joined_before_shutdown_returns(self):
        from piano_core.api.handlers import Route
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        original_close = self.channel.close
        def close(): original_close(); closed.set()
        self.channel.close = close
        def handler(*args):
            entered.set()
            if not release.wait(2): raise AssertionError('handler release missing')
            return 200, {'finished': True}
        self.server._httpd.routes = (Route('GET', '/inflight', handler),)
        errors, results = [], []
        def client():
            try:
                c = self.connection(); c.request('GET', '/inflight'); response = c.getresponse()
                results.append((response.status, json.loads(response.read()))); response.close(); c.close()
            except BaseException as exc: errors.append(exc)
        def stop():
            try: self.server.shutdown(timeout=1)
            except BaseException as exc: errors.append(exc)
        t = threading.Thread(target=client); t.start(); self.assertTrue(entered.wait(1))
        stopper = threading.Thread(target=stop); stopper.start()
        try: self.assertTrue(closed.wait(1))
        finally: release.set(); t.join(2); stopper.join(2)
        self.assertEqual(errors, []); self.assertEqual(results, [(200, {'finished': True})])
        self.assertFalse(t.is_alive()); self.assertFalse(stopper.is_alive()); self.assert_drained()

    def test_stalled_handler_times_out_once_and_can_be_joined_after_release(self):
        from piano_core.api.handlers import Route
        entered, release = threading.Event(), threading.Event()
        def handler(*args):
            entered.set()
            if not release.wait(2): raise AssertionError('handler release missing')
            return 200, {'finished': True}
        self.server._httpd.routes = (Route('GET', '/stalled', handler),)
        conn = socket.create_connection(self.server.bound_address, timeout=2); self.addCleanup(conn.close)
        conn.sendall(b'GET /stalled HTTP/1.1\r\nHost: local\r\n\r\n')
        self.assertTrue(entered.wait(1))
        try:
            start = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, 'request handlers'):
                self.server.shutdown(timeout=.15)
            self.assertLess(time.monotonic() - start, .5)
            self.assertNotIn('api server stopped', self.logs.getvalue())
            self.assertEqual(conn.recv(1), b'')
        finally: release.set(); self.server.shutdown(timeout=1)
        self.assert_drained()

    def test_application_stop_request_closes_publication_immediately(self):
        from piano_core.app import Application
        from piano_core.live_notes import LiveNotePublicationClosedError
        app = Application.from_env()
        app.request_stop('R4 test')
        with self.assertRaises(LiveNotePublicationClosedError): app.live_notes.subscribe()
        app.request_stop('R4 repeated'); app.shutdown()

    def test_serve_thread_start_failure_closes_listener(self):
        server = ApiServer(SimpleNamespace(live_notes=LiveNoteState()), '127.0.0.1', 0)
        with patch('piano_core.api.server.threading.Thread.start', side_effect=RuntimeError('serve start fault')):
            with self.assertRaisesRegex(RuntimeError, 'serve start fault'): server.start()
        self.assertEqual(server._httpd.fileno(), -1)
        server.shutdown()


if __name__ == '__main__':
    unittest.main()
