"""V2 tests: in-process health model (transitions, timestamps, safety)."""

import unittest
from datetime import datetime, timezone

from piano_core.health import (
    Health,
    HealthState,
    InvalidHealthTransition,
)

def _clock_factory(start_minute: int = 0):
    """Fake UTC clock factory: each call advances by one deterministic minute."""

    def _clock() -> datetime:
        nonlocal start_minute
        start_minute += 1
        return datetime(2026, 9, 4, 12, start_minute, tzinfo=timezone.utc)

    return _clock


class TransitionTests(unittest.TestCase):
    def test_initial_state_is_starting(self):
        self.assertIs(Health().state, HealthState.STARTING)

    def test_full_happy_path(self):
        health = Health()
        health.transition(HealthState.READY)
        self.assertIs(health.state, HealthState.READY)
        health.transition(HealthState.STOPPING)
        self.assertIs(health.state, HealthState.STOPPING)
        health.transition(HealthState.STOPPED)
        self.assertIs(health.state, HealthState.STOPPED)

    def test_early_shutdown_before_ready_is_legal(self):
        health = Health()
        health.transition(HealthState.STOPPING)
        health.transition(HealthState.STOPPED)
        self.assertIs(health.state, HealthState.STOPPED)

    def test_starting_to_degraded_is_legal(self):
        # PIANO-P015-HF2: an ENABLED subsystem that failed to start
        # (e.g. the API bind) yields an honestly DEGRADED lifecycle.
        health = Health()
        health.transition(HealthState.DEGRADED)
        self.assertIs(health.state, HealthState.DEGRADED)

    def test_degraded_to_stopping_is_legal(self):
        health = Health()
        health.transition(HealthState.DEGRADED)
        health.transition(HealthState.STOPPING)
        health.transition(HealthState.STOPPED)
        self.assertIs(health.state, HealthState.STOPPED)

    def test_degraded_to_ready_is_illegal(self):
        health = Health()
        health.transition(HealthState.DEGRADED)
        with self.assertRaises(InvalidHealthTransition):
            health.transition(HealthState.READY)

    def test_starting_to_stopped_is_illegal(self):
        health = Health()
        with self.assertRaises(InvalidHealthTransition):
            health.transition(HealthState.STOPPED)

    def test_ready_to_starting_is_illegal(self):
        health = Health()
        health.transition(HealthState.READY)
        with self.assertRaises(InvalidHealthTransition):
            health.transition(HealthState.STARTING)

    def test_stopped_is_terminal(self):
        health = Health(now=_clock_factory())
        health.transition(HealthState.READY)
        health.transition(HealthState.STOPPING)
        health.transition(HealthState.STOPPED)
        for target in HealthState:
            with self.assertRaises(InvalidHealthTransition):
                health.transition(target)

    def test_same_state_transition_is_illegal(self):
        health = Health()
        health.transition(HealthState.READY)
        with self.assertRaises(InvalidHealthTransition):
            health.transition(HealthState.READY)

    def test_non_state_argument_raises_type_error(self):
        health = Health()
        with self.assertRaises(TypeError):
            health.transition("READY")

    def test_non_state_initialization_raises_type_error(self):
        with self.assertRaises(TypeError):
            Health(state="STARTING")


class TimestampTests(unittest.TestCase):
    def test_updated_at_is_timezone_aware_utc(self):
        health = Health()
        self.assertIsNotNone(health.updated_at.tzinfo)
        self.assertEqual(health.updated_at.utcoffset().total_seconds(), 0)

    def test_timestamps_advance_with_injected_clock(self):
        health = Health(now=_clock_factory())
        first = health.updated_at
        health.transition(HealthState.READY)
        second = health.updated_at
        self.assertGreater(second, first)

    def test_describe_contains_state(self):
        health = Health(now=_clock_factory())
        self.assertIn("STARTING", health.describe())
        self.assertIn("2026-09-04", health.describe())


if __name__ == "__main__":
    unittest.main()
