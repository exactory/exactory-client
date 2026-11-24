"""Real native controller consumers of reading and parts composite finals."""

import copy
from itertools import product
import json
import shutil
import unittest
from unittest.mock import patch

import property_stub_b
from codex_route_fixtures import limit_codex_input, probe_codex_route, record_codex_route
from research_harness import native_review, openness_reading as reading, openness_successor
from research_harness import openness_reading_v7 as v7
from research_harness import review_composition, review_composite
from research_harness import review_parts, review_parts_v3, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_continuation import plan_next
from search_controller.errors import SearchError
from search_controller.reviewer import require_review
from search_controller.storage import ContentSink
from tests import openness_chain_support
from tests.openness_chain_support import MathOpennessChainCase
from tests.openness_support import attach_minimum
from tests import test_review_parts_native as native_parts_fixtures
from test_review_parts_v3 import make_turn_output
import test_openness_reading_v7_turns as v7_fixtures
from test_openness_reading_math_v2 import cite_evidence_bodies


MATH_V2 = 'exactory-openness-reading-math-v2'


class ControllerMathCase(MathOpennessChainCase):
    """Export the proof, searches and supplementary input through the controller."""

    build_v7_answer = v7_fixtures.V7CommandTests.build_v7_answer

    def build_controller_math_fixture(self, *, neighbours=0, output_tokens=128000,
                                      response_size=90000, text_size=50000):
        with patch.object(openness_chain_support, 'record_codex_route', side_effect=lambda case:
                record_codex_route(case, output_tokens=output_tokens, model='gpt-6-astra')):
            self.build_fixture(response_sizes=(response_size,), text_sizes=(text_size,))
        self.subject = self.controller.status()['checkpoints']['checkpoint-000001']
        if neighbours:
            original = copy.deepcopy(self.review['findings']['openness']['minimum'])
            self.review = attach_minimum(self.controller, self.review, subject=self.subject,
                neighbours=['Synthetic neighbouring formulation %d.' % index for index in range(neighbours)])
            minimum = self.review['findings']['openness']['minimum']
            minimum['searches'] = original['searches'] + minimum['searches']
            minimum['reading_digests'] = original['reading_digests']
        self.supplementary = self.artifact('supplementary.md',
            'This supplementary text is absent from every captured search response.')
        self.export_fixture(self.subject, [self.supplementary])
        native = json.loads(self.artifacts.read(self.exported['native_packet']))
        self.proof_digest = next(row['digest'] for row in native['evidence']
            if self.artifacts.read(row['artifact']) == (self.root / 'proof.md').read_bytes())
        limit_codex_input(self, 1300000)
        return native

    def build_controller_final(self, packet, assignment, decision, seen, digests=None):
        """The native result review. Its evidence cites the delivered bodies of the units that the native packet
        lists, or, when digests is given, those digests as the manifest and the notes of a chain name them."""
        if decision != 'approve':
            return dict(decision=decision, reason='A further proof argument is needed.',
                        objections=[dict(blocking=True, description='The endpoint case needs review.')])
        output = copy.deepcopy(self.review)
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
        if digests is None and reading.find_stage(packet) == 'final':
            return property_stub_b.build_math_final(packet, packet['reading_final'])
        output['findings']['openness']['evidence'] = (list(digests) if digests is not None else [dict(ref=handle)
            for handle in cite_evidence_bodies(self.artifacts, assignment, native, seen)])
        if reading.find_stage(packet) == 'closing':
            output = {key: output[key] for key in ('decision', 'findings')}
            for key in ('minimum', 'statement_digest', 'coverage'):
                output['findings']['openness'].pop(key)
        else:
            output['findings']['openness'].pop('minimum')
        return output

    def finish_controller_math_chain(self, decision='approve', *, first='turn-1', assignment=None, digests=None):
        assignment = assignment or self.assign_turn(first)
        packets, identifiers, seen = [], [], {}
        for number in range(100):
            packet = self.read_turn(assignment['id'])
            packets.append(packet)
            identifiers.append(assignment['id'])
            self.assertEqual(reading.find_protocol(packet), MATH_V2)
            stage = reading.find_stage(packet)
            if stage == 'reading':
                seen.update((row['handle'], row['sha256']) for row in packet['reading_turn']['plan']['now'])
                output = self.build_v7_answer(packet)
            elif stage == 'page':
                output = dict(targets=[dict(formulation=target, coverage='The bound sources cover this formulation.',
                    sentence='The formulation is covered.') for target in packet['reading_final']['page']['targets']])
            elif stage == 'combine':
                block = packet['reading_combine']['block']
                output = dict(block=block['handle'], summary='The source bindings remain available.',
                              refs=[block['entries'][0]['handle']])
            else:
                output = self.build_controller_final(packet, assignment, decision, seen, digests)
            result = self.invoke(assignment['id'], output)
            self.assertNotIn('output_refusal', result, result)
            if stage in ('final', 'closing'):
                records = self.store.snapshot()['records']
                self.review, composite = review_composite.read_final_output(records, self.artifacts, assignment)
                return assignment, packets, identifiers, composite
            assignment = self.assign_turn(first + '-next-' + str(number + 2), assignment['id'])
        self.fail('The controller math chain did not reach its final.')

    def check_controller_review(self, **options):
        return require_review(self.root, self.controller.status(), ContentSink(self.controller.store),
            self.review, subject=self.subject, new=True, **options)

    def assert_controller_delivery(self, native, packets):
        units = json.loads(self.artifacts.read(self.store.snapshot()['records']
            ['review_assignment']['turn-1']['reading_chain']['inventory']))['units']
        by_digest = {row['sha256']: row for row in units}
        for identity in (self.proof_digest, self.supplementary['digest']):
            self.assertIn(identity, by_digest)
            self.assertIn(identity, {row['digest'] for row in native['evidence']})
            delivered = ''.join(row['content'] for packet in packets for row in packet['evidence']
                                if row['artifact']['sha256'] == identity)
            self.assertEqual(delivered, self.artifacts.read(by_digest[identity]['artifact']).decode('utf-8'))
        self.assertEqual(native['scientific_context']['supplementary_inputs']['inputs'][0]['digest'],
                         self.supplementary['digest'])
        self.assertNotIn('audit_forecast', native['scientific_context'])
        self.assertEqual(native['scientific_context']['openness_minimum']['schema_version'], 1)
        final_native = packets[-1]['native']
        self.assertEqual(final_native['subject'], native['subject'])
        for field in ('scope', 'supplementary_inputs'):
            self.assertEqual(final_native['scientific_context'][field], native['scientific_context'][field])


