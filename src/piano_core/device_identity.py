"""Persistent appliance device identity (PIANO-PROD-001).

The appliance owns exactly one stable identity: a random UUIDv4 stored
as a strictly-validated JSON record at
``<directory>/device.json`` (production:
``/var/lib/piano-core/identity/device.json``). The architecture is
locked by the PIANO-PROD-001 specification and
``docs/decisions/ADR-DEVICE-IDENTITY-V1.md``:

- **Creation and loading are strictly separated.** The UUID is
  generated ONLY by explicit initialization
  (``piano-core-identity initialize --directory <dir>``). Normal
  application startup calls :func:`load_identity`, which reads and
  validates and NEVER writes, creates, repairs, or regenerates. A
  corrupted record stays unavailable until an operator replaces it —
  silent regeneration would defeat the identity's purpose.
- **Publication is atomic and exclusive.** Initialization validates
  the storage, takes a nonblocking exclusive ``flock`` on
  ``.initialize.lock``, serializes the record to a unique temporary
  file in the SAME directory, flushes + ``fsync``s it, publishes it
  with a NO-OVERWRITE hard link, and ``fsync``s the directory. A
  losing contender gets ``identity_busy`` and may retry; the winner's
  identity is then returned to everyone.
- **Storage is validated, never repaired.** The directory and the
  record must be owned by the current effective user, the directory
  mode must be ``0700`` and the record/lock modes ``0600``; symlinks
  and non-regular objects are rejected. This module never recursively
  chmods/chowns, never creates or repairs system directories, and
  never falls back to ``$HOME``, ``/tmp``, the repository, or
  ``/opt``.
- **Bounded stable reason codes.** Every failure carries one of the
  documented reasons (see :class:`IdentityError`); raw file contents
  and sensitive paths never surface through this API.

The derived display code (``"PC-" + uuid.hex[:12].upper()``) is a
presentation convenience only; the full UUID remains the device
identity. The UUID is a public device identifier, NOT an
authentication secret.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import secrets
import stat
import uuid
from dataclasses import dataclass

__all__ = [
    "DEVICE_IDENTITY_SCHEMA",
    "DISPLAY_CODE_PREFIX",
    "IDENTITY_DIRECTORY_MODE",
    "IDENTITY_RECORD_MODE",
    "LOCK_NAME",
    "MAX_RECORD_BYTES",
    "RECORD_NAME",
    "DeviceIdentity",
    "IdentityBusyError",
    "IdentityError",
    "initialize_identity",
    "load_identity",
    "parse_identity_record",
    "serialize_identity_record",
    "validate_directory",
]

#: The only supported identity record schema version.
DEVICE_IDENTITY_SCHEMA = 1

#: Record file name inside the identity directory.
RECORD_NAME = "device.json"

#: Initialization lock file name inside the identity directory (never
#: created, replaced, or removed by any other code path).
LOCK_NAME = ".initialize.lock"

#: Hard upper bound for one record read (bytes).
MAX_RECORD_BYTES = 4096

#: Production storage contract (see docs/operations/DEVICE_IDENTITY.md):
#: directory 0700, record and lock 0600, all owned by the service user.
IDENTITY_DIRECTORY_MODE = 0o700
IDENTITY_RECORD_MODE = 0o600

#: Prefix of the derived display code.
DISPLAY_CODE_PREFIX = "PC-"

#: Bounded attempts to find a unique temporary basename (review R1):
#: retries happen ONLY on random-name collision (O_EXCL), never on
#: failure and never with sleeps.
_TEMP_NAME_ATTEMPTS = 8

# ----------------------------------------------------------------------
# Bounded stable reason codes (never raw contents, never paths)
# ----------------------------------------------------------------------
REASON_DIRECTORY_MISSING = "identity_directory_missing"
REASON_STORAGE_UNAVAILABLE = "identity_storage_unavailable"
REASON_MISSING = "identity_missing"
REASON_INVALID_RECORD = "identity_invalid_record"
REASON_SCHEMA_UNSUPPORTED = "identity_schema_unsupported"
REASON_UNSAFE_STORAGE = "identity_unsafe_storage"
REASON_BUSY = "identity_busy"
REASON_CONFLICT = "identity_conflict"

_REASON_MESSAGES = {
    REASON_DIRECTORY_MISSING: "identity directory is missing",
    REASON_STORAGE_UNAVAILABLE: "identity storage is unavailable",
    REASON_MISSING: "device identity record is missing",
    REASON_INVALID_RECORD: "device identity record is invalid",
    REASON_SCHEMA_UNSUPPORTED: "device identity record schema is unsupported",
    REASON_UNSAFE_STORAGE: "identity storage is unsafe",
    REASON_BUSY: "identity initialization is busy",
    REASON_CONFLICT: "device identity conflict during initialization",
}


class IdentityError(Exception):
    """One bounded identity failure with a stable machine-readable reason.

    The public message is a fixed phrase per reason — raw file contents
    and filesystem paths never reach this exception.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(_REASON_MESSAGES.get(reason, reason))


