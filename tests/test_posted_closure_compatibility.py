"""Publication closure preserves unposted legacy review admission."""

import copy
import unittest

from openness_reading_fixtures import OpennessReadingCase
from research_harness import problem_delivery, review_continuation
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError


class UnpostedClosureValidationTests(unittest.TestCase):
    def build_records(self):
        return {
            'workspace': {'problem': {'orchestration': 'problem_creation'}},
            'review_assignment': {},
        }

    def test_no_posts_return_before_review_binding_validation(self):
        root = dict(id='legacy', role='bar', dossier_id=None, context={},
                    subject=dict(kind='problem', statement_id=None))
        assignments = [dict(root, **{field: []})
                       for field in ('context', 'subject', 'staged_review')]
        assignments.append(dict(root, role='native_math', reviewer_id='reviewer'))
        for posts in (None, {}):
            for assignment in assignments:
                for check_unbound in (False, True):
                    records = self.build_records()
                    if posts is not None:
                        records['problem_post'] = posts
                    for operation in (problem_delivery.find_review_post,
                                      problem_delivery.require_unposted_review):
                        with self.subTest(posts=posts, assignment=assignment,
                                          check_unbound=check_unbound,
                                          operation=operation.__name__):
                            self.assertIsNone(operation(
                                records, assignment, check_unbound=check_unbound))

    def test_no_posts_return_before_closure_ancestry_validation(self):
        root = dict(id='legacy', role='bar', dossier_id=None, context={},
                    subject=dict(kind='problem', statement_id=None))
        assignments = [
            root,
            dict(root, recorded_revision=1,
                 context=dict(prior_assignment_id='missing')),
            dict(root, recorded_revision=1,
                 context=dict(prior_assignment_id='legacy')),
            dict(root, recorded_revision=1, slot_id=[]),
            dict(root, staged_review=dict(kind='task',
                                         controller_assignment_id='missing')),
        ]
        for posts in (None, {}):
            for assignment in assignments:
                records = self.build_records()
                if posts is not None:
                    records['problem_post'] = posts
                records['review_assignment']['legacy'] = assignment
                for operation in (problem_delivery.find_review_post,
                                  problem_delivery.require_unposted_review):
                    with self.subTest(posts=posts, assignment=assignment,
                                      operation=operation.__name__):
                        self.assertIsNone(operation(records, assignment))

    def test_existing_post_keeps_binding_and_ancestry_validation_strict(self):
        root = dict(id='legacy', role='bar', dossier_id=None, context={},
                    subject=dict(kind='problem', statement_id=None))
        for assignment in (dict(root, context=[]), root):
            for operation in (problem_delivery.find_review_post,
                              problem_delivery.require_unposted_review):
                with self.subTest(assignment=assignment, operation=operation.__name__):
                    records = self.build_records()
                    records['problem_post'] = {
                        'post': dict(id='post', statement_digest='synthetic-claim',
                                     recorded_revision=2),
                    }
                    records['review_assignment']['legacy'] = assignment
                    with self.assertRaises(ResearchError) as caught:
                        operation(records, assignment)
                    self.assertEqual(caught.exception.code, 'invalid_review_context')


class LegacyUnpostedAdmissionTests(OpennessReadingCase):
    def test_unposted_native_review_without_revision_plans_and_invokes(self):
        assignment = self.assign('legacy-openness', self.export(
            assignment_id='legacy-openness'))
        legacy = copy.deepcopy(assignment)
        legacy.pop('recorded_revision')
        legacy['slot_id'] = legacy['id']
        self.put('review_assignment', legacy['id'], legacy)
        records = self.store.snapshot()['records']
        self.assertNotIn('problem_post', records)
        self.assertNotIn('recorded_revision', records['review_assignment'][legacy['id']])

        with self.subTest(phase='plan'):
            plan = review_continuation.plan_next(records, self.artifacts, legacy['id'])
            self.assertEqual(plan['state'], 'run', plan)
            self.assertEqual(plan['command'], 'review-run')
            self.assertEqual(plan['payload']['assignment_id'], legacy['id'])

        packet = self.read_packet(legacy['id'])
        output = (self.build_reading_response(packet) if 'reading_turn' in packet
                  else self.build_native_result(packet))
        with self.subTest(phase='admission'):
            receipt = self.invoke(legacy['id'], output)
            self.assertEqual(receipt['result']['status'], 'completed')
            records = self.store.snapshot()['records']
            attempt = records['review_attempt']['attempt-' + legacy['id']]
            self.assertTrue(protocol.authenticate_attempt(
                records, self.artifacts, legacy, attempt))
            self.assertIn('attempt-' + legacy['id'], records['review_invocation'])
            self.assertNotIn('problem_post', records)


if __name__ == '__main__':
    unittest.main()