class MathReadingControllerTests(ControllerMathCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.start_patch(patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (MATH_V2, reading.MATH_PROTOCOL), create=True))
        self.start_patch(patch.object(v7, 'MATH_V2_FINAL_FORMS', ('request', 'pages')))

    def assert_controller_acceptance(self, native, packets, identifiers, composite):
        native_review.validate_output(native, self.review)
        self.assertEqual(self.review['findings']['openness']['minimum'], native['scientific_context']['openness_minimum'])
        self.assert_controller_delivery(native, packets)
        checked = self.check_controller_review()
        self.assertEqual([row['assignment_id'] for row in checked['reading_turns']], identifiers)
        self.accept_result()
        state = self.controller.status(full_audit=True)
        accepted = state['acceptances']['acceptance-000001']
        repeated = self.check_controller_review(record=accepted)
        self.assertEqual(repeated['reading_turns'], checked['reading_turns'])
        self.assertEqual(repeated.get('composite'), checked.get('composite'))
        self.assertEqual(state['freshness']['status'], 'fresh')
        self.assertEqual(composite is not None, 'composite' in checked)
        changed = copy.deepcopy(accepted)
        changed['reading_turns'][0]['attempt_id'] = 'changed-attempt'
        with self.assertRaises(SearchError) as caught:
            self.check_controller_review(record=changed)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')

    def test_a25_real_controller_request_delivers_proof_and_supplementary_input(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        native = self.build_controller_math_fixture()
        respond = self.respond

        def answer_request(route, request, output):
            message = request['input'][-1]['content']
            if reading.find_stage(property_stub_b.read_packet(message)) == 'final':
                output = property_stub_b.build_answer(request['instructions'], message)
            return respond(route, request, output)

        with patch.object(self, 'respond', side_effect=answer_request):
            assignment, packets, identifiers, composite = self.finish_controller_math_chain()
        self.assertIsNone(composite)
        self.assertTrue(reading.verify_chain(self.store.snapshot()['records'], self.artifacts, assignment))
        self.assert_controller_acceptance(native, packets, identifiers, composite)
        registry = read_packet_registry()
        for packet in packets:
            self.assertEqual(validate_packet('native_math', packet, registry), [])

    def test_a25_real_controller_two_pages_and_closing_accept_native_review(self):
        native = self.build_controller_math_fixture(neighbours=3, output_tokens=5000)
        assignment, packets, identifiers, composite = self.finish_controller_math_chain()
        self.assertIsNotNone(composite)
        self.assertEqual(sum(reading.find_stage(packet) == 'page' for packet in packets), 2)
        self.assertEqual(len(self.review['findings']['openness']['coverage'].splitlines()), 4)
        self.assertTrue(reading.verify_chain(self.store.snapshot()['records'], self.artifacts, assignment))
        self.assert_controller_acceptance(native, packets, identifiers, composite)

    def assert_closing_assessment_authorizes_nothing(self, decision):
        native = self.build_controller_math_fixture(neighbours=3, output_tokens=5000)
        assignment, packets, identifiers, composite = self.finish_controller_math_chain(decision)
        self.assertIsNotNone(composite)
        self.assertEqual(self.review['decision'], decision)
        native_review.validate_output(native, self.review)
        self.assertTrue(reading.verify_chain(self.store.snapshot()['records'], self.artifacts, assignment))
        before = self.controller.status()
        with self.assertRaises(SearchError) as caught:
            self.accept_result()
        self.assertEqual(caught.exception.code, 'native_review_not_approved')
        self.assertEqual(self.controller.status(), before)
        self.assertEqual(before['acceptances'], {})

    def test_a25_real_controller_closing_revise_is_recorded_and_authorizes_nothing(self):
        self.assert_closing_assessment_authorizes_nothing('revise')

    def test_a25_real_controller_closing_reject_is_recorded_and_authorizes_nothing(self):
        self.assert_closing_assessment_authorizes_nothing('reject')

    def test_stored_math_v1_chain_keeps_output_receipts_and_digests_after_v2_activation(self):
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
            native = self.build_controller_math_fixture()
            identifiers = self.run_chain()
            native_review.validate_output(native, self.review)
            checked = self.check_controller_review()
            self.accept_result()
        before = copy.deepcopy(self.store.snapshot()['records'])
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        repeated = self.check_controller_review(record=accepted)
        self.assertEqual(repeated['output'], self.review)
        self.assertEqual(repeated['output_digest'], checked['output_digest'])
        self.assertEqual(repeated['reading_turns'], checked['reading_turns'])
        self.assertEqual([row['assignment_id'] for row in repeated['reading_turns']], identifiers)
        self.assertEqual(self.store.snapshot()['records'], before)

    def test_a25_real_controller_successor_export_carries_v1_readings_to_recorded_review(self):
        with patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (reading.MATH_PROTOCOL,)):
            native = self.build_controller_math_fixture()
            assignment = self.assign_turn('turn-1')
            old_packets = []
            for number in range(30):
                packet = self.read_turn(assignment['id'])
                if reading.find_stage(packet) == 'final':
                    break
                old_packets.append(packet)
                self.invoke(assignment['id'], self.build_reading_response(packet))
                assignment = self.assign_turn('old-' + str(number + 2), assignment['id'])
            else:
                self.fail('The native v1 chain did not reach its final.')
            with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1000):
                with self.assertRaises(ResearchError) as caught:
                    self.invoke(assignment['id'], self.review)
        before = self.store.snapshot()['records']
        stored = {kind: {key: digest(row) for key, row in before.get(kind, {}).items()}
                  for kind in ('review_assignment', 'review_attempt', 'review_observation')}
        handoff = openness_successor.describe_successor_handoff(before, self.artifacts, assignment,
                                                               caught.exception.code, caught.exception.details)
        self.assertEqual(handoff['state'], 'stop')
        self.assertEqual(handoff['details']['successor']['protocol'], MATH_V2)
        answer = handoff['details']['decision']['then'][0]
        self.assertEqual(answer['answer'], openness_successor.MATH_SUCCESSOR_ANSWER)
        [step] = answer['steps']
        self.assertEqual((step['id'], step['command']), (5, 'search review-packet'))
        self.assertEqual(step['payload']['openness'], native['scientific_context']['openness_minimum'])
        self.review['reviewer']['attestation_id'] = 'review-assignment:successor'
        self.exported = self.controller.command('review-packet', dict(step['payload'],
            reviewer=self.review['reviewer'], inputs=[self.supplementary]), None, None)
        successor_native = json.loads(self.artifacts.read(self.exported['native_packet']))
        payload = dict(step['then'][1]['payload'], id='successor',
                       context=dict(native_packet=self.exported['native_packet']))
        from research_harness.check_mode import check_mutation
        checked = check_mutation(self.store, protocol.record_assignment, payload,
            expected_revision=self.store.revision, request_id='check-controller-successor', bounded_payload=payload)
        assigned = protocol.record_assignment(self.store, payload, expected_revision=self.store.revision,
                                              request_id='assign-controller-successor')['result']
        self.assertEqual(checked['carried_readings'], assigned['carried_readings'])
        # The successor's reading turns deliver no carried unit, so its final cites the bound records by digest.
        minimum = successor_native['scientific_context']['openness_minimum']
        cited = [row['search_digest'] for row in minimum['searches']] + minimum['reading_digests'] + [
            self.proof_digest, self.supplementary['digest']]
        final, packets, identifiers, composite = self.finish_controller_math_chain(first='successor', assignment=assigned,
                                                                                   digests=cited)
        self.assertTrue(reading.verify_chain(self.store.snapshot()['records'], self.artifacts, final))
        old_bodies = {row['artifact']['sha256'] for packet in old_packets for row in packet['evidence']}
        self.assertFalse(old_bodies & {row['artifact']['sha256'] for packet in packets for row in packet['evidence']})
        current = self.store.snapshot()['records']
        for kind, group in stored.items():
            self.assertEqual({key: digest(current[kind][key]) for key in group}, group)
        native_review.validate_output(successor_native, self.review)
        self.accept_result()
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        receipts = self.check_controller_review(record=accepted)['reading_turns']
        self.assertEqual(receipts[-1]['assignment_id'], identifiers[-1])
        self.assertEqual(receipts[0]['assignment_id'], 'turn-1')


