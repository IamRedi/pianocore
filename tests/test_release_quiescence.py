"""Deterministic resource-observation controls, no real appliance access."""
import unittest
from tests.release_harness.quiescence import observe_quiescence


def sample(t, fd=24, **kw):
    return dict(monotonic=t, request_started_monotonic=t, fds=fd, pid=7, alsa_fds=2, midi='CONNECTED',
                health='ok', application='READY', **kw)


class Feed:
    def __init__(self, rows, batches):
        self.rows, self.batches, self.now = rows, iter(batches), 0
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def wait(self, timeout):
        self.now += 1
        self.rows.extend(next(self.batches, []))


class QuiescenceTests(unittest.TestCase):
    def observe(self, rows, batches, **kw):
        feed = Feed(rows, batches)
        return observe_quiescence(rows, feed, after=10, limit=32, pid=7, alsa=2,
                                  timeout=4, clock=lambda: feed.now, **kw)

    def test_active_old_samples_cannot_qualify(self):
        with self.assertRaisesRegex(AssertionError, 'deadline'):
            self.observe([sample(t) for t in [1, 2, 3, 10]], [])

    def test_delayed_owned_drain_then_three_fresh_samples(self):
        rows = [sample(9, 33)]
        result = self.observe(rows, [[sample(11, 33)], [sample(12), sample(13)], [sample(14)]])
        self.assertEqual([s['monotonic'] for s in result['qualifying_samples']], [12, 13, 14])
        self.assertEqual([s['fds'] for s in rows], [33, 33, 24, 24, 24])

    def test_genuine_above_threshold_plateau_fails(self):
        with self.assertRaisesRegex(AssertionError, 'deadline'):
            self.observe([], [[sample(11, 33)], [sample(12, 33)], [sample(13, 33)]])

    def test_one_transient_low_sample_cannot_hide_leak(self):
        with self.assertRaisesRegex(AssertionError, 'deadline'):
            self.observe([], [[sample(11)], [sample(12, 33)], [sample(13)]])

    def test_original_limit_exactly_32_is_retained(self):
        self.assertEqual(self.observe([], [[sample(t, 32) for t in [11, 12, 13]]])['limit'], 32)

    def test_pid_alsa_midi_health_drift_fail_closed(self):
        for key, value in [('pid', 8), ('alsa_fds', 1), ('midi', 'DISCONNECTED'),
                           ('health', 'error'), ('application', 'STOPPING')]:
            with self.subTest(key=key), self.assertRaises(AssertionError):
                row = sample(11); row[key] = value
                self.observe([], [[row]])

    def test_abort_threshold_not_weakened(self):
        with self.assertRaisesRegex(AssertionError, 'headroom'):
            self.observe([], [[sample(11, 768)]])

    def test_delayed_response_started_before_cleanup_is_not_fresh(self):
        rows = [sample(t) for t in [11, 12, 13]]
        for row in rows: row['request_started_monotonic'] = 9
        with self.assertRaisesRegex(AssertionError, 'deadline'):
            self.observe(rows, [])

    def test_qualifying_batch_after_deadline_cannot_pass(self):
        with self.assertRaisesRegex(AssertionError, 'deadline'):
            self.observe([], [[], [], [], [sample(t) for t in [11, 12, 13]]])
