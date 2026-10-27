"""``piano-core-identity``: explicit device-identity initialization CLI.

This is the ONLY supported way to create a device identity — normal
application startup never generates one. There is deliberately no
reset, repair, replacement, or import command: the identity exists to
be stable for the lifetime of the appliance.

Usage::

    piano-core-identity initialize --directory <absolute-directory>

Behavior:

- an existing VALID identity is returned unchanged (idempotent);
- exit code ``0`` — identity created or existing valid identity; the
  public identity descriptor (product/device_id/display_code/
  identity_schema) is the only stdout output;
- exit code ``2`` — invalid arguments, invalid/corrupt identity, or a
  storage error; a stable reason code goes to stderr, never a
  traceback and never the descriptor;
- exit code ``3`` — the initialization lock is held by another
  initializer (``identity_busy``); retry once that initializer
  finishes.
"""

from __future__ import annotations

import argparse
import json
import sys

from piano_core.api.serialization import device_identity_payload
from piano_core.device_identity import (
    IdentityBusyError,
    IdentityError,
    initialize_identity,
    validate_directory,
)

PROG = "piano-core-identity"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Piano Core device identity: create the persistent appliance "
            "identity exactly once (never run while the service manages "
            "the same directory concurrently; retry on identity_busy)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser(
        "initialize",
        help="create the device identity if absent; return the existing one",
    )
    p_init.add_argument(
        "--directory",
        required=True,
        metavar="DIR",
        help="absolute identity directory (production: "
        "/var/lib/piano-core/identity); the directory must already "
        "exist, be owned by the current user, and have mode 0700",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Console entrypoint. Returns a process exit code (0 / 2 / 3)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # R7: ONE canonical argument contract — the module's
    # validate_directory(). Any explicitly invalid identity-directory
    # syntax (relative, '..', NUL, non-string) is translated into the
    # stable exit-2 argument error with NO traceback; a defensive
    # ValueError guard below keeps the no-traceback guarantee even if
    # the module contract ever raises later in the call chain.
    try:
        directory = validate_directory(args.directory)
    except ValueError as error:
        print(f"{PROG}: invalid directory: {error}", file=sys.stderr)
        return 2

    try:
        identity = initialize_identity(directory)
    except IdentityBusyError:
        print("identity_busy", file=sys.stderr)
        return 3
    except IdentityError as error:
        print(error.reason, file=sys.stderr)
        return 2
    except ValueError as error:  # defensive: argument contract, not traceback
        print(f"{PROG}: invalid directory: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            device_identity_payload(identity),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - console-script entry
    sys.exit(main())
