"""Current successor slots retain objections and independently bound adjudications."""

from unittest.mock import patch

import test_research_problem_delivery as fixtures
import test_research_problem_objections as objections
from research_harness import problem_claims, problem_delivery
from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness.errors import ResearchError


class RefreshCase(fixtures.ProblemDeliveryCase):
    make_objection = objections.ProblemObjectionTests.make_objection
    make_finding = objections.ProblemObjectionTests.make_finding
    build_slate_output = objections.ProblemObjectionTests.build_slate_output
    prepare_problem_evidence = objections.ProblemObjectionTests.prepare_problem_evidence
    prepare_reviews = objections.ProblemObjectionTests.prepare_reviews
    observe_adjudication = objections.ProblemObjectionTests.observe_adjudication

    def change_review_currency(self, *, change_material=False):
        """A new slate objection changes review currency without changing scientific evidence."""
        before = problem_claims.compute_problem_evidence_digest(self.store.snapshot()['records'], self.artifacts)
        self.record_candidate('candidate-2', statement='Does the same estimate hold for odd indices?')
        output = self.build_slate_output(['candidate-1', 'candidate-2'])
        objection = self.make_objection()
        if change_material:
            objection['reason'] = 'The supplied witness also fails at odd indices.'
            objection['resolution_condition'] = 'Account for the witness at odd indices.'
        else:
            objection['id'] = 'new-slate-objection'
        output['objections'] = [dict(objection, candidate_ids=['candidate-1'])]
        review = self.observe('slate', 'first', output, identifier='currency-slate')
        self.record_slate('slate-2', deferred=[{'candidate_id': 'candidate-2', 'evidence_needed': 'A supported extension.'}],
                          review_ids=[review['id']])
        self.assertEqual(problem_claims.compute_problem_evidence_digest(self.store.snapshot()['records'], self.artifacts), before)

    def start_successors(self, reviewers=('first', 'second')):
        return [self.assign('result', reviewer, identifier='successor-' + reviewer) for reviewer in reviewers]

    def unresolved_ids(self, report):
        return {row['objection_id'] for row in report['obligations'] if row['code'] == 'research_objection_unresolved'}


class EmptyPairAdjudicationTests(RefreshCase):
    def test_focused_adjudication_keeps_its_effect_while_both_successors_are_pending(self):
        self.prepare_reviews(bar_reviewer='first')
        self.observe_adjudication()
        self.change_review_currency()
        self.start_successors()
        records = self.store.snapshot()['records']
        report = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertEqual(report['review_ids'], [])
        self.assertEqual(report['problem_evidence_digest'],
                         problem_claims.compute_problem_evidence_digest(records, self.artifacts))
        self.assertNotIn('unsupported-implication', self.unresolved_ids(report))
        post = problem_delivery.assess_post(records, self.artifacts)
        self.assertFalse(post['ready'])
        self.assertNotIn('unsupported-implication', self.unresolved_ids(post))
        codes = {row['code'] for row in post['obligations']}
        # Both slots are occupied by pending successors, so the gate reports pending work, not a missing pair.
        self.assertIn('review_task_pending', codes)
        self.assertNotIn('problem_review_pair_required', codes)

    def test_not_binding_adjudication_keeps_its_effect_with_an_empty_pair(self):
        self.prepare_reviews(bar_reviewer='first')
        self.observe_adjudication('not_binding')
        self.change_review_currency()
        self.start_successors()
        report = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertNotIn('unsupported-implication', self.unresolved_ids(report))
        self.assertFalse(report['ready'])

    def test_changed_evidence_digest_leaves_the_objection_unresolved(self):
        self.prepare_reviews(bar_reviewer='first')
        self.observe_adjudication()
        self.mutate(problem_claims.record_value, self.build_value([self.item], identifier='changed-value'))
        self.start_successors()
        report = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertIn('unsupported-implication', self.unresolved_ids(report))

    def test_changed_material_version_leaves_the_objection_unresolved(self):
        self.prepare_reviews(bar_reviewer='first')
        self.observe_adjudication()
        self.change_review_currency(change_material=True)
        self.start_successors()
        report = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertIn('unsupported-implication', self.unresolved_ids(report))

    def test_digest_failure_with_empty_pair_is_reported_by_post(self):
        self.prepare_reviews(bar_reviewer='first', results=False)
        records = self.store.snapshot()['records']
        failure = ResearchError('synthetic_evidence_unavailable', 'The saved evidence cannot be read.')
        with patch.object(problem_claims, 'compute_problem_evidence_digest', side_effect=failure) as compute, \
                patch.object(problem_delivery, 'compute_problem_evidence_digest', side_effect=failure), \
                patch.object(decisions, '_assess_problem_coverage') as coverage:
            report = decisions.assess_problem_reviews(records, self.artifacts)
            self.assertIsNone(report['problem_evidence_digest'])
            self.assertEqual(compute.call_count, 1)
            coverage.assert_not_called()
            post = problem_delivery.assess_post(records, self.artifacts)
        self.assertIn(failure.code, {row['code'] for row in post['obligations']})

    def test_check_evidence_false_computes_neither_digest_nor_coverage(self):
        self.prepare_reviews(bar_reviewer='first')
        records = self.store.snapshot()['records']
        with patch.object(problem_claims, 'compute_problem_evidence_digest') as compute, \
                patch.object(problem_claims, 'describe_problem_evidence') as describe, \
                patch.object(decisions, '_assess_problem_coverage') as coverage:
            report = decisions.assess_problem_reviews(records, self.artifacts, check_evidence=False)
        self.assertIsNone(report['problem_evidence_digest'])
        compute.assert_not_called()
        describe.assert_not_called()
        coverage.assert_not_called()

    def test_complete_pair_keeps_digest_coverage_and_error_propagation(self):
        self.prepare_reviews(bar_reviewer='first')
        self.observe_adjudication()
        records = self.store.snapshot()['records']
        report = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertTrue(report['ready'], report)
        self.assertEqual(report['review_ids'], ['review-result-first', 'review-result-second'])
        self.assertNotIn('slot_outcomes', report)
        failure = ResearchError('synthetic_evidence_unavailable', 'The saved evidence cannot be read.')
        with patch.object(problem_claims, 'compute_problem_evidence_digest', side_effect=failure):
            with self.assertRaises(ResearchError) as caught:
                decisions.assess_problem_reviews(records, self.artifacts)
        self.assertIs(caught.exception, failure)

    def test_programming_error_with_empty_pair_surfaces(self):
        self.prepare_reviews(bar_reviewer='first', results=False)
        with patch.object(problem_claims, 'compute_problem_evidence_digest', side_effect=KeyError('defect')):
            with self.assertRaises(KeyError):
                decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
