"""Actual process FD observability without changing the health JSON contract."""
import errno
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from piano_core.api.server import ApiServer, _process_fd_headers
from piano_core.health import HealthState
from tests.test_api_http import FakeContext


class ResourceHeaderTests(unittest.TestCase):
    def setUp(self):
        self.context = FakeContext()
        self.context.health.transition(HealthState.READY)
        self.server = ApiServer(self.context, '127.0.0.1', 0)
        self.server.start()
        self.addCleanup(self.server.shutdown)
        host, port = self.server.bound_address
        self.base = f'http://{host}:{port}'

    def get(self, path='/api/v1/health'):
        try: response = urllib.request.urlopen(self.base + path, timeout=10)
        except urllib.error.HTTPError as exc: response = exc
        with response:
            return response.status, response.headers, json.load(response)

    def test_real_http_count_tracks_retained_open_descriptors_and_close(self):
        status, before, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(before['X-Piano-Process-PID'], str(os.getpid()))
        self.assertEqual(before['X-Piano-FD-Sample'], 'sampled')
        held = [tempfile.TemporaryFile() for _ in range(12)]
        try:
            _, during, same_body = self.get()
            self.assertEqual(body, same_body)
            self.assertEqual(int(during['X-Piano-Process-FDs']), int(before['X-Piano-Process-FDs']) + 12)
        finally:
            for file in held: file.close()
        _, after, same_body = self.get()
        self.assertEqual(body, same_body)
        self.assertEqual(after['X-Piano-Process-FDs'], before['X-Piano-Process-FDs'])

    def test_missing_sample_does_not_change_health_json_or_invent_zero(self):
        status, _, expected = self.get()
        for fault in (PermissionError('denied'), OSError(errno.EMFILE, 'descriptor fault')):
            with self.subTest(fault=type(fault).__name__):
                with patch('piano_core.api.server.os.listdir', side_effect=fault):
                    actual_status, headers, body = self.get()
                self.assertEqual((actual_status, body), (status, expected))
                self.assertEqual(headers['X-Piano-FD-Sample'], 'unavailable')
                self.assertNotIn('X-Piano-Process-FDs', headers)
                self.assertNotIn('X-Piano-ALSA-FDs', headers)

    def test_readlink_permission_error_invalidates_whole_sample(self):
        with patch('piano_core.api.server.os.readlink', side_effect=PermissionError('denied')):
            _, headers, _ = self.get()
        self.assertEqual(headers['X-Piano-FD-Sample'], 'unavailable')
        self.assertNotIn('X-Piano-Process-FDs', headers)

    def test_alsa_count_and_disappearing_descriptor_are_sampled_without_paths(self):
        targets = {'0':'/dev/null', '1':'/dev/snd/seq', '2':'/dev/snd/seq',
                   '3':'private/database.db', '4':None}
        def readlink(path):
            target=targets[path.rsplit('/',1)[-1]]
            if target is None: raise FileNotFoundError(path)
            return target
        with patch('piano_core.api.server.os.listdir', return_value=[*targets, 'non-numeric']), \
             patch('piano_core.api.server.os.readlink', side_effect=readlink):
            headers=dict(_process_fd_headers())
        self.assertEqual(headers['X-Piano-Process-FDs'], '4')
        self.assertEqual(headers['X-Piano-ALSA-FDs'], '2')
        self.assertNotIn('database', str(headers))
        self.assertNotIn('/dev/', str(headers))

    def test_other_routes_and_errors_do_not_sample(self):
        with patch('piano_core.api.server._process_fd_headers') as observer:
            for path in ('/api/v1/state', '/api/v1/unknown', '/api/v1/health?unknown=value'):
                _, headers, _ = self.get(path)
                self.assertNotIn('X-Piano-Process-FDs', headers)
            observer.assert_not_called()


if __name__ == '__main__': unittest.main()
