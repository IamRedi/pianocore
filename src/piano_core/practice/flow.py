"""Flow matching strategy; PracticeEngine remains the lifecycle authority.

Canonical target onsets + monotonic input timestamps only. No threads,
hardware, MIDI parser, output or lifecycle state in this module.
"""
from __future__ import annotations

import math
from piano_core.midi.events import MidiEventType

WINDOW = 0.180
ON_TIME = 0.060
INGRESS_GRACE = 0.050
COUNT_IN = 2.0


class FlowEvaluation:
    def __init__(self, plan, now, pressed, *, speed=1.0):
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.25 <= speed <= 1:
            raise ValueError("Flow speed must be finite and in [0.25, 1.0]")
        if any(not math.isfinite(t.onset_seconds) for t in plan.targets) or not math.isfinite(plan.duration_seconds):
            raise ValueError("Flow requires finite canonical timing")
        self.plan = plan
        self.speed = float(speed)
        self.origin = now + COUNT_IN
        self.paused_at = None
        self.position = -COUNT_IN * self.speed
        self.last_clock = now
        self.held = pressed
        self.attempts = {}
        self.results = {}
        self.deltas = []
        self.correct = 0
        self.missed = 0
        self.early = 0
        self.late = 0
        self.absolute_error = 0.0
        self.wrong = 0
        self.latest = "NONE"
        self.last_note = None
        self.target_index = 0
        self.next_expiry = 0
        self.last_input = -math.inf
        self.ignore_before = now

    def clock_position(self, now):
        return ((self.paused_at if self.paused_at is not None else now) - self.origin) * self.speed

    def tick(self, now):
        if not math.isfinite(now) or now < self.last_clock:
            raise ValueError("Flow monotonic clock moved backwards")
        self.last_clock = now
        self.position = self.clock_position(now)
        if self.paused_at is not None:
            return False
        targets = self.plan.targets
        # Expiry advances a monotonic cursor; no full-score scan per frame/input.
        while self.next_expiry < len(targets):
            i = self.next_expiry
            if now <= self.origin + targets[i].onset_seconds / self.speed + WINDOW + INGRESS_GRACE:
                break
            if i not in self.results:
                self.results[i] = "MISSED"
                self.missed += 1
                self.attempts.pop(i, None)
                self.latest = "MISSED"
            self.next_expiry += 1
        while self.target_index < len(targets) and self.target_index in self.results:
            self.target_index += 1
        endpoint = max(self.plan.duration_seconds, targets[-1].onset_seconds)
        return now >= self.origin + endpoint / self.speed + WINDOW + INGRESS_GRACE and len(self.results) == len(targets)

    def observe(self, event, pressed, now):
        timestamp = event.timestamp
        if not math.isfinite(timestamp) or timestamp > now + 0.001 or timestamp < self.last_input:
            raise ValueError("Flow input timestamp is non-finite, future or reordered")
        self.last_input = timestamp
        was_held = event.note in self.held
        self.held = pressed
        if timestamp < self.ignore_before:
            return  # captured before Start/Resume: update occupancy, never grade
        if self.paused_at is not None:
            return
        if event.type is MidiEventType.NOTE_OFF:
            if event.note not in pressed:
                for attempt in self.attempts.values():
                    attempt.pop(event.note, None)
            return
        if event.type is not MidiEventType.NOTE_ON or was_held or event.note not in pressed:
            return
        self.last_note = event.note
        if timestamp < self.origin - WINDOW:
            return  # count-in is preparation, not a scored opportunity
        if now - timestamp > INGRESS_GRACE:
            raise ValueError("Flow input exceeded the 50 ms ingress budget")
        candidates = []
        for i in range(self.next_expiry, len(self.plan.targets)):
            target = self.plan.targets[i]
            delta = timestamp - (self.origin + target.onset_seconds / self.speed)
            if delta < -WINDOW:
                break
            if i not in self.results and event.note in target.notes and abs(delta) <= WINDOW and event.note not in self.attempts.get(i, {}):
                candidates.append((abs(delta), i, delta))
        if not candidates:
            self.wrong += 1
            self.latest = "INCORRECT"
            return
        _, i, delta = min(candidates)
        attempt = self.attempts.setdefault(i, {})
        attempt[event.note] = (timestamp, delta)
        self.latest = "EARLY" if delta < -ON_TIME else "LATE" if delta > ON_TIME else "CORRECT"
        target = self.plan.targets[i]
        if all(n in attempt and n in pressed for n in target.notes):
            times = [attempt[n][0] for n in target.notes]
            if max(times) - min(times) <= WINDOW:
                self.results[i] = "CORRECT"
                self.correct += 1
                for note in target.notes:
                    attack_delta = attempt[note][1]
                    self.deltas.append(attack_delta)
                    self.early += attack_delta < -ON_TIME
                    self.late += attack_delta > ON_TIME
                    self.absolute_error += abs(attack_delta)
                self.attempts.pop(i, None)

    def pause(self, now):
        if self.paused_at is None:
            self.position = self.clock_position(now)
            self.paused_at = now
            self.attempts.clear()

    def resume(self, now, pressed):
        if self.paused_at is not None:
            self.origin += now - self.paused_at
            self.paused_at = None
            self.held = pressed
            self.attempts.clear()
            self.ignore_before = now

    def snapshot(self, now, *, include_evidence=False):
        result = {
            "position_seconds": self.clock_position(now),
            "duration_seconds": self.plan.duration_seconds,
            "sample_monotonic_seconds": now,
            "speed": self.speed,
            "paused": self.paused_at is not None,
            "count_in_seconds": max(0.0, -self.clock_position(now) / self.speed),
            "correct_events": self.correct, "missed_events": self.missed,
            "expected_events": self.plan.total_steps, "wrong_presses": self.wrong,
            "early_attacks": self.early, "late_attacks": self.late,
            "on_time_attacks": len(self.deltas) - self.early - self.late,
            "measured_attacks": len(self.deltas),
            "mean_absolute_error_ms": round(self.absolute_error * 1000 / len(self.deltas), 1) if self.deltas else None,
            "event_accuracy": round(self.correct / self.plan.total_steps, 4),
            "window_ms": 180, "on_time_ms": 60, "ingress_budget_ms": 50,
            "last_note": self.last_note,
        }
        if include_evidence:
            result["signed_attack_errors_ms"] = [round(delta * 1000, 3) for delta in self.deltas]
            result["event_results"] = [self.results.get(i, "UNRESOLVED") for i in range(self.plan.total_steps)]
        return result