class MathRequestControllerTests(ControllerMathCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.start_patch(patch.object(reading, 'NEW_MATH_CHAIN_PROTOCOLS', (MATH_V2, reading.MATH_PROTOCOL)))

    def build_controller_math_fixture(self, *, response_size=512, text_size=512, **options):
        return super().build_controller_math_fixture(response_size=response_size, text_size=text_size, **options)

    def reach_final(self, first='turn-1'):
        assignment = self.assign_turn(first)
        for number in range(1, 20):
            packet = self.read_turn(assignment['id'])
            if reading.find_stage(packet) == 'final':
                return assignment
            self.assertEqual(reading.find_stage(packet), 'reading')
            self.assertNotIn('output_refusal', self.invoke(assignment['id'], self.build_v7_answer(packet)))
            assignment = self.assign_turn(first + '-read-' + str(number), assignment['id'])
        self.fail('The fixture must reach its one-request final.')

    def send_final(self, assignment, change=None):
        respond = self.respond

        def answer(route, request, output):
            message = request['input'][-1]['content']
            packet = property_stub_b.read_packet(message)
            review = property_stub_b.build_answer(request['instructions'], message)
            return respond(route, request, change(packet, review) if change else review)

        with patch.object(self, 'respond', side_effect=answer):
            return self.invoke(assignment['id'], {})

    def plan_final(self, assignment):
        return plan_next(self.store.snapshot()['records'], self.artifacts, assignment['id'])

    def correct_final(self, assignment):
        plan = self.plan_final(assignment)
        self.assertEqual((plan['state'], plan['command']), ('replace', 'review-assignment'))
        return protocol.record_assignment(self.store, plan['payload'],
            expected_revision=self.store.revision, request_id='correct-final-' + assignment['id'])['result']

    def fill_step(self, step, choices):
        self.assertEqual(set(choices), set(step['chooses']))
        payload = copy.deepcopy(step['payload'])
        for key, value in choices.items():
            head, _, tail = key.partition('.')
            if tail:
                payload[head][tail] = value
            else:
                payload[head] = value
        return payload

    def execute_native_step(self, step, choices, request_id):
        self.assertEqual(step['root'], str(self.root.resolve()))
        payload = self.fill_step(step, choices)
        target = payload.pop('target', None)
        return self.controller.command(step['command'].split(' ', 1)[1], payload, self.controller.status()['revision'],
                                       request_id, target=target)

    def accept_checkpoint_from_plan(self, assignment, plan=None):
        if plan is None:
            plan = self.plan_final(assignment)
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'search accept'))
        step = plan['details']['step']
        available = dict(self.build_acceptance(), target='checkpoint-000001')
        self.execute_native_step(step, {key: available[key] for key in step['chooses']},
                                 'accept-from-plan-' + assignment['id'])
        self.review = step['payload']['review']
        self.assertIn('acceptance-000001', self.controller.status()['acceptances'])

    def assert_checkpoint_refusal(self, path, change, *, correct=False):
        self.build_controller_math_fixture()
        assignment = self.reach_final()
        refusal = self.send_final(assignment, change).get('output_refusal', {})
        self.assertEqual((refusal.get('code'), refusal.get('details', {}).get('path')),
                         ('invalid_native_review', path))
        if correct:
            replacement = self.correct_final(assignment)
            self.assertNotIn('output_refusal', self.send_final(replacement))
            self.accept_checkpoint_from_plan(replacement)

    def test_missing_required_citation_can_be_corrected_and_accepted_from_the_plan(self):
        def omit(packet, review):
            required = [row for row in packet['reading_final']['evidence_units']['rows'] if row['required']]
            self.assertGreaterEqual(len(required), 2)
            review['findings']['openness']['evidence'] = [dict(ref=required[0]['handle'])]
            return review
        self.assert_checkpoint_refusal('/findings/openness/evidence', omit, correct=True)

    def test_a_pre_capture_date_refuses_the_checkpoint_final(self):
        def predate(packet, review):
            review['findings']['openness']['searched_at'] = '1970-01-01'
            return review
        self.assert_checkpoint_refusal('/findings/openness/searched_at', predate)

    def test_a_digest_absent_from_the_native_packet_refuses_the_checkpoint_final(self):
        def cite_unknown(packet, review):
            review['findings']['openness']['evidence'].append('f' * 64)
            return review
        self.assert_checkpoint_refusal('/findings/openness/evidence', cite_unknown)

    def test_a_schema_one_approval_refuses_the_checkpoint_final(self):
        def downgrade(packet, review):
            review['schema_version'] = 1
            review['findings'].pop('openness')
            return review
        self.assert_checkpoint_refusal('/schema_version', downgrade)

    def test_a_blocked_minimum_refuses_the_checkpoint_final(self):
        def claim_solved(packet, review):
            review['findings']['openness']['outcome'] = 'already_solved'
            return review
        self.assert_checkpoint_refusal('/findings/openness/outcome', claim_solved)

    def test_a_checkpoint_with_unresolved_openness_can_still_be_accepted(self):
        self.build_controller_math_fixture()
        checkpoint = self.reach_final()

        def unresolved(packet, review):
            review['findings']['openness']['outcome'] = 'unresolved'
            return review

        self.assertNotIn('output_refusal', self.send_final(checkpoint, unresolved))
        self.accept_checkpoint_from_plan(checkpoint)

    def test_closure_requires_resolved_openness_and_runs_complete_from_the_plan(self):
        self.build_controller_math_fixture()
        checkpoint = self.reach_final()
        self.assertNotIn('output_refusal', self.send_final(checkpoint))
        self.review = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts,
                                                checkpoint['id'])['output']
        self.accept_result()
        closure_inputs = self.build_closure()['inputs']
        closure = self.reach_final('closure-1')

        def unresolved(packet, review):
            review['findings']['openness']['outcome'] = 'unresolved'
            return review

        refused = self.send_final(closure, unresolved).get('output_refusal', {})
        self.assertEqual((refused.get('code'), refused.get('details', {}).get('path')),
                         ('invalid_native_review', '/findings/openness/outcome'))
        corrected = self.correct_final(closure)
        self.assertNotIn('output_refusal', self.send_final(corrected))
        plan = self.plan_final(corrected)
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'search complete'))
        self.execute_native_step(plan['details']['step'], {key: closure_inputs for key in plan['details']['step']['chooses']},
                                 'complete-from-plan')
        self.assertEqual(self.controller.status()['proof_status'], 'proved')

    def assert_nonapproval_step(self, decision, objections):
        self.build_controller_math_fixture()
        assignment = self.reach_final()
        assessment = dict(decision=decision, reason='The endpoint needs further work.', objections=objections)
        self.assertNotIn('output_refusal', self.send_final(assignment, lambda packet, review: assessment))
        before = self.controller.status()['revision']
        plan = self.plan_final(assignment)
        self.assertEqual((plan['state'], plan['code'], plan['command']),
                         ('handoff', 'native_review_not_approved', 'search review-packet'))
        self.assertEqual({key: plan['details'][key] for key in ('review_decision', 'reason', 'objections')},
                         dict(review_decision=decision, reason=assessment['reason'], objections=objections))
        fresh = self.start_fresh_review(assignment, plan, inputs=[self.supplementary])
        self.assertEqual(self.controller.status()['revision'], before)
        self.assertEqual(native_review.load_packet(self.artifacts, fresh['context']['native_packet'], 'openness-reviewer')
                         ['reviewer']['attestation_id'], 'review-assignment:' + fresh['id'])

    def test_revise_offers_a_native_new_review_step(self):
        self.assert_nonapproval_step('revise', [])

    def test_reject_offers_a_native_new_review_step(self):
        self.assert_nonapproval_step('reject', [dict(blocking=True, description='The bound argument is incomplete.')])

    def start_fresh_review(self, previous, plan, *, inputs, later=None, route_id='route', step=None, revised=None):
        from research_harness.check_mode import check_mutation
        from research_harness.review_route_capacity import read_chain_capacity_plan

        if step is None:
            self.assertIn(plan['state'], ('handoff', 'stop'))
            self.assertEqual(plan['command'], 'search review-packet')
            step = plan['details']['step']
        native = json.loads(self.artifacts.read(previous['context']['native_packet']))
        minimum = native['scientific_context']['openness_minimum']
        if later is not None:
            minimum['searches'].append(dict(formulation=later['question'], search_digest=digest(later)))
        old_id = 'reissued-closure' if plan.get('code') == 'native_review_legacy_closure' else 'revised'
        attestation = step['payload']['reviewer'].get('attestation_id', 'review-assignment:' + old_id)
        fresh_id = attestation.split(':', 1)[1]
        choices = dict(subject=native['subject'], claim_digest=native['claim_digest'], openness=minimum, inputs=inputs)
        choices['reviewer.attestation_id'] = attestation
        choices.update({key: step['payload'][key] for key in step['chooses'] if key in step['payload']})
        if revised is not None:
            choices.update(revised)
        exported = self.execute_native_step(step, {key: choices[key] for key in step['chooses']}, 'export-' + fresh_id)
        [check, write] = step['then']
        self.assertTrue(check['check'])
        self.assertEqual(check['command'], write['command'])
        self.assertEqual(write['command'], 'review-assignment')
        available = {'id': fresh_id, 'route_id': route_id, 'context.native_packet': exported['native_packet']}
        check_payload = self.fill_step(check, {key: available[key] for key in check['chooses']})
        revision = self.store.revision
        checked = check_mutation(self.store, protocol.record_assignment, check_payload, expected_revision=revision,
                                 request_id='check-' + fresh_id, bounded_payload=check_payload)
        self.assertEqual((checked['check'], checked['recorded']), ('accepted', False))
        self.assertEqual(self.store.revision, revision)
        payload = self.fill_step(write, {key: available[key] for key in write['chooses']})
        prepared = protocol.record_assignment(self.store, payload, expected_revision=revision,
                                              request_id='assign-' + fresh_id)['result']
        self.assertEqual(prepared['id'], fresh_id)
        self.assertEqual(self.store.revision, revision + 1)
        self.assertEqual(checked['capacity_plan'], read_chain_capacity_plan(self.artifacts, prepared))
        self.assertEqual(self.plan_final(prepared)['state'], 'run')
        if plan.get('code') != 'review_output_over_cap':
            after = self.plan_final(previous)
            self.assertEqual(after['state'], 'done')
            if 'reason' in plan['details']:
                for field in ('review_decision', 'reason', 'objections'):
                    self.assertEqual(after['details'].get(field), plan['details'][field])
        return prepared

    def execute_cap_decision(self, final, plan, *, inputs, later=None):
        [answer] = plan['details']['decision']['then']
        # Review M-F4: a capped request final stops after one retry, which lowers no frame of a route of its class.
        self.assertNotIn('condition', answer)
        route, export = answer['steps']
        self.assertEqual((route['command'], export['command']), ('review-route', 'search review-packet'))
        choices = dict(route['payload'], id='route-after-cap', model='fixture-other')
        payload = self.fill_step(route, {key: choices[key] for key in route['chooses']})
        recorded = protocol.record_route(self.store, payload, expected_revision=self.store.revision,
                                         request_id='route-after-cap')['result']
        self.assertEqual(recorded['id'], payload['id'])
        [probe] = route['then']
        self.assertEqual(probe['command'], 'review-probe')
        choices = dict(id='probe-after-cap', route_id=payload['id'])
        probe_payload = self.fill_step(dict(probe, payload=probe.get('payload', {})), choices)
        probe_codex_route(self, probe_payload['id'], probe_payload['route_id'])
        fresh = self.start_fresh_review(final, plan, inputs=inputs, route_id=payload['id'], step=export)
        if later is not None:
            self.assertIn(digest(later), {item['search_digest'] for item in export['payload']['openness']['searches']})
        replay = self.plan_final(final)
        self.assertEqual((replay['state'], replay['details']['successor_assignment_id']), ('done', fresh['id']))
        self.assert_no_stopped_call(final, 'review_output_over_cap')
        return fresh

    def run_planned_turn(self, assignment, *, decision='approve', invalid=False, capped=False, unresolved=False):
        from codex_route_fixtures import bind_codex_response

        plan = self.plan_final(assignment)
        self.assertEqual((plan['state'], plan['command']), ('run', 'review-run'), plan)
        def answer(route, request, credential):
            message = request['input'][-1]['content']
            packet = property_stub_b.read_packet(message)
            value = property_stub_b.build_answer(request['instructions'], message)
            if invalid:
                value = (dict(irrelevant=True) if reading.find_stage(packet) == 'reading' else
                         dict(decision='revise', reason='Needs work.', objections=['invalid shape']))
            elif decision != 'approve' and reading.find_stage(packet) == 'final':
                value = dict(decision=decision, reason='The proof needs further work.', objections=[])
            elif unresolved and reading.find_stage(packet) == 'final':
                value['findings']['openness']['outcome'] = 'unresolved'
            response = self.respond(route, request, value)
            if capped:
                response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
                return bind_codex_response(route, request, response)
            return response
        with patch.object(transport, 'send_request', side_effect=answer):
            return protocol.invoke_assignment(self.store, plan['payload'], credential='test-only',
                expected_revision=self.store.revision, request_id=plan.get('request_id', 'plan-' + plan['payload']['id']))

    def planned_final(self, first):
        for _ in range(16):
            if reading.find_stage(self.read_turn(first['id'])) == 'final':
                return first
            self.assertNotIn('output_refusal', self.run_planned_turn(first))
            plan = self.plan_final(first)
            self.assertEqual((plan['state'], plan['command']), ('continue', 'review-assignment'), plan)
            first = protocol.record_assignment(self.store, plan['payload'], expected_revision=self.store.revision,
                                               request_id='next-' + plan['payload']['id'])['result']
        self.fail('The planned reading chain did not reach its final.')

    def record_material_search(self, label):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return ProblemOpennessMinimumTests.search(self.case, self.versions,
            formulation=self.subject['claim']['statement'], query=label, captured_at='2026-10-17T12:00:00Z')

    def test_search_before_observation_needs_a_fresh_export_instead_of_output_correction(self):
        self.build_controller_math_fixture()
        final = self.reach_final()
        later = self.record_material_search('A material search before observation')
        refusal = self.send_final(final)['output_refusal']
        self.assertEqual(refusal['details']['code'], 'openness_search_stale')
        plan = self.plan_final(final)
        self.assertEqual(plan['code'], 'native_review_stale_minimum')
        self.start_fresh_review(final, plan, inputs=[self.supplementary], later=later)

    def observe_terminal_history(self, subject, history, decision):
        inputs = [self.supplementary]
        if subject != 'checkpoint':
            spec = self.build_closure()
            inputs = spec['inputs']
            if subject == 'legacy':
                self.export_fixture(dict(spec['subject'], schema_version=1), inputs)
            first = self.assign_turn('closure-1')
            if subject == 'legacy':
                plan = self.plan_final(first)
                self.assertEqual(plan.get('code'), 'native_review_legacy_closure', plan)
                self.assert_no_stopped_call(first, 'native_review_legacy_closure')
                first = self.start_fresh_review(first, plan, inputs=inputs)
        else:
            first = self.assign_turn('turn-1')
        if history in ('first-refusal', 'both-refused'):
            self.assertIn('output_refusal', self.run_planned_turn(first, invalid=True))
            first = self.correct_final(first)
        final = self.planned_final(first)
        if history == 'cap':
            self.run_planned_turn(final, decision=decision, capped=True)
        if history in ('final-refusal', 'both-refused', 'cap'):
            self.assertIn('output_refusal', self.run_planned_turn(final, invalid=True))
            final = self.correct_final(final)
        result = self.run_planned_turn(final, decision=decision, capped=history == 'cap')
        self.assertNotIn('output_refusal', result)
        return final, result, inputs

    def assert_terminal_history(self, subject, history, freshness, decision, observation=None):
        final, result, inputs = observation or self.observe_terminal_history(subject, history, decision)
        later = self.record_material_search('A later material search') if freshness == 'later' else None
        plan = self.plan_final(final)
        if history == 'cap':
            self.assertEqual((plan['state'], plan['code']), ('stop', 'review_output_over_cap'), plan)
            self.assert_no_stopped_call(final, 'review_output_over_cap')
            payload = dict(id=result['result']['id'], assignment_id=final['id'])
            with patch.object(transport, 'send_request') as send:
                replay = protocol.invoke_assignment(self.store, payload, expected_revision=self.store.revision,
                    request_id=result['request_id'], credential='test-only')
            self.assertEqual(replay, result)
            send.assert_not_called()
            self.execute_cap_decision(final, plan, inputs=inputs, later=later)
            return
        if decision != 'approve':
            self.assertEqual(plan['code'], 'native_review_not_approved')
            self.assertEqual(plan['details']['review_decision'], decision)
            self.start_fresh_review(final, plan, inputs=inputs, later=later)
            return
        if later is not None:
            self.assertEqual(plan['code'], 'native_review_stale_minimum', plan)
            fresh = self.start_fresh_review(final, plan, inputs=inputs, later=later)
            final = self.planned_final(fresh)
            self.assertNotIn('output_refusal', self.run_planned_turn(final))
            plan = self.plan_final(final)
        if subject == 'checkpoint':
            self.accept_checkpoint_from_plan(final, plan)
        else:
            self.assertEqual(plan['command'], 'search complete')
            step = plan['details']['step']
            self.execute_native_step(step, {key: inputs for key in step['chooses']}, 'complete-from-plan')
            self.assertEqual(self.controller.status()['proof_status'], 'proved')
        self.assertEqual(self.plan_final(final)['state'], 'done')
        self.record_material_search('A material search after native recording')
        self.assertEqual(self.controller.status(full_audit=True)['freshness']['status'], 'fresh')
        self.assertEqual(self.plan_final(final)['state'], 'done')

    def assert_no_stopped_call(self, assignment, code):
        with patch.object(transport, 'send_request',
                          side_effect=AssertionError('The stopped native review reached transport.')) as send:
            with self.assertRaises(ResearchError) as stopped:
                protocol.invoke_assignment(self.store, dict(id='unplanned-call', assignment_id=assignment['id']),
                    expected_revision=self.store.revision, request_id='unplanned-call', credential='test-only')
            self.assertEqual(stopped.exception.code, code)
            send.assert_not_called()

    def test_planned_native_transitions_cover_subject_history_freshness_and_decision(self):
        self.build_controller_math_fixture()
        self.assertEqual(self.store.root, self.root.resolve())
        checkpoint = self.root.parent / 'checkpoint-fixture'
        closure = self.root.parent / 'closure-fixture'
        fixture = ('review', 'exported')
        checkpoint_state = {key: copy.deepcopy(getattr(self, key)) for key in fixture}
        shutil.copytree(self.root, checkpoint)
        # Seed the native acceptance without using the handoff under test.
        terminal = self.reach_final()
        self.assertNotIn('output_refusal', self.send_final(terminal))
        self.review = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, terminal['id'])['output']
        self.accept_result()
        closure_state = {key: copy.deepcopy(getattr(self, key)) for key in fixture}
        shutil.copytree(self.root, closure)
        sequence = self.case.sequence
        observed = self.root.parent / 'observed-fixture'
        for subject, history, decision in product(
                ('checkpoint', 'closure', 'legacy'), ('clean', 'first-refusal', 'final-refusal', 'cap'),
                ('approve', 'revise', 'reject')):
            with self.subTest(subject=subject, history=history, decision=decision):
                shutil.rmtree(self.root)
                seed, saved = (checkpoint, checkpoint_state) if subject == 'checkpoint' else (closure, closure_state)
                shutil.copytree(seed, self.root)
                for key, value in saved.items():
                    setattr(self, key, copy.deepcopy(value))
                self.case.sequence = sequence
                observation = self.observe_terminal_history(subject, history, decision)
                review = copy.deepcopy(self.review)
                observation_sequence = self.case.sequence
                shutil.copytree(self.root, observed)
                try:
                    for freshness in ('current', 'later'):
                        with self.subTest(freshness=freshness):
                            self.assert_terminal_history(subject, history, freshness, decision, observation)
                        if freshness == 'current':
                            shutil.rmtree(self.root)
                            shutil.copytree(observed, self.root)
                            self.review = copy.deepcopy(review)
                            self.case.sequence = observation_sequence
                finally:
                    shutil.rmtree(observed)

    def test_a_stored_hypothesis_has_no_acceptance_step(self):
        self.build_controller_math_fixture()
        spec = self.external_checkpoint()
        spec['checkpoint']['kind'] = 'hypothesis'
        self.controller.command('checkpoint', spec, self.controller.status()['revision'], 'invalid-acceptance-subject')
        self.subject = self.controller.status()['checkpoints']['checkpoint-000002']
        self.export_fixture(self.subject, [self.supplementary])
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final))
        before = self.controller.status()['revision']
        plan = self.plan_final(final)
        self.assertEqual((plan['state'], plan['code']), ('stop', 'native_review_recording_blocked'))
        self.assertEqual(plan['details']['message'], 'Hypotheses grant no accepted progress')
        self.assertEqual(plan['details']['refusal']['code'], 'invalid_record')
        inspected = self.execute_native_step(plan['details']['step'], {}, 'inspect-hypothesis')
        self.assertEqual(inspected['revision'], before)

    def run_series_to_end(self, assignment):
        from research_harness.review_series import run_series

        rows = []
        run_series(self.store, assignment['id'], emit=rows.append)
        return rows[-1]

    def test_series_ends_at_the_stale_stop_after_a_recorded_approval(self):
        """Review M-F1: the plan of an approved final whose minimum a later search made stale keeps the decision of
        the review in details.review_decision, and review-series ends with that stop."""
        self.build_controller_math_fixture()
        final = self.reach_final()
        self.assertNotIn('output_refusal', self.send_final(final))
        self.record_material_search('A later material search')
        plan = self.plan_final(final)
        self.assertEqual((plan['state'], plan['code'], plan['details']['review_decision']),
                         ('stop', 'native_review_stale_minimum', 'approve'))
        ending = self.run_series_to_end(final)
        self.assertEqual((ending['state'], ending['code'], ending['details']['review_decision']),
                         ('stop', 'native_review_stale_minimum', 'approve'))
        [answer] = ending['details']['decision']['then']
        self.assertEqual(answer['steps'], [ending['details']['step']])

    def test_stale_stop_after_a_refused_final_answers_with_its_export(self):
        """Review M-F3: the stop native_review_stale_minimum after a final refused for a stale minimum has one answer,
        the checked export of details.step; review-series prints it, and taking it starts the new review."""
        self.build_controller_math_fixture()
        final = self.reach_final()
        later = self.record_material_search('A material search before observation')
        self.assertEqual(self.send_final(final)['output_refusal']['details']['code'], 'openness_search_stale')
        plan = self.plan_final(final)
        self.assertEqual((plan['state'], plan['code']), ('stop', 'native_review_stale_minimum'))
        ending = self.run_series_to_end(final)
        [answer] = ending['details']['decision']['then']
        self.assertEqual(answer['steps'], [ending['details']['step']])
        self.assertEqual(answer['steps'][0]['command'], 'search review-packet')
        fresh = self.start_fresh_review(final, plan, inputs=[self.supplementary], later=later,
                                        step=plan['details']['decision']['then'][0]['steps'][0])
        self.assertEqual(self.plan_final(final)['details']['successor_assignment_id'], fresh['id'])

    def test_series_ends_at_the_blocked_stop_of_an_approved_hypothesis(self):
        """Review M-F1: review-series ends with the stop native_review_recording_blocked of an approved hypothesis,
        whose decision is the decision of its registry row."""
        self.build_controller_math_fixture()
        spec = self.external_checkpoint()
        spec['checkpoint']['kind'] = 'hypothesis'
        self.controller.command('checkpoint', spec, self.controller.status()['revision'], 'series-hypothesis')
        self.subject = self.controller.status()['checkpoints']['checkpoint-000002']
        self.export_fixture(self.subject, [self.supplementary])
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final))
        ending = self.run_series_to_end(final)
        self.assertEqual((ending['state'], ending['code'], ending['details']['review_decision']),
                         ('stop', 'native_review_recording_blocked', 'approve'))
        [answer] = ending['details']['decision']['then']
        self.assertEqual(answer['steps'], [ending['details']['step']])
        self.assertEqual(answer['steps'][0]['command'], 'search guard-status')

    def refuse_successor(self, predecessor, native_packet, identifier):
        """Record a first turn that names predecessor as its native predecessor; return the refusal of the admission,
        after checking that the store did not change."""
        payload = dict(id=identifier, route_id='route', dossier_id=None, role='native_math',
                       reviewer_id=predecessor['reviewer_id'], author_id=predecessor['author_id'],
                       context=dict(native_packet=native_packet, native_review_predecessor=predecessor['id']))
        revision = self.store.revision
        with self.assertRaises(ResearchError) as refused:
            protocol.record_assignment(self.store, payload, expected_revision=revision, request_id='refused-' + identifier)
        self.assertEqual(self.store.revision, revision)
        return refused.exception

    def test_a_successor_of_a_hypothesis_final_gets_the_harness_stop_of_the_recording_check(self):
        """The recovery check of the admission asks the controller to record the approval of a hypothesis, which it
        refuses (invalid_record): the admission refuses with the stop of the plan, native_review_recording_blocked,
        and keeps the controller's code and message; the plan of the head stays that stop, whose step runs."""
        self.build_controller_math_fixture()
        spec = self.external_checkpoint()
        spec['checkpoint']['kind'] = 'hypothesis'
        self.controller.command('checkpoint', spec, self.controller.status()['revision'], 'invalid-acceptance-subject')
        self.subject = self.controller.status()['checkpoints']['checkpoint-000002']
        self.export_fixture(self.subject, [self.supplementary])
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final))
        plan = self.plan_final(final)
        self.review['reviewer']['attestation_id'] = 'review-assignment:hypothesis-successor'
        self.export_fixture(self.subject, [self.supplementary])
        refusal = self.refuse_successor(final, self.exported['native_packet'], 'hypothesis-successor')
        self.assertEqual((refusal.code, refusal.message), ('native_review_recording_blocked',
                                                           'Hypotheses grant no accepted progress'))
        self.assertEqual(refusal.details, dict(refusal=plan['details']['refusal']))
        self.assertEqual(refusal.details['refusal']['code'], 'invalid_record')
        self.assertEqual(self.plan_final(final), plan)
        before = self.controller.status()['revision']
        inspected = self.execute_native_step(plan['details']['step'], {}, 'inspect-hypothesis-successor')
        self.assertEqual(inspected['revision'], before)

    def test_a_successor_export_older_than_a_material_search_gets_the_harness_stop_of_the_export_check(self):
        """The export check of the admission finds a material search recorded after the successor's export, which
        the controller refuses (openness_search_stale): the admission refuses with native_review_recording_blocked,
        keeps the controller's code and message in export_refusal, and the plan of the head offers a fresh export
        with that search, which the store takes."""
        self.build_controller_math_fixture()
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final, decision='revise'))
        plan = self.plan_final(final)
        step = plan['details']['step']
        native = json.loads(self.artifacts.read(final['context']['native_packet']))
        fresh_id = step['payload']['reviewer']['attestation_id'].split(':', 1)[1]
        choices = dict(subject=native['subject'], claim_digest=native['claim_digest'], inputs=[self.supplementary],
                       openness=native['scientific_context']['openness_minimum'])
        exported = self.execute_native_step(step, {key: choices[key] for key in step['chooses']}, 'export-' + fresh_id)
        later = self.record_material_search('A material search after the export')
        refusal = self.refuse_successor(final, exported['native_packet'], fresh_id)
        self.assertEqual(refusal.code, 'native_review_recording_blocked')
        self.assertEqual(refusal.details['export_refusal']['code'], 'openness_search_stale')
        self.assertEqual(refusal.message, refusal.__cause__.message)
        self.assertEqual(refusal.details['export_refusal'],
                         dict(code=refusal.__cause__.code, details=refusal.__cause__.details))
        replan = self.plan_final(final)
        self.assertEqual((replan['state'], replan['code']), ('handoff', 'native_review_not_approved'))
        self.start_fresh_review(final, replan, inputs=[self.supplementary], later=later)

    def test_different_formulation_with_shared_work_requires_a_fresh_export(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests

        self.build_controller_math_fixture()
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final))
        original = self.plan_final(final)
        ProblemOpennessMinimumTests.search(self.case, self.versions, formulation='Another exact formulation',
                                           query='Another query for the same work')
        plan = self.plan_final(final)
        self.assertNotEqual(plan, original)
        self.assertEqual((plan['state'], plan['code'], plan['details']['cause']),
                         ('stop', 'native_review_stale_minimum', 'review_packet_mismatch'))
        successor = self.start_fresh_review(final, plan, inputs=[self.supplementary])
        new_final = self.planned_final(successor)
        self.assertNotIn('output_refusal', self.run_planned_turn(new_final))
        self.accept_checkpoint_from_plan(new_final)

    def test_unresolved_checkpoint_with_empty_later_search_can_record(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests

        self.build_controller_math_fixture()
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final, unresolved=True))
        ProblemOpennessMinimumTests.search(self.case, [], formulation=self.subject['claim']['statement'],
                                           query='new empty search', gaps=['No captured enumeration.'])
        self.accept_checkpoint_from_plan(final)
        self.assertEqual(self.plan_final(final)['state'], 'done')
        self.assertEqual(self.controller.status(full_audit=True)['freshness']['status'], 'fresh')

    def test_cap_after_a_material_search_takes_the_owner_steps(self):
        self.build_controller_math_fixture()
        self.assert_terminal_history('checkpoint', 'cap', 'later', 'approve')

    def test_series_after_a_cap_answer_names_the_successor(self):
        """Review M-F2: after the answer of the cap stop of a math v2 final starts its successor chain, the plan of
        the capped head is done with successor_assignment_id, and review-series on that head ends there."""
        self.build_controller_math_fixture()
        final, _, inputs = self.observe_terminal_history('checkpoint', 'cap', 'approve')
        fresh = self.execute_cap_decision(final, self.plan_final(final), inputs=inputs)
        ending = self.run_series_to_end(final)
        self.assertEqual((ending['state'], ending['details']['successor_assignment_id']), ('done', fresh['id']))
        self.assertEqual(self.plan_final(fresh)['state'], 'run')

    def test_legacy_closure_link_rejects_a_real_checkpoint_packet(self):
        from research_harness.check_mode import check_mutation

        self.build_controller_math_fixture()
        checkpoint = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(checkpoint))
        self.accept_checkpoint_from_plan(checkpoint)
        spec = self.build_closure()
        self.export_fixture(dict(spec['subject'], schema_version=1), spec['inputs'])
        legacy = self.assign_turn('closure-1')
        plan = self.plan_final(legacy)
        step = plan['details']['step']['then'][-1]
        identifier = step['payload'].get('id', 'separate-checkpoint')
        self.review['reviewer']['attestation_id'] = 'review-assignment:' + identifier
        self.export_fixture(self.subject, [self.supplementary])
        choices = {'id': identifier, 'route_id': 'route', 'context.native_packet': self.exported['native_packet']}
        payload = self.fill_step(step, {key: choices[key] for key in step['chooses']})
        revision = self.store.revision
        with self.assertRaises(ResearchError) as check:
            check_mutation(self.store, protocol.record_assignment, payload, expected_revision=revision,
                           request_id='bad-link-check', bounded_payload=payload)
        with self.assertRaises(ResearchError) as write:
            protocol.record_assignment(self.store, payload, expected_revision=revision, request_id='bad-link-write')
        self.assertEqual((check.exception.code, write.exception.code), ('invalid_review_context', 'invalid_review_context'))
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(plan, self.plan_final(legacy))
        self.start_fresh_review(legacy, plan, inputs=spec['inputs'])

    def test_fresh_handoff_executes_the_check_child_it_returns(self):
        self.build_controller_math_fixture()
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final, decision='revise'))
        plan = self.plan_final(final)
        plan['details']['step']['then'][0]['payload']['role'] = 'invalid-role'
        with self.assertRaises(ResearchError) as refused:
            self.start_fresh_review(final, plan, inputs=[self.supplementary])
        self.assertEqual(refused.exception.code, 'invalid_review_role')
        self.assertEqual(self.plan_final(final)['state'], 'handoff')

    def test_relocated_accepted_objective_never_targets_the_original(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.cli import status_report
        from research_harness.storage import Store

        self.build_controller_math_fixture()
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final))
        self.accept_checkpoint_from_plan(final)
        self.assertEqual(self.plan_final(final)['state'], 'done')
        destination = self.root.parent / 'accepted-copy'
        shutil.copytree(self.root, destination)
        clone = Store(destination)
        plan = plan_next(clone.snapshot()['records'], ArtifactStore(destination), final['id'])
        self.assertEqual((plan['state'], plan['code']), ('stop', 'native_review_objective_unavailable'))
        step = plan['details']['step']
        self.assertEqual(step, dict(command='status', payload={}, chooses=[]))
        self.assertEqual(status_report(clone)['revision'], clone.revision)
        self.assertEqual(self.plan_final(final)['state'], 'done')

    def test_unreadable_native_log_cannot_authorize_recording(self):
        self.build_controller_math_fixture()
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final))
        path = self.root / '.search' / 'tree.json'
        saved = self.root.parent / 'withheld-native-tree.json'
        path.rename(saved)
        try:
            plan = self.plan_final(final)
            self.assertEqual((plan['state'], plan['code'], plan['command']),
                             ('stop', 'native_review_objective_unavailable', 'status'))
            self.assertNotIn('root', plan['details']['step'])
        finally:
            saved.rename(path)
        self.accept_checkpoint_from_plan(final)
        self.assertEqual(self.plan_final(final)['state'], 'done')

    def test_revised_checkpoint_handoff_allows_a_new_subject_and_refused_first_turn(self):
        self.build_controller_math_fixture()
        final = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(final, decision='revise'))
        plan = self.plan_final(final)
        self.controller.command('checkpoint', self.external_checkpoint(),
                                self.controller.status()['revision'], 'revised-checkpoint')
        subject = self.controller.status()['checkpoints']['checkpoint-000002']
        fresh = self.start_fresh_review(final, plan, inputs=[self.supplementary], revised=dict(subject=subject))
        self.assertIn('output_refusal', self.run_planned_turn(fresh, invalid=True))
        terminal = self.planned_final(self.correct_final(fresh))
        self.assertNotIn('output_refusal', self.run_planned_turn(terminal))
        step = self.plan_final(terminal)['details']['step']
        available = dict(self.build_acceptance(), target=subject['id'])
        self.execute_native_step(step, {key: available[key] for key in step['chooses']}, 'accept-revised-checkpoint')
        accepted = self.controller.status()['acceptances']['acceptance-000001']
        self.assertEqual(accepted['checkpoint_id'], subject['id'])
        self.assertEqual(self.plan_final(terminal)['state'], 'done')

    def test_first_and_final_refusals_can_both_be_replaced(self):
        self.build_controller_math_fixture()
        self.assert_terminal_history('checkpoint', 'both-refused', 'current', 'approve')

    def test_two_distinct_approvals_can_both_record(self):
        self.build_controller_math_fixture()
        first = self.planned_final(self.assign_turn('turn-1'))
        self.assertNotIn('output_refusal', self.run_planned_turn(first))
        self.review['reviewer']['attestation_id'] = 'review-assignment:secondary'
        self.export_fixture(self.subject, [self.supplementary])
        second = self.planned_final(self.assign_turn('secondary'))
        self.assertNotIn('output_refusal', self.run_planned_turn(second))
        for final in (first, second):
            self.accept_checkpoint_from_plan(final)
            self.assertEqual(self.plan_final(final)['state'], 'done')
        self.assertEqual(len(self.controller.status()['acceptances']), 2)


