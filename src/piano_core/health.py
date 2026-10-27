"""In-process health model for Piano Core.

Tracks the application lifecycle state only. There is deliberately no I/O,
no hardware access, and no network exposure here; a future API task may
project this model externally, but the model itself stays in-process
(PIANO-P001).

Legal transitions::

    STARTING -> READY
    STARTING -> DEGRADED  (PIANO-P015-HF2: an ENABLED required subsystem
                           failed to start — e.g. the API bind failed —
                           while the process keeps running)
    STARTING -> STOPPING   (shutdown before becoming ready)
    READY    -> STOPPING
    DEGRADED -> STOPPING
    STOPPING -> STOPPED

``STOPPED`` is terminal. All other transitions raise
:class:`InvalidHealthTransition`.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum


class HealthState(Enum):
    """Lifecycle states of the application."""

    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


_ALLOWED_TRANSITIONS = {
    HealthState.STARTING: frozenset(
        {HealthState.READY, HealthState.DEGRADED, HealthState.STOPPING}
    ),
    HealthState.READY: frozenset({HealthState.STOPPING}),
    HealthState.DEGRADED: frozenset({HealthState.STOPPING}),
    HealthState.STOPPING: frozenset({HealthState.STOPPED}),
    HealthState.STOPPED: frozenset(),
}


class InvalidHealthTransition(RuntimeError):
    """Raised when a health state transition is not allowed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Health:
    """Thread-safe holder for the current lifecycle state.

    The clock is injectable (``now``) so tests can assert deterministically
    without sleeping or mocking global time.
    """

    __slots__ = ("_lock", "_now", "_state", "_updated_at")

    def __init__(
        self,
        state: HealthState = HealthState.STARTING,
        now=_utc_now,
    ) -> None:
        if not isinstance(state, HealthState):
            raise TypeError(
                f"state must be a HealthState, got {type(state).__name__}"
            )
        self._lock = threading.Lock()
        self._now = now
        self._state = state
        self._updated_at = now()

    @property
    def state(self) -> HealthState:
        """Current lifecycle state."""
        with self._lock:
            return self._state

    @property
    def updated_at(self) -> datetime:
        """UTC timestamp of the last state change."""
        with self._lock:
            return self._updated_at

    def transition(self, to_state: HealthState) -> None:
        """Move to ``to_state`` if the transition is legal, else raise."""
        if not isinstance(to_state, HealthState):
            raise TypeError(
                f"to_state must be a HealthState, got {type(to_state).__name__}"
            )
        with self._lock:
            allowed = _ALLOWED_TRANSITIONS[self._state]
            if to_state not in allowed:
                raise InvalidHealthTransition(
                    f"illegal health transition "
                    f"{self._state.value} -> {to_state.value}"
                )
            self._state = to_state
            self._updated_at = self._now()

    def describe(self) -> str:
        """Human-readable one-line description (never contains secrets)."""
        with self._lock:
            state, at = self._state, self._updated_at
        return f"health={state.value} since={at.isoformat()}"
