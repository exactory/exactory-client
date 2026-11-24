"""Property coverage for parts v3 with a reviewer at the transport boundary."""

import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import property_stub_a as stub
import test_review_parts_native as fixtures
from research_harness import bar_sources, review_parts, review_parts_v3 as parts, review_protocol as protocol
from research_harness import review_route_capacity as capacity
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from test_review_output_profile import build_row


class PartsDriver:
    def setUp(self):
        self.enter_patch(patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,)))
        self.select_parts_v3()
        planner = capacity.plan_chain_capacity

        def request_plan(*args, **kwargs):
            kwargs['final_pages'] = False
            return planner(*args, **kwargs)

        self.enter_patch(patch.object(capacity, 'plan_chain_capacity', side_effect=request_plan))

    def enter_patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def select_parts_v3(self):
        """Put parts v3 first. A fixture of a released series states parts v2 in its own setUp, so a driver states its
        order again after it."""
        self.enter_patch(patch.object(review_parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL, review_parts.PROTOCOL)))

    def open_series(self, *, grouped=False, identifier='first'):
        case = fixtures.NativePartsTests()
        self.addCleanup(case.doCleanups)
        if grouped:
            record_route = fixtures.record_codex_route
            with patch.object(fixtures, 'record_codex_route',
                              side_effect=lambda case, **options: record_route(case, output_tokens=128000)):
                case.setUp()
            self.enter_patch(patch.object(capacity, 'read_profile_priors', return_value=[
                dict(build_row(level=None, kind='read', thinking=1), largest_output_tokens=12)]))
        else:
            case.setUp()
        self.select_parts_v3()
        if not grouped:
            for index, row in enumerate(case.native['evidence']):
                ref = case.artifacts.put(('Exact argument %d.\n' % index * 700).encode(), 'text/plain')
                row.update(digest=ref['sha256'], artifact=ref)
        case.native['reviewer']['attestation_id'] = 'review-assignment:' + identifier
        case.reference = case.artifacts.put(json.dumps(case.native).encode(), 'application/json')
        assignment = case.assign(identifier)
        header = case.read_packet(assignment)['review_parts']
        self.assertEqual((header['protocol'], header['kind']), (parts.PROTOCOL, 'read'))
        return case, assignment

    def continue_series(self, case, assignment):
        records = case.store.snapshot()['records']
        action = plan_next(records, case.artifacts, assignment['id'])
        self.assertEqual((action['state'], action['command']), ('continue', 'review-assignment'),
                         (action['state'], action.get('command'), action.get('code')))
        return protocol.record_assignment(case.store, action['payload'],
            expected_revision=case.store.revision, request_id='advance-' + assignment['id'])['result']

    def drive_to_final(self, case, assignment):
        for _ in range(30):
            if case.read_packet(assignment)['review_parts']['kind'] == 'final':
                return assignment
            stub.invoke_prompt(self, case, assignment, stub.build_series_turn)
            assignment = self.continue_series(case, assignment)
        self.fail('The finite series did not reach a final')

    def verify_final(self, case, assignment):
        records = case.store.snapshot()['records']
        result = review_parts.verify_series(records, case.artifacts, assignment['id'])
        self.assertTrue(result['complete'], result['issues'])
        attempt, output = protocol.read_assignment_output(records, case.artifacts, assignment['id'])
        self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, assignment, attempt))
        self.assertTrue(output['findings']['openness']['evidence'])
        return attempt

    def finish_native(self, case, assignment):
        final = self.drive_to_final(case, assignment)
        stub.invoke_prompt(self, case, final, stub.request_first_original)
        final = self.continue_series(case, final)
        stub.invoke_prompt(self, case, final, stub.build_native_final)
        self.verify_final(case, final)
        return final


