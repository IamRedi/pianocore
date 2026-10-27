"""``piano-core-packages`` CLI: offline import + library inspection (M5).

The OFFLINE path is the primary delivery channel: the operator copies a
practice package folder (or tar envelope) from the Studio machine and
imports it explicitly, mirroring the ``piano-core-library`` scan
pattern. No network, no Studio access, no auto-discovery.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from piano_core.packages.importer import PackageImportError, PackageImporter
from piano_core.packages.store import PackageStore

_EXIT_OK = 0
_EXIT_REJECTED = 1
_EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="piano-core-packages",
        description="Import and inspect Practice Packages (M5).",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="packages library root (default: PIANO_CORE_PACKAGES_ROOT)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="packages SQLite DB (default: PIANO_CORE_PACKAGES_DB_PATH)",
    )
    sub = parser.add_subparsers(dest="command")
    imp = sub.add_parser("import", help="import a package folder or tar file")
    imp.add_argument("source", help="package directory or .tar path")
    sub.add_parser("list", help="list imported packages")
    show = sub.add_parser("show", help="show one imported package")
    show.add_argument("package_id")
    return parser


def _resolve_paths(args) -> tuple[str, str]:
    root = args.root or Path(
        __import__("os").environ.get("PIANO_CORE_PACKAGES_ROOT", "")
    )
    db = args.db or Path(
        __import__("os").environ.get("PIANO_CORE_PACKAGES_DB_PATH", "")
    )
    if not root or not db:
        print(
            "packages root/db not configured (use --root/--db or "
            "PIANO_CORE_PACKAGES_ROOT / PIANO_CORE_PACKAGES_DB_PATH)",
            file=sys.stderr,
        )
        raise SystemExit(_EXIT_USAGE)
    return str(root), str(db)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.command:
        parser.print_usage(sys.stderr)
        return _EXIT_USAGE
    root, db = _resolve_paths(args)
    store = PackageStore(db)
    importer = PackageImporter(root, store)

    if args.command == "import":
        source = Path(args.source)
        try:
            if source.is_dir():
                result = importer.import_from_directory(source)
            else:
                result = importer.import_from_tar(source.read_bytes())
        except (PackageImportError, OSError) as exc:
            print(f"REJECTED: {exc}", file=sys.stderr)
            return _EXIT_REJECTED
        print(json.dumps(result, sort_keys=True))
        return _EXIT_OK

    if args.command == "list":
        rows = [row.to_dict() for row in store.list_packages()]
        print(json.dumps({"packages": rows, "count": len(rows)}, sort_keys=True))
        return _EXIT_OK

    if args.command == "show":
        row = store.get_package(args.package_id)
        if row is None:
            print(f"unknown package: {args.package_id}", file=sys.stderr)
            return _EXIT_REJECTED
        print(json.dumps(row.to_dict(), sort_keys=True))
        return _EXIT_OK

    return _EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
