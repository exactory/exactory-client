"""Staged obligations retain the existing controller, execution and capture actions."""

import copy
import json
import sys
import unittest
from unittest.mock import patch

import test_review_staged_frontier as fixtures
from research_harness import cli, review_protocol as protocol, review_worker
from research_harness import review_task_execution as execution
from research_harness.problem_delivery import assess_source_requests
from research_harness.review_continuation import (
    annotate_review_obligations, describe_next_actions, list_chain_heads, plan_next)
from research_harness.review_series import run_series


class StagedContinuationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FrontierTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case
        self.controller = self.fixture.create_controller()

    def plan(self):
        return plan_next(self.case.read_records(), self.case.artifacts, self.controller['id'])

    def execute(self, plan, request='staged-next'):
        return cli.OPERATIONS[plan['command']](self.case.store, plan['payload'],
            expected_revision=self.case.store.revision,
            request_id=plan.get('request_id', request))

    def test_fresh_controller_plans_then_runs_its_planned_task(self):
        plan = self.plan()
        self.assertEqual((plan['chain'], plan['state'], plan['command']),
                         ('staged', 'plan', 'review-assignment'))
        self.execute(plan)
        plan = self.plan()
        self.assertEqual((plan['state'], plan['command']), ('run', 'review-run'))
        records = self.case.read_records()
        child = next(row for row in records['review_assignment'].values()
                     if row.get('staged_review', {}).get('task_id') == plan['payload']['task_id'])
        self.assertEqual(plan['payload'], dict(assignment_id=self.controller['id'],
            epoch_id=self.controller['staged_review']['epoch_id'],
            task_id=child['staged_review']['task_id'], id=child['id'] + '-run-1'))
        self.assertNotIn(child['id'], list_chain_heads(records, self.case.artifacts))
        obligations = assess_source_requests(records, self.case.artifacts)
        obligations = annotate_review_obligations(
            records, self.case.artifacts, dict(obligations=obligations))['obligations']
        item = next(row for row in obligations if row.get('task_id') == plan['payload']['task_id'])
        self.assertEqual(item['next'][0]['command'], plan['command'])
        self.assertEqual(item['next'][0]['payload'], plan['payload'])

    def test_staged_diagnostic_identity_is_attached_after_gate_digest(self):
        from research_harness.evidence import digest
        from research_harness.gates import gate_state

        records = self.case.read_records()
        with patch('research_harness.review_staged_protocol.matches_currency', return_value=False):
            original = gate_state(records, self.case.artifacts, 'post')
            upcoming = next(row for row in original['obligations']
                            if row['code'] == 'review_epoch_stale' and 'task_id' not in row)
            original = dict(original, next=upcoming)
            before = copy.deepcopy(original)
            enriched = annotate_review_obligations(records, self.case.artifacts, original)
        row = next(row for row in original['obligations']
                   if row['code'] == 'review_epoch_stale' and 'task_id' not in row)
        self.assertNotIn('assignment_id', row)
        self.assertNotIn('next', row)
        self.assertEqual(original['digest'], digest(dict(
            problem_evidence_digest=original['problem_evidence_digest'], decision=None,
            obligations=original['obligations'])))
        self.assertEqual(enriched['digest'], original['digest'])
        row = next(row for row in enriched['obligations']
                   if row['code'] == 'review_epoch_stale' and 'task_id' not in row)
        self.assertTrue(row['next'])
        self.assertEqual(row['next'][0]['command'], 'review-assignment')
        self.assertEqual(row['next'][0]['state'], 'handoff')
        self.assertEqual(enriched['next'], row)
        self.assertEqual(original, before)

    def test_killed_task_acceptance_reconciles_then_resumes_original_request(self):
        self.execute(self.plan())
        plan = self.plan()
        output = self.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.case.root / 'scientific.json').write_text(json.dumps(output))
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', fixtures.WORKER]):
            with patch.object(execution, 'accept_observation', side_effect=RuntimeError('driver killed')):
                with self.assertRaises(RuntimeError):
                    self.execute(plan, request='original-task-call')
            after = self.plan()
            self.assertEqual(after['state'], 'resume')
            self.assertEqual(after['payload']['action'], 'accept')
            self.assertEqual(after['request_id'], 'original-task-call')
            self.assertEqual(after['payload']['id'], plan['payload']['id'])
            self.execute(after)
        calls = (self.case.root / 'calls').read_text().splitlines()
        self.assertEqual(len(calls), 1)
        self.assertGreater(len(self.plan()['progress']['completed_tasks']), 0)

    def test_task_navigation_keeps_the_heads_terminal_or_handoff_plan(self):
        self.execute(self.plan())
        action = self.plan()
        records = self.case.read_records()
        original = dict(kind='review', workflow='review-run',
            scope=dict(task_id=action['payload']['task_id']),
            command=action['command'], payload=action['payload'])
        for state in ('done', 'stop', 'handoff'):
            selected = dict(state=state, head_id=self.controller['id'], chain='staged',
                            progress={}, code='fixture', details={})
            if state == 'handoff':
                selected['command'] = 'review-assignment'
            with self.subTest(state=state), patch(
                    'research_harness.review_continuation.plan_next', return_value=selected):
                enriched = describe_next_actions(records, self.case.artifacts,
                                                 self.controller['id'], [original])[0]
                self.assertEqual(enriched['state'], state)
                self.assertEqual(enriched['scope'], original['scope'])
                self.assertEqual(enriched['workflow'], original['workflow'])
                self.assertNotIn('payload', enriched)
                self.assertEqual(enriched.get('command'), selected.get('command'))


if __name__ == '__main__':
    unittest.main()
