"""Correction requests rebuild from the historical inputs they actually used."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from research_harness.record_check import OfflineStore, check_reconstruction
import test_review_output_correction as fixtures


class CorrectionRecordCheckTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CorrectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare_refused()
        self.fixture.correct(self.fixture.delta())

    def test_each_correction_invocation_rebuilds_its_exact_request(self):
        result = check_reconstruction(OfflineStore(self.fixture.fixture.root))['invocations']
        self.assertIn('review_output_correction_invocation:correction', result)

    def test_correction_observation_authentication_is_reported_individually(self):
        from research_harness.record_check import check_authentication
        result = check_authentication(OfflineStore(self.fixture.fixture.root))
        self.assertTrue(result['corrections']['correction']['authenticated'])

    def test_fingerprint_includes_correction_request_digest(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'ci/record_fingerprint.py'),
                                 '--harness', str(root), '--workspace', str(self.fixture.fixture.root)],
                                capture_output=True, text=True, cwd=root, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = json.loads(result.stdout)['assignments'][self.fixture.case.assignment['id']]['rebuilt_requests']
        self.assertIn('review_output_correction_invocation:correction', requests)
        self.assertIn('sha256', requests['review_output_correction_invocation:correction'])

    def test_later_context_changes_do_not_change_historical_request_reconstruction(self):
        from research_harness import review_protocol as protocol
        case = self.fixture.fixture
        assignment = self.fixture.case.assignment
        evidence = case.artifacts.put(b'A synthetic later context event.', 'text/plain')
        case.mutate(protocol.record_context_event, {
            'id': 'later-event', 'assignment_id': assignment['id'], 'kind': 'contamination',
            'source': 'author_history', 'reason': 'Synthetic context change after the correction',
            'evidence': [evidence]})
        result = check_reconstruction(OfflineStore(case.root))['invocations']
        self.assertIn('review_output_correction_invocation:correction', result)


if __name__ == '__main__':
    unittest.main()
