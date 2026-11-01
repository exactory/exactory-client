"""Only continuation of the recorded legacy series retains its v1 planner."""

import json
import unittest
from unittest.mock import patch

from problem_parts_fixtures import KEY, ResultPartsCase
from research_harness import review_problem_parts as parts, review_protocol as protocol
from research_harness import review_staged, review_transport as transport
from research_harness.evidence import digest


class ProblemResultPartsEntryTests(ResultPartsCase):
    staged_entry = True

    def make_legacy_first(self):
        self.oversized()
        with patch.object(parts, 'allows_staged_entry', return_value=False):
            first = self.assign('legacy-first')
        self.invoke(first)
        return first

    def make_legacy_final(self):
        self.oversized()
        with patch.object(parts, 'allows_staged_entry', return_value=False):
            _, final = self.finish_parts()
        self.record_final(final)
        return final

    def prepare_successor(self, prior, refresh_marker=False):
        records, artifacts = self.case.read_records(), self.case.artifacts
        with transport.text_only_delivery(True):
            packet = protocol.build_packet(records, artifacts, None, 'result', 'first')
        if refresh_marker:
            packet['problem_refresh'] = {'assignment_id': 'earlier-recorded-final'}
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation')
        value = {'id': 'successor', 'role': 'result', 'context': {'prior_assignment_id': prior['id']}}
        prepared = parts.prepare_packet(records, artifacts, value, records['review_route']['route'],
                                        packet, prompt, [])
        return packet, prepared

    def assert_staged(self, packet):
        self.assertNotIn(KEY, set(packet))
        self.assertEqual(packet.get('staged_review'),
                         {'protocol': review_staged.PROTOCOL, 'kind': 'controller'})

    def test_changed_digest_starts_staged_instead_of_a_new_v1_series(self):
        first = self.make_legacy_first()
        self.case.expected['openness_searches'][0]['search']['reason'] += ' New evidence.'
        original, prepared = self.prepare_successor(first)
        self.assertNotEqual(original['problem_evidence_digest'], self.packet(first)['problem_evidence_digest'])
        self.assert_staged(prepared)

    def test_changed_digest_that_fits_preserves_the_complete_ordinary_packet(self):
        first = self.make_legacy_first()
        self.case.expected['openness_searches'] = [
            {'search': {'id': 'small-search', 'reason': 'New evidence.'}, 'sources': []}]
        original, prepared = self.prepare_successor(first)
        self.assertTrue(prepared is original, 'A fitting new series must retain the complete ordinary packet')
        self.assertNotIn(KEY, set(prepared))
        self.assertNotIn('staged_review', set(prepared))

    def test_recorded_final_refresh_starts_staged_even_with_the_same_digest(self):
        final = self.make_legacy_final()
        original, prepared = self.prepare_successor(final)
        self.assertEqual(original['problem_evidence_digest'], self.packet(final)['problem_evidence_digest'])
        self.assert_staged(prepared)

    def test_recorded_final_refresh_that_fits_preserves_the_complete_ordinary_packet(self):
        final = self.make_legacy_final()
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1000000):
            original, prepared = self.prepare_successor(final)
        self.assertTrue(prepared is original, 'A fitting refresh must retain the complete ordinary packet')
        self.assertNotIn(KEY, set(prepared))
        self.assertNotIn('staged_review', set(prepared))

    def test_same_recorded_series_continues_v1_despite_an_inherited_refresh_marker(self):
        first = self.make_legacy_first()
        _, prepared = self.prepare_successor(first, refresh_marker=True)
        self.assertNotIn('staged_review', set(prepared))
        plan = prepared[KEY]
        self.assertEqual((plan['protocol'], plan['series_assignment_id'], plan['stage'], plan['turn']),
                         ('exactory-problem-evidence-parts-v1', first['id'], 'part', 2))
        self.assertEqual([row['assignment_id'] for row in plan['delivered_before']], [first['id']])

    def test_visual_result_and_focused_work_can_enter_staged(self):
        packet = {'subject': {'kind': 'problem'}}
        route = {'adapter': 'codex_cli_visual_v1'}
        for role in ('result', 'adjudicator'):
            with self.subTest(role=role):
                value = {'role': role, 'context': {}}
                self.assertTrue(parts.allows_staged_entry(value, route, packet))

    def test_visual_entry_uses_the_native_text_limit_when_the_legacy_limit_is_absent(self):
        from research_harness import review_codex_visual_transport as visual, review_source_complete
        from research_harness import review_pdf_pages, review_visual_sources
        records = self.case.read_records()
        route = dict(records['review_route']['route'], adapter='codex_cli_visual_v1')
        value = {'id': 'visual', 'role': 'adjudicator', 'context': {}}
        prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation')
        for size in (100, 200000):
            packet = {'subject': {'kind': 'problem'}, 'role': 'adjudicator', 'evidence': [], 'reason': 'x' * size,
                      review_source_complete.MARKER: dict(protocol=review_source_complete.PROTOCOL,
                          history_digest=digest([]), sources=[], alternate_body_sha256=[],
                          protected_sha256=[], restored_evidence=[]),
                      review_visual_sources.MARKER: dict(protocol=review_visual_sources.PROTOCOL,
                          render_profile=review_pdf_pages.profile(), sources=[], pages=[], readings=[])}
            with self.subTest(size=size), patch.object(visual, 'MAX_USER_TEXT', 50000):
                prepared = parts.prepare_packet(records, self.case.artifacts, value, route, packet, prompt, [])
                if size == 100:
                    self.assertTrue(prepared is packet, 'The complete fitting native packet stays ordinary')
                else:
                    self.assert_staged(prepared)


class NativeOrdinaryEntryTests(unittest.TestCase):
    def test_complete_fitting_native_repair_stays_an_ordinary_visual_assignment(self):
        import test_visual_context_repair as fixtures
        fixture = fixtures.VisualContextRepairTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        assignment = fixture.assign()
        self.assertNotIn('staged_review', set(assignment))
        packet = json.loads(fixture.artifacts.read(assignment['packet']))
        request = protocol.build_assignment_request(fixture.case.read_records(), fixture.artifacts, assignment)
        self.assertNotIn('staged_review', set(packet))
        self.assertTrue(request['exactory_image_inputs'])
        self.assertEqual(assignment['history'], [])
