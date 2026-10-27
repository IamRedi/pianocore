"""piano-core-learn CLI: deterministic Learn Library build + validation."""

from __future__ import annotations

import argparse
import json
import sys

from piano_core.learn import LearnError
from piano_core.learn.ingest import ingest
from piano_core.learn.validate import validate_build


def _cmd_ingest(args: argparse.Namespace) -> int:
    result = ingest(
        sources_root=args.sources_root,
        out_root=args.out_root,
        upstream_commit=args.upstream_commit,
    )
    print(
        json.dumps(
            {
                "discovered": result.discovered,
                "validated": result.validated,
                "published": result.published,
                "excluded": result.excluded,
                "courses": result.courses,
                "levels": {str(k): v for k, v in sorted(result.levels.items())},
                "catalog": str(result.catalog_path),
                "manifest": str(result.manifest_path),
            },
            indent=1,
        )
    )
    if result.excluded:
        print(
            f"NOTE: {result.excluded} item(s) excluded; "
            "see the build manifest ledger for exact reasons",
            file=sys.stderr,
        )
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_build(args.build_root)
    summary = report.summary()
    print(json.dumps(summary, indent=1))
    return 0 if not summary["failed"] and summary["total"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="piano-core-learn")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_parser = sub.add_parser("ingest", help="build the Learn Library")
    ingest_parser.add_argument("sources_root", help="downloaded sources root")
    ingest_parser.add_argument("out_root", help="build output root (outside Git)")
    ingest_parser.add_argument(
        "--upstream-commit",
        default=None,
        help="pinned upstream commit recorded into provenance",
    )
    ingest_parser.set_defaults(func=_cmd_ingest)

    validate_parser = sub.add_parser(
        "validate", help="validate a build through the real package importer"
    )
    validate_parser.add_argument("build_root", help="build output root to validate")
    validate_parser.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (LearnError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
