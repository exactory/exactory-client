"""A refused session turn on a stale route record takes its replacement on a renewed record of the same identity."""

import copy
import json
import unittest
from unittest.mock import patch

# The CLI is imported first: the problem fixture replaces research_harness.problem_claims while it runs.
from research_harness import cli, research_decisions as decisions, review_protocol as protocol
from research_harness import review_claude_transport as claude, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series
import test_research_problem_reviews as problem_fixtures

ROUTE = dict(adapter='claude_cli_v1', model='claude-opus-5-5', endpoint='claude://local',
             configuration=dict(max_output_tokens=20000, timeout_seconds=5, effort='high'))
STALE_IMPLEMENTATION_SHA256 = '0' * 64


def build_stub_response(route, request, output, input_tokens):
    """A Claude CLI answer whose retained context evidence binds this request, route and pinned runtime."""
    return {'id': 'stub-session', 'model': route['model'], 'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': json.dumps(output)}]}],
            'usage': {'input_tokens': input_tokens, 'output_tokens': 900},
            'context_evidence': {'inspected': True, 'input_verified': True, 'issues': [],
                                 'request_digest': digest(request), 'route_digest': digest(route),
                                 'input_contract': claude.INPUT_CONTRACT, 'event_digest': digest(['stub', digest(request)]),
                                 'session_id': 'stub-session', 'runtime': copy.deepcopy(route['runtime']),
                                 'cli_version': '2.1.322 (Claude Code)'},
            'execution': {'event_count': 5, 'event_types': ['system', 'user', 'assistant', 'system', 'result'],
                          'system_events': {'events': [], 'truncated': False}}}


