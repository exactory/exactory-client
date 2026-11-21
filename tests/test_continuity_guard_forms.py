"""G1, G4, K7 and K8 for the chain kinds of 0.92.0: a v7 final in pages, the math protocol v2, the session rules v4
and the parts protocol v3. Every plan is executed with the real command; only the transport and the clock are stubs."""

import unittest

from ci import continuity_guard as guard
from ci.evidence_tests import require_evidence_tests
import continuity_forms_fixtures as forms
import continuity_stage3_fixtures as stage3
from research_harness import review_failure_class as failures, review_route_capacity as capacity, stop_classes
from research_harness.review_continuation import _describe_chain
from test_series_code_reach import record_reachable_errors


class FormSizeRowTests(unittest.TestCase):
    """K8: each turn of each new kind, protocol and form, and each size outcome, ends at a replacement on the same
    route and reviewer that passes the preflight, or at the decision stop with every answer executed to its end."""

    def execute_size_rows(self, family):
        """Each turn of the family is built once and each of its six rows works on a copy of its store; the number
        of turns and rows, each a real command on a store, is what needs the time of these tests (parts v3 the most,
        because each command of its series reads two large bodies)."""
        turns = [turn for name, turn in guard.list_form_turns(capacity.list_capacity_recovery_rows())
                 if name == family and (name, turn) not in guard.FORM_SIZE_GAPS]
        executed = []
        for form in forms.iterate_turns(self, family, turns):
            turn = form.turn
            for outcome, detail in guard.SIZE_OUTCOMES:
                with self.subTest(turn=turn, outcome=outcome, detail=detail), forms.fork_store(form):
                    with record_reachable_errors('plan_next'):
                        executed.append(forms.build_size_row(self, form, outcome, detail))
        turns = [row for row in guard.list_form_turns(capacity.list_capacity_recovery_rows()) if row[0] == family]
        gaps = {key: reason for key, reason in guard.FORM_SIZE_GAPS.items() if key[0] == family}
        self.assertEqual(guard.validate_form_size_rows(executed, turns, gaps), [])
        return executed

    def test_k8_table_requires_a_complete_row_or_a_named_gap_for_each_turn(self):
        turns = [('family', 'first'), ('family', 'second')]

        def build_row(outcome, detail):
            call = dict(id='attempt', request_digest='digest', request='request', recorded_revision=1)
            boundary = dict(kind='assignment', id='following', prior_head='head', eligible=True, preflight=dict(fits=True),
                            record=dict(id='following', recorded_revision=2), subject={})
            return dict(family='family', turn='first', outcome=outcome, detail=detail, attempt_id='attempt',
                        request_digest='digest', call=call, accepted_commands=['review-assignment'],
                        assignment_id='head', subject={}, boundary=boundary)
        rows = [build_row(outcome, detail) for outcome, detail in guard.SIZE_OUTCOMES]
        gaps = {('family', 'second'): 'No builder reaches this turn.'}
        self.assertEqual(guard.validate_form_size_rows(rows, turns, gaps), [])
        self.assertTrue(guard.validate_form_size_rows(rows[:-1], turns, gaps))
        self.assertTrue(guard.validate_form_size_rows(rows, turns, {}))
        self.assertTrue(guard.validate_form_size_rows(rows, turns, {('family', 'second'): ''}))
        for extra in (('family', 'first'), ('family', 'third')):
            self.assertTrue(guard.validate_form_size_rows(rows, turns, {**gaps, extra: 'A gap beside the rows.'}))
        rows[0]['boundary'].pop('preflight')
        self.assertTrue(guard.validate_form_size_rows(rows, turns, gaps))

    def test_k8_session_v4_request_final_and_pages(self):
        self.execute_size_rows('session-v4')

    def test_k8_parts_v3_read_and_pages(self):
        self.execute_size_rows('parts-v3')

    def test_k8_v7_page_and_closing(self):
        self.execute_size_rows('v7-pages')

    def test_k8_math_v2_reading_and_final(self):
        self.execute_size_rows('math-v2')


