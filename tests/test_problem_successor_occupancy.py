"""Ordinary and both parts formats expose every occupied current result slot."""

from unittest.mock import patch

from problem_parts_fixtures import KEY, ResultPartsCase
from review_parts_fixtures import PartsResultCase
from test_problem_review_refresh import RefreshCase
import test_research_problem_reviews as fixtures
from research_harness import cli, problem_claims, problem_delivery, report_views, research_decisions as decisions
from research_harness import review_continuation, review_protocol as protocol


def make_objection(evidence):
    return dict(id='synthetic-bound-gap', claim='The finite bound lacks its induction step.',
                reason='The delivered example establishes only the initial case.', evidence=[evidence],
                resolution_condition='Supply evidence for the induction step.')


class OrdinaryOccupancyTests(RefreshCase):
    def test_no_head_one_pending_two_pending_and_complete_keep_report_shapes(self):
        self.prepare_reviews(bar_reviewer='first', results=False)
        report = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertNotIn('slot_outcomes', report)
        self.assertIn('problem_review_pair_required', {row['code'] for row in report['obligations']})
        self.assign('result', 'first')
        report = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertIn('slot_outcomes', report)
        self.assertEqual(report['slot_outcomes']['occupied'], 1)
        self.assertEqual(len(report['slot_outcomes']['pending']), 1)
        self.assertLessEqual({'review_task_pending', 'problem_review_pair_required'},
                             {row['code'] for row in report['obligations']})
        self.assign('result', 'second')
        report = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertIn('slot_outcomes', report)
        self.assertEqual(report['slot_outcomes']['occupied'], 2)
        self.assertEqual(len(report['slot_outcomes']['pending']), 2)
        self.assertNotIn('problem_review_pair_required', {row['code'] for row in report['obligations']})

    def test_completed_peer_is_checked_while_ordinary_successor_is_pending(self):
        self.prepare_reviews(bar_reviewer='first')
        self.change_review_currency()
        self.start_successors(('second',))
        self.mutate(problem_claims.record_value, self.build_value([self.item], identifier='peer-stale-evidence'))
        records = self.store.snapshot()['records']
        peer = records['value_review']['review-result-first']
        peer['reviewer_id'] = 'problem-author'
        peer['payload']['value']['assurances'][0]['status'] = 'failed'
        peer['payload']['value']['rubric']['priority'] = 0
        report = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertEqual(len(report['slot_outcomes']['complete']), 1)
        self.assertLessEqual({'review_task_pending', 'review_not_independent', 'problem_assurance_pending',
                             'problem_rubric_below_threshold', 'problem_evidence_unassessed'},
                             {row['code'] for row in report['obligations']})

    def test_saved_and_explicit_superseded_pairs_stay_refused_after_refresh(self):
        self.prepare_reviews(bar_reviewer='first')
        self.observe_adjudication()
        old = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.mutate(problem_claims.record_decision, dict(id='saved-decision', action='post',
                    review_ids=old['review_ids'], bar_review_ids=old['bar_review_ids'], reason='The current pair is complete.'))
        self.change_review_currency()
        self.start_successors()
        records = self.store.snapshot()['records']
        explicit = decisions.assess_problem_reviews(records, self.artifacts, old['review_ids'], old['bar_review_ids'])
        self.assertIn('review_series_superseded', {row['code'] for row in explicit['obligations']})
        report = cli.status_report(self.store)
        self.assertIn('review_series_superseded', {row['code'] for row in report['obligations']})
        self.assertFalse(report['ready'])
        for assignment in ('successor-first', 'successor-second'):
            self.assertEqual(review_continuation.plan_next(records, self.artifacts, assignment)['state'], 'run')
        summary = report_views.next_summary(report)
        self.assertEqual(report_views.status_summary(report)['review_slots'], dict(occupied=2, complete=0, incomplete=0, pending=2))
        self.assertIn('next_hint', summary)
        self.assertIn('post_obligations', summary)

    def test_ordinary_pending_next_names_a_current_assignment(self):
        self.prepare_reviews(bar_reviewer='first')
        self.change_review_currency()
        self.start_successors()
        report = cli.status_report(self.store)
        self.assertTrue(report['next'].get('next'), report['next'])
        action = report['next']['next'][0]
        self.assertEqual((action['command'], action['state']), ('review-run', 'run'))
        self.assertIn(action['payload']['assignment_id'], ('successor-first', 'successor-second'))
        summary = report_views.next_summary(report)
        self.assertEqual(summary['next_hint']['next_step']['command'], 'review-run')

    def test_current_finals_resolve_until_one_or_both_slots_refresh(self):
        self.prepare_reviews(bar_reviewer='first')
        self.change_review_currency()
        for reviewer in ('first', 'second'):
            output = self.build_review_output('result')
            output['value']['objection_findings'] = [self.make_finding('resolved'),
                                                   dict(self.make_finding('resolved'), id='new-slate-objection')]
            self.observe('result', reviewer, output, identifier='resolving-' + reviewer)
        report = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertTrue(report['ready'], report)
        self.assertNotIn('slot_outcomes', report)
        self.mutate(problem_claims.record_value, self.build_value([self.item], identifier='new-evidence'))
        self.start_successors(('first',))
        report = cli.status_report(self.store)
        self.assertIn('unsupported-implication', self.unresolved_ids(report))
        self.assertIn('slot_outcomes', report)
        self.assertEqual((report['slot_outcomes']['occupied'], len(report['slot_outcomes']['pending'])), (2, 1))
        self.start_successors(('second',))
        report = cli.status_report(self.store)
        self.assertIn('unsupported-implication', self.unresolved_ids(report))
        self.assertIn('slot_outcomes', report)
        self.assertEqual((report['slot_outcomes']['occupied'], len(report['slot_outcomes']['pending'])), (2, 2))
        self.assertNotIn('problem_review_pair_required', {row['code'] for row in report['obligations']})


