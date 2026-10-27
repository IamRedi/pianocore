"""Practice package store: additive SQLite persistence (M5).

A dedicated database for imported packages, practice sessions and
progress summaries. It is deliberately SEPARATE from the canonical
library index (which stays a disposable projection of the read-only
source tree): imported packages are self-contained activated copies,
and sessions/progress are appliance state.

Schema is versioned in-place (``meta.schema_version``); migrations are
additive only and never reset existing data.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("piano_core.packages.store")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packages (
    package_id        TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    source_version_id INTEGER NOT NULL,
    midi_sha256       TEXT NOT NULL,
    package_digest    TEXT NOT NULL,
    package_schema_version    INTEGER NOT NULL,
    practice_contract_version INTEGER NOT NULL,
    led_semantic_version      INTEGER NOT NULL,
    difficulty        TEXT,
    hands_availability TEXT NOT NULL,
    rh_count          INTEGER NOT NULL,
    lh_count          INTEGER NOT NULL,
    duration_seconds  REAL NOT NULL,
    section_count     INTEGER NOT NULL,
    event_count       INTEGER NOT NULL,
    source_type       TEXT NOT NULL,
    transformation_type TEXT,
    required_capabilities TEXT NOT NULL,
    provenance_json   TEXT,
    package_path      TEXT NOT NULL,
    activated_at      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    package_id        TEXT NOT NULL REFERENCES packages(package_id),
    mode              TEXT NOT NULL,
    hand_selection    TEXT,
    section_id        TEXT,
    tempo_scale       REAL NOT NULL DEFAULT 1.0,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    expected_count    INTEGER NOT NULL DEFAULT 0,
    correct_count     INTEGER NOT NULL DEFAULT 0,
    missed_count      INTEGER NOT NULL DEFAULT 0,
    extra_count       INTEGER NOT NULL DEFAULT 0,
    chord_completions INTEGER NOT NULL DEFAULT 0,
    best_streak       INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds   REAL NOT NULL DEFAULT 0,
    completed         INTEGER NOT NULL DEFAULT 0,
    device            TEXT,
    detail_json       TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_package ON sessions(package_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
"""


@dataclass(frozen=True, slots=True)
class PackageRow:
    """Library-facing summary of one imported package."""

    package_id: str
    title: str
    source_version_id: int
    midi_sha256: str
    package_digest: str
    difficulty: str | None
    hands_availability: str
    rh_count: int
    lh_count: int
    duration_seconds: float
    section_count: int
    event_count: int
    source_type: str
    transformation_type: str | None
    provenance: dict
    package_path: str
    activated_at: str
    status: str

    def to_dict(self) -> dict:
        return {
            "package_id": self.package_id,
            "title": self.title,
            "source_version_id": self.source_version_id,
            "midi_sha256": self.midi_sha256,
            "package_digest": self.package_digest,
            "difficulty": self.difficulty,
            "hands_availability": self.hands_availability,
            "rh_count": self.rh_count,
            "lh_count": self.lh_count,
            "duration_seconds": self.duration_seconds,
            "section_count": self.section_count,
            "event_count": self.event_count,
            "source_type": self.source_type,
            "transformation_type": self.transformation_type,
            "provenance_summary": self.provenance,
            "activated_at": self.activated_at,
            "status": self.status,
        }


