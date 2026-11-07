"""Controller refusals use the research store's conservative stop policy."""

from contextlib import redirect_stderr
import io
import json
import unittest

from search_controller.cli import print_error
from search_controller.errors import SearchError


class StopClassTests(unittest.TestCase):
    def test_cli_derives_class_from_code(self):
        for code, expected in (("lock_timeout", "waiting"), ("review_series_no_progress", "harness_defect"),
                               ("review_failure_unknown", "decision"), ("review_input_too_large", "decision")):
            with self.subTest(code=code):
                stream = io.StringIO()
                with redirect_stderr(stream):
                    print_error(SearchError(code, "Stop."))
                row = json.loads(stream.getvalue())["error"]
                self.assertEqual(row.get("stop_class"), expected)