# The turn of each family on which the rows of one plan run; the other turns of the family take the same plan with
# the same command (the exploration of the lane report), so they are folded into this row.
FINAL_TURNS = {'session-v4': 'final', 'parts-v3': 'closing', 'v7-pages': 'closing', 'math-v2': 'final'}
FIRST_TURNS = {'session-v4': 'request', 'parts-v3': 'read', 'v7-pages': 'page', 'math-v2': 'reading'}
CHAIN_KINDS = {'session-v4': 'session', 'parts-v3': 'parts_v2:problem_result', 'v7-pages': 'reading',
               'math-v2': 'reading'}


class FormOutcomeRowTests(unittest.TestCase):
    """G1 for the outcomes that are not size limits and for the conditions of D2 4.6 that a chain kind can change."""

    def execute_families(self, method):
        for family in guard.FORM_FAMILIES.values():
            with self.subTest(family=family):
                try:
                    with record_reachable_errors('plan_next'):
                        method(family)
                finally:
                    self.doCleanups()

    def test_g1_last_sequence_of_an_outcome_executes_both_answers_to_their_end(self):
        """Three sequences of failures on the final turn of four families, and both answers each; the final turns
        (a closing page of a series and of a v7 chain) are what needs the time of this test."""
        def execute(family):
            form = forms.build_turn(self, family, FINAL_TURNS[family])
            self.assertEqual(_describe_chain(form.assignment()), CHAIN_KINDS[family])
            respond = stage3.fail_outcome_request(form.case, 'answer_malformed')
            stop, counts, waits = forms.drive_failures(self, form, respond, 'answer_malformed')
            limit = failures.OUTCOME_ROWS['answer_malformed']['attempt_limit']
            self.assertEqual(counts, {sequence: limit for sequence in range(1, failures.SEQUENCE_LIMIT + 1)})
            self.assertEqual(set(waits), {'review_sequence_wait'})
            self.assertEqual((stop['state'], stop['code']), ('stop', 'review_turn_failures_exhausted'))
            journeys = forms.execute_stop_answers(self, form, stop)
            # "Keep this route" ends at a recorded action; "Move to another route" at a new head of the slot or chain.
            self.assertIn('action', {journey['end']['kind'] for journey in journeys})
        self.execute_families(execute)

    def test_g1_runtime_outcomes_execute_their_handoff_steps(self):
        """Seven failed runs and a route step on four families; the seven runs are what needs the time."""
        def execute(family):
            form = forms.build_turn(self, family, FIRST_TURNS[family])
            for outcome, runs in (('runtime_unavailable', 7), ('runtime_stale', 1)):
                with self.subTest(outcome=outcome), forms.fork_store(form):
                    plan, counts, _ = forms.drive_failures(self, form, stage3.fail_outcome_request(form.case, outcome), outcome)
                    self.assertEqual(counts, {1: runs})
                    self.assertEqual(plan['state'], 'handoff', plan)
                    self.assertIn(plan['code'], stop_classes.STOP_REGISTRY)
                    if outcome == 'runtime_stale':
                        self.assertEqual(plan['details']['renewal'], dict(admitted=False, route_id=None, lacks='record'))
                    forms.execute_handoff(self, form, plan)
        self.execute_families(execute)

    def test_g1_provider_refusal_takes_a_lower_level_on_the_same_route(self):
        def execute(family):
            form = forms.build_turn(self, family, FIRST_TURNS[family])
            plan, counts, _ = forms.drive_failures(self, form, forms.refuse_provider, 'provider_refused')
            self.assertEqual(counts, {1: failures.PROVIDER_REFUSAL_ATTEMPT_LIMIT})
            self.assertEqual(plan['state'], 'replace', plan)
            forms.check_replacement(self, form, plan)
        self.execute_families(execute)

    def test_g1_session_v4_and_series_v3_on_a_stale_route_repair_to_a_head_that_runs(self):
        """The rows that the merge of lane route-plan left to this lane: a continuation of a session v4 and of a
        series v3 whose route record pins another runtime takes the stale-route procedure (steps 8, 7, 1) to a
        context repair on a route record with the installed runtime, and the repaired head runs."""
        from test_review_route_renewal import make_codex_stale
        for family in ('session-v4', 'parts-v3'):
            with self.subTest(family=family):
                try:
                    form = forms.build_turn(self, family, FIRST_TURNS[family])
                    forms.run_head(form, forms.respond_with(form.answer()))
                    self.assertEqual(form.plan()['state'], 'continue')
                    make_codex_stale(self)
                    plan = form.plan()
                    self.assertEqual((plan['state'], plan['command'], plan['code']),
                                     ('handoff', 'review-context', 'review_route_runtime_stale'))
                    self.assertEqual(plan['details']['renewal'], dict(admitted=False, route_id=None, lacks='record'))
                    self.assertEqual([step['id'] for step in plan['details']['steps']], [8, 7, 1])
                    handoff = forms.execute_handoff(self, form, plan)
                    self.assertEqual(handoff['commands'], ['review-context', 'review-route', 'review-probe',
                                                           'review-assignment'])
                    repaired = handoff['end']['record']
                    self.assertNotEqual(repaired['route_id'], 'route')
                    self.assertTrue(handoff['end']['preflight'])
                finally:
                    self.doCleanups()

    def test_g1_stop_boundary_releases_to_a_run_of_the_same_head(self):
        def execute(family):
            form = forms.build_turn(self, family, FIRST_TURNS[family])
            forms.matrix.execute(form.case, 'review-stop', dict(id='stop', action='stop', assignment_id=form.head,
                mode='now', reason='The owner stops this chain.'))
            plan = form.plan()
            self.assertEqual((plan['state'], plan['command'], plan['code']),
                             ('handoff', 'review-stop', 'review_chain_stopped'))
            step = plan['details']['step']
            forms.matrix.execute(form.case, step['command'], dict(step['payload'], id='release',
                reason='The owner resumes this chain.'))
            self.assertEqual(form.plan()['state'], 'run')
            forms.run_head(form, forms.respond_with(form.answer()))
            self.assertNotEqual(form.plan()['state'], 'run')
        self.execute_families(execute)