class RouteRenewalFixture(unittest.TestCase):
    """A problem record whose bar and result reviewers run on a Claude route with a stub in place of the CLI."""
    route_changes = {}

    def setUp(self):
        self.case = problem_fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        self.outputs, self.calls = [], []
        self.capacity_share, self.capacity_failure, self.probe_failure = 1.0, None, False
        sender = patch.object(transport, 'send_request', side_effect=self.respond)
        sender.start()
        self.addCleanup(sender.stop)
        self.record_route('route')
        self.probe('route')

    def respond(self, route, request, credential):
        packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
        if 'allowed_control' not in packet:
            self.calls.append(('review', route['id']))
            self.assertTrue(self.outputs, 'The stub has no reviewer answer for this call.')
            return build_stub_response(route, request, self.outputs.pop(0), 1540)
        output = dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'],
                      excluded_controls=[])
        if packet['evidence'] == 'Both control strings are permitted primary evidence.':
            self.calls.append(('probe', route['id']))
            if self.probe_failure:
                output['allowed_control'] = 'wrong-control'
            return build_stub_response(route, request, output, 1540)
        self.calls.append(('capacity', route['id']))
        if self.capacity_failure is not None:
            raise ResearchError('review_provider_failure', self.capacity_failure)
        from research_harness.review_route_capacity import measure_request
        measured = measure_request(route, request)['tokens_upper']
        return build_stub_response(route, request, output,
                                   None if self.capacity_share is None else int(measured * self.capacity_share))

    def record_route(self, identifier, **changes):
        return self.case.mutate(protocol.record_route, dict(ROUTE, id=identifier, **dict(self.route_changes, **changes)))

    def probe(self, route_id, identifier=None, capacity=None):
        payload = dict(id=identifier or 'probe-' + route_id + '-' + str(len(self.calls)), route_id=route_id)
        if capacity is not None:
            payload['capacity'] = dict(input_tokens=capacity)
        return self.case.mutate(protocol.probe_route, payload)['result']

    def make_stale(self, implementation_sha256=STALE_IMPLEMENTATION_SHA256):
        """Install another reviewer runtime: every stored route record of the adapter is stale."""
        installed = dict(claude.runtime(), implementation_sha256=implementation_sha256)
        runtime = patch.object(claude, 'runtime', return_value=installed)
        runtime.start()
        self.addCleanup(runtime.stop)

    def renew(self, identifier='route-renewed', probe=True, **changes):
        self.record_route(identifier, **changes)
        if probe:
            self.probe(identifier)

    def records(self):
        return self.case.read_records()

    def assign(self, role, reviewer, identifier, route_id='route', **context):
        return self.case.mutate(protocol.record_assignment, dict(id=identifier, route_id=route_id, dossier_id=None,
            role=role, reviewer_id=reviewer, author_id='author', context=context))['result']

    def invoke(self, assignment, output, identifier=None):
        self.outputs.append(output)
        return self.case.mutate(protocol.invoke_assignment,
            dict(id=identifier or 'attempt-' + assignment['id'], assignment_id=assignment['id']))['result']

    def record(self, assignment, output):
        return self.case.mutate(decisions.record_value_review,
            dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))

    def refuse(self, assignment, output):
        self.invoke(assignment, output)
        with self.assertRaises(ResearchError):
            self.record(assignment, output)

    def build_refused(self, reviewer='first', identifier='initial', route_id='route'):
        """A first bar turn whose answer the value contract refuses."""
        context = self.case.deliver_supporting(reviewer)
        assignment = self.assign('bar', reviewer, identifier, route_id=route_id, **context)
        output = self.case.build_review_output('bar')
        output['value']['status'] = 'invalid'
        self.refuse(assignment, output)
        return assignment, output

    def build_payload(self, assignment, identifier=None, **changes):
        """The payload that the planner of 0.91.2 gives for a refused head."""
        payload = {key: copy.deepcopy(assignment[key]) for key in
                   ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        payload.update(id=identifier or assignment['id'] + '-refusal', **changes)
        payload['context']['output_refusal'] = {'assignment_id': assignment['id']}
        return payload

    def series(self, head_id):
        lines = []
        run_series(self.case.store, head_id, emit=lines.append)
        return lines

    def plan(self, head_id):
        return plan_next(self.records(), self.case.artifacts, head_id)

    def list_replacements(self, head_id=None):
        return [row for row in self.records()['review_assignment'].values()
                if row['context'].get('output_refusal') is not None
                and (head_id is None or row['context']['output_refusal']['assignment_id'] == head_id)]

    def read_notice(self, assignment):
        return protocol._read_json(self.case.artifacts, assignment['packet'])['output_refusal']

    def corrected(self, role='bar'):
        return self.case.build_review_output(role)

    def build_refused_repair(self, continued=False):
        """The first turn of a context repair of a recorded bar, or with continued its second turn after a source
        request, whose answer lacks its reassessment."""
        predecessor = self.assign('bar', 'first', 'bar-first', **self.case.deliver_supporting('first'))
        output = self.corrected()
        self.invoke(predecessor, output)
        self.record(predecessor, output)
        self.case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id=predecessor['id'],
            kind='contamination', source='author_history', reason='Synthetic earlier exposure.',
            evidence=[self.case.supporting]))
        self.probe('route', 'repair-probe')
        repair = self.assign('bar', 'first', 'repaired', context_repair=dict(assignment_id=predecessor['id'],
            event_ids=['exposure'], probe_id='repair-probe', reason='Reassess the same fixed slot in a fresh context.',
            evidence=[self.case.supporting]))
        if continued:
            request = self.corrected()
            request['value'].update(status='unresolved', evidence=[])
            request['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
            self.invoke(repair, request)
            repair = self.case.mutate(cli.OPERATIONS['review-assignment'], self.plan(repair['id'])['payload'])['result']
        self.refuse(repair, self.corrected())
        return repair

    def admit(self, plan, state='replace'):
        """Take the planned review-assignment of this state."""
        self.assertEqual((plan['state'], plan.get('command')), (state, 'review-assignment'), plan)
        return self.case.mutate(cli.OPERATIONS['review-assignment'], plan['payload'])['result']

    def retire(self, assignment, identifier):
        self.case.mutate(protocol.record_context_event, dict(id=identifier, assignment_id=assignment['id'],
            kind='route_retired', source='owner', reason='The owner retired this route.',
            evidence=[self.case.supporting]))

    def build_twin_replacement(self, head):
        """The replacement that the same store, without the stale runtime, records on the route of the refused turn."""
        from research_harness.review_refusal_replacement import prepare_replacement
        from research_harness.review_staged_protocol import ControllerArtifacts
        records = self.records()
        value = dict(self.build_payload(head), subject=head['subject'])
        staged = ControllerArtifacts(self.case.artifacts)
        _, twin = prepare_replacement(records, staged, value, self.case.store.revision + 1)
        return twin, protocol._read_json(staged, twin['packet']), protocol.build_assignment_request(records, staged, twin)

    def check_role_stop(self, plan, reason):
        self.assertEqual((plan['state'], plan['code'], plan['details']['step']['id']),
                         ('handoff', 'review_slot_exhausted', 3), plan)
        self.assertEqual(plan['details']['renewal']['unserved']['reason'], reason)
        self.assertEqual((plan['command'], plan['details']['step']['command']), ('review-assignment', 'review-assignment'))


class RouteRenewalRegressionTests(RouteRenewalFixture):
    def test_refused_final_on_a_stale_route_is_recorded_through_a_replacement_on_a_renewed_route(self):
        first, _ = self.build_refused()
        self.make_stale()
        self.renew()
        self.outputs.append(self.corrected())
        lines = self.series(first['id'])
        # On 0.91.2 the last line is the handoff review-route under review_route_runtime_stale.
        self.assertEqual(lines[-1]['state'], 'done', lines[-1])
        [replacement] = self.list_replacements(first['id'])
        self.assertEqual(replacement['route_id'], 'route-renewed')
        self.assertEqual((replacement['reviewer_id'], replacement['slot_id']), (first['reviewer_id'], first['slot_id']))
        records = self.records()
        attempt = next(row for row in records['review_attempt'].values() if row['assignment_id'] == replacement['id'])
        request = json.loads(self.case.artifacts.read(attempt['request']))
        self.assertIn('"output_refusal"', request['input'][-1]['content'])
        self.assertTrue(any(row['assignment_id'] == replacement['id'] for row in records['value_review'].values()))

    def test_stale_route_without_a_renewed_route_ends_before_any_record(self):
        first, _ = self.build_refused()
        self.make_stale()
        lines = self.series(first['id'])
        # On 0.91.2 the store holds a replacement on the stale route.
        self.assertEqual(self.list_replacements(), [])
        self.assertEqual((lines[-1]['state'], lines[-1]['command'], lines[-1]['code']),
                         ('handoff', 'review-route', 'review_route_runtime_stale'))
        self.assertEqual(lines[-1]['details']['renewal'], dict(admitted=True, route_id=None, lacks='record'))

    def test_review_assignment_admits_the_replacement_on_a_renewed_route(self):
        first, _ = self.build_refused()
        self.make_stale()
        self.renew()
        # On 0.91.2 this raises review_refusal_replacement_mismatch.
        receipt = self.case.mutate(protocol.record_assignment, self.build_payload(first, route_id='route-renewed'))
        replacement = receipt['result']
        self.assertEqual(replacement['route_id'], 'route-renewed')
        attempt = self.invoke(replacement, self.corrected())
        records = self.records()
        self.assertEqual(self.calls[-1], ('review', 'route-renewed'))
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, replacement,
                                                      records['review_attempt'][attempt['id']]))

    def test_refused_turn_outside_the_renewal_on_a_stale_route_ends_before_any_record(self):
        with self.subTest(turn='first turn of a context repair'):
            repair = self.build_refused_repair()
            self.assertEqual(self.plan(repair['id'])['state'], 'replace')
            self.make_stale()
            check_stale_procedure(self, self.case.store, repair['id'], [8, 7, 1], self.renew)
            with self.assertRaises(ResearchError) as caught:
                self.case.mutate(protocol.record_assignment, self.build_payload(repair, route_id='route-renewed'))
            self.assertEqual(caught.exception.code, 'review_refusal_replacement_mismatch')
        with self.subTest(turn='turn of a series in parts'):
            fixture, assignment = build_refused_parts_turn(self)
            case = fixture.case
            self.assertEqual(plan_next(case.read_records(), case.artifacts, assignment['id'])['state'], 'replace')
            make_codex_stale(self)
            check_stale_procedure(self, case.store, assignment['id'], [8, 7, 1], lambda: renew_codex_route(case))

    def test_no_command_records_an_assignment_on_a_stale_route_record(self):
        first, _ = self.build_refused()
        first_turn = dict(id='second-first', route_id='route', dossier_id=None, role='bar', reviewer_id='second',
                          author_id='author', context=self.case.deliver_supporting('second'))
        self.make_stale()
        with self.subTest(case='replacement that the planner of 0.91.2 gives'):
            details = check_refused_without_record(self, self.case.store, cli.OPERATIONS['review-assignment'],
                                                        self.build_payload(first), 'route')
            self.assertEqual(details['renewal'], dict(admitted=True, route_id=None, lacks='record'))
        with self.subTest(case='first assignment of a chain'):
            details = check_refused_without_record(self, self.case.store, cli.OPERATIONS['review-assignment'],
                                                        first_turn, 'route')
            self.assertNotIn('renewal', details)
        # The refused first assignment, given again under the same id on a route with the installed runtime.
        self.renew()
        source = self.case.mutate(cli.OPERATIONS['review-assignment'], dict(first_turn, route_id='route-renewed'))['result']
        self.assertEqual((source['id'], source['route_id']), ('second-first', 'route-renewed'))
        with self.subTest(case='continuation after a source request, through review-series'):
            request = self.corrected()
            request['value'].update(status='unresolved', evidence=[])
            request['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
            self.invoke(source, request)
            self.assertEqual(self.plan(source['id'])['state'], 'continue')
            self.make_stale('1' * 64)
            before = set(self.records()['review_assignment'])
            lines = self.series(source['id'])
            self.assertEqual(set(self.records()['review_assignment']), before)
            self.assertEqual(len(lines), 1, lines)
            # The plan itself is the stale-route procedure of the slot; no command is executed and refused.
            self.assertEqual((lines[0]['state'], lines[0]['command'], lines[0]['code']),
                             ('handoff', 'review-context', 'review_route_runtime_stale'))
            self.assertNotIn('error', lines[0])
            self.assertEqual(lines[0]['details']['route_id'], 'route-renewed')
            self.assertEqual(lines[0]['details']['renewal'], dict(admitted=False, route_id=None, lacks='record'))
            self.assertEqual([step['id'] for step in lines[0]['details']['steps']], [8, 7, 1])
        with self.subTest(case='capacity replacement'):
            from test_review_task_size import RecoveryRowsTests
            from review_parts_fixtures import PartsResultCase
            from research_harness import review_failure_class as failures
            rows = RecoveryRowsTests()
            self.addCleanup(rows.doCleanups)
            fixture = rows.prepare(PartsResultCase)
            fixture.oversized(count=12, characters=10000, room=80000)
            assignment = fixture.assign()
            rows.invoke_event(fixture, assignment, 'Prompt is too long')
            recovery = failures.plan_after_failure(fixture.case.read_records(), fixture.case.artifacts, assignment['id'])
            payload = recovery['details']['recovery']['payload']
            make_codex_stale(self)
            details = check_refused_without_record(self, fixture.case.store, protocol.record_assignment, payload, 'route')
            self.assertNotIn('renewal', details)
        with self.subTest(case='frontier of a staged review'):
            import test_review_staged_frontier as frontier_fixtures
            fixture = frontier_fixtures.FrontierTests()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            controller = fixture.create_controller()
            step = controller['next'][0]
            make_codex_stale(self)
            details = check_refused_without_record(self, fixture.case.store, cli.OPERATIONS[step['command']],
                                                        step['payload'], controller['route_id'])
            self.assertNotIn('renewal', details)


def check_refused_without_record(test, store, operation, payload, route_id):
    """The command refuses the stale route record, records nothing and keeps the revision."""
    before = store.snapshot()
    with test.assertRaises(ResearchError) as caught:
        operation(store, payload, expected_revision=before['revision'], request_id='stale-' + payload.get('id', 'frontier'))
    after = store.snapshot()
    # On 0.91.2 the store holds the new assignment.
    test.assertEqual(set(after['records']['review_assignment']), set(before['records']['review_assignment']))
    test.assertEqual(after['revision'], before['revision'])
    test.assertEqual(caught.exception.code, 'review_route_runtime_stale')
    test.assertEqual(caught.exception.details['route_id'], route_id)
    return caught.exception.details


def check_stale_procedure(test, store, head_id, steps, renew, renewed_steps=None):
    """The series ends with the steps of the stale-route procedure, before any record and any call, also after
    a route of the same identity with the installed runtime is recorded and probed. renewed_steps are the steps
    after that, when the verified record makes a step unnecessary."""
    invocations = set(store.snapshot()['records'].get('review_invocation', {}))
    for renewed in (False, True):
        if renewed:
            renew()
            steps = renewed_steps or steps
        lines = []
        run_series(store, head_id, emit=lines.append)
        records = store.snapshot()['records']
        # On 0.91.2 the store holds a replacement whose refusal link names the head.
        test.assertEqual([key for key, row in records['review_assignment'].items()
                          if (row['context'].get('output_refusal') or {}).get('assignment_id') == head_id], [])
        line = lines[-1]
        test.assertEqual((line['state'], line['code']), ('handoff', 'review_route_runtime_stale'), line)
        test.assertIs(line['details']['renewal']['admitted'], False)
        test.assertEqual([step['id'] for step in line['details']['steps']], steps)
        test.assertEqual(line['command'], line['details']['steps'][0]['command'])
        # Once the store holds a route record with the installed runtime, the step that verifies a route probes it.
        test.assertEqual([step['command'] for step in line['details']['steps'] if step['id'] == 7],
                         ['review-probe' if renewed else 'review-route'] * (7 in steps))
        test.assertEqual(set(records.get('review_invocation', {})), invocations)


IDENTITY_MEMBERS = ('adapter', 'model', 'endpoint', 'configuration')


def make_codex_stale(test):
    """Install another Codex runtime for the routes of a fixture of the suite."""
    from research_harness import review_codex_transport as codex
    runtime = patch.object(codex, 'runtime',
                           return_value=dict(codex.runtime(), implementation_sha256=STALE_IMPLEMENTATION_SHA256))
    runtime.start()
    test.addCleanup(runtime.stop)


def renew_codex_route(case, route_id='route', identifier='route-renewed'):
    """Record and probe a Codex route of the same identity with the installed runtime."""
    from codex_route_fixtures import probe_codex_route
    route = case.store.snapshot()['records']['review_route'][route_id]
    protocol.record_route(case.store, dict(id=identifier, **{key: route[key] for key in IDENTITY_MEMBERS}),
                          expected_revision=case.store.revision, request_id='renewed-route-' + identifier)
    probe_codex_route(case, 'probe-' + identifier, identifier)


def build_refused_parts_turn(test):
    """A refused read turn of a result series in parts on a Codex route, for which 0.91.2 plans a replacement."""
    import review_parts_fixtures as parts_fixtures
    from codex_route_fixtures import limit_codex_input
    fixture = parts_fixtures.PartsResultCase()
    fixture.setUp()
    test.addCleanup(fixture.doCleanups)
    fixture.oversized(count=4, characters=24000, room=40000)
    assignment = fixture.assign()
    output = parts_fixtures.make_output(fixture.packet(assignment))
    output.pop('kind')
    fixture.invoke(assignment, output)
    limit_codex_input(fixture, protocol.transport.local_input_limit(
        fixture.case.read_records()['review_route']['route']) + 15000)
    return fixture, assignment


def build_refused_v7_turn(test):
    """A refused first reading turn of a chain of exactory-openness-reading-v7 on a Codex route."""
    import test_openness_reading_v7_refusal as v7_fixtures
    case = v7_fixtures.V7RefusalTests()
    case.setUp()
    test.addCleanup(case.doCleanups)
    native = case.export()
    case.assign('turn-1', native)
    case.invoke('turn-1', dict(stage='reading', notes=42))
    return case


class RouteRenewalContractTests(RouteRenewalFixture):
    def test_first_turn_position_keeps_its_members_across_a_renewal(self):
        from research_harness.review_failure_class import count_position, list_turn_assignments
        first, output = self.build_refused()
        second = self.admit(self.plan(first['id']))
        self.refuse(second, output)
        self.make_stale()
        self.renew()
        third = self.admit(self.plan(second['id']))
        self.assertEqual(third['route_id'], 'route-renewed')
        records = self.records()
        members = [first['id'], second['id'], third['id']]
        for identifier in members:
            self.assertEqual(list_turn_assignments(records, records['review_assignment'][identifier]), members)
        self.assertEqual(count_position(records, self.case.artifacts, third)['replacements']['output_refused'], 2)
        entries = self.read_notice(third)['refused']
        refusals = records['review_output_refusal']
        self.assertEqual([entry['attempt_id'] for entry in entries], ['attempt-' + first['id'], 'attempt-' + second['id']])
        for entry in entries:
            self.assertEqual(entry['refusal_digest'], digest(refusals[entry['attempt_id']]))
            self.assertEqual(entry['output_digest'], digest(output))
        self.refuse(third, output)
        plan = self.plan(third['id'])
        self.assertEqual((plan['state'], plan['code'], plan['details']['step']['id']),
                         ('handoff', 'review_slot_exhausted', 3))
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(cli.OPERATIONS['review-assignment'], self.build_payload(third, 'fourth'))
        self.assertEqual(caught.exception.code, 'review_slot_exhausted')

    def test_two_slots_on_routes_with_equal_members_keep_their_positions(self):
        from research_harness.review_failure_class import count_position, list_turn_assignments
        self.record_route('route-b')
        self.probe('route-b')
        heads = [self.build_refused('first', 'slot-a')[0], self.build_refused('second', 'slot-b', 'route-b')[0]]
        self.make_stale()
        self.renew()
        replacements = [self.admit(self.plan(head['id'])) for head in heads]
        records = self.records()
        for head, replacement in zip(heads, replacements):
            self.assertEqual(replacement['route_id'], 'route-renewed')
            self.assertEqual(list_turn_assignments(records, replacement), [head['id'], replacement['id']])
            self.assertEqual([entry['attempt_id'] for entry in self.read_notice(replacement)['refused']],
                             ['attempt-' + head['id']])
            self.assertEqual(count_position(records, self.case.artifacts, replacement)['replacements']['output_refused'], 1)
            self.assertEqual(replacement['slot_id'], head['slot_id'])

    def run_renewed_replacement(self):
        first, _ = self.build_refused()
        self.make_stale()
        self.renew()
        self.outputs.append(self.corrected())
        self.assertEqual(self.series(first['id'])[-1]['state'], 'done')
        [replacement] = self.list_replacements(first['id'])
        return first, replacement

    def test_origin_of_a_renewed_replacement_verifies_from_stored_records(self):
        from research_harness.review_refusal_replacement import validate_replacement_origin
        first, replacement = self.run_renewed_replacement()
        def verify():
            records = self.records()
            attempt = next(row for row in records['review_attempt'].values() if row['assignment_id'] == replacement['id'])
            validate_replacement_origin(records, self.case.artifacts, replacement)
            self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, replacement, attempt))
        with self.subTest(state='another installed runtime'):
            with patch.object(claude, 'runtime', return_value=dict(claude.runtime(), implementation_sha256='3' * 64)):
                verify()
        with self.subTest(state='a later failed probe of the renewed route'):
            self.probe_failure = True
            self.probe('route-renewed', 'failed-probe')
            self.probe_failure = False
            self.assertEqual(protocol.route_state(self.records(), self.case.artifacts, 'route-renewed')['context_status'],
                             'unverified')
            verify()
        with self.subTest(state='the retirement of the renewed route'):
            self.retire(replacement, 'retire-renewed')
            verify()
        with self.subTest(state='the retirement of the stale route'):
            self.retire(first, 'retire-stale')
            verify()
        with patch.object(transport, 'runtime', side_effect=AssertionError('The origin reads no runtime.')), \
                patch.object(claude, 'runtime', side_effect=AssertionError('The origin reads no runtime.')):
            validate_replacement_origin(self.records(), self.case.artifacts, replacement)

    def test_admission_refuses_a_route_that_is_no_renewal(self):
        first, _ = self.build_refused()
        for identifier in ('route-current-twin', 'route-stale-twin'):
            self.record_route(identifier)
            self.probe(identifier)
        with self.subTest(route='same members while the route of the refused turn has the installed runtime'):
            with self.assertRaises(ResearchError) as caught:
                self.case.mutate(cli.OPERATIONS['review-assignment'], self.build_payload(first, route_id='route-current-twin'))
            self.assertEqual(caught.exception.code, 'review_refusal_replacement_mismatch')
        self.make_stale()
        self.renew('route-effort', configuration=dict(ROUTE['configuration'], effort='xhigh'))
        self.renew('route-unprobed', probe=False)
        self.renew('route-retired')
        self.retire(self.assign('bar', 'second', 'retired-turn', route_id='route-retired',
                                **self.case.deliver_supporting('second')), 'retire-route')
        for route_id, code in (('route-effort', 'review_refusal_replacement_mismatch'),
                               ('route-unprobed', 'review_refusal_replacement_mismatch'),
                               ('route-stale-twin', 'review_route_runtime_stale'),
                               ('route-retired', 'review_route_retired')):
            with self.subTest(route=route_id), self.assertRaises(ResearchError) as caught:
                self.case.mutate(cli.OPERATIONS['review-assignment'], self.build_payload(first, route_id=route_id))
            self.assertEqual(caught.exception.code, code)
        mismatch = None
        try:
            self.case.mutate(cli.OPERATIONS['review-assignment'], self.build_payload(first, route_id='route-unprobed'))
        except ResearchError as error:
            mismatch = error
        self.assertEqual(mismatch.details['renewal'], dict(admitted=True, route_id='route-unprobed', lacks='probe'))
        with self.subTest(route='a turn of a series in parts'):
            fixture, assignment = build_refused_parts_turn(self)
            make_codex_stale(self)
            renew_codex_route(fixture.case)
            payload = self.build_payload(assignment, route_id='route-renewed')
            with self.assertRaises(ResearchError) as caught:
                fixture.case.mutate(protocol.record_assignment, payload)
            self.assertEqual(caught.exception.code, 'review_refusal_replacement_mismatch')
            self.assertIs(caught.exception.details['renewal']['admitted'], False)

    def test_origin_refuses_a_stored_route_change_that_is_no_renewal(self):
        from research_harness.review_refusal_replacement import validate_replacement_origin
        first, _ = self.build_refused()
        self.record_route('route-twin')
        self.record_route('route-model', model='claude-fable-5-2')
        replacement = self.admit(self.plan(first['id']))
        records = self.records()
        validate_replacement_origin(records, self.case.artifacts, replacement)
        # The two stored replacements are written into the records directly, past the admission check.
        for route_id in ('route-twin', 'route-model'):
            with self.subTest(route=route_id):
                forged = dict(replacement, id='forged-' + route_id, route_id=route_id,
                              route_digest=digest(records['review_route'][route_id]))
                stored = copy.deepcopy(records)
                stored['review_assignment'][forged['id']] = forged
                with self.assertRaises(ResearchError) as caught:
                    validate_replacement_origin(stored, self.case.artifacts, forged)
                self.assertEqual(caught.exception.code, 'review_refusal_replacement_origin_invalid')

    def test_plan_names_the_record_and_the_probe_that_a_renewal_lacks(self):
        from research_harness.record_check import check_record
        first, _ = self.build_refused()
        self.make_stale()
        before = set(self.records()['review_assignment'])
        plan = self.plan(first['id'])
        self.assertEqual((plan['state'], plan['command'], plan['details']['step']['id']),
                         ('handoff', 'review-route', 7))
        self.assertEqual(plan['details']['renewal']['lacks'], 'record')
        self.assertEqual(plan['details']['step']['then'][-1], dict(command='review-series', assignment_id=first['id']))
        report = check_record(self.case.root, isolate=False)
        self.assertTrue(report['ready'], report)
        continuation = next(row for row in report['steps'] if row['step'] == 'continuation')
        chain = next(row for row in continuation['result']['chains'] if row['head_id'] == first['id'])
        self.assertEqual(chain['boundary'], 'handoff')
        self.assertEqual((chain['actions'][-1]['code'], chain['actions'][-1]['details']['renewal']['lacks']),
                         ('review_route_runtime_stale', 'record'))
        self.assertEqual({row['kind'] for row in report['local_reads']},
                         {'route_runtime:claude_cli_v1', 'route_context:claude_cli_v1'})
        self.renew(probe=False)
        plan = self.plan(first['id'])
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'review-probe'))
        self.assertEqual(plan['details']['step']['payload'], dict(route_id='route-renewed'))
        self.assertEqual(plan['details']['renewal'], dict(admitted=True, route_id='route-renewed', lacks='probe'))
        self.assertEqual(set(self.records()['review_assignment']), before)

    def test_request_of_a_renewed_replacement_equals_that_on_the_stale_record_for_the_same_form(self):
        first, _ = self.build_refused()
        twin, twin_packet, twin_request = self.build_twin_replacement(first)
        self.make_stale()
        self.renew()
        replacement = self.admit(self.plan(first['id']))
        self.assertEqual(replacement['route_id'], 'route-renewed')
        request = protocol.build_assignment_request(self.records(), self.case.artifacts, replacement)
        self.assertEqual(self.read_notice(replacement)['form'], twin_packet['output_refusal']['form'])
        self.assertEqual((replacement['packet_digest'], replacement['prompt_hash'], digest(request)),
                         (twin['packet_digest'], twin['prompt_hash'], digest(twin_request)))

    def test_source_request_after_a_renewed_replacement_continues_on_the_renewed_route(self):
        first, _ = self.build_refused()
        self.make_stale()
        self.renew()
        request = self.corrected()
        request['value'].update(status='unresolved', evidence=[])
        request['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        self.outputs.extend([request, self.corrected()])
        self.assertEqual(self.series(first['id'])[-1]['state'], 'done')
        [replacement] = self.list_replacements(first['id'])
        records = self.records()
        [continuation] = [row for row in records['review_assignment'].values()
                          if row['context'].get('prior_assignment_id') == replacement['id']]
        self.assertEqual((replacement['route_id'], continuation['route_id']), ('route-renewed', 'route-renewed'))
        self.assertTrue(any(row['assignment_id'] == continuation['id'] for row in records['value_review'].values()))

    def test_exhausted_position_across_a_renewal_takes_the_step_of_its_role(self):
        with self.subTest(position='bar'):
            first, output = self.build_refused()
            self.refuse(self.admit(self.plan(first['id'])), output)
            self.make_stale()
            self.renew()
            head = self.admit(self.plan(first['id'] + '-refusal'))
            self.refuse(head, output)
            plan = self.plan(head['id'])
            step = plan['details']['step']
            self.assertEqual((plan['state'], plan['code'], step['id']), ('handoff', 'review_slot_exhausted', 3))
            self.assertIn('first', step['excluded_reviewer_ids'])
            fresh = self.case.mutate(cli.OPERATIONS[step['command']], dict(step['payload'], id='new-reviewer',
                route_id='route-renewed', reviewer_id='third', context=self.case.deliver_supporting('third')))['result']
            self.assertEqual(fresh['slot_id'], first['slot_id'])
        with self.subTest(position='result'):
            for reviewer in ('first', 'second'):
                if reviewer == 'second':
                    bar = self.assign('bar', reviewer, 'bar-second', route_id='route-renewed',
                                      **self.case.deliver_supporting(reviewer))
                else:
                    bar = fresh
                answer = self.corrected()
                self.invoke(bar, answer)
                self.record(bar, answer)
            self.record_route('route-result')
            self.probe('route-result')
            result = self.assign('result', 'first', 'result-first', route_id='route-result')
            bad = self.corrected('result')
            bad['value']['bar_ids'] = [row['id'] for row in protocol._read_json(self.case.artifacts, result['packet'])['bars']]
            bad['value']['status'] = 'invalid'
            self.refuse(result, bad)
            self.refuse(self.admit(self.plan(result['id'])), bad)
            self.make_stale('4' * 64)
            self.renew('route-result-renewed')
            head = self.admit(self.plan(result['id'] + '-refusal'))
            self.assertEqual(head['route_id'], 'route-result-renewed')
            self.refuse(head, bad)
            plan = self.plan(head['id'])
            step = plan['details']['step']
            self.assertEqual((plan['state'], plan['code'], step['id']), ('handoff', 'review_slot_exhausted', 2))
            binding = step['payload']['context']['context_repair']['exhaustion']
            records = self.records()
            position = [result['id'], result['id'] + '-refusal', head['id']]
            self.assertEqual({records['review_assignment'][key]['route_id'] for key in position},
                             {'route-result', 'route-result-renewed'})
            self.assertEqual(binding['position'], position)
            self.assertEqual(sorted(row['attempt_id'] for row in binding['refusals']),
                             sorted('attempt-' + key for key in position))
            probe = self.probe('route-result-renewed', 'repair-probe')
            payload = copy.deepcopy(step['payload'])
            payload.update(id='result-repair', route_id='route-result-renewed')
            payload['context']['context_repair'].update(probe_id=probe['id'],
                reason='Continue the exhausted result slot with a fresh context.')
            repair = self.case.mutate(cli.OPERATIONS[step['command']], payload)['result']
            self.assertEqual(repair['slot_id'], result['slot_id'])

    def test_latest_verified_record_is_named(self):
        first, _ = self.build_refused()
        self.make_stale()
        self.renew('route-earlier')
        self.renew('route-later')
        self.assertEqual(self.plan(first['id'])['payload']['route_id'], 'route-later')
        self.retire(self.assign('bar', 'second', 'later-turn', route_id='route-later',
                                **self.case.deliver_supporting('second')), 'retire-later')
        self.assertEqual(self.plan(first['id'])['payload']['route_id'], 'route-earlier')

    def test_unverified_history_parent_keeps_the_stale_route_procedure(self):
        source = self.assign('bar', 'first', 'source-turn', **self.case.deliver_supporting('first'))
        request = self.corrected()
        request['value'].update(status='unresolved', evidence=[])
        request['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        self.invoke(source, request)
        turn = self.admit(self.plan(source['id']), 'continue')
        bad = self.corrected()
        bad['value']['status'] = 'invalid'
        self.refuse(turn, bad)
        self.probe_failure = True
        self.probe('route', 'failed-probe')
        self.probe_failure = False
        self.make_stale()
        self.renew()
        before = set(self.records()['review_assignment'])
        line = self.series(turn['id'])[-1]
        self.assertEqual((line['state'], line['code']), ('handoff', 'review_route_runtime_stale'), line)
        self.assertIs(line['details']['renewal']['admitted'], False)
        self.assertEqual([step['id'] for step in line['details']['steps']], [8, 7, 1])
        self.assertEqual(set(self.records()['review_assignment']), before)
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(cli.OPERATIONS['review-assignment'], self.build_payload(turn, route_id='route-renewed'))
        self.assertEqual(caught.exception.code, 'review_refusal_replacement_mismatch')


    def check_limit_stop(self, head, limit):
        """Under a limit that no capacity probe changes, the plan of the stale head is the step of its role."""
        before = set(self.records()['review_assignment'])
        with limit:
            plan = self.plan(head['id'])
        self.assertEqual(plan['details']['renewal']['unserved'], dict(reason='limit'))
        self.assertEqual(plan['command'], 'review-assignment', plan)
        self.assertEqual(set(self.records()['review_assignment']), before)
        return plan

    def measure_characters(self, assignment):
        from research_harness.review_route_capacity import measure_request
        records = self.records()
        request = protocol.build_assignment_request(records, self.case.artifacts, assignment)
        return measure_request(records['review_route'][assignment['route_id']], request, records=records,
                               artifacts=self.case.artifacts)

    def test_limit_that_no_probe_changes_gives_the_step_of_the_role(self):
        from research_harness import review_route_capacity as capacity
        with self.subTest(position='result'):
            # Its own store, run before this store installs another runtime.
            other = type(self)(self._testMethodName)
            other.setUp()
            try:
                other.check_result_limit_stop()
            finally:
                other.doCleanups()
        first, _ = self.build_refused()
        second, _ = self.build_refused('second', 'second-slot')
        first_measure, second_measure = self.measure_characters(first), self.measure_characters(second)
        self.make_stale()
        self.renew()
        with self.subTest(limit='the character limit of the Claude transport'):
            limit = patch.object(claude, 'INPUT_LIMIT_CHARACTERS', first_measure['characters'] + 100)
            plan = self.check_limit_stop(first, limit)
            step = plan['details']['step']
            self.assertEqual((plan['code'], step['id']), ('review_slot_exhausted', 3))
            # The admission of a new reviewer asks the planner whether the slot is exhausted.
            with limit:
                fresh = self.case.mutate(cli.OPERATIONS[step['command']], dict(step['payload'], id='new-reviewer',
                    route_id='route-renewed', reviewer_id='third', context=self.case.deliver_supporting('third')))['result']
            self.assertEqual(fresh['slot_id'], first['slot_id'])
        with self.subTest(limit='a documented input limit below both forms'):
            threshold = second_measure['tokens_upper'] + second_measure['output_reservation_tokens']
            rows = tuple((row[0], row[1], threshold, row[3], row[4]) if row[0] == ROUTE['model'] else row
                         for row in capacity.MODEL_LIMITS)
            plan = self.check_limit_stop(second, patch.object(capacity, 'MODEL_LIMITS', rows))
            self.assertEqual((plan['code'], plan['details']['step']['id']), ('review_slot_exhausted', 3))

    def build_refused_result(self, route_id='route'):
        """A first result turn, after two recorded bars, whose answer the value contract refuses."""
        for reviewer in ('first', 'second'):
            bar = self.assign('bar', reviewer, 'bar-' + reviewer, **self.case.deliver_supporting(reviewer))
            answer = self.corrected()
            self.invoke(bar, answer)
            self.record(bar, answer)
        result = self.assign('result', 'first', 'result-first', route_id=route_id)
        bad = self.corrected('result')
        bad['value']['bar_ids'] = [row['id'] for row in protocol._read_json(self.case.artifacts, result['packet'])['bars']]
        bad['value']['status'] = 'invalid'
        self.refuse(result, bad)
        return result, bad

    def check_result_limit_stop(self):
        result, _ = self.build_refused_result()
        self.make_stale()
        self.renew()
        # The character limit lies below both forms of the replacement, which the trials of a failed admission measure.
        with patch.object(claude, 'INPUT_LIMIT_CHARACTERS', 1):
            trials = self.plan(result['id'])['details']['trials']
        characters = min(trial['request_measure']['characters'] for trial in trials)
        plan = self.check_limit_stop(result, patch.object(claude, 'INPUT_LIMIT_CHARACTERS', characters - 1))
        step = plan['details']['step']
        self.assertEqual((plan['code'], step['id']), ('review_slot_exhausted', 2))
        self.assertEqual(step['payload']['context']['context_repair']['exhaustion']['position'], [result['id']])

    def test_renewed_record_takes_the_smaller_task_of_its_turn(self):
        from research_harness import bar_sources, review_parts as parts, review_refusal_notice as notices
        from research_harness.review_refusal_replacement import validate_replacement_origin
        from review_parts_fixtures import make_output
        real = notices.select_refusal_notice
        def select_parts_only(*args, **options):
            # No form fits a packet without a parts header, as when the notice lifts the task over its level.
            selected = real(*args, **options)
            return selected if parts.KEY in options['packet'] else dict(selected, notice=None)
        # The turns of record 7 hold the session sources of version 2, whose repair turns a series in parts can
        # split; a read unit of 30,000 characters splits the 112,000 characters of this material.
        self.case.supporting = self.case.artifacts.put(('A synthetic field standard. ' * 4000).encode(), 'text/plain')
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            turn = self.build_refused_repair(continued=True)
            first_turn, _ = self.build_refused('second', 'second-slot')
        with patch.object(notices, 'select_refusal_notice', side_effect=select_parts_only), \
                patch.object(parts, 'READ_CHARACTERS', 30000):
            twin, twin_packet, twin_request = self.build_twin_replacement(turn)
            self.make_stale()
            self.renew()
            with self.subTest(turn='a turn of a repair chain'):
                replacement = self.admit(self.plan(turn['id']))
                packet = protocol._read_json(self.case.artifacts, replacement['packet'])
                self.assertEqual((parts.find_kind(packet), packet['output_refusal']['form']), ('read', 'bounded'))
                self.assertEqual(replacement['route_id'], 'route-renewed')
                request = protocol.build_assignment_request(self.records(), self.case.artifacts, replacement)
                self.assertEqual((replacement['packet_digest'], replacement['prompt_hash'], digest(request)),
                                 (twin['packet_digest'], twin['prompt_hash'], digest(twin_request)))
                validate_replacement_origin(self.records(), self.case.artifacts, replacement)
                attempt = self.invoke(replacement, make_output(packet))
                records = self.records()
                self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, replacement,
                                                              records['review_attempt'][attempt['id']]))
                following = self.admit(self.plan(replacement['id']), 'continue')
                self.assertEqual(following['route_id'], 'route-renewed')
            with self.subTest(turn='the first turn of a slot, which has no smaller task'):
                plan = self.plan(first_turn['id'])
                self.assertEqual((plan['state'], plan['code'], plan['details']['step']['id']),
                                 ('handoff', 'review_slot_exhausted', 3), plan)
                self.assertEqual(plan['details']['renewal']['unserved'], dict(reason='limit'))

    def test_stale_is_inequality_with_the_whole_installed_runtime_in_a_command(self):
        from research_harness.artifacts import ArtifactStore
        command = ArtifactStore(self.case.root)
        with patch.object(claude, 'runtime', return_value=dict(claude.runtime(), removed='member')):
            self.record_route('route-extra')
        route = self.records()['review_route']['route-extra']
        self.assertTrue(protocol.is_route_stale(command, route))
        base = dict(id='https-route', adapter=transport.ADAPTER, model='fixture-model',
                    endpoint='https://api.openai.com/v1/responses',
                    configuration=dict(max_output_tokens=1000, timeout_seconds=5))
        self.assertTrue(protocol.is_route_stale(command, dict(base, runtime=dict(transport.runtime(base), removed='member'))))
        self.assertFalse(protocol.is_route_stale(command, dict(base, runtime=transport.runtime(base))))
        payload = dict(id='second-first', route_id='route-extra', dossier_id=None, role='bar', reviewer_id='second',
                       author_id='author', context=self.case.deliver_supporting('second'))
        before = self.case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(cli.OPERATIONS['review-assignment'], payload)
        self.assertEqual(caught.exception.code, 'review_route_runtime_stale')
        self.assertEqual(self.case.store.snapshot(), before)

    def test_record_check_compares_an_https_route_by_its_adapter_and_its_transport_hash(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.record_check import OfflineArtifacts
        command, offline = ArtifactStore(self.case.root), OfflineArtifacts(self.case.root, [])
        base = dict(id='https-route', adapter=transport.ADAPTER, model='fixture-model',
                    endpoint='https://api.openai.com/v1/responses',
                    configuration=dict(max_output_tokens=1000, timeout_seconds=5))
        installed = transport.runtime(base)
        self.assertEqual(set(offline.read_runtime_fingerprint(dict(base, runtime=installed))),
                         {'adapter', 'implementation_sha256'})
        self.assertFalse(protocol.is_route_stale(offline, dict(base, runtime=installed)))
        self.assertTrue(protocol.is_route_stale(offline, dict(base, runtime=dict(installed, implementation_sha256='0' * 64))))
        other_host = dict(base, runtime=dict(installed, host='another-host'))
        self.assertFalse(protocol.is_route_stale(offline, other_host))
        self.assertTrue(protocol.is_route_stale(command, other_host))


UNLISTED_CHANGES = dict(model='claude-unlisted-5-0',
                        configuration=dict(max_output_tokens=128000, timeout_seconds=5, effort='high'))


class RouteRenewalCapacityTests(RouteRenewalFixture):
    """Contract tests whose capacity evidence is keyed by the runtime: a Claude model without a row in MODEL_LIMITS,
    the stand-in for the Codex adapters. Such a record has the default limit of 72,000 tokens until a capacity probe
    of its own key is verified."""
    route_changes = UNLISTED_CHANGES
    old_capacity_tokens = 400000

    def setUp(self):
        super().setUp()
        self.probe('route', 'old-capacity-probe', capacity=self.old_capacity_tokens)
        # A first bar request of about 128,000 tokens fits the old key and no form of its replacement fits the default.
        self.case.supporting = self.case.artifacts.put(('A synthetic field standard. ' * 4000).encode(), 'text/plain')

    def read_capacity_plan(self, head_id):
        plan = self.plan(head_id)
        self.assertEqual((plan['state'], plan['command'], plan['details']['step']['id']), ('handoff', 'review-probe', 16), plan)
        self.assertEqual(plan['details']['step']['payload']['route_id'], plan['details']['renewal']['route_id'])
        return plan

    def run_capacity_step(self, plan, identifier, share):
        self.capacity_share = share
        step = plan['details']['step']
        self.case.mutate(cli.OPERATIONS[step['command']], dict(step['payload'], id=identifier))
        self.assertEqual(step['then'], [dict(command='review-series', assignment_id=plan['head_id'])])

    def test_capacity_probe_is_offered_when_evidence_is_what_the_record_lacks(self):
        from research_harness.review_capacity_evidence import read_capacity_evidence
        first, _ = self.build_refused()
        self.make_stale()
        self.renew()
        before = set(self.records()['review_assignment'])
        plan = self.read_capacity_plan(first['id'])
        capacity = plan['details']['renewal']['capacity']
        trial = next(row for row in plan['details']['trials'] if row['form'] == capacity['form'])
        self.assertEqual(capacity['request_tokens'], trial['request_measure']['tokens_upper'])
        needed = capacity['request_tokens'] + trial['request_measure']['output_reservation_tokens']
        renewed = self.records()['review_route']['route-renewed']
        hint = read_capacity_evidence(renewed, self.records(), artifacts=self.case.artifacts)['probe_input_tokens']
        self.assertEqual(plan['details']['step']['payload']['capacity'], dict(input_tokens=max(needed, hint)))
        self.assertEqual(set(self.records()['review_assignment']), before)
        self.run_capacity_step(plan, 'renewal-capacity', 1.0)
        self.outputs.append(self.corrected())
        self.assertEqual(self.series(first['id'])[-1]['state'], 'done')
        [replacement] = self.list_replacements(first['id'])
        self.assertEqual(replacement['route_id'], 'route-renewed')

    def check_short_probes_end_the_offer(self):
        """Two slots on one renewed record; both capacity probes that the first slot causes leave the evidence short."""
        first, _ = self.build_refused()
        second, _ = self.build_refused('second', 'second-slot')
        self.make_stale()
        self.renew()
        self.run_capacity_step(self.read_capacity_plan(first['id']), 'short-capacity', 0.45)
        self.run_capacity_step(self.read_capacity_plan(first['id']), 'short-again', 0.3)
        # The count reads the key: the second slot counts both probes, which were recorded after its refusal.
        for head in (first, second):
            stop = self.plan(head['id'])
            self.check_role_stop(stop, 'probes_used')
            self.assertEqual(stop['details']['renewal']['unserved']['probe_ids'], ['short-again', 'short-capacity'])

    def test_second_capacity_probe_follows_a_first_one_that_left_the_evidence_short(self):
        with self.subTest(store='both probes short, on its own store'):
            other = type(self)(self._testMethodName)
            other.setUp()
            try:
                other.check_short_probes_end_the_offer()
            finally:
                other.doCleanups()
        first, _ = self.build_refused()
        self.make_stale()
        self.renew()
        self.run_capacity_step(self.read_capacity_plan(first['id']), 'short-capacity', 0.45)
        again = self.read_capacity_plan(first['id'])
        capacity = again['details']['renewal']['capacity']
        trial = next(row for row in again['details']['trials'] if row['form'] == capacity['form'])
        self.assertEqual(again['details']['step']['payload']['capacity'],
                         dict(input_tokens=capacity['request_tokens'] + trial['request_measure']['output_reservation_tokens']))
        self.run_capacity_step(again, 'enough-capacity', 0.45)
        plan = self.plan(first['id'])
        self.assertEqual((plan['state'], plan['payload']['route_id']), ('replace', 'route-renewed'), plan)
        self.assertEqual(plan['details']['form'], capacity['form'])


    def test_probe_that_cannot_run_gives_the_step_of_the_role(self):
        from research_harness import review_route_capacity as capacity
        first, _ = self.build_refused()
        second, _ = self.build_refused('second', 'second-slot')
        self.make_stale()
        self.renew()
        with self.subTest(probe='a second probe larger than the Claude transport can build'):
            # The second probe scales the first by 50 and needs more than the 1,300,000 characters of the transport.
            self.run_capacity_step(self.read_capacity_plan(first['id']), 'small-capacity', 0.02)
            plan = self.plan(first['id'])
            self.check_role_stop(plan, 'probe_unavailable')
            self.assertEqual(plan['details']['renewal']['unserved']['code'], 'invalid_review_capacity_probe')
        visual = dict(id='visual-route', adapter='codex_cli_visual_v1', model='gpt-6-astra', endpoint='codex://local',
                      configuration=dict(max_output_tokens=1000, timeout_seconds=5))
        with self.assertRaises(ResearchError) as refused:
            capacity.build_capacity_probe_request(visual, protocol.build_probe_prompt(visual),
                dict(allowed_control='a' * 32, evidence_control='b' * 32, evidence=''), 150000)
        self.assertEqual(refused.exception.code, 'invalid_review_capacity_probe')
        with self.subTest(probe='a builder that refuses the adapter of the route'):
            with patch.object(capacity, 'build_capacity_probe_request', side_effect=refused.exception):
                plan = self.plan(second['id'])
            self.check_role_stop(plan, 'probe_unavailable')
            self.assertEqual(plan['details']['renewal']['unserved'],
                             dict(reason='probe_unavailable', code=refused.exception.code, message=refused.exception.message))

    def check_probe_that_does_not_complete(self):
        first, _ = self.build_refused()
        self.make_stale()
        self.renew()
        plan = self.read_capacity_plan(first['id'])
        self.capacity_failure = 'The provider ended the capacity probe with an error.'
        self.run_capacity_step(plan, 'failed-capacity', 1.0)
        self.capacity_failure = None
        records = self.records()
        self.assertEqual(protocol.route_state(records, self.case.artifacts, 'route-renewed')['context_status'], 'unverified')
        stop = self.plan(first['id'])
        self.check_role_stop(stop, 'probes_used')
        self.assertEqual(stop['details']['renewal']['unserved']['probe_ids'], ['failed-capacity'])
        probes = sorted((row for row in records['review_route_probe'].values() if row['route_id'] == 'route-renewed'),
                        key=lambda row: row['recorded_revision'])
        self.assertEqual([row['id'] for row in probes if 'capacity' in row], ['failed-capacity'])
        self.assertEqual(probes[-1]['id'], 'failed-capacity')

    def test_capacity_probe_without_evidence_ends_the_offer(self):
        with self.subTest(probe='a capacity probe that does not complete, on its own store'):
            other = type(self)(self._testMethodName)
            other.setUp()
            try:
                other.check_probe_that_does_not_complete()
            finally:
                other.doCleanups()
        first, _ = self.build_refused()
        self.make_stale()
        self.renew()
        self.run_capacity_step(self.read_capacity_plan(first['id']), 'empty-capacity', None)
        records = self.records()
        self.assertFalse(records['review_route_probe']['empty-capacity']['capacity_verified'])
        self.assertEqual(protocol.route_state(records, self.case.artifacts, 'route-renewed')['context_status'],
                         'verified_for_route')
        stop = self.plan(first['id'])
        self.check_role_stop(stop, 'probes_used')
        self.assertEqual(stop['details']['renewal']['unserved']['probe_ids'], ['empty-capacity'])

    def test_renewal_can_select_another_form_of_the_notice(self):
        from research_harness.review_refusal_replacement import validate_replacement_origin
        first, _ = self.build_refused()
        twin, twin_packet, twin_request = self.build_twin_replacement(first)
        self.assertEqual(twin_packet['output_refusal']['form'], 'whole')
        self.make_stale()
        self.renew()
        plan = self.read_capacity_plan(first['id'])
        capacity = plan['details']['renewal']['capacity']
        self.assertEqual(capacity['form'], 'bounded')
        trial = next(row for row in plan['details']['trials'] if row['form'] == 'bounded')
        # The capacity probe of the size that the bounded form needs leaves the whole form over the limit.
        self.case.mutate(cli.OPERATIONS['review-probe'], dict(id='bounded-capacity', route_id='route-renewed',
            capacity=dict(input_tokens=capacity['request_tokens'] + trial['request_measure']['output_reservation_tokens'])))
        replacement = self.admit(self.plan(first['id']))
        notice = self.read_notice(replacement)
        records = self.records()
        request = protocol.build_assignment_request(records, self.case.artifacts, replacement)
        self.assertEqual(notice['form'], 'bounded')
        for renewed, stale in ((replacement['packet_digest'], twin['packet_digest']),
                               (replacement['prompt_hash'], twin['prompt_hash']), (digest(request), digest(twin_request))):
            self.assertNotEqual(renewed, stale)
        self.assertEqual(notice['refused'], twin_packet['output_refusal']['refused'])
        for entry in notice['refused']:
            self.assertEqual(entry['refusal_digest'], digest(records['review_output_refusal'][entry['attempt_id']]))
        validate_replacement_origin(records, self.case.artifacts, replacement)
        corrected = self.corrected()
        attempt = self.invoke(replacement, corrected)
        records = self.records()
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, replacement,
                                                      records['review_attempt'][attempt['id']]))
        self.record(replacement, corrected)


