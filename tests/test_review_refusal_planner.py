"""Executable next steps after real stored contract refusals."""

import copy
import unittest
from unittest.mock import Mock, patch

from research_harness import review_continuation as continuation, review_protocol as protocol
from research_harness import review_refusal as refusal, review_refusal_notice as notices
from research_harness.errors import ResearchError
import test_review_refusal_replacement as fixtures
import test_review_output_correction as correction_fixtures


class RefusalPlannerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RefusalReplacementTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case

    def plan(self, assignment):
        return continuation.plan_next(self.case.read_records(), self.case.artifacts, assignment['id'])

    def admit(self, plan):
        self.assertEqual(plan['state'], 'replace', plan)
        return self.case.mutate(protocol.record_assignment, plan['payload'])['result']

    def test_first_refusal_plans_and_executes_replacement_then_records(self):
        assignment, _ = self.fixture.build_refused()
        before = copy.deepcopy(self.case.read_records())
        plan = self.plan(assignment)
        self.assertEqual(plan['payload']['context']['output_refusal'], {'assignment_id': assignment['id']})
        self.assertEqual(self.case.read_records(), before)
        replacement = self.admit(plan)
        output = self.case.build_review_output('bar')
        self.fixture.invoke(replacement, output)
        self.fixture.record(replacement, output)
        self.assertEqual(self.plan(replacement)['state'], 'done')

    def test_two_replacements_end_at_registered_bar_handoff(self):
        assignment, output = self.fixture.build_refused()
        for number in (1, 2):
            assignment = self.admit(self.plan(assignment))
            self.fixture.invoke(assignment, output, 'refused-' + str(number))
            with self.assertRaises(ResearchError):
                self.fixture.record(assignment, output)
        plan = self.plan(assignment)
        self.assertEqual(plan['state'], 'handoff', plan)
        self.assertEqual(plan['code'], 'review_slot_exhausted')
        self.assertEqual(plan['details']['step']['id'], 3)
        self.assertEqual(plan['details']['step']['payload']['context'], {})
        payload = dict(plan['details']['step']['payload'], id='new-reviewer', route_id='route', reviewer_id='third')
        fresh = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(fresh['slot_id'], assignment['slot_id'])

    def test_current_check_builds_notice_and_metadata_overflow_uses_role_step(self):
        assignment, _ = self.fixture.build_refused()
        original = refusal.classify_observed_output
        def changed(*args):
            diagnosis = original(*args)
            diagnosis['details']['message'] = 'x' * 6500
            return diagnosis
        with patch.object(refusal, 'classify_observed_output', side_effect=changed):
            plan = self.plan(assignment)
        self.assertEqual(plan['state'], 'handoff', plan)
        self.assertEqual(plan['code'], 'review_refusal_notice_metadata_too_large')
        from research_harness.stop_classes import STOP_REGISTRY
        self.assertEqual(STOP_REGISTRY[plan['code']]['next'], STOP_REGISTRY['review_slot_exhausted']['next'])
        self.assertGreater(plan['details']['characters'], notices.REFUSAL_ENTRY_CHARACTERS)
        self.assertEqual(plan['details']['step']['id'], 3)


    def test_ordinary_result_with_unexpected_reassessment_is_replaced_without_identical_run(self):
        for reviewer in ('first', 'second'):
            self.case.observe('bar', reviewer)
        assignment = self.case.assign('result', 'first', identifier='ordinary-result')
        output = self.case.build_review_output('result')
        output['reassessment'] = dict(assignment_id='no-predecessor', findings=[])
        self.fixture.invoke(assignment, output)
        with self.assertRaises(ResearchError) as caught:
            self.fixture.record(assignment, output)
        self.assertEqual(caught.exception.code, 'review_reassessment_mismatch')
        replacement = self.admit(self.plan(assignment))
        self.assertEqual(replacement['context']['output_refusal'], {'assignment_id': assignment['id']})

    def test_a7_whole_then_bounded_and_capacity_floor_end_at_role_step(self):
        from research_harness import review_failure_class as failures
        assignment, _ = self.fixture.build_refused()
        real = notices.select_refusal_notice
        trials = []
        def refuse_notice(*args, **options):
            measured = real(*args, **options)
            trials.append(measured['notice']['form'])
            return dict(measured, notice=None)
        with patch.object(notices, 'select_refusal_notice', side_effect=refuse_notice), \
                patch.object(failures, 'plan_capacity_recovery', wraps=failures.plan_capacity_recovery) as lower:
            plan = self.plan(assignment)
        self.assertEqual(trials, ['whole'])
        self.assertEqual(lower.call_args.args[-1], 'input_over_limit')
        self.assertEqual(plan['state'], 'handoff', plan)
        self.assertEqual(plan['details']['step']['id'], 3)

    def test_a7_lower_level_carries_bounded_notice_and_admits_the_plan(self):
        from research_harness import review_failure_class as failures
        assignment, _ = self.fixture.build_refused()
        packet = protocol._read_json(self.case.artifacts, assignment['packet'])
        preflight = dict(protocol='synthetic-smaller-task-proof')
        recovery = dict(kind='replace', reason='capacity', payload={}, preflight=preflight, details={})
        lower = dict(state='replace', packet=packet, prompt=assignment['prompt'], preflight=preflight)
        original = notices.select_refusal_notice
        forms = []
        def measure(*args, **options):
            forms.append(options.get('forms'))
            measured = original(*args, **options)
            if options.get('forms') != ('bounded',):
                return dict(measured, notice=None)
            return measured
        with patch.object(notices, 'select_refusal_notice', side_effect=measure), \
                patch.object(failures, 'plan_capacity_recovery', return_value=recovery), \
                patch.object(failures, 'plan_output_replacement', return_value=lower):
            plan = self.plan(assignment)
            self.assertEqual(plan['details']['form'], 'bounded')
            replacement = self.admit(plan)
        self.assertEqual(forms, [('whole', 'bounded'), ('bounded',), ('whole', 'bounded'), ('bounded',)])
        self.assertEqual(self.fixture.read_notice(replacement)['form'], 'bounded')
        output = self.case.build_review_output('bar')
        self.fixture.invoke(replacement, output)
        self.fixture.record(replacement, output)

    def test_a7_large_output_selects_a_real_bounded_request(self):
        output = self.case.build_review_output('bar')
        output['limitations'] = ['A synthetic limitation. ' + 'x' * 100000]
        with patch.object(self.case, 'build_review_output', return_value=output):
            assignment, _ = self.fixture.build_refused()
        plan = self.plan(assignment)
        self.assertEqual(plan['state'], 'replace', plan)
        self.assertEqual(plan['details']['form'], 'bounded')
        replacement = self.admit(plan)
        self.assertEqual(self.fixture.read_notice(replacement)['form'], 'bounded')
        corrected = self.case.build_review_output('bar')
        self.fixture.invoke(replacement, corrected)
        self.fixture.record(replacement, corrected)


class CorrectionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.case = correction_fixtures.CorrectionTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fixture = self.case.fixture
        self.assignment = self.case.case.assignment

    def plan(self):
        return continuation.plan_next(self.fixture.read_records(), self.fixture.artifacts, self.assignment['id'])

    def test_new_reassessment_reruns_once_then_corrects_and_records(self):
        self.case.case.observe(self.case.output, 'refused-one')
        with self.assertRaises(ResearchError):
            self.fixture.mutate(correction_fixtures.decisions.record_value_review, self.case.case.payload(self.case.output))
        self.assertEqual(self.plan()['state'], 'run')
        self.case.case.observe(self.case.output, 'refused-two')
        with self.assertRaises(ResearchError):
            self.fixture.mutate(correction_fixtures.decisions.record_value_review, self.case.case.payload(self.case.output))
        correction_plan = self.plan()
        self.assertEqual(correction_plan['command'], 'review-output-correction')
        self.assertIn('preflight', correction_plan['details'])
        self.case.correct(self.case.delta())
        plan = self.plan()
        self.assertEqual(plan['command'], 'value-review', plan)
        self.fixture.mutate(correction_fixtures.decisions.record_value_review, plan['payload'])

    def test_declined_correction_gets_replacement_and_is_not_offered_again(self):
        self.case.prepare_refused()
        self.assertEqual(self.plan()['command'], 'review-output-correction')
        self.case.correct(dict(correction='declined', reason='The exact delta cannot be supplied.'))
        for _ in range(2):
            plan = self.plan()
            self.assertEqual(plan['state'], 'replace', plan)
        replacement = self.fixture.mutate(protocol.record_assignment, plan['payload'])['result']
        notice = protocol._read_json(self.fixture.artifacts, replacement['packet'])['output_refusal']
        self.assertEqual(notice['form'], 'bounded')


    def test_pending_correction_blocks_planner_and_replacement_admission(self):
        self.case.prepare_refused()
        self.fixture.put('review_output_correction_invocation', 'pending',
            dict(id='pending', assignment_id=self.assignment['id'], route_id='route'))
        plan = self.plan()
        self.assertEqual(plan['state'], 'wait', plan)
        self.assertEqual(plan['payload']['operation'], 'review.output_correction')
        context = dict(self.assignment['context'], output_refusal={'assignment_id': self.assignment['id']})
        with self.assertRaises(ResearchError) as caught:
            self.fixture.assign('result', 'first', identifier='forbidden', **context)
        self.assertEqual(caught.exception.code, 'review_invocation_pending')

    def test_accepted_correction_blocks_direct_replacement_admission(self):
        self.case.prepare_refused()
        self.case.correct(self.case.delta())
        context = dict(self.assignment['context'], output_refusal={'assignment_id': self.assignment['id']})
        with self.assertRaises(ResearchError) as caught:
            self.fixture.assign('result', 'first', identifier='forbidden', **context)
        self.assertEqual(caught.exception.code, 'review_refusal_replacement_ineligible')