class PartsOccupancyMixin:
    def setUp(self):
        original = fixtures.ProblemReviewTests.build_review_output

        def build_output(case, stage, *args, **kwargs):
            output = original(case, stage, *args, **kwargs)
            if stage == 'bar' and 'review-bar-first' not in case.read_records().get('value_review', {}):
                output['objections'] = [make_objection(case.supporting)]
            return output

        with patch.object(fixtures.ProblemReviewTests, 'build_review_output', build_output):
            super().setUp()
        self.case.expected['assessment_evidence'] = [self.case.supporting]

    def oversized(self, *args, **kwargs):
        super().oversized(*args, **kwargs)
        for entry in self.case.expected['openness_searches']:
            search = entry['search']
            search.update(profile='research', purpose='openness', cited_work_ids=[], found_work_ids=[], responses=[])
            self.case.put('literature_search', search['id'], search)

    def resolve_pair(self):
        for reviewer in ('first', 'second'):
            assignment = self.assign('original-' + reviewer, reviewer=reviewer)
            output = self.case.build_review_output('result')
            output['value']['objection_findings'] = [dict(id='synthetic-bound-gap', status='resolved',
                reason='The supplied induction step closes this gap.', evidence=[self.case.supporting])]
            self.record_final(assignment, output)
        report = decisions.assess_problem_reviews(self.case.read_records(), self.case.artifacts)
        self.assertTrue(report['ready'], report)
        self.assertNotIn('slot_outcomes', report)

    def report(self):
        return decisions.assess_problem_reviews(self.case.read_records(), self.case.artifacts)

    def test_no_head_one_pending_two_pending_and_complete_peer(self):
        # Empty ordinary roots retain the report shape; pending parts heads expose occupancy.
        self.assertNotIn('slot_outcomes', self.report())
        self.oversized()
        first = self.assign('first-pending')
        one = self.report()
        self.assertEqual((one['slot_outcomes']['occupied'], len(one['slot_outcomes']['pending'])), (1, 1))
        self.assertLessEqual({'review_task_pending', 'problem_review_pair_required'},
                             {row['code'] for row in one['obligations']})
        second = self.assign('second-pending', reviewer='second')
        both = self.report()
        self.assertEqual((both['slot_outcomes']['occupied'], len(both['slot_outcomes']['pending'])), (2, 2))
        self.assertNotIn('problem_review_pair_required', {row['code'] for row in both['obligations']})
        if self.use_parts_v2:
            assignment = first
            for turn in range(1, 40):
                if self.packet(assignment)['review_parts']['kind'] == 'final':
                    break
                self.invoke(assignment)
                assignment = self.assign('first-complete-' + str(turn), assignment['id'])
        else:
            _, assignment = self.finish_parts(first)
        output = self.final_output()
        output['value']['objection_findings'] = [dict(id='synthetic-bound-gap', status='resolved',
            reason='The supplied induction step closes this gap.', evidence=[self.case.supporting])]
        output['value']['assurances'][0]['status'] = 'failed'
        output['value']['rubric']['priority'] = 0
        self.record_final(assignment, output)
        peer = self.report()
        self.assertEqual((len(peer['slot_outcomes']['complete']), len(peer['slot_outcomes']['pending'])), (1, 1))
        self.assertLessEqual({'problem_assurance_pending', 'problem_rubric_below_threshold', 'review_task_pending'},
                             {row['code'] for row in peer['obligations']})
        # The second head remains the same actionable pending series.
        self.assertEqual(review_continuation.plan_next(self.case.read_records(), self.case.artifacts, second['id'])['state'], 'run')
        if self.use_parts_v2:
            assignment = second
            for turn in range(1, 40):
                if self.packet(assignment)['review_parts']['kind'] == 'final':
                    break
                self.invoke(assignment)
                assignment = self.assign('second-complete-' + str(turn), assignment['id'], 'second')
        else:
            _, assignment = self.finish_parts(second, reviewer='second')
        self.record_final(assignment, output)
        complete = self.report()
        expected_fields = {'ready', 'obligations', 'review_ids', 'bar_review_ids', 'problem_evidence_digest'}
        if self.use_parts_v2:
            expected_fields.add('slot_outcomes')
        self.assertEqual(set(complete), expected_fields)
        self.assertNotIn('review_task_pending', {row['code'] for row in complete['obligations']})
        self.assertNotIn('synthetic-bound-gap', {row.get('objection_id') for row in complete['obligations']})

    def test_resolved_pair_reopens_objections_for_one_then_two_successors_and_after_post(self):
        self.resolve_pair()
        self.oversized()
        first = self.assign('refreshed-first', reviewer='first')
        self.invoke(first)
        report = cli.status_report(self.case.store)
        self.assertIn('synthetic-bound-gap', {row.get('objection_id') for row in report['obligations']})
        self.assertIn('slot_outcomes', report)
        self.assertEqual((report['slot_outcomes']['occupied'], len(report['slot_outcomes']['pending'])), (2, 1))
        second = self.assign('refreshed-second', reviewer='second')
        self.invoke(second)
        report = cli.status_report(self.case.store)
        self.assertIn('slot_outcomes', report)
        self.assertEqual((report['slot_outcomes']['occupied'], len(report['slot_outcomes']['pending'])), (2, 2))
        self.assertNotIn('problem_review_pair_required', {row['code'] for row in report['obligations']})
        self.assertIn(report['next']['code'], ('problem_evidence_part_pending', 'review_parts_pending'))
        for assignment in (first, second):
            self.assertEqual(review_continuation.plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])['state'], 'continue')
        old_ids = ['review-original-first', 'review-original-second']
        explicit = decisions.assess_problem_reviews(self.case.read_records(), self.case.artifacts, review_ids=old_ids)
        self.assertIn('review_series_superseded', {row['code'] for row in explicit['obligations']})
        summary = report_views.next_summary(report)
        self.assertEqual(report_views.status_summary(report)['review_slots'], dict(occupied=2, complete=0, incomplete=0, pending=2))
        self.assertIn('next_hint', summary)
        self.assertIn('post_obligations', summary)
        findings = problem_delivery.assess_post(self.case.read_records(), self.case.artifacts)['obligations']
        statement = self.case.statement
        self.case.mutate(problem_claims.record_post_external, dict(id='external-post',
            problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
            statement_digest=problem_claims.compute_statement_digest(statement),
            reason='The owner authorized publication.', evidence='The publication instruction.'))
        posted = cli.status_report(self.case.store)
        self.assertEqual(posted['next']['code'], 'problem_already_posted')
        self.assertEqual(posted['after_post'], [dict(row, reason=row.get('reason', row['explanation']))
                                              for row in findings])


