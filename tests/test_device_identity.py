"""PIANO-PROD-001 tests: device identity model, persistence, storage
security, CLI, configuration, and packaging/rollback contract."""

import dataclasses
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from piano_core import config as config_module
from piano_core.config import AppConfig, ConfigError
from piano_core.device_identity import (
    DEVICE_IDENTITY_SCHEMA,
    IDENTITY_DIRECTORY_MODE,
    IDENTITY_RECORD_MODE,
    LOCK_NAME,
    MAX_RECORD_BYTES,
    RECORD_NAME,
    DeviceIdentity,
    IdentityBusyError,
    IdentityError,
    initialize_identity,
    load_identity,
    parse_identity_record,
    serialize_identity_record,
    _open_identity_directory,
    _read_record_bytes,
    _validate_directory_stat,
    _validate_record_stat,
)
from piano_core.identity_cli import main as cli_main

_REPO_ROOT = Path(__file__).resolve().parent.parent

_VALID_UUID = "a7f32c91-d2e4-4abc-9def-0123456789ab"


def _record_bytes(device_id: str = _VALID_UUID, schema: int = 1) -> bytes:
    return json.dumps({"identity_schema": schema, "device_id": device_id}).encode()


def _make_identity_dir(tmp_path: Path) -> str:
    directory = tmp_path / "identity"
    directory.mkdir()
    os.chmod(directory, IDENTITY_DIRECTORY_MODE)
    return str(directory)


