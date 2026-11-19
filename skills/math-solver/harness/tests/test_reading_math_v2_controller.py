"""Real native controller consumers of reading and parts composite finals."""

import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import limit_codex_input, record_codex_route
from research_harness import native_review, openness_reading as reading, openness_successor
from research_harness import review_composition, review_composite
from research_harness import review_parts, review_parts_v3, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
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

    def build_controller_math_fixture(self, *, neighbours=0, output_tokens=128000):
        with patch.object(openness_chain_support, 'record_codex_route', side_effect=lambda case:
                record_codex_route(case, output_tokens=output_tokens, model='gpt-6-astra')):
            self.build_fixture(response_sizes=(90000,), text_sizes=(50000,))
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
                        objections=['The endpoint case needs review.'])
        output = copy.deepcopy(self.review)
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
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
        assignment, packets, identifiers, composite = self.finish_controller_math_chain()
        self.assertIsNone(composite)
        registry = read_packet_registry()
        for packet in packets:
            self.assertEqual(validate_packet('native_math', packet, registry), [])
        self.assertTrue(reading.verify_chain(self.store.snapshot()['records'], self.artifacts, assignment))
        self.assert_controller_acceptance(native, packets, identifiers, composite)

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
        with self.assertRaises(SearchError):
            self.accept_result()
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