class NativePartsV3ControllerTests(MathOpennessChainCase, unittest.TestCase):
    build_native_fixture = native_parts_fixtures.NativePartsAcceptanceTests.build_native_fixture
    check_review = native_parts_fixtures.NativePartsAcceptanceTests.check_review

    def setUp(self):
        super().setUp()
        self.start_patch(patch.object(review_parts, 'NEW_SERIES_PROTOCOLS',
            (review_parts_v3.PROTOCOL, review_parts.PROTOCOL)))

    def finish_controller_parts(self, *, paged):
        with patch.object(openness_chain_support, 'record_codex_route', side_effect=lambda case:
                record_codex_route(case, output_tokens=8000 if paged else 128000, model='gpt-6-astra')):
            self.build_native_fixture()
        assignment = self.assign_turn('turn-1')
        identifiers, packets = [], []
        for number in range(100):
            packet = self.read_turn(assignment['id'])
            header = packet[review_parts.KEY]
            packets.append(packet)
            identifiers.append(assignment['id'])
            self.assertEqual(header['protocol'], review_parts_v3.PROTOCOL)
            self.assertNotIn('receipts', header)
            self.assertNotIn('openness_minimum', packet['native']['scientific_context'])
            if header['kind'] != 'final':
                output = make_turn_output(packet)
            elif 'page_plan_digest' in header:
                logical = dict(review_parts.read_logical_packet(self.artifacts, header), review_parts=header)
                rule = review_composition.find_rule('native_math', logical)
                reference = json.loads(self.artifacts.read(header['final_page_plan']))
                page_plan = json.loads(self.artifacts.read(reference['artifact']))
                targets = [row['targets'] for row in page_plan['turns'] if row['kind'] in ('page', 'final')]
                pages, closing = review_composition.split_output(rule, logical, self.review, targets)
                output = closing if header['final_form'] == 'closing' else next(
                    row['output'] for row in pages if row['targets'] == header['page']['targets'])
            else:
                output = self.review
            result = self.invoke(assignment['id'], output)
            self.assertNotIn('output_refusal', result, result)
            if header['kind'] == 'final' and header.get('final_form') != 'page':
                records = self.store.snapshot()['records']
                self.review, composite = review_composite.read_final_output(records, self.artifacts, assignment)
                return assignment, identifiers, packets, composite
            assignment = self.assign_turn('turn-' + str(number + 2), assignment['id'])
        self.fail('The native parts series did not reach its final.')

    def assert_recorded_parts_review(self, assignment, identifiers, composite):
        records = self.store.snapshot()['records']
        verified = review_parts.verify_series(records, self.artifacts, assignment['id'])
        self.assertTrue(verified['complete'], verified['issues'])
        checked = self.check_review()
        self.assertEqual([row['assignment_id'] for row in checked['reading_turns']], identifiers)
        self.assertEqual([row['attempt_id'] for row in checked['reading_turns']],
                         ['attempt-' + identity for identity in identifiers])
        self.assertTrue(all(row['delivered_units'] == [] for row in checked['reading_turns']))
        self.assertEqual(composite is not None, 'composite' in checked)
        self.accept_result()
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        repeated = self.check_review(record=accepted)
        self.assertEqual(repeated['reading_turns'], checked['reading_turns'])
        self.assertEqual(repeated.get('composite'), checked.get('composite'))

    def test_native_parts_v3_request_final_accepts_and_rechecks_state_receipts(self):
        assignment, identifiers, packets, composite = self.finish_controller_parts(paged=False)
        self.assertIsNone(composite)
        self.assertFalse(any(packet[review_parts.KEY].get('final_form') == 'page' for packet in packets))
        self.assert_recorded_parts_review(assignment, identifiers, composite)

    def test_native_parts_v3_paged_final_accepts_and_rechecks_state_receipts(self):
        assignment, identifiers, packets, composite = self.finish_controller_parts(paged=True)
        self.assertIsNotNone(composite)
        self.assertGreater(sum(packet[review_parts.KEY].get('final_form') == 'page' for packet in packets), 1)
        self.assert_recorded_parts_review(assignment, identifiers, composite)

    def test_stored_parts_v2_acceptance_keeps_output_receipts_and_digests_after_v3_activation(self):
        with patch.object(review_parts, 'NEW_SERIES_PROTOCOLS', (review_parts.PROTOCOL,)):
            self.build_native_fixture()
            identifiers = native_parts_fixtures.NativePartsAcceptanceTests.finish_native_series(self)
            checked = self.check_review()
            self.accept_result()
        before = copy.deepcopy(self.store.snapshot()['records'])
        accepted = self.controller.status(full_audit=True)['acceptances']['acceptance-000001']
        repeated = self.check_review(record=accepted)
        self.assertEqual(repeated['output'], self.review)
        self.assertEqual(repeated['output_digest'], checked['output_digest'])
        self.assertEqual(repeated['reading_turns'], checked['reading_turns'])
        self.assertEqual([row['assignment_id'] for row in repeated['reading_turns']], identifiers)
        self.assertEqual(self.store.snapshot()['records'], before)
