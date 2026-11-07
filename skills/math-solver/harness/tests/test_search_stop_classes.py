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
        for code, expected in (("lock_timeout", "waiting"), ("lock_io_error", "decision"),
                               ("store_busy", "waiting"), ("review_provider_unavailable", "waiting"),
                               ("review_series_no_progress", "harness_defect"),
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
            for code in (errno.EIO, errno.EBADF):
                with self.subTest(errno=code), patch("search_controller.output_views.fcntl.flock",
                        side_effect=OSError(code, "I/O failure")):
                    with self.assertRaises(SearchError) as caught:
                        hold_render_lock(store)
                    self.assertEqual(caught.exception.code, "lock_io_error")
                    self.assertEqual(classify_stop(caught.exception.code), "decision")
                    self.assertIn("I/O failure", caught.exception.message)
                    self.assertEqual(caught.exception.details["errno"], code)
                    self.assertIn("I/O failure", caught.exception.details["reason"])
                    self.assertEqual(caught.exception.details["path"], str(store.root / "output-views.lock"))

    def test_render_lock_contention_remains_a_waiting_stop(self):
        from research_harness.stop_classes import classify_stop
        from search_controller.output_views import hold_render_lock
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory), _check_root=lambda: None)
            for code in (errno.EACCES, errno.EAGAIN):
                with self.subTest(errno=code), patch("search_controller.output_views.LOCK_TIMEOUT_SECONDS", 0), \
                        patch("search_controller.output_views.fcntl.flock", side_effect=OSError(code, "Held")):
                    with self.assertRaises(SearchError) as caught:
                        hold_render_lock(store)
                    self.assertEqual(caught.exception.code, "lock_timeout")
                    self.assertEqual(classify_stop(caught.exception.code), "waiting")

    def test_writer_lock_io_error_is_a_decision_stop_and_preserves_history(self):
        from research_harness.stop_classes import classify_stop
        from search_controller.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize({"claim": "A"}, "objective-a")
            original = store.tree_path.read_bytes()
            for code in (errno.EIO, errno.EBADF):
                with self.subTest(errno=code), patch("search_controller.storage.fcntl.flock",
                        side_effect=OSError(code, "I/O failure")):
                    with self.assertRaises(SearchError) as caught:
                        store.append("record", {"value": 1}, 0, "request-one")
                    self.assertEqual(caught.exception.code, "lock_io_error")
                    self.assertEqual(classify_stop(caught.exception.code), "decision")
                    self.assertIn("I/O failure", caught.exception.message)
                    self.assertEqual(caught.exception.details["errno"], code)
                    self.assertIn("I/O failure", caught.exception.details["reason"])
                    self.assertEqual(caught.exception.details["path"], str(store.root / "tree.lock"))
                    self.assertEqual(store.tree_path.read_bytes(), original)
            self.assertEqual(store.append("record", {"value": 1}, 0, "request-one")["sequence"], 1)
