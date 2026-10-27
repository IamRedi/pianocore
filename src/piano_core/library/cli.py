"""``piano-core-library``: catalog CLI surface (PIANO-P010).

Subcommands:

- ``scan``    — inventory + (bounded, resumable) metadata enrichment
- ``stats``   — catalog counts by status
- ``list``    — deterministic, paginated song listing (filters optional)
- ``search``  — case-insensitive substring search on the relative path
- ``resolve`` — song_id to the validated source MIDI path (P009 input)

Configuration: ``--library-root`` / ``--index-path`` flags override
``PIANO_CORE_LIBRARY_ROOT`` / ``PIANO_CORE_LIBRARY_INDEX_PATH``; unset
configuration fails closed. The source library is only ever read.

Exit codes: ``0`` success, ``1`` operation error, ``2`` usage/config.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from piano_core import PRODUCT_NAME, RUNTIME_IDENTIFIER, __version__
from piano_core.config import AppConfig, ConfigError
from piano_core.library.model import (
    LibraryConfigError,
    LibraryError,
    LibraryPathError,
    SongStatus,
)
from piano_core.library.service import LibraryConfig, LibraryService


def _build_parser() -> argparse.ArgumentParser:
    # Library connection options are accepted both before and after the
    # subcommand (shared parent parser).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--library-root", default=None, metavar="PATH",
                        help="source MIDI library root (overrides PIANO_CORE_LIBRARY_ROOT)")
    common.add_argument("--index-path", default=None, metavar="PATH",
                        help="catalog index database (overrides PIANO_CORE_LIBRARY_INDEX_PATH)")
    parser = argparse.ArgumentParser(
        prog="piano-core-library",
        description="Piano Core song catalog (read-only over the source library).",
        parents=[common],
    )
    parser.add_argument("--version", action="store_true", help="print product identity and exit")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", parents=[common], help="inventory + bounded metadata enrichment")
    p_scan.add_argument("--no-enrich", action="store_true", help="inventory only")
    p_scan.add_argument("--limit", type=int, default=None, metavar="N",
                        help="enrich at most N files this run (resumable)")
    p_scan.add_argument("--batch-size", type=int, default=200, metavar="N")

    sub.add_parser("stats", parents=[common], help="catalog counts by status")

    p_list = sub.add_parser("list", parents=[common], help="list catalog entries (deterministic order)")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--status", default=None, choices=[s.value for s in SongStatus])
    p_list.add_argument("--format", type=int, default=None, dest="midi_format")

    p_search = sub.add_parser("search", parents=[common], help="case-insensitive path substring search")
    p_search.add_argument("text")
    p_search.add_argument("--limit", type=int, default=20)

    p_resolve = sub.add_parser("resolve", parents=[common], help="print the validated source path for a song_id")
    p_resolve.add_argument("song_id")
    return parser


def _config(args: argparse.Namespace, env: object) -> tuple[LibraryConfig, AppConfig]:
    app_config = AppConfig.from_env(env)
    root = args.library_root if args.library_root is not None else app_config.library_root
    index_path = args.index_path if args.index_path is not None else app_config.library_index_path
    return LibraryConfig(root=root, index_path=index_path), app_config


def _print_record(record) -> None:
    meta = ""
    if record.status.value == "parsed":
        meta = (
            f" fmt={record.midi_format} tracks={record.track_count} "
            f"dur={record.duration_seconds}s"
        )
    print(f"{record.song_id}  {record.status.value:11} {record.size_bytes:>9}B  "
          f"{record.relpath}{meta}")


def main(
    argv: list[str] | None = None,
    env: object = None,
) -> int:
    """Console entrypoint. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"{PRODUCT_NAME} {__version__} ({RUNTIME_IDENTIFIER})")
        return 0

    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2

    try:
        config, _app_config = _config(args, env)
        service = LibraryService(config)
    except (ConfigError, LibraryConfigError) as error:
        print(f"piano-core-library: invalid configuration: {error}", file=sys.stderr)
        return 2

    try:
        if args.command == "scan":
            result = service.scan(
                enrich=not args.no_enrich,
                enrich_limit=args.limit,
                batch_size=max(1, args.batch_size),
            )
            print(f"scan: {result.describe()}")
            return 0

        if args.command == "stats":
            stats = service.stats()
            print(f"total={stats['total']} parsed={stats['parsed']} "
                  f"pending={stats['pending']} invalid={stats['invalid']} "
                  f"unsupported={stats['unsupported']}")
            return 0

        if args.command == "list":
            status = SongStatus(args.status) if args.status else None
            for record in service.list_songs(
                limit=args.limit, offset=args.offset, status=status,
                midi_format=args.midi_format,
            ):
                _print_record(record)
            return 0

        if args.command == "search":
            for record in service.search(args.text, limit=args.limit):
                _print_record(record)
            return 0

        if args.command == "resolve":
            path = service.resolve(args.song_id)
            print(str(Path(path)))
            return 0

    except LibraryPathError as error:
        print(f"piano-core-library: {error}", file=sys.stderr)
        return 1
    except LibraryError as error:
        print(f"piano-core-library: {error}", file=sys.stderr)
        return 1
    return 2