class PartsProperties(PartsDriver, unittest.TestCase):
    def test_over_bound_final_is_recorded_and_authenticates(self):
        case, first = self.open_series()
        final = self.drive_to_final(case, first)
        stub.invoke_prompt(self, case, final, stub.request_first_original)
        final = self.continue_series(case, final)

        def answer(prompt):
            output = stub.build_native_final(prompt)
            return stub.size_answer(output, output['findings']['openness'], 'reason',
                                    2 * stub.read_answer_bound(prompt))

        stub.invoke_prompt(self, case, final, answer)
        attempt = self.verify_final(case, final)
        bound = case.read_packet(final)['review_parts']['capacity']['answer_bound']
        self.assertEqual(attempt['answer']['characters'], 2 * bound)

    def test_failed_attempts_reach_the_position_stop(self):
        case, first = self.open_series(identifier='s' * 60)
        stub.exhaust_attempts(self, case, first, floor=True)


    def test_d31_group_credit(self):
        """Two groups in one read distinguish delivered text from credit for a card."""
        case, first = self.open_series(grouped=True)
        packet = case.read_packet(first)
        self.assertGreaterEqual(len(packet['review_parts_groups']), 2)
        stub.invoke_prompt(self, case, first, stub.build_cross_group_answer)
        final = self.finish_native(case, self.continue_series(case, first))
        records = case.store.snapshot()['records']
        observations = parts.observe_series(records, case.artifacts, final['id'])
        card = observations[0]['delta']['cards'][0]
        self.assertEqual(card['statements'], [])
        self.assertEqual(card['unverified_statements'], 1)

    def test_d31_card_refusal(self):
        """One grouped read supplies distinct cards for omission, duplication and changed boundaries."""
        case, first = self.open_series(grouped=True)
        records = case.store.snapshot()['records']
        request = protocol.build_assignment_request(records, case.artifacts, first)
        prompt = stub.read_prompt(request)
        self.assertGreaterEqual(len(prompt['review_parts_groups']), 2)
        for variant in ('missing', 'duplicate', 'first', 'last', 'unit_count'):
            with self.subTest(variant=variant):
                with self.assertRaises(ResearchError) as refused:
                    parts.validate_turn_output(records, case.artifacts, first, prompt,
                                               stub.build_broken_cards(prompt, variant))
                self.assertEqual(refused.exception.code, 'invalid_value_review')
        self.finish_native(case, first)

    def test_d31_uncarded_citation(self):
        case, first = self.open_series()
        final = self.finish_native(case, first)
        records = case.store.snapshot()['records']
        observations = parts.observe_series(records, case.artifacts, final['id'])
        cards = [row for turn in observations for row in turn['delta']['cards']]
        self.assertTrue(cards)
        self.assertFalse(any(row['statements'] for row in cards))
        _, output = protocol.read_assignment_output(records, case.artifacts, final['id'])
        self.assertEqual(len(output['findings']['openness']['evidence']), 1)

    def test_d31_middle_unit_change(self):
        """Three units under one card let the middle change with identical group boundaries."""
        case, first = self.open_series()
        final = self.finish_native(case, first)
        records = case.store.snapshot()['records']
        for assignment, packet in parts.list_series(records, case.artifacts, final['id']):
            groups = packet.get('review_parts_groups', [])
            material = packet.get('review_parts_material', [])
            if len(groups) == 1 and groups[0]['unit_count'] >= 3:
                break
        else:
            self.fail('No group contains a middle unit')
        changed = deepcopy(packet)
        texts = [index for index, item in enumerate(material) if isinstance(item, str)]
        index = texts[len(texts) // 2]
        text = material[index]
        changed['review_parts_material'][index] = ('z' if text[0] != 'z' else 'y') + text[1:]
        changed['digest'] = digest({key: value for key, value in changed.items() if key != 'digest'})
        self.assertEqual(changed['review_parts_groups'], groups)
        with self.assertRaises(ResearchError) as refused:
            parts.validate_turn_output(records, case.artifacts, assignment, changed, stub.build_series_turn(changed))
        self.assertEqual(refused.exception.code, 'review_parts_delivery_invalid')
        tampered = records['review_assignment'][assignment['id']]
        tampered['packet'] = parts._put_value(case.artifacts, changed)
        tampered['packet_digest'] = changed['digest']
        self.assertFalse(review_parts.verify_series(records, case.artifacts, final['id'])['complete'])
