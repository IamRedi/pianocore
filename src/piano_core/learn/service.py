"""Learn catalog service (PIANO-LEARN-001).

Runtime read-side of the Learn Library. Serves catalog overview,
lesson lists/detail with filters and search, curriculum neighbors, and
merged per-lesson progress (practice-session truth stays in the M5
package store — this service never invents progress).

Degrade contract (house pattern): construction/runtime failures mark
the service unready and raise LearnError; they never break application
startup or the Player.
"""

from __future__ import annotations

import logging
from pathlib import Path

from piano_core.learn import (
    LEARN_CATALOG_SCHEMA_VERSION,
    LearnCatalogError,
    LearnConfigError,
)
from piano_core.learn.catalog import CourseRow, LearnCatalog, LessonRow

__all__ = ["LearnService"]

_LOGGER = logging.getLogger(__name__)


class LearnService:
    """Read-only Learn catalog service with progress merge."""

    def __init__(
        self,
        db_path: str | Path,
        progress_lookup=None,
        logger: logging.Logger | None = None,
        progress_all_lookup=None,
        progress_many_lookup=None,
    ) -> None:
        self._log = logger or _LOGGER
        path = Path(db_path)
        if not str(path) or not path.is_absolute():
            raise LearnConfigError(
                "learn_db_path must be an absolute path "
                "(PIANO_CORE_LEARN_DB_PATH)"
            )
        if not path.is_file():
            raise LearnCatalogError(f"learn catalog database missing: {path}")
        self._catalog = LearnCatalog(path)
        # Fail fast on an empty/stale catalog: readiness must be honest.
        self._catalog.schema_version()
        self._progress_all_lookup = progress_all_lookup
        self._progress_lookup = progress_lookup
        self._progress_many_lookup = progress_many_lookup
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready

    def close(self) -> None:
        self._catalog.close()
        self._ready = False

    # -- catalog surfaces --------------------------------------------

    def overview(self) -> dict:
        stats = self._catalog.stats()
        courses = [
            {
                "course_id": course.course_id,
                "title": course.title,
                "level": course.level,
                "summary": course.summary,
                "lesson_count": course.lesson_count,
            }
            for course in self._catalog.courses()
        ]
        progress = self._academy_progress()
        for course in courses:
            course["completed_lessons"] = progress["courses"].get(course["course_id"], 0)
        return {
            "progress": progress,
            "ready": True,
            "schema_version": LEARN_CATALOG_SCHEMA_VERSION,
            "lesson_count": stats["lesson_count"],
            "course_count": stats["course_count"],
            "levels": stats["levels"],
            "sources": stats["sources"],
            "courses": courses,
        }

    def _academy_progress(self):
        result = {"available": self._progress_all_lookup is not None, "courses": {}, "levels": {}, "completed_lessons": 0, "continue_lesson": None}
        if self._progress_all_lookup is None:
            return result
        try:
            summaries = self._progress_all_lookup()
            recent_time = ""
            for package_id, summary in summaries.items():
                lesson = self._catalog.lesson_by_package(package_id)
                if lesson is None:
                    continue
                if summary.get("completions", 0) > 0:
                    result["completed_lessons"] += 1
                    result["courses"][lesson.course_id] = result["courses"].get(lesson.course_id, 0) + 1
                    key = str(lesson.level)
                    result["levels"][key] = result["levels"].get(key, 0) + 1
                recent = summary.get("last_practiced") or ""
                if recent > recent_time:
                    recent_time = recent
                    result["continue_lesson"] = {**lesson.to_dict(), "progress": summary}
        except Exception:
            self._log.exception("Academy progress unavailable")
            return {**result, "available": False, "continue_lesson": None}
        return result

    def lessons(
        self,
        *,
        course_id: str | None = None,
        level: int | None = None,
        text: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        rows = self._catalog.lessons(
            course_id=course_id, level=level, text=text, limit=limit, offset=offset
        )
        if self._progress_many_lookup is None:
            details = [self._lesson_with_progress(row) for row in rows]
        else:
            try:
                summaries = self._progress_many_lookup([row.package_id for row in rows])
            except Exception as exc:
                self._log.warning("learn page progress lookup failed: %s", exc)
                summaries = {}
            details = []
            for row in rows:
                summary = summaries.get(row.package_id)
                details.append({**row.to_dict(),
                    "progress_available": summary is not None,
                    "progress": self._qualified_progress(summary)})
        return {
            "lessons": details,
            "count": len(rows),
            "offset": offset,
            "limit": limit,
        }

    def _lesson_with_progress(self, row):
        detail = row.to_dict()
        detail["progress_available"] = self._progress_lookup is not None
        if self._progress_lookup is not None:
            try:
                detail["progress"] = self._qualified_progress(self._progress_lookup(row.package_id))
            except Exception:
                detail["progress"] = None
                detail["progress_available"] = False
        return detail

    def lesson_detail(self, lesson_id: str) -> dict:
        row = self._catalog.lesson(lesson_id)
        if row is None:
            raise LearnCatalogError(f"unknown lesson: {lesson_id!r}")
        previous, nxt = self._catalog.neighbors(row)
        detail = row.to_dict()
        detail["metrics"] = row.metrics
        detail["level_decision"] = row.level_decision
        detail["previous_lesson_id"] = previous.lesson_id if previous else None
        detail["next_lesson_id"] = nxt.lesson_id if nxt else None
        detail["license"] = {
            "name": row.license_name,
            "url": row.license_url,
            "attribution": row.attribution,
        }
        progress = None
        detail["progress_available"] = self._progress_lookup is not None
        if self._progress_lookup is not None:
            try:
                progress = self._qualified_progress(self._progress_lookup(row.package_id))
            except Exception as exc:  # degrade, never break the read
                self._log.warning("learn progress lookup failed: %s", exc)
                progress = None
                detail["progress_available"] = False
        detail["progress"] = progress
        return detail

    @staticmethod
    def _qualified_progress(progress):
        if progress is not None and "full_lesson_completions" in progress:
            return {**progress, "completions": progress["full_lesson_completions"]}
        return progress

    def lesson_by_package(self, package_id: str) -> dict | None:
        row = self._catalog.lesson_by_package(package_id)
        return row.to_dict() if row else None
