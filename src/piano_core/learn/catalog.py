"""Learn catalog store (PIANO-LEARN-001).

SQLite catalog over the published Learn library: courses, lessons and
their curriculum placement. This is a SEPARATE namespace from both the
Player library index and the M5 package store — the Learn catalog rows
reference packages by ``package_id`` but never expose Player songs, and
the Player surfaces never read this database.

The catalog is built offline by the ingest CLI (deterministic; same
sources always produce the same rows and identities) and read by the
LearnService in the appliance runtime.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from piano_core.learn import LEARN_CATALOG_SCHEMA_VERSION, LearnCatalogError

__all__ = ["LearnCatalog", "CourseRow", "LessonRow"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
    course_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    level INTEGER NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    lesson_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lessons (
    lesson_id TEXT PRIMARY KEY,
    lesson_key TEXT NOT NULL UNIQUE,
    package_id TEXT NOT NULL UNIQUE,
    course_id TEXT NOT NULL,
    title TEXT NOT NULL,
    composer TEXT,
    source TEXT NOT NULL,
    level INTEGER NOT NULL,
    difficulty TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    order_index INTEGER NOT NULL,
    hands_availability TEXT NOT NULL,
    license_name TEXT NOT NULL,
    license_url TEXT,
    attribution TEXT,
    search_text TEXT NOT NULL DEFAULT '',
    metrics_json TEXT NOT NULL,
    level_json TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(course_id) REFERENCES courses(course_id)
);
CREATE INDEX IF NOT EXISTS idx_lessons_level ON lessons(level, order_index);
CREATE INDEX IF NOT EXISTS idx_lessons_course ON lessons(course_id, order_index);
"""


@dataclass(frozen=True, slots=True)
class CourseRow:
    course_id: str
    title: str
    level: int
    summary: str
    source: str
    order_index: int
    lesson_count: int

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "title": self.title,
            "level": self.level,
            "summary": self.summary,
            "source": self.source,
            "lesson_count": self.lesson_count,
        }


@dataclass(frozen=True, slots=True)
class LessonRow:
    lesson_id: str
    lesson_key: str
    package_id: str
    course_id: str
    title: str
    composer: str | None
    source: str
    level: int
    difficulty: str
    duration_seconds: float
    order_index: int
    hands_availability: str
    license_name: str
    license_url: str | None
    attribution: str | None
    metrics: dict
    level_decision: dict
    summary: str

    def to_dict(self) -> dict:
        return {
            "lesson_id": self.lesson_id,
            "package_id": self.package_id,
            "course_id": self.course_id,
            "title": self.title,
            "composer": self.composer,
            "source": self.source,
            "level": self.level,
            "difficulty": self.difficulty,
            "duration_seconds": round(self.duration_seconds, 3),
            "hands_availability": self.hands_availability,
            "license_name": self.license_name,
            "summary": self.summary,
        }


def _fold(text: str) -> str:
    """Accent-fold + lowercase for deterministic search matching."""
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c)
    )


def _lesson_row(row) -> LessonRow:
    return LessonRow(
        lesson_id=row[0],
        lesson_key=row[1],
        package_id=row[2],
        course_id=row[3],
        title=row[4],
        composer=row[5],
        source=row[6],
        level=row[7],
        difficulty=row[8],
        duration_seconds=row[9],
        order_index=row[10],
        hands_availability=row[11],
        license_name=row[12],
        license_url=row[13],
        attribution=row[14],
        metrics=json.loads(row[15]),
        level_decision=json.loads(row[16]),
        summary=row[17],
    )

_LESSON_COLUMNS = (
    "lesson_id, lesson_key, package_id, course_id, title, composer, source, "
    "level, difficulty, duration_seconds, order_index, hands_availability, "
    "license_name, license_url, attribution, metrics_json, level_json, summary"
)


def _catalog_operation(method):
    """Own the shared connection through schema checks and complete fetches.

    SQLite's serialized mode does not protect CPython's cached-statement
    lifecycle across concurrent execute/fetch calls. Materialize each catalog
    result before releasing ownership; progress callbacks run in LearnService,
    outside this boundary. RLock permits nested schema_version validation.
    """
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


