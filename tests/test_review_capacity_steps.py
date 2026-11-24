"""A capacity handoff continues the preserved slot through the advertised steps."""

import copy
import json
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from codex_route_fixtures import build_codex_response, invoke_codex, probe_codex_route, record_codex_route
from continuity_forms_fixtures import execute_route_step, fork_store, prepare_unit_fixture
from research_harness import bar_sources, cli, review_protocol as protocol
from research_harness import review_codex_framed_transport as framed, review_codex_transport as codex
from research_harness import review_route_capacity as capacity
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from research_harness.review_plan_steps import CAPACITY_NEW_ENTRY_CONDITION, complete_plan, describe_capacity_steps
import test_review_session_v4_entry as entry_fixtures
import test_review_parts_slate as slate_fixtures


@contextmanager
def fork_rollouts():
    """Give a forked answer its own stand-in of the Codex rollout, which a probe or a run replaces."""
    saved = [(adapter._rollout, adapter._rollout.return_value) for adapter in (codex, framed)
             if isinstance(adapter._rollout, Mock)]
    try:
        yield
    finally:
        for rollout, value in saved:
            rollout.return_value = value


class CapacityAnswers:
    def record_route(self, identifier, tokens, model='fixture-model'):
        with patch.object(self.case, 'capacity_probe_tokens', tokens, create=True):
            record_codex_route(self.case, identifier, model=model, output_tokens=128000)

    def record_cause(self, identifier, head_id, kind):
        return self.case.mutate(protocol.record_context_event, dict(id=identifier, assignment_id=head_id, kind=kind,
            source='owner' if kind == 'route_retired' else 'author_history',
            reason='The retained review needs reassessment.', evidence=[self.case.supporting]))['result']

    def capture_stop(self, payload):
        revision = self.case.store.revision
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, payload)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual(self.case.store.revision, revision)
        return caught.exception.details

    def serving_answers(self, stop):
        return {answer['route_id']: answer['steps'] for answer in stop['then']
                if 'route_id' in answer and 'condition' not in answer}

    def probe_capacity(self, payload, *, observed=None):
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(protocol.transport.PACKET_LABEL):])
            output = dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'],
                          excluded_controls=[])
            if route['adapter'] == protocol.transport.ADAPTER:
                build = getattr(self.case, 'build_provider_response', None) or self.case.provider_response
                response = build(output)
            else:
                response = build_codex_response(route, request, output)
            response['usage']['input_tokens'] = observed if observed is not None else payload['capacity']['input_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            protocol.probe_route(self.case.store, payload, credential='test-only',
                                 expected_revision=self.case.store.revision, request_id=payload['id'])

    def execute_repair(self, steps, route_id, reviewer='first', *, observed=None, probe_id='handoff-probe',
                       renewal_observed=None):
        """Execute advertised commands, completing only the choices offered by those steps."""
        evidence = getattr(self.case, 'supporting', None) or getattr(self.case, 'evidence', None)
        current_probe, retirement, captured, saved = None, None, None, None
        for step in steps:
            payload = copy.deepcopy(step['payload'])
            command = step['command']
            if command == 'review-context':
                choices = dict(id='handoff-retirement', evidence=[evidence],
                               reason='Continue the preserved review.')
                payload.update({key: choices[key] for key in step['chooses']})
                retirement = self.case.mutate(protocol.record_context_event, payload)['result']
            elif command == 'review-probe':
                current_probe = probe_id
                if 'capacity' in payload:
                    self.probe_capacity(dict(payload, id=probe_id), observed=observed)
                else:
                    tokens = renewal_observed if renewal_observed is not None else observed
                    tokens = tokens if tokens is not None else 5000
                    self.probe_capacity(dict(payload, id=probe_id), observed=tokens)
            elif command == 'review-route':
                route_id, commands = execute_route_step(SimpleNamespace(case=self.case), step, 'handoff-route')
                self.assertEqual(commands, ['review-route', 'review-probe'])
                current_probe = 'handoff-route-probe'
            elif command == 'artifact':
                path = self.case.root / 'capture.txt'
                path.write_text('A faithful text form of the synthetic body.')
                if 'id' in step['chooses']:
                    payload['id'] = 'text-form'
                captured = self.case.mutate(cli.OPERATIONS[command], dict(payload, path=path.name))['result']['artifact']
            else:
                self.assertEqual(command, 'review-assignment')
                choices = {'id': 'handoff-repair', 'route_id': route_id, 'reviewer_id': reviewer}
                if captured is not None:
                    choices['context'] = dict(artifact_refs=[captured])
                binding = payload['context'].get('context_repair')
                if binding is not None:
                    if current_probe is None:
                        current_probe = protocol.route_state(self.case.store.snapshot()['records'], self.case.artifacts,
                                                            route_id)['probe_id']
                    choices.update({'context.context_repair.probe_id': current_probe,
                        'context.context_repair.reason': 'Continue the preserved review.',
                        'context.context_repair.event_ids': binding['event_ids'] + (
                            [retirement['id']] if retirement else []),
                        'context.context_repair.evidence': binding['evidence'] + (
                            retirement['evidence'] if retirement else [])})
                for key in step['chooses']:
                    if key.startswith('context.context_repair.'):
                        binding[key.rsplit('.', 1)[-1]] = choices[key]
                    else:
                        payload[key] = choices[key]
                saved = self.case.mutate(protocol.record_assignment, payload)['result']
        # An answer that ends with a probe or a capture records no assignment; the next plan names it.
        if saved is not None:
            self.assertEqual(saved['route_id'], route_id, steps)
        return saved

    def take_probe_then_next_stop(self, stop, route_id, payload, **options):
        """Take the answer of route_id, which ends with its probe, then the answer that the next stop of the same
        payload names for that route."""
        [steps] = [answer['steps'] for answer in stop['then'] if answer.get('route_id') == route_id]
        self.assertEqual(steps[-1]['command'], 'review-probe')
        self.assertIsNone(self.execute_repair(steps, route_id, **options))
        following = CapacityAnswers.capture_stop(self, payload)
        [steps] = [answer['steps'] for answer in following['then'] if answer.get('route_id') == route_id]
        return self.execute_repair(steps, route_id, **options)

    def take_next_plan(self, head_id, route_id, **options):
        """Take the step that the plan of the predecessor names after the answer's probe."""
        plan = plan_next(self.case.store.snapshot()['records'], self.case.artifacts, head_id)
        return self.execute_repair([plan['details']['step']], route_id, **options)

    def take_answers(self, details, *, chosen_route=None, reviewer='first', observed=None, independent_head=None,
                     probe_id='handoff-probe', renewal_observed=None, check_assignment=None):
        """Start every answer at the same revision and leave the other answers' routes verified."""
        if 'then' in details:
            answers = details['then']
        elif 'decision' in details:
            answers = details['decision']['then']
        else:
            answers = [dict(steps=details.get('steps') or [details['step']])]

        def find_route(answer):
            if any(step['command'] == 'review-route' for step in answer['steps']):
                return 'handoff-route'
            return (answer.get('route_id') or answer['steps'][-1]['payload'].get('route_id')
                    or chosen_route)

        def named_routes(steps):
            for step in steps:
                route_id = step.get('payload', {}).get('route_id')
                if route_id and step['command'] in ('review-probe', 'review-assignment'):
                    yield route_id
                yield from named_routes(step.get('then', []))

        if 'serving_routes' in details:
            self.assertEqual({row['route_id'] for row in details['serving_routes']},
                             set(self.serving_answers(details)))
        initial = self.case.store.snapshot()['records']
        initial_plan = (plan_next(initial, self.case.artifacts, independent_head) if independent_head else None)
        usable = {identifier for answer in answers for identifier in named_routes(answer['steps'])}
        usable.update(find_route(answer) for answer in answers
                      if not any(step['command'] == 'review-route' for step in answer['steps']))
        results = {}
        for answer in answers:
            route_id = find_route(answer)
            reset_sequence = patch.object(self.case, 'sequence', getattr(self.case, 'sequence', 0), create=True)
            with self.subTest(route=route_id), fork_store(self), reset_sequence, fork_rollouts():
                saved = self.execute_repair(answer['steps'], route_id, reviewer=reviewer, observed=observed,
                                            probe_id=probe_id, renewal_observed=renewal_observed)
                if check_assignment is not None and saved is not None:
                    check_assignment(saved)
                records = self.case.store.snapshot()['records']
                for identifier in usable | {route_id}:
                    self.assertIn(identifier, records['review_route'])
                    self.assertNotIn(identifier, protocol.retired_routes(records))
                    self.assertEqual(protocol.route_state(records, self.case.artifacts, identifier)['context_status'],
                                     'verified_for_route')
                if independent_head:
                    current = plan_next(records, self.case.artifacts, independent_head)
                    self.assertEqual((current['state'], current.get('code')),
                                     (initial_plan['state'], initial_plan.get('code')))
                key = (route_id, answer['answer']) if route_id in results else route_id
                results[key] = (saved, records)
        self.assertEqual(len(results), len(answers))
        return results


