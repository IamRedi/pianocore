"""V2 tests: appliance-wide playback advisory lock (PIANO-P012).

The shared cross-process lock (CLI + service PlaybackManager): mutual
exclusion, clean release, and the cross-user read-only fallback that
keeps exclusion intact when the lock file was created by the other
identity.
"""

import fcntl
import os
import tempfile
import unittest
from pathlib import Path

from piano_core.playback.advisory import (
    LOCK_FILENAME,
    acquire_playback_lock,
    default_lock_path,
)


class AdvisoryLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock_path = str(Path(self.tmp.name) / LOCK_FILENAME)

    def test_acquire_release_and_reacquire(self):
        lock = acquire_playback_lock(self.lock_path)
        try:
            self.assertFalse(lock._released)
        finally:
            lock.release()
        self.assertTrue(lock._released)
        lock.release()  # idempotent
        again = acquire_playback_lock(self.lock_path)
        again.release()

    def test_second_acquire_while_held_fails_closed(self):
        lock = acquire_playback_lock(self.lock_path)
        try:
            with self.assertRaises(OSError):
                acquire_playback_lock(self.lock_path)
        finally:
            lock.release()

    def test_lock_records_version_stamp_when_writable(self):
        lock = acquire_playback_lock(self.lock_path)
        lock.release()
        content = Path(self.lock_path).read_text()
        self.assertTrue(content.strip(), "version stamp expected")

    def test_cross_user_read_only_fallback_keeps_exclusion(self):
        """A 0444 lock file (other-owner scenario) still enforces flock."""
        lock = acquire_playback_lock(self.lock_path)
        try:
            os.chmod(self.lock_path, 0o444)
            # Our O_RDWR|O_CREAT open fails (no write bit) -> O_RDONLY
            # fallback; flock must still refuse.
            with self.assertRaises(OSError):
                acquire_playback_lock(self.lock_path)
        finally:
            os.chmod(self.lock_path, 0o644)
            lock.release()

    def test_default_lock_path_prefers_run_lock(self):
        if not os.path.isdir("/run/lock"):
            self.skipTest("/run/lock not present on this host")
        self.assertEqual(default_lock_path(), "/run/lock/" + LOCK_FILENAME)


class FcntlSemanticsTests(unittest.TestCase):
    """flock on a READ-ONLY descriptor works (cross-user fallback basis)."""

    def test_flock_works_on_read_only_fd(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "ro-fd.lock"
        path.write_text("x")
        os.chmod(path, 0o444)
        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            other = os.open(path, os.O_RDONLY)
            try:
                with self.assertRaises(OSError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(other)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