class LearnCatalog:
    """Read/write access to the deterministic Learn catalog database."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if not path.is_absolute():
            raise LearnCatalogError("learn catalog path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        # Derived-index migration: the catalog is fully rebuildable from
        # the packages, so a missing column drops and rebuilds the tables
        # (never a partial schema).
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(lessons)").fetchall()
        }
        if "search_text" not in columns:
            self._conn.executescript(
                "DROP TABLE IF EXISTS lessons;"
                "DROP TABLE IF EXISTS courses;"
            )
            self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- ingest-side (writer) API ------------------------------------

    def replace_catalog(
        self,
        courses: list[dict],
        lessons: list[dict],
    ) -> None:
        """Atomically replace the whole catalog (deterministic rebuild)."""
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM lessons")
                self._conn.execute("DELETE FROM courses")
                self._conn.executemany(
                    "INSERT INTO courses(course_id, title, level, summary, source,"
                    " order_index, lesson_count) VALUES(?,?,?,?,?,?,?)",
                    [
                        (
                            c["course_id"],
                            c["title"],
                            int(c["level"]),
                            c["summary"],
                            c["source"],
                            int(c["order_index"]),
                            int(c["lesson_count"]),
                        )
                        for c in courses
                    ],
                )
                self._conn.executemany(
                    "INSERT INTO lessons(lesson_id, lesson_key, package_id,"
                    " course_id, title, composer, source, level, difficulty,"
                    " duration_seconds, order_index, hands_availability,"
                    " license_name, license_url, attribution, search_text,"
                    " metrics_json, level_json, summary) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            l["lesson_id"],
                            l["lesson_key"],
                            l["package_id"],
                            l["course_id"],
                            l["title"],
                            l["composer"],
                            l["source"],
                            int(l["level"]),
                            l["difficulty"],
                            float(l["duration_seconds"]),
                            int(l["order_index"]),
                            l["hands_availability"],
                            l["license_name"],
                            l["license_url"],
                            l["attribution"],
                            _fold(
                                f"{l['title']} {l['composer'] or ''} "
                                f"{l['lesson_key']}"
                            ),
                            json.dumps(l["metrics"], sort_keys=True),
                            json.dumps(l["level_decision"], sort_keys=True),
                            l["summary"],
                        )
                        for l in lessons
                    ],
                )
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(LEARN_CATALOG_SCHEMA_VERSION),),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # -- runtime-side (reader) API -----------------------------------

    @_catalog_operation
    def schema_version(self) -> str:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            raise LearnCatalogError("learn catalog is empty (no schema_version)")
        if row[0] != str(LEARN_CATALOG_SCHEMA_VERSION):
            raise LearnCatalogError(
                f"learn catalog schema {row[0]!r} unsupported "
                f"(expected {LEARN_CATALOG_SCHEMA_VERSION!r})"
            )
        return row[0]

    @_catalog_operation
    def stats(self) -> dict:
        self.schema_version()
        row = self._conn.execute(
            "SELECT COUNT(*), MIN(level), MAX(level),"
            " COUNT(DISTINCT course_id) FROM lessons"
        ).fetchone()
        by_level = self._conn.execute(
            "SELECT level, COUNT(*) FROM lessons GROUP BY level ORDER BY level"
        ).fetchall()
        by_source = self._conn.execute(
            "SELECT source, COUNT(*) FROM lessons GROUP BY source ORDER BY source"
        ).fetchall()
        return {
            "lesson_count": row[0],
            "min_level": row[1],
            "max_level": row[2],
            "course_count": row[3],
            "levels": {str(level): count for level, count in by_level},
            "sources": {source: count for source, count in by_source},
        }

    @_catalog_operation
    def courses(self) -> list[CourseRow]:
        self.schema_version()
        rows = self._conn.execute(
            "SELECT course_id, title, level, summary, source, order_index,"
            " lesson_count FROM courses ORDER BY order_index"
        ).fetchall()
        return [
            CourseRow(
                course_id=r[0], title=r[1], level=r[2], summary=r[3],
                source=r[4], order_index=r[5], lesson_count=r[6],
            )
            for r in rows
        ]

    @_catalog_operation
    def lessons(
        self,
        *,
        course_id: str | None = None,
        level: int | None = None,
        text: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LessonRow]:
        self.schema_version()
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        clauses = []
        params: list = []
        if course_id:
            clauses.append("course_id = ?")
            params.append(course_id)
        if level is not None:
            clauses.append("level = ?")
            params.append(int(level))
        if text:
            tokens = _fold(text).split()
            for token in tokens:
                clauses.append("search_text LIKE ?")
                params.append(f"%{token}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT {_LESSON_COLUMNS} FROM lessons{where}"
            " ORDER BY level, order_index LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_lesson_row(r) for r in rows]

    @_catalog_operation
    def lesson(self, lesson_id: str) -> LessonRow | None:
        self.schema_version()
        row = self._conn.execute(
            f"SELECT {_LESSON_COLUMNS} FROM lessons WHERE lesson_id = ?",
            (lesson_id,),
        ).fetchone()
        return _lesson_row(row) if row else None

    @_catalog_operation
    def lesson_by_package(self, package_id: str) -> LessonRow | None:
        self.schema_version()
        row = self._conn.execute(
            f"SELECT {_LESSON_COLUMNS} FROM lessons WHERE package_id = ?",
            (package_id,),
        ).fetchone()
        return _lesson_row(row) if row else None

    @_catalog_operation
    def neighbors(self, lesson: LessonRow) -> tuple[LessonRow | None, LessonRow | None]:
        """Previous/next lesson in global curriculum order (level, order)."""
        self.schema_version()
        prev_row = self._conn.execute(
            f"SELECT {_LESSON_COLUMNS} FROM lessons"
            " WHERE (level < ?) OR (level = ? AND order_index < ?)"
            " ORDER BY level DESC, order_index DESC LIMIT 1",
            (lesson.level, lesson.level, lesson.order_index),
        ).fetchone()
        next_row = self._conn.execute(
            f"SELECT {_LESSON_COLUMNS} FROM lessons"
            " WHERE (level > ?) OR (level = ? AND order_index > ?)"
            " ORDER BY level ASC, order_index ASC LIMIT 1",
            (lesson.level, lesson.level, lesson.order_index),
        ).fetchone()
        return (
            _lesson_row(prev_row) if prev_row else None,
            _lesson_row(next_row) if next_row else None,
        )

    @_catalog_operation
    def course_progress(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT course_id, COUNT(*) FROM lessons GROUP BY course_id"
        ).fetchall()
        return {course_id: count for course_id, count in rows}