class CapacityRepairStepsTests(CapacityAnswers, unittest.TestCase):
    def setUp(self):
        self.fixture = prepare_unit_fixture(self, entry_fixtures.SessionEntryTests,
            'test_new_slot_below_the_v4_floor_stops_and_a_context_repair_takes_the_next_rule_set')
        self.case = self.fixture.case
        rules = patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        rules.start()
        self.addCleanup(rules.stop)
        self.fixture.add_sources([2000])

    def stop_contamination_repair(self, *, second_route=False, calibrated=False):
        self.fixture.record_legacy_bars()
        self.case.assign('slate', 'second', identifier='other-head')
        self.record_cause('cause', 'bar-first', 'contamination')
        if second_route:
            self.record_route('wide', 5000, model='gpt-6-astra')
        self.record_route('tiny', 130000)
        probe_id = 'probe-tiny'
        if calibrated:
            # The retry's chosen probe ID must fit within the measured request.
            probe_id = 'prior-capacity-probe'
            self.probe_capacity(dict(id=probe_id, route_id='tiny', capacity=dict(input_tokens=130000)))
        head = self.case.read_records()['review_assignment']['bar-first']
        payload = self.fixture.build_repair_payload(head, 'tiny')
        payload['context']['context_repair']['probe_id'] = probe_id
        self.stopped_payload = payload
        return self.capture_stop(payload)

    def test_contamination_repair_reuses_the_predecessors_serving_route(self):
        stop = self.stop_contamination_repair()
        self.assertEqual(stop['serving_routes'], [])
        answer = next(answer for answer in stop['then'] if answer.get('route_id') == 'route')
        self.assertTrue(answer['condition'])
        steps = answer['steps']
        self.assertEqual([step['command'] for step in steps], ['review-probe'])
        self.take_answers(stop, observed=300000, independent_head='other-head')
        saved = self.take_probe_then_next_stop(stop, 'route', self.stopped_payload, observed=300000)
        records = self.case.read_records()
        self.assertEqual(saved['context']['context_repair']['event_ids'], ['cause'])
        self.assertEqual(saved['context']['context_repair']['probe_id'], 'handoff-probe')
        self.assertEqual(protocol.retired_routes(records), set())
        self.assertFalse(protocol.is_dead_assignment(records, records['review_assignment']['other-head']))

    def test_repair_refused_by_the_selected_parts_v3_keeps_the_answers_of_its_capacity_stop(self):
        """Repairs PL-F8 of the review of the merged tree. With the parts protocol v3 selected, a repair of a head
        that is no recorded v3 series and that v3 refuses falls to the planner of v2, so its capacity stop has the
        answers of release 0.92.1, and each answer is taken through the store."""
        from research_harness import review_parts, review_parts_v3
        with patch.object(review_parts, 'NEW_SERIES_PROTOCOLS', (review_parts_v3.PROTOCOL, review_parts.PROTOCOL)):
            stop = self.stop_contamination_repair()
            self.assertEqual([answer['answer'] for answer in stop['then']],
                             ['Probe this route', 'Reassess the retained review'])
            self.take_answers(stop, observed=300000, independent_head='other-head')

    def test_each_serving_answer_leaves_both_routes_usable(self):
        stop = self.stop_contamination_repair(second_route=True)
        self.assertEqual(set(self.serving_answers(stop)), {'wide'})
        self.assertEqual({a['route_id'] for a in stop['then'] if a.get('condition') and 'route_id' in a}, {'route'})
        records = self.case.read_records()
        limits = [capacity.describe_route_capacity(records['review_route'][route_id], records=records,
                  artifacts=self.case.artifacts)['input_tokens'] for route_id in ('route', 'wide')]
        self.assertEqual([(limit['source'], limit['value']) for limit in limits],
                         [('observed', 2872000), ('observed', 2872000)])
        results = self.take_answers(stop, observed=300000, independent_head='other-head')
        self.assertIsNone(results['route'][0])
        with fork_store(self):
            followed = self.take_probe_then_next_stop(stop, 'route', self.stopped_payload, observed=300000)
            records = self.case.read_records()
        for saved, records in (results['wide'], (followed, records)):
            self.assertEqual(saved['context']['context_repair']['event_ids'], ['cause'])
            self.assertEqual(protocol.retired_routes(records), set())

    def test_probe_answer_of_a_stopped_repair_names_the_plan_of_its_predecessor(self):
        """Repairs A11. The probe answer of a stopped repair says that the stopped command binds the earlier probe and
        that the plan of the predecessor names the repair. The test of the probe answer that uses the new probe in the
        same repair follows that plan."""
        from research_harness.review_plan_steps import CAPACITY_PROBE_REPAIR_CONDITION
        stop = self.stop_contamination_repair()
        [answer] = [row for row in stop['then'] if row['answer'] == 'Probe this route']
        self.assertEqual((answer['condition'], [step['id'] for step in answer['steps']]),
                         (CAPACITY_PROBE_REPAIR_CONDITION, [16]))

    def test_capacity_probe_answer_uses_the_new_probe_in_the_same_repair(self):
        """Follow the plan from the stop: each probe that an answer names, then the step that the plan names, until
        the repair records with the new probe, after at most two probes and without repeating a refused step."""
        stop = self.stop_contamination_repair()
        sizes = []
        for _ in range(3):
            [steps] = [answer['steps'] for answer in stop['then'] if answer['answer'] == 'Probe this route']
            self.assertEqual([step['id'] for step in steps], [16])
            self.take_answers(stop, observed=300000)
            size = steps[0]['payload']['capacity']['input_tokens']
            self.assertNotIn(size, sizes)
            sizes.append(size)
            self.probe_capacity(dict(steps[0]['payload'], id='plan-probe-' + str(len(sizes))), observed=size)
            plan = plan_next(self.case.store.snapshot()['records'], self.case.artifacts, 'bar-first')
            self.assertEqual(plan['details']['step']['id'], 1)
            try:
                saved = self.execute_repair([plan['details']['step']], 'tiny')
                break
            except ResearchError as error:
                self.assertEqual(error.code, 'review_route_capacity_below_floor')
                stop = error.details
        self.assertLessEqual(len(sizes), 2)
        self.assertEqual(saved['context']['context_repair']['probe_id'], 'plan-probe-' + str(len(sizes)))
        self.assertEqual(saved['context']['context_repair']['event_ids'], ['cause'])

    def test_exhaustion_repair_keeps_its_binding_on_the_serving_route(self):
        _, payload, _ = self.fixture.build_exhausted_legacy_slate()
        self.record_route('tiny', 130000)
        payload['route_id'] = 'tiny'
        binding = payload['context']['context_repair']
        binding['probe_id'] = 'probe-tiny'
        stop = self.capture_stop(payload)
        answers = self.serving_answers(stop)
        self.assertEqual(set(answers), {'route'})
        self.assertEqual([step['id'] for step in answers['route']], [14])
        saved, records = self.take_answers(stop, observed=300000)['route']
        self.assertEqual(saved['context']['context_repair'], dict(binding, probe_id='repair-probe'))
        self.assertEqual(protocol.retired_routes(records), set())

    def test_slate_without_a_cause_keeps_the_direct_answer_under_the_same_evidence(self):
        from research_harness import review_capacity_evidence
        self.fixture.record_legacy_bars()
        self.record_route('wide', 3000000, model='fixture-wide')
        self.record_route('tiny', 130000)
        payload = dict(id='stopped-slate', role='slate', route_id='tiny', dossier_id=None, reviewer_id='first',
                       author_id='author', context={})
        measure = review_capacity_evidence.read_capacity_evidence
        def read_room(route, *args, **kwargs):
            result = measure(route, *args, **kwargs)
            return dict(result, input_tokens=5000) if route['id'] == 'route' else result
        for observations in (0, 128):
            if observations:
                revision = self.case.store.revision
                def seed_observations(transaction):
                    for index in range(observations):
                        identifier = 'note-%d' % index
                        transaction.put('review_context_event', identifier, dict(id=identifier,
                            assignment_id='bar-second', kind='observation', source='owner',
                            reason='A retained note.', evidence=[self.case.supporting], recorded_revision=revision + 1))
                self.case.store.mutate('fixture.observations', {}, seed_observations,
                                       expected_revision=revision, request_id='notes')
            with self.subTest(observations=observations), \
                    patch.object(review_capacity_evidence, 'read_capacity_evidence', side_effect=read_room), \
                    patch.object(protocol.context_repair, 'find_repair_cause',
                                 wraps=protocol.context_repair.find_repair_cause) as read_cause:
                wall, processor = time.perf_counter(), time.process_time()
                stop = self.capture_stop(payload)
                print('CAPACITY_STOP_COST', observations, round(time.perf_counter() - wall, 5),
                      round(time.process_time() - processor, 5), read_cause.call_count)
                self.assertEqual(read_cause.call_count, 1)
                self.assertEqual([(row['route_id'], row['delivery']) for row in stop['serving_routes']],
                                 [('wide', 'single')])
                saved, records = self.take_answers(stop, observed=300000)['wide']
                self.assertEqual((saved['role'], saved['slot_id'], saved['history_assignment_id']),
                                 ('slate', 'bar-first', 'bar-first'))
                self.assertFalse(protocol.retired_routes(records))

    def test_ordinary_slate_can_use_its_bar_route_without_retirement(self):
        self.fixture.record_legacy_bars()
        self.record_route('tiny', 130000)
        self.record_route('wide', 3000000, model='fixture-wide')
        payload = dict(id='slate', role='slate', route_id='tiny', dossier_id=None, reviewer_id='first',
                       author_id='author', context={})
        stop = self.capture_stop(payload)
        self.assertEqual([(row['route_id'], row['delivery']) for row in stop['serving_routes']],
                         [('route', 'single'), ('wide', 'single')])
        results = self.take_answers(stop, observed=300000)
        for route_id in ('route', 'wide'):
            saved, records = results[route_id]
            self.assertEqual((saved['role'], saved['slot_id'], saved['history_assignment_id']),
                             ('slate', 'bar-first', 'bar-first'))
            self.assertFalse(protocol.retired_routes(records))

    def test_fresh_bar_trials_the_public_payload_at_the_profile_boundary(self):
        self.record_route('from', 128005, model='from-model')
        self.record_route('target', 135829, model='gpt-6.1-sol')
        payload = dict(id='bar-first', role='bar', route_id='from', dossier_id=None, reviewer_id='first',
                       author_id='author', context=self.case.deliver_supporting('first'))
        stop = self.capture_stop(payload)
        self.assertNotIn('target', self.serving_answers(stop))
        target_stop = self.capture_stop(dict(payload, route_id='target'))
        self.assertEqual((target_stop['measure']['tokens_upper'], target_stop['measure']['token_limit']), (7830, 7829))
        for details in (stop, target_stop):
            self.take_answers(details, observed=300000)

        with patch.object(self.case, 'capacity_probe_tokens', 135830, create=True):
            probe_codex_route(self.case, 'fits-exactly', 'target')
        stop = self.capture_stop(payload)
        row = next(row for row in stop['serving_routes'] if row['route_id'] == 'target')
        self.assertEqual((row['measure']['tokens_upper'], row['measure']['token_limit']), (7830, 7830))
        self.take_answers(stop, observed=300000)

    def stop_narrow_exhaustion(self, *, include_competing=False, **extra):
        _, payload, _ = self.fixture.build_exhausted_legacy_slate()
        self.record_route('narrow', 211500, model='fixture-narrow')
        if include_competing:
            self.record_route('almost', 211500, model='fixture-almost')
        payload.update(route_id='narrow', **extra)
        payload['context']['context_repair']['probe_id'] = 'probe-narrow'
        return payload, self.capture_stop(payload)

    def test_floor_repair_trials_known_proofs_on_other_routes(self):
        _, stop = self.stop_narrow_exhaustion(include_competing=True)
        self.assertGreater(stop['measure']['tokens_upper'], stop['measure']['token_limit'])
        self.assertEqual(set(self.serving_answers(stop)), {'route'})
        saved, _ = self.take_answers(stop, observed=300000)['route']
        self.assertIn('exhaustion', saved['context']['context_repair'])

    def test_exhaustion_trial_with_an_old_probe_keeps_the_known_binding(self):
        self.record_route('stale', 211500, model='stale-model')
        self.record_route('old-wide', 3000000, model='wide-model')
        payload, stop = self.stop_narrow_exhaustion(include_competing=True)
        self.assertEqual(set(self.serving_answers(stop)), {'route'})
        named = {a['route_id']: a for a in stop['then'] if 'route_id' in a}
        self.assertNotIn('stale', named)
        self.assertTrue(named['old-wide']['condition'])
        self.take_answers(stop, observed=3000000, probe_id='fresh')
        with fork_store(self):
            saved = self.take_probe_then_next_stop(stop, 'old-wide', payload, observed=3000000, probe_id='fresh')
        binding = payload['context']['context_repair']
        self.assertEqual(saved['context']['context_repair'], dict(binding, probe_id='fresh'))

        with fork_store(self), patch.object(self.case, 'capacity_probe_tokens', 211500, create=True):
            probe_codex_route(self.case, 'fresh', 'stale')
            retry = copy.deepcopy(dict(payload, route_id='stale'))
            retry['context']['context_repair']['probe_id'] = 'fresh'
            refused = self.capture_stop(retry)
            self.assertGreater(refused['measure']['tokens_upper'], refused['measure']['token_limit'])
            self.take_answers(refused, observed=3000000, probe_id='after-fresh')

    def test_stopped_repair_keeps_scheduler_when_retried(self):
        policy = dict(store_limit=2, route_limits={'route': 2})
        _, stop = self.stop_narrow_exhaustion(scheduler=policy)
        steps = self.serving_answers(stop)['route']
        self.assertEqual(steps[-1]['payload'].get('scheduler'), policy)
        _, records = self.take_answers(stop, observed=300000)['route']
        saved, = records['review_capacity_policy'].values()
        self.assertEqual(saved['store_limit'], policy['store_limit'])
        self.assertEqual(saved['route_limits'], policy['route_limits'])

    def test_invalid_scheduler_fails_before_offering_or_committing_a_repair(self):
        _, payload, _ = self.fixture.build_exhausted_legacy_slate()
        self.record_route('narrow', 211500, model='fixture-narrow')
        payload.update(route_id='narrow', scheduler=dict(store_limit=0, route_limits={}))
        binding = payload['context']['context_repair']
        binding['probe_id'] = 'probe-narrow'
        for candidate in (payload, dict(payload, route_id='route', context=dict(
                context_repair=dict(binding, probe_id='repair-probe')))):
            with self.subTest(route=candidate['route_id']):
                before = self.case.store.snapshot()
                with self.assertRaises(ResearchError) as caught:
                    self.case.mutate(protocol.record_assignment, candidate)
                self.assertEqual(caught.exception.code, 'invalid_input')
                self.assertEqual(self.case.store.snapshot(), before)

    def test_retirement_on_another_head_requires_a_retirement_of_this_head(self):
        self.fixture.record_legacy_bars()
        self.case.assign('slate', 'second', identifier='other-head')
        self.record_cause('first-retirement', 'bar-first', 'route_retired')
        self.record_route('wide', 3000000, model='gpt-6-astra')
        plan = plan_next(self.case.read_records(), self.case.artifacts, 'other-head')
        self.assertEqual(plan['code'], 'review_route_retired')
        details = plan['details']
        saved, _ = self.take_answers(details, chosen_route='wide', reviewer='second')['wide']
        binding = saved['context']['context_repair']
        self.assertEqual(binding['assignment_id'], 'other-head')
        self.assertEqual(binding['event_ids'], ['handoff-retirement'])

    def test_other_repair_handoffs_require_a_citable_local_cause(self):
        """Exercise dispatch for injected codes; store journeys cover the distinct procedures."""
        self.fixture.record_legacy_bars()
        self.case.assign('slate', 'second', identifier='other-head')
        self.record_cause('contamination', 'bar-first', 'contamination')
        self.record_cause('retirement', 'bar-first', 'route_retired')
        records = self.case.read_records()
        for code, head_id, expected in (
                ('review_context_contaminated', 'bar-first', [1]),
                ('review_context_repair_required', 'other-head', [8, 7, 1]),
                ('review_context_repair_stale', 'other-head', [8, 7, 1]),
                ('review_context_repair_mismatch', 'other-head', [8, 7, 1]),
                ('review_context_repair_unverified', 'other-head', [8, 7, 1])):
            with self.subTest(code=code):
                head = records['review_assignment'][head_id]
                plan = complete_plan(records, self.case.artifacts, head, dict(state='handoff', code=code))
                details = plan['details']
                self.assertEqual([step['id'] for step in details.get('steps', [details['step']])], expected)

    def test_retired_route_handoff_executes_registration_and_nested_probe(self):
        self.fixture.record_legacy_bars()
        self.case.assign('slate', 'second', identifier='other-head')
        self.record_cause('first-retirement', 'bar-first', 'route_retired')
        plan = plan_next(self.case.read_records(), self.case.artifacts, 'other-head')
        self.assertEqual(plan['code'], 'review_route_retired')
        details = plan['details']
        steps = details['steps']
        self.assertEqual([step['command'] for step in steps],
                         ['review-context', 'review-route', 'review-assignment'])
        self.assertEqual([step['command'] for step in steps[1]['then']], ['review-probe'])
        saved, records = self.take_answers(details, reviewer='second')['handoff-route']
        binding = saved['context']['context_repair']
        self.assertEqual(binding['assignment_id'], 'other-head')
        self.assertEqual(binding['probe_id'], 'handoff-route-probe')
        self.assertIn(binding['probe_id'], records['review_route_probe'])

    def test_an_earlier_retirement_alone_does_not_supply_a_cause(self):
        self.fixture.record_legacy_bars()
        self.record_cause('first-retirement', 'bar-first', 'route_retired')
        self.record_route('copy', 3000000, model='gpt-6-astra')
        head = self.case.read_records()['review_assignment']['bar-first']
        first = self.fixture.build_repair_payload(head, 'copy')
        first['context']['context_repair']['probe_id'] = 'probe-copy'
        predecessor = self.case.mutate(protocol.record_assignment, first)['result']
        self.record_route('wide', 3000000, model='fixture-wide')
        second = self.fixture.build_repair_payload(predecessor, 'wide')
        second['id'] = 'second-repair'
        second['context']['context_repair']['probe_id'] = 'probe-wide'
        self.assertEqual(second['context']['context_repair']['event_ids'], ['first-retirement'])
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, second)
        self.assertEqual(caught.exception.code, 'review_context_repair_mismatch')

        continuation = dict(first, id='continuation', context=dict(prior_assignment_id=predecessor['id']))
        answer, = describe_capacity_steps(self.case.read_records(), self.case.artifacts, continuation,
                                         [dict(route_id='wide', delivery='reassessment')])
        steps = answer['steps']
        self.assertEqual([step['command'] for step in steps], ['review-context', 'review-probe'])
        self.assertEqual(steps[0]['route_id'], 'copy')
        self.take_answers(dict(then=[answer]))
        self.assertIsNone(self.execute_repair(steps, 'wide'))
        saved = self.take_next_plan(predecessor['id'], 'wide')
        self.assertEqual(saved['context']['context_repair']['event_ids'], ['first-retirement', 'handoff-retirement'])