class RouteRenewalCompatibilityTests(RouteRenewalFixture):
    def test_head_on_a_route_with_the_installed_runtime_keeps_its_plan(self):
        first, _ = self.build_refused()
        self.renew('route-twin')
        plan = self.plan(first['id'])
        self.assertEqual(plan['payload'], self.build_payload(first))
        replacement = self.admit(plan)
        self.assertEqual((replacement['route_id'], replacement['route_digest'], replacement['context_state']),
                         (first['route_id'], first['route_digest'], first['context_state']))
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(cli.OPERATIONS['review-assignment'], self.build_payload(replacement, route_id='route-twin'))
        self.assertEqual(caught.exception.code, 'review_refusal_replacement_mismatch')

    def test_refused_head_on_a_retired_route_keeps_its_handoff(self):
        first, _ = self.build_refused()
        self.retire(first, 'retire-route')
        self.make_stale()
        line = self.series(first['id'])[-1]
        # The retired route record is also stale, so the slot first needs a route record with the installed runtime.
        self.assertEqual((line['state'], line['code'], [step['id'] for step in line['details']['steps']]),
                         ('handoff', 'review_route_retired', [7, 1]), line)
        self.assertNotIn('error', line)
        self.assertEqual(self.list_replacements(), [])
        with self.subTest(review='the frontier of a staged review on a retired and stale route'):
            import test_review_staged_frontier as frontier_fixtures
            fixture = frontier_fixtures.FrontierTests()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            controller = fixture.create_controller()
            fixture.case.mutate(protocol.record_context_event, dict(id='retire-controller-route',
                assignment_id=controller['id'], kind='route_retired', source='owner',
                reason='The owner retired this route.', evidence=[fixture.case.supporting]))
            make_codex_stale(self)
            before = set(fixture.case.read_records()['review_assignment'])
            planned = fixture.command('review-assignment', controller['next'][0]['payload'])
            self.assertEqual(len(set(fixture.case.read_records()['review_assignment']) - before), 1)
            with self.assertRaises(ResearchError) as caught:
                fixture.command(planned['next'][0]['command'], planned['next'][0]['payload'])
            self.assertEqual(caught.exception.code, 'review_route_retired')

    def test_plans_on_a_stale_route_keep_a_pending_call_and_name_no_command_on_the_stale_record(self):
        with self.subTest(head='a refused head with a pending call'):
            first, _ = self.build_refused()
            records = self.records()
            records['review_invocation']['pending'] = dict(id='pending', assignment_id=first['id'])
            current = plan_next(records, self.case.artifacts, first['id'])
            self.make_stale()
            self.renew()
            self.assertEqual(plan_next(records, self.case.artifacts, first['id']), current)
        with self.subTest(head='a result turn whose plan is the output correction'):
            import test_review_output_correction as correction_fixtures
            correction = correction_fixtures.CorrectionTests()
            correction.setUp()
            self.addCleanup(correction.doCleanups)
            correction.prepare_refused()
            fixture, assignment = correction.fixture, correction.case.assignment
            current = plan_next(fixture.read_records(), fixture.artifacts, assignment['id'])
            self.assertEqual(current['command'], 'review-output-correction')
            route = fixture.read_records()['review_route'][assignment['route_id']]
            with patch.object(transport, 'runtime', return_value=dict(route['runtime'], implementation_sha256='0' * 64)):
                self.assertNotEqual(transport.runtime(route), route['runtime'])
                # The correction needs a route record with the installed runtime (tests/test_review_route_plan.py).
                stale = plan_next(fixture.read_records(), fixture.artifacts, assignment['id'])
                self.assertEqual((stale['state'], stale['code'], stale['details']['renewal']),
                                 ('handoff', 'review_route_runtime_stale', dict(admitted=True, route_id=None, lacks='record')))
        with self.subTest(head='a staged review with a refused answer'):
            import sys
            import test_review_staged_frontier as frontier_fixtures
            from research_harness import review_worker
            fixture = frontier_fixtures.FrontierTests()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            controller = fixture.create_controller()
            (fixture.case.root / 'refuse').touch()
            with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier_fixtures.WORKER]):
                planned = fixture.command('review-assignment', controller['next'][0]['payload'])
                self.assertTrue(fixture.command('review-run', planned['next'][0]['payload'])['refusal'])
            records = fixture.case.read_records()
            current = plan_next(records, fixture.case.artifacts, controller['id'])
            self.assertEqual((current['state'], current['command']), ('run', 'review-run'))
            make_codex_stale(self)
            # The next call of the staged review cannot run on the stale route record.
            stale = plan_next(records, fixture.case.artifacts, controller['id'])
            self.assertEqual((stale['state'], stale['code']), ('handoff', 'review_route_runtime_stale'))
            self.assertIs(stale['details']['renewal']['admitted'], False)

    def test_recorded_replacement_on_a_route_that_became_stale_keeps_its_handoff(self):
        from research_harness.review_failure_class import count_position
        first, _ = self.build_refused()
        replacement = self.admit(self.plan(first['id']))
        self.make_stale()
        before = set(self.records()['review_assignment'])
        for renewed in (False, True):
            if renewed:
                self.renew()
            line = self.series(first['id'])[-1]
            self.assertEqual((line['state'], line['command'], line['code'], [step['id'] for step in line['details']['steps']]),
                             ('handoff', 'review-context', 'review_route_runtime_stale', [8, 7, 1]), line)
            self.assertEqual(line['details']['steps'][1]['command'], 'review-probe' if renewed else 'review-route')
            records = self.records()
            self.assertEqual(set(records['review_assignment']), before)
            self.assertEqual(count_position(records, self.case.artifacts, replacement)['replacements']['output_refused'], 1)
            self.assertFalse(any(row['assignment_id'] == replacement['id']
                                 for row in records.get('review_invocation', {}).values()))


