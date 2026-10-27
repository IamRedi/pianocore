"""Objective practice sessions and progress (M5).

Scoring is TRANSPARENT: expected, correct, missed, extra, streak,
completion — every number is a count, every percentage is an explicit
division of two counts. There is deliberately NO opaque "AI
performance score", no learning-science overclaims, and no automatic
adaptation from session data.

Wait-mode mapping (documented, one source of truth):

- expected        = plan steps in scope (total_steps);
- correct         = completed_steps;
- missed          = expected - correct (only when the session ended
                    early; a naturally COMPLETED session has none);
- extra           = incorrect_attempts (wrong/extra presses, evidence);
- completion      = final engine state is COMPLETED.

Listen/Follow guidance runs record expected = note groups in scope and
completion only; they are passive (no user grading happens without
input — honesty over inflated numbers).
"""

from __future__ import annotations

from dataclasses import dataclass

from piano_core.packages.store import PackageStore, utc_now

__all__ = ["SessionResult", "record_wait_session", "record_guidance_session"]


@dataclass(frozen=True, slots=True)
class SessionResult:
    session_id: str
    package_id: str
    mode: str
    hand_selection: str | None
    section_id: str | None
    tempo_scale: float
    started_at: str
    ended_at: str | None
    expected_count: int
    correct_count: int
    missed_count: int
    extra_count: int
    chord_completions: int
    best_streak: int
    elapsed_seconds: float
    completed: bool
    device: str | None = None

    @property
    def accuracy(self) -> float | None:
        """Transparent correct/expected ratio (None without expectations)."""
        if not self.expected_count:
            return None
        return round(self.correct_count / self.expected_count, 4)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "package_id": self.package_id,
            "mode": self.mode,
            "hand_selection": self.hand_selection,
            "section_id": self.section_id,
            "tempo_scale": self.tempo_scale,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "expected_count": self.expected_count,
            "correct_count": self.correct_count,
            "missed_count": self.missed_count,
            "extra_count": self.extra_count,
            "chord_completions": self.chord_completions,
            "best_streak": self.best_streak,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "completed": self.completed,
            "device": self.device,
            "accuracy": self.accuracy,
        }


def record_wait_session(
    store: PackageStore,
    *,
    session_id: str,
    package_id: str,
    mode: str,
    hand_selection: str | None,
    section_id: str | None,
    tempo_scale: float,
    started_at: str,
    ended_at: str | None,
    total_steps: int,
    completed_steps: int,
    incorrect_attempts: int,
    completed: bool,
    best_streak: int = 0,
    elapsed_seconds: float = 0.0,
    device: str | None = None,
) -> SessionResult:
    """Persist one Wait-mode (learning engine) session objectively."""
    expected = max(0, int(total_steps))
    correct = min(max(0, int(completed_steps)), expected)
    missed = max(0, expected - correct) if not completed else 0
    result = SessionResult(
        session_id=session_id,
        package_id=package_id,
        mode=mode,
        hand_selection=hand_selection,
        section_id=section_id,
        tempo_scale=float(tempo_scale),
        started_at=started_at,
        ended_at=ended_at or utc_now(),
        expected_count=expected,
        correct_count=correct,
        missed_count=missed,
        extra_count=max(0, int(incorrect_attempts)),
        chord_completions=0,
        best_streak=max(0, int(best_streak)),
        elapsed_seconds=max(0.0, float(elapsed_seconds)),
        completed=bool(completed),
        device=device,
    )
    store.record_session(
        session_id=result.session_id,
        package_id=result.package_id,
        mode=result.mode,
        hand_selection=result.hand_selection,
        section_id=result.section_id,
        tempo_scale=result.tempo_scale,
        started_at=result.started_at,
        ended_at=result.ended_at,
        expected_count=result.expected_count,
        correct_count=result.correct_count,
        missed_count=result.missed_count,
        extra_count=result.extra_count,
        chord_completions=result.chord_completions,
        best_streak=result.best_streak,
        elapsed_seconds=result.elapsed_seconds,
        completed=result.completed,
        device=result.device,
        detail={"accuracy": result.accuracy},
    )
    return result


def record_guidance_session(
    store: PackageStore,
    *,
    session_id: str,
    package_id: str,
    mode: str,
    section_id: str | None,
    tempo_scale: float,
    started_at: str,
    note_groups: int,
    completed: bool,
    elapsed_seconds: float,
    loop_count: int = 0,
    hand_selection: str | None = None,
) -> SessionResult:
    """Persist one passive Listen/Follow run (no grading is invented)."""
    result = SessionResult(
        session_id=session_id,
        package_id=package_id,
        mode=mode,
        hand_selection=hand_selection,
        section_id=section_id,
        tempo_scale=float(tempo_scale),
        started_at=started_at,
        ended_at=utc_now(),
        expected_count=max(0, int(note_groups)),
        correct_count=0,
        missed_count=0,
        extra_count=0,
        chord_completions=0,
        best_streak=0,
        elapsed_seconds=max(0.0, float(elapsed_seconds)),
        completed=bool(completed),
    )
    store.record_session(
        session_id=result.session_id,
        package_id=result.package_id,
        mode=result.mode,
        hand_selection=result.hand_selection,
        section_id=result.section_id,
        tempo_scale=result.tempo_scale,
        started_at=result.started_at,
        ended_at=result.ended_at,
        expected_count=result.expected_count,
        correct_count=result.correct_count,
        missed_count=result.missed_count,
        extra_count=result.extra_count,
        chord_completions=result.chord_completions,
        best_streak=result.best_streak,
        elapsed_seconds=result.elapsed_seconds,
        completed=result.completed,
        device=None,
        detail={"loop_count": int(loop_count), "passive": True},
    )
    return result


def record_engine_session(store, session):
    """Persist a Core-owned terminal/stop snapshot, outside input callbacks."""
    if not session.song_id or not session.song_id.startswith("package:"):
        return
    flow = session.flow
    completed = session.state.value == "COMPLETED"
    detail = {"full_lesson": bool(session.full_lesson), "failed": session.state.value == "FAILED"}
    if flow is not None:
        detail.update(flow)
    expected = flow["expected_events"] if flow else (session.total_steps or 0)
    correct = flow["correct_events"] if flow else session.completed_steps
    store.record_session(
        session_id=session.session_id, package_id=session.song_id[8:],
        mode=session.mode.value, hand_selection=session.hand_selection,
        section_id=session.scope_section_id,
        tempo_scale=flow["speed"] if flow else 1.0,
        started_at=session.started_at, ended_at=session.ended_at or utc_now(),
        expected_count=expected, correct_count=correct,
        missed_count=flow["missed_events"] if flow else max(0, expected - correct),
        extra_count=flow["wrong_presses"] if flow else session.incorrect_attempts,
        chord_completions=0, best_streak=0,
        elapsed_seconds=session.elapsed_seconds, completed=completed, detail=detail,
    )