class LegacyPartsOccupancyTests(PartsOccupancyMixin, ResultPartsCase):
    use_parts_v2 = False

    def test_two_refreshed_legacy_slots_remain_pending_at_part_turn_21(self):
        self.resolve_pair()
        # Twenty successful parts still leave originals to read at turn 21.
        self.oversized(count=22, characters=50000, limit=110000)
        heads = []
        for reviewer in ('first', 'second'):
            assignment = self.assign('long-' + reviewer, reviewer=reviewer)
            for turn in range(1, 21):
                self.assertEqual(self.packet(assignment)[KEY]['stage'], 'part')
                self.invoke(assignment)
                assignment = self.assign('long-' + reviewer + '-' + str(turn + 1), assignment['id'], reviewer)
            self.assertEqual((self.packet(assignment)[KEY]['turn'], self.packet(assignment)[KEY]['stage']), (21, 'part'))
            self.invoke(assignment)
            heads.append(assignment)
        report = cli.status_report(self.case.store)
        self.assertIn('slot_outcomes', report)
        self.assertEqual((report['slot_outcomes']['occupied'], len(report['slot_outcomes']['pending'])), (2, 2))
        self.assertNotIn('problem_review_pair_required', {row['code'] for row in report['obligations']})
        self.assertIn('synthetic-bound-gap', {row.get('objection_id') for row in report['obligations']})
        self.assertEqual(report['next']['code'], 'problem_evidence_part_pending')
        for assignment in heads:
            plan = review_continuation.plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])
            self.assertEqual((plan['state'], plan['command']), ('continue', 'review-assignment'))


class FreshPartsOccupancyTests(PartsOccupancyMixin, PartsResultCase):
    pass
