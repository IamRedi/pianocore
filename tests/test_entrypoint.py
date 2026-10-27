"""V2 tests: CLI/entrypoint behavior and clean importability."""

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

import piano_core
from piano_core.app import main


class VersionFlagTests(unittest.TestCase):
    def test_version_flag_prints_identity_and_exits_zero(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(
            buffer.getvalue().strip(),
            f"{piano_core.PRODUCT_NAME} {piano_core.__version__} "
            f"({piano_core.RUNTIME_IDENTIFIER})",
        )


class ConfigurationFailureTests(unittest.TestCase):
    def test_invalid_configuration_exits_2(self):
        with mock.patch.dict(
            os.environ,
            {"PIANO_CORE_MODE": "carrier"},
            clear=False,
        ):
            code = main(["--run-for", "0.01"])
        self.assertEqual(code, 2)


class BoundedRunTests(unittest.TestCase):
    def test_bounded_run_starts_and_shuts_down_cleanly(self):
        self.assertEqual(main(["--run-for", "0.05"]), 0)

    def test_zero_run_for_shuts_down_immediately(self):
        self.assertEqual(main(["--run-for", "0"]), 0)

    def test_production_mode_env_with_bounded_run_exits_zero(self):
        with mock.patch.dict(
            os.environ,
            {"PIANO_CORE_MODE": "production", "PIANO_CORE_LOG_LEVEL": "DEBUG"},
            clear=False,
        ):
            self.assertEqual(main(["--run-for", "0.01"]), 0)


class ImportabilityTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertTrue(hasattr(piano_core, "__version__"))

    def test_entrypoint_module_imports(self):
        import piano_core.__main__  # noqa: F401 — import success is the test

        self.assertTrue(callable(piano_core.__main__.main))


if __name__ == "__main__":
    unittest.main()
