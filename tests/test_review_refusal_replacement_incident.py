"""Synthetic replay of the repaired bar continuation with two legacy part-offset refusals."""

import copy
import json
import unittest
from unittest.mock import patch

import test_research_problem_reviews as fixtures
from research_harness import cli, literature, reading, research_decisions as decisions
from research_harness import review_protocol as protocol, review_refusal as refusal
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.source_links import span_locator


class PartOffsetReplacementIncidentTests(unittest.TestCase):
    def build_revision_200(self):
        """The store of record 7 at revision 200: a repaired bar continuation with two refused answers that cite the
        second part of a split full text with whole-text offsets."""
        # Shape and quantities: 20261112072628-bar-final-refused-for-part-citation-with-whole-text-offsets, T2.
        case = fixtures.ProblemReviewTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.supporting = case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        case.prepare_review_route()
        case.observe('bar', 'first')
        second_bar = case.build_review_output('bar')
        second_bar['objections'] = [dict(id='part-question', claim='A premise needs evidence.',
            reason='The implication needs an independent source.', evidence=[case.supporting],
            resolution_condition='Supply the decisive part.')]
        case.observe('bar', 'second', second_bar)
        version = case.metadata(31)
        part_text = ('A synthetic part of a field comparison.\n' * 1100)[:38629]
        self.assertEqual(len(part_text), 38629)
        whole_text = 'p' * 118926 + part_text + '\nReferences: none.'
        capture = case.capture(version, pdf_text=whole_text)
        bundle = case.bundle(version, capture)
        case.mutate(literature.import_bundle, bundle)
        case.mutate(reading.record_reading, case.full_note(bundle))
        part = case.artifacts.put(part_text.encode(), capture['text']['media_type'])
        whole = capture['text']
        delivery = dict(status='delivered', version_id=version, excerpt=part, evidence=[part],
            request=dict(id='part-body', kind='source', version_id=version, depth='fulltext'),
            part=dict(start=118926, end=157555, part=2, parts=2),
            origin=dict(source_id=capture['source_id'], artifact_descriptor=dict(
                location=whole['path'], **{key: whole[key] for key in ('sha256', 'size', 'media_type')}),
                locator=span_locator(whole_text, 118926, 157555)))
        case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id='bar-first',
            kind='contamination', source='author_history', reason='Synthetic earlier exposure.', evidence=[case.supporting]))
        def probe(route, request, credential):
            packet = json.loads(request['input'][0]['content'].split('\n', 1)[1])
            return case.build_provider_response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch.object(protocol.transport, 'send_request', side_effect=probe):
            case.mutate(protocol.probe_route, dict(id='repair-probe', route_id='route'), credential='fixture')
        build = protocol._repair_packet
        def build_repaired_packet(*args, **kwargs):
            packet = build(*args, **kwargs)
            packet['source_deliveries'].append(copy.deepcopy(delivery))
            packet['evidence'].append(dict(artifact=part, encoding='utf-8', content=part_text))
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet
        with patch.object(protocol, '_repair_packet', side_effect=build_repaired_packet), patch.object(protocol, 'COMMON_VERSION', 3):
            repaired = case.assign('bar', 'first', identifier='repaired', context_repair=dict(
                assignment_id='bar-first', event_ids=['exposure'], probe_id='repair-probe',
                reason='Reassess the same fixed slot in a fresh context.', evidence=[case.supporting]))
        def invoke(assignment, output, identifier):
            with patch.object(protocol.transport, 'send_request', return_value=case.build_provider_response(output)):
                case.mutate(protocol.invoke_assignment, dict(id=identifier, assignment_id=assignment['id']), credential='fixture')
        parent = repaired
        for identifier, request in (('turn-4', dict(id='page', kind='inventory_page', page=0)),
                                    ('turn-5', dict(id='abstract', kind='source', version_id=version, depth='abstract'))):
            output = case.build_review_output('bar')
            output['value'].update(status='unresolved', evidence=[])
            output['source_requests'] = [request]
            invoke(parent, output, 'attempt-' + parent['id'])
            parent = case.assign('bar', 'first', identifier=identifier, prior_assignment_id=parent['id'])
            self.assertEqual(parent['prompt']['parameters']['common_version'], 3)
        bad = case.build_review_output('bar')
        bad['reassessment'] = dict(assignment_id='bar-first', findings=[dict(
            review_id='review-bar-first', disposition='revised', reason='The part refines the earlier field standard.',
            evidence=[case.supporting])])
        link = dict(version_id=version, source_id=capture['source_id'], artifact=part,
                    locator=dict(span_locator(part_text, 0, 38629), start=118926, end=157555))
        bad['value']['evidence'] = [dict(kind='source', link=link)]
        for number in (1, 2):
            invoke(parent, bad, 'refused-' + str(number))
            with self.assertRaises(ResearchError) as caught:
                case.mutate(decisions.record_value_review,
                    dict(bad, id='unrecorded', dossier_id=None, assignment_id=parent['id']))
            self.assertEqual(caught.exception.code, 'invalid_locator')
            stored = case.read_records()['review_output_refusal']['refused-' + str(number)]
            stored['details'] = dict(path='/value/evidence', message='The span must lie within the saved text')
            case.put('review_output_refusal', stored['id'], stored)
        while case.store.revision < 200:
            case.put('fixture_revision', 'padding', dict(value=case.store.revision))
        self.assertEqual(case.store.revision, 200)
        return case, parent, bad, part_text, version, invoke

    def test_revision_200_repaired_bar_gets_part_offsets_and_records_in_its_slot(self):
        case, parent, bad, part_text, version, invoke = self.build_revision_200()
        before = copy.deepcopy(case.read_records())
        relative = copy.deepcopy(bad)
        relative['value']['evidence'][0]['link']['locator'] = span_locator(part_text, 0, 38629)
        # A stored non-notice input keeps C12's diagnosis and a recordable refusal path.
        with self.assertRaises(ResearchError) as corrected_original:
            decisions.validate_review_contract(before, case.artifacts, parent, relative,
                attempt=before['review_attempt']['refused-2'])
        self.assertEqual(corrected_original.exception.code, 'source_link_not_fulltext')
        from research_harness.review_refusal_replacement import validate_replacement_origin
        installed = refusal.classify_observed_output
        with patch.object(refusal, 'classify_observed_output', wraps=installed) as check:
            replacement = case.assign('bar', 'first', identifier='replacement', prior_assignment_id='turn-4',
                                      output_refusal={'assignment_id': parent['id']})
        self.assertEqual([call.args[2] for call in check.call_args_list], ['refused-1', 'refused-2'])
        packet = protocol._read_json(case.artifacts, replacement['packet'])
        for entry in packet['output_refusal']['refused']:
            self.assertEqual(entry['details']['part_relative_span'], dict(start=0, end=38629))
            self.assertIn('start 0 and end 38629', entry['message'])
        self.assertEqual(replacement['slot_id'], parent['slot_id'])
        self.assertEqual(replacement['reviewer_id'], parent['reviewer_id'])
        self.assertEqual(replacement['prompt']['parameters']['common_version'], 3)
        validate_replacement_origin(case.read_records(), case.artifacts, replacement)
        corrected = copy.deepcopy(bad)
        corrected['value']['evidence'][0]['link']['locator'] = span_locator(part_text, 0, 38629)
        invoke(replacement, corrected, 'corrected')
        records = case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, replacement, records['review_attempt']['corrected']))
        case.mutate(decisions.record_value_review,
            dict(corrected, id='recorded', dossier_id=None, assignment_id=replacement['id']))
        after = case.read_records()
        from research_harness.review_reading import plan_readings
        without_reading = copy.deepcopy(after)
        without_reading['reading'] = {key: value for key, value in after['reading'].items()
                                      if value['version_id'] != version}
        handoff = plan_readings(without_reading, case.artifacts, replacement, corrected)
        self.assertEqual(handoff['command'], 'read')
        self.assertEqual(len(handoff['details']['readings']), 1)
        result = case.assign('result', 'first', identifier='result-initial')
        result_packet = protocol._read_json(case.artifacts, result['packet'])
        objection_id = result_packet['material_objections'][0]['id']
        result_output = case.build_review_output('result')
        result_output['value']['bar_ids'] = [bar['id'] for bar in result_packet['bars']]
        result_output['value']['objection_findings'] = [dict(id=objection_id, status='resolved',
            reason='This delivered part establishes the needed premise.',
            evidence=copy.deepcopy(corrected['value']['evidence']))]
        invalid_result = copy.deepcopy(result_output)
        invalid_result['value']['status'] = 'invalid'
        invoke(result, invalid_result, 'result-refused')
        with self.assertRaises(ResearchError):
            case.mutate(decisions.record_value_review,
                dict(invalid_result, id='unrecorded-result', dossier_id=None, assignment_id=result['id']))
        result_replacement = case.assign('result', 'first', identifier='result-replacement',
                                        output_refusal={'assignment_id': result['id']})
        invoke(result_replacement, result_output, 'result-corrected')
        saved_result = case.mutate(decisions.record_value_review,
            dict(result_output, id='result-recorded', dossier_id=None, assignment_id=result_replacement['id']))['result']
        decision = dict(payload=dict(objections=[dict(id=objection_id, disposition='resolved', adjudication_id=None)]))
        gate = decisions._objection_obligations(case.read_records(), case.artifacts,
            result_replacement['subject'], decision, [saved_result])
        self.assertEqual(gate, [])
        after = case.read_records()
        for kind in ('review_output_refusal', 'review_assignment', 'review_attempt'):
            for key, value in before[kind].items():
                self.assertEqual(after[kind][key], value)

    def test_revision_200_stale_route_records_through_a_replacement_on_a_renewed_route(self):
        """The route of the chain pins an earlier reviewer runtime, as the route of record 7 did at revision 200."""
        from research_harness.review_series import run_series
        case, parent, bad, part_text, _, _ = self.build_revision_200()
        route = case.read_records()['review_route']['route']
        installed = dict(route['runtime'], implementation_sha256='0' * 64)
        corrected = copy.deepcopy(bad)
        corrected['value']['evidence'][0]['link']['locator'] = span_locator(part_text, 0, 38629)
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'].split('\n', 1)[1])
            if 'allowed_control' in packet:
                return case.build_provider_response(dict(allowed_control=packet['allowed_control'],
                    evidence_control=packet['evidence_control'], excluded_controls=[]))
            return case.build_provider_response(corrected)
        lines = []
        with patch.object(protocol.transport, 'runtime', return_value=installed), \
                patch.object(protocol.transport, 'send_request', side_effect=respond):
            case.mutate(cli.OPERATIONS['review-route'], dict(id='route-renewed',
                **{key: route[key] for key in ('adapter', 'model', 'endpoint', 'configuration')}))
            case.mutate(protocol.probe_route, dict(id='renewed-probe', route_id='route-renewed'), credential='fixture')
            run_series(case.store, parent['id'], emit=lines.append)
        # On 0.91.2 the series ends with the handoff review-route under review_route_runtime_stale.
        self.assertEqual(lines[-1]['state'], 'done', lines[-1])
        records = case.read_records()
        [replacement] = [row for row in records['review_assignment'].values()
                         if row['context'].get('output_refusal') == {'assignment_id': parent['id']}]
        self.assertEqual(replacement['route_id'], 'route-renewed')
        notice = protocol._read_json(case.artifacts, replacement['packet'])['output_refusal']
        self.assertEqual([entry['attempt_id'] for entry in notice['refused']], ['refused-1', 'refused-2'])
        for entry in notice['refused']:
            self.assertEqual(entry['details']['part_relative_span'], dict(start=0, end=38629))
        self.assertEqual((replacement['slot_id'], replacement['reviewer_id']), (parent['slot_id'], parent['reviewer_id']))
        self.assertTrue(any(row['assignment_id'] == replacement['id'] for row in records['value_review'].values()))