class RouteRenewalStoredShapeTests(unittest.TestCase):
    """K1. The fixtures of these stores need the problem modules that the session fixture replaces, so this class
    has no session fixture of its own."""

    def run_with_membership_check(self, test_class, method, *, set_up=True):
        from research_harness import review_failure_class as failures
        from review_legacy_fixtures import list_turn_assignments_as_0_91_2
        installed, installed_position = failures.list_turn_assignments, failures.list_failure_position_assignments
        compared = []
        def compare(records, assignment):
            result = installed(records, assignment)
            self.assertEqual(result, list_turn_assignments_as_0_91_2(records, assignment))
            compared.append(assignment['id'])
            return result
        def compare_position(records, artifacts, assignment):
            result = installed_position(records, artifacts, assignment)
            if failures.identify_paged_chain(assignment) is not None:
                # A final in pages counts each page position apart: the members of the rule of 0.91.2 at this page.
                position = failures.read_paged_position(artifacts, assignment)
                self.assertEqual(sorted(result), sorted(
                    identifier for identifier in list_turn_assignments_as_0_91_2(records, assignment)
                    if failures.read_paged_position(artifacts, records['review_assignment'][identifier]) == position))
                compared.append(assignment['id'])
            return result
        fixture = test_class(method)
        if set_up:
            fixture.setUp()
        try:
            with patch.object(failures, 'list_turn_assignments', side_effect=compare), patch.object(
                    failures, 'list_failure_position_assignments', side_effect=compare_position):
                getattr(fixture, method)()
        finally:
            fixture.doCleanups()
        self.assertTrue(compared)

    def test_membership_equals_the_rule_of_0_91_2_for_stored_shapes(self):
        import test_openness_reading_v7_refusal as v7_fixtures
        import test_review_refusal_replacement as session_fixtures
        import test_review_refusal_replacement_incident as incident_fixtures
        import test_review_refusal_replacement_parts as parts_fixtures
        import test_review_parts_v3_pages as series_v3_fixtures
        import test_review_session_v4_turns as session_v4_fixtures
        for shape, test_class, method, set_up in (
                ('session', session_fixtures.RefusalReplacementTests,
                 'test_second_replacement_lists_revived_attempt_and_keeps_head_finality', True),
                ('two replacements on one route', session_fixtures.RefusalReplacementTests,
                 'test_third_replacement_is_refused', True),
                ('repair', incident_fixtures.PartOffsetReplacementIncidentTests,
                 'test_revision_200_repaired_bar_gets_part_offsets_and_records_in_its_slot', False),
                ('parts series', parts_fixtures.PartsReplacementTests,
                 'test_every_parts_kind_can_be_replaced_and_the_corrected_series_records', False),
                ('adjudication', parts_fixtures.PartsReplacementTests,
                 'test_ordinary_focused_adjudication_replaces_its_refusal_and_records', False),
                ('reading chain with a capacity replacement', v7_fixtures.V7RefusalTests,
                 'test_a7_refusal_replacement_capacity_failure_keeps_notice_and_records', True),
                ('session v4', session_v4_fixtures.SessionTurnsTests,
                 'test_refused_final_keeps_level_and_material_with_notice_then_records', True),
                ('series v3 with pages', series_v3_fixtures.PagedSeriesTests,
                 'test_a6_refused_closing_is_replaced_with_notice_then_records', True)):
            with self.subTest(shape=shape):
                self.run_with_membership_check(test_class, method, set_up=set_up)