# The turns whose floor ends in the decision stop of D2 (K7, point 3 of the brief of record): a page, a closing page and
# a condensing turn of each family that has them. The v7 page and the v7 condensing turn have their rows in 0.91.0 and
# in lane d2-page-replacement (FLOOR_EVIDENCE).
FLOOR_TURNS = {'session-v4': ('page', 'closing', 'combine'), 'parts-v3': ('page', 'closing'),
               'v7-pages': ('closing',), 'math-v2': ('page', 'closing', 'combine')}
FLOOR_EVIDENCE = (
    'test_openness_page_replacement.PageReplacementTests.test_floor_retries_once_then_executes_export_and_route_answers',
    'test_continuity_guard_stage3.Stage3MatrixTests.test_g1_k8_v7_positions_execute_each_size_outcome_to_next_assignment',
)


class FormRequiredStopTests(unittest.TestCase):
    """K7: each required stop of the entries that 0.92.0 completes has a registry row, its fixture sends no request
    to the transport, and the steps of every answer are executed to their end."""

    def execute_floors(self, family):
        """Each replacement is a smaller task on the same route until the floor, whose decision stop is executed
        with every answer. The walk of a turn down its levels is what needs the time of these tests."""
        for form in forms.iterate_turns(self, family, FLOOR_TURNS[family]):
            with self.subTest(turn=form.turn), forms.fork_store(form), record_reachable_errors('plan_next'):
                replacements, stop, journeys = forms.walk_to_floor(self, form)
                self.assertIn(stop['code'], stop_classes.STOP_REGISTRY)
                self.assertEqual(stop_classes.classify_stop(stop['code']), 'decision')
                # A stop whose only answer the store refuses is a finding of the report (FORM_DEFECTS).
                refused = guard.FORM_DEFECTS.get((family, form.turn, 'Move to another route'))
                self.assertEqual(bool(journeys), refused is None)

    def test_k7_floor_evidence_of_the_v7_page_and_condensing_turn(self):
        self.assertEqual(require_evidence_tests(FLOOR_EVIDENCE), {})

    def test_k7_session_v4_page_closing_and_condensing_floors_execute_every_answer(self):
        self.execute_floors('session-v4')

    def test_k7_parts_v3_page_and_closing_floors_execute_every_answer(self):
        self.execute_floors('parts-v3')

    def test_k7_v7_closing_floor_executes_every_answer(self):
        self.execute_floors('v7-pages')

    def test_k7_math_v2_page_closing_and_condensing_floors_execute_every_answer(self):
        self.execute_floors('math-v2')


    def test_k7_route_below_the_minimum_of_a_final_stops_before_any_call_with_pages_installed(self):
        """Entry A5: a new slot of a session v4 (rule V5, reason session_below_floor) and the first turn of a v7
        chain with final pages, on a route below the minimum of a final."""
        for family in ('session-v4', 'v7-pages'):
            with self.subTest(family=family):
                try:
                    form, payload, refusal = forms.refuse_below_floor(self, family)
                    self.assertEqual(refusal.code, 'review_route_capacity_below_floor')
                    self.assertIn(refusal.code, stop_classes.STOP_REGISTRY)
                    if family == 'session-v4':
                        self.assertEqual(refusal.details['reason'], 'session_below_floor')
                    decision = refusal.details.get('decision') or dict(then=refusal.details['then'])
                    stop = dict(code=refusal.code, head_id=None, details=dict(decision=decision))
                    journeys = forms.execute_admission_answers(self, form, stop, payload)
                    self.assertTrue(all(row['end']['record']['role'] == payload['role'] for row in journeys))
                finally:
                    self.doCleanups()


    @record_reachable_errors('plan_next')
    def test_k7_math_v1_projection_stop_ends_in_a_chain_of_the_math_protocol_v2(self):
        """review_chain_projection_over_limit on a final of the math protocol v1 (the exact measure of ruling 24.2):
        every answer is executed, and the answer that starts a new chain starts it under the math protocol v2."""
        import test_openness_reading_math_v2 as math
        from research_harness import openness_reading as reading
        fixture = forms.prepare_unit_fixture(self, math.MathV2CarriedTests,
            'test_a25_stopped_v1_turns_are_carried_without_repeating_units_to_a_recorded_review')
        fixture.seed_stopped_v1()
        form = forms.FormTurn('math-v2', 'v1-final', fixture, fixture, 'math-final')
        self.assertEqual(reading.find_protocol(form.packet()), reading.MATH_PROTOCOL)
        # The input limit under which the exact request of the v1 final stopped; the answers then run on the route
        # capacity of the fixture, as the unit test of the carried turns does.
        fixture.limit_input(1000)
        stop = form.plan()
        fixture.limit_input(1300000)
        self.assertEqual(stop['state'], 'stop', stop)
        self.assertIn(stop['code'], stop_classes.STOP_REGISTRY)
        journeys = forms.execute_stop_answers(self, form, stop)
        chains = [row['end'] for row in journeys if row['end']['kind'] == 'assignment'
                  and row['end']['record'].get('reading_chain', {}).get('turn') == 1]
        self.assertTrue(chains, stop)
        self.assertEqual({end['protocol'] for end in chains}, {math.MATH_V2})



