"""G1, G4, K7 and K8 for the chain kinds of 0.92.0: a v7 final in pages, the math protocol v2, the session rules v4
and the parts protocol v3. Every plan is executed with the real command; only the transport and the clock are stubs."""

import unittest
from unittest.mock import patch

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
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

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
                if family == 'math-v2' and turn == 'final' and outcome == 'input_over_limit':
                    continue
                with self.subTest(turn=turn, outcome=outcome, detail=detail), forms.fork_store(form):
                    executed.append(forms.build_size_row(self, form, outcome, detail))
        if family == 'math-v2':
            form = forms.build_attested_math_final(self)
            for outcome, detail in guard.SIZE_OUTCOMES:
                if outcome == 'input_over_limit':
                    with self.subTest(turn='final', outcome=outcome, detail=detail), forms.fork_store(form):
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
        rows = self.execute_size_rows('math-v2')
        input_stops = [row for row in rows if row['turn'] == 'final' and row['outcome'] == 'input_over_limit']
        self.assertCountEqual([row['detail'] for row in input_stops], [None, 'compacted'])
        for row in input_stops:
            self.assertEqual(row['plan']['state'], 'stop')
            self.assertEqual(row['boundary']['protocol'], forms.reading.MATH_PROTOCOL_V2)

    def test_k8_math_final_exports_both_answers_after_a_new_search(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests

        form = forms.build_attested_math_final(self)
        for detail in (None, 'compacted'):
            with self.subTest(detail=detail), forms.fork_store(form):
                stopped, _, _ = forms.drive_failures(
                    self, form, forms.fail_size_request('input_over_limit', detail), 'input_over_limit', limit=4)
                self.assertEqual((stopped['state'], stopped['code']), ('stop', 'review_input_too_large'))
                extra = ProblemOpennessMinimumTests.search(form.case, form.case.versions)
                current = form.plan()
                self.assertEqual((current['state'], current['code']), (stopped['state'], stopped['code']))
                journeys = forms.execute_stop_answers(self, form, current)
                self.assertEqual(len(journeys), 2)
                for journey, answer in zip(journeys, current['details']['decision']['then']):
                    [step] = answer['steps']
                    self.assertEqual(step['chooses'].count('openness'), 1)
                    self.assertNotIn('openness', step['payload'])
                    self.assertTrue(any(row.get('search_ids') == [extra['id']] for row in step['prerequisites']))
                    self.assertEqual(journey['accepted_commands'],
                                     ['search review-packet', 'review-assignment', 'review-assignment'])


# The turn of each family on which the rows of one plan run; the other turns of the family take the same plan with
# the same command (the exploration of the lane report), so they are folded into this row.
FINAL_TURNS = {'session-v4': 'final', 'parts-v3': 'closing', 'v7-pages': 'closing', 'math-v2': 'final'}
FIRST_TURNS = {'session-v4': 'request', 'parts-v3': 'read', 'v7-pages': 'page', 'math-v2': 'reading'}
CHAIN_KINDS = {'session-v4': 'session', 'parts-v3': 'parts_v2:problem_result', 'v7-pages': 'reading',
               'math-v2': 'reading'}


class FormOutcomeRowTests(unittest.TestCase):
    """G1 for the outcomes that are not size limits and for the conditions of D2 4.6 that a chain kind can change."""
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

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
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def execute_floors(self, family):
        """Each replacement is a smaller task on the same route until the floor, whose decision stop is executed
        with every answer. The walk of a turn down its levels is what needs the time of these tests."""
        executed = {}
        for form in forms.iterate_turns(self, family, FLOOR_TURNS[family]):
            with self.subTest(turn=form.turn), forms.fork_store(form), record_reachable_errors('plan_next'):
                replacements, stop, journeys = forms.walk_to_floor(self, form)
                self.assertIn(stop['code'], stop_classes.STOP_REGISTRY)
                self.assertEqual(stop_classes.classify_stop(stop['code']), 'decision')
                self.assertTrue(journeys)
                executed[form.turn] = journeys
        return executed

    def test_k7_floor_evidence_of_the_v7_page_and_condensing_turn(self):
        self.assertEqual(require_evidence_tests(FLOOR_EVIDENCE), {})

    def test_k7_session_v4_page_closing_and_condensing_floors_execute_every_answer(self):
        self.execute_floors('session-v4')

    def test_k7_parts_v3_page_and_closing_floors_execute_every_answer(self):
        self.execute_floors('parts-v3')

    def test_k7_parts_v3_page_floor_route_answer_names_another_output_class(self):
        """Repairs A16. At the page floor of a parts-v3 chain the floor comes from the measured output profile of the
        class of the route (adapter, model, effort): the answer "Move to another route" says that the new route has
        another class, and the store takes the answer on a route of another model. The walk of the page down its
        levels is what needs the time of this test."""
        form = forms.build_turn(self, 'parts-v3', 'page')
        with forms.fork_store(form), patch.object(forms, 'execute_stop_answers', return_value=[]):
            _, stop, _ = forms.walk_to_floor(self, form)
            form.head = stop['head_id']
            index, answer = next((index, row) for index, row in enumerate(stop['details']['decision']['then'])
                                 if row['answer'] == 'Move to another route')
            self.assertEqual(answer['condition'], capacity.OUTPUT_CLASS_ROUTE_CONDITION)
            steps = [dict(step, payload=dict(step['payload'], model='fixture-model-other')) if step.get('id') == 7
                     else step for step in answer['steps']]
            journey = forms.execute_answer(self, form, dict(answer, steps=steps), index)
            self.assertEqual(journey['end']['kind'], 'assignment')

    def test_k7_parts_v3_page_floor_route_of_the_walked_class_refuses_the_repair(self):
        """The answer "Move to another route" at the page floor of a parts-v3 chain, on a route of the model of the
        walked route: the context repair keeps the parts protocol v3 of its recorded head, the output profile of the
        model holds the size failures of the walk, and the store refuses the assignment, which it does not record. The
        walk of the page down its levels is what needs the time of this test."""
        form = forms.build_turn(self, 'parts-v3', 'page')
        with forms.fork_store(form):
            _, stop, _ = forms.walk_to_floor(self, form)
            form.head = stop['head_id']
            index, answer = next((index, row) for index, row in enumerate(stop['details']['decision']['then'])
                                 if row['answer'] == 'Move to another route')
            with patch.dict(forms.OTHER_ROUTE_MODELS, clear=True), self.assertRaises(forms.ResearchError) as refused:
                forms.execute_answer(self, form, answer, index)
            self.assertEqual(refused.exception.code, 'review_route_capacity_below_floor')
            self.assertEqual({key: refused.exception.details[key] for key in ('kind', 'room', 'minimum')},
                             dict(kind='final', room=60000, minimum=105000))
            self.assertNotIn('answer-%d' % index, form.records()['review_assignment'])

    def test_k7_route_class_condition_stands_only_where_the_store_refuses_the_class(self):
        """Review M-F4 and PL-F1: at the eight floors of the plans review, the answer "Move to another route" states
        that the store refuses a route of the same class exactly where the store refuses that route (its route record
        of step 7 taken with the class of the stopped route). The walk of each turn down its levels is what needs the
        time of this test."""
        floors = (('session-v4', 'page'), ('session-v4', 'closing'), ('session-v4', 'combine'), ('parts-v3', 'page'),
                  ('parts-v3', 'closing'), ('v7-pages', 'closing'), ('math-v2', 'page'), ('math-v2', 'closing'))
        outcomes = {}
        for family, turn in floors:
            try:
                form = forms.build_turn(self, family, turn)
                with forms.fork_store(form), patch.object(forms, 'execute_stop_answers', return_value=[]):
                    _, stop, _ = forms.walk_to_floor(self, form)
                    form.head = stop['head_id']
                    index, answer = next((index, row) for index, row in enumerate(stop['details']['decision']['then'])
                                         if row['answer'] == 'Move to another route')
                    refused = None
                    with patch.dict(forms.OTHER_ROUTE_MODELS, clear=True):
                        try:
                            forms.execute_answer(self, form, answer, index)
                        except forms.ResearchError as error:
                            refused = error.code
                    outcomes[family, turn] = ('condition' in answer, refused)
            finally:
                # Each floor builds its own fixture; the next one starts after the cleanups of this one.
                self.doCleanups()
        self.assertEqual(outcomes, {floor: ((True, 'review_route_capacity_below_floor') if floor == ('parts-v3', 'page')
                                            else (False, None)) for floor in floors})

    def test_k7_v7_closing_floor_executes_every_answer(self):
        self.execute_floors('v7-pages')

    def test_k7_math_v2_page_closing_and_condensing_floors_execute_every_answer(self):
        """At the floor of a page the room of the final of math v2 is below its minimum on this route, so a new review
        starts as a single math v1 request; at the closing and the combine the new review is a math v2 chain."""
        reading = forms.reading
        executed = self.execute_floors('math-v2')
        for turn, journeys in executed.items():
            records = [journey['end']['record'] for journey in journeys if 'record' in journey['end']]
            self.assertTrue(records, turn)
            for record in records:
                admissions = record['protocol_selection']['admissions']
                with self.subTest(turn=turn, assignment=record['id']):
                    if turn == 'page':
                        self.assertEqual([(row['protocol'], row['state']) for row in admissions],
                                         [(reading.MATH_PROTOCOL_V2, 'stop'), (reading.MATH_PROTOCOL, 'record')])
                        details = admissions[0]['details']
                        self.assertEqual((admissions[0]['code'], details['kind'], details['minimum'], details['room']),
                                         ('review_route_capacity_below_floor', 'final', 105000, 30000))
                    else:
                        self.assertEqual(record['protocol_selection']['protocol'], reading.MATH_PROTOCOL_V2)

    def test_k7_math_v2_cap_answer_names_a_native_predecessor_only_where_the_store_takes_it(self):
        """Move to another route: at the floor of a capped page of a math-v2 chain, step 5 names no native predecessor
        and the store takes the assignment that the executor builds from it; at a capped request final, step 5 names
        the head as its native predecessor. The walk of the page down its levels is what needs the time of this
        test."""
        def find_route_answer(stop):
            answer = next(row for row in stop['details']['decision']['then'] if row['answer'] == 'Move to another route')
            return answer, next(step for step in answer['steps'] if step.get('id') == 5)

        page = forms.build_turn(self, 'math-v2', 'page')
        with forms.fork_store(page):
            _, stop, journeys = forms.walk_to_floor(self, page)
            answer, step = find_route_answer(stop)
            self.assertIn('inputs', step['chooses'])
            for child in step['then']:
                self.assertNotIn('native_review_predecessor', child['payload'].get('context', {}))
            journey = next(row for row in journeys if row['answer'] == answer['answer'])
            self.assertEqual(journey['end']['kind'], 'assignment')
        final = forms.build_turn(self, 'math-v2', 'final')
        with forms.fork_store(final):
            plan, _, _ = forms.drive_failures(self, final, forms.fail_size_request('output_over_cap', None),
                                              'output_over_cap', limit=4)
            self.assertEqual(plan['state'], 'stop', plan)
            _, step = find_route_answer(plan)
            self.assertEqual(step['chooses'], [])
            self.assertEqual([child['payload']['context'] for child in step['then']],
                             [dict(native_review_predecessor=final.head)] * 2)

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
                    if family == 'v7-pages':
                        self.assertEqual(refusal.details['largest_one_request_targets'], 0)
                        self.assertEqual([[step['id'] for step in answer['steps']] for answer in decision['then']],
                                         [[7, 4]])
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
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

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
                    self.assertEqual(position['attempts'], [])
                    parent = failures.count_position(records, form.case.artifacts, records['review_assignment'][form.head])
                    self.assertEqual(parent['failed_attempts'], counts[1], parent)
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


class FormFindingTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def prepare(self, cls):
        return forms.prepare_unit_fixture(self, cls, 'runTest')

    def test_f2_compacted_series_read_counts_as_input_over_limit(self):
        form = forms.build_turn(self, 'parts-v3', 'read')
        attempt = forms.run_head(form, forms.fail_size_request('input_over_limit', 'compacted'))
        records = form.records()
        classified = failures.classify_attempt(records, form.case.artifacts, records['review_attempt'][attempt['id']])
        self.assertEqual((classified['outcome'], classified['signal'].get('detail')),
                         ('input_over_limit', 'compacted'))

    def test_f3_stop_on_a_closing_page_of_a_series_moves_to_a_repair_that_the_store_accepts(self):
        """The repair needs a real closing head with at least one authenticated, answered target page in its history."""
        form = forms.build_turn(self, 'parts-v3', 'closing')
        stop, _, _ = forms.drive_failures(self, form, stage3.fail_outcome_request(form.case, 'answer_malformed'),
                                          'answer_malformed')
        answer = next(row for row in stop['details']['decision']['then'] if row['answer'] == 'Move to another route')
        end = forms.execute_answer(self, form, answer, 0)['end']
        self.assertEqual(end['kind'], 'assignment')
        packet = form.packet(end['id'])
        parts = forms.protocol.review_parts
        header = packet[parts.KEY]
        self.assertEqual((header['adapter'], header['kind'], header['series_assignment_id']),
                         ('context_repair', 'read', end['id']))
        artifacts = form.case.artifacts
        logical = parts.read_logical_packet(artifacts, header)
        self.assertEqual(logical['context_repair']['assignment_id'], form.head)
        page_outputs = []
        for turn in logical['scientific_history']:
            frames = [forms.protocol._read_json(artifacts, frame['artifact']) for frame in turn['packet']['frames']]
            if any('page' in frame.get(parts.KEY, {}) for frame in frames):
                page_outputs.extend(forms.protocol._read_json(artifacts, output['artifact']) for output in turn['outputs'])
        self.assertTrue(any(isinstance(output, dict) and 'targets' in output for output in page_outputs))

    def test_f4_runtime_handoff_ends_once_the_probe_verifies_the_route(self):
        from research_harness.review_series import run_series

        form = forms.build_turn(self, 'session-v4', 'request')
        with patch.object(form, 'plan', wraps=form.plan) as planned:
            plan, counts, waits = forms.drive_failures(self, form,
                stage3.fail_outcome_request(form.case, 'runtime_unavailable'), 'runtime_unavailable')
        self.assertEqual(planned.call_count, sum(counts.values()) + len(waits) + 1)
        deadline = failures.datetime.fromisoformat(plan['not_before'])
        due = forms.build_due_clock(deadline)
        with patch.object(failures, 'datetime', due):
            self.assertEqual(form.plan()['state'], 'handoff')
        forms.execute_route_step(form, plan['details']['step'], 'probe-after')
        early = forms.build_due_clock(deadline - failures.timedelta(seconds=1))
        with patch.object(failures, 'datetime', early):
            self.assertEqual(form.plan()['state'], 'handoff')

        with forms.fork_store(form):
            self.assert_recovered_call_boundaries(form, due)

        def stop_after_run(row):
            if row['state'] == 'run':
                forms.matrix.execute(form.case, 'review-stop', dict(id='stop-after-run', action='stop',
                    assignment_id=form.head, mode='now', reason='The owner keeps the resumed run.'))

        with patch.object(failures, 'datetime', due), patch.object(forms.protocol.transport, 'send_request',
                side_effect=forms.respond_with(form.answer())) as send:
            following = form.plan()
            self.assertEqual((following['state'], following['command'], following['head_id']),
                             ('run', 'review-run', form.head))
            run_series(form.case.store, form.head, emit=stop_after_run)
        self.assertEqual(send.call_count, 1)
        latest = max(form.records()['review_attempt'].values(), key=lambda row: row['recorded_revision'])
        self.assertEqual((latest['assignment_id'], latest['status']), (form.head, 'completed'))

    def assert_recovered_call_boundaries(self, form, due):
        from itertools import cycle
        from research_harness import review_budget, review_state_evaluation
        from research_harness.errors import ResearchError

        with patch.object(failures, 'datetime', due):
            healthy = form.plan()
            self.assertEqual(healthy['state'], 'run')
            charged = review_budget.read_budget_usage(form.records())['charged_calls']
            forms.matrix.execute(form.case, 'review-budget', dict(max_calls=charged, reason='All calls charged.'))
            with self.subTest(boundary='budget'), patch.object(forms.protocol.transport, 'send_request') as send:
                revision = form.case.store.revision
                plans = [form.plan(), form.plan()]
                for blocked in plans:
                    with self.assertRaises(ResearchError) as refused:
                        forms.matrix.execute(form.case, 'review-run', healthy['payload'])
                    self.assertEqual(refused.exception.code, 'review_budget_exhausted')
                    self.assertEqual((blocked['state'], blocked['code']), ('stop', refused.exception.code))
                    self.assertEqual(blocked['details']['message'], refused.exception.message)
                    self.assertEqual(blocked['details']['decision']['then'][0]['steps'][0]['command'], 'review-budget')
                self.assertEqual(plans[0], plans[1])
                self.assertEqual(form.case.store.revision, revision)
                send.assert_not_called()
            with self.subTest(boundary='standalone recovery'):
                records = form.records()
                for outcome in (
                    failures.plan_recovered_runtime(records, form.case.artifacts, form.assignment()),
                    failures.plan_after_failure(records, form.case.artifacts, form.head),
                ):
                    self.assertEqual((outcome['state'], outcome['code']), ('stop', 'review_budget_exhausted'))
            forms.matrix.execute(form.case, 'review-budget', dict(max_calls=charged + 1, reason='One more call.'))
            recovered = form.plan()
            self.assertEqual((recovered['state'], recovered['payload']), ('run', healthy['payload']))
            pin = form.records()['review_route'][form.assignment()['route_id']]['runtime']
            with self.subTest(boundary='stale route'), patch.object(forms.protocol.transport, 'runtime',
                    return_value=dict(pin, version='changed')), patch.object(forms.protocol.transport, 'send_request') as send:
                stale = form.plan()
                with self.assertRaises(ResearchError) as refused:
                    forms.matrix.execute(form.case, 'review-run', recovered['payload'])
                self.assertEqual(refused.exception.code, 'review_route_runtime_stale')
                self.assertEqual(stale['code'], refused.exception.code)
                self.assertEqual(stale['details']['refusal'],
                                 dict(code=refused.exception.code, message=refused.exception.message))
                self.assertEqual(stale['details']['step']['command'], 'review-context')
                send.assert_not_called()
            fingerprint = cycle(('before', 'after'))
            with self.subTest(boundary='runtime drift'), patch.object(
                    review_state_evaluation, '_runtime_fingerprint', side_effect=fingerprint), patch.object(
                    forms.protocol.transport, 'send_request') as send:
                records = form.records()
                drifted = [failures.plan_recovered_runtime(records, form.case.artifacts, form.assignment()),
                           failures.plan_after_failure(records, form.case.artifacts, form.head), form.plan()]
                with self.assertRaises(ResearchError) as refused:
                    forms.matrix.execute(form.case, 'review-run', recovered['payload'])
                for outcome in drifted:
                    self.assertEqual((outcome['state'], outcome['code']), ('stop', refused.exception.code))
                    self.assertEqual(outcome['details']['step']['command'], 'review-series')
                send.assert_not_called()
            with patch.object(forms.protocol.transport, 'send_request', side_effect=forms.respond_with(form.answer())) as send:
                receipt = forms.matrix.execute(form.case, recovered['command'], recovered['payload'])
            self.assertEqual(receipt['status'], 'completed')
            send.assert_called_once()
            self.assertEqual(review_budget.read_budget_usage(form.records())['remaining_calls'], 0)

    def test_f5_page_of_a_split_of_a_session_v4_is_a_new_position(self):
        form = forms.build_turn(self, 'session-v4', 'page')
        plan, _, _ = forms.drive_failures(self, form, forms.fail_size_request('output_over_cap', None),
                                          'output_over_cap', limit=4)
        following = forms.check_replacement(self, form, plan)['record']
        records = form.records()
        position = failures.count_position(records, form.case.artifacts, records['review_assignment'][following['id']])
        self.assertEqual(position['failed_attempts'], 0)

    def test_f6_below_floor_stop_offers_no_smaller_statement_when_none_fits(self):
        form, payload, refusal = forms.refuse_below_floor(self, 'v7-pages')
        self.assertEqual(refusal.details['largest_one_request_targets'], 0)
        self.assertEqual(refusal.details['decision']['owner_decides'], 'Whether this review runs on another route.')
        self.assertNotIn('Record a statement with fewer neighbours',
                         [answer['answer'] for answer in refusal.details['decision']['then']])

    def test_recorded_parts_closing_is_delivered_with_its_pages_in_the_repair(self):
        """Admission needs genuinely answered pages and a recorded, authenticated parts closing."""
        from test_review_parts_repair_journey import ResultRepairJourneyTests

        form = forms.build_turn(self, 'parts-v3', 'closing')
        closing = form.assignment()
        form.fixture.finish(closing)
        records, artifacts = form.records(), form.case.artifacts
        saved = next(row['payload'] for row in records['value_review'].values()
                     if row['assignment_id'] == form.head)
        parts = forms.protocol.review_parts
        answered_pages = {
            assignment['id']: forms.protocol.read_assignment_output(records, artifacts, assignment['id'])[1]
            for assignment in form.fixture.turns
            if 'page' in forms.protocol._read_json(artifacts, assignment['packet'])[parts.KEY]
        }
        binding = ResultRepairJourneyTests.contaminate(form.fixture, closing)
        repair = ResultRepairJourneyTests.repair_assignment(form.fixture, closing, binding)
        history = parts.read_logical_packet(artifacts, form.packet(repair['id'])[parts.KEY])['scientific_history']
        retained = {}
        closing_outputs = []
        for turn in history:
            frames = [forms.protocol._read_json(artifacts, row['artifact']) for row in turn['packet']['frames']]
            outputs = [forms.protocol._read_json(artifacts, row['artifact']) for row in turn['outputs']]
            if any('page' in frame.get(parts.KEY, {}) for frame in frames):
                retained[turn['assignment_id']] = outputs
            if turn['assignment_id'] == form.head:
                closing_outputs = outputs
        self.assertIn({key: value for key, value in saved.items()
                       if key not in ('id', 'dossier_id', 'assignment_id')}, closing_outputs)
        self.assertEqual(set(retained), set(answered_pages))
        self.assertTrue(any(answer.get('targets') for answer in answered_pages.values()))
        for identifier, answer in answered_pages.items():
            self.assertIn(answer, retained[identifier])

    def test_runtime_removed_after_probe_keeps_the_install_handoff(self):
        from research_harness.errors import ResearchError

        form = forms.build_turn(self, 'session-v4', 'request')
        plan, _, _ = forms.drive_failures(self, form, stage3.fail_outcome_request(form.case, 'runtime_unavailable'),
                                          'runtime_unavailable')
        forms.execute_route_step(form, plan['details']['step'], 'verified-before-removal')
        due = forms.build_due_clock(failures.datetime.fromisoformat(plan['not_before']))
        missing = ResearchError('review_runtime_missing', 'The fixture runtime was removed after verification.')
        revision = form.case.store.revision
        with patch.object(failures, 'datetime', due), patch.object(forms.protocol.transport, 'runtime',
                side_effect=missing), patch.object(forms.protocol.transport, 'send_request') as send:
            first, second = form.plan(), form.plan()
        self.assertEqual(first, second)
        self.assertEqual((first['state'], first['command'], first['code']),
                         ('handoff', 'review-probe', 'review_runtime_missing'))
        self.assertEqual(first['not_before'], plan['not_before'])
        self.assertEqual(first['details']['step']['payload']['route_id'], form.assignment()['route_id'])
        self.assertEqual(form.case.store.revision, revision)
        send.assert_not_called()

    def test_probe_does_not_make_an_invalid_source_continuation_runnable(self):
        case, head, fixture = forms.matrix.prepare_assignment(self, 'source')
        form = forms.FormTurn('ordinary', 'source', fixture, case, head['id'])
        plan, _, _ = forms.drive_failures(self, form, stage3.fail_outcome_request(case, 'runtime_unavailable'),
                                          'runtime_unavailable')
        forms.matrix.execute(case, 'review-context', dict(id='later-exposure',
            assignment_id=head['history_assignment_id'], kind='contamination', source='author_history',
            reason='Exposure after the earlier review.', evidence=[case.supporting]))
        forms.execute_route_step(form, plan['details']['step'], 'verified-after-exposure')
        due = forms.build_due_clock(failures.datetime.fromisoformat(plan['not_before']))
        revision = case.store.revision
        with patch.object(failures, 'datetime', due), patch.object(forms.protocol.transport, 'send_request') as send:
            first, second = form.plan(), form.plan()
            # Present-context precedence already catches this event. The failure planner must also reject the
            # run independently, using the same source readiness as dispatch.
            recovery = failures.plan_after_failure(form.records(), case.artifacts, head['id'])
        self.assertEqual(first, second)
        self.assertEqual((first['state'], first['command']), ('handoff', 'review-assignment'))
        self.assertEqual(first['details']['step']['id'], 1)
        binding = first['details']['step']['payload']['context']['context_repair']
        self.assertIn('later-exposure', binding['event_ids'])
        self.assertEqual((recovery['state'], recovery['code']), ('handoff', 'review_context_contaminated'))
        self.assertEqual(case.store.revision, revision)
        send.assert_not_called()

    def test_split_child_admits_its_first_refusal_replacement(self):
        from research_harness import review_refusal_notice as notices, review_refusal_replacement as replacements
        from research_harness.errors import ResearchError

        form = forms.build_turn(self, 'session-v4', 'page')
        parent_targets = forms.list_page_targets(form)
        legacy_parent = None
        for _ in range(2):
            answer = form.answer()
            answer['targets'][0]['sentence'] = 'x' * 301
            forms.run_head(form, forms.respond_with(answer))
            plan = form.plan()
            self.assertEqual(plan['state'], 'replace', plan)
            # Write the historical packet shape in the real admission path.
            with patch.object(notices, 'PAGED_POSITION_BINDING', None, create=True):
                form.head = forms.matrix.execute(form.case, plan['command'], plan['payload'])['id']
            legacy_parent = form.assignment()
        plan, _, _ = forms.drive_failures(self, form, forms.fail_size_request('output_over_cap', None), 'output_over_cap')
        form.head = forms.check_replacement(self, form, plan)['id']
        self.assertLess(len(forms.list_page_targets(form)), len(parent_targets))
        local = failures.count_position(form.records(), form.case.artifacts, form.assignment())
        self.assertEqual(local['replacements'], {'output_refused': 0, 'capacity': 0})
        answer = form.answer()
        answer['targets'][0]['sentence'] = 'x' * 301
        forms.run_head(form, forms.respond_with(answer))
        child = form.head
        plan = form.plan()
        self.assertEqual(plan['state'], 'replace', plan)
        replacement = forms.matrix.execute(form.case, plan['command'], plan['payload'])
        self.assertEqual(replacement['context']['output_refusal']['assignment_id'], child)
        form.head = replacement['id']
        records, artifacts = form.records(), form.case.artifacts
        legacy_entries = form.packet(legacy_parent['id'])['output_refusal']['refused']
        replacements.validate_replacement_origin(records, artifacts, legacy_parent)
        replacements.validate_replacement_origin(records, artifacts, replacement)
        packet = form.packet()
        notice = packet['output_refusal']
        with self.subTest(derivation='legacy must reject a position-only list'):
            unmarked = {key: value for key, value in notice.items() if key != 'position_binding'}
            with self.assertRaises(ResearchError) as raised:
                replacements.validate_replacement_origin(records, artifacts, replacement,
                                                         dict(packet, output_refusal=unmarked))
            self.assertEqual(raised.exception.code, 'review_refusal_replacement_origin_invalid')
        with self.subTest(derivation='position must reject a legacy-only list'):
            self.assertEqual(notice['position_binding'], 'exactory-review-paged-refusal-position-v1')
            with self.assertRaises(ResearchError) as raised:
                replacements.validate_replacement_origin(records, artifacts, replacement, dict(packet,
                    output_refusal=dict(notice, refused=legacy_entries + notice['refused'])))
            self.assertEqual(raised.exception.code, 'review_refusal_replacement_origin_invalid')
        self.assertEqual(form.plan()['state'], 'run')
        receipt = forms.run_head(form, forms.respond_with(form.answer()))
        self.assertEqual((receipt['assignment_id'], receipt['status']), (form.head, 'completed'))
        self.assertNotIn('output_refusal', receipt)

    def test_resumed_closing_keeps_its_failure_across_condensation(self):
        """A real closing, failed at its cap, must advance through condensing work in each paged family."""
        for family in ('session-v4', 'parts-v3'):
            with self.subTest(family=family):
                try:
                    form = forms.build_turn(self, family, 'closing')
                    before = form.assignment()
                    plan, _, _ = forms.drive_failures(self, form, forms.fail_size_request('output_over_cap', None),
                                                      'output_over_cap', limit=4)
                    form.head = forms.check_replacement(self, form, plan)['id']
                    for _ in range(12):
                        binding = (forms.protocol.review_session_v4.read_session_binding(form.packet())
                                   if family == 'session-v4' else form.packet()[forms.protocol.review_parts.KEY])
                        if 'closing' in binding:
                            break
                        form.head = form.fixture.advance(form.assignment())['id']
                    self.assertIn('closing', binding)
                    self.assertNotEqual(before['id'], form.head)
                    artifacts, records = form.case.artifacts, form.records()
                    self.assertEqual(failures.read_paged_position(artifacts, before),
                                     failures.read_paged_position(artifacts, form.assignment()))
                    self.assertEqual(failures.count_position(records, artifacts, form.assignment())['failed_attempts'], 1)
                    if family == 'session-v4':
                        self.assert_closing_plan_keeps_derived_objects_in_memory(form)
                finally:
                    self.doCleanups()

    def assert_closing_plan_keeps_derived_objects_in_memory(self, form):
        from research_harness.artifacts import ArtifactStore

        references = {}
        original_put = ArtifactStore.put

        def observe(store, data, media_type):
            reference = original_put(store, data, media_type)
            references[reference['sha256']] = reference
            return reference

        with patch.object(ArtifactStore, 'put', observe):
            forms.protocol.prepare_assignment_call(form.records(), form.case.artifacts, form.assignment(), dispatch=True)
        self.assertTrue(references, 'The dispatch preparation materializes a combined object.')

        def artifact_files():
            return sorted(str(path.relative_to(form.case.root)) for path in
                          (form.case.root / 'research/sources').rglob('*') if path.is_file())

        for present in (True, False):
            with self.subTest(present=present):
                if not present:
                    for reference in references.values():
                        path = form.case.root / reference['path']
                        path.rename(form.case.root / ('derived-' + reference['sha256']))
                before = artifact_files()
                revision = form.case.store.revision
                with patch.object(ArtifactStore, 'put', autospec=True, side_effect=original_put) as publish:
                    selected = form.plan()
                    self.assertEqual(selected['state'], 'run')
                self.assertEqual(artifact_files(), before)
                publish.assert_not_called()
                self.assertEqual(form.case.store.revision, revision)

        from research_harness.errors import ResearchError
        reference = next(iter(references.values()))
        (form.case.root / reference['path']).write_bytes(b'X' * reference['size'])
        before = artifact_files()
        with patch.object(forms.protocol.transport, 'send_request') as send, patch.object(
                ArtifactStore, 'put', autospec=True, side_effect=original_put) as publish:
            blocked = form.plan()
            publish.assert_not_called()
            with self.assertRaises(ResearchError) as refused:
                forms.matrix.execute(form.case, 'review-run', selected['payload'])
            self.assertEqual(refused.exception.code, 'review_invocation_unverified')
            self.assertEqual((blocked['state'], blocked.get('code')), ('stop', refused.exception.code))
            self.assertEqual(blocked['details']['message'], refused.exception.message)
            send.assert_not_called()
        self.assertEqual(artifact_files(), before)
        self.assertEqual(form.case.store.revision, revision)

    def test_direct_module_entry_point_sees_the_incident_class(self):
        import runpy
        import sys

        def load_incidents():
            module = sys.modules['__main__']
            self.assertTrue(hasattr(module, 'FormFindingTests'))
            self.assertTrue(hasattr(module, 'SixthRoundTests'))

        with patch.object(unittest, 'main', side_effect=load_incidents) as main:
            runpy.run_path(__file__, run_name='__main__')
        main.assert_called_once_with()


class ThirdRoundTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def prepare(self, cls):
        return forms.prepare_unit_fixture(self, cls, 'runTest')

    def test_dispatch_and_recovered_planner_walk_the_same_preparation(self):
        """Inject assignment-preparation faults before reader transport and compare the two paths."""
        from contextlib import ExitStack
        from research_harness import problem_delivery, review_preflight, review_refusal
        from research_harness import review_refusal_replacement, review_source_complete, review_visual_sources
        from research_harness import openness_reading_v7
        from research_harness.errors import ResearchError

        protocol = forms.protocol
        scope = forms.build_turn(self, 'session-v4', 'request')
        injected = ResearchError('artifact_corrupt', 'The synthetic preparation check was refused.', dict(turn=1))

        def blocks(form, code, patches):
            payload = dict(id='checked-attempt', assignment_id=form.head)
            with self.subTest(refusal=code), ExitStack() as stack:
                for target, name, options in patches:
                    stack.enter_context(patch.object(target, name, **options))
                send = stack.enter_context(patch.object(protocol.transport, 'send_request'))
                revision = form.case.store.revision
                action = failures.plan_recovered_runtime(form.records(), form.case.artifacts, form.assignment())
                with self.assertRaises(ResearchError) as caught:
                    forms.matrix.execute(form.case, 'review-run', payload)
                self.assertEqual(caught.exception.code, code)
                self.assertNotEqual(action.get('state'), 'run')
                self.assertEqual(action.get('code'), code)
                self.assertEqual(action['details']['message'], caught.exception.message)
                self.assertEqual(form.case.store.revision, revision)
                send.assert_not_called()

        for target, name in (
                (problem_delivery, 'require_unposted_review'),
                (protocol, 'require_verified_assignment'),
                (protocol.context_repair, 'require_current_slot'),
                (review_visual_sources, 'require_installed_renderer'),
                (review_source_complete, 'validate'),
                (protocol.review_session_v4, 'authenticate_session_binding'),
                (protocol, 'validate_assignment_inventories'),
                (protocol.context_repair, 'validate_repair_binding'),
                (review_refusal_replacement, 'validate_replacement_origin'),
                (protocol, 'build_assignment_request'),
                (review_preflight, 'build_assignment_report'),
                (review_preflight, 'check_request'),
                (capacity, 'describe_session_forecast'),
                (capacity, 'describe_session_stop')):
            with self.subTest(check=name):
                blocks(scope, injected.code, [(target, name, dict(side_effect=injected))])

        for name, value, code in (
                ('retired_routes', {scope.assignment()['route_id']}, 'review_route_retired'),
                ('find_replacement', {'id': 'durable-successor'}, 'review_assignment_replaced'),
                ('_prompt_matches', False, 'review_prompt_mismatch')):
            with self.subTest(check=name):
                blocks(scope, code, [(protocol, name, dict(return_value=value))])

        get = protocol._get

        def changed_route(records, kind, identifier):
            found = get(records, kind, identifier)
            return dict(found, model='changed-model') if kind == 'review_route' else found

        blocks(scope, 'review_route_changed', [(protocol, '_get', dict(side_effect=changed_route))])
        route = scope.records()['review_route'][scope.assignment()['route_id']]
        blocks(scope, 'review_route_runtime_stale', [
            (protocol.transport, 'runtime', dict(return_value=dict(route['runtime'], version='changed')))])
        blocks(scope, 'review_runtime_missing', [
            (protocol.transport, 'runtime', dict(side_effect=ResearchError('review_runtime_missing', 'Synthetic missing runtime.'))),
            (protocol.time, 'sleep', {})])

        read_filtered = protocol.read_filtered_records

        def with_pending(records, record_kind, **where):
            if record_kind == 'review_invocation':
                return {'pending': dict(id='pending', assignment_id=scope.head)}
            return read_filtered(records, record_kind, **where)

        blocks(scope, 'review_invocation_pending', [(protocol, 'read_filtered_records', dict(side_effect=with_pending))])
        read_group = protocol._read_review_group
        completed = dict(id='earlier-attempt', status='completed', request_digest='different')
        completions = [completed]

        def with_completed(records, artifacts, kind, field, value, **kwargs):
            if kind == 'review_attempt':
                return completions
            return read_group(records, artifacts, kind, field, value, **kwargs)

        observed = [(protocol, '_read_review_group', dict(side_effect=with_completed)),
                    (protocol, '_context_observed', dict(return_value=True))]
        blocks(scope, 'review_resampling_forbidden', observed + [
            (review_refusal, 'refused_attempts', dict(return_value=[]))])
        blocks(scope, 'review_request_changed', observed + [
            (review_refusal, 'refused_attempts', dict(return_value=[completed]))])
        completions.append(dict(completed, id='second-earlier-attempt'))
        blocks(scope, 'review_slot_exhausted', observed + [
            (review_refusal, 'refused_attempts', dict(return_value=completions))])
        completions.pop()

        self.assertEqual(failures.plan_recovered_runtime(scope.records(), scope.case.artifacts,
                                                        scope.assignment())['state'], 'run')
        result = forms.run_head(scope, forms.respond_with(scope.answer()))
        self.assertEqual(result['status'], 'completed')

        # Each family installs its own request builders and runtime stubs.
        self.doCleanups()
        parts = forms.build_turn(self, 'parts-v3', 'read')
        blocks(parts, injected.code, observed + [
            (failures, 'classify_final_capacity_attempt', dict(side_effect=injected))])

        self.doCleanups()
        case, head, fixture = forms.matrix.prepare_assignment(self, 'source')
        ordinary = forms.FormTurn('ordinary', 'source', fixture, case, head['id'])
        blocks(ordinary, 'review_source_continuation_unverified', [
            (protocol, 'has_verified_history', dict(return_value=False))])

        self.doCleanups()
        math = forms.build_turn(self, 'math-v2', 'reading')
        blocks(math, injected.code, [(openness_reading_v7, 'describe_ratio_handoff', dict(side_effect=injected))])
        measure_request = capacity.measure_request

        def just_over_limit(route, request, *args, **kwargs):
            measured = measure_request(route, request, *args, **kwargs)
            return dict(measured, character_limit=measured['characters'] - 1)

        blocks(math, injected.code, [
            (capacity, 'measure_request', dict(side_effect=just_over_limit)),
            (forms.reading, 'describe_reading_receipt', dict(side_effect=injected))])

    def test_recovered_visual_repair_refuses_the_changed_renderer_before_any_call(self):
        """Only a real visual repair with a frozen renderer profile can test the installed-profile boundary."""
        from research_harness.errors import ResearchError
        from test_visual_context_repair import VisualContextRepairTests
        from test_visual_manifest_recorded_profile import install_other_renderer

        fixture = forms.prepare_unit_fixture(self, VisualContextRepairTests, 'runTest')
        head = fixture.assign()
        case = fixture.case
        form = forms.FormTurn('visual', 'repair', fixture, case, head['id'])

        def check_renderer(*, repeats):
            healthy = form.plan()
            self.assertEqual(healthy['state'], 'run')
            with install_other_renderer(), patch.object(forms.protocol.transport, 'send_request') as send:
                revision = case.store.revision
                plans = [form.plan() for _ in range(repeats)]
                with self.assertRaises(ResearchError) as refused:
                    forms.matrix.execute(case, 'review-run', healthy['payload'])
                error = refused.exception
                self.assertEqual(error.code, 'review_visual_source_mismatch')
                for planned in plans:
                    self.assertEqual((planned['state'], planned.get('code')), ('stop', error.code))
                    self.assertEqual(planned['details']['message'], error.message)
                    steps = [step['command'] for answer in planned['details']['decision']['then']
                             for step in answer['steps']]
                    self.assertIn('record-check', steps)
                if repeats == 2:
                    self.assertEqual(plans[0], plans[1])
                self.assertEqual(case.store.revision, revision)
                send.assert_not_called()

        with self.subTest(call='fresh'):
            check_renderer(repeats=1)
        plan, _, _ = forms.drive_failures(self, form, stage3.fail_outcome_request(case, 'runtime_unavailable'),
                                          'runtime_unavailable')
        fixture.probe('verified-after-failures')
        due = forms.build_due_clock(failures.datetime.fromisoformat(plan['not_before']))
        with patch.object(failures, 'datetime', due), self.subTest(call='recovered'):
            check_renderer(repeats=2)

    def test_page_of_changed_parts_inventory_admits_its_first_refusal_replacement(self):
        """Spend both replacements, finish the old page, then deliver a required unit to start a new currency."""
        from test_review_parts_v3_pages import PagedSeriesTests

        fixture = forms.prepare_unit_fixture(self, PagedSeriesTests, 'runTest')
        old = fixture.reach_page(fixture.start(0))
        form = forms.FormTurn('parts-v3', 'page', fixture, fixture.case, old['id'])

        def refuse_and_replace():
            answer = form.answer()
            answer['targets'][0]['sentence'] = 'x' * 301
            self.assertIn('output_refusal', fixture.invoke(form.assignment(), answer))
            plan = form.plan()
            self.assertEqual((plan['state'], plan['command']), ('replace', 'review-assignment'), plan)
            form.head = forms.matrix.execute(form.case, plan['command'], plan['payload'])['id']

        for _ in range(2):
            refuse_and_replace()
        self.assertNotIn('output_refusal', fixture.invoke(form.assignment(), form.answer()))
        search = dict(id='late-bound-search', queries=['late unit'], reason='A newly bound required record.')
        fixture.case.put('literature_search', search['id'], search)
        fixture.case.expected['openness_searches'].append(dict(search=search, sources=[]))
        reading = fixture.assign('late-reading', form.head)
        following = fixture.reach_page(fixture.advance(reading))
        form.head = following['id']
        parts_key = forms.protocol.review_parts.KEY
        before, after = fixture.packet(old)[parts_key], form.packet()[parts_key]
        self.assertEqual(before['final_turn'], after['final_turn'])
        self.assertNotEqual(before['page_plan_digest'], after['page_plan_digest'])
        position = failures.count_position(form.records(), form.case.artifacts, following)
        self.assertEqual(position['replacements']['output_refused'], 0)
        refuse_and_replace()
        self.assertEqual(form.assignment()['context']['output_refusal']['assignment_id'], following['id'])
        self.assertEqual(form.plan()['state'], 'run')

    def test_request_form_parts_material_is_intermediate_and_terminal_answer_needs_recording(self):
        """Reach a real unpaged parts final so repair authenticates the material and terminal answers."""
        from research_harness.errors import ResearchError
        from test_review_parts_repair_journey import ResultRepairJourneyTests as Repair
        from test_review_parts_v3 import RecordedSeriesTests

        fixture = forms.prepare_unit_fixture(self, RecordedSeriesTests, 'runTest')
        fixture.oversized(count=1, characters=50000, limit=1300000)
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            first = fixture.assign()
        _, final = fixture.finish_series(first)
        form = forms.FormTurn('parts-v3', 'final', fixture, fixture.case, final['id'])
        parts = forms.protocol.review_parts
        self.assertNotIn('final_form', form.packet()[parts.KEY])
        with forms.fork_store(form):
            self.assertNotIn('output_refusal', fixture.invoke(final, fixture.final_output()))
            binding = Repair.contaminate(fixture, final)
            with self.assertRaises(ResearchError) as refused:
                Repair.repair_assignment(fixture, final, binding)
            self.assertEqual(refused.exception.code, 'review_context_repair_history_unrecorded')
            self.assertEqual(refused.exception.details['assignment_id'], final['id'])
        material = dict(stage='material', title='A finite argument', part=1, more=True,
                        text='The first part establishes the finite case.')
        self.assertNotIn('output_refusal', fixture.invoke(final, material))
        with forms.fork_store(form):
            plan = form.plan()
            self.assertEqual((plan['state'], plan['command']), ('continue', 'review-assignment'))
            following = forms.matrix.execute(form.case, plan['command'], plan['payload'])
            self.assertEqual(form.packet(following['id'])['review_parts_material_request']['part'], 2)
        binding = Repair.contaminate(fixture, final)
        admitted = Repair.repair_assignment(fixture, final, binding)
        artifacts = form.case.artifacts
        delivered_packet = form.packet(admitted['id'])
        uses_parts = parts.KEY in delivered_packet
        if uses_parts:
            delivered_packet = parts.read_logical_packet(artifacts, delivered_packet[parts.KEY])
        history = delivered_packet['scientific_history']
        turn = next(row for row in history if row['assignment_id'] == final['id'])
        if uses_parts:
            delivered = [forms.protocol._read_json(artifacts, row['artifact']) for row in turn['outputs']]
            self.assertIn(material, delivered)
        else:
            self.assertEqual(turn['intermediate_output'], material)
            self.assertIsNone(turn['observed_output'])

    def test_paged_membership_scans_assignment_rows_once_per_evaluation(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from research_harness import openness_final_pages, review_final_pages
        from research_harness.artifacts import ArtifactStore
        from research_harness.evaluation import Evaluation

        class CountedRows(dict):
            visits = 0

            def items(self):
                for key, row in super().items():
                    self.visits += 1
                    yield key, row

            def values(self):
                for _, row in self.items():
                    yield row

        for count in (1, 8, 32):
            with self.subTest(chains=count), TemporaryDirectory() as root:
                artifacts = ArtifactStore(Path(root))
                assignments = CountedRows()
                for index in range(count):
                    identifier = str(index)
                    page_plan = openness_final_pages.publish_page_plan(artifacts, dict(
                        protocol=review_final_pages.FINAL_PAGE_PLAN_PROTOCOL, chain=identifier,
                        currency={'inventory_digest': identifier}, turns=[dict(number=1)]))
                    packet = forms.protocol._put_json(artifacts, dict(session_final=dict(final_turn=1)))
                    assignments[identifier] = dict(id=identifier, role='result', packet=packet,
                        session_chain=dict(first_assignment_id=identifier, final_page_plan=page_plan))
                records = dict(review_assignment=assignments)
                heads = list(dict.values(assignments))
                for name, planned in (('one plan', heads[:1]), ('one status', heads)):
                    assignments.visits = 0
                    evaluation = Evaluation.of(records, artifacts)
                    for row in planned:
                        for _ in range(2):
                            self.assertEqual(failures.list_failure_position_assignments(
                                records, evaluation, row), [row['id']])
                    print(f'{name}, {count} chains: {assignments.visits} role rows')
                    self.assertEqual(assignments.visits, count)


class FourthRoundTests(unittest.TestCase):
    def test_failed_fixture_setup_restores_the_request_builder(self):
        import problem_parts_fixtures
        from research_harness import review_request_evaluation
        from test_review_parts_v3_pages import PagedSeriesTests

        original = review_request_evaluation._build_request
        owner = unittest.TestCase()
        fixtures = []

        class Interrupted(PagedSeriesTests):
            def setUp(self):
                fixtures.append(self)
                super().setUp()

        with patch.object(problem_parts_fixtures, 'record_codex_route',
                          side_effect=ValueError('Interrupted route setup')):
            with self.assertRaisesRegex(ValueError, 'Interrupted route setup'):
                forms.prepare_unit_fixture(owner, Interrupted, 'runTest')
            try:
                owner.doCleanups()
                self.assertIs(review_request_evaluation._build_request, original)
            finally:
                fixtures[0].doCleanups()

    def test_failed_inner_session_setup_restores_the_claims_module(self):
        import importlib
        import sys
        import test_research_problem_reviews as problem
        from test_review_session_v4_pages import SessionPagesTests

        original = importlib.import_module('research_harness.problem_claims')
        owner, seen = unittest.TestCase(), []

        def interrupt(case):
            seen.append(case)
            raise RuntimeError('Interrupted at rubric anchors')

        try:
            with patch.object(problem, 'install_rubric_anchors', interrupt):
                with self.assertRaisesRegex(RuntimeError, 'Interrupted at rubric anchors'):
                    forms.prepare_unit_fixture(owner, SessionPagesTests, 'runTest')
            owner.doCleanups()
            self.assertIs(sys.modules['research_harness.problem_claims'], original)
        finally:
            for case in seen:
                case.doCleanups()


class FifthRoundTests(unittest.TestCase):
    def setUp(self):
        # Recording a route reads the platform of the host. The caches that this read fills stay inside the test: the
        # offline reader of record-check takes a warm cache as a permitted local observation.
        import platform
        for name, value in (('_platform_cache', {}), ('_uname_cache', None)):
            patcher = patch.object(platform, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_released_problem_reading_forms_plan_and_dispatch_range_assembled_text(self):
        import shutil
        from literature_fixtures import LiteratureCase
        from research_harness import openness_reading, reading, source_links
        from research_harness.errors import ResearchError
        from test_capture_classes import CaptureClassTests
        from test_openness_reading_v7_turns import V7CommandTests
        from test_openness_v6_chain import V6ChainTests

        class V6RangeReview(V6ChainTests):
            # Even when the range capture replaces the first work, the other
            # texts exceed one turn and select the released reading chain.
            text_sizes = (1000, 80000, 80000)

        class V7RangeReview(V7CommandTests):
            text_sizes = (1000,)

        range_case, version, capture = CaptureClassTests.create_capture_case(self, 'http_range_v1')
        source = range_case.store.snapshot()['records']
        bundle = CaptureClassTests.build_bundle(self, range_case, version, capture)
        bundle.update(id='bundle-1', original_sha256=capture['original']['sha256'], sequence=1)
        note = LiteratureCase.full_note(range_case, bundle, 'read-1')
        for selected, fixture in ((openness_reading.PROTOCOL_V6, V6RangeReview),
                                  (openness_reading.PROTOCOL_V7, V7RangeReview)):
            with self.subTest(protocol=selected):
                owner = unittest.TestCase()
                try:
                    case = forms.prepare_unit_fixture(owner, fixture, 'runTest')
                    shutil.copytree(range_case.root / 'research/sources', case.root / 'research/sources', dirs_exist_ok=True)

                    def seed(transaction):
                        for kind in ('work', 'source', 'source_assembly', 'fulltext_unit', 'fulltext_job'):
                            for key, row in source.get(kind, {}).items():
                                transaction.put(kind, key, row)
                        transaction.put('source_bundle', bundle['id'], bundle)
                        transaction.put('reading', note['id'], note)

                    case.store.mutate('fixture.range', {}, seed,
                                      expected_revision=case.store.revision, request_id='range')
                    bound = dict(note, assessment=reading._assess(case.store.snapshot()['records'], case.artifacts, note))
                    case.store.mutate('fixture.assessment', {}, lambda tx: tx.put('reading', bound['id'], bound),
                                      expected_revision=case.store.revision, request_id='range-assessment')
                    native = case.export(assignment_id='p-1')
                    with patch.object(openness_reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (selected,)):
                        assignment = case.assign('p-1', native)
                    self.assertEqual(assignment['prompt']['template_id'], selected + '/reading')
                    form = forms.FormTurn('released', 'reading', case, case, 'p-1')
                    with forms.fork_store(form):
                        plan = form.plan()
                        self.assertEqual((plan['state'], plan['command']), ('run', 'review-run'))
                        packet = case.read_packet('p-1')
                        output = (case.build_v7_answer(packet) if selected == openness_reading.PROTOCOL_V7
                                  else case.build_reading_response(packet, excerpts=[]))
                        with patch.object(forms.protocol.transport, 'send_request',
                                          side_effect=forms.respond_with(output)) as send:
                            receipt = forms.matrix.execute(case, plan['command'], plan['payload'])
                        self.assertEqual(receipt['status'], 'completed')
                        send.assert_called_once()
                    original = capture['original']
                    path = case.root / original['path']
                    path.chmod(0o600)
                    path.write_bytes(b'X' * original['size'])
                    with self.assertRaises(ResearchError) as corrupt:
                        source_links.captured_source(form.records(), case.artifacts, capture['source_id'])
                    self.assertEqual(corrupt.exception.code, 'artifact_corrupt')
                    blocked = form.plan()
                    self.assertIn(blocked['state'], ('stop', 'handoff'))
                    self.assertIn(blocked['code'], stop_classes.STOP_REGISTRY)
                    self.assertTrue({'step', 'decision'} & blocked['details'].keys())
                finally:
                    owner.doCleanups()

    def test_corrupt_existing_request_is_refused_before_reservation(self):
        from research_harness.errors import ResearchError
        from strategy_fixtures import StrategyCase

        case = forms.prepare_unit_fixture(self, StrategyCase, 'runTest')
        case.route()
        case.mutate(forms.protocol.record_assignment, dict(id='first', route_id='route', dossier_id=None,
            role='standalone', reviewer_id='reader', author_id='author', context={'artifact_refs': [case.evidence]}))
        form = forms.FormTurn('standalone', 'first', case, case, 'first')
        healthy = form.plan()
        request = forms.protocol.build_assignment_request(form.records(), case.artifacts, form.assignment())
        reference = forms.protocol._put_json(case.artifacts, request)
        self.assertEqual(form.plan(), healthy)
        path = case.root / reference['path']
        path.chmod(0o600)
        bad = b'X' * reference['size']
        path.write_bytes(bad)
        revision = case.store.revision
        with patch.object(forms.protocol.transport, 'send_request') as send:
            blocked = form.plan()
            with self.assertRaises(ResearchError) as refused:
                forms.matrix.execute(case, 'review-run', healthy['payload'])
            self.assertEqual(refused.exception.code, 'artifact_corrupt')
            self.assertEqual((blocked['state'], blocked.get('code')), ('stop', refused.exception.code))
            self.assertEqual(blocked['details']['message'], refused.exception.message)
            send.assert_not_called()
        self.assertEqual(case.store.revision, revision)
        self.assertEqual(path.read_bytes(), bad)

    def test_planning_arena_releases_peak_derivatives_at_each_head(self):
        from contextlib import nullcontext
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from research_harness.artifacts import ArtifactStore
        from research_harness.errors import ResearchError
        from research_harness.evaluation import Evaluation
        from research_harness.review_state_evaluation import runtime_scope

        with TemporaryDirectory() as folder:
            store = ArtifactStore(Path(folder))
            records = {}
            root = Evaluation(records, store, retention_budget_bytes=8192)
            with runtime_scope(records, root):
                root.once('verified value', lambda: b'v' * 2048)
                for head in range(8):
                    view = root.planning_view()
                    with (view if hasattr(type(view), '__enter__') else nullcontext(view)) as preview:
                        content = bytes([head]) * 131072
                        reference = preview.put(content, 'application/octet-stream')
                        self.assertEqual(preview.read(reference), content)
                        preview.once(('prospective value', head), lambda: b'p' * 4096)
                        self.assertEqual(preview.retention_report()['optional_bytes'],
                                         root.retention_report()['optional_bytes'])
                        self.assertLessEqual(root.retention_report()['optional_bytes'], 8192)
                        with self.assertRaises(ResearchError) as missing:
                            root.read(reference)
                        self.assertEqual(missing.exception.code, 'artifact_missing')
                    with self.assertRaises(ResearchError):
                        preview.read(reference)
                self.assertEqual(root.retention_report()['planning_live_bytes'], 0)
                self.assertEqual(root.retention_report()['planning_peak_bytes'], 131072)
                print('planning peak derivative bytes: 131072')
            fresh = Evaluation({}, store, retention_budget_bytes=8192)
            with self.assertRaises(ResearchError):
                fresh.read(reference)
            self.assertEqual(fresh.retention_report()['planning_peak_bytes'], 0)

    def test_runtime_scope_drift_refuses_public_plan_and_exact_dispatch(self):
        from itertools import cycle
        from research_harness import review_series, review_state_evaluation
        from research_harness.errors import ResearchError

        form = forms.build_turn(self, 'session-v4', 'request')
        healthy = form.plan()
        fingerprint = cycle(('before', 'after'))
        revision = form.case.store.revision
        with patch.object(review_state_evaluation, '_runtime_fingerprint', side_effect=fingerprint), patch.object(
                forms.protocol.transport, 'send_request') as send:
            stopped = form.plan()
            with self.assertRaises(ResearchError) as refused:
                forms.matrix.execute(form.case, 'review-run', healthy['payload'])
            self.assertEqual(refused.exception.code, 'review_runtime_changed')
            self.assertEqual((stopped['state'], stopped.get('code')), ('stop', refused.exception.code))
            self.assertEqual(stopped['details']['message'], refused.exception.message)
            self.assertEqual(stopped['details']['step']['command'], 'review-series')
            self.assertIn(refused.exception.code, review_series.REPLAN_CODES)
            send.assert_not_called()
        self.assertEqual(form.case.store.revision, revision)

    def test_full_status_limits_assignment_reads_at_eight_and_thirty_two_heads(self):
        from statistics import median
        from time import monotonic, process_time
        from research_harness import cli, review_continuation
        from strategy_fixtures import StrategyCase

        class CountedAssignments(dict):
            rows = 0

            def items(self):
                for key, value in super().items():
                    self.rows += 1
                    yield key, value

            def values(self):
                for _, value in self.items():
                    yield value

        case = forms.prepare_unit_fixture(self, StrategyCase, 'runTest')
        case.route()
        snapshot = case.store.snapshot
        plan_next = review_continuation.plan_next
        for index in range(1, 33):
            case.mutate(forms.protocol.record_assignment, dict(id='head-' + str(index), route_id='route', dossier_id=None,
                role='standalone', reviewer_id='reader-' + str(index), author_id='author',
                context={'artifact_refs': [case.evidence]}))
            if index not in (8, 32):
                continue
            with self.subTest(heads=index):
                counts, walls, cpus = [], [], []
                for _ in range(4):
                    tables, planned = [], []

                    def inspect_snapshot():
                        value = snapshot()
                        table = CountedAssignments(value['records']['review_assignment'])
                        value['records']['review_assignment'] = table
                        tables.append(table)
                        return value

                    def inspect_plan(*args, **kwargs):
                        plan = plan_next(*args, **kwargs)
                        planned.append((plan['head_id'], plan['state']))
                        return plan

                    with patch.object(case.store, 'snapshot', inspect_snapshot), patch.object(
                            review_continuation, 'plan_next', inspect_plan):
                        wall, cpu = monotonic(), process_time()
                        cli.status_report(case.store)
                        walls.append(monotonic() - wall)
                        cpus.append(process_time() - cpu)
                    self.assertEqual(len(tables), 1)
                    self.assertEqual(sorted(planned), sorted(('head-' + str(n), 'run') for n in range(1, index + 1)))
                    counts.append(tables[0].rows)
                print(f'status {index} heads: rows {counts}, median wall {median(walls):.4f} s, CPU {median(cpus):.4f} s')
                self.assertLessEqual(max(counts), {8: 552, 32: 8352}[index])


class SixthRoundTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_carried_reading_of_a_released_and_a_math_chain_admits_the_next_chain(self):
        """A healthy earlier chain is carried into the check and the record of a new chain, as release 0.92.0 does;
        a required object that is missing still refuses both."""
        from research_harness import openness_reading, review_protocol
        from research_harness.check_mode import check_mutation
        from research_harness.errors import ResearchError
        from test_openness_reading_math_v2 import MathV2RecoveryTests
        from test_openness_reading_v7_turns import V7CommandTests

        class ReleasedReading(V7CommandTests):
            text_sizes = (1000,)

        class MathReading(MathV2RecoveryTests):
            text_sizes = (1000,)

        for fixture, build, protocol_id in ((ReleasedReading, 'export', openness_reading.PROTOCOL_V7),
                                             (MathReading, 'build_math_packet', openness_reading.MATH_PROTOCOL_V2)):
            with self.subTest(protocol=protocol_id):
                owner = unittest.TestCase()
                try:
                    case = forms.prepare_unit_fixture(owner, fixture, 'runTest')
                    case.assign('earlier', getattr(case, build)(assignment_id='earlier'))
                    case.invoke('earlier', case.build_v7_answer(case.read_packet('earlier')))
                    native = getattr(case, build)(assignment_id='next', name='next-export')
                    payload = dict(id='next', route_id='route', dossier_id=None, role='native_math',
                                   reviewer_id='openness-reviewer', author_id='problem-author',
                                   context={'native_packet': native})

                    def check():
                        return check_mutation(case.store, review_protocol.record_assignment, payload,
                                              expected_revision=case.store.revision, request_id='next',
                                              bounded_payload=payload)

                    def read_folder():
                        return {path: path.read_bytes() for path in (case.root / 'research/sources').rglob('*')
                                if path.is_file()}

                    folder, revision = read_folder(), case.store.revision
                    self.assertEqual([check()['check'] for _ in range(2)], ['accepted', 'accepted'])
                    self.assertEqual((read_folder(), case.store.revision), (folder, revision))
                    required = case.store.snapshot()['records']['review_assignment']['earlier']['packet']
                    path = case.root / required['path']
                    parked = path.with_name('parked')
                    path.rename(parked)
                    try:
                        for admit in (check, lambda: case.assign('next', native)):
                            with self.assertRaises(ResearchError) as missing:
                                admit()
                            self.assertEqual((missing.exception.code, missing.exception.details),
                                             ('artifact_missing', dict(path=required['path'])))
                    finally:
                        parked.rename(path)
                    self.assertEqual(case.store.revision, revision)
                    assignment = case.assign('next', native)
                    self.assertEqual((assignment['prompt']['template_id'], len(assignment['carried_readings']['outputs'])),
                                     (protocol_id + '/reading', 1))
                finally:
                    owner.doCleanups()

    def test_spent_allowance_stops_a_fresh_staged_task_until_a_grant(self):
        import json
        import sys
        from research_harness import review_budget, review_worker
        from research_harness.errors import ResearchError
        import test_review_staged_frontier as frontier
        from test_review_continuation_staged import StagedContinuationTests

        staged = forms.prepare_unit_fixture(self, StagedContinuationTests, 'runTest')
        staged.execute(staged.plan())
        healthy = staged.plan()
        self.assertEqual((healthy['state'], healthy['command']), ('run', 'review-run'))
        charged = review_budget.read_budget_usage(staged.case.read_records())['charged_calls']
        staged.execute(dict(command='review-budget', payload=dict(max_calls=charged, reason='All calls charged.')),
                       request='spent')
        with patch.object(review_worker, 'launch_worker', side_effect=AssertionError('worker')) as worker:
            plans = [staged.plan(), staged.plan()]
            self.assertEqual(plans[0], plans[1])
            blocked = plans[0]
            self.assertEqual((blocked['state'], blocked.get('code')), ('stop', 'review_budget_exhausted'))
            step = blocked['details']['decision']['then'][0]['steps'][0]
            self.assertEqual(step['command'], 'review-budget')
            with self.assertRaises(ResearchError) as refused:
                staged.execute(healthy, request='spent-run')
            self.assertEqual(refused.exception.code, blocked['code'])
            worker.assert_not_called()
        staged.execute(dict(command=step['command'], payload=dict(max_calls=charged + 1, reason='One more call.')),
                       request='grant')
        granted = staged.plan()
        self.assertEqual((granted['state'], granted['command'], granted['payload']),
                         ('run', 'review-run', healthy['payload']))
        output = staged.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (staged.case.root / 'scientific.json').write_text(json.dumps(output))
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]):
            staged.execute(granted, request='granted-run')
        self.assertEqual(len((staged.case.root / 'calls').read_text().splitlines()), 1)

    def test_planning_view_reads_the_store_and_reaches_no_publication(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from research_harness.artifacts import ArtifactStore, _CheckEffectRequired, _StagedArtifacts
        from research_harness.evaluation import Evaluation
        from research_harness.record_check import OfflineArtifacts
        from research_harness.review_staged_protocol import ControllerArtifacts

        def read_folder(root):
            return {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}

        with TemporaryDirectory() as folder:
            root = Path(folder)
            store = ArtifactStore(root)
            stored = store.put(b'stored', 'text/plain')
            for source in (store, _StagedArtifacts(root), ControllerArtifacts(store), OfflineArtifacts(root, [])):
                prepared = source.put(b'prepared', 'text/plain') if isinstance(source, ControllerArtifacts) else None
                for supplied in (source, Evaluation({}, source)):
                    with self.subTest(wrapper=type(source).__name__, evaluated=supplied is not source):
                        before = read_folder(root)
                        with Evaluation({}, supplied).planning_view() as preview:
                            view = preview.store
                            self.assertEqual(view.read(ref=stored), b'stored')
                            view.verify(stored)
                            with view.open_verified(stored) as frozen:
                                self.assertEqual(frozen.stream.read(), b'stored')
                            planned = view.put(b'planned', 'text/plain')
                            self.assertEqual(view.read(planned), b'planned')
                            for name in ('publish', 'put_stream', 'put_file', 'read_digests'):
                                self.assertFalse(hasattr(view, name), name)
                            if isinstance(source, OfflineArtifacts):
                                with self.assertRaises(_CheckEffectRequired):
                                    view.require_external_effect()
                        self.assertEqual(read_folder(root), before)
                if prepared is not None:
                    source.publish()
                    self.assertEqual(store.read(prepared), b'prepared')

    def test_nested_plan_evaluations_share_one_arena_and_forget_what_they_staged(self):
        from research_harness import review_continuation
        from research_harness.artifacts import describe_artifact
        from research_harness.errors import ResearchError
        from research_harness.evaluation import Evaluation
        from research_harness.openness_reading_v7 import _PlanningArtifacts

        form = forms.build_turn(self, 'session-v4', 'closing')
        records = form.records()
        root = Evaluation(records, form.case.artifacts, retention_budget_bytes=8192)
        arenas, retain = set(), Evaluation._retain_once

        def observe(evaluation, *args, **kwargs):
            arenas.add(id(evaluation._optional))
            value = retain(evaluation, *args, **kwargs)
            self.assertLessEqual(evaluation._retained_bytes[0], 8192)
            return value

        with patch.object(Evaluation, '_retain_once', observe):
            self.assertEqual(review_continuation.plan_next(records, root, form.head)['state'], 'run')
        self.assertEqual(arenas, {id(root._optional)})
        report = root.retention_report()
        self.assertEqual((report['planning_live_bytes'], root._planning_accounting['entries']), (0, None))

        content = b'b' * 32768
        reference = describe_artifact(content, 'application/octet-stream')
        with self.assertRaises(ResearchError), root.planning_view() as preview:
            nested = _PlanningArtifacts(preview)
            nested.stage_bodies({reference['sha256']: content})
            self.assertEqual(nested.read(reference), content)
            self.assertEqual(root.retention_report()['planning_live_bytes'], len(content))
            inner = Evaluation.of(records, nested)
            self.assertIs(inner._optional, root._optional)
            self.assertIs(inner._runtime_scope, root._runtime_scope)
            for index in range(64):
                inner.once(('planned', index), lambda: b'p' * 512)
            entries = root._planning_accounting['entries']
            self.assertLessEqual(len(entries), len(root._optional) + len(root._requests))
            self.assertLessEqual(root.retention_report()['optional_bytes'], 8192)
            raise ResearchError('review_runtime_changed', 'The plan ends here.')
        self.assertEqual(root.retention_report()['planning_live_bytes'], 0)
        self.assertFalse([key for key in root._optional if isinstance(key, tuple) and inner._namespace in key])

    def test_fifth_round_route_fixtures_leave_the_platform_caches_cold(self):
        import platform
        from strategy_fixtures import StrategyCase

        with patch.object(platform, '_platform_cache', {}) as cold, patch.object(platform, '_uname_cache', None):
            fixture = FifthRoundTests('test_corrupt_existing_request_is_refused_before_reservation')
            fixture.setUp()
            try:
                forms.prepare_unit_fixture(fixture, StrategyCase, 'runTest').route()
            finally:
                fixture.doCleanups()
            self.assertEqual((cold, platform._uname_cache), ({}, None))



class ControllerObjectiveStopTests(unittest.TestCase):
    def test_math_v2_native_without_a_controller_directory_stops_with_the_status_step(self):
        """A math v2 native whose root is no controller directory: the plan after the approved final asks the
        controller for the recording, finds no objective, and stops with the registry row and its status step. The
        store takes that step, and the plan stays the same stop."""
        from research_harness import cli, openness_reading as reading
        from test_openness_reading_math_v2 import MathV2RecoveryTests

        case = forms.prepare_unit_fixture(self, MathV2RecoveryTests,
                                          'test_reading_at_output_limit_is_replaced_at_next_lower_level')
        native = case.build_math_packet()
        case.assign('math-1', native)
        form = forms.FormTurn('math-v2', 'reading', case, case, 'math-1')
        while reading.find_stage(form.packet()) == 'reading':
            case.invoke(form.head, form.answer())
            following = 'math-%d' % (len(form.records()['review_assignment']) + 1)
            case.assign(following, native, prior=form.head)
            form.head = following
        self.assertEqual(reading.find_stage(form.packet()), 'final')
        case.invoke(form.head, form.answer())
        plan = form.plan()
        self.assertEqual((plan['state'], plan['code'], plan['details']['step']['command']),
                         ('stop', 'native_review_objective_unavailable', 'status'))
        self.assertEqual(stop_classes.classify_stop(plan['code']), 'decision')
        revision = case.store.revision
        cli.status_report(case.store)
        self.assertEqual(case.store.revision, revision)
        self.assertEqual(form.plan(), plan)


if __name__ == '__main__':
    unittest.main()