class RouteRenewalReadingRegressionTests(unittest.TestCase):
    """The reading-chain case of N5. Its fixture needs the problem modules that the session fixture replaces."""

    def test_refused_v7_reading_turn_on_a_stale_route_ends_before_any_record(self):
        case = build_refused_v7_turn(self)
        self.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, 'turn-1')['state'], 'replace')
        make_codex_stale(self)
        check_stale_procedure(self, case.store, 'turn-1', [7, 4], lambda: renew_codex_route(case), renewed_steps=[4])


def build_session_fixture(test):
    """A bar session of exactory-independent-review-sources-v4 on a Codex route."""
    import test_review_session_v4_turns as session_fixtures
    fixture = session_fixtures.SessionTurnsTests('test_refused_final_keeps_level_and_material_with_notice_then_records')
    fixture.setUp()
    test.addCleanup(fixture.doCleanups)
    return fixture


def build_series_fixture(test):
    """A result series of exactory-review-parts-v3 with pages on a Codex route."""
    import test_review_parts_v3_pages as series_fixtures
    fixture = series_fixtures.PagedSeriesTests('test_a6_refused_closing_is_replaced_with_notice_then_records')
    fixture.setUp()
    test.addCleanup(fixture.doCleanups)
    return fixture


def check_series_stops_on_the_stale_route(test, store, head_id):
    """review-series ends with the stale-route procedure of the head, before any record and any command."""
    before = set(store.snapshot()['records']['review_assignment'])
    lines = []
    run_series(store, head_id, emit=lines.append)
    test.assertEqual(set(store.snapshot()['records']['review_assignment']), before)
    test.assertEqual(len(lines), 1, lines)
    test.assertEqual((lines[0]['state'], lines[0]['command'], lines[0]['code']),
                     ('handoff', 'review-context', 'review_route_runtime_stale'))
    test.assertNotIn('error', lines[0])
    test.assertEqual(lines[0]['details']['route_id'], 'route')
    test.assertEqual(lines[0]['details']['renewal'], dict(admitted=False, route_id=None, lacks='record'))
    test.assertEqual([step['id'] for step in lines[0]['details']['steps']], [8, 7, 1])


