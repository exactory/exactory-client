"""Status shares successful closure walks only inside its current command."""

from collections import Counter
from contextlib import contextmanager
import copy
import gc
import json
import unittest
from unittest.mock import patch
import weakref

from openness_reading_fixtures import OpennessReadingCase
from test_posted_chain_closure import ClosureCase
from research_harness import cli, problem_delivery
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.review_state_evaluation import runtime_scope


CLOSURE_FIELDS = (
    'id', 'role', 'dossier_id', 'context', 'staged_review', 'slot_id',
    'history_assignment_id', 'recorded_revision',
)


class WeakRecords(dict):
    pass


class PostedClosureCostTests(unittest.TestCase):
    def setUp(self):
        self.find_advisories = cli.find_view_advisories
        self.case = ClosureCase()
        self.addCleanup(self.case.doCleanups)
        OpennessReadingCase.setUp(self.case)
        self.case.limit_input()
        reference = self.case.export(assignment_id='openness-root')
        root = self.case.assign('openness-root', reference)
        self.case.invoke(root['id'], self.case.build_reading_response(
            self.case.read_packet(root['id'])))
        self.assignment = self.case.assign(
            'openness-head', reference, prior=root['id'])
        self.case.post(legacy=True)

    @contextmanager
    def count_walks(self):
        original = problem_delivery.find_review_closure_root
        walks = []

        def walk(records, assignment):
            binding = {name: assignment[name] for name in CLOSURE_FIELDS
                       if name in assignment}
            walks.append((records, json.dumps(binding, sort_keys=True)))
            return original(records, assignment)

        with patch.object(problem_delivery, 'find_review_closure_root',
                          side_effect=walk):
            yield walks

    def run_status(self, inspect=None, *, snapshot=None, evaluation=None):
        snapshot = self.case.store.snapshot() if snapshot is None else snapshot
        evaluation = (Evaluation(snapshot['records'], self.case.artifacts)
                      if evaluation is None else evaluation)
        def inspect_views(store, revision):
            if inspect is not None:
                inspect(evaluation)
            return self.find_advisories(store, revision)

        with patch.object(self.case.store, 'snapshot', return_value=snapshot), \
                patch.object(cli, 'Evaluation', return_value=evaluation), \
                patch.object(cli, 'find_view_advisories', side_effect=inspect_views):
            return cli.status_report(self.case.store)

    def build_unbound_records(self):
        root = dict(id='candidate', role='bar', dossier_id=None, context={},
                    subject=dict(kind='problem', statement_id=None),
                    recorded_revision=3)
        earlier = dict(root, id='earlier', recorded_revision=1)
        return {
            'workspace': {'problem': {'orchestration': 'problem_creation'}},
            'review_assignment': {'candidate': root, 'earlier': earlier},
            'problem_post': {'post': dict(id='post', statement_digest='synthetic',
                                          recorded_revision=2)},
        }

    def assert_walks_once(self, walks):
        counts = Counter((id(records), binding) for records, binding in walks)
        self.assertGreater(sum(counts.values()), 0)
        self.assertLessEqual(max(counts.values()), 1, counts)

    def test_posted_status_walks_each_identical_view_and_binding_once(self):
        with self.count_walks() as walks:
            report = self.run_status()
        self.assertEqual(report['next']['code'], 'problem_already_posted')
        self.assert_walks_once(walks)

    def test_second_status_recomputes_with_the_same_snapshot_and_evaluation(self):
        snapshot = self.case.store.snapshot()
        evaluation = Evaluation(snapshot['records'], self.case.artifacts)
        with self.count_walks() as walks:
            first = self.run_status(snapshot=snapshot, evaluation=evaluation)
            first_walks = list(walks)
            walks.clear()
            second = self.run_status(snapshot=snapshot, evaluation=evaluation)
            second_walks = list(walks)
        self.assertEqual(first['next'], second['next'])
        self.assert_walks_once(first_walks)
        self.assert_walks_once(second_walks)

    def test_same_assignment_in_distinct_record_views_resolves_distinct_roots(self):
        first = self.build_unbound_records()
        first['review_assignment']['candidate']['context'] = dict(
            prior_assignment_id='earlier')
        second = copy.deepcopy(first)
        second['review_assignment']['earlier']['recorded_revision'] = 4
        assignment = first['review_assignment']['candidate']

        def inspect(evaluation):
            with self.count_walks() as walks:
                for _ in range(2):
                    self.assertEqual(problem_delivery.find_review_post(
                        first, assignment, evaluation)['id'], 'post')
                    self.assertIsNone(problem_delivery.find_review_post(
                        second, assignment, evaluation))
            self.assertEqual(len(walks), 2)
            self.assert_walks_once(walks)

        self.run_status(inspect)

    def test_changed_closure_bindings_do_not_share_roots(self):
        changes = [
            dict(id='renamed'),
            dict(role='result'),
            dict(dossier_id='another-dossier'),
            dict(context=dict(prior_assignment_id='earlier')),
            dict(staged_review=dict(kind='controller')),
            dict(slot_id='earlier'),
            dict(history_assignment_id='earlier'),
            dict(recorded_revision=1),
            dict(staged_review=None),
        ]

        def inspect(evaluation):
            for change in changes:
                with self.subTest(change=change):
                    records = self.build_unbound_records()
                    assignment = records['review_assignment']['candidate']
                    changed = dict(assignment, **change)
                    with self.count_walks() as walks:
                        self.assertIsNone(problem_delivery.find_review_post(
                            records, assignment, evaluation))
                        post = problem_delivery.find_review_post(
                            records, changed, evaluation)
                        if any(name in change for name in (
                                'context', 'slot_id', 'history_assignment_id',
                                'recorded_revision')):
                            self.assertEqual(post['id'], 'post')
                        else:
                            self.assertIsNone(post)
                    self.assertEqual(len(walks), 2)

        self.run_status(inspect)

    def test_unrelated_transport_history_does_not_repeat_a_closure_walk(self):
        def inspect(evaluation):
            records = self.build_unbound_records()
            assignment = records['review_assignment']['candidate']
            with_history = dict(assignment, history=[
                dict(role='user', content='Synthetic transport history. ' * 10000)])
            with self.count_walks() as walks:
                self.assertIsNone(problem_delivery.find_review_post(
                    records, assignment, evaluation))
                self.assertIsNone(problem_delivery.find_review_post(
                    records, with_history, evaluation))
            self.assertEqual(len(walks), 1)

        self.run_status(inspect)

    def test_post_refusal_details_share_the_successful_classification_walk(self):
        def inspect(evaluation):
            with self.count_walks() as walks:
                with self.assertRaises(ResearchError) as caught:
                    problem_delivery.require_unposted_review(
                        evaluation.records, self.assignment, evaluation)
            self.assertEqual(caught.exception.code, 'problem_already_posted')
            self.assertEqual(caught.exception.details['closure_root_id'], 'openness-root')
            self.assertEqual(len(walks), 1)

        self.run_status(inspect)

    def test_current_malformed_ancestry_is_rechecked_and_errors_are_not_reused(self):
        def inspect(evaluation):
            records = self.build_unbound_records()
            assignment = records['review_assignment']['candidate']
            with self.count_walks() as walks:
                self.assertIsNone(problem_delivery.find_review_post(
                    records, assignment, evaluation))
                broken = dict(assignment, context=dict(prior_assignment_id='missing'))
                for _ in range(2):
                    with self.assertRaises(ResearchError) as caught:
                        problem_delivery.find_review_post(records, broken, evaluation)
                    self.assertEqual(caught.exception.code, 'invalid_review_context')
            self.assertEqual(len(walks), 3)

        self.run_status(inspect)

    def test_warm_roots_do_not_bypass_native_or_statement_validation(self):
        def inspect(evaluation):
            records = evaluation.records
            self.assertEqual(problem_delivery.find_review_post(
                records, self.assignment, evaluation)['id'], 'post')
            with self.assertRaises(ResearchError) as caught:
                problem_delivery.find_review_post(records, dict(
                    self.assignment, reviewer_id='another-reviewer'), evaluation)
            self.assertEqual(caught.exception.code, 'invalid_native_review_packet')

            records = self.build_unbound_records()
            statement = dict(id='statement', statement='Every synthetic object is finite.',
                             resolution_criteria='Prove the exact finite bound.')
            records['problem_statement'] = {'statement': statement}
            assignment = dict(records['review_assignment']['candidate'],
                              subject=dict(kind='problem', statement_id='statement'),
                              slot_id='candidate')
            self.assertIsNone(problem_delivery.find_review_post(
                records, assignment, evaluation))
            changed = dict(assignment, subject=dict(kind='problem', statement_id='missing'))
            with self.assertRaises(ResearchError) as caught:
                problem_delivery.find_review_post(records, changed, evaluation)
            self.assertEqual(caught.exception.code, 'problem_statement_missing')

        self.run_status(inspect)

    def test_memo_retains_each_records_view_only_until_status_returns(self):
        references = []

        def inspect(evaluation):
            records = WeakRecords(self.build_unbound_records())
            references.append(weakref.ref(records))
            self.assertIsNone(problem_delivery.find_review_post(
                records, records['review_assignment']['candidate'], evaluation))
            del records
            gc.collect()
            self.assertIsNotNone(references[0]())

        self.run_status(inspect)
        gc.collect()
        self.assertIsNone(references[0]())

    def test_other_runtime_scopes_do_not_reuse_closure_walks(self):
        records = self.build_unbound_records()
        evaluation = Evaluation(records, self.case.artifacts)
        with runtime_scope(records, evaluation), self.count_walks() as walks:
            for _ in range(2):
                self.assertIsNone(problem_delivery.find_review_post(
                    records, records['review_assignment']['candidate'], evaluation))
        self.assertEqual(len(walks), 2)

    def test_status_memo_does_not_escape_into_a_surrounding_runtime_scope(self):
        snapshot = self.case.store.snapshot()
        records = snapshot['records']
        evaluation = Evaluation(records, self.case.artifacts)
        with runtime_scope(records, evaluation):
            self.run_status(snapshot=snapshot, evaluation=evaluation)
            with self.count_walks() as walks:
                for _ in range(2):
                    self.assertEqual(problem_delivery.find_review_post(
                        records, self.assignment, evaluation)['id'], 'post')
            self.assertEqual(len(walks), 2)

    def test_failed_status_removes_its_memo_from_a_surrounding_runtime_scope(self):
        snapshot = self.case.store.snapshot()
        records = snapshot['records']
        evaluation = Evaluation(records, self.case.artifacts)

        def inspect(evaluation):
            self.assertEqual(problem_delivery.find_review_post(
                records, self.assignment, evaluation)['id'], 'post')
            raise ResearchError('synthetic_status_failure', 'Stop this synthetic status.')

        with runtime_scope(records, evaluation):
            with self.assertRaises(ResearchError) as caught:
                self.run_status(inspect, snapshot=snapshot, evaluation=evaluation)
            self.assertEqual(caught.exception.code, 'synthetic_status_failure')
            with self.count_walks() as walks:
                for _ in range(2):
                    self.assertEqual(problem_delivery.find_review_post(
                        records, self.assignment, evaluation)['id'], 'post')
            self.assertEqual(len(walks), 2)

    def test_nested_status_restores_the_surrounding_status_memo(self):
        snapshot = self.case.store.snapshot()
        records = snapshot['records']
        evaluation = Evaluation(records, self.case.artifacts)

        def inspect(evaluation):
            with self.count_walks() as walks:
                self.assertEqual(problem_delivery.find_review_post(
                    records, self.assignment, evaluation)['id'], 'post')
                nested = self.run_status(snapshot=snapshot, evaluation=evaluation)
                self.assertEqual(nested['next']['code'], 'problem_already_posted')
                self.assertEqual(problem_delivery.find_review_post(
                    records, self.assignment, evaluation)['id'], 'post')
            self.assertEqual(len(walks), 2)

        self.run_status(inspect, snapshot=snapshot, evaluation=evaluation)


if __name__ == '__main__':
    unittest.main()