def _write_record(directory: str, data: bytes) -> None:
    fd = os.open(
        os.path.join(directory, RECORD_NAME),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        IDENTITY_RECORD_MODE,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(os.path.join(directory, RECORD_NAME), IDENTITY_RECORD_MODE)


def _snapshot(directory: str) -> dict:
    state = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        st = os.lstat(path)
        if stat.S_ISREG(st.st_mode):
            with open(path, "rb") as handle:
                data = handle.read()
        else:
            data = b""
        state[name] = (data, st.st_mode, st.st_mtime_ns, st.st_size)
    return state


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(args)
    return code, out.getvalue(), err.getvalue()


# ----------------------------------------------------------------------
# DeviceIdentity model
# ----------------------------------------------------------------------
class DeviceIdentityModelTests(unittest.TestCase):
    def test_valid_identity_roundtrip(self):
        identity = DeviceIdentity(identity_schema=1, device_id=_VALID_UUID)
        self.assertEqual(identity.identity_schema, 1)
        self.assertEqual(identity.device_id, _VALID_UUID)

    def test_display_code_is_derived_from_uuid(self):
        identity = DeviceIdentity(identity_schema=1, device_id=_VALID_UUID)
        self.assertEqual(identity.display_code, "PC-A7F32C91D2E4")

    def test_identity_is_immutable(self):
        identity = DeviceIdentity(identity_schema=1, device_id=_VALID_UUID)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            identity.device_id = str(uuid.uuid4())  # type: ignore[misc]

    def test_schema_bool_rejected(self):
        with self.assertRaises(IdentityError):
            DeviceIdentity(identity_schema=True, device_id=_VALID_UUID)

    def test_schema_float_and_string_rejected(self):
        for bad in (1.0, "1", None):
            with self.assertRaises(IdentityError):
                DeviceIdentity(identity_schema=bad, device_id=_VALID_UUID)  # type: ignore[arg-type]

    def test_schema_other_integer_is_unsupported(self):
        with self.assertRaises(IdentityError) as ctx:
            DeviceIdentity(identity_schema=2, device_id=_VALID_UUID)
        self.assertEqual(ctx.exception.reason, "identity_schema_unsupported")

    def test_noncanonical_uuid_rejected_and_never_rewritten(self):
        for bad in (
            _VALID_UUID.upper(),
            "{" + _VALID_UUID + "}",
            "urn:uuid:" + _VALID_UUID,
            _VALID_UUID[:-1],
            _VALID_UUID + "0",
            "a7f32c91d2e44abc9def0123456789ab",
            "a7f32c91-d2e4-4abc-9def-0123456789ab ",
            12345,
        ):
            with self.assertRaises(IdentityError):
                DeviceIdentity(identity_schema=1, device_id=bad)  # type: ignore[arg-type]

    def test_wrong_uuid_version_rejected(self):
        version5 = _VALID_UUID[:14] + "5" + _VALID_UUID[15:]
        with self.assertRaises(IdentityError):
            DeviceIdentity(identity_schema=1, device_id=version5)

    def test_wrong_uuid_variant_rejected(self):
        microsoft = _VALID_UUID[:19] + "c" + _VALID_UUID[20:]
        with self.assertRaises(IdentityError):
            DeviceIdentity(identity_schema=1, device_id=microsoft)

    def test_version_bits_are_never_silently_rewritten(self):
        """Parsing is a checker only: a v1 UUID string stays rejected even
        though uuid.UUID could normalize the object."""
        version1 = _VALID_UUID[:14] + "1" + _VALID_UUID[15:]
        with self.assertRaises(IdentityError):
            DeviceIdentity(identity_schema=1, device_id=version1)


# ----------------------------------------------------------------------
# Strict record parsing
# ----------------------------------------------------------------------
class ParseRecordTests(unittest.TestCase):
    def test_valid_record(self):
        identity = parse_identity_record(_record_bytes())
        self.assertEqual(identity.device_id, _VALID_UUID)
        self.assertEqual(identity.identity_schema, DEVICE_IDENTITY_SCHEMA)

    def test_oversize_record_rejected(self):
        padded = _record_bytes() + b" " * (MAX_RECORD_BYTES + 1)
        self.assertGreater(len(padded), MAX_RECORD_BYTES)
        with self.assertRaises(IdentityError) as ctx:
            parse_identity_record(padded)
        self.assertEqual(ctx.exception.reason, "identity_invalid_record")

    def test_exact_limit_record_accepted(self):
        padded = _record_bytes() + b" " * (MAX_RECORD_BYTES - len(_record_bytes()))
        self.assertEqual(len(padded), MAX_RECORD_BYTES)
        self.assertEqual(parse_identity_record(padded).device_id, _VALID_UUID)

    def test_invalid_utf8_rejected(self):
        with self.assertRaises(IdentityError):
            parse_identity_record(b"\xff\xfe\x00")

    def test_empty_and_truncated_rejected(self):
        for bad in (b"", b"{", b'{"identity_schema": 1', b'{"device_id":'):
            with self.assertRaises(IdentityError):
                parse_identity_record(bad)

    def test_trailing_non_whitespace_rejected(self):
        with self.assertRaises(IdentityError):
            parse_identity_record(_record_bytes() + b" x")

    def test_trailing_whitespace_tolerated(self):
        identity = parse_identity_record(_record_bytes() + b"\n  ")
        self.assertEqual(identity.device_id, _VALID_UUID)

    def test_array_and_scalar_top_level_rejected(self):
        for bad in (b"[1, 2]", b'"text"', b"42", b"null"):
            with self.assertRaises(IdentityError):
                parse_identity_record(bad)

    def test_duplicate_keys_rejected(self):
        duplicate = (
            b'{"identity_schema": 1, "device_id": "' + _VALID_UUID.encode()
            + b'", "device_id": "' + _VALID_UUID.encode() + b'"}'
        )
        with self.assertRaises(IdentityError):
            parse_identity_record(duplicate)

    def test_invalid_json_constant_rejected(self):
        with self.assertRaises(IdentityError):
            parse_identity_record(
                b'{"identity_schema": NaN, "device_id": "'
                + _VALID_UUID.encode()
                + b'"}'
            )

    def test_extra_field_rejected(self):
        extra = json.dumps(
            {
                "identity_schema": 1,
                "device_id": _VALID_UUID,
                "display_code": "PC-A7F32C91D2E4",
            }
        ).encode()
        with self.assertRaises(IdentityError):
            parse_identity_record(extra)

    def test_missing_field_rejected(self):
        with self.assertRaises(IdentityError):
            parse_identity_record(json.dumps({"identity_schema": 1}).encode())

    def test_schema_bool_float_string_rejected(self):
        for bad in ("true", "1.0", '"1"'):
            record = json.dumps(
                {"identity_schema": json.loads(bad), "device_id": _VALID_UUID}
            ).encode()
            with self.assertRaises(IdentityError):
                parse_identity_record(record)

    def test_schema_other_integer_unsupported(self):
        with self.assertRaises(IdentityError) as ctx:
            parse_identity_record(_record_bytes(schema=2))
        self.assertEqual(ctx.exception.reason, "identity_schema_unsupported")

    def test_noncanonical_device_id_rejected(self):
        with self.assertRaises(IdentityError):
            parse_identity_record(_record_bytes(_VALID_UUID.upper()))


class SerializeRecordTests(unittest.TestCase):
    def test_exact_serialization_shape(self):
        identity = DeviceIdentity(identity_schema=1, device_id=_VALID_UUID)
        self.assertEqual(
            serialize_identity_record(identity),
            b'{"identity_schema":1,"device_id":"'
            + _VALID_UUID.encode()
            + b'"}',
        )
        self.assertEqual(
            parse_identity_record(serialize_identity_record(identity)), identity
        )


# ----------------------------------------------------------------------
# load_identity
# ----------------------------------------------------------------------
class LoadIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = _make_identity_dir(Path(self.tmp.name))

    def test_missing_directory(self):
        with self.assertRaises(IdentityError) as ctx:
            load_identity(os.path.join(self.tmp.name, "absent"))
        self.assertEqual(ctx.exception.reason, "identity_directory_missing")

    def test_missing_record(self):
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_missing")

    def test_valid_load(self):
        _write_record(self.directory, _record_bytes())
        identity = load_identity(self.directory)
        self.assertEqual(identity.device_id, _VALID_UUID)

    def test_load_is_read_only(self):
        _write_record(self.directory, _record_bytes())
        before = _snapshot(self.directory)
        load_identity(self.directory)
        self.assertEqual(_snapshot(self.directory), before)

    def test_load_never_creates_lock_or_temp(self):
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_missing")
        self.assertEqual(os.listdir(self.directory), [])

    def test_corrupt_record_rejected(self):
        _write_record(self.directory, b"{corrupt")
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_invalid_record")

    def test_unsupported_schema_rejected(self):
        _write_record(self.directory, _record_bytes(schema=7))
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_schema_unsupported")

    def test_symlinked_record_rejected(self):
        target = os.path.join(self.tmp.name, "elsewhere.json")
        with open(target, "wb") as handle:
            handle.write(_record_bytes())
        os.symlink(target, os.path.join(self.directory, RECORD_NAME))
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_symlinked_directory_rejected(self):
        os.symlink(self.directory, os.path.join(self.tmp.name, "link"))
        with self.assertRaises(IdentityError) as ctx:
            load_identity(os.path.join(self.tmp.name, "link"))
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_fifo_record_rejected(self):
        os.mkfifo(os.path.join(self.directory, RECORD_NAME))
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_directory_object_record_rejected(self):
        os.mkdir(os.path.join(self.directory, RECORD_NAME))
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_wrong_directory_mode_rejected(self):
        os.chmod(self.directory, 0o755)
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_wrong_record_mode_rejected(self):
        _write_record(self.directory, _record_bytes())
        os.chmod(os.path.join(self.directory, RECORD_NAME), 0o644)
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_foreign_owner_rejected(self):
        _write_record(self.directory, _record_bytes())
        with mock.patch("os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(IdentityError) as ctx:
                load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_non_directory_path_rejected(self):
        plain = os.path.join(self.tmp.name, "plain")
        with open(plain, "wb"):
            pass
        os.chmod(plain, IDENTITY_DIRECTORY_MODE)
        with self.assertRaises(IdentityError) as ctx:
            load_identity(plain)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_invalid_arguments_are_value_errors(self):
        for bad in ("", "relative/path", "/a/../b", None, 42):
            with self.assertRaises(ValueError):
                load_identity(bad)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# initialize_identity
# ----------------------------------------------------------------------
class InitializeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = _make_identity_dir(Path(self.tmp.name))

    def test_first_initialization(self):
        identity = initialize_identity(self.directory)
        self.assertEqual(identity.identity_schema, DEVICE_IDENTITY_SCHEMA)
        parsed = uuid.UUID(identity.device_id)
        self.assertEqual(parsed.version, 4)
        self.assertEqual(str(parsed), identity.device_id)
        self.assertEqual(
            Path(self.directory, RECORD_NAME).read_bytes(),
            serialize_identity_record(identity),
        )

    def test_exactly_one_uuid_generated(self):
        with mock.patch(
            "piano_core.device_identity.uuid.uuid4", wraps=uuid.uuid4
        ) as spy:
            initialize_identity(self.directory)
            self.assertEqual(spy.call_count, 1)

    def test_reload_identical(self):
        identity = initialize_identity(self.directory)
        self.assertEqual(load_identity(self.directory), identity)

    def test_idempotent_initialization(self):
        first = initialize_identity(self.directory)
        before = Path(self.directory, RECORD_NAME).read_bytes()
        second = initialize_identity(self.directory)
        self.assertEqual(first, second)
        self.assertEqual(Path(self.directory, RECORD_NAME).read_bytes(), before)

    def test_fresh_process_gets_same_identity(self):
        first = initialize_identity(self.directory)
        program = (
            "import sys; sys.path.insert(0, %r);"
            "from piano_core.device_identity import initialize_identity;"
            "print(initialize_identity(%r).device_id)"
            % (str(_REPO_ROOT / "src"), self.directory)
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), first.device_id)

    def test_record_mode_after_initialization(self):
        initialize_identity(self.directory)
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.join(self.directory, RECORD_NAME)).st_mode),
            IDENTITY_RECORD_MODE,
        )
        self.assertTrue(os.path.exists(os.path.join(self.directory, LOCK_NAME)))
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.join(self.directory, LOCK_NAME)).st_mode),
            IDENTITY_RECORD_MODE,
        )
        self.assertEqual(
            stat.S_IMODE(os.stat(self.directory).st_mode),
            IDENTITY_DIRECTORY_MODE,
        )

    def test_no_temp_files_left_after_success(self):
        initialize_identity(self.directory)
        self.assertEqual(
            sorted(os.listdir(self.directory)), [LOCK_NAME, RECORD_NAME]
        )

    def test_concurrent_initialization_exactly_one_winner(self):
        workers = 4
        barrier = threading.Barrier(workers)
        results = []

        def worker():
            barrier.wait(timeout=10)
            try:
                identity = initialize_identity(self.directory)
            except IdentityBusyError:
                results.append("busy")
            else:
                results.append(identity.device_id)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
        ids = {value for value in results if value != "busy"}
        self.assertLessEqual(len(ids), 1)
        self.assertEqual(len(results), workers)
        self.assertLessEqual(results.count("busy"), workers - 1)
        persisted = load_identity(self.directory)
        self.assertIn(persisted.device_id, ids)

    def test_retry_after_contention_returns_winner_identity(self):
        lock_fd = os.open(
            os.path.join(self.directory, LOCK_NAME),
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            IDENTITY_RECORD_MODE,
        )
        try:
            import fcntl

            os.fchmod(lock_fd, IDENTITY_RECORD_MODE)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            outcome = {}

            def contender():
                try:
                    initialize_identity(self.directory)
                    outcome["result"] = "unexpected-success"
                except IdentityBusyError:
                    outcome["result"] = "busy"

            thread = threading.Thread(target=contender)
            thread.start()
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outcome.get("result"), "busy")
        finally:
            os.close(lock_fd)
        winner = initialize_identity(self.directory)
        self.assertEqual(initialize_identity(self.directory), winner)

    def test_atomic_visibility_exact_publication(self):
        identity = initialize_identity(self.directory)
        self.assertEqual(
            Path(self.directory, RECORD_NAME).read_bytes(),
            serialize_identity_record(identity),
        )

    def test_interrupted_write_leftover_temp_is_ignored(self):
        stale = os.path.join(self.directory, ".device-stale.tmp")
        with open(stale, "wb") as handle:
            handle.write(b"partial garbage")
        os.chmod(stale, IDENTITY_RECORD_MODE)
        identity = initialize_identity(self.directory)
        self.assertEqual(load_identity(self.directory), identity)
        # Only THIS invocation's temporary path is removed; the stale
        # leftover from the interrupted write is never touched.
        self.assertTrue(os.path.exists(stale))

    def test_file_fsync_failure_publishes_nothing(self):
        real_fsync = os.fsync

        def failing_fsync(fd):
            raise OSError("injected fsync failure")

        with mock.patch("os.fsync", side_effect=failing_fsync):
            with self.assertRaises(IdentityError) as ctx:
                initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_storage_unavailable")
        self.assertFalse(os.path.exists(os.path.join(self.directory, RECORD_NAME)))
        self.assertNotIn(".device-", str(os.listdir(self.directory)))
        self.assertIs(real_fsync, os.fsync)

    def test_directory_fsync_failure_after_publication_keeps_record(self):
        real_fsync = os.fsync
        calls = {"count": 0}

        def fsync_then_fail(fd):
            calls["count"] += 1
            if calls["count"] > 1:  # first call = temp file, later = directory
                raise OSError("injected directory fsync failure")
            return real_fsync(fd)

        with mock.patch("os.fsync", side_effect=fsync_then_fail):
            with self.assertRaises(IdentityError) as ctx:
                initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_storage_unavailable")
        # The record is already published: cleanup must NOT remove it.
        self.assertTrue(os.path.exists(os.path.join(self.directory, RECORD_NAME)))
        persisted = load_identity(self.directory)
        retry = initialize_identity(self.directory)
        self.assertEqual(persisted, retry)

    def test_lock_is_never_removed(self):
        initialize_identity(self.directory)
        lock_path = os.path.join(self.directory, LOCK_NAME)
        self.assertTrue(os.path.exists(lock_path))
        initialize_identity(self.directory)
        self.assertTrue(os.path.exists(lock_path))

    def test_unwritable_directory_mode_fails_closed(self):
        os.chmod(self.directory, 0o500)
        with self.assertRaises(IdentityError) as ctx:
            initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")
        self.assertFalse(os.path.exists(os.path.join(self.directory, RECORD_NAME)))

    def test_publish_permission_failure_fails_closed(self):
        real_link = os.link

        def denied_link(*args, **kwargs):
            raise PermissionError("injected denied link")

        with mock.patch("os.link", side_effect=denied_link):
            with self.assertRaises(IdentityError) as ctx:
                initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_storage_unavailable")
        self.assertFalse(os.path.exists(os.path.join(self.directory, RECORD_NAME)))
        self.assertNotIn(".device-", str(os.listdir(self.directory)))
        self.assertIs(real_link, os.link)

    def test_existing_corrupt_record_is_never_regenerated(self):
        _write_record(self.directory, b"{corrupt")
        with self.assertRaises(IdentityError) as ctx:
            initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_invalid_record")
        self.assertEqual(
            Path(self.directory, RECORD_NAME).read_bytes(), b"{corrupt"
        )

    def test_existing_valid_record_returned_unchanged(self):
        _write_record(self.directory, _record_bytes())
        identity = initialize_identity(self.directory)
        self.assertEqual(identity.device_id, _VALID_UUID)
        self.assertEqual(
            Path(self.directory, RECORD_NAME).read_bytes(), _record_bytes()
        )

    def test_missing_directory_never_created(self):
        absent = os.path.join(self.tmp.name, "not-there")
        with self.assertRaises(IdentityError) as ctx:
            initialize_identity(absent)
        self.assertEqual(ctx.exception.reason, "identity_directory_missing")
        self.assertFalse(os.path.exists(absent))

    def test_readable_identity_does_not_require_write(self):
        identity = initialize_identity(self.directory)
        with mock.patch("os.open", wraps=os.open) as spy:
            self.assertEqual(load_identity(self.directory), identity)
            for call in spy.call_args_list:
                flags = call.args[1] if len(call.args) > 1 else call.kwargs.get("flags", 0)
                self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT), 0)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
class IdentityCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = _make_identity_dir(Path(self.tmp.name))

    def test_initialize_success_prints_descriptor_only(self):
        code, out, err = _run_cli(["initialize", "--directory", self.directory])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(
            set(payload),
            {"product", "device_id", "display_code", "identity_schema"},
        )
        self.assertEqual(payload["product"], "Piano Core")
        self.assertTrue(payload["display_code"].startswith("PC-"))

    def test_existing_valid_identity_exit_zero(self):
        initialize_identity(self.directory)
        code, out, err = _run_cli(["initialize", "--directory", self.directory])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        persisted = load_identity(self.directory)
        self.assertEqual(json.loads(out)["device_id"], persisted.device_id)

    def test_busy_exit_three_with_stable_reason(self):
        import fcntl

        lock_fd = os.open(
            os.path.join(self.directory, LOCK_NAME),
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            IDENTITY_RECORD_MODE,
        )
        try:
            os.fchmod(lock_fd, IDENTITY_RECORD_MODE)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, out, err = _run_cli(
                ["initialize", "--directory", self.directory]
            )
            self.assertEqual(code, 3)
            self.assertEqual(out, "")
            self.assertEqual(err.strip(), "identity_busy")
        finally:
            os.close(lock_fd)

    def test_corrupt_identity_exit_two(self):
        _write_record(self.directory, b"{corrupt")
        code, out, err = _run_cli(["initialize", "--directory", self.directory])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "identity_invalid_record")
        self.assertNotIn("Traceback", err)

    def test_relative_directory_exit_two(self):
        code, out, err = _run_cli(["initialize", "--directory", "relative/dir"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertNotIn("Traceback", err)

    def test_missing_subcommand_exit_two(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli_main([])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(out.getvalue(), "")

    def test_no_traceback_on_expected_failures(self):
        os.chmod(self.directory, 0o755)
        code, out, err = _run_cli(["initialize", "--directory", self.directory])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
class DeviceIdentityConfigTests(unittest.TestCase):
    def test_default_empty(self):
        self.assertEqual(AppConfig().device_identity_dir, "")

    def test_absolute_accepted(self):
        cfg = AppConfig.from_mapping(
            {"device_identity_dir": "/var/lib/piano-core/identity"}
        )
        self.assertEqual(cfg.device_identity_dir, "/var/lib/piano-core/identity")

    def test_relative_rejected(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"device_identity_dir": "identity"})

    def test_tilde_not_expanded_and_rejected(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"device_identity_dir": "~/identity"})

    def test_env_expansion_not_performed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"device_identity_dir": "$HOME/identity"})

    def test_nul_rejected(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"device_identity_dir": "/tmp/a\x00b"})

    def test_parent_traversal_rejected(self):
        for bad in ("/var/../piano", "/..", "/a/../b"):
            with self.assertRaises(ConfigError):
                AppConfig.from_mapping({"device_identity_dir": bad})

    def test_env_variable_accepted(self):
        cfg = AppConfig.from_env(
            {"PIANO_CORE_DEVICE_IDENTITY_DIR": "/var/lib/piano-core/identity"}
        )
        self.assertEqual(cfg.device_identity_dir, "/var/lib/piano-core/identity")

    def test_env_empty_is_unconfigured(self):
        cfg = AppConfig.from_env({"PIANO_CORE_DEVICE_IDENTITY_DIR": ""})
        self.assertEqual(cfg.device_identity_dir, "")

    def test_unknown_config_still_fails(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"device_identity_path": "/tmp/x"})

    def test_unknown_env_var_still_fails(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_env({"PIANO_CORE_DEVICE_IDENTITY_PATH": "/tmp/x"})

    def test_both_allowlists_updated(self):
        self.assertIn("device_identity_dir", config_module.KNOWN_KEYS)
        self.assertIn(
            "PIANO_CORE_DEVICE_IDENTITY_DIR", config_module.KNOWN_ENV_VARS
        )


# ----------------------------------------------------------------------
# Packaging / rollback contract
# ----------------------------------------------------------------------
_PRIOR_KNOWN_KEYS = frozenset(
    {
        "mode",
        "log_level",
        "midi_input_enabled",
        "midi_input_port_matcher",
        "midi_output_enabled",
        "midi_output_port_matcher",
        "library_root",
        "library_index_path",
        "library_enabled",
        "api_enabled",
        "api_host",
        "api_port",
        "ui_enabled",
        "led_enabled",
        "led_ws_host",
        "led_ws_port",
        "led_mode",
        "led_brightness",
    }
)


class PackagingRollbackTests(unittest.TestCase):
    def test_cli_entrypoint_registered(self):
        pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(
            'piano-core-identity = "piano_core.identity_cli:main"', pyproject
        )

    def test_package_contains_no_identity_artifacts(self):
        src = _REPO_ROOT / "src"
        forbidden = {"device.json", ".initialize.lock"}
        for path in src.rglob("*"):
            self.assertNotIn(path.name, forbidden)
            self.assertFalse(path.name.startswith(".device-"))

    def test_prior_release_parser_rejects_new_variable(self):
        """Older releases fail closed on unknown PIANO_CORE_* keys."""
        self.assertNotIn("device_identity_dir", _PRIOR_KNOWN_KEYS)
        suffix = "PIANO_CORE_DEVICE_IDENTITY_DIR"[
            len(config_module.ENV_PREFIX):
        ].lower()
        self.assertNotIn(suffix, _PRIOR_KNOWN_KEYS)

    def test_prior_parser_surface_rejection_via_actual_parser_code(self):
        """Rollback proof through the ACTUAL from_env code path running
        under the prior release's key surface (not only a copied
        constant): the new variable is rejected, and without it parsing
        behaves identically to the prior release."""
        prior_keys = frozenset(config_module.KNOWN_KEYS) - {
            "device_identity_dir"
        }
        with mock.patch.object(config_module, "KNOWN_KEYS", prior_keys):
            with self.assertRaises(ConfigError) as ctx:
                AppConfig.from_env(
                    {"PIANO_CORE_DEVICE_IDENTITY_DIR": "/tmp/x"}
                )
            self.assertIn("PIANO_CORE_DEVICE_IDENTITY_DIR", str(ctx.exception))
            cfg = AppConfig.from_env({})
            self.assertEqual(cfg.device_identity_dir, "")

    def test_removing_variable_restores_prior_compatibility(self):
        """Without the new variable, parsing succeeds under the old key
        set and the new parser agrees with the old defaults."""
        env = {"PIANO_CORE_MODE": "production"}
        cfg = AppConfig.from_env(env)
        self.assertEqual(cfg.device_identity_dir, "")
        data = {
            key[len(config_module.ENV_PREFIX):].lower(): value
            for key, value in env.items()
        }
        unknown = [key for key in data if key not in _PRIOR_KNOWN_KEYS]
        self.assertEqual(unknown, [])

    def test_identity_bytes_untouched_by_load(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = _make_identity_dir(Path(tmp.name))
        initialize_identity(directory)
        before = Path(directory, RECORD_NAME).read_bytes()
        load_identity(directory)
        self.assertEqual(Path(directory, RECORD_NAME).read_bytes(), before)


# ----------------------------------------------------------------------
# Independent-review remediation regressions (R1–R4)
# ----------------------------------------------------------------------
class ReviewRemediationTests(unittest.TestCase):
    """Deterministic regressions for the R1–R5 review findings.

    R1: descriptor-authoritative directory (trailing-slash symlink,
    replacement race, descriptor-vs-pathname authority). R2: an
    existing wrong-mode lock is never repaired. R3: GID is validated
    (per-object validation is exercised at the validator level because
    chown'ing fixtures to a foreign GID requires privileges the test
    account must not need — recorded as an accepted test limitation).
    R4: hostile umask cannot produce an unusable published identity.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = _make_identity_dir(Path(self.tmp.name))

    # -- R1 ------------------------------------------------------------
    def test_trailing_slash_symlink_directory_rejected(self):
        """R1 exact reviewer case: the trailing-slash symlink form that a
        plain open() would dereference must be rejected."""
        link = os.path.join(self.tmp.name, "identity-link")
        os.symlink(self.directory, link)
        with_slash = link + "/"
        for operation in (load_identity, initialize_identity):
            with self.assertRaises(IdentityError) as ctx:
                operation(with_slash)
            self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")
        self.assertEqual(os.listdir(self.directory), [])

    def test_directory_replacement_cannot_redirect_open_descriptor(self):
        """R1: swapping the directory after the descriptor is opened and
        validated cannot redirect an in-flight load to another
        directory's identity."""
        identity = initialize_identity(self.directory)
        dir_fd = _open_identity_directory(self.directory)
        try:
            moved = os.path.join(self.tmp.name, "moved-aside")
            os.rename(self.directory, moved)
            replacement = _make_identity_dir(Path(self.tmp.name))
            os.rename(replacement, self.directory)
            other = initialize_identity(self.directory)
            self.assertNotEqual(other, identity)
            # The ORIGINAL descriptor still resolves to the ORIGINAL
            # validated directory inode.
            self.assertEqual(
                parse_identity_record(_read_record_bytes(dir_fd)), identity
            )
        finally:
            os.close(dir_fd)

    def test_passing_pathname_check_alone_is_never_sufficient(self):
        """R1: a pathname check that falsely claims safe storage must
        not let a load succeed — the opened descriptor is the only
        authority."""
        os.chmod(self.directory, 0o755)  # descriptor-level truth: unsafe
        lying = os.stat_result(
            (
                stat.S_IFDIR | IDENTITY_DIRECTORY_MODE,
                1,
                2,
                1,
                os.geteuid(),
                os.getegid(),
                4096,
                0,
                0,
                0,
            )
        )
        with mock.patch("os.lstat", return_value=lying):
            with self.assertRaises(IdentityError) as ctx:
                load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_descriptor_attributes_are_the_authority(self):
        """R1: an already-validated descriptor stays authoritative even
        after the pathname's metadata changes underneath it; a fresh
        load re-validates and rejects the now-unsafe path."""
        identity = initialize_identity(self.directory)
        dir_fd = _open_identity_directory(self.directory)
        try:
            os.chmod(self.directory, 0o755)
            self.assertEqual(
                parse_identity_record(_read_record_bytes(dir_fd)), identity
            )
            with self.assertRaises(IdentityError) as ctx:
                load_identity(self.directory)
            self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")
        finally:
            os.close(dir_fd)
            os.chmod(self.directory, IDENTITY_DIRECTORY_MODE)

    # -- R2 ------------------------------------------------------------
    def test_existing_wrong_mode_lock_is_not_repaired(self):
        """R2 exact reviewer case: pre-existing .initialize.lock 0644
        must fail initialization and REMAIN 0644 — never silently
        repaired."""
        lock = os.path.join(self.directory, LOCK_NAME)
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
        os.chmod(lock, 0o644)
        with self.assertRaises(IdentityError) as ctx:
            initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")
        self.assertEqual(stat.S_IMODE(os.stat(lock).st_mode), 0o644)
        self.assertFalse(
            os.path.exists(os.path.join(self.directory, RECORD_NAME))
        )
        # Explicit operator repair (not code repair) restores operation.
        os.chmod(lock, 0o600)
        identity = initialize_identity(self.directory)
        self.assertEqual(load_identity(self.directory), identity)

    def test_newly_created_lock_mode_is_umask_independent(self):
        previous = os.umask(0o077)
        try:
            initialize_identity(self.directory)
        finally:
            os.umask(previous)
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.join(self.directory, LOCK_NAME)).st_mode),
            IDENTITY_RECORD_MODE,
        )

    # -- R3 ------------------------------------------------------------
    def test_wrong_gid_directory_rejected_fail_closed(self):
        """R3: wrong GID must fail closed without mutation (directory
        boundary; integration level)."""
        with mock.patch("os.getegid", return_value=os.getegid() + 1):
            with self.assertRaises(IdentityError) as ctx:
                load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")
        self.assertEqual(os.listdir(self.directory), [])

    def test_wrong_gid_is_rejected_for_every_object_validator(self):
        """R3: the record/lock validator (shared by device.json,
        .initialize.lock, and the pre-publication temp file) and the
        directory validator each reject wrong-GID descriptors.

        Per-object INTEGRATION fixtures with a genuinely foreign GID
        would require chown privileges; the validator level is the
        deterministic equivalent (accepted test limitation)."""
        wrong_gid = os.getegid() + 1
        directory_stat = os.stat_result(
            (
                stat.S_IFDIR | IDENTITY_DIRECTORY_MODE,
                1,
                2,
                1,
                os.geteuid(),
                wrong_gid,
                4096,
                0,
                0,
                0,
            )
        )
        record_stat = os.stat_result(
            (
                stat.S_IFREG | IDENTITY_RECORD_MODE,
                1,
                2,
                1,
                os.geteuid(),
                wrong_gid,
                36,
                0,
                0,
                0,
            )
        )
        with self.assertRaises(IdentityError):
            _validate_directory_stat(directory_stat)
        with self.assertRaises(IdentityError):
            _validate_record_stat(record_stat)

    def test_wrong_gid_existing_lock_fails_closed(self):
        """R3+R2: an existing 0600 lock is rejected when the effective
        GID does not match, without any mutation."""
        lock = os.path.join(self.directory, LOCK_NAME)
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.chmod(lock, 0o600)
        with mock.patch("os.getegid", return_value=os.getegid() + 1):
            with self.assertRaises(IdentityError) as ctx:
                initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")
        self.assertEqual(stat.S_IMODE(os.stat(lock).st_mode), 0o600)
        self.assertFalse(
            os.path.exists(os.path.join(self.directory, RECORD_NAME))
        )

    # -- R4 ------------------------------------------------------------
    def test_hostile_umask_still_produces_usable_identity(self):
        """R4 exact reviewer case: umask 0777 must still yield a 0600
        published record that loads immediately with the same UUID."""
        previous = os.umask(0o777)
        try:
            identity = initialize_identity(self.directory)
        finally:
            os.umask(previous)
        record = os.path.join(self.directory, RECORD_NAME)
        self.assertEqual(
            stat.S_IMODE(os.stat(record).st_mode), IDENTITY_RECORD_MODE
        )
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.join(self.directory, LOCK_NAME)).st_mode),
            IDENTITY_RECORD_MODE,
        )
        self.assertEqual(load_identity(self.directory), identity)

    # -- Test-quality improvements required by the review ---------------
    def test_publication_boundary_atomic_visibility(self):
        """Before publication device.json is ABSENT; at the publication
        boundary it is immediately COMPLETE and valid — no partial final
        JSON ever becomes visible."""
        real_link = os.link
        observed = {}

        def spy_link(src, dst, **kwargs):
            observed["before"] = os.path.exists(
                os.path.join(self.directory, RECORD_NAME)
            )
            real_link(src, dst, **kwargs)
            observed["published_raw"] = Path(
                self.directory, RECORD_NAME
            ).read_bytes()

        with mock.patch("os.link", side_effect=spy_link):
            identity = initialize_identity(self.directory)
        self.assertFalse(observed["before"])
        self.assertEqual(
            observed["published_raw"], serialize_identity_record(identity)
        )
        self.assertEqual(
            json.loads(observed["published_raw"])["device_id"],
            identity.device_id,
        )
        self.assertEqual(load_identity(self.directory), identity)

    def test_cross_process_initialization_single_winner(self):
        """True cross-process concurrency (no sleeps, bounded joins): at
        most one creator wins; contenders get busy or observe the
        winner; every successful result is the SAME UUID."""
        helper = Path(self.tmp.name) / "race_helper.py"
        helper.write_text(
            "\n".join(
                [
                    "import json",
                    "import multiprocessing as mp",
                    "import sys",
                    "",
                    "",
                    "def child(directory, barrier, queue):",
                    "    from piano_core.device_identity import (",
                    "        IdentityBusyError,",
                    "        initialize_identity,",
                    "    )",
                    "",
                    "    barrier.wait(timeout=20)",
                    "    try:",
                    "        identity = initialize_identity(directory)",
                    "        queue.put(['ok', identity.device_id])",
                    "    except IdentityBusyError:",
                    "        queue.put(['busy', None])",
                    "",
                    "",
                    "def main():",
                    "    mp.set_start_method('fork')",
                    "    directory = sys.argv[1]",
                    "    workers_count = 4",
                    "    barrier = mp.Barrier(workers_count)",
                    "    queue = mp.Queue()",
                    "    workers = [",
                    "        mp.Process(target=child, args=(directory, barrier, queue))",
                    "        for _ in range(workers_count)",
                    "    ]",
                    "    for worker in workers:",
                    "        worker.start()",
                    "    for worker in workers:",
                    "        worker.join(timeout=30)",
                    "    results = [queue.get(timeout=15) for _ in range(workers_count)]",
                    "    alive = any(worker.is_alive() for worker in workers)",
                    "    print(json.dumps({'alive': alive, 'results': results}))",
                    "    sys.exit(1 if alive else 0)",
                    "",
                    "",
                    'if __name__ == "__main__":',
                    "    main()",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        env = dict(os.environ, PYTHONPATH=str(_REPO_ROOT / "src"))
        result = subprocess.run(
            [sys.executable, str(helper), self.directory],
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["alive"])
        outcomes = report["results"]
        self.assertEqual(len(outcomes), 4)
        self.assertTrue(
            all(status in ("ok", "busy") for status, _ in outcomes)
        )
        winners = [device_id for status, device_id in outcomes if status == "ok"]
        self.assertGreaterEqual(len(winners), 1)
        self.assertLessEqual(len(set(winners)), 1)
        persisted = load_identity(self.directory)
        self.assertIn(persisted.device_id, winners)


# ----------------------------------------------------------------------
# Independent-review remediation regressions — round 2 (R1 / R6 / R7)
# ----------------------------------------------------------------------
class ReviewRemediation2Tests(unittest.TestCase):
    """Deterministic regressions for the second re-review findings:
    R1 (temp creation must stay inside the validated directory
    descriptor), R6 (lock-open failure must not raise
    UnboundLocalError), R7 (CLI must translate argument errors to the
    exit-2 contract). All filesystem effects are REAL — no mkstemp
    mocking."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.directory = _make_identity_dir(self.tmp_path)

    # -- R1 ------------------------------------------------------------
    def test_temp_and_publication_stay_inside_validated_descriptor(self):
        """R1 exact reviewer case, real filesystem: open+validate A,
        swap the PATHNAME to resolve to B while initialization is in
        flight (interleaved at the module's own UUID seam — not a
        mkstemp mock), then prove nothing escapes into B."""
        moved_aside = self.tmp_path / "original"
        decoy = self.tmp_path / "decoy"
        decoy.mkdir()
        os.chmod(decoy, IDENTITY_DIRECTORY_MODE)

        real_uuid4 = uuid.uuid4
        swaps = {"done": False}

        def swap_pathname_then_uuid():
            # Deterministic interleaving: this runs AFTER the directory
            # was securely opened/validated and the lock was taken, and
            # BEFORE temp creation/publication.
            if not swaps["done"]:
                os.rename(self.directory, moved_aside)
                os.rename(decoy, self.directory)
                swaps["done"] = True
            return real_uuid4()

        with mock.patch(
            "piano_core.device_identity.uuid.uuid4",
            side_effect=swap_pathname_then_uuid,
        ):
            identity = initialize_identity(self.directory)

        # The replacement directory at the ORIGINAL pathname received
        # NOTHING: no temp file, no lock, no device.json.
        self.assertEqual(os.listdir(self.directory), [])
        # Publication happened only relative to the validated
        # descriptor (the original, now renamed directory).
        self.assertEqual(
            sorted(os.listdir(moved_aside)), [LOCK_NAME, RECORD_NAME]
        )
        record = json.loads(
            (moved_aside / RECORD_NAME).read_bytes()
        )
        self.assertEqual(record["device_id"], identity.device_id)
        self.assertEqual(load_identity(str(moved_aside)), identity)
        # The pathname is no longer the authority: it now resolves to
        # the untouched decoy with no identity.
        with self.assertRaises(IdentityError) as ctx:
            load_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_missing")

    def test_initialization_never_uses_pathname_based_temp_creation(self):
        """R1: tempfile/mkstemp-style pathname resolution must not be
        part of identity initialization at all — initialization
        succeeds with those APIs poisoned."""
        def poison(*args, **kwargs):  # noqa: ARG001
            raise AssertionError("pathname-based temp creation used")

        with mock.patch("tempfile.mkstemp", side_effect=poison), mock.patch(
            "tempfile.NamedTemporaryFile", side_effect=poison
        ):
            identity = initialize_identity(self.directory)
        self.assertEqual(load_identity(self.directory), identity)
        self.assertEqual(
            sorted(os.listdir(self.directory)), [LOCK_NAME, RECORD_NAME]
        )

    # -- R6 ------------------------------------------------------------
    def test_pre_existing_lock_symlink_fails_controlled(self):
        """R6 exact reviewer case: a symlinked .initialize.lock must
        produce a controlled identity error — never an
        UnboundLocalError/traceback from closing an unassigned fd."""
        os.symlink(
            os.path.join(self.tmp.name, "elsewhere.lock"),
            os.path.join(self.directory, LOCK_NAME),
        )
        with self.assertRaises(IdentityError) as ctx:
            initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")
        self.assertFalse(
            os.path.exists(os.path.join(self.directory, RECORD_NAME))
        )

    def test_pre_existing_lock_directory_fails_controlled(self):
        """R6: a failed lock OPEN (EISDIR) follows the same controlled
        path (open/validation/cleanup separated; fd=None pattern)."""
        os.mkdir(os.path.join(self.directory, LOCK_NAME))
        with self.assertRaises(IdentityError) as ctx:
            initialize_identity(self.directory)
        self.assertEqual(ctx.exception.reason, "identity_unsafe_storage")

    def test_cli_lock_symlink_exit_two_end_to_end(self):
        """R6 end-to-end through the CLI: exit 2, stable reason on
        stderr, no identity on stdout, no traceback."""
        os.symlink(
            os.path.join(self.tmp.name, "elsewhere.lock"),
            os.path.join(self.directory, LOCK_NAME),
        )
        code, out, err = _run_cli(["initialize", "--directory", self.directory])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "identity_unsafe_storage")
        self.assertNotIn("Traceback", err)
        self.assertNotIn("UnboundLocalError", err)

    # -- R7 ------------------------------------------------------------
    def test_cli_dotdot_path_exit_two_no_traceback(self):
        """R7 exact reviewer case: an absolute path containing '..' is
        an argument error with the exit-2 contract — not exit 1 with a
        ValueError traceback."""
        crafted = os.path.join(self.tmp.name, "identity", "..", "identity")
        self.assertTrue(os.path.isdir(self.directory))
        code, out, err = _run_cli(["initialize", "--directory", crafted])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("invalid directory", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(os.listdir(self.directory), [])

    def test_cli_relative_and_nul_paths_exit_two(self):
        for bad in ("relative/dir", "/tmp/a\x00b", "~", "$HOME/identity"):
            code, out, err = _run_cli(["initialize", "--directory", bad])
            self.assertEqual(code, 2, bad)
            self.assertEqual(out, "", bad)
            self.assertNotIn("Traceback", err, bad)

    def test_validate_directory_is_the_single_canonical_contract(self):
        """R7: one validation surface shared by module and CLI."""
        from piano_core.device_identity import validate_directory

        self.assertEqual(
            validate_directory("/var/lib/piano-core/identity"),
            "/var/lib/piano-core/identity",
        )
        for bad in ("", "relative", "/a/../b", "/tmp/a\x00b", None, 42):
            with self.assertRaises(ValueError):
                validate_directory(bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
