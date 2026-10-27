"""V2/V3 tests: UI state logic under node (PIANO-P013).

Runs the deterministic unit tests for the pure logic exported by the
packaged ``app.js`` (tests/weblogic/app.test.mjs) with node. Skipped
when node is unavailable — the pure functions remain statically
contract-checked by tests/test_web_contract.py in that case.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST = _REPO_ROOT / "tests" / "weblogic" / "app.test.mjs"
_PLAYER_APP_TEST = _REPO_ROOT / "tests" / "weblogic" / "player_app.test.mjs"
_KEYBOARD_TEST = _REPO_ROOT / "tests" / "weblogic" / "keyboard.test.mjs"
_API_CLIENT_HTTP_TEST = (
    _REPO_ROOT / "tests" / "weblogic" / "api_client_http.test.mjs"
)
_COMMAND_OUTCOME_HTTP_TEST = (
    _REPO_ROOT / "tests" / "weblogic" / "command_outcome_http.test.mjs"
)


@unittest.skipIf(shutil.which("node") is None, "node is not available")
class WebLogicTests(unittest.TestCase):
    def test_source_aware_selection_and_metadata_races_pass_under_node(self):
        result = subprocess.run(
            ["node", str(_REPO_ROOT / "tests/weblogic/source_isolation.test.mjs")],
            capture_output=True, text=True, timeout=120, cwd=_REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FAIL 0", result.stdout)

    def test_pure_ui_logic_passes_under_node(self):
        result = subprocess.run(
            ["node", str(_TEST)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=_REPO_ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"node logic tests failed:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("ALL LOGIC TESTS PASSED", result.stdout)

    def test_player_application_coordination_passes_under_node(self):
        result = subprocess.run(
            ["node", str(_PLAYER_APP_TEST)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=_REPO_ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"player application tests failed:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("ALL PLAYER APPLICATION TESTS PASSED", result.stdout)

    def test_live_keyboard_logic_passes_under_node(self):
        result = subprocess.run(
            ["node", str(_KEYBOARD_TEST)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=_REPO_ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"live keyboard tests failed:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("ALL KEYBOARD LOGIC TESTS PASSED", result.stdout)

    def test_api_client_full_request_lifetime_passes_under_real_http(self):
        result = subprocess.run(
            ["node", str(_API_CLIENT_HTTP_TEST)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=_REPO_ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"real HTTP ApiClient tests failed:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("ALL REAL HTTP API CLIENT TESTS PASSED", result.stdout)

    def test_command_outcome_flow_passes_under_real_http(self):
        result = subprocess.run(
            ["node", str(_COMMAND_OUTCOME_HTTP_TEST)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=_REPO_ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"real command-outcome integration tests failed:\n"
            f"{result.stdout}\n{result.stderr}",
        )
        self.assertIn(
            "ALL REAL COMMAND-OUTCOME INTEGRATION TESTS PASSED", result.stdout
        )


if __name__ == "__main__":
    unittest.main()
