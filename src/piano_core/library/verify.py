"""Service-context library verification suite (PIANO-P011).

Runs INSIDE the piano-core service context (as user ``piano-core``, with
the service-facing read-only view of the MIDI source library) and proves,
step by step:

1. index access          — open the production index, report stats
2. catalog query         — deterministic listing/search over real entries
3. exact lookup          — song_id -> record
4. resolve               — song_id -> validated source path
5. source readability    — the resolved file opens and starts with MThd
6. P009 boundary         — load_timeline parses the resolved file
7. read-only proof       — creating anything in the source view MUST fail
8. index writability     — a probe row write/delete in a dedicated probe
                           table MUST succeed (production index is
                           service-writable)

Exit code 0 = all required steps passed (steps that need catalog entries
are skipped with an explicit SKIP notice when the catalog is empty).
This module is executed by the ``piano-core-library-verify.service``
oneshot unit; it makes no network calls and sends no MIDI.
"""

from __future__ import annotations

import sys

from piano_core.library.index import LibraryIndex
from piano_core.library.model import SongStatus
from piano_core.library.service import LibraryConfig, LibraryService

_RESULTS: list[tuple[str, str]] = []


def _report(step: str, outcome: str, detail: str = "") -> None:
    _RESULTS.append((step, outcome))
    line = f"[{outcome:>4}] {step}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    import os

    root = os.environ.get("PIANO_CORE_LIBRARY_ROOT", "")
    index_path = os.environ.get("PIANO_CORE_LIBRARY_INDEX_PATH", "")
    print(f"service-context verify: root={root!r} index={index_path!r}", flush=True)

    config = LibraryConfig(root=root, index_path=index_path)
    service = LibraryService(config)

    # [1] index access (full status breakdown as evidence)
    stats = service.stats()
    _report(
        "index-access",
        "PASS",
        f"total={stats['total']} parsed={stats['parsed']} "
        f"pending={stats['pending']} invalid={stats['invalid']} "
        f"unsupported={stats['unsupported']}",
    )

    # [2]/[3]/[4]/[5]/[6] need at least one catalog entry
    entries = service.list_songs(limit=1)
    if not entries:
        _report("catalog-query", "SKIP", "catalog is empty (run the scan unit first)")
        _report("search", "SKIP", "catalog is empty")
        _report("exact-lookup", "SKIP", "catalog is empty")
        _report("source-readable", "SKIP", "catalog is empty")
        _report("p009-load_timeline", "SKIP", "catalog is empty")
    else:
        record = entries[0]
        _report("catalog-query", "PASS", f"first={record.relpath!r}")
        looked_up = service.get(record.song_id)
        if looked_up != record:
            _report("exact-lookup", "FAIL", "lookup mismatch")
            return 1
        _report("exact-lookup", "PASS", record.song_id)
        # filename-fragment search: the first entry must be findable by text
        fragment = record.filename[:8]
        hits = service.search(fragment, limit=5)
        if any(hit.song_id == record.song_id for hit in hits):
            _report("search", "PASS", f"fragment={fragment!r} hits={len(hits)}")
        else:
            _report("search", "FAIL", f"fragment={fragment!r} did not match its own entry")
            return 1
        try:
            resolved = service.resolve(record.song_id)
        except Exception as exc:
            _report("resolve", "FAIL", str(exc))
            return 1
        _report("resolve", "PASS", str(resolved))
        try:
            with open(resolved, "rb") as handle:
                head = handle.read(4)
            if head != b"MThd":
                raise RuntimeError(f"unexpected header {head!r}")
            _report("source-readable", "PASS", f"{record.size_bytes}B")
        except Exception as exc:
            _report("source-readable", "FAIL", str(exc))
            return 1
        from piano_core.playback.timeline import load_timeline

        try:
            timeline = load_timeline(resolved)
            _report(
                "p009-load_timeline",
                "PASS",
                f"events={len(timeline.events)} duration={timeline.duration:.1f}s",
            )
        except Exception as exc:
            _report("p009-load_timeline", "FAIL", str(exc))
            return 1

    # [7] read-only proof: creation in the source view MUST fail
    probe = config.root_real / f".piano-core-readonly-probe-{os.getpid()}"
    try:
        probe.mkdir()
    except OSError:
        _report("source-read-only", "PASS", "creation refused by the service view")
    else:
        try:
            probe.rmdir()
        except OSError:
            pass
        _report("source-read-only", "FAIL", "creation SUCCEEDED — contract violated")
        return 1

    # [8] index writability: probe table write/delete (transactional)
    with LibraryIndex(index_path) as index:
        try:
            with index._conn:  # noqa: SLF001 — probe lives in the same package
                index._conn.execute(
                    "CREATE TABLE IF NOT EXISTS verify_probe(song_id TEXT PRIMARY KEY)"
                )
                index._conn.execute(
                    "INSERT OR IGNORE INTO verify_probe(song_id) VALUES ('probe')"
                )
                index._conn.execute("DELETE FROM verify_probe WHERE song_id = 'probe'")
            index._conn.execute("DROP TABLE IF EXISTS verify_probe")
            _report("index-writable", "PASS", "probe row written and removed")
        except Exception as exc:
            _report("index-writable", "FAIL", str(exc))
            return 1

    failed = [step for step, outcome in _RESULTS if outcome == "FAIL"]
    print(f"verify summary: {len(_RESULTS)} steps, {len(failed)} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
