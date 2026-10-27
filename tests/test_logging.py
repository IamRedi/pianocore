"""V2 tests: deterministic logging foundation."""

import io
import logging
import re
import unittest

from piano_core.config import LogLevel
from piano_core.logging_setup import LOGGER_NAME, configure_logging

# UTC ISO-8601 with milliseconds: 2026-09-04T12:00:00.000Z
_TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"
_LINE = re.compile(
    rf"^{_TIMESTAMP} (DEBUG|INFO|WARNING|ERROR) piano_core(\.\S+)? .+$"
)


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()

    def tearDown(self):
        logger = logging.getLogger(LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    def _logger(self):
        return configure_logging(stream=self.stream)

    def test_single_line_structured_format(self):
        logger = self._logger()
        logger.info("hello %s", "piano")
        line = self.stream.getvalue().strip()
        self.assertEqual(len(line.splitlines()), 1)
        self.assertRegex(line, _LINE)
        self.assertTrue(line.endswith("hello piano"))

    def test_level_filtering(self):
        logger = self._logger()
        logger.warning("visible")
        logger.debug("filtered")
        output = self.stream.getvalue()
        self.assertIn("visible", output)
        self.assertNotIn("filtered", output)

    def test_loglevel_member_accepted(self):
        logger = configure_logging(LogLevel.DEBUG, stream=self.stream)
        self.assertEqual(logger.level, logging.DEBUG)
        logger.debug("debug visible")
        self.assertIn("debug visible", self.stream.getvalue())

    def test_reconfiguration_does_not_duplicate_handlers(self):
        first = self._logger()
        second = self._logger()
        self.assertIs(first, second)
        self.assertEqual(len(second.handlers), 1)
        second.info("once")
        self.assertEqual(len(self.stream.getvalue().strip().splitlines()), 1)

    def test_invalid_level_type_raises(self):
        with self.assertRaises(TypeError):
            configure_logging(level="INFO")

    def test_root_logger_untouched(self):
        root_level = logging.getLogger().level
        root_handlers = list(logging.getLogger().handlers)
        self._logger()
        self.assertEqual(logging.getLogger().level, root_level)
        self.assertEqual(logging.getLogger().handlers, root_handlers)


if __name__ == "__main__":
    unittest.main()
