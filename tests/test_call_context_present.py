"""The tests of rules PC1 to PC8: a reviewer call and the plan of a head read the context of the present.

Each store is a fixture store that the commands build. An event then takes the verified context from a turn: a probe
of its route record that returns no answer, or a contamination event that names it. Each regression test (W1 to W15)
fails before the rule at the assertion that its docstring names and passes with the rule. K1 to K7 are contract
tests: they hold what stays and pass on both trees (K5 needs the function of the rule). The joint test of the rule
with the route rule, a turn on another route record that is stale, is in tests/test_review_route_plan.py.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, invoke_codex, probe_codex_route, record_codex_route
from openness_reading_fixtures import OpennessReadingCase
from test_openness_carried_integration import CarriedIntegrationSupport
import test_review_session_v4_refresh_edges as refresh_edges
from research_harness import cli, openness_reading_v7 as v7, review_output_correction as correction
from research_harness import review_excerpts, review_protocol as protocol, review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series

UNVERIFIED, CONTAMINATED = 'review_context_unverified', 'review_context_contaminated'


def read_records(case):
    return case.store.snapshot()['records']


def read_plan(case, head):
    records = read_records(case)
    return plan_next(records, Evaluation(records, ArtifactStore(case.store.root)), head)


def describe_plan(case, head):
    plan = read_plan(case, head)
    return plan['state'], plan.get('command'), plan.get('code')


def fail_probe(case, route_id, probe_id='failed-probe'):
    """review-probe whose call returns no answer."""
    with patch.object(transport, 'send_request', side_effect=ResearchError(
            'fixture_probe_failed', 'The authored probe failed.')):
        protocol.probe_route(case.store, dict(id=probe_id, route_id=route_id), credential='test-only',
                             expected_revision=case.store.revision, request_id=probe_id)


def build_response(route, request, output):
    if route['adapter'].startswith('codex_cli'):
        return build_codex_response(route, request, output)
    return {'id': 'provider-response', 'model': route['model'], 'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': json.dumps(output)}]}],
            'usage': {'input_tokens': 3000000 if 'allowed_control' in output else 37, 'output_tokens': 12}}


def complete_probe(case, route_id, probe_id='completed-probe'):
    """review-probe whose call returns both controls."""
    def respond(route, request, credential):
        content = request['input'][0]['content']
        packet = json.loads(content[content.index('{'):])
        return build_response(route, request, {'allowed_control': packet['allowed_control'],
            'evidence_control': packet['evidence_control'], 'excluded_controls': []})
    with patch.object(transport, 'send_request', side_effect=respond):
        protocol.probe_route(case.store, dict(id=probe_id, route_id=route_id), credential='test-only',
                             expected_revision=case.store.revision, request_id=probe_id)


def contaminate(case, assignment_id, event_id='exposure'):
    exposure = ArtifactStore(case.store.root).put(b'Synthetic exposure of a turn.', 'text/plain')
    protocol.record_context_event(case.store, dict(id=event_id, assignment_id=assignment_id, kind='contamination',
        source='author_history', reason='The author history reached the reviewer.', evidence=[exposure]),
        expected_revision=case.store.revision, request_id=event_id)


def run_directly(case, head, identifier=None):
    """review-run for a recorded assignment. Return (the refusal or None, the number of requests that were sent)."""
    sent = []

    def respond(route, request, credential):
        sent.append(1)
        raise ResearchError('fixture_transport_failed', 'The stub transport returns no answer.')
    identifier = identifier or 'direct-' + head
    with patch.object(transport, 'send_request', side_effect=respond):
        try:
            protocol.invoke_assignment(case.store, dict(id=identifier, assignment_id=head), credential='test-only',
                                       expected_revision=case.store.revision, request_id=identifier)
        except ResearchError as error:
            return error, len(sent)
    return None, len(sent)


class RoleFixtures:
    """One recorded assignment that has not run, on a route record with a completed probe, for each role."""

    def start(self, case):
        self.opened = getattr(self, 'opened', []) + [case]
        self.addCleanup(self.close_cases)
        case.setUp()
        return case

    def close_cases(self):
        """Run the cleanups of the fixtures of one role before the next role builds its own."""
        for case in reversed(getattr(self, 'opened', [])):
            case.doCleanups()
        self.opened = []

    def build_problem_case(self):
        import test_research_problem_reviews as fixtures
        case = self.start(fixtures.ProblemReviewTests())
        case.supporting = case.artifacts.put(b'A captured field standard.', 'text/plain')
        case.prepare_review_route()
        return case

    def build_bar(self):
        case = self.build_problem_case()
        return case, case.assign('bar', 'first', **case.deliver_supporting('first'))['id']

    def build_bar_continuation(self):
        case = self.build_problem_case()
        parent = case.assign('bar', 'first', **case.deliver_supporting('first'))
        output = case.build_review_output('bar')
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        output['value']['status'] = 'unresolved'
        with patch('research_harness.review_transport.send_request',
                   return_value=case.build_provider_response(output)):
            case.mutate(protocol.invoke_assignment, dict(id='parent-run', assignment_id=parent['id']),
                        credential='fixture')
        plan = plan_next(case.read_records(), case.artifacts, parent['id'])
        child = case.mutate(cli.OPERATIONS[plan['command']], plan['payload'])['result']
        return case, child['id']

    def build_adjudicator(self):
        import test_research_problem_reviews as fixtures
        case = self.start(fixtures.ProblemReviewTests())
        case.prepare_below_threshold_reviews()
        identifier = fixtures.rubric_objection_id('review-result-first', 'priority')
        return case, case.assign('adjudicator', 'focused-third', identifier='rubric-focused',
            review_ids=['review-result-first', 'review-result-second'], objection_id=identifier)['id']

    def build_standalone(self):
        from test_research_review_protocol import ReviewProtocolTests
        case = self.start(ReviewProtocolTests())
        case.route()
        case.run_probe()
        return case, case.assign()['result']['id']

    def build_verification(self):
        from test_review_parts_study import VerificationPartsTests
        outer = self.start(VerificationPartsTests())
        return outer.case, outer.assign()['id']

    def build_solver_review(self):
        from test_review_parts_native import NativePartsTests
        case = self.start(NativePartsTests())
        return case, case.assign()['id']

    def build_reading_turn(self):
        case = self.start(OpennessReadingCase())
        case.limit_input()
        return case, case.assign('turn-1', case.export())['id']

    ROLES = ('bar', 'bar_continuation', 'adjudicator', 'standalone', 'verification', 'solver_review', 'reading_turn')


class V7ChainSupport(CarriedIntegrationSupport):
    """The v7 chain 'new' on 'second-route'. It carries turn-1 of an earlier chain, which is on 'route', so an event
    on 'second-route' or on a turn of 'new' touches the context of the chain itself alone.

    The events of W8, W9, W12 and W14 stand after the second and the third turn and after the final call, so the
    chain needs three reading turns and a final. On the route of the fixture three texts of 120,000 characters give
    them; three texts of 100,000 characters give two reading turns and a final (measured)."""
    text_sizes = (120000,) * 3

    def setUp(self):
        super().setUp()
        record_codex_route(self, 'second-route', model=self.route_model, output_tokens=self.route_output_tokens)

    def build_payload(self, native, identifier='new', prior=None):
        context = dict(native_packet=native)
        if prior is not None:
            context['prior_assignment_id'] = prior
        return dict(id=identifier, route_id='second-route', dossier_id=None, role='native_math',
                    reviewer_id='openness-reviewer', author_id='problem-author', context=context)

    def start_chain(self):
        native = self.export(name='new-export', assignment_id='new')
        receipt = self.mutate(protocol.record_assignment, self.build_payload(native))['result']
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        return native

    def answer_final(self, packet):
        binding = packet['reading_final']
        return dict(schema_version=3, **{key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
            findings=dict({key: 'Assessed the supplied subject.' for key in
                           ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                openness=dict(outcome='unresolved', coverage='Reviewed the required units.', reason='A remaining gap.',
                    grounds=[dict(kind='own_argument' if 'finding_index' in packet else 'reviewer_gap',
                                  text='The reviewer argument requires further checking.' if 'finding_index' in packet
                                  else 'Further work is needed.')])))

    def run_turn(self, identifier, answer=None):
        packet = self.read_packet(identifier)
        output = answer if answer is not None else self.answer_v7(packet)
        with patch.object(protocol.transport, 'send_request',
                          side_effect=lambda route, request, credential: build_codex_response(route, request, output)):
            return protocol.invoke_assignment(self.store, dict(id='attempt-' + identifier, assignment_id=identifier),
                credential='test-only', expected_revision=self.store.revision, request_id='invoke-' + identifier)

    def start_two_turns(self):
        """The first own turn is observed, and the second turn is recorded and has not run."""
        native = self.start_chain()
        self.run_turn('new')
        self.mutate(protocol.record_assignment, self.build_payload(native, 'new-t2', prior='new'))
        return native

    def lose_call(self, head):
        with patch.object(protocol.transport, 'send_request', side_effect=ResearchError(
                'fixture_transport_failed', 'The stub transport returns no answer.')):
            protocol.invoke_assignment(self.store, dict(id='lost-' + head, assignment_id=head), credential='test-only',
                expected_revision=self.store.revision, request_id='lost-' + head)

    def drive(self, head, *, after_call=None, after_assignment=None, calls_before=1):
        """Run review-series with a stub transport. The named function is called once: after the line of the reviewer
        call number calls_before, or after the line of the assignment that follows it. Return (exit code, lines, one
        flag for each reviewer call: made after the event or not)."""
        calls, lines, written = [], [], []

        def respond(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            calls.append(bool(written))
            return build_codex_response(route, request, self.answer_v7(packet) if 'reading_turn' in packet
                                        else self.answer_final(packet))

        def emit(row):
            lines.append(row)
            command = 'review-run' if after_call is not None else 'review-assignment'
            event = after_call or after_assignment
            if (event is not None and not written and row.get('command') == command and 'error' not in row
                    and len(calls) >= calls_before):
                event()
                written.append(True)

        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            code = run_series(self.store, head, emit=emit)
        return code, lines, calls

    def read_status_rows(self, head):
        report = cli._build_status_report(self.store)
        return [(row['code'], [(action.get('state'), action.get('command'), action.get('code'))
                               for action in row['next']])
                for row in report['obligations'] if row.get('assignment_id') == head]

    def assert_own_turn(self, details, *, head, turn, events, count=1):
        self.assertEqual(details['assignment_id'], head)
        self.assertEqual(details['turn'], dict(assignment_id=turn, events=events,
            route=dict(id='second-route', context_status='verified_for_route'), count=count))


class CallContextRoleTests(RoleFixtures, unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_w1_review_run_waits_for_a_probe_of_its_route_record_in_every_role(self):
        """On 12ce6199 review-run sends the request ('ResearchError not raised'), except for the bar continuation,
        whose refusal has another code and no step."""
        for role in self.ROLES:
            with self.subTest(role=role):
                case, head = getattr(self, 'build_' + role)()
                route_id = read_records(case)['review_assignment'][head]['route_id']
                self.assertEqual(describe_plan(case, head)[:2], ('run', 'review-run'))
                fail_probe(case, route_id)
                refusal, sent = run_directly(case, head)
                self.assertIsNotNone(refusal, 'review-run sent the request')
                self.assertEqual((refusal.code, sent), (UNVERIFIED, 0))
                self.assertEqual(refusal.message, protocol.CONTEXT_UNVERIFIED_MSG)
                self.assertEqual(refusal.details['turn'], dict(assignment_id=refusal.details['turn']['assignment_id'],
                    events=[], route=dict(id=route_id, context_status='unverified'),
                    count=refusal.details['turn']['count']))
                following = refusal.details['next']
                self.assertEqual((following['state'], following['command'], following['code']),
                                 ('handoff', 'review-probe', UNVERIFIED))
                self.assertEqual(following['details']['step']['payload'], dict(route_id=route_id))
                self.assertEqual(read_records(case).get('review_invocation', {}).get('direct-' + head), None)
                complete_probe(case, route_id)
                self.assertEqual(describe_plan(case, head)[:2], ('run', 'review-run'))
                refusal, sent = run_directly(case, head)
                self.assertEqual((refusal, sent), (None, 1))
            self.close_cases()

    def test_w2_review_run_is_refused_after_a_contamination_of_its_assignment_in_every_role(self):
        """On 12ce6199 review-run sends the request: 'ResearchError not raised'."""
        steps = dict(bar='review-assignment', bar_continuation='review-assignment', adjudicator='review-assignment',
                     standalone='review-assignment', verification='review-assignment',
                     solver_review='search review-packet', reading_turn='export')
        for role in self.ROLES:
            with self.subTest(role=role):
                case, head = getattr(self, 'build_' + role)()
                contaminate(case, head)
                refusal, sent = run_directly(case, head)
                self.assertIsNotNone(refusal, 'review-run sent the request')
                self.assertEqual((refusal.code, sent), (CONTAMINATED, 0))
                self.assertEqual(refusal.message, protocol.CONTEXT_CONTAMINATED_MSG)
                self.assertEqual((refusal.details['turn']['assignment_id'], refusal.details['turn']['events']),
                                 (head, ['exposure']))
                following = refusal.details['next']
                self.assertEqual((following['state'], following['command'], following['code']),
                                 ('handoff', steps[role], CONTAMINATED))
            self.close_cases()

    def test_w3_staged_task_waits_for_a_probe_of_its_route_record(self):
        """On 12ce6199 the task reaches its dispatch: the refusal is the one that the patched dispatch raises."""
        from test_review_continuation_staged import StagedContinuationTests
        fixture = StagedContinuationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.execute(fixture.plan())
        planned = fixture.plan()
        self.assertEqual((planned['state'], planned['command']), ('run', 'review-run'))
        fail_probe(fixture.case, 'route')
        with patch('research_harness.review_staged_protocol.wait_for_capture',
                   side_effect=ResearchError('fixture_dispatch_reached', 'The task reached its dispatch.')):
            with self.assertRaises(ResearchError) as caught:
                fixture.execute(planned, request='direct-staged')
        self.assertEqual(caught.exception.code, UNVERIFIED)
        self.assertEqual(describe_plan(fixture.case, fixture.controller['id']), ('handoff', 'review-probe', UNVERIFIED))

    def test_w4_plan_of_a_head_with_a_failed_call_is_the_handoff(self):
        """A head whose first call returned no answer. On 12ce6199 the plan stays the retry of the call after the
        probe of its route record failed and after a contamination event named it: the first assertion on the plan
        fails with ('run', 'review-run', 'review_failure_unknown')."""
        for event, expected in (('probe', ('handoff', 'review-probe', UNVERIFIED)),
                                ('contamination', ('handoff', 'review-assignment', CONTAMINATED))):
            with self.subTest(event=event):
                case, head = self.build_bar()
                refusal, sent = run_directly(case, head, 'lost-call')
                self.assertEqual((refusal, sent), (None, 1))
                self.assertEqual(describe_plan(case, head)[:2], ('run', 'review-run'))
                if event == 'probe':
                    fail_probe(case, 'route')
                else:
                    contaminate(case, head)
                self.assertEqual(describe_plan(case, head), expected)
                refusal, sent = run_directly(case, head)
                self.assertEqual((refusal.code, sent), (expected[2], 0))
            self.close_cases()

    def test_w5_earlier_turn_of_a_session_gives_the_repair_handoff(self):
        """A bar continuation whose first turn a contamination event names. On 12ce6199 the plan is the stop
        review_source_parent_unverified, whose step is record-check: the assertion on the plan fails."""
        case, head = self.build_bar_continuation()
        parent = read_records(case)['review_assignment'][head]['history_assignment_id']
        contaminate(case, parent)
        plan = read_plan(case, head)
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')),
                         ('handoff', 'review-assignment', CONTAMINATED))
        self.assertEqual(plan['details']['turn'], dict(assignment_id=parent, events=['exposure'],
            route=dict(id='route', context_status='verified_for_route'), count=1))
        repair = plan['details']['step']['payload']['context']['context_repair']
        self.assertEqual((repair['assignment_id'], repair['event_ids']), (head, ['exposure']))
        refusal, sent = run_directly(case, head)
        self.assertEqual((refusal.code, sent), (CONTAMINATED, 0))

    def test_w6_parts_series_stops_for_a_turn_before_the_prior_turn(self):
        """The third turn of a math-solver review in parts, after a contamination event on its first turn. On
        12ce6199 review-run sends the request, because the call reads the state of the prior turn alone."""
        from review_parts_fixtures import make_output
        from test_review_parts_native import NativePartsTests
        case = self.start(NativePartsTests())
        case.limit_input()
        first = case.assign('first')
        invoke_codex(case, first['id'], make_output(case.read_packet(first)))
        second = case.assign('turn-2', prior=first['id'])
        self.assertNotEqual(case.read_packet(second)['review_parts']['kind'], 'final')
        invoke_codex(case, second['id'], make_output(case.read_packet(second)))
        third = case.assign('turn-3', prior=second['id'])
        contaminate(case, first['id'])
        refusal, sent = run_directly(case, third['id'])
        self.assertIsNotNone(refusal, 'review-run sent the request')
        self.assertEqual((refusal.code, sent), (CONTAMINATED, 0))
        self.assertEqual(refusal.details['turn']['assignment_id'], first['id'])
        self.assertEqual(describe_plan(case, third['id']), ('handoff', 'search review-packet', CONTAMINATED))

    def test_k1_assignment_recorded_on_an_unverified_route_stays_dead(self):
        """The frozen context stays the first condition: a later completed probe never makes the assignment run."""
        case, head = self.build_bar()
        fail_probe(case, 'route')
        late = case.assign('bar', 'second', **case.deliver_supporting('second'))
        self.assertEqual(late['context_state']['context_status'], 'unverified')
        complete_probe(case, 'route')
        refusal, sent = run_directly(case, late['id'])
        self.assertEqual((refusal.code, sent), (UNVERIFIED, 0))
        self.assertIn('when the assignment was recorded', refusal.message)
        self.assertEqual(describe_plan(case, late['id'])[:2], ('replace', 'review-assignment'))

    def test_k2_context_repair_starts_a_new_context(self):
        """The call of a repair is sent while a contamination event names the turn that it repairs."""
        from test_research_repair_slot_replacement import RepairSlotReplacementTests
        case = self.start(RepairSlotReplacementTests())
        case.prepare_repair()
        self.assertEqual(describe_plan(case, 'repair-a')[:2], ('run', 'review-run'))
        refusal, sent = run_directly(case, 'repair-a')
        self.assertEqual((refusal, sent), (None, 1))

    def test_k3_recorded_request_is_replayed_after_the_event(self):
        """A review-run request that is already committed returns its receipt after the probe failed: the replay
        starts no call, so it passes no check of a new call."""
        case, head = self.build_bar()
        output = case.build_review_output('bar')
        with patch('research_harness.review_transport.send_request',
                   return_value=case.build_provider_response(output)):
            first = case.store.revision
            receipt = protocol.invoke_assignment(case.store, dict(id='observed', assignment_id=head),
                credential='test-only', expected_revision=first, request_id='observed-call')
        fail_probe(case, 'route')
        with patch('research_harness.review_transport.send_request', side_effect=AssertionError('a call started')):
            again = protocol.invoke_assignment(case.store, dict(id='observed', assignment_id=head),
                credential='test-only', expected_revision=first, request_id='observed-call')
        self.assertEqual(again['result']['id'], receipt['result']['id'])


class CallContextRefreshTests(refresh_edges.EdgeFixture):
    @classmethod
    def setUpClass(cls):
        fixture = refresh_edges.EdgeFixture()
        cls.addClassCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.add_sources([100])
        first = fixture.case.assign('bar', 'first', identifier='request', **fixture.case.deliver_supporting('first'))
        cls.original, unit = fixture.drive(first)
        fixture.finish(cls.original, [dict(ref=unit['handle'])])
        fixture.route('refresh-route', timeout=7)
        cls.original_case = fixture.case
        seeds = tempfile.TemporaryDirectory()
        cls.addClassCleanup(seeds.cleanup)
        with fixture.fork():
            fixture.add_area()
            plan = fixture.plan(cls.original['id'])
            payload = dict(plan['payload'], route_id='refresh-route')
            cls.refresh = fixture.record(payload)
            fixture.assert_new_chain_in_slot(cls.refresh, cls.original)
            cls.refresh_seed = fixture.save(Path(seeds.name) / 'refresh')

    def setUp(self):
        self.case = self.original_case

    def assert_readers(self, assignment, context_status, obligation_code, action):
        identifier = assignment['id']
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, identifier)
        self.assertEqual(state['context_status'], context_status)
        self.assertEqual([row['code'] for row in state['obligations'] if row['code'].startswith('review_context_')],
                         [obligation_code] if obligation_code.startswith('review_context_') else [])
        self.assertIn(obligation_code, [row['code'] for row in state['obligations']])
        plan = self.plan(identifier)
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), action)
        report = cli._build_status_report(self.case.store)
        rows = [row for row in report['obligations'] if row.get('assignment_id') == identifier]
        self.assertEqual([row['code'] for row in rows if row['code'].startswith('review_context_')],
                         [obligation_code] if obligation_code.startswith('review_context_') else [])
        row = next(row for row in rows if row['code'] == obligation_code)
        fields = ('state', 'command', 'code', 'details', 'payload')
        self.assertEqual([[step.get(key) for key in fields] for step in row['next']],
                         [[plan.get(key) for key in fields]])
        return plan

    def test_k8_refresh_call_hands_off_uncovered_earlier_contamination(self):
        with self.fork(self.refresh_seed):
            self.event(self.original, 'exposure')
            head = self.refresh['id']
            revision = self.case.store.revision
            refusal, sent = run_directly(self.case, head)
            self.assertIsNotNone(refusal)
            self.assertEqual((refusal.code, sent, self.case.store.revision), (CONTAMINATED, 0, revision))
            self.assertIn('turn', refusal.details or {})
            turn = refusal.details['turn']
            self.assertEqual((turn['assignment_id'], turn['events'], turn['route']['id'], turn['count']),
                             (self.original['id'], ['exposure'], self.original['route_id'], 1))
            plan = self.assert_readers(self.refresh, 'contaminated', CONTAMINATED,
                                       ('handoff', 'review-assignment', CONTAMINATED))
            self.assertEqual(refusal.details['next'], plan)
            step = plan['details']['step']
            binding = step['payload']['context']['context_repair']
            self.assertEqual((step['id'], step['command'], binding['assignment_id'], binding['event_ids']),
                             (1, 'review-assignment', head, ['exposure']))
            self.route()
            accepted = self.repair(self.refresh, step)
            self.assertEqual(accepted['context']['context_repair']['event_ids'], ['exposure'])

    def test_refresh_call_ignores_a_failed_probe_of_the_earlier_route(self):
        with self.fork(self.refresh_seed):
            old_route = self.original['route_id']
            self.assertNotEqual(old_route, self.refresh['route_id'])
            fail_probe(self.case, old_route)
            records = self.case.read_records()
            self.assertEqual(protocol.route_state(records, self.case.artifacts, old_route)['context_status'],
                             'unverified')
            self.assert_readers(self.refresh, 'verified_for_route', 'review_output_pending', ('run', 'review-run', None))
            self.assertEqual(run_directly(self.case, self.refresh['id']), (None, 1))

    def test_refresh_call_admitted_when_earlier_contamination_is_covered(self):
        with self.fork():
            self.event(self.original, 'covered')
            self.route()
            repair = self.repair(self.original)
            binding = self.read_logical(repair)['context_repair']
            self.assertEqual(binding['event_ids'], ['covered'])
            final, unit = self.drive(repair)
            self.record_final(final, self.final_answer(unit, binding))
            self.add_area()
            head = self.assign_refresh(final)
            self.assert_readers(head, 'verified_for_route', 'review_output_pending', ('run', 'review-run', None))
            with self.fork():
                self.event(self.original, 'late')
                stale = 'review_context_repair_stale'
                plan = self.assert_readers(head, 'contaminated', stale, ('handoff', 'review-assignment', stale))
                step = plan['details']['step']
                binding = step['payload']['context']['context_repair']
                self.assertEqual((step['id'], binding['event_ids']), (1, ['covered', 'late']))
                refusal, sent = run_directly(self.case, head['id'])
                self.assertIsNotNone(refusal)
                self.assertEqual((refusal.code, sent), (stale, 0))
                self.assertIsNone(refusal.details)
            self.assertEqual(run_directly(self.case, head['id']), (None, 1))


class CallContextCorrectionTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_w15_output_correction_reads_the_context_of_the_review_of_its_result(self):
        """review-output-correction starts a reviewer call too (finding F4 of the review of the design). Its packet
        checks the context of the result itself; the start of the call also reads the earlier turns of the review of
        the result. The store of test_review_output_correction holds a context repair with two refused answers and no
        turn after the repair, so the read of the review records is replaced by an earlier turn that a contamination
        event names; the tests of the other roles hold the read itself. Before the rule the correction is sent:
        'ResearchError not raised'."""
        import test_review_output_correction as fixtures
        case = fixtures.CorrectionTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_refused()
        binding = case.case.assignment['context']['context_repair']
        lost = dict(assignment_id=binding['assignment_id'], events=binding['event_ids'],
                    route=dict(id='route', context_status='verified_for_route'), count=1)
        with patch.object(protocol, 'find_unverified_context', return_value=lost), \
                patch.object(transport, 'send_request', side_effect=AssertionError('a call started')), \
                self.assertRaises(ResearchError) as caught:
            case.fixture.mutate(correction.invoke_correction, dict(
                id='correction', assignment_id=case.case.assignment['id'], route_id='route'), credential='test-only')
        self.assertEqual(caught.exception.code, CONTAMINATED)
        self.assertEqual(caught.exception.details['turn'], lost)
        following = caught.exception.details['next']
        self.assertEqual((following['state'], following['code']), ('handoff', CONTAMINATED))
        self.assertNotIn('correction', case.fixture.read_records().get('review_output_correction_invocation', {}))


class CallContextStaleRouteTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_k7_stale_route_record_keeps_its_refusal_and_its_plan(self):
        """A recorded bar on a route record whose latest probe answered with a wrong control and whose runtime is
        then stale. A stale record takes no probe, so the rules of a stale route decide: review-run is refused with
        review_route_runtime_stale, and the plan of the head is the stale-route procedure of the route rule
        (tests/test_review_route_plan.py), whose first step retires the record (rule PC6)."""
        from test_review_route_renewal import RouteRenewalFixture
        fixture = RouteRenewalFixture()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        head = fixture.assign('bar', 'first', 'stale-head', **fixture.case.deliver_supporting('first'))
        fixture.probe_failure = True
        fixture.probe('route', 'failed-probe')
        fixture.probe_failure = False
        self.assertEqual(describe_plan(fixture.case, head['id']), ('handoff', 'review-probe', UNVERIFIED))
        fixture.make_stale()
        self.assertEqual(describe_plan(fixture.case, head['id']),
                         ('handoff', 'review-context', 'review_route_runtime_stale'))
        with self.assertRaises(ResearchError) as caught:
            fixture.case.mutate(protocol.invoke_assignment, dict(id='stale-run', assignment_id=head['id']),
                                credential='fixture')
        self.assertEqual(caught.exception.code, 'review_route_runtime_stale')
        self.assertEqual([call for call in fixture.calls if call[0] == 'review'], [])


    def test_planned_replacement_on_a_stale_route_record_with_a_failed_probe_keeps_the_stale_route_handoff(self):
        """A v7 reading turn whose call ended over the input limit plans its capacity replacement. The latest probe
        of its route record then returns no answer, and the installed runtime changes. The planner simulates the
        replacement on its artifact overlay. The destination's standing (PC6) still gives the stale-route handoff.
        The original incident was an AttributeError while reading runtime standing through the overlay."""
        from test_openness_reading_v7_refusal import V7RefusalTests
        from test_review_output_profile import build_row
        from research_harness import review_route_capacity as capacity
        fixture = V7RefusalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        priors = patch.object(capacity, 'read_profile_priors', return_value=[
            build_row(level=None, thinking=1000, count=8, over=1)])
        priors.start()
        self.addCleanup(priors.stop)
        native = fixture.export()
        fixture.assign('turn-1', native)
        fixture.invoke('turn-1', fixture.build_v7_answer(fixture.read_packet('turn-1')))
        head = fixture.assign('size-position', native, prior='turn-1')['id']
        with patch.object(protocol.transport, 'send_request', side_effect=ResearchError(
                'review_input_too_large', 'The authored input limit.')):
            fixture.mutate(protocol.invoke_assignment, dict(id='size-call', assignment_id=head),
                           credential='fixture')
        self.assertEqual(describe_plan(fixture, head), ('replace', 'review-assignment', 'review_input_too_large'))
        fail_probe(fixture, 'route')
        pinned = read_records(fixture)['review_route']['route']['runtime']
        with patch.object(protocol.transport, 'runtime', return_value=dict(pinned, implementation_sha256='0' * 64)):
            self.assertEqual(describe_plan(fixture, head),
                             ('handoff', 'review-route', 'review_route_runtime_stale'))


class CallContextChainTests(V7ChainSupport, OpennessReadingCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_w7_recorded_assignment_is_not_sent_after_a_contamination_of_an_own_earlier_turn(self):
        """On 12ce6199 review-run sends the request: 'ResearchError not raised'."""
        self.start_two_turns()
        self.assertEqual(describe_plan(self, 'new-t2'), ('run', 'review-run', None))
        contaminate(self, 'new')
        refusal, sent = run_directly(self, 'new-t2')
        self.assertIsNotNone(refusal, 'review-run sent the request')
        self.assertEqual((refusal.code, sent), (CONTAMINATED, 0))
        self.assert_own_turn(refusal.details, head='new-t2', turn='new', events=['exposure'])
        plan = read_plan(self, 'new-t2')
        self.assertEqual((plan['state'], plan.get('command'), plan.get('code')), ('handoff', 'export', CONTAMINATED))
        self.assert_own_turn(plan['details'], head='new-t2', turn='new', events=['exposure'])
        self.assertEqual(plan['details']['message'], protocol.CONTEXT_CONTAMINATED_MSG)
        self.assertEqual(plan['details']['step']['payload'], dict(kind='problem-openness'))
        self.assertEqual(refusal.details['next']['details']['step'], plan['details']['step'])

    def test_w8_series_ends_with_the_export_after_a_contamination_of_an_own_earlier_turn(self):
        """The event is written after the line of the second call, before the next assignment. On 12ce6199 the
        driver ends with the stop review_source_continuation_unverified, whose step is record-check."""
        self.start_chain()
        code, lines, calls = self.drive('new', after_call=lambda: contaminate(self, 'new'), calls_before=2)
        self.assertEqual(calls, [False, False])
        last = lines[-1]
        self.assertEqual((code, last['state'], last.get('command'), last.get('code')),
                         (3, 'handoff', 'export', CONTAMINATED))
        self.assert_own_turn(last['details'], head='new-t2', turn='new', events=['exposure'])
        self.assertEqual(last['details']['step']['command'], 'export')

    def test_w9_series_sends_no_recorded_assignment_after_a_contamination_of_an_own_earlier_turn(self):
        """The event is written after the line of the third assignment. On 12ce6199 that assignment is sent:
        [False, False, True] != [False, False]."""
        self.start_chain()
        code, lines, calls = self.drive('new', after_assignment=lambda: contaminate(self, 'new'), calls_before=2)
        self.assertEqual(calls, [False, False])
        last = lines[-1]
        self.assertEqual((code, last['state'], last.get('command'), last.get('code')),
                         (3, 'handoff', 'export', CONTAMINATED))
        self.assert_own_turn(last['details'], head='new-t3', turn='new', events=['exposure'])

    def test_w10_next_assignment_names_the_turn_and_the_plan(self):
        """review-assignment for the next turn after a contamination of an own earlier turn. On 12ce6199 it is
        refused with review_source_continuation_unverified, and the plan that its text names is this command."""
        native = self.start_two_turns()
        self.run_turn('new-t2')
        contaminate(self, 'new')
        before = len(read_records(self)['review_assignment'])
        with self.assertRaises(ResearchError) as caught:
            self.mutate(protocol.record_assignment, self.build_payload(native, 'new-t3', prior='new-t2'))
        self.assertEqual(caught.exception.code, CONTAMINATED)
        self.assert_own_turn(caught.exception.details, head='new-t2', turn='new', events=['exposure'])
        following = caught.exception.details['next']
        self.assertEqual((following['state'], following['command'], following['code']),
                         ('handoff', 'export', CONTAMINATED))
        self.assertEqual(len(read_records(self)['review_assignment']), before)

    def test_w11_status_row_of_a_head_with_an_observed_output_holds_the_handoff(self):
        """On 12ce6199 status holds no row of the head: [] != [...]."""
        self.start_two_turns()
        self.run_turn('new-t2')
        self.assertEqual(self.read_status_rows('new-t2'), [])
        contaminate(self, 'new')
        self.assertEqual(self.read_status_rows('new-t2'),
                         [(CONTAMINATED, [('handoff', 'export', CONTAMINATED)])])

    def test_w12_series_makes_no_call_on_a_route_record_whose_probe_failed_after_a_failed_call(self):
        """The first call of the second turn returned no answer, and a probe of the route record of the chain then
        fails. On 12ce6199 review-series sends that turn again: [False] != []. After a completed probe the driver
        makes the remaining calls and records the finding."""
        self.start_two_turns()
        self.lose_call('new-t2')
        fail_probe(self, 'second-route')
        code, lines, calls = self.drive('new-t2')
        self.assertEqual(calls, [])
        last = lines[-1]
        self.assertEqual((code, last['state'], last.get('command'), last.get('code')),
                         (3, 'handoff', 'review-probe', UNVERIFIED))
        self.assertEqual(last['details']['step']['payload'], dict(route_id='second-route'))
        self.assertEqual(last['details']['turn'], dict(assignment_id='new', events=[],
            route=dict(id='second-route', context_status='unverified'), count=2))
        probe_codex_route(self, 'completed-probe', 'second-route')
        # The retry of the lost call waits for the first provider wait; the test does not spend that time.
        with patch('research_harness.review_series.time.sleep'):
            code, lines, calls = self.drive('new-t2')
        # The second turn again, three more reading turns and the final. The frame of a v7 turn reserves its notes
        # cost and its list rows, so each reading turn of this fixture takes 60,000 characters of material.
        self.assertEqual((code, len(calls)), (0, 5))
        self.assertTrue(read_records(self).get('problem_openness'))

    def test_w13_refused_turn_is_not_replaced_after_a_contamination_of_an_own_earlier_turn(self):
        """The second turn returns an answer that its contract refuses, and the plan is its replacement. On
        12ce6199 the plan stays the replacement after the contamination of the first own turn."""
        self.start_two_turns()
        self.run_turn('new-t2', dict(stage='reading', notes=42))
        replacement = read_plan(self, 'new-t2')
        self.assertEqual((replacement['state'], replacement['command']), ('replace', 'review-assignment'))
        contaminate(self, 'new')
        self.assertEqual(describe_plan(self, 'new-t2'), ('handoff', 'export', CONTAMINATED))
        before = len(read_records(self)['review_assignment'])
        with self.assertRaises(ResearchError) as caught:
            self.mutate(protocol.record_assignment, replacement['payload'])
        self.assertEqual(caught.exception.code, CONTAMINATED)
        self.assertEqual(len(read_records(self)['review_assignment']), before)

    def test_w14_returned_finding_gives_the_export_after_a_contamination_of_an_own_earlier_turn(self):
        """The event is written after the line of the final call, before the finding is recorded. On 12ce6199 the
        driver ends with the stop openness_reading_incomplete, whose step is record-check."""
        self.start_chain()
        code, lines, calls = self.drive('new', after_call=lambda: contaminate(self, 'new'), calls_before=4)
        self.assertEqual(calls, [False] * 4)
        last = lines[-1]
        self.assertEqual((code, last['state'], last.get('command'), last.get('code')),
                         (3, 'handoff', 'export', CONTAMINATED))
        self.assert_own_turn(last['details'], head=last['head_id'], turn='new', events=['exposure'])
        self.assertFalse(read_records(self).get('problem_openness'))

    def test_k6_contamination_of_a_replaced_turn_leaves_the_chain(self):
        """A turn whose answer was refused and that a later turn replaced is no turn on which the finding of a
        reading chain rests: after a contamination event on it the chain continues and records its finding."""
        self.start_two_turns()
        self.run_turn('new-t2', dict(stage='reading', notes=42))
        replacement = read_plan(self, 'new-t2')
        self.assertEqual((replacement['state'], replacement['command']), ('replace', 'review-assignment'))
        accepted = self.mutate(protocol.record_assignment, replacement['payload'])['result']
        before = describe_plan(self, accepted['id'])
        self.assertEqual(before[:2], ('run', 'review-run'))
        contaminate(self, 'new-t2')
        self.assertEqual(describe_plan(self, accepted['id']), before)
        code, lines, calls = self.drive(accepted['id'])
        self.assertEqual(code, 0)
        self.assertTrue(read_records(self).get('problem_openness'))

    def test_k4_chain_with_a_recorded_finding_keeps_its_plan(self):
        native = self.start_chain()
        code, lines, calls = self.drive('new')
        self.assertEqual(code, 0)
        head = lines[-1]['head_id']
        self.assertEqual(read_plan(self, head)['state'], 'done')
        contaminate(self, 'new')
        self.assertEqual(read_plan(self, head)['state'], 'done')

    def test_k5_named_turn_is_a_turn_whose_state_lost_its_verified_context(self):
        """The check reads two kinds of record. This test holds it to assignment_state: it names a turn exactly when
        the state of a turn of the chain holds a context obligation."""
        self.start_two_turns()

        def compare(expected):
            records = read_records(self)
            lost = protocol.find_unverified_context(records, self.artifacts, records['review_assignment']['new-t2'])
            codes = {key: sorted(item['code'] for item in protocol.assignment_state(
                records, self.artifacts, key)['obligations'] if item['code'].startswith('review_context_'))
                for key in ('new', 'new-t2')}
            self.assertEqual(lost is not None, any(codes.values()))
            self.assertEqual(None if lost is None else (lost['assignment_id'], lost['count']), expected)
        compare(None)
        fail_probe(self, 'second-route')
        compare(('new', 2))
        probe_codex_route(self, 'completed-probe', 'second-route')
        compare(None)
        contaminate(self, 'new')
        compare(('new', 1))


if __name__ == '__main__':
    unittest.main()
