"""Exhausted fixed positions retain immutable causes and authenticated delivery."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import research_decisions as decisions, review_context_repair as repair
from research_harness import review_protocol as protocol, review_refusal as refusal
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_research_problem_reviews as problem_fixtures


class ExhaustionRepairTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        self.case = problem_fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        self.case.prepare_review_route()
        for reviewer in ('first', 'second'):
            self.case.observe('bar', reviewer)

    def invoke(self, assignment, output, identifier=None):
        with patch.object(protocol.transport, 'send_request', return_value=self.case.build_provider_response(output)):
            return self.case.mutate(protocol.invoke_assignment,
                dict(id=identifier or 'attempt-' + assignment['id'], assignment_id=assignment['id']),
                credential='fixture')

    def record(self, assignment, output):
        return self.case.mutate(decisions.record_value_review,
            dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))

    def refuse(self, assignment):
        output = self.case.build_review_output(assignment['role'])
        output['value']['status'] = 'invalid'
        self.invoke(assignment, output)
        with self.assertRaises(ResearchError):
            self.record(assignment, output)

    def replace(self, assignment, identifier):
        payload = {key: copy.deepcopy(assignment[key]) for key in
                   ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
        payload.update(id=identifier)
        payload['context']['output_refusal'] = dict(assignment_id=assignment['id'])
        return self.case.mutate(protocol.record_assignment, payload)['result']

    def build_position(self, role='result', replacements=2):
        assignment = self.case.assign(role, 'first', identifier='initial-' + role)
        self.refuse(assignment)
        for number in range(replacements):
            assignment = self.replace(assignment, role + '-replacement-' + str(number + 1))
            self.refuse(assignment)
        return assignment

    def probe(self, identifier='exhaustion-probe'):
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'].removeprefix('The packet below is JSON.\n'))
            return self.case.build_provider_response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.probe_route, dict(id=identifier, route_id='route'), credential='fixture')

    def build_payload(self, predecessor, identifier='repair'):
        records = self.case.read_records()
        lineage = repair._exposure_lineage(records, predecessor)
        events = repair.cause_events(records, {row['id'] for row in lineage})
        return dict(id=identifier, route_id='route', dossier_id=None, role=predecessor['role'],
            reviewer_id='first', author_id='author', context=dict(context_repair=dict(
                assignment_id=predecessor['id'], event_ids=[event['id'] for event in events],
                evidence=[item for event in events for item in event['evidence']], probe_id='exhaustion-probe',
                reason='Reassess the exhausted position and its retained observations.',
                exhaustion=repair.build_exhaustion_binding(records, self.case.artifacts, predecessor))))

    def admit(self, predecessor):
        self.probe()
        return self.case.mutate(protocol.record_assignment, self.build_payload(predecessor))['result']

    def test_result_handoff_has_complete_binding_and_records_without_retirement(self):
        from research_harness.review_plan_steps import describe_role_handoff
        head = self.build_position()
        records = self.case.read_records()
        frozen = copy.deepcopy(records)
        plan = describe_role_handoff(records, self.case.artifacts, head, 'review_slot_exhausted')
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'review-assignment'))
        step = plan['details']['step']
        self.assertEqual(step['id'], 2)
        binding = step['payload']['context']['context_repair']
        self.assertEqual(binding['exhaustion'], repair.build_exhaustion_binding(records, self.case.artifacts, head))
        self.assertEqual(step['payload']['reviewer_id'], head['reviewer_id'])
        self.assertEqual(set(binding), {'assignment_id', 'event_ids', 'evidence', 'exhaustion'})
        saved = self.admit(head)
        packet = protocol._read_json(self.case.artifacts, saved['packet'])
        self.assertEqual(packet['context_repair']['exhaustion'], binding['exhaustion'])
        self.assertEqual(packet['context_repair']['cause'], [])
        self.assertIn(repair.EXHAUSTION_PROMPT_V1, saved['prompt']['text'])
        self.assertEqual(saved['prompt']['parameters']['context_repair_exhaustion'], repair.EXHAUSTION_PROTOCOL)
        self.assertTrue(protocol._prompt_matches(saved, packet))
        self.assertTrue(any(row.get('raw_observations') for row in packet['scientific_history']))
        output = self.case.build_review_output('result')
        output['reassessment'] = dict(assignment_id=head['id'], findings=[])
        self.invoke(saved, output)
        self.record(saved, output)
        self.assertTrue(protocol.assignment_state(self.case.read_records(), self.case.artifacts, saved['id'])['ready'])
        for kind in ('review_assignment', 'review_attempt', 'review_output_refusal'):
            for identifier, value in frozen[kind].items():
                self.assertEqual(self.case.read_records()[kind][identifier], value)

    def test_slate_handoff_admits_the_same_complete_binding(self):
        from research_harness.review_plan_steps import describe_role_handoff
        head = self.build_position('slate')
        plan = describe_role_handoff(self.case.read_records(), self.case.artifacts, head, 'review_slot_exhausted')
        self.assertEqual(plan['state'], 'handoff')
        saved = self.admit(head)
        repair.validate_repair_binding(self.case.read_records(), self.case.artifacts, saved)

    def test_a_position_with_replacements_left_cannot_claim_exhaustion(self):
        head = self.build_position(replacements=0)
        self.probe()
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, self.build_payload(head))
        self.assertEqual(caught.exception.code, 'review_context_repair_mismatch')

    def test_every_forged_binding_member_is_refused_at_admission(self):
        head = self.build_position()
        self.probe()
        payload = self.build_payload(head)
        original = payload['context']['context_repair']['exhaustion']
        alterations = []
        for key, value in (('protocol', 'unknown'), ('head_assignment_id', 'initial-result'),
                           ('position', original['position'][:-1]), ('position', list(reversed(original['position']))),
                           ('refusals', original['refusals'][:-1]),
                           ('correction', dict(invocation_id='invented', status='declined'))):
            altered = copy.deepcopy(original)
            altered[key] = value
            alterations.append(altered)
        for key, value in (('assignment_id', 'invented'), ('attempt_id', 'invented'),
                           ('output_digest', '0' * 64), ('refusal_digest', '0' * 64), ('recorded_revision', 0)):
            altered = copy.deepcopy(original)
            altered['refusals'][0][key] = value
            alterations.append(altered)
        for altered in alterations:
            with self.subTest(binding=altered):
                prospective = copy.deepcopy(payload)
                prospective['context']['context_repair']['exhaustion'] = altered
                with self.assertRaises(ResearchError) as caught:
                    self.case.mutate(protocol.record_assignment, prospective)
                self.assertEqual(caught.exception.code, 'review_context_repair_mismatch')

    def test_probe_must_follow_the_last_refusal(self):
        head = self.build_position()
        payload = self.build_payload(head)
        payload['context']['context_repair']['probe_id'] = 'probe'
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, payload)
        self.assertEqual(caught.exception.code, 'review_context_repair_unverified')

    def test_origin_uses_stored_evidence_after_the_validator_changes(self):
        head = self.build_position()
        saved = self.admit(head)
        before = copy.deepcopy(self.case.read_records())
        with patch.object(refusal, 'classify_observed_output', side_effect=AssertionError('Do not recheck science')), \
                patch.object(decisions, 'validate_review_contract', side_effect=AssertionError('Do not recheck science')):
            repair.validate_repair_origin(self.case.read_records(), self.case.artifacts, saved)
            repair.validate_repair_binding(self.case.read_records(), self.case.artifacts, saved)
        self.assertEqual(before, self.case.read_records())

    def build_capacity_repair(self, *, continuing):
        from codex_route_fixtures import record_codex_route, probe_codex_route, invoke_codex
        record_codex_route(self.case, 'bounded-route', output_tokens=128000)
        head = self.case.mutate(protocol.record_assignment, dict(id='bounded-initial',
            route_id='bounded-route', dossier_id=None, role='result', reviewer_id='first',
            author_id='author', context={}))['result']
        for number in range(3):
            output = self.case.build_review_output('result')
            output['value']['status'] = 'invalid'
            invoke_codex(self.case, head['id'], output)
            with self.assertRaises(ResearchError):
                self.record(head, output)
            if number < 2:
                head = self.replace(head, 'bounded-refusal-' + str(number))
        probe_codex_route(self.case, 'bounded-exhaustion-probe', 'bounded-route')
        payload = self.build_payload(head)
        payload['route_id'] = 'bounded-route'
        payload['context']['context_repair']['probe_id'] = 'bounded-exhaustion-probe'
        saved = self.case.mutate(protocol.record_assignment, payload)['result']
        first_packet = protocol._read_json(self.case.artifacts, saved['packet'])
        if continuing:
            output = self.case.build_review_output('result')
            output['value']['status'] = 'unresolved'
            output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
            invoke_codex(self.case, saved['id'], output)
            saved = self.case.mutate(protocol.record_assignment, dict(payload, id='ordinary-continuation',
                context=dict(prior_assignment_id=saved['id'])))['result']
        from codex_route_fixtures import build_codex_response
        def over_cap(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=over_cap):
            self.case.mutate(protocol.invoke_assignment,
                dict(id='repair-over-cap', assignment_id=saved['id']), credential='fixture')
        return saved, first_packet

    def check_capacity_history_after_upgrade(self, *, continuing):
        from research_harness import review_parts as parts
        from research_harness.review_route_capacity import plan_capacity_recovery
        saved, original = self.build_capacity_repair(continuing=continuing)
        with patch.object(refusal, 'classify_observed_output', return_value=None):
            recovery = plan_capacity_recovery(self.case.read_records(), self.case.artifacts,
                                              saved, 'output_over_cap')
            if continuing:
                self.assertEqual((recovery['kind'], recovery['code']), ('decision', 'review_output_over_cap'))
                self.assertNotIn('handler_error', recovery['details'])
                payload = {key: copy.deepcopy(saved[key]) for key in
                           ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
                payload['id'] = 'continued-capacity-replacement'
            else:
                self.assertEqual(recovery['kind'], 'replace', recovery)
                payload = recovery['payload']
            replacement = self.case.mutate(protocol.record_assignment, payload)['result']
        packet = protocol._read_json(self.case.artifacts, replacement['packet'])
        logical = parts.read_logical_packet(self.case.artifacts, packet[parts.KEY])
        self.assertEqual(logical['context_repair']['exhaustion'], original['context_repair']['exhaustion'])
        self.assertEqual(logical['historical_reviews'], original['historical_reviews'])
        for identifier in original['context_repair']['exhaustion']['position']:
            turn = next(row for row in logical['scientific_history'] if row['assignment_id'] == identifier)
            original_turn = next(row for row in original['scientific_history'] if row['assignment_id'] == identifier)
            self.assertIsNone(original_turn['observed_output'])
            self.assertEqual([protocol._read_json(self.case.artifacts, row['artifact']) for row in turn['outputs']],
                [[protocol.read_raw_observation(self.case.read_records(), self.case.artifacts, row['attempt_id'])
                  for row in original_turn['raw_observations']]])
        repair.validate_repair_origin(self.case.read_records(), self.case.artifacts, replacement)

    def test_capacity_recovery_keeps_refused_history_after_a_validator_upgrade(self):
        self.check_capacity_history_after_upgrade(continuing=False)

    def test_ordinary_continuation_capacity_recovery_keeps_exhaustion_raw_history(self):
        self.check_capacity_history_after_upgrade(continuing=True)

    def test_revived_predecessor_cannot_fork_its_active_repair(self):
        head = self.build_position()
        self.admit(head)
        before = self.case.store.snapshot()
        with patch.object(refusal, 'classify_observed_output', return_value=None):
            with self.assertRaises(ResearchError) as caught:
                self.case.mutate(protocol.record_assignment, self.build_payload(head, 'forked-repair'))
        self.assertEqual(caught.exception.code, 'review_resampling_forbidden')
        self.assertEqual(self.case.store.snapshot(), before)

    def test_origin_refuses_forged_position_refusal_output_and_probe_records(self):
        head = self.build_position()
        saved = self.admit(head)
        original = self.case.read_records()
        changes = []
        changed = copy.deepcopy(original)
        changed['review_assignment']['result-replacement-1']['context'].pop('output_refusal')
        changes.append(changed)
        changed = copy.deepcopy(original)
        changed['review_attempt']['attempt-' + head['id']]['status'] = 'failed'
        changes.append(changed)
        changed = copy.deepcopy(original)
        changed['review_output_refusal']['attempt-' + head['id']]['output_digest'] = '0' * 64
        changes.append(changed)
        changed = copy.deepcopy(original)
        changed['review_output_refusal']['attempt-' + head['id']]['recorded_revision'] = saved['recorded_revision'] + 1
        changes.append(changed)
        changed = copy.deepcopy(original)
        changed['review_route_probe']['exhaustion-probe']['recorded_revision'] = saved['recorded_revision']
        changes.append(changed)
        for changed in changes:
            with self.subTest(changed=digest(changed)):
                with self.assertRaises(ResearchError) as caught:
                    repair.validate_repair_origin(changed, self.case.artifacts, changed['review_assignment'][saved['id']])
                self.assertEqual(caught.exception.code, 'review_context_repair_unverified')

    def test_a_later_predecessor_event_makes_the_exhaustion_binding_stale(self):
        head = self.build_position()
        saved = self.admit(head)
        self.case.mutate(protocol.record_context_event, dict(id='later-contamination', assignment_id=head['id'],
            kind='contamination', source='author_history', reason='A later actual exposure.',
            evidence=[self.case.supporting]))
        repair.validate_repair_origin(self.case.read_records(), self.case.artifacts, saved)
        with self.assertRaises(ResearchError) as caught:
            repair.validate_repair_binding(self.case.read_records(), self.case.artifacts, saved)
        self.assertEqual(caught.exception.code, 'review_context_repair_stale')

    def test_rebinding_after_a_later_event_keeps_the_original_exhaustion(self):
        from research_harness.review_continuation import plan_next
        head = self.build_position()
        first = self.admit(head)
        self.case.mutate(protocol.record_context_event, dict(id='later-contamination', assignment_id=head['id'],
            kind='contamination', source='author_history', reason='A later actual exposure.',
            evidence=[self.case.supporting]))
        self.probe('after-later-event')
        with patch.object(refusal, 'classify_observed_output', return_value=None):
            plan = plan_next(self.case.read_records(), self.case.artifacts, first['id'])
            self.assertEqual((plan['state'], plan['command']), ('handoff', 'review-assignment'))
            payload = copy.deepcopy(plan['details']['step']['payload'])
            payload.update(id='repair-after-event', route_id='route')
            payload['context']['context_repair'].update(probe_id='after-later-event',
                reason='Rebind the accepted exhaustion proof to the later cause.')
            saved = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(saved['context']['context_repair']['exhaustion'],
                         first['context']['context_repair']['exhaustion'])
        self.assertEqual(saved['context']['context_repair']['event_ids'], ['later-contamination'])
        repair.validate_repair_binding(self.case.read_records(), self.case.artifacts, saved)
        from research_harness.review_delivered_evidence import collect_exhaustion_predecessor_turns
        self.assertEqual(collect_exhaustion_predecessor_turns(
            self.case.read_records(), self.case.artifacts, saved), [])
        self.case.mutate(protocol.record_context_event, dict(id='repair-own-contamination', assignment_id=saved['id'],
            kind='contamination', source='author_history', reason='An exposure in the repair itself.',
            evidence=[self.case.supporting]))
        ordinary = plan_next(self.case.read_records(), self.case.artifacts, saved['id'])
        ordinary_binding = ordinary['details']['step']['payload']['context']['context_repair']
        self.assertEqual(ordinary_binding['assignment_id'], saved['id'])
        self.assertNotIn('exhaustion', ordinary_binding)

    def test_an_exhausted_continuation_releases_its_earlier_repair(self):
        head = self.build_position()
        first = self.admit(head)
        request = self.case.build_review_output('result')
        request['value']['status'] = 'unresolved'
        request['source_requests'] = [dict(id='inventory', kind='inventory_page', page=0)]
        self.invoke(first, request)
        continuation = self.case.assign('result', 'first', identifier='repair-continuation',
                                        prior_assignment_id=first['id'])
        self.refuse(continuation)
        for number in (1, 2):
            continuation = self.replace(continuation, 'continued-replacement-' + str(number))
            self.refuse(continuation)
        self.probe('after-exhausted-continuation')
        payload = self.build_payload(head, 'repair-again')
        payload['context']['context_repair']['probe_id'] = 'after-exhausted-continuation'
        saved = self.case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(saved['context']['context_repair']['assignment_id'], head['id'])

    def test_pending_and_accepted_corrections_cannot_authenticate_exhaustion(self):
        head = self.build_position()
        self.probe()
        for status in ('pending', 'accepted'):
            records = copy.deepcopy(self.case.read_records())
            revision = records['review_route_probe']['exhaustion-probe']['recorded_revision'] - 1
            records['review_output_correction_invocation'] = {'correction': dict(id='correction',
                assignment_id=head['id'], recorded_revision=revision)}
            if status == 'accepted':
                records['review_output_correction'] = {'correction': dict(id='correction',
                    correction_status=status, recorded_revision=revision)}
            payload = self.build_payload(head)
            payload['subject'] = head['subject']
            payload['context']['context_repair']['exhaustion'] = repair.build_exhaustion_binding(
                records, self.case.artifacts, head)
            with self.subTest(status=status), self.assertRaises(ResearchError) as caught:
                repair.prepare_repair(records, self.case.artifacts, payload)
            self.assertEqual(caught.exception.code, 'review_context_repair_mismatch')

    def test_every_earlier_repair_prompt_keeps_its_exact_bytes(self):
        exhaustion_prompt = protocol.canonical_prompt('result', repair=True, orchestration='problem_creation',
            repair_prompt=repair.EXHAUSTION_PROMPT_V1, common_version=1)
        for suffix in repair.EARLIER_PROMPTS + (repair.PROMPT,):
            prompt = protocol.canonical_prompt('result', repair=True, orchestration='problem_creation',
                                               repair_prompt=suffix, common_version=1)
            assignment = dict(role='result', subject=dict(kind='problem'), prompt=prompt,
                              reassessment_required=True, context={})
            before = copy.deepcopy(prompt)
            self.assertTrue(protocol._prompt_matches(assignment))
            self.assertEqual(prompt, before)
            self.assertNotEqual(prompt, exhaustion_prompt)
            self.assertNotIn('context_repair_exhaustion', prompt['parameters'])

    def test_parts_final_uses_the_versioned_exhaustion_cause_and_replays_the_old_final(self):
        from research_harness import review_parts_prompts, review_parts_repair
        for exhausted in (False, True):
            parameters = dict(repair=True, orchestration='problem_creation',
                review_parts=dict(adapter='context_repair', kind='final'))
            if exhausted:
                parameters['repair_prompt'] = repair.EXHAUSTION_PROMPT_V1
            prompt = protocol.canonical_prompt('result', **parameters)
            suffix = (review_parts_repair.EXHAUSTION_FINAL_PROMPT_V1 if exhausted
                      else review_parts_prompts.FINAL_REPAIR)
            self.assertTrue(prompt['text'].endswith(suffix))
            if exhausted:
                self.assertNotIn('This series reassesses after a recorded contamination', prompt['text'])


class ExhaustionDeliveryTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_predecessor_input_is_credited_only_for_the_same_reviewer_and_route(self):
        from research_harness.review_delivered_evidence import read_chain_delivery, read_selected_delivery
        fixture = ExhaustionRepairTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        case = fixture.case
        predecessor = fixture.build_position()
        repaired = fixture.admit(predecessor)
        output = case.build_review_output('result')
        output['reassessment'] = dict(assignment_id=predecessor['id'], findings=[])
        fixture.invoke(repaired, output)
        fixture.record(repaired, output)
        records = case.read_records()
        body = case.supporting
        attempt = records['review_attempt']['attempt-' + repaired['id']]
        view = read_chain_delivery(records, case.artifacts, repaired, attempt=attempt)
        self.assertIn(body['sha256'], view['artifacts'])
        selected = read_selected_delivery(records, case.artifacts, repaired['id'])
        self.assertIn(body['sha256'], selected['coverage_witnesses']['artifacts'])
        self.assertIn(predecessor['packet_digest'],
                      [packet['digest'] for packet in selected['coverage_witnesses']['packets']])
        self.assertNotIn(predecessor['packet_digest'],
                         [packet['digest'] for packet in selected['current_input']['packets']])
        from research_harness.review_delivered_evidence import collect_exhaustion_predecessor_turns
        for field, value in (('reviewer_id', 'another-reviewer'), ('route_id', 'another-route')):
            changed = dict(repaired, **{field: value})
            with self.subTest(field=field):
                self.assertEqual(collect_exhaustion_predecessor_turns(records, case.artifacts, changed), [])
        ordinary = copy.deepcopy(repaired)
        ordinary['context']['context_repair'].pop('exhaustion')
        self.assertEqual(collect_exhaustion_predecessor_turns(records, case.artifacts, ordinary), [])
        for forged in (dict(protocol=repair.EXHAUSTION_PROTOCOL),
                       dict(repaired['context']['context_repair']['exhaustion'], refusals=[])):
            changed = copy.deepcopy(records)
            saved = changed['review_assignment'][repaired['id']]
            saved['context']['context_repair']['exhaustion'] = forged
            with self.subTest(forged=forged), self.assertRaises(ResearchError) as caught:
                collect_exhaustion_predecessor_turns(changed, case.artifacts, saved)
            self.assertEqual(caught.exception.code, 'review_context_repair_unverified')


if __name__ == '__main__':
    unittest.main()