class CapacitySlateSeriesStepsTests(CapacityAnswers, unittest.TestCase):
    def setUp(self):
        self.fixture = prepare_unit_fixture(self, slate_fixtures.SlateSeriesTests,
                                            'test_route_discovery_offers_local_parts_for_an_oversized_slate')
        mode = patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL)
        mode.start()
        self.addCleanup(mode.stop)

    def import_large_bodies(self):
        from research_harness import problem_forming
        from test_problem_forming_import import FormingImportTests
        from test_research_problem_forming import build_forming
        fixture = prepare_unit_fixture(self, FormingImportTests, 'runTest')
        bodies = [fixture.source.artifacts.put(bytes([letter]) * 275000, 'text/plain')
                  for letter in (97, 98, 99, 100)]
        call = fixture.source.mutate(problem_forming.record_forming, build_forming('imported-call', forms=[],
            inputs=[dict(description='Synthetic forming inputs.', evidence=bodies)],
            performer=dict(kind='model_lane', lane_id='fixture', model='fixture-model', effort='reported',
                           prompt=bodies[0], output=bodies[-1])))['result']
        imported = fixture.import_history(destination=self.case)
        cite = fixture.reference('forming_call', call, imported['source_origin'])
        self.case.record_candidate('candidate-2', source=dict(
            kind='computation', reference='Imported finite computations.', evidence=[cite]))

    def test_series_answer_protects_its_route_from_the_reassessment_answer(self):
        """The four imported bodies cross the ordinary limit and leave a real parts-series start."""
        from research_harness import research_decisions as decisions, review_parts
        from test_research_problem_slate import ProblemSlateTests
        self.case = prepare_unit_fixture(self, ProblemSlateTests, 'runTest')
        self.case.record_candidate()
        self.case.prepare_review_route()
        self.case.supporting = self.case.artifacts.put(b'Fixture field evidence.', 'text/plain')
        record_codex_route(self.case, 'local', output_tokens=128000)
        for reviewer in ('first', 'second'):
            context = self.case.deliver_supporting(reviewer)
            bar = dict(id='bar-' + reviewer, role='bar', route_id='local', context=context,
                       author_id='problem-author', dossier_id=None, reviewer_id=reviewer)
            head = self.case.mutate(protocol.record_assignment, bar)['result']
            output = self.case.build_review_output('bar')
            invoke_codex(self.case, head['id'], output)
            self.case.mutate(decisions.record_value_review, dict(output, id='review-' + head['id'],
                dossier_id=None, assignment_id=head['id']))
        payload = dict(id='stopped', role='slate', route_id='tiny', context={},
                       author_id='problem-author', dossier_id=None, reviewer_id='first')
        self.case.mutate(protocol.record_assignment, dict(payload, id='other-head', route_id='local',
                                                         reviewer_id='second'))
        self.import_large_bodies()
        self.case.mutate(protocol.record_route, dict(id='tiny', adapter=protocol.transport.ADAPTER,
            endpoint='https://tiny.example/responses', model='fixture-model',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        self.probe_capacity(dict(id='wide-witness', route_id='route'), observed=1000000)
        self.probe_capacity(dict(id='tiny-probe', route_id='tiny'), observed=1020)
        stop = self.capture_stop(payload)
        results = self.take_answers(stop, observed=2000000, independent_head='other-head')
        self.assertEqual([(row['route_id'], row['delivery']) for row in stop['serving_routes']], [('local', 'parts')])
        saved, _ = results['local']
        self.assertEqual(saved[review_parts.KEY]['adapter'], 'problem_slate')

    def test_projected_slate_turn_stays_on_its_exact_route(self):
        from research_harness import review_parts
        from test_review_parts_v3 import make_turn_output
        self.case = self.fixture.make_case()
        self.import_large_bodies()
        record_codex_route(self.case, 'other-local', output_tokens=128000)
        first = self.fixture.assign(self.case)
        packet = protocol._read_json(self.case.artifacts, first['packet'])
        invoke_codex(self.case, first['id'], make_turn_output(packet))
        following = dict(id='second', role='slate', route_id='local', dossier_id=None, reviewer_id='first',
                         author_id='problem-author', context=dict(prior_assignment_id=first['id']))
        covered = capacity.require_covered_assignment
        captured = {}
        def read_routes(records, artifacts, value, packet, prompt, request=None, history=(), **kwargs):
            if request is not None:
                self.assertEqual(packet[review_parts.KEY]['adapter'], 'problem_slate')
                rows = capacity.find_serving_routes(records, 'slate', packet, artifacts=artifacts,
                                                    assignment=value, prompt=prompt, history=history)
                captured.update(serving_routes=rows, then=describe_capacity_steps(records, artifacts, value, rows,
                                                                                  packet=packet))
            return covered(records, artifacts, value, packet, prompt, request, history, **kwargs)
        with fork_store(self), patch.object(capacity, 'require_covered_assignment', side_effect=read_routes):
            self.case.mutate(protocol.record_assignment, following)
        self.assertEqual([(row['route_id'], row['delivery']) for row in captured['serving_routes']],
                         [('local', 'single')])
        saved, _ = self.take_answers(captured)['local']
        self.assertEqual(saved['context']['prior_assignment_id'], first['id'])
        self.assertEqual([(answer['route_id'], [step['id'] for step in answer['steps']]) for answer in captured['then']],
                         [('local', [14])])
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, dict(following, id='cross-route', route_id='other-local'))
        self.assertEqual(caught.exception.code, 'review_source_continuation_mismatch')


class CapacityPublicEntryTests(CapacityAnswers, unittest.TestCase):
    def test_stopped_study_slate_can_reassess_its_bar_on_the_same_record(self):
        from test_research_context_repair_integration import ContextRepairIntegrationTests
        self.case = prepare_unit_fixture(self, ContextRepairIntegrationTests, 'runTest')
        self.case.mutate(protocol.record_route, dict(id='medium', adapter=protocol.transport.ADAPTER,
            endpoint='https://medium.example/responses', model='fixture-model',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        self.probe_capacity(dict(id='verified', route_id='medium'), observed=23500)
        payload = dict(id='stopped', route_id='medium', dossier_id='dossier-1', role='slate',
                       reviewer_id='reviewer-a', author_id='author', context={})
        stop = self.capture_stop(payload)
        self.assertEqual(stop['measure']['token_limit'], 22500)
        self.assertEqual(stop['serving_routes'], [])
        self.assertNotIn('coverage', stop)
        answer, = [a for a in stop['then'] if a.get('route_id') == 'medium']
        self.assertTrue(answer['condition'])
        self.assertEqual([step['id'] for step in answer['steps']], [8, 7])
        self.take_answers(stop, reviewer='third', observed=300000, renewal_observed=23500)
        saved = self.take_probe_then_next_stop(stop, 'medium', payload, reviewer='third', observed=300000,
                                               renewal_observed=23500)
        records = self.case.store.snapshot()['records']
        self.assertEqual((saved['role'], saved['slot_id']), ('bar', 'assignment-bar-a'))
        self.assertEqual(protocol.retired_routes(records), {'route'})

    def test_body_capture_resumes_the_original_capacity_stop(self):
        from test_research_review_protocol import ReviewProtocolTests
        from test_review_uncovered_stop import UncoveredReviewTests
        self.case = prepare_unit_fixture(self, UncoveredReviewTests, 'runTest')
        self.case.mutate(protocol.record_route, dict(id='route', adapter=protocol.transport.ADAPTER,
            endpoint='https://api.openai.com/v1/responses', model='gpt-6-astra',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        ReviewProtocolTests.run_probe(self.case)
        original = self.case.artifacts.put(b'\xff' * 700000, 'application/octet-stream')
        self.case.seed('execution', 'execution', dict(id='execution', payload=dict(outputs=[dict(artifact=original)])))
        payload = dict(self.case.build_assignment(), context=dict(artifact_refs=[original]))
        stop = self.capture_stop(payload)
        self.assertEqual(stop['reason'], 'body_without_text_form')
        self.assertTrue(stop['then'][0]['condition'])
        _, records = self.take_answers(stop)['route']
        self.assertIn('text-form', records['local_artifact'])

    def test_slate_refresh_falling_back_from_v4_keeps_the_refresh_link(self):
        from research_harness import problem_forming
        from research_harness.evidence import digest
        from test_research_problem_forming import build_forming
        from test_review_session_v4_refresh import SessionRefreshTests
        fixture = prepare_unit_fixture(self, SessionRefreshTests, 'runTest')
        self.case = fixture.case
        fixture.record_legacy_bars()
        fixture.add_sources([2000])
        head = self.case.assign('slate', 'first', identifier='slate')
        final = fixture.drive(head)
        fixture.finish(final)
        binary = self.case.artifacts.put(bytes([255, 254, 253]), 'application/octet-stream')
        forming = self.case.mutate(problem_forming.record_forming, build_forming('extra-form', forms=[],
            inputs=[dict(description='Source evidence', evidence=[binary])]))['result']
        cite = dict(kind='record', record_kind='forming_call', id=forming['id'], digest=digest(forming))
        self.case.put('problem_candidate', 'candidate-extra', dict(id='candidate-extra', previous=None,
            statement='A further candidate statement.', source=dict(kind='computation', reference='notes',
            evidence=[cite]), observation='An observation.', neighbours=[], potential='A potential.',
            notes='', evidence=[binary]))
        payload = fixture.plan(final['id'])['payload']
        self.record_route('wide', 3000000, model='gpt-6-astra')
        self.record_route('tiny', 130000, model='fixture-tiny')
        # The binary forming body stops the refresh for its text form first, under the capture id the step fixes.
        capture = self.capture_stop(dict(payload, route_id='tiny'))
        self.assertEqual(capture['reason'], 'body_without_text_form')
        [step] = [step for answer in capture['then'] for step in answer['steps']]
        self.execute_repair([step], 'tiny')
        stop = self.capture_stop(dict(payload, route_id='tiny'))
        results = self.take_answers(stop, observed=300000)
        self.assertIn('route', results)
        for saved, records in results.values():
            if saved is None:
                continue
            self.assertEqual(saved['role'], 'slate')
            self.assertEqual(saved['context']['session_refresh'], payload['context']['session_refresh'])
            self.assertEqual(saved['slot_id'], final['slot_id'])
            self.assertFalse(protocol.retired_routes(records))

    def test_admitted_parts_adjudicator_retry_contains_only_public_context(self):
        from test_review_parts_repair_journey import AdjudicatorRepairJourneyTests
        fixture = AdjudicatorRepairJourneyTests()
        self.addCleanup(fixture.doCleanups)
        self.case, head, value = fixture.prepare_adjudicator_repair(staged=False)
        self.record_route('tiny', 128050, model='fixture-tiny')
        probe_codex_route(self.case, 'current', head['route_id'])
        binding = dict(assignment_id=head['id'], event_ids=['exposure'], probe_id='probe-tiny',
                       reason='Reassess the retained objection.', evidence=[self.case.supporting])
        payload = dict(value, id='repair', route_id='tiny', reviewer_id='replacement',
                       context=dict(context_repair=binding))
        stop = self.capture_stop(payload)
        results = self.take_answers(stop, observed=3000000)
        for answer in stop['then']:
            if answer['steps'][-1]['command'] == 'review-assignment':
                self.assertEqual(set(answer['steps'][-1]['payload']['context']), {'context_repair'})
        self.assertIn(head['route_id'], results)
        self.assertIn('tiny', results)
        for saved, _ in results.values():
            if saved is not None:
                self.assertEqual(saved['context']['context_repair']['assignment_id'], head['id'])

    def test_retired_plain_adjudicator_keeps_the_focused_assignment_step(self):
        from test_research_problem_adjudication_contract import ProblemAdjudicationContractTests
        self.case = prepare_unit_fixture(self, ProblemAdjudicationContractTests, 'runTest')
        self.case.prepare_reviews(result_reviewer='first')
        self.record_route('wide', 3000000)
        payload = dict(id='focused', role='adjudicator', route_id='route', dossier_id=None,
            reviewer_id='focused-third', author_id=self.case.read_records()['workspace']['problem']['authors'][0],
            context=dict(review_ids=['review-result-first', 'review-result-second'], objection_id='unsupported-implication'))
        head = self.case.mutate(protocol.record_assignment, payload)['result']
        self.record_cause('unrelated', 'bar-first', 'route_retired')
        plan = plan_next(self.case.read_records(), self.case.artifacts, head['id'])
        self.assertEqual(plan['code'], 'review_route_retired')
        saved, _ = self.take_answers(plan['details'], chosen_route='wide', reviewer='replacement')['wide']
        self.assertEqual(plan['details']['step']['id'], 3)
        self.assertEqual(saved['context'], payload['context'])


class CapacityNewEntryTests(CapacityAnswers, unittest.TestCase):
    """A predecessor whose role steps are [7, 3] or [7, 6] moves by a new route record and a new entry on it."""

    def list_new_entry_answers(self, parent, role):
        """The new-entry answers on the route record wide of the stop of a continuation of the parent on its route,
        from the planner of a capacity stop. The continuation stays on the route of its parent, so the trial of the
        public payload on another route is refused, as the store refuses it; every other trial is the store's own
        admission, on a copy of the store."""
        from research_harness.review_plan_steps import describe_capacity_repair

        def try_on_a_copy(payload, **options):
            if payload['context'].get('prior_assignment_id') is not None:
                raise ResearchError('review_source_continuation_mismatch', 'Continue on the route of the parent.')
            with fork_store(SimpleNamespace(case=self.case)):
                self.case.mutate(protocol.record_assignment, payload)
            return dict(delivery='single')

        records = self.case.store.snapshot()['records']
        packet = protocol._read_json(self.case.artifacts, parent['packet'])
        stopped = dict(parent, id='refused', context=dict(parent['context'], prior_assignment_id=parent['id']))
        plan = describe_capacity_repair(records, self.case.artifacts, stopped, packet)
        serving = capacity.find_serving_routes(records, role, packet, artifacts=self.case.artifacts,
            assignment=stopped, prompt=parent['prompt'], exclude_route=parent['route_id'], repair_plan=plan,
            trial_assignment=try_on_a_copy)
        answers = describe_capacity_steps(records, self.case.artifacts, stopped, serving, repair_plan=plan)
        return [row for row in answers if row.get('route_id') == 'wide']

    def find_new_entry_answer(self, parent, role):
        [answer] = self.list_new_entry_answers(parent, role)
        self.assertEqual(answer['condition'], CAPACITY_NEW_ENTRY_CONDITION)
        return answer

    def test_plain_adjudicator_names_the_focused_assignment_only_when_the_store_takes_it(self):
        """Repairs A5. The store refuses the focused replacement of a selected adjudicator without a recorded failure
        (review_adjudicator_fixed), so the answer is not offered; after a failed call of the parent the answer keeps
        both role steps of the release, and the store takes each of them."""
        from test_research_problem_adjudication_contract import ProblemAdjudicationContractTests
        self.case = prepare_unit_fixture(self, ProblemAdjudicationContractTests, 'runTest')
        self.case.prepare_reviews(result_reviewer='first')
        self.record_route('wide', 3000000)
        payload = dict(id='focused', role='adjudicator', route_id='route', dossier_id=None,
            reviewer_id='focused-third', author_id=self.case.read_records()['workspace']['problem']['authors'][0],
            context=dict(review_ids=['review-result-first', 'review-result-second'], objection_id='unsupported-implication'))
        parent = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(self.list_new_entry_answers(parent, 'adjudicator'), [])
        with patch.object(protocol.transport, 'send_request',
                          side_effect=ResearchError('review_provider_failure', 'A provider error.')):
            self.case.mutate(protocol.invoke_assignment, dict(id='failed-call', assignment_id=parent['id']))
        answer = self.find_new_entry_answer(parent, 'adjudicator')
        self.assertEqual([(step['id'], step['command']) for step in answer['steps']],
                         [(7, 'review-route'), (3, 'review-assignment')])
        saved = self.execute_repair(answer['steps'], 'handoff-route', reviewer='replacement')
        self.assertEqual((saved['role'], saved['route_id']), ('adjudicator', 'handoff-route'))

    def test_standalone_review_moves_by_route_record_and_new_first_assignment(self):
        from test_review_uncovered_stop import UncoveredReviewTests
        self.case = prepare_unit_fixture(self, UncoveredReviewTests, 'runTest')
        record_codex_route(self.case, 'route', output_tokens=1000)
        self.record_route('wide', 3000000)
        payload = self.case.build_assignment()
        parent = self.case.mutate(protocol.record_assignment, payload)['result']
        answer = self.find_new_entry_answer(parent, 'standalone')
        self.assertEqual([step['id'] for step in answer['steps']], [7, 6])
        saved = self.execute_repair(answer['steps'], 'handoff-route', reviewer='second-reviewer')
        self.assertEqual((saved['role'], saved['route_id'], saved['context']), ('standalone', 'handoff-route', payload['context']))