class PlannerChainRowsTests(unittest.TestCase):
    def execute_planned_replacements(self, test_class, method):
        fixture = test_class()
        self.addCleanup(fixture.doCleanups)
        real = protocol.record_assignment
        checked = []
        def admit(store, payload, **identity):
            binding = payload.get('context', {}).get('output_refusal')
            if binding is not None:
                records = store.snapshot()['records']
                plan = continuation.plan_next(records, store.build_artifact_store(), binding['assignment_id'])
                self.assertEqual(plan['state'], 'replace', plan)
                self.assertEqual(plan['payload']['context']['output_refusal'], binding)
                # The reused incident fixture pins checks made by admission itself. Planning has its own checks.
                if isinstance(refusal.classify_observed_output, Mock):
                    refusal.classify_observed_output.reset_mock()
                payload = dict(plan['payload'], id=payload['id'])
                checked.append(binding['assignment_id'])
            return real(store, payload, **identity)
        with patch.object(protocol, 'record_assignment', side_effect=admit):
            getattr(fixture, method)()
        self.assertTrue(checked)

    def test_parts_read_combine_check_and_final_execute_the_planned_row(self):
        import test_review_refusal_replacement_parts as parts_fixtures
        self.execute_planned_replacements(parts_fixtures.PartsReplacementTests,
            'test_every_parts_kind_can_be_replaced_and_the_corrected_series_records')

    def test_a7_real_refused_read_descends_and_admits_its_bounded_notice(self):
        import review_parts_fixtures as parts_fixtures
        from codex_route_fixtures import limit_codex_input
        from research_harness import review_parts as parts
        from research_harness.review_problem_parts import _measure
        fixture = parts_fixtures.PartsResultCase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.oversized(count=5, characters=30000, room=65000)
        first = fixture.assign()
        packet = fixture.packet(first)
        self.assertEqual(parts.find_kind(packet), 'read')
        output = dict(parts_fixtures.make_output(packet), synthetic_refusal_context='x' * 100000)
        output.pop('kind')
        fixture.invoke(first, output)
        records = fixture.case.read_records()
        request = protocol.build_assignment_request(records, fixture.case.artifacts, first)
        limit_codex_input(fixture, _measure(records['review_route']['route'], request) + 10000)
        plan = continuation.plan_next(fixture.case.read_records(), fixture.case.artifacts, first['id'])
        self.assertEqual(plan['state'], 'replace', plan)
        self.assertEqual(plan['details']['form'], 'bounded')
        replacement = fixture.case.mutate(protocol.record_assignment, plan['payload'])['result']
        lower = fixture.packet(replacement)
        self.assertLess(lower[parts.KEY]['unit_characters'], packet[parts.KEY]['unit_characters'])
        fixture.invoke(replacement, parts_fixtures.make_output(lower))
        self.assertEqual(continuation.plan_next(fixture.case.read_records(), fixture.case.artifacts,
                                              first['id'])['state'], 'continue')

    def test_focused_adjudication_executes_the_planned_row(self):
        import test_review_refusal_replacement_parts as parts_fixtures
        self.execute_planned_replacements(parts_fixtures.PartsReplacementTests,
            'test_ordinary_focused_adjudication_replaces_its_refusal_and_records')

    def test_native_parts_executes_the_planned_row(self):
        import test_review_refusal_replacement_parts as parts_fixtures
        self.execute_planned_replacements(parts_fixtures.PartsReplacementTests,
            'test_native_parts_replacement_and_next_turn_preserve_the_implicit_slot')

    def test_verification_parts_executes_the_planned_row(self):
        import test_review_refusal_replacement_parts as parts_fixtures
        self.execute_planned_replacements(parts_fixtures.PartsReplacementTests,
            'test_verification_parts_initial_replacement_keeps_its_slot_and_old_head_is_refused')

    def test_repaired_bar_final_executes_the_planned_row(self):
        import test_review_refusal_replacement_incident as incident_fixtures
        self.execute_planned_replacements(incident_fixtures.PartOffsetReplacementIncidentTests,
            'test_revision_200_repaired_bar_gets_part_offsets_and_records_in_its_slot')

    def test_c12_a_bar_abstract_link_executes_planned_replacement_and_records(self):
        self.execute_planned_replacements(fixtures.CitationReplacementTests,
            'test_abstract_link_replacement_delivers_two_accepted_forms_and_records')

    def test_c12_b_bar_mixed_links_executes_replacement_and_reading_handoff(self):
        self.execute_planned_replacements(fixtures.CitationReplacementTests,
            'test_abstract_beside_fulltext_replacement_keeps_one_reading_handoff')
