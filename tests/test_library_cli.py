"""V2 tests: piano-core-library CLI surface (PIANO-P010)."""

import tempfile
import unittest
from pathlib import Path

from piano_core.library.cli import main
from tests.test_library import write_midi

try:
    import mido  # noqa: F401

    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False


class LibraryCliTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir(parents=True)
        self.index = Path(self.tmp.name) / "idx" / "library.sqlite3"
        self.env = {
            "PIANO_CORE_LIBRARY_ROOT": str(self.root),
            "PIANO_CORE_LIBRARY_INDEX_PATH": str(self.index),
        }

    def test_version_flag(self):
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("piano-core", out.getvalue())

    def test_unset_configuration_fails_closed(self):
        code = main(["stats"], env={})
        self.assertEqual(code, 2)

    def test_unknown_env_rejected(self):
        code = main(["stats"], env={"PIANO_CORE_LIBRARY_BOGUS": "x"})
        self.assertEqual(code, 2)

    @unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
    def test_scan_stats_list_search_resolve_flow(self):
        import io
        from contextlib import redirect_stdout

        write_midi(self.root / "alfa.mid", notes=((60, 80),))
        write_midi(self.root / "sub" / "beta.MID", notes=((62, 80),))

        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["scan"], env=self.env)
        self.assertEqual(code, 0)
        self.assertIn("added=2", out.getvalue())
        self.assertIn("pending_remaining=0", out.getvalue())

        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["stats"], env=self.env)
        self.assertEqual(code, 0)
        self.assertIn("total=2", out.getvalue())
        self.assertIn("parsed=2", out.getvalue())

        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["list", "--limit", "1"], env=self.env)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().count("\n"), 1)

        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["search", "beta"], env=self.env)
        self.assertEqual(code, 0)
        self.assertIn("beta.MID", out.getvalue())

        song_id = out.getvalue().split()[0]  # first token of the search hit
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["resolve", song_id], env=self.env)
        self.assertEqual(code, 0)
        self.assertEqual(Path(out.getvalue().strip()).name, "beta.MID")
        self.assertTrue(Path(out.getvalue().strip()).is_absolute())

    def test_resolve_unknown_id_fails(self):
        write_midi(self.root / "a.mid")
        code = main(["scan", "--no-enrich"], env=self.env)
        self.assertEqual(code, 0)
        code = main(["resolve", "nope"], env=self.env)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