class PackageStore:
    """SQLite-backed practice package library (fail-closed, additive)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._path), timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # sqlite3's context manager commits/rolls back but does not close.
            # Own the handle through configuration, transaction and failure.
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row["value"]) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"practice store schema {row['value']} is newer than "
                    f"supported {SCHEMA_VERSION}"
                )

    # ----------------------------------------------------------------- packages

    def get_package(self, package_id: str) -> PackageRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM packages WHERE package_id=?", (package_id,)
            ).fetchone()
        return self._row_to_summary(row) if row else None

    def find_by_midi_sha(self, midi_sha256: str) -> PackageRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM packages WHERE midi_sha256=? AND status='active' "
                "ORDER BY activated_at DESC LIMIT 1",
                (midi_sha256,),
            ).fetchone()
        return self._row_to_summary(row) if row else None

    def list_packages(self, limit: int = 200) -> list[PackageRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM packages WHERE status='active' "
                "ORDER BY activated_at DESC, package_id LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def insert_package(self, summary: PackageRow, provenance: dict) -> None:
        """Insert a new package row (caller guarantees uniqueness)."""
        self._insert(summary, provenance, replace=False)

    def replace_package(self, summary: PackageRow, provenance: dict) -> None:
        """Insert or replace (crash-repair path for the same identity)."""
        self._insert(summary, provenance, replace=True)

    def _insert(self, summary: PackageRow, provenance: dict, *, replace: bool) -> None:
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        with self._connect() as conn:
            conn.execute(
                f"""{verb} INTO packages (
                       package_id, title, source_version_id, midi_sha256,
                       package_digest, package_schema_version,
                       practice_contract_version, led_semantic_version,
                       difficulty, hands_availability, rh_count, lh_count,
                       duration_seconds, section_count, event_count,
                       source_type, transformation_type,
                       required_capabilities, provenance_json, package_path,
                       activated_at, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')""",
                (
                    summary.package_id, summary.title,
                    summary.source_version_id, summary.midi_sha256,
                    summary.package_digest, 1, 1, 1,
                    summary.difficulty, summary.hands_availability,
                    summary.rh_count, summary.lh_count,
                    summary.duration_seconds, summary.section_count,
                    summary.event_count, summary.source_type,
                    summary.transformation_type,
                    "[]", json.dumps(provenance, sort_keys=True),
                    summary.package_path, summary.activated_at,
                ),
            )

    def package_path(self, package_id: str) -> Path | None:
        row = self.get_package(package_id)
        return Path(row.package_path) if row else None

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> PackageRow:
        try:
            provenance = json.loads(row["provenance_json"] or "{}")
        except json.JSONDecodeError:
            provenance = {}
        return PackageRow(
            package_id=row["package_id"],
            title=row["title"],
            source_version_id=row["source_version_id"],
            midi_sha256=row["midi_sha256"],
            package_digest=row["package_digest"],
            difficulty=row["difficulty"],
            hands_availability=row["hands_availability"],
            rh_count=row["rh_count"],
            lh_count=row["lh_count"],
            duration_seconds=row["duration_seconds"],
            section_count=row["section_count"],
            event_count=row["event_count"],
            source_type=row["source_type"],
            transformation_type=row["transformation_type"],
            provenance=provenance,
            package_path=row["package_path"],
            activated_at=row["activated_at"],
            status=row["status"],
        )

    # ----------------------------------------------------------------- sessions

    def record_session(
        self,
        *,
        session_id: str,
        package_id: str,
        mode: str,
        hand_selection: str | None,
        section_id: str | None,
        tempo_scale: float,
        started_at: str,
        ended_at: str | None,
        expected_count: int,
        correct_count: int,
        missed_count: int,
        extra_count: int,
        chord_completions: int,
        best_streak: int,
        elapsed_seconds: float,
        completed: bool,
        device: str | None = None,
        detail: dict | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sessions (
                       session_id, package_id, mode, hand_selection,
                       section_id, tempo_scale, started_at, ended_at,
                       expected_count, correct_count, missed_count,
                       extra_count, chord_completions, best_streak,
                       elapsed_seconds, completed, device, detail_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, package_id, mode, hand_selection,
                    section_id, float(tempo_scale), started_at, ended_at,
                    int(expected_count), int(correct_count), int(missed_count),
                    int(extra_count), int(chord_completions), int(best_streak),
                    float(elapsed_seconds), int(completed), device,
                    json.dumps(detail or {}, sort_keys=True),
                ),
            )

    def sessions_for_package(self, package_id: str, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE package_id=? "
                "ORDER BY started_at DESC, session_id LIMIT ?",
                (package_id, max(1, min(int(limit), 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    # ----------------------------------------------------------------- progress

    def learning_progress(self) -> dict:
        """One bounded read for Academy progress; sessions remain authoritative."""
        with self._connect() as conn:
            rows = conn.execute("SELECT package_id, COUNT(*) attempts, SUM(CASE WHEN completed = 1 AND json_extract(detail_json, '$.full_lesson') = 1 THEN 1 ELSE 0 END) completions, MAX(started_at) last_practiced FROM sessions GROUP BY package_id").fetchall()
        return {r["package_id"]: dict(r) for r in rows}

    def progress_summary(self, package_id: str) -> dict:
        """Deterministic progress statistics (transparent formulas only)."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS attempts,
                          SUM(completed) AS completions,
                          SUM(CASE WHEN completed=1 AND json_extract(detail_json, '$.full_lesson')=1 THEN 1 ELSE 0 END) AS full_lesson_completions,
                          SUM(expected_count) AS expected,
                          SUM(correct_count) AS correct,
                          SUM(missed_count) AS missed,
                          SUM(extra_count) AS extra,
                          MAX(best_streak) AS best_streak,
                          MAX(started_at) AS last_practiced
                   FROM sessions WHERE package_id=?""",
                (package_id,),
            ).fetchone()
            best_completed = conn.execute(
                "SELECT MAX(tempo_scale) AS best FROM sessions "
                "WHERE package_id=? AND completed=1",
                (package_id,),
            ).fetchone()
            recent = conn.execute(
                "SELECT tempo_scale FROM sessions WHERE package_id=? "
                "ORDER BY started_at DESC, session_id DESC LIMIT 1",
                (package_id,),
            ).fetchone()
        expected = row["expected"] or 0
        correct = row["correct"] or 0
        return {
            "attempts": row["attempts"] or 0,
            "completions": row["completions"] or 0,
            "full_lesson_completions": row["full_lesson_completions"] or 0,
            "note_accuracy": round(correct / expected, 4) if expected else None,
            "expected_notes": expected,
            "correct_notes": correct,
            "missed_notes": row["missed"] or 0,
            "extra_notes": row["extra"] or 0,
            "best_streak": row["best_streak"] or 0,
            "best_completed_tempo": best_completed["best"] if best_completed else None,
            "recent_tempo": recent["tempo_scale"] if recent else None,
            "last_practiced": row["last_practiced"],
            "trouble_sections": self.trouble_sections(package_id),
        }

    def progress_summaries(self, package_ids: list[str]) -> dict[str, dict]:
        """Fresh progress for one catalog page, using one owned read snapshot.

        Bound fanout by the catalog's maximum page size. No connection or
        summary survives this call; concurrent WAL writers remain independent.
        Formulas and recent-session tie breaking match progress_summary.
        """
        if len(package_ids) > 100:
            raise ValueError("progress page exceeds 100 packages")
        ids = tuple(dict.fromkeys(package_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            # Pin all three reads to the same snapshot, including empty IDs.
            conn.execute("BEGIN")
            aggregates = conn.execute(
                f"""SELECT package_id, COUNT(*) AS attempts,
                           SUM(completed) AS completions,
                           SUM(CASE WHEN completed=1 AND json_extract(detail_json, '$.full_lesson')=1 THEN 1 ELSE 0 END) AS full_lesson_completions,
                           SUM(expected_count) AS expected,
                           SUM(correct_count) AS correct,
                           SUM(missed_count) AS missed,
                           SUM(extra_count) AS extra,
                           MAX(best_streak) AS best_streak,
                           MAX(started_at) AS last_practiced,
                           MAX(CASE WHEN completed=1 THEN tempo_scale END) AS best
                    FROM sessions WHERE package_id IN ({placeholders})
                    GROUP BY package_id""", ids,
            ).fetchall()
            recent = conn.execute(
                f"""SELECT package_id, tempo_scale FROM (
                        SELECT package_id, tempo_scale,
                               ROW_NUMBER() OVER (
                                   PARTITION BY package_id
                                   ORDER BY started_at DESC, session_id DESC
                               ) AS ordinal
                        FROM sessions WHERE package_id IN ({placeholders})
                    ) WHERE ordinal=1""", ids,
            ).fetchall()
            sections = conn.execute(
                f"""SELECT package_id, section_id, COUNT(*) AS attempts,
                           SUM(missed_count) AS missed,
                           SUM(expected_count) AS expected
                    FROM sessions WHERE package_id IN ({placeholders})
                         AND section_id IS NOT NULL
                    GROUP BY package_id, section_id""", ids,
            ).fetchall()
        by_id = {row["package_id"]: dict(row) for row in aggregates}
        tempos = {row["package_id"]: row["tempo_scale"] for row in recent}
        trouble: dict[str, list[dict]] = {}
        for row in sections:
            if (row["expected"] or 0) > 0:
                trouble.setdefault(row["package_id"], []).append({
                    "section_id": row["section_id"], "attempts": row["attempts"],
                    "missed_notes": row["missed"] or 0,
                    "miss_rate": round((row["missed"] or 0) / row["expected"], 4),
                })
        result = {}
        for ident in ids:
            row = by_id.get(ident, {})
            expected = row.get("expected") or 0
            correct = row.get("correct") or 0
            ranked = trouble.get(ident, [])
            ranked.sort(key=lambda item: (-item["miss_rate"], item["section_id"]))
            result[ident] = {
                "attempts": row.get("attempts", 0),
                "completions": row.get("completions") or 0,
                "full_lesson_completions": row.get("full_lesson_completions") or 0,
                "note_accuracy": round(correct / expected, 4) if expected else None,
                "expected_notes": expected, "correct_notes": correct,
                "missed_notes": row.get("missed") or 0,
                "extra_notes": row.get("extra") or 0,
                "best_streak": row.get("best_streak") or 0,
                "best_completed_tempo": row.get("best"),
                "recent_tempo": tempos.get(ident),
                "last_practiced": row.get("last_practiced"),
                "trouble_sections": ranked,
            }
        return result

    def trouble_sections(self, package_id: str, min_sessions: int = 1) -> list[dict]:
        """Sections ranked by miss rate (objective, session-derived)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT section_id,
                          COUNT(*) AS attempts,
                          SUM(missed_count) AS missed,
                          SUM(expected_count) AS expected
                   FROM sessions
                   WHERE package_id=? AND section_id IS NOT NULL
                   GROUP BY section_id""",
                (package_id,),
            ).fetchall()
        result = []
        for row in rows:
            expected = row["expected"] or 0
            missed = row["missed"] or 0
            if expected <= 0:
                continue
            result.append(
                {
                    "section_id": row["section_id"],
                    "attempts": row["attempts"],
                    "missed_notes": missed,
                    "miss_rate": round(missed / expected, 4),
                }
            )
        result.sort(key=lambda item: (-item["miss_rate"], item["section_id"]))
        return [item for item in result if item["attempts"] >= min_sessions]


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
