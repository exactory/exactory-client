"""Every frozen staged dispatch participates in record qualification."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from research_harness import review_staged_protocol as staged
from research_harness.record_check import OfflineStore, check_continuation, check_reconstruction, run_timed_check
import test_review_staged_frontier as fixtures


class StagedRecordCheckTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FrontierTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.controller = self.fixture.create_controller()

    def dispatch(self):
        result = self.fixture.command('review-assignment', {'controller_assignment_id': self.controller['id']})
        action = result['next'][0]
        with patch.object(staged, 'wait_for_capture', side_effect=RuntimeError('before worker launch')):
            with self.assertRaises(RuntimeError):
                self.fixture.command(action['command'], action['payload'])
        return self.fixture.case.read_records()

    def test_staged_dispatch_and_frozen_plan_requests_rebuild_without_a_worker(self):
        records = self.dispatch()
        result = check_reconstruction(OfflineStore(self.fixture.case.root))['invocations']
        for identifier in records['review_task_dispatch']:
            self.assertIn('review_task_dispatch:' + identifier, result)
        for identifier in records['review_execution_plan']:
            self.assertIn('review_execution_plan:' + identifier, result)

    def test_staged_budget_incomplete_is_an_enumerated_decision_handoff(self):
        with patch('time.time', return_value=self.controller['staged_started_at'] + 86401):
            self.fixture.command('review-assignment', {'controller_assignment_id': self.controller['id']})
        result = run_timed_check('continuation', lambda: check_continuation(OfflineStore(self.fixture.case.root)))
        self.assertEqual(result['status'], 'passed', result)
        chain = next(row for row in result['result']['chains'] if row['head_id'] == self.controller['id'])
        self.assertEqual(chain['actions'][-1]['code'], 'assessment_incomplete')

    def test_fingerprint_includes_pending_staged_dispatch(self):
        records = self.dispatch()
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'ci/record_fingerprint.py'),
                                 '--harness', str(root), '--workspace', str(self.fixture.case.root)],
                                capture_output=True, text=True, cwd=root, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(result.stdout)['assignments']
        for identifier, dispatch in records['review_task_dispatch'].items():
            self.assertIn('review_task_dispatch:' + identifier,
                          entries[dispatch['assignment_id']]['rebuilt_requests'])

    def test_full_pending_staged_check_never_launches_or_reconciles_worker(self):
        from research_harness.record_check import check_record
        self.dispatch()
        sentinels = []
        with ExitStack() as stack:
            for target in ('subprocess.Popen', 'os.posix_spawn', 'socket.socket.connect',
                           'research_harness.review_transport.send_request',
                           'research_harness.review_claude_transport.send_request',
                           'research_harness.review_codex_transport.send_request',
                           'research_harness.review_codex_framed_transport.send_request',
                           'research_harness.review_codex_visual_transport.send_request'):
                sentinels.append(stack.enter_context(patch(target, side_effect=AssertionError(target))))
            result = check_record(self.fixture.case.root, isolate=False)
        for sentinel in sentinels:
            sentinel.assert_not_called()
        self.assertTrue(result['ready'], result)

    def test_offline_staged_planning_uses_no_uncached_git_provenance(self):
        from research_harness.record_check import check_record
        from research_harness.provenance import runtime_provenance
        runtime_provenance.cache_clear()
        with patch('subprocess.Popen', side_effect=AssertionError('unexpected process')) as popen:
            result = check_record(self.fixture.case.root, isolate=False)
        popen.assert_not_called()
        self.assertTrue(result['ready'], result)


if __name__ == '__main__':
    unittest.main()
