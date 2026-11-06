"""Unposted legacy metadata preserves the released continuation selection."""

import copy
import unittest

from research_harness import problem_delivery
import test_review_continuation as continuation_fixtures


class LegacyUnpostedHeadSelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = continuation_fixtures.ContinuationTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def build_legacy_cases(self):
        original = self.fixture.case.read_records()
        for name in ('staged-upgrade', 'slot', 'history'):
            records = copy.deepcopy(original)
            assignment = records['review_assignment'][self.fixture.assignment['id']]
            if name == 'staged-upgrade':
                assignment['context']['staged_upgrade'] = {}
            elif name == 'slot':
                assignment['slot_id'] = ''
            else:
                assignment['history_assignment_id'] = ''
            yield name, records

    def test_legacy_empty_metadata_keeps_the_unposted_run_plan(self):
        for name, records in self.build_legacy_cases():
            with self.subTest(metadata=name):
                self.assertNotIn('problem_post', records)
                plan = self.fixture.plan(records)
                self.assertEqual((plan['state'], plan.get('command')),
                                 ('run', 'review-run'), plan)
                self.assertEqual(plan['head_id'], 'bar-first')
                self.assertEqual(plan['payload'], dict(
                    id='bar-first-run-1', assignment_id='bar-first'))

    def test_existing_post_keeps_empty_metadata_validation_strict(self):
        for name, records in self.build_legacy_cases():
            with self.subTest(metadata=name):
                statement = self.fixture.case.statement
                records['problem_post'] = {'post': dict(
                    id='post', problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
                    statement_id=statement['id'],
                    statement_digest=problem_delivery.compute_statement_digest(statement),
                    gate='passed', posted_at='2026-11-05T02:53:34.915Z',
                    recorded_revision=self.fixture.case.store.revision + 1)}
                plan = self.fixture.plan(records)
                self.assertEqual((plan['state'], plan.get('code')),
                                 ('stop', 'invalid_review_context'), plan)
                self.assertNotIn('command', plan)

    def test_unrelated_legacy_metadata_does_not_stop_the_requested_unposted_head(self):
        records = self.fixture.case.read_records()
        original = records['review_assignment'][self.fixture.assignment['id']]
        unrelated = copy.deepcopy(original)
        unrelated.update(id='legacy-unrelated', slot_id='other-slot',
                         reviewer_id='other-reviewer',
                         recorded_revision=original['recorded_revision'] + 1)
        unrelated['context']['staged_upgrade'] = {}
        records['review_assignment'][unrelated['id']] = unrelated
        plan = self.fixture.plan(records)
        self.assertEqual((plan['state'], plan.get('command')),
                         ('run', 'review-run'), plan)
        self.assertEqual(plan['head_id'], 'bar-first')
        self.assertEqual(plan['payload'], dict(
            id='bar-first-run-1', assignment_id='bar-first'))


if __name__ == '__main__':
    unittest.main()
