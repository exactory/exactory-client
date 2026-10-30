"""Bounded finishing retries preserve the original refusal and isolate costly work."""

import unittest
from unittest.mock import patch

from research_harness.errors import ResearchError


class FinishingWriteTests(unittest.TestCase):
    def test_expired_deadline_does_not_start_another_finishing_write(self):
        from research_harness.finishing import retry_store_write
        error = ResearchError('store_busy', 'Writer remains active')
        with patch('research_harness.finishing.time.monotonic', side_effect=[0, 7, 8]), \
                patch('research_harness.finishing.time.sleep') as sleep, \
                patch('builtins.input', side_effect=error) as write:
            with self.assertRaises(ResearchError) as caught:
                retry_store_write(write)
        self.assertIs(caught.exception, error)
        self.assertEqual(write.call_count, 1)
        sleep.assert_called_once_with(1.0)

    def test_permanent_busy_refusal_stops_within_retry_bound(self):
        from research_harness.finishing import retry_store_write
        error = ResearchError('store_busy', 'Writer remains active')
        with patch('research_harness.storage._BUSY_TIMEOUT_SECONDS', 0.001), \
                patch('research_harness.finishing.time.sleep') as sleep, \
                patch('research_harness.finishing.time.monotonic', return_value=0), \
                patch('builtins.input', side_effect=error) as write:
            with self.assertRaises(ResearchError) as caught:
                retry_store_write(write)
        self.assertIs(caught.exception, error)
        self.assertGreater(write.call_count, 1)
        self.assertLessEqual(write.call_count, 8)
        self.assertEqual(sleep.call_count, write.call_count - 1)

    def test_other_refusal_is_propagated_without_retry(self):
        from research_harness.finishing import retry_store_write
        error = ResearchError('operation_superseded', 'A different operation owns the capture')
        with patch('builtins.input', side_effect=error) as write:
            with self.assertRaises(ResearchError) as caught:
                retry_store_write(write)
        self.assertIs(caught.exception, error)
        self.assertEqual(write.call_count, 1)