# The tests of the unit lanes that hold the positions of a split (finding 3 of the Astra review of the plan, part c:
# a failed page followed by its authenticated split pages passes the replacement check, a child page that splits
# again has its own position, and two over-limit attempts in one unchanged position fail) and the capacity split of
# a page that carries a refusal.
G4_EVIDENCE = (
    'test_verification_criterion.StoredSessionVerificationTests.test_real_page_split_and_nested_split_keep_new_positions',
    'test_verification_criterion.StoredSessionVerificationTests.test_real_parent_and_child_page_failures_both_count_in_the_bounds',
    'test_verification_criterion.StoredSessionVerificationTests.test_real_floor_one_extra_run_is_one_failure_and_second_fails_position',
    'test_verification_criterion.VerificationCriterionTests.test_two_over_limit_attempts_in_one_position_and_recorded_limit_answer_fail',
    'test_verification_criterion.StoredSessionVerificationTests.test_real_closing_split_gives_each_condensing_turn_a_position',
    'test_openness_page_replacement.PageReplacementTests.test_capacity_split_after_refusal_keeps_notice_and_starts_new_page_positions',
)


class FormCountingTests(unittest.TestCase):
    """G4 with the positions of a final in pages: the pages of a split are new positions, a capacity replacement
    spends none of the two replacements that carry a refusal, and the executable plans end (the floor rows)."""

    def test_g4_page_split_starts_new_positions_without_spending_a_refusal_replacement(self):
        self.assertEqual(require_evidence_tests(G4_EVIDENCE), {})
        for family in guard.FORM_FAMILIES.values():
            with self.subTest(family=family):
                try:
                    form = forms.build_turn(self, family, 'page')
                    plan, counts, _ = forms.drive_failures(self, form, forms.fail_size_request('output_over_cap', None),
                                                           'output_over_cap', limit=4)
                    self.assertEqual(plan['state'], 'replace', plan)
                    before = forms.list_page_targets(form)
                    boundary = forms.check_replacement(self, form, plan)
                    after = forms.list_page_targets(form, boundary['id'])
                    self.assertLess(len(after), len(before))
                    self.assertTrue(set(after) < set(before))
                    records = form.records()
                    following = records['review_assignment'][boundary['id']]
                    position = failures.count_position(records, form.case.artifacts, following)
                    self.assertEqual(position['replacements']['output_refused'], 0)
                    inherited = guard.FORM_DEFECTS.get((family, 'page', 'split')) is not None
                    self.assertEqual(form.head in failures.list_turn_assignments(records, following), inherited)
                    self.assertEqual(position['failed_attempts'], counts[1] if inherited else 0, position)
                finally:
                    self.doCleanups()



