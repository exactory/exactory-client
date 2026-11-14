"""Protocol journeys use synthetic stores and authenticated fake reviewer outputs."""

import json
import copy
import sys
import unittest
from unittest.mock import patch

import codex_route_fixtures as codex_fixtures
import openness_reading_fixtures as reading_fixtures
import problem_parts_fixtures as legacy_fixtures
import review_parts_fixtures as parts_fixtures
import test_research_context_repair_integration as repair_fixtures
import test_review_parts_adjudication_journey as focused_fixtures
import test_review_parts_native as native_fixtures
import test_review_parts_repair_journey as parts_repair_fixtures
import test_review_parts_study as study_fixtures
import test_review_staged_frontier as staged_fixtures
import test_research_review_protocol as protocol_fixtures
from integration_fixtures import build_review_core, _prediction_value
from research_harness import cli, review_parts as parts, review_protocol as protocol
from research_harness import review_excerpts, review_worker
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series
from research_harness.evidence import digest


class SeriesJourneyTests(unittest.TestCase):
    def prepare(self, case_type):
        case = case_type()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def drive_codex(self, case, head, output, expected_exit=0, observe=None):
        steps, calls, kinds = [], [], []
        def emit(row):
            if observe is not None:
                observe(row)
            steps.append(row)
        def provider(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            calls.append(request)
            kinds.append(parts.find_kind(packet))
            return codex_fixtures.build_codex_response(route, request, output(packet))
        with patch.object(protocol.transport, 'send_request', side_effect=provider):
            code = run_series(case.store, head, emit=emit)
        self.assertEqual(code, expected_exit, steps[-1])
        self.assertEqual(len(calls), len(case.store.snapshot()['records'].get('review_attempt', {}))
                         - getattr(self, 'earlier_calls', 0))
        return steps, kinds

    def test_parts_v1_to_recorded_final(self):
        fixture = self.prepare(legacy_fixtures.ResultPartsCase)
        fixture.oversized(count=6, characters=24000)
        first = fixture.assign()
        self.earlier_calls = 2
        def output(packet):
            if packet['problem_evidence_parts']['stage'] == 'final':
                return fixture.final_output()
            return dict(stage='result', phase='evidence_part',
                part=packet['problem_evidence_parts']['turn'], notes=[dict(unit=unit,
                    note='The delivered evidence bears on the bounded claim.')
                    for unit in legacy_fixtures.note_units(packet)])
        steps, _ = self.drive_codex(fixture.case, first['id'], output)
        self.assertGreaterEqual(len([step for step in steps if step['state'] == 'continue']), 1)
        self.assertEqual(steps[-1]['state'], 'done')

    def test_parts_v2_read_combine_check_final(self):
        fixture = self.prepare(parts_fixtures.PartsResultCase)
        fixture.oversized(count=6, characters=24000)
        first = fixture.assign()
        self.earlier_calls = 2
        def output(packet):
            kind = parts.find_kind(packet)
            if kind == 'final':
                return fixture.final_output()
            if kind == 'combine':
                units = [row['unit'] for row in packet['review_parts_state']['cards']][:2]
                return parts_fixtures.make_output(packet, [dict(units=units,
                    question='Do the two records agree?', view='The records support the same bound.',
                    read=[dict(unit=unit) for unit in units])])
            return parts_fixtures.make_output(packet)
        steps, kinds = self.drive_codex(fixture.case, first['id'], output)
        self.assertTrue({'read', 'combine', 'check', 'final'} <= set(kinds), kinds)
        self.assertEqual(steps[-1]['state'], 'done')

    def test_reading_chain_three_reading_turns_records_openness(self):
        fixture = self.prepare(reading_fixtures.OpennessReadingCase)
        fixture.limit_input(24000)
        reference = fixture.export(assignment_id='first')
        fixture.assign('first', reference)
        self.earlier_calls = 0
        def output(packet):
            if 'reading_turn' in packet:
                return fixture.build_reading_response(packet)
            return fixture.build_native_result(packet)
        steps, kinds = self.drive_codex(fixture, 'first', output)
        self.assertEqual(steps[-1]['state'], 'done')
        self.assertEqual(steps[-2]['command'], 'problem-openness')
        self.assertEqual(steps[-2]['payload'], dict(assignment_id=steps[-2]['head_id']))
        self.assertEqual(len([step for step in steps if step['state'] == 'continue']), 3)

    def test_openness_chain_renders_native_views_before_the_next_turn(self):
        fixture = self.prepare(reading_fixtures.OpennessReadingCase)
        fixture.limit_input(24000)
        reference = fixture.export(assignment_id='first')
        fixture.assign('first', reference)
        turns = []

        def observe(row):
            path = fixture.root / 'intermediate/reviews/native_math/first/series.json'
            self.assertTrue(path.is_file())
            marker = json.loads((fixture.root / '.exactory/output-views.json').read_text())
            self.assertEqual(marker['revision'], fixture.store.revision)
            if row['state'] == 'run':
                turns.append(len(json.loads(path.read_text())['turns']))

        def output(packet):
            return (fixture.build_reading_response(packet) if 'reading_turn' in packet
                    else fixture.build_native_result(packet))

        steps, _ = self.drive_codex(fixture, 'first', output, observe=observe)
        self.assertEqual(steps[-1]['state'], 'done')
        self.assertEqual(turns, [1, 2, 3, 4])
        self.assertTrue(list((fixture.root / 'final/problem/openness').glob('*.json')))

    def test_native_parts_retains_packet_and_hands_off_final(self):
        fixture = self.prepare(native_fixtures.NativePartsTests)
        fixture.limit_input()
        fixture.assign()
        self.earlier_calls = 0
        def output(packet):
            if parts.find_kind(packet) != 'final':
                return parts_fixtures.make_output(packet)
            return fixture.final_output([row['digest'] for row in fixture.native['evidence']])
        steps, kinds = self.drive_codex(fixture, 'first', output, expected_exit=3)
        self.assertIn('read', kinds)
        self.assertEqual(steps[-1]['command'], 'search accept')

    def test_verification_parts_hands_off_verdict_body(self):
        fixture = self.prepare(study_fixtures.VerificationPartsTests)
        fixture.enlarge_verification()
        fixture.install_packet()
        fixture.assign()
        self.earlier_calls = 0
        # The final's packet already supplies its inventory; this factory keeps
        # the unchanged verdict and the full-read checks of the shared fixture.
        def final_output(packet):
            if parts.find_kind(packet) != 'final':
                return parts_fixtures.make_output(packet)
            assignment = next(row for row in fixture.records()['review_assignment'].values()
                              if row['packet_digest'] == packet['digest'])
            return fixture.final_output(assignment)
        steps, kinds = self.drive_codex(fixture.case, 'first', final_output, expected_exit=3)
        self.assertEqual(steps[-1]['command'], 'bind-verdict')
        self.assertIn('final', kinds)

    def test_readiness_parts_preserves_missing_bar_refusal_at_recording(self):
        fixture = self.prepare(study_fixtures.ReadinessPartsTests)
        fixture.enlarge_readiness()
        fixture.assign()
        self.earlier_calls = len(fixture.records().get('review_attempt', {}))
        def output(packet):
            if parts.find_kind(packet) != 'final':
                return parts_fixtures.make_output(packet)
            assignment = next(row for row in fixture.records()['review_assignment'].values()
                              if row['packet_digest'] == packet['digest'])
            return fixture.final_output(assignment, all_citations=True)
        steps, kinds = self.drive_codex(fixture.case, 'first', output, expected_exit=3)
        self.assertEqual(steps[-1]['code'], 'review_bar_pending')
        self.assertIn('read', kinds)

    def test_readiness_parts_records_both_support_and_value(self):
        fixture = self.prepare(study_fixtures.ReadinessPartsTests)
        dossier = copy.deepcopy(fixture.records()['strategy_dossier'][fixture.dossier_id])
        dossier['payload'].update(phase='result', material_change=None)
        dossier['digest'] = digest(dossier['payload'])
        changes = [('strategy_dossier', fixture.dossier_id, dossier)]
        bars = []
        for number in range(2):
            identifier = 'bar-' + str(number)
            output = dict(fixture.final_output(None), stage='bar', support=None)
            output['value'].update(bar_ids=[], objective_adequacy='not_assessed',
                                   method_adequacy='not_assessed', assurances=[])
            record = dict(id=identifier, dossier_id=fixture.dossier_id,
                assignment_id='assignment-' + identifier, reviewer_id='reviewer-' + identifier,
                payload=dict(output, id=identifier, dossier_id=fixture.dossier_id,
                             assignment_id='assignment-' + identifier),
                digest=digest(output), recorded_revision=fixture.case.store.revision + 1)
            changes.append(('value_review', identifier, record))
            bars.append(dict(id=identifier, review=record['payload']))
        def write(tx):
            for kind, identifier, record in changes:
                tx.put(kind, identifier, record)
        fixture.case.store.mutate('fixture.current_bars', {}, write,
            expected_revision=fixture.case.store.revision, request_id='fixture-current-bars')
        fixture.logical['bars'] = bars
        fixture.enlarge_readiness()
        first = fixture.assign()
        self.earlier_calls = len(fixture.records().get('review_attempt', {}))

        def output(packet):
            if parts.find_kind(packet) != 'final':
                return parts_fixtures.make_output(packet)
            assignment = next(row for row in fixture.records()['review_assignment'].values()
                              if row['packet_digest'] == packet['digest'])
            result = fixture.final_output(assignment, all_citations=True)
            result['value']['bar_ids'] = [bar['id'] for bar in bars]
            return result

        steps, kinds = self.drive_codex(fixture.case, first['id'], output)
        self.assertEqual(steps[-1]['state'], 'done')
        self.assertIn('final', kinds)
        self.assertTrue(fixture.records().get('readiness_review'))

    def test_adjudication_parts_records_exact_focused_final(self):
        fixture = self.prepare(focused_fixtures.AdjudicationPartsCase)
        fixture.oversized(count=2, paragraphs=1500)
        first = fixture.assign()
        self.earlier_calls = len(fixture.case.read_records()['review_attempt'])
        def output(packet):
            return fixture.final_output() if parts.find_kind(packet) == 'final' else parts_fixtures.make_output(packet)
        steps, kinds = self.drive_codex(fixture.case, first['id'], output)
        self.assertEqual(steps[-1]['state'], 'done')
        self.assertIn('read', kinds)
        self.assertEqual(steps[-2]['command'], 'review-adjudication')

    def test_context_repair_records_preserved_reassessment(self):
        fixture = self.prepare(repair_fixtures.ContextRepairIntegrationTests)
        fixture.prepare_repair()
        output = fixture.final_response()
        with patch.object(protocol.transport, 'send_request', return_value=fixture.provider_response(output)) as send:
            steps = []
            code = run_series(fixture.store, 'repair-a', emit=steps.append)
        self.assertEqual(code, 0, steps[-1])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(steps[-1]['state'], 'done')

    def test_manuscript_session_hands_off_its_observed_final(self):
        fixture = self.prepare(protocol_fixtures.ReviewProtocolTests)
        fixture.route()
        fixture.run_probe()
        fixture.seed('collection', 'cohort', dict(id='cohort', definition=dict(corpus='arxiv',
            primaryCategory='cs.LG', windowStart='2026-01-01', windowEnd='2026-01-31')))
        fixture.seed('literature_scope', 'research', dict(collection_ids=['cohort']))
        paper = fixture.artifacts.put(b'A bounded synthetic manuscript.', 'text/plain')
        bundle = dict(id='paper', digest='f' * 64, files=dict(pdf=dict(artifact=paper)),
            claim_evidence=[], execution_observations={},
            review_inputs=dict(synthesis=dict(sections=dict(standards=dict(payload=dict(
                scientific_requirement='State the finite assumptions.'))), foundation={})))
        fixture.seed('publication_bundle', 'paper', bundle)
        fixture.mutate(protocol.record_assignment, dict(id='paper-review', route_id='route',
            dossier_id=None, role='manuscript', reviewer_id='reader', author_id='author',
            context=dict(bundle=bundle)))
        output = dict(review=build_review_core(), prediction=_prediction_value(30, (20, 40)),
                      reasons=['The synthetic finite result has a bounded scope.'])
        steps = []
        with patch.object(protocol.transport, 'send_request', return_value=fixture.response(output)) as send:
            code = run_series(fixture.store, 'paper-review', emit=steps.append)
        self.assertEqual(code, 3)
        self.assertEqual([step['state'] for step in steps], ['run', 'handoff'])
        self.assertEqual(steps[-1]['command'], 'manuscript-review')
        self.assertEqual(send.call_count, 1)

    def test_first_failed_repair_is_replaced_into_parts_and_records_final(self):
        fixture = self.prepare(parts_repair_fixtures.BarRepairJourneyTests)
        first = fixture.assign_repair()
        earlier_calls = len(fixture.store.snapshot()['records']['review_attempt'])
        calls, steps = [], []
        def provider(route, request, credential):
            calls.append(request)
            packet = review_excerpts.read_sent_packet(request)
            if len(calls) == 1:
                response = codex_fixtures.build_codex_response(route, request, {})
                response['status'] = 'incomplete'
                response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
                return response
            if parts.find_kind(packet) != 'final':
                output = parts_fixtures.make_output(packet)
            else:
                from research_harness.review_refusal import evidence_arrays
                inventory = parts.read_inventory(fixture.artifacts, packet[parts.KEY]['inventory'])
                output = fixture.final_response()
                citations = [reference for row in inventory['units'] for reference in row['citations']]
                for evidence in evidence_arrays(output):
                    evidence[:] = citations
            return codex_fixtures.build_codex_response(route, request, output)
        with patch.object(protocol.transport, 'send_request', side_effect=provider), patch(
                'research_harness.review_failure_class.plan_after_failure',
                return_value=dict(state='replace', code='review_output_over_cap', details={})):
            code = run_series(fixture.store, first['id'], emit=steps.append)
        self.assertEqual(code, 0, steps[-1])
        self.assertEqual(steps[-1]['chain'], 'parts_v2:context_repair')
        replacement = next(row for row in steps if row['state'] == 'replace')
        self.assertEqual(replacement['payload']['context'], fixture.repair_payload()['context'])
        resumed = steps[steps.index(replacement) + 1]
        self.assertIn(first['id'] + '-run-1', resumed['progress']['attempts'])
        self.assertEqual(len(calls), len(fixture.store.snapshot()['records']['review_attempt']) - earlier_calls)

    def test_staged_controller_plans_runs_and_records(self):
        fixture = self.prepare(staged_fixtures.FrontierTests)
        controller = fixture.create_controller()
        output = fixture.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (fixture.case.root / 'scientific.json').write_text(json.dumps(output))
        steps = []
        with patch.object(review_worker, '_entry_command',
                          return_value=[sys.executable, '-c', staged_fixtures.WORKER]):
            code = run_series(fixture.case.store, controller['id'], emit=steps.append)
        self.assertEqual(code, 0, steps[-1])
        calls = (fixture.case.root / 'calls').read_text().splitlines()
        records = fixture.case.read_records()
        self.assertEqual(len(calls), len(records['review_reservation']))
        self.assertEqual(steps[-1]['state'], 'done')
        self.assertIn('plan', [step['state'] for step in steps])


if __name__ == '__main__':
    unittest.main()