class RouteRenewalSessionV4SeriesV3Tests(unittest.TestCase):
    """N5 and N6 for a turn of a session of sources v4 and of a series of parts v3, which the 0.92.0 branch adds.
    Both turns are outside the renewal. Their fixtures need the problem modules that the session fixture replaces."""

    def test_refused_session_v4_final_on_a_stale_route_ends_before_any_record(self):
        from codex_route_fixtures import invoke_codex
        fixture = build_session_fixture(self)
        final, _ = fixture.build_final()
        output = fixture.case.build_review_output('bar')
        output['unexpected'] = 'refused'
        invoke_codex(fixture.case, final['id'], output)
        self.assertEqual(plan_next(fixture.case.read_records(), fixture.case.artifacts, final['id'])['state'],
                         'replace')
        make_codex_stale(self)
        check_stale_procedure(self, fixture.case.store, final['id'], [8, 7, 1], lambda: renew_codex_route(fixture.case))

    def test_refused_series_v3_closing_on_a_stale_route_ends_before_any_record(self):
        from research_harness import review_parts
        fixture = build_series_fixture(self)
        assignment = fixture.reach_page(fixture.start(0))
        while fixture.packet(assignment)[review_parts.KEY].get('final_form') != 'closing':
            assignment = fixture.advance(assignment)
        refused = fixture.answer(assignment)
        refused['fields']['/value/status'] = 'invalid-status'
        self.assertIn('output_refusal', fixture.invoke(assignment, refused))
        self.assertEqual(plan_next(fixture.case.read_records(), fixture.case.artifacts, assignment['id'])['state'],
                         'replace')
        make_codex_stale(self)
        check_stale_procedure(self, fixture.case.store, assignment['id'], [8, 7, 1],
                              lambda: renew_codex_route(fixture.case))

    def test_session_v4_continuation_on_a_stale_route_records_nothing(self):
        from research_harness import review_session_v4 as session
        fixture = build_session_fixture(self)
        fixture.add_sources([2000])
        current = fixture.case.assign('bar', 'first', identifier='request')
        _, inventory, _ = session.read_bound_session(fixture.case.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        fixture.respond(current, [dict(id='source', kind='source', version_id=unit['identity']['version_id'],
                                       depth='fulltext')])
        self.assertEqual(plan_next(fixture.case.read_records(), fixture.case.artifacts, current['id'])['state'],
                         'continue')
        make_codex_stale(self)
        check_series_stops_on_the_stale_route(self, fixture.case.store, current['id'])

    def test_session_v4_capacity_replacement_on_a_stale_route_records_nothing(self):
        from codex_route_fixtures import build_codex_response
        from research_harness import review_failure_class as failures
        fixture = build_session_fixture(self)
        fixture.install_measured_profile()
        fixture.add_sources([180000])
        current = fixture.case.assign('bar', 'first', identifier='request')
        def respond_over_limit(route, request, credential):
            response = build_codex_response(route, request, dict())
            response['usage']['output_tokens'] = 138371
            return response
        for number in range(3):
            with patch.object(protocol.transport, 'send_request', side_effect=respond_over_limit):
                fixture.case.mutate(protocol.invoke_assignment, dict(id='cap-%d' % number, assignment_id=current['id']),
                                    credential='test-only')
            step = failures.plan_after_failure(fixture.case.read_records(), fixture.case.artifacts, current['id'])
            if step['state'] != 'run':
                break
        self.assertEqual(step['state'], 'replace', step)
        payload = step['details']['recovery']['payload']
        self.assertEqual(payload['context']['capacity_replacement'], current['id'])
        make_codex_stale(self)
        details = check_refused_without_record(self, fixture.case.store, protocol.record_assignment, payload, 'route')
        self.assertNotIn('renewal', details)

    def test_series_v3_continuation_on_a_stale_route_records_nothing(self):
        fixture = build_series_fixture(self)
        first = fixture.start(0)
        self.assertNotIn('output_refusal', fixture.invoke(first, fixture.answer(first)))
        self.assertEqual(plan_next(fixture.case.read_records(), fixture.case.artifacts, first['id'])['state'],
                         'continue')
        make_codex_stale(self)
        check_series_stops_on_the_stale_route(self, fixture.case.store, first['id'])

    def test_series_v3_capacity_replacement_on_a_stale_route_records_nothing(self):
        from research_harness import review_route_capacity as capacity
        fixture = build_series_fixture(self)
        page = fixture.reach_page(fixture.start(0))
        fixture.invoke_usage(page, tokens=128000)
        recovery = capacity.plan_capacity_recovery(fixture.case.read_records(), fixture.case.artifacts, page,
                                                   'output_over_cap')
        self.assertEqual(recovery['kind'], 'replace', recovery)
        make_codex_stale(self)
        details = check_refused_without_record(self, fixture.case.store, protocol.record_assignment,
                                               recovery['payload'], 'route')
        self.assertNotIn('renewal', details)


if __name__ == '__main__':
    unittest.main()
