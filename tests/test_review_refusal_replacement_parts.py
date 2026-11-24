"""Replacements preserve every stage of a real fresh-input series."""

from unittest.mock import patch
import copy
import unittest

import review_parts_fixtures as fixtures
from research_harness import research_decisions as decisions, review_protocol as protocol, review_parts as parts
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class PartsReplacementTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_every_parts_kind_can_be_replaced_and_the_corrected_series_records(self):
        fixture = fixtures.PartsResultCase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.oversized(count=4, characters=24000, room=40000)
        assignment = fixture.assign()
        seen = set()
        for number in range(20):
            packet = fixture.packet(assignment)
            kind = parts.find_kind(packet)
            if kind == 'final':
                output = fixture.final_output()
            else:
                output = fixtures.make_output(packet)
            if kind == 'combine':
                cards = packet[parts.STATE_KEY]['cards']
                handles = [card['unit'] for card in cards[:2]]
                output['combinations'] = [dict(units=handles, question='Do these records agree?',
                    view='They support the same bounded conclusion.', read=[dict(unit=handles[0])])]
            if kind not in seen:
                # Missing the stage field produces a real stored contract refusal.
                bad = copy.deepcopy(output)
                bad.pop('stage' if kind == 'final' else 'kind')
                fixture.invoke(assignment, bad)
                records = fixture.case.read_records()
                self.assertIn('attempt-' + assignment['id'], records['review_output_refusal'])
                context = copy.deepcopy(assignment['context'])
                context['output_refusal'] = {'assignment_id': assignment['id']}
                from codex_route_fixtures import limit_codex_input
                limit_codex_input(fixture, protocol.transport.local_input_limit(
                    fixture.case.read_records()['review_route']['route']) + 15000)
                replacement = fixture.case.assign('result', 'first', identifier='replacement-' + str(number), **context)
                self.assertEqual(fixture.packet(replacement)[parts.KEY], packet[parts.KEY])
                self.assertEqual(replacement['prompt']['template_id'], assignment['prompt']['template_id'])
                self.assertEqual(protocol.read_history(fixture.case.artifacts, replacement), [])
                from research_harness.review_refusal_replacement import validate_replacement_origin
                validate_replacement_origin(fixture.case.read_records(), fixture.case.artifacts, replacement)
                self.assertTrue(parts._list_series(fixture.case.read_records(), fixture.case.artifacts, replacement['id']))
                assignment = replacement
                seen.add(kind)
            fixture.invoke(assignment, output, attempt='good-' + assignment['id'])
            if kind in seen:
                from research_harness.review_delivered_evidence import read_chain_delivery
                records = fixture.case.read_records()
                delivered = read_chain_delivery(records, fixture.case.artifacts, assignment,
                    attempt=records['review_attempt']['good-' + assignment['id']])
                self.assertIn(packet['digest'], [row['digest'] for row in delivered['packets']])
            if kind == 'final':
                fixture.case.mutate(decisions.record_value_review,
                    dict(output, id='final-record', dossier_id=None, assignment_id=assignment['id']))
                self.assertTrue(parts.verify_series(fixture.case.read_records(), fixture.case.artifacts, assignment['id'])['complete'])
                break
            assignment = fixture.assign('turn-' + str(number + 2), assignment['id'])
        self.assertEqual(seen, {'read', 'combine', 'check', 'final'})

    def test_ordinary_focused_adjudication_replaces_its_refusal_and_records(self):
        import test_review_parts_adjudication_journey as focused_fixtures
        fixture = focused_fixtures.AdjudicationPartsCase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        assignment = fixture.assign()
        good = fixture.final_output()
        bad = dict(good, disposition='invalid')
        fixture.invoke(assignment, bad)
        with self.assertRaises(ResearchError):
            fixture.case.mutate(protocol.record_adjudication,
                dict(bad, id='unrecorded', assignment_id=assignment['id']))
        replacement = fixture.assign(identifier='replacement', output_refusal={'assignment_id': assignment['id']})
        fixture.invoke(replacement, good)
        records = fixture.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, fixture.case.artifacts, replacement,
                        records['review_attempt']['attempt-replacement']))
        from research_harness.review_delivered_evidence import read_chain_delivery
        delivered = read_chain_delivery(records, fixture.case.artifacts, replacement,
                        attempt=records['review_attempt']['attempt-replacement'])
        self.assertIn(assignment['packet_digest'], [row['digest'] for row in delivered['packets']])
        fixture.case.mutate(protocol.record_adjudication,
                dict(good, id='recorded', assignment_id=replacement['id']))

    def test_legacy_parts_continue_with_the_common_version_of_their_chain(self):
        import problem_parts_fixtures as legacy_fixtures
        from unittest.mock import patch
        fixture = legacy_fixtures.ResultPartsCase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.oversized()
        with patch.object(protocol, 'COMMON_VERSION', 3):
            first = fixture.assign()
        fixture.invoke(first)
        continued = fixture.assign('continued', first['id'])
        self.assertEqual(protocol.COMMON_VERSION, 4)
        self.assertEqual(continued['prompt']['parameters']['common_version'], 3)

    def test_v6_reading_continues_with_its_stored_prompt_version_and_cannot_carry_refusal(self):
        import test_openness_v6_chain as reading_fixtures
        from unittest.mock import patch
        fixture = reading_fixtures.V6ChainTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        native = fixture.export()
        with patch.object(protocol, 'COMMON_VERSION', 3):
            first = fixture.assign('turn-1', native)
        fixture.invoke(first['id'], fixture.build_reading_response(fixture.read_packet(first['id']), excerpts=[]))
        continued = fixture.assign('turn-2', native, first['id'])
        self.assertEqual(protocol.COMMON_VERSION, 4)
        self.assertEqual(continued['prompt']['parameters']['common_version'], 3)
        payload = {key: copy.deepcopy(continued[key]) for key in
                   ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
        payload['id'] = 'refusal-replacement'
        payload['context']['output_refusal'] = {'assignment_id': continued['id']}
        with self.assertRaises(ResearchError) as caught:
            fixture.mutate(protocol.record_assignment, payload)
        self.assertEqual(caught.exception.code, 'review_refusal_replacement_ineligible')

    def test_native_parts_replacement_and_next_turn_preserve_the_implicit_slot(self):
        import test_review_parts_native as native_fixtures
        from codex_route_fixtures import invoke_codex, limit_codex_input
        fixture = native_fixtures.NativePartsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.limit_input()
        limit_codex_input(fixture, 45000)
        first = fixture.assign()
        invoke_codex(fixture, first['id'], fixtures.make_output(fixture.read_packet(first)))
        second = fixture.assign('second', first['id'])
        output = fixtures.make_output(fixture.read_packet(second))
        bad = copy.deepcopy(output)
        bad.pop('kind')
        invoke_codex(fixture, second['id'], bad)
        context = copy.deepcopy(second['context'])
        context['output_refusal'] = {'assignment_id': second['id']}
        route = fixture.store.snapshot()['records']['review_route']['route']
        limit_codex_input(fixture, protocol.transport.local_input_limit(route) + 15000)
        replacement = protocol.record_assignment(fixture.store, dict(id='replacement', role='native_math',
            route_id='route', dossier_id=None, reviewer_id='reviewer', author_id='author', context=context),
            expected_revision=fixture.store.revision, request_id='replace-native')['result']
        invoke_codex(fixture, replacement['id'], output)
        records = fixture.store.snapshot()['records']
        self.assertEqual(len(parts.observe_series(records, fixture.artifacts, replacement['id'])), 2)
        continued = fixture.assign('continued', replacement['id'])
        self.assertEqual(fixture.read_packet(continued)['review_parts']['turn'], 3)
        self.assertEqual(protocol.context_repair.slot_id(fixture.store.snapshot()['records'], continued), first['id'])

    def test_verification_parts_initial_replacement_keeps_its_slot_and_old_head_is_refused(self):
        import test_review_parts_study as study_fixtures
        from codex_route_fixtures import limit_codex_input
        from unittest.mock import patch
        from research_harness import review_refusal
        fixture = study_fixtures.VerificationPartsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.enlarge_verification()
        limit_codex_input(fixture, 80000)
        first = fixture.assign()
        output = fixtures.make_output(fixture.packet(first))
        bad = copy.deepcopy(output)
        bad.pop('kind')
        fixture.invoke(first, bad)
        context = dict(first['context'], output_refusal={'assignment_id': first['id']})
        limit_codex_input(fixture, 95000)
        replacement = fixture.assign('replacement', context=context)
        fixture.invoke(replacement, output)
        self.assertEqual(len(parts.observe_series(fixture.records(), fixture.case.artifacts, replacement['id'])), 1)
        continued = fixture.assign('continued', replacement['id'])
        self.assertEqual(fixture.packet(continued)['review_parts']['turn'], 2)
        with patch.object(review_refusal, 'classify_observed_output', return_value=None):
            with self.assertRaises(ResearchError) as caught:
                fixture.assign('reopen', first['id'])
            self.assertEqual(caught.exception.code, 'review_assignment_replaced')
