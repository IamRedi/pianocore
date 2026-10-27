"""V2 tests: canonical application identity, version, and packaging."""

import tomllib
import unittest
from pathlib import Path

import piano_core

_REPO_ROOT = Path(__file__).resolve().parent.parent


class IdentityTests(unittest.TestCase):
    def test_product_name(self):
        self.assertEqual(piano_core.PRODUCT_NAME, "Piano Core")

    def test_runtime_identifier(self):
        self.assertEqual(piano_core.RUNTIME_IDENTIFIER, "piano-core")

    def test_version_present_and_canonical_shape(self):
        version = piano_core.__version__
        self.assertIsInstance(version, str)
        self.assertRegex(version, r"^\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$")

    def test_identity_exported(self):
        for name in ("PRODUCT_NAME", "RUNTIME_IDENTIFIER", "__version__"):
            self.assertIn(name, piano_core.__all__)


class PackagingTests(unittest.TestCase):
    """pyproject stays consistent with the canonical identity source."""

    def test_pyproject_parses_and_matches_identity(self):
        with open(_REPO_ROOT / "pyproject.toml", "rb") as handle:
            data = tomllib.load(handle)
        self.assertEqual(data["project"]["name"], piano_core.RUNTIME_IDENTIFIER)
        self.assertIn("version", data["project"]["dynamic"])
        self.assertEqual(
            data["project"]["requires-python"], ">=3.11"
        )
        self.assertEqual(
            data["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "piano_core.__version__",
        )

    def test_version_is_declared_exactly_once(self):
        """No static version string may exist in pyproject (single source)."""
        raw = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertNotIn("0.1.0-dev", raw)


if __name__ == "__main__":
    unittest.main()