class IdentityBusyError(IdentityError):
    """Another initialization currently owns the identity lock."""

    def __init__(self) -> None:
        super().__init__(REASON_BUSY)


# ----------------------------------------------------------------------
# DeviceIdentity model
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class DeviceIdentity:
    """Immutable, validated appliance identity.

    Constructing an invalid identity is impossible: ``identity_schema``
    must be the integer ``1`` (booleans are rejected even though
    ``bool`` subclasses ``int``) and ``device_id`` must be the canonical
    lowercase hyphenated RFC-4122 UUIDv4 form.
    """

    identity_schema: int
    device_id: str

    def __post_init__(self) -> None:
        if isinstance(self.identity_schema, bool) or not isinstance(
            self.identity_schema, int
        ):
            raise IdentityError(REASON_INVALID_RECORD)
        if self.identity_schema != DEVICE_IDENTITY_SCHEMA:
            raise IdentityError(REASON_SCHEMA_UNSUPPORTED)
        if not isinstance(self.device_id, str):
            raise IdentityError(REASON_INVALID_RECORD)
        _validate_canonical_uuid4(self.device_id)

    @property
    def display_code(self) -> str:
        """Short human presentation code derived ONLY from the UUID.

        Example: ``PC-A7F32C91D2E4``. Presentation convenience; the full
        UUID remains the authoritative device identity.
        """
        return DISPLAY_CODE_PREFIX + self.device_id.replace("-", "")[:12].upper()


def _validate_canonical_uuid4(value: str) -> None:
    """Require the exact canonical lowercase hyphenated UUIDv4 form.

    ``uuid.UUID`` parsing is used only as a checker, never as a
    normalizer: the round-trip comparison below rejects uppercase,
    braced/URN spellings and any other non-canonical representation
    instead of silently rewriting them, and the explicit version/variant
    checks reject wrong-version or wrong-variant bits.
    """
    if not isinstance(value, str) or len(value) != 36:
        raise IdentityError(REASON_INVALID_RECORD)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise IdentityError(REASON_INVALID_RECORD) from None
    if parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise IdentityError(REASON_INVALID_RECORD)
    if str(parsed) != value:
        raise IdentityError(REASON_INVALID_RECORD)


