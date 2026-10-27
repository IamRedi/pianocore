"""Learn content ingest pipeline (PIANO-LEARN-001).

Deterministic build of the complete Learn Library from the downloaded
source collections:

1. discover: enumerate the pinned Pianio corpus and the Source B
   provenance manifest (every file with its SHA-256 and license);
2. convert: run every source item through the canonical adapters
   (UNSUPPORTED items fall out with exact reasons — never repaired);
3. build: every supported item becomes a contract-validated
   practice-package/v1 package with a stable cross-rebuild identity
   (``source_version_id`` derives from the lesson key hash, so package
   identities do not shift when the catalog grows);
4. publish: write packages + the catalog database + a build manifest
   with the exact SOURCE -> DISCOVERED -> DOWNLOADED -> VALIDATED ->
   IMPORTED -> PUBLISHED -> EXCLUDED ledger.

No timestamps or machine-local paths enter the outputs (the build is
byte-reproducible for identical inputs).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from piano_core.learn.builder import build_package
from piano_core.learn.catalog import LearnCatalog
from piano_core.learn.model import LearnItem, lesson_id_for
from piano_core.learn.sources_midi import iter_midi_items
from piano_core.learn.sources_pianio import iter_pianio_items

__all__ = ["ingest", "IngestResult"]

#: Stable non-negative identity basis: 63 bits of the lesson key hash.
#: Collision probability across the catalog is negligible (birthday bound
#: far below 1e-9 for any realistic catalog size), and identities never
#: shift when the catalog grows or reorders.
_VERSION_BITS = 63


def _source_version_id(lesson_key: str) -> int:
    digest = hashlib.sha256(lesson_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def _pianio_courses(content_dir: Path, upstream_commit: str | None) -> list[dict]:
    """Course metadata straight from the pinned upstream corpus."""
    courses = []
    for course_path in sorted(content_dir.glob("*/*/course.json")):
        raw = json.loads(course_path.read_text(encoding="utf-8"))
        course_id = str(raw.get("courseId") or course_path.parent.name)
        level = raw.get("level")
        courses.append(
            {
                "course_id": f"pianio:{course_id}",
                "title": str(raw.get("title") or course_id),
                # Curriculum level comes from the adapter's level map via
                # the exercise hints; the raw upstream level is preserved.
                "upstream_level": level if isinstance(level, int) else None,
                "summary": str(raw.get("summary") or ""),
                "source": "pianio",
                "exercises": [str(e.get("id")) for e in raw.get("exercises", []) if e.get("id")],
            }
        )
    return courses


def _pianio_course_level_map(content_dir: Path, upstream_commit: str | None) -> dict[str, int]:
    """course_id -> curriculum level (majority of its exercises' hints)."""
    counts: dict[str, dict[int, int]] = {}
    for item in iter_pianio_items(content_dir, upstream_commit=upstream_commit):
        course_id = str(item.source_detail.get("course_id"))
        hint = item.level_hint
        if hint is None:
            continue
        counts.setdefault(f"pianio:{course_id}", {}).setdefault(hint, 0)
        counts[f"pianio:{course_id}"][hint] += 1
    return {
        course: max(hints.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        for course, hints in counts.items()
    }


def _midi_records(matches_path: Path, sources_root: Path) -> list[dict]:
    """Source B records from the provenance manifest (SHA-bound)."""
    matches = json.loads(matches_path.read_text(encoding="utf-8"))
    records = []
    for match in matches:
        mutopia = match.get("mutopia")
        primary = (
            sources_root / mutopia["local_mid"] if mutopia else sources_root / match["pianovera_mid"]
        )
        fallback = sources_root / match["pianovera_mid"] if mutopia else None
        license_name = (
            str(mutopia.get("license"))
            if mutopia and mutopia.get("license")
            else "Public Domain Mark 1.0"
        )
        license_url = (
            None
            if mutopia
            else "https://creativecommons.org/publicdomain/mark/1.0/"
        )
        source_detail = {
            "collection_category": match["category"],
            "pianovera_slug": match["slug"],
            "pianovera_page": match["title"],
            "pianovera_midi_sha256": match["pianovera_mid_sha256"],
            "pianovera_pdf_sha256": match["pianovera_pdf_sha256"],
            "pianovera_pdf": match["pianovera_pdf"],
        }
        if mutopia:
            source_detail.update(
                {
                    "mutopia_id": mutopia["id"],
                    "mutopia_music_id": mutopia.get("music_id"),
                    "mutopia_title": mutopia.get("title"),
                    "mutopia_composer": mutopia.get("composer"),
                    "mutopia_opus": mutopia.get("opus"),
                    "mutopia_copyright": mutopia.get("copyright"),
                    "mutopia_typesetter": mutopia.get("typesetter"),
                    "mutopia_arranger": mutopia.get("arranger"),
                    "mutopia_source": mutopia.get("source"),
                    "mutopia_midi_sha256": mutopia.get("mid_sha256"),
                    "mutopia_download": mutopia.get("midi"),
                }
            )
        records.append(
            {
                "lesson_key": (
                    f"mutopia:{match['slug']}" if mutopia else f"pianovera:{match['slug']}"
                ),
                "title": match["title"],
                "composer": match.get("composer"),
                "path": primary,
                "fallback_path": fallback,
                "source": "mutopia" if mutopia else "pianovera",
                "category": match["category"],
                "license_name": license_name,
                "license_url": license_url,
                "attribution": (
                    "Engraved by the Mutopia Project"
                    if mutopia
                    else "Engraving by Pianovera (Mutopia-sourced, Public Domain Mark 1.0)"
                ),
                "midi_sha256": (
                    mutopia.get("mid_sha256") if mutopia else match["pianovera_mid_sha256"]
                ),
                "source_detail": source_detail,
                "summary": "",
            }
        )
    return records


def ingest(
    sources_root: str | Path,
    out_root: str | Path,
    upstream_commit: str | None = None,
) -> "IngestResult":
    """Build the full Learn Library into ``out_root``."""
    sources = Path(sources_root)
    out = Path(out_root)
    packages_dir = out / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)

    pianio_content = sources / "upstream" / "pianio" / "content"
    matches_path = sources / "source-b" / "sourceb_matches.json"
    if not pianio_content.is_dir():
        raise FileNotFoundError(f"missing Pianio content at {pianio_content}")
    if not matches_path.is_file():
        raise FileNotFoundError(f"missing Source B manifest at {matches_path}")

    course_levels = _pianio_course_level_map(pianio_content, upstream_commit)
    course_meta = {
        c["course_id"]: c for c in _pianio_courses(pianio_content, upstream_commit)
    }

    items: list[LearnItem] = []
    excluded: list[dict] = []
    discovered = 0

    for item in iter_pianio_items(pianio_content, upstream_commit=upstream_commit):
        discovered += 1
        if item.problems:
            excluded.append({"lesson_key": item.lesson_key, "reasons": list(item.problems)})
        else:
            items.append(item)

    for record in _midi_records(matches_path, sources):
        discovered += 1
        built_items = list(iter_midi_items(iter([record])))
        item = built_items[0]
        if item.problems:
            excluded.append({"lesson_key": item.lesson_key, "reasons": list(item.problems)})
        else:
            items.append(item)

    # Deterministic order: (source, lesson_key). Ids never depend on it.
    items.sort(key=lambda i: (i.source, i.lesson_key))

    packages: list[PackageBuildRecord] = []
    lesson_rows: list[dict] = []
    course_rows: dict[str, dict] = {}

    for index, item in enumerate(items):
        build = build_package(
            item,
            source_version_id=_source_version_id(item.lesson_key),
        )
        package_dir = packages_dir / build.package_id
        package_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in build.members.items():
            member_path = package_dir / name
            if not member_path.is_file() or member_path.read_bytes() != payload:
                member_path.write_bytes(payload)

        course_id = _course_for(item, course_levels, course_meta)
        level = build.level.level
        course = course_rows.get(course_id)
        if course is None:
            if item.source == "pianio":
                meta = course_meta[course_id]
                title, summary = meta["title"], meta["summary"]
                course_level = course_levels[course_id]
            else:
                band = str(item.source_detail.get("collection_category"))
                course_level = {"beginner": 1, "intermediate": 3, "advanced": 4}[band]
                title = f"Repertoire — {band.capitalize()}"
                summary = (
                    f"Published {band} piano repertoire "
                    f"(Mutopia Project / Pianovera collection)"
                )
            course = {
                "course_id": course_id,
                "title": title,
                "level": course_level,
                "summary": summary,
                "source": item.source,
                "order_index": len(course_rows),
                "lesson_count": 0,
            }
            course_rows[course_id] = course
        course["lesson_count"] += 1

        order_index = len(lesson_rows)
        lesson_rows.append(
            {
                "lesson_id": build.lesson_id,
                "lesson_key": item.lesson_key,
                "package_id": build.package_id,
                "course_id": course_id,
                "title": item.title,
                "composer": item.composer,
                "source": item.source,
                "level": level,
                "difficulty": build.level.difficulty,
                "duration_seconds": build.metrics.duration_seconds,
                "order_index": order_index,
                "hands_availability": item.hands_availability,
                "license_name": item.license_name,
                "license_url": item.license_url,
                "attribution": item.attribution,
                "metrics": build.metrics.as_dict(),
                "level_decision": build.level.as_dict(),
                "summary": item.summary or "",
            }
        )
        packages.append(
            PackageBuildRecord(
                lesson_key=item.lesson_key,
                lesson_id=build.lesson_id,
                package_id=build.package_id,
                midi_sha256=build.midi_sha256,
                source_version_id=_source_version_id(item.lesson_key),
                level=level,
                difficulty=build.level.difficulty,
                duration_seconds=round(build.metrics.duration_seconds, 3),
                note_count=len(item.notes),
                hands_availability=item.hands_availability,
            )
        )

    courses = sorted(
        course_rows.values(), key=lambda c: (c["level"], c["order_index"])
    )
    for position, course in enumerate(courses):
        course["order_index"] = position

    catalog_path = out / "learn.sqlite3"
    catalog = LearnCatalog(catalog_path)
    catalog.replace_catalog(courses, lesson_rows)
    stats = catalog.stats()
    catalog.close()

    manifest = {
        "upstream": {
            "pianio_commit": upstream_commit or "unknown",
            "pianio_license": "CC0-1.0",
            "source_b_manifest": "source-b/sourceb_matches.json",
        },
        "ledger": {
            "discovered": discovered,
            "validated": discovered - len(excluded),
            "imported": len(packages),
            "published": len(packages),
            "excluded": len(excluded),
        },
        "catalog_stats": stats,
        "excluded": excluded,
        "packages": [p._asdict() for p in packages],
    }
    manifest_path = out / "build-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

    return IngestResult(
        discovered=discovered,
        validated=discovered - len(excluded),
        published=len(packages),
        excluded=len(excluded),
        courses=len(courses),
        levels={int(k): v for k, v in stats["levels"].items()},
        manifest_path=manifest_path,
        catalog_path=catalog_path,
    )


def _course_for(
    item: LearnItem,
    course_levels: dict[str, int],
    course_meta: dict[str, dict],
) -> str:
    if item.source == "pianio":
        course_id = f"pianio:{item.source_detail['course_id']}"
        if course_id not in course_meta:
            raise ValueError(f"missing course metadata for {course_id}")
        return course_id
    band = str(item.source_detail.get("collection_category") or "beginner")
    return f"repertoire:{band}"


class PackageBuildRecord:
    """Ledger row for one published package."""

    __slots__ = (
        "lesson_key", "lesson_id", "package_id", "midi_sha256",
        "source_version_id", "level", "difficulty", "duration_seconds",
        "note_count", "hands_availability",
    )

    def __init__(
        self,
        *,
        lesson_key: str,
        lesson_id: str,
        package_id: str,
        midi_sha256: str,
        source_version_id: int,
        level: int,
        difficulty: str,
        duration_seconds: float,
        note_count: int,
        hands_availability: str,
    ) -> None:
        self.lesson_key = lesson_key
        self.lesson_id = lesson_id
        self.package_id = package_id
        self.midi_sha256 = midi_sha256
        self.source_version_id = source_version_id
        self.level = level
        self.difficulty = difficulty
        self.duration_seconds = duration_seconds
        self.note_count = note_count
        self.hands_availability = hands_availability

    def _asdict(self) -> dict:
        return {
            "lesson_key": self.lesson_key,
            "lesson_id": self.lesson_id,
            "package_id": self.package_id,
            "midi_sha256": self.midi_sha256,
            "source_version_id": self.source_version_id,
            "level": self.level,
            "difficulty": self.difficulty,
            "duration_seconds": self.duration_seconds,
            "note_count": self.note_count,
            "hands_availability": self.hands_availability,
        }


class IngestResult:
    __slots__ = (
        "discovered", "validated", "published", "excluded", "courses",
        "levels", "manifest_path", "catalog_path",
    )

    def __init__(
        self,
        *,
        discovered: int,
        validated: int,
        published: int,
        excluded: int,
        courses: int,
        levels: dict[int, int],
        manifest_path: Path,
        catalog_path: Path,
    ) -> None:
        self.discovered = discovered
        self.validated = validated
        self.published = published
        self.excluded = excluded
        self.courses = courses
        self.levels = levels
        self.manifest_path = manifest_path
        self.catalog_path = catalog_path
