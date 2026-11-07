"""Controller refusals use the research store's conservative stop policy."""

from contextlib import redirect_stderr
import io
import json
import errno
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from search_controller.cli import print_error
from search_controller.errors import SearchError


class StopClassTests(unittest.TestCase):
    def test_cli_derives_class_from_code(self):
        for code, expected in (("lock_timeout", "decision"), ("review_series_no_progress", "harness_defect"),
                               ("review_failure_unknown", "decision"), ("review_input_too_large", "decision")):
            with self.subTest(code=code):
                stream = io.StringIO()
                with redirect_stderr(stream):
                    print_error(SearchError(code, "Stop."))
                row = json.loads(stream.getvalue())["error"]
                self.assertEqual(row.get("stop_class"), expected)

    def test_render_lock_io_error_is_a_decision_stop(self):
        from research_harness.stop_classes import classify_stop
        from search_controller.output_views import hold_render_lock
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory), _check_root=lambda: None)
            with patch("search_controller.output_views.fcntl.flock", side_effect=OSError(errno.EIO, "I/O failure")):
                with self.assertRaises(SearchError) as caught:
                    hold_render_lock(store)
            self.assertEqual(caught.exception.code, "lock_timeout")
            self.assertEqual(classify_stop(caught.exception.code), "decision")
