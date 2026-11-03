"""Prospective oversized reviews retain a posting-capable legacy delivery path."""

import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, record_codex_route
from problem_parts_fixtures import KEY, ResultPartsCase, note_units
from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness import review_problem_parts as parts, review_staged_protocol as staged
from research_harness import review_transport as transport
from research_harness.gates import gate_state


class ReviewEntryPolicyTests(ResultPartsCase):
    staged_entry = True

    def test_automatic_entry_does_not_construct_or_preflight_a_manifest(self):
        self.oversized()
        with patch.object(staged, 'build_manifest', wraps=staged.build_manifest) as manifest, \
                patch.object(staged, 'check_scale', wraps=staged.check_scale) as preflight:
            assignment = self.assign('automatic')
        self.assertEqual(manifest.call_count, 0)
        self.assertEqual(preflight.call_count, 0)
        self.assertEqual(self.packet(assignment)[KEY]['stage'], 'part')
        records = self.case.read_records()
        for kind in ('review_epoch', 'review_task', 'review_slot_head'):
            self.assertFalse(records.get(kind), kind)


class ReviewEntryPostGateTests(unittest.TestCase):
    def prepare_oversized_focused_assignment(self):
        from test_research_problem_adjudication_contract import ProblemAdjudicationContractTests

        case = ProblemAdjudicationContractTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        original = case.make_objection
        case.make_objection = lambda: dict(original(), reason='Unsupported implication. ' + 'x' * 1100000)
        case.prepare_reviews(result_reviewer='first')
        record_codex_route(case, 'local-route', output_tokens=128000, timeout_seconds=600)
        value = dict(id='focused-anchor', role='adjudicator', reviewer_id='focused-third',
            author_id='problem-author', dossier_id=None, route_id='local-route', context=dict(
                review_ids=['review-result-first', 'review-result-second'], objection_id='unsupported-implication'))
        return case, value

    def test_new_oversized_focused_assignment_refuses_an_indivisible_frame(self):
        import json
        from research_harness import review_parts
        from research_harness.errors import ResearchError

        case, value = self.prepare_oversized_focused_assignment()
        before = case.store.snapshot()
        with self.assertRaises(ResearchError) as raised:
            case.mutate(protocol.record_assignment, value)
        self.assertEqual(raised.exception.code, 'review_parts_frame_too_large')
        details = raised.exception.details
        self.assertGreater(details['frame_characters'], details['input_limit_characters'])
        records = case.read_records()
        self.assertEqual(details['input_limit_characters'],
                         transport.local_input_limit(records['review_route']['local-route']))
        packet = protocol.build_packet(records, case.artifacts, None, 'adjudicator',
                                       value['reviewer_id'], **value['context'])
        frame = review_parts.build_frame(packet, review_parts.enumerate_units(packet, 'problem_adjudicator'))
        largest = sorted(((key, len(json.dumps(item, ensure_ascii=False, sort_keys=True,
                                               allow_nan=False, separators=(',', ':'))))
                          for key, item in frame.items()), key=lambda row: row[1], reverse=True)[:10]
        self.assertEqual(details['largest_fields'], largest)
        self.assertGreater(dict(largest)['objection'], 1100000)
        self.assertEqual(case.store.snapshot(), before)

    def test_pre_072_oversized_focused_anchor_keeps_its_staged_upgrade(self):
        from research_harness import review_parts

        case, value = self.prepare_oversized_focused_assignment()
        with patch.object(review_parts, 'prepare_packet', side_effect=lambda records, artifacts,
                          value, route, packet, prompt, history, **kwargs: packet):
            assignment = case.mutate(protocol.record_assignment, value)['result']
        self.assertNotIn('staged_review', assignment)
        records = case.read_records()
        self.assertEqual(records['review_assignment'][assignment['id']], assignment)
        request = protocol.build_assignment_request(records, case.artifacts, assignment)
        self.assertGreater(parts._measure(records['review_route']['local-route'], request),
                           transport.local_input_limit(records['review_route']['local-route']))
        successor = case.mutate(protocol.record_assignment, dict(value, id='focused-upgrade',
            context=dict(staged_upgrade=dict(assignment_id=assignment['id']))))['result']
        self.assertEqual(successor['staged_review']['kind'], 'controller')
        epoch = case.read_records()['review_epoch'][successor['staged_review']['epoch_id']]
        self.assertEqual(epoch['predecessor']['assignment_id'], assignment['id'])

    def test_scale_supported_oversized_results_complete_v2_and_make_post_ready(self):
        from test_research_problem_delivery import ProblemDeliveryCase
        from test_research_problem_attempt import build_attempt
        from test_research_problem_assessment import build_assessment
        from research_harness import literature, reading
        from research_harness.problem_attempt import record_attempt

        case = ProblemDeliveryCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.initialize_problem()
        case.record_candidate()
        case.observe_bars()
        case.record_slate()
        case.selected = case.select_statement(candidate_id='candidate-1')
        item = case.record_full_reading()
        case.mutate(record_attempt, build_attempt(reading=item))
        case.observe_openness()
        claims = case.load_problem_module()
        case.mutate(claims.record_openness, {'assignment_id': 'openness-assignment'})
        evidence = [item]
        for index in range(6):
            version = case.metadata(78000 + index)
            bundle = case.bundle(version, bundle_id='large-bundle-' + str(index))
            case.mutate(literature.import_bundle, bundle)
            note = case.full_note(bundle, note_id='large-reading-' + str(index))
            next(iter(note['notes'].values()))['text'] = 'Inspected evidence. ' + 'x' * 240000
            case.mutate(reading.record_reading, note)
            evidence.append(dict(kind='reading', version_id=version, reading_id=note['id']))
        case.mutate(claims.record_value, case.build_value(evidence))
        case.mutate(claims.record_assessment, build_assessment(case.selected))
        record_codex_route(case, 'local-route', output_tokens=128000, timeout_seconds=600)
        records = case.store.snapshot()['records']
        route = records['review_route']['local-route']
        with transport.text_only_delivery(True):
            packet = protocol.build_packet(records, case.artifacts, None, 'result', 'first')
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation')
        request = transport.request_body(route, prompt['text'], packet, [])
        self.assertGreater(parts._measure(route, request), transport.local_input_limit(route))
        scale = staged.check_scale(records, case.artifacts, route,
                                   staged.build_manifest(case.artifacts, packet, 'result'), 'result')
        self.assertLessEqual(scale['card_output_tokens'], scale['output_allowance'])

        protocols = []
        for reviewer in ('first', 'second'):
            from research_harness.review_refusal import evidence_arrays
            scientific = case.build_review_output('result')
            for references in evidence_arrays(scientific):
                references[:] = [dict(kind='record', record_kind='reading', id='read-full')]
            prior = None
            for turn in range(1, 31):
                identifier = 'result-' + reviewer if turn == 1 else 'result-' + reviewer + '-' + str(turn)
                assignment = case.mutate(protocol.record_assignment, dict(id=identifier,
                    route_id='local-route', dossier_id=None, role='result', reviewer_id=reviewer,
                    author_id='problem-author', context={'prior_assignment_id': prior} if prior else {}))['result']
                recorded = protocol._read_json(case.artifacts, assignment['packet'])
                from research_harness import review_parts
                from review_parts_fixtures import make_output
                plan = recorded.get(review_parts.KEY)
                protocols.append(plan['protocol'] if plan else recorded.get('staged_review', {}).get('protocol'))
                self.assertFalse(assignment.get('staged_review'), 'Prospective result work must use parts v2')
                self.assertIsNotNone(plan, protocols)
                output = scientific if plan['kind'] == 'final' else make_output(recorded)
                invoke_codex(case, assignment['id'], output)
                if plan['kind'] == 'final':
                    case.mutate(decisions.record_value_review, dict(output, id='review-result-' + reviewer,
                        dossier_id=None, assignment_id=assignment['id']))
                    break
                prior = assignment['id']
            else:
                self.fail('The finite parts v2 evidence queue did not finish')
        report = gate_state(case.store.snapshot()['records'], case.artifacts, 'post')
        self.assertEqual({row['code'] for row in report['obligations']}, {'problem_decision_missing'})
        case.record_post_decision()
        records = case.store.snapshot()['records']
        self.assertEqual(set(protocols), {'exactory-review-parts-v2'})
        for kind in ('review_epoch', 'review_task', 'review_slot_head'):
            self.assertFalse(records.get(kind), kind)
        report = gate_state(records, case.artifacts, 'post')
        self.assertTrue(report['ready'], report['obligations'])
        self.assertNotIn('review_quality_measurement_pending', {row['code'] for row in report['obligations']})