# G8 for the math chain: the selection of the turns of a v1 chain (lane d1-math-carried) and the v2 chain that
# carries them to a recorded review.
MATH_CARRY_EVIDENCE = (
    'test_openness_reading_math_v2.MathV2CarriedTests.test_a25_stopped_v1_turns_are_carried_without_repeating_units_to_a_recorded_review',
    'test_openness_carried_math.MathStoppedChainTests.test_g8_three_readings_survive_ruling_24_2_final_stop_and_keep_record_digests',
)


class FormJourneyTests(unittest.TestCase):
    """Point 5 of the brief of record: the handoffs between the forms, as journeys to a recorded review."""

    @record_reachable_errors('plan_next')
    def test_g8_v5_and_v6_finals_over_the_input_limit_hand_off_to_a_v7_chain_in_pages_that_records(self):
        """A chain of v5 and one of v6 whose final the provider refused for its input, over a statement of nine
        formulations: the handoff export starts a v7 chain that carries the readings, and whose final, in pages,
        records a finding. The two old chains of four large texts are what needs the time of this test."""
        import test_openness_reading_v7_refusal as refusal
        from test_openness_page_replacement import PageReplacementTests
        from unittest.mock import patch
        from research_harness import openness_reading as reading, openness_reading_v7 as v7
        from research_harness.review_continuation import plan_next
        self.assertEqual(require_evidence_tests(MATH_CARRY_EVIDENCE), {})
        for historical in (True, False):
            with self.subTest(protocol='v5' if historical else 'v6'):
                try:
                    case = forms.prepare_unit_fixture(self, refusal.LegacySuccessorTests,
                                                      'test_k7_v5_input_limit_exports_to_v7_carries_and_records')
                    pages = patch.object(v7, 'V7_FINAL_FORMS', ('request', 'pages'))
                    pages.start()
                    self.addCleanup(pages.stop)
                    case.select_targets(9)
                    case.historical_v5 = historical
                    with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS',
                                      (reading.PROTOCOL_V5,) if historical else (reading.PROTOCOL_V6,)):
                        head = case.fail_legacy_final(
                            expected_protocol=reading.PROTOCOL_V5 if historical else reading.PROTOCOL_V6)
                    with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
                        plan = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                        self.assertEqual((plan['state'], plan['command']), ('handoff', 'export'), plan)
                        self.assertIn(plan['code'], stop_classes.STOP_REGISTRY)
                        reference, receipt = case.execute_export(plan)
                        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
                        self.assertTrue(receipt['carried_readings']['chains'])
                        current, stages = 'successor', []
                        for number in range(40):
                            packet = case.read_packet(current)
                            stages.append(reading.find_stage(packet))
                            case.invoke(current, PageReplacementTests.answer(case, packet))
                            if stages[-1] in ('final', 'closing'):
                                break
                            case.assign('successor-%d' % number, reference, prior=current)
                            current = 'successor-%d' % number
                        self.assertIn('page', stages)
                        self.assertEqual(stages[-1], 'closing')
                        records = case.store.snapshot()['records']
                        self.assertTrue(reading.verify_chain(records, case.artifacts, records['review_assignment'][current]))
                        result = case.mutate(case.load_problem_module().record_openness,
                                             dict(assignment_id=current))['result']
                        self.assertEqual(result['outcome'], 'unresolved')
                finally:
                    self.doCleanups()


if __name__ == '__main__':
    unittest.main()