# ----------------------------------------------------------------------
# Strict record parsing / serialization
# ----------------------------------------------------------------------
def parse_identity_record(data: bytes) -> DeviceIdentity:
    """Parse and strictly validate one serialized identity record.

    Accepts ONLY a UTF-8 JSON object with exactly the fields
    ``identity_schema`` (integer 1; booleans/floats/strings rejected)
    and ``device_id`` (canonical UUIDv4 string), without duplicate
    keys, without NaN/Infinity constants, and without trailing
    non-whitespace data, and at most :data:`MAX_RECORD_BYTES` bytes.
    Anything else raises a bounded :class:`IdentityError`.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise IdentityError(REASON_INVALID_RECORD)
    if len(data) > MAX_RECORD_BYTES:
        raise IdentityError(REASON_INVALID_RECORD)
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        raise IdentityError(REASON_INVALID_RECORD) from None

    def _object_pairs(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen:
                raise _DuplicateKeyError()
            seen.add(key)
        return dict(pairs)

    def _reject_constant(name):  # noqa: ARG001 (json API)
        raise _InvalidConstantError(name)

    try:
        record = json.loads(
            text, object_pairs_hook=_object_pairs, parse_constant=_reject_constant
        )
    except (ValueError, TypeError):
        # JSONDecodeError, duplicate keys, NaN/Infinity, trailing data.
        raise IdentityError(REASON_INVALID_RECORD) from None

    if not isinstance(record, dict):
        raise IdentityError(REASON_INVALID_RECORD)
    if set(record) != {"identity_schema", "device_id"}:
        raise IdentityError(REASON_INVALID_RECORD)

    schema = record["identity_schema"]
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise IdentityError(REASON_INVALID_RECORD)
    if schema != DEVICE_IDENTITY_SCHEMA:
        raise IdentityError(REASON_SCHEMA_UNSUPPORTED)

    return DeviceIdentity(
        identity_schema=schema, device_id=record["device_id"]
    )


class _DuplicateKeyError(ValueError):
    """Internal: a JSON object contained the same key twice."""


class _InvalidConstantError(ValueError):
    """Internal: the JSON document used NaN/Infinity constants."""


def serialize_identity_record(identity: DeviceIdentity) -> bytes:
    """Serialize the identity to the exact canonical record bytes."""
    return json.dumps(
        {"identity_schema": identity.identity_schema, "device_id": identity.device_id},
        separators=(",", ":"),
    ).encode("utf-8")


# ----------------------------------------------------------------------
# Storage security primitives
# ----------------------------------------------------------------------
def validate_directory(directory: object) -> str:
    """Canonical identity-directory argument contract (review R7).

    ONE validation surface shared by the module operations and the CLI:
    a non-empty absolute string, without NUL and without ``..``
    components. Raises :class:`ValueError` for caller/argument errors
    (never a storage condition); the CLI translates this into its
    stable exit-2 argument error, so raw ValueErrors can never leak as
    tracebacks. Structural only — storage security is validated later
    against the opened descriptor.
    """
    if not isinstance(directory, str) or not directory:
        raise ValueError("directory must be a non-empty string")
    if not os.path.isabs(directory):
        raise ValueError("directory must be an absolute path")
    if "\0" in directory:
        raise ValueError("directory must not contain NUL")
    if any(component == ".." for component in directory.split("/")):
        raise ValueError("directory must not contain '..' components")
    return directory


def _current_user_owns(st: os.stat_result) -> bool:
    return st.st_uid == os.geteuid()


def _current_group_owns(st: os.stat_result) -> bool:
    return st.st_gid == os.getegid()


def _validate_directory_stat(st: os.stat_result) -> None:
    """The identity directory must be a directory owned by us (UID+GID),
    mode exactly 0700."""
    if not stat.S_ISDIR(st.st_mode):
        raise IdentityError(REASON_UNSAFE_STORAGE)
    if not _current_user_owns(st) or not _current_group_owns(st):
        raise IdentityError(REASON_UNSAFE_STORAGE)
    if stat.S_IMODE(st.st_mode) != IDENTITY_DIRECTORY_MODE:
        raise IdentityError(REASON_UNSAFE_STORAGE)


def _open_identity_directory(directory: str) -> int:
    """Securely open the identity directory and return the validated fd.

    The directory itself is DESCRIPTOR-AUTHORITATIVE (review R1): every
    path component is opened ``O_DIRECTORY|O_NOFOLLOW`` relative to the
    previously opened descriptor — there is NO lstat()-then-open()
    window and no way for a symlink component (including the
    trailing-slash form that plain ``open()`` would dereference) to
    enter the traversal. The final descriptor is immediately
    ``fstat()``ed and validated (type, UID, GID, exact mode 0700), and
    ALL subsequent operations — record access AND directory fsync —
    use this same descriptor. Storage is never accepted merely because
    a pathname check passed.
    """
    components = [
        component
        for component in directory.split("/")
        if component and component != "."
    ]
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None
    try:
        for component in components:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
    except FileNotFoundError:
        os.close(fd)
        raise IdentityError(REASON_DIRECTORY_MISSING) from None
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            # Symlink traversal (incl. trailing-slash forms) or a
            # non-directory object in the path.
            raise IdentityError(REASON_UNSAFE_STORAGE) from None
        raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None
    try:
        _validate_directory_stat(os.fstat(fd))
    except Exception:
        os.close(fd)
        raise
    return fd


def _validate_record_stat(st: os.stat_result) -> None:
    """The record must be a regular file owned by us (UID+GID), mode
    exactly 0600."""
    if not stat.S_ISREG(st.st_mode):
        raise IdentityError(REASON_UNSAFE_STORAGE)
    if not _current_user_owns(st) or not _current_group_owns(st):
        raise IdentityError(REASON_UNSAFE_STORAGE)
    if stat.S_IMODE(st.st_mode) != IDENTITY_RECORD_MODE:
        raise IdentityError(REASON_UNSAFE_STORAGE)


def _read_record_bytes(dir_fd: int) -> bytes:
    """Read the record through the directory fd without following symlinks.

    The final object is re-validated via ``fstat`` after opening
    (``O_NOFOLLOW`` + lstat-style check + regular-file check), so
    symlink/FIFO/device substitutions fail closed, and the read is
    bounded at :data:`MAX_RECORD_BYTES` + 1 to detect oversize records.
    """
    try:
        st = os.stat(RECORD_NAME, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise IdentityError(REASON_MISSING) from None
    except OSError:
        raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None
    _validate_record_stat(st)
    try:
        fd = os.open(
            RECORD_NAME,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=dir_fd,
        )
    except FileNotFoundError:
        raise IdentityError(REASON_MISSING) from None
    except OSError:
        raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None
    try:
        _validate_record_stat(os.fstat(fd))
        chunks = []
        remaining = MAX_RECORD_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None
    finally:
        os.close(fd)
    return b"".join(chunks)


def _load_from_directory_fd(dir_fd: int) -> DeviceIdentity:
    """Read + parse the record behind an already-validated directory fd."""
    return parse_identity_record(_read_record_bytes(dir_fd))


def _open_lock_file(dir_fd: int) -> int:
    """Securely create/open ``.initialize.lock`` and return its fd.

    Created and pre-existing locks are treated DIFFERENTLY (review R2) —
    there is no automatic repair of existing storage:

    - newly CREATED lock: the mode is explicitly set to 0600 (a fresh
      file may inherit a degraded umask mode) and verified via
      ``fstat``;
    - PRE-EXISTING lock: must ALREADY be a regular file owned by us
      (UID+GID) with mode EXACTLY 0600 — anything else fails with
      ``identity_unsafe_storage`` and is never chmod'd/chown'd.

    Symlink substitution fails closed via ``O_NOFOLLOW``. The lock file
    is NEVER removed here.
    """
    try:
        fd = os.open(
            LOCK_NAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            IDENTITY_RECORD_MODE,
            dir_fd=dir_fd,
        )
    except FileExistsError:
        # R6: open, validation, and cleanup are separated with an
        # fd=None pattern — a FAILED open (symlink ELOOP, directory
        # EISDIR, …) must surface as a controlled identity error, never
        # as an UnboundLocalError from closing an unassigned descriptor.
        fd = None
        try:
            fd = os.open(
                LOCK_NAME,
                os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=dir_fd,
            )
            # Existing lock: validate ONLY — never repair.
            _validate_record_stat(os.fstat(fd))
            return fd
        except IdentityError:
            if fd is not None:
                os.close(fd)
            raise
        except OSError:
            if fd is not None:
                os.close(fd)
            raise IdentityError(REASON_UNSAFE_STORAGE) from None
    except OSError:
        raise IdentityError(REASON_UNSAFE_STORAGE) from None
    try:
        # Newly created lock: enforce the deterministic secure mode
        # regardless of the process umask, then verify the descriptor.
        os.fchmod(fd, IDENTITY_RECORD_MODE)
        _validate_record_stat(os.fstat(fd))
    except IdentityError:
        os.close(fd)
        raise
    except OSError:
        os.close(fd)
        raise IdentityError(REASON_UNSAFE_STORAGE) from None
    return fd


def _fsync_directory_fd(dir_fd: int) -> None:
    """Flush a directory entry change to stable storage.

    Uses the ALREADY-VALIDATED directory descriptor (review R1) — the
    directory is never re-opened by path, so no validation window can
    be reintroduced at fsync time.
    """
    try:
        os.fsync(dir_fd)
    except OSError:
        raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None


# ----------------------------------------------------------------------
# Public operations
# ----------------------------------------------------------------------
def load_identity(directory: str) -> DeviceIdentity:
    """Load and validate the persisted identity. NEVER writes.

    This is the ONLY identity operation the normal application startup
    performs. Missing directory -> ``identity_directory_missing``; a
    missing record -> ``identity_missing``; unsafe storage ->
    ``identity_unsafe_storage``; unparsable/corrupt records ->
    ``identity_invalid_record`` / ``identity_schema_unsupported``; an
    unusable storage backend -> ``identity_storage_unavailable``.
    """
    directory = validate_directory(directory)
    dir_fd = _open_identity_directory(directory)
    try:
        return _load_from_directory_fd(dir_fd)
    finally:
        os.close(dir_fd)


def initialize_identity(directory: str) -> DeviceIdentity:
    """Create the identity exactly once; otherwise return the existing one.

    Explicit-initialization-only entry point (the
    ``piano-core-identity`` CLI): normal application startup MUST NOT
    call this. Sequence: validate storage security; return an existing
    valid record unchanged (a CORRUPT existing record is an error, never
    regenerated); securely create/open ``.initialize.lock``; take a
    NONBLOCKING exclusive ``flock`` (a losing contender gets
    ``identity_busy`` — never an indefinite wait); recheck the record;
    generate exactly one UUIDv4; write it to a unique temporary file in
    the same directory, flush + ``fsync``; publish with a NO-OVERWRITE
    hard link (an unexpected existing record is ``identity_conflict``);
    ``fsync`` the directory; remove only this invocation's temporary
    file; ``fsync`` the directory again; finally read the published
    record back through the validated descriptor and require it to
    equal the generated identity — success is never reported for an
    identity that is not loadable under the same storage contract. The
    lock is released in ``finally`` and never unlinked; cleanup never
    removes a published record.
    """
    directory = validate_directory(directory)
    dir_fd = _open_identity_directory(directory)
    temp_name: str | None = None
    lock_fd: int | None = None
    try:
        # Fast path (also the retry-after-contention path): an existing
        # valid record is returned unchanged. A corrupt record raises —
        # corrupted identities are NEVER silently regenerated.
        try:
            return _load_from_directory_fd(dir_fd)
        except IdentityError as exc:
            if exc.reason != REASON_MISSING:
                raise

        lock_fd = _open_lock_file(dir_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise IdentityBusyError() from None

        # Recheck under the lock: the winner of a previous contention
        # already published the identity.
        try:
            return _load_from_directory_fd(dir_fd)
        except IdentityError as exc:
            if exc.reason != REASON_MISSING:
                raise

        identity = DeviceIdentity(
            identity_schema=DEVICE_IDENTITY_SCHEMA, device_id=str(uuid.uuid4())
        )
        payload = serialize_identity_record(identity)

        # Unique temporary file created INSIDE the validated directory
        # authority (review R1): descriptor-relative open on the SAME
        # validated dir_fd — the directory PATH is never re-resolved, so
        # a pathname swap after validation cannot move the temp file (or
        # anything downstream) out of the validated directory. Unique
        # basename with bounded retry ONLY on random-name collision (no
        # sleeps). The file gets a DETERMINISTIC secure final mode
        # independent of the process umask (review R4): mode is set and
        # the descriptor verified (regular file, UID, GID, exact 0600)
        # BEFORE fsync and publication.
        tmp_fd = None
        try:
            temp_name = None
            for _ in range(_TEMP_NAME_ATTEMPTS):
                candidate = f".device-{secrets.token_hex(16)}.tmp"
                try:
                    tmp_fd = os.open(
                        candidate,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | os.O_NOFOLLOW
                        | os.O_CLOEXEC,
                        IDENTITY_RECORD_MODE,
                        dir_fd=dir_fd,
                    )
                except FileExistsError:
                    continue  # random basename collision: bounded retry
                except OSError:
                    raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None
                temp_name = candidate
                break
            if tmp_fd is None:
                raise IdentityError(REASON_STORAGE_UNAVAILABLE)
        except IdentityError:
            temp_name = None
            raise
        try:
            os.fchmod(tmp_fd, IDENTITY_RECORD_MODE)
            _validate_record_stat(os.fstat(tmp_fd))
        except IdentityError:
            os.close(tmp_fd)
            raise
        except OSError:
            os.close(tmp_fd)
            raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None
        try:
            with os.fdopen(tmp_fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None

        # Publish with a no-overwrite hard link (never rename-over).
        try:
            os.link(
                temp_name,
                RECORD_NAME,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            # A record appeared outside this locking discipline; never
            # overwrite it.
            raise IdentityError(REASON_CONFLICT) from None
        except OSError:
            raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None

        # From here the record is published: even if durability (fsync)
        # fails, cleanup must NOT remove it.
        _fsync_directory_fd(dir_fd)
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        except OSError:
            raise IdentityError(REASON_STORAGE_UNAVAILABLE) from None
        temp_name = None
        _fsync_directory_fd(dir_fd)
        # Success requires the PUBLISHED identity to be loadable under
        # the same storage contract (review R4): read it back through
        # the validated descriptor and require it to equal the identity
        # this invocation generated.
        if _load_from_directory_fd(dir_fd) != identity:
            raise IdentityError(REASON_STORAGE_UNAVAILABLE)
        return identity
    finally:
        if temp_name is not None:
            # Remove ONLY this invocation's temporary path; never the
            # published record, never the lock.
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        os.close(dir_fd)
