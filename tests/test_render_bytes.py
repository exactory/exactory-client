"""Stored parts series render the integration base's bytes with one listing per verification."""

from collections import Counter
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from research_harness import (bar_sources, output_views, review_output_correction, review_parts as parts, review_parts_v3,
                              review_protocol, review_route_capacity)
from research_harness.evidence import digest
from render_reference import read_render_outcome, use_reference_functions
from review_parts_fixtures import PartsResultCase as PartsFixture, make_output
import test_research_output_views as views
import test_review_parts_repair_journey as repair
import test_review_parts_v3 as v3_fixtures


def render_with_list_counts(store, *, reference=False):
    with use_reference_functions() if reference else nullcontext():
        verify = parts.verify_series
        counts = []

        def count_lists(records, artifacts, identifier):
            with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
                result = verify(records, artifacts, identifier)
            counts.append((identifier, listing.call_count))
            return result

        with patch.object(parts, 'verify_series', side_effect=count_lists):
            summary = output_views.render_all(store)
        return summary, counts


class RenderComparison:
    def setUp(self):
        select_bar = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        select_bar.start()
        self.addCleanup(select_bar.stop)
        super().setUp()

    def compare_renders(self, *, missing=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = views.copy_store(self.case.root, root / 'current')
            reference = views.copy_store(self.case.root, root / 'reference')
            current_summary, current_counts = render_with_list_counts(current)
            base_summary, base_counts = render_with_list_counts(reference, reference=True)
            self.assertEqual(current_summary, base_summary)
            self.assertEqual(current_summary['errors'], 0)
            self.assertEqual(output_views.read_rendered_revision(current), self.case.store.revision)
            result = read_render_outcome(current.root)
            self.assertEqual(result, read_render_outcome(reference.root))
            with self.subTest(check='list calls'):
                assignments = {identifier: assignment[parts.KEY]['protocol'] for identifier, assignment in
                               self.case.read_records()['review_assignment'].items() if parts.KEY in assignment}
                self.assertTrue(assignments)
                for counts, expected in ((base_counts, 2), (current_counts, 1)):
                    expected_counts = [(identifier, 0 if protocol == review_parts_v3.PROTOCOL
                                        else 1 if identifier in missing else expected)
                                       for identifier, protocol in assignments.items()]
                    self.assertEqual(Counter(counts), Counter(expected_counts))
            return result[0]


class RenderBytesTests(RenderComparison, views.PartsViewCase):
    def setUp(self):
        select_legacy = patch.object(parts, 'NEW_SERIES_PROTOCOLS', (parts.PROTOCOL,))
        select_legacy.start()
        self.addCleanup(select_legacy.stop)
        super().setUp()

    def start_series(self):
        self.prepare_parts(count=3)
        return self.assign()

    def start_check(self, *, compare_turns=False):
        assignment = self.start_series()
        while self.packet(assignment)[parts.KEY]['kind'] == 'read':
            self.invoke(assignment)
            if compare_turns:
                with self.subTest(turn=assignment['id']):
                    self.compare_renders()
            assignment = self.assign('next-' + assignment['id'], assignment['id'])
        packet = self.packet(assignment)
        self.assertEqual(packet[parts.KEY]['kind'], 'combine')
        combination = dict(units=['u1'], question='What does the original support?',
                           view='Read its opening.', read=[dict(unit='u1', item=True, start=0, end=100)])
        self.invoke(assignment, make_output(packet, [combination]))
        if compare_turns:
            with self.subTest(turn=assignment['id']):
                self.compare_renders()
        check = self.assign('check', assignment['id'])
        self.assertEqual(self.packet(check)[parts.KEY]['kind'], 'check')
        return check

    def test_read_combine_check_and_final_keep_their_bytes(self):
        """Render each completed turn to compare the bytes of every prefix."""
        check = self.start_check(compare_turns=True)
        self.invoke(check)
        with self.subTest(turn=check['id']):
            self.compare_renders()
        final = self.assign('final', check['id'])
        self.record_final(final)
        with self.subTest(turn=final['id']):
            self.compare_renders()

    def test_check_keeps_a_match_and_an_unmatched_statement_on_the_same_unit(self):
        check = self.start_check()
        packet = self.packet(check)
        output = make_output(packet)
        excerpt = next(entry['content'] for entry in packet['evidence'] if entry.get('unit') == 'u1')
        output['findings'][0]['statements'] = [dict(unit='u1', text=text, why='Compare the original.')
                                              for text in (excerpt[:32], 'absent quotation')]
        result = self.invoke(check, output)
        self.assertNotIn('output_refusal', result)
        records = self.case.read_records()
        observation = parts.observe_series(records, self.case.artifacts, check['id'])[-1]
        finding = observation['delta']['findings'][0]
        self.assertEqual((len(finding['statements']), finding['unverified_statements']), (1, 1))
        self.compare_renders()

    def test_refused_raw_final_and_accepted_correction_keep_their_bytes(self):
        first = self.start_series()
        _, final = self.finish_series(first)
        corrected = self.final_output()
        self.invoke(final, dict(stage='result'), attempt='raw')
        records = self.case.read_records()
        self.assertIn('raw', records.get('review_output_refusal', {}))
        invocation = dict(id='fix', assignment_id=final['id'], diagnostic=dict(attempt_id='raw'),
                          recorded_revision=self.case.store.revision + 1)
        correction = dict(invocation, status='completed', correction_status='accepted',
                          output=review_protocol._put_json(self.case.artifacts, dict(findings=[])),
                          corrected_output=review_protocol._put_json(self.case.artifacts, corrected))
        self.case.put('review_output_correction_invocation', 'fix', invocation)
        self.case.put('review_output_correction', 'fix', correction)

        select = review_output_correction.selected_correction

        def select_observed_correction(records, artifacts, identifier):
            if identifier != final['id']:
                return select(records, artifacts, identifier)
            return dict(id='fix', attempt_id='raw', output=corrected, route_id=final['route_id'],
                        source_raw_output_digest=digest(dict(stage='result')),
                        corrected_raw_output_digest=digest(corrected))

        # The correction authentication has its own journey tests. Fix its result
        # here so the two real renders exercise the same selected correction.
        with patch.object(review_output_correction, 'selected_correction', side_effect=select_observed_correction):
            paths = self.compare_renders()
        self.assertTrue(any(path.startswith('final/') and path.endswith('.fix.corrected.json') for path in paths))

    def test_context_repair_keeps_its_bytes(self):
        predecessor = self.start_series()
        self.invoke(predecessor)
        binding = repair.ResultRepairJourneyTests.contaminate(self, predecessor)
        repaired = repair.ResultRepairJourneyTests.repair_assignment(self, predecessor, binding)
        packet = self.packet(repaired)
        self.assertEqual(packet[parts.KEY]['adapter'], 'context_repair')
        self.assertNotIn('output_refusal', self.invoke(repaired, make_output(packet)))
        self.compare_renders()

    def test_a_turn_from_another_session_keeps_the_empty_listing_outcome(self):
        first = self.start_series()
        self.invoke(first)
        successor = self.assign('other-turn', first['id'])
        self.case.put('review_assignment', successor['id'], dict(successor, session_id='unrelated-session'))
        self.assertEqual(parts._list_series(self.case.read_records(), self.case.artifacts, successor['id']), [])
        paths = self.compare_renders()
        turns = [turn for path, raw in paths.items() if path.endswith('/series.json')
                 for turn in json.loads(raw)['turns'] if turn['assignment_id'] == successor['id']]
        self.assertEqual(len(turns), 1)
        self.assertFalse(turns[0]['review_parts']['complete'])
        (self.case.root / successor['packet']['path']).unlink()
        self.compare_renders(missing={successor['id']})


class V3RenderBytesTests(RenderComparison, PartsFixture):
    output_tokens = 128000

    def test_recorded_v3_dispatch_preserves_render_and_ignores_a_legacy_listing(self):
        self.oversized(count=1, characters=50000, limit=1300000)
        with patch.object(review_route_capacity, 'read_profile_priors', return_value=[]), \
                patch.object(parts, 'NEW_SERIES_PROTOCOLS', (review_parts_v3.PROTOCOL, parts.PROTOCOL)):
            assignment = self.assign()
        packet = self.packet(assignment)
        self.assertEqual(packet[parts.KEY]['protocol'], review_parts_v3.PROTOCOL)
        self.invoke(assignment, v3_fixtures.make_turn_output(packet))
        self.compare_renders()
        records = self.case.read_records()
        artifacts = self.case.artifacts
        expected = parts.observe_series(records, artifacts, assignment['id'])
        self.assertTrue(expected)
        with patch.object(parts, '_list_series', wraps=parts._list_series) as listing:
            observation = parts.observe_series(records, artifacts, assignment['id'], chain=[])
        listing.assert_not_called()
        self.assertEqual(observation, expected)
