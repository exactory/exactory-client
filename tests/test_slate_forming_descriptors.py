"""Slate forming provenance carries records, while artifact text arrives on request."""

import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, record_codex_route
from research_harness import bar_sources, problem_forming, problem_forming_import
from research_harness import review_capacity_evidence, review_excerpts, review_prompt_paragraphs
from research_harness import review_protocol as protocol
from research_harness.evidence import digest
import test_problem_forming_import as import_fixtures
from test_research_problem_forming import build_forming, build_outcome
import test_research_problem_slate as slate_fixtures


class SlateFormingTests(unittest.TestCase):
    def make_destination(self):
        case = slate_fixtures.ProblemSlateTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def invoke(self, case, assignment, output):
        with patch('research_harness.review_transport.send_request', return_value=case.build_provider_response(output)):
            return case.mutate(protocol.invoke_assignment, dict(id='attempt-' + assignment['id'],
                assignment_id=assignment['id']), credential='fixture-only')

    def test_equal_artifact_parts_deliver_each_original_offset(self):
        case = self.make_destination()
        artifact = case.artifacts.put(b'abcdefghij' * 3, 'text/plain')
        call = case.mutate(problem_forming.record_forming, build_forming(performer=dict(
            kind='model_lane', lane_id='forming-lane', model='fixture-model', effort='reported',
            prompt=artifact, output=artifact)))['result']
        reference = dict(kind='record', record_kind='forming_call', id=call['id'], digest=digest(call))
        case.record_candidate(source=dict(kind='phenomenon', reference='A repeated finite comparison.', evidence=[reference]))
        case.observe_bars()
        assignment = case.assign('slate', 'first')
        output = case.build_slate_output(['candidate-1'])
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='repeated', kind='artifact', sha256=artifact['sha256'])]
        self.invoke(case, assignment, output)
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 10):
            for index in range(3):
                assignment = case.assign('slate', 'first', identifier='part-' + str(index),
                                         prior_assignment_id=assignment['id'])
                packet = json.loads(case.artifacts.read(assignment['packet']))
                part = packet['source_deliveries'][0]['part']
                self.assertEqual((part['start'], part['end']), (index * 10, (index + 1) * 10))
                self.assertEqual(bool(packet['pending_source_requests']), index < 2)
                if index < 2:
                    more = copy.deepcopy(output)
                    more['source_requests'] = []
                    self.invoke(case, assignment, more)

    def test_a_slate_repair_keeps_v3_for_unrequested_forming_artifacts(self):
        case = self.make_destination()
        artifact = case.artifacts.put(b'A held finite comparison.', 'text/plain')
        call = case.mutate(problem_forming.record_forming, build_forming(performer=dict(
            kind='model_lane', lane_id='forming-lane', model='fixture-model', effort='reported',
            prompt=artifact, output=artifact)))['result']
        reference = dict(kind='record', record_kind='forming_call', id=call['id'], digest=digest(call))
        self.check_slate_repair(case, artifact, reference)

    def test_a_slate_repair_keeps_v3_for_unrequested_imported_artifacts(self):
        fixture = import_fixtures.FormingImportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        artifact = fixture.source.artifacts.put(b'A held imported finite comparison.', 'text/plain')
        call = fixture.source.mutate(problem_forming.record_forming, build_forming('held-call', forms=[],
            performer=dict(kind='model_lane', lane_id='forming-lane', model='fixture-model', effort='reported',
                           prompt=artifact, output=artifact)))['result']
        case = self.make_destination()
        imported = fixture.import_history(destination=case)
        reference = fixture.reference('forming_call', call, imported['source_origin'])
        self.check_slate_repair(case, artifact, reference)

    def check_slate_repair(self, case, artifact, reference):
        from research_harness import review_parts_repair
        case.record_candidate(source=dict(kind='phenomenon', reference='The finite comparison.', evidence=[reference]))
        case.observe_bars()
        original = case.assign('slate', 'first')
        output = case.build_slate_output(['candidate-1'])
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        self.invoke(case, original, output)
        case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id=original['id'],
            kind='contamination', source='author_history', reason='Author context reached this slate.',
            evidence=[case.supporting]))
        record_codex_route(case, 'repair-route')
        payload = dict(id='slate-repair', route_id='repair-route', dossier_id=None, role='slate',
            reviewer_id='replacement', author_id=original['author_id'], context=dict(context_repair=dict(
                assignment_id=original['id'], event_ids=['exposure'], probe_id='probe-repair-route',
                reason='Reassess the retained slate.', evidence=[case.supporting])))
        with patch.object(review_parts_repair, 'requires_parts', return_value=True):
            repaired = case.mutate(protocol.record_assignment, payload)['result']
        packet = json.loads(case.artifacts.read(repaired['packet']))
        self.assertEqual(packet['source_rules'], bar_sources.PROTOCOL_V3)
        self.assertNotIn('review_parts', packet)
        self.assertNotIn(artifact, [row['artifact'] for row in packet['evidence']])
        output['source_requests'] = [dict(id='held', kind='artifact', sha256=artifact['sha256'])]
        invoke_codex(case, repaired['id'], output)
        state = protocol.assignment_state(case.read_records(), case.artifacts, repaired['id'])
        self.assertTrue(state['ready'], state['obligations'])
        child = case.mutate(protocol.record_assignment, dict(payload, id='slate-repair-next',
            context=dict(prior_assignment_id=repaired['id'])))['result']
        delivered = json.loads(case.artifacts.read(child['packet']))
        self.assertIn(artifact, [row['artifact'] for row in delivered['evidence']])
        self.assertFalse(delivered['source_deliveries'][0]['reading_credit'])

    def test_native_call_and_three_outcomes_arrive_without_their_artifact_bodies(self):
        case = self.make_destination()
        artifact = case.artifacts.put(b'Exploratory output. ' * 160000, 'text/plain')
        prompt = case.artifacts.put(b'Explore a finite family.', 'text/plain')
        call = case.mutate(problem_forming.record_forming, build_forming(performer=dict(
            kind='model_lane', lane_id='forming-lane', model='fixture-model', effort='reported',
            prompt=prompt, output=artifact)))['result']
        outcomes = [case.mutate(problem_forming.record_form_outcome, build_outcome('outcome-' + str(index)))['result']
                    for index in range(3)]
        references = [dict(kind='record', record_kind=kind, id=row['id'], digest=digest(row))
                      for kind, row in [('forming_call', call)] + [('form_outcome', row) for row in outcomes]]
        case.record_candidate(source=dict(kind='phenomenon', reference='The recorded finite observation.', evidence=references))
        case.observe_bars()
        assignment = case.assign('slate', 'first')
        packet = json.loads(case.artifacts.read(assignment['packet']))
        self.assertEqual([row['cite'] for row in packet.get('forming_records', [])],
                         sorted(references, key=lambda row: (row['record_kind'], row['id'])))
        self.assertNotIn(artifact, [entry['artifact'] for entry in packet['evidence']])
        self.assertLess(len(json.dumps(protocol.build_assignment_request(case.read_records(), case.artifacts, assignment))), 1300000)
        carried = protocol.collect_carried_records(case.read_records(), case.artifacts, None, packets=[packet])
        for reference in references:
            self.assertIsNotNone(protocol.find_carried_record(case.read_records(), carried, reference, frozen=True))
        self.assertIn('exactory-problem-slate-forming-v2',
                      assignment['prompt']['parameters'][review_prompt_paragraphs.PARAMETER])
        output = case.build_slate_output(['candidate-1'])
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='probe-output', kind='artifact', sha256=artifact['sha256'])]
        self.invoke(case, assignment, output)
        self.assertTrue(protocol.assignment_state(case.read_records(), case.artifacts, assignment['id'])['ready'])
        child = case.assign('slate', 'first', identifier='slate-next', prior_assignment_id=assignment['id'])
        delivered = json.loads(case.artifacts.read(child['packet']))
        self.assertEqual(delivered['source_deliveries'][0]['origin']['artifact_descriptor']['sha256'], artifact['sha256'])
        self.assertEqual(delivered['source_deliveries'][0]['part']['end'], 120000)
        self.assertEqual(len(delivered['pending_source_requests']), 1)

    def test_imported_history_keeps_origin_citations_and_delivers_requested_text(self):
        # Incident 20261112012956-slate-review-cannot-carry-a-forming-import.
        fixture = import_fixtures.FormingImportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        artifact = fixture.source.artifacts.put(b'Finite computation. ' * 160000, 'text/plain')
        prompt = fixture.source.artifacts.put(b'Explore the comparison.', 'text/plain')
        call = fixture.source.mutate(problem_forming.record_forming, build_forming('large-call', forms=[],
            performer=dict(kind='model_lane', lane_id='forming-lane', model='fixture-model', effort='reported',
                           prompt=prompt, output=artifact)))['result']
        case = self.make_destination()
        imported = fixture.import_history(destination=case)
        reference = fixture.reference('forming_call', call, imported['source_origin'])
        case.record_candidate(source=dict(kind='phenomenon', reference='An imported observation.', evidence=[reference]))
        case.observe_bars()
        from capacity_evidence_fixtures import build_capacity_prior
        route = case.read_records()['review_route']['route']
        # The historical all-body packet needs its own synthetic keyed allowance.
        priors = tuple(review_capacity_evidence.read_capacity_priors()) + (
            build_capacity_prior(route, 10000000, 'historical-forming-prior'),)
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL), \
                patch.object(review_capacity_evidence, 'CAPACITY_PRIORS', priors):
            legacy = case.assign('slate', 'first', identifier='legacy-slate')
        old_packet_bytes = case.artifacts.read(legacy['packet'])
        old_packet = json.loads(old_packet_bytes)
        self.assertIn(artifact, [row['artifact'] for row in old_packet['evidence']])
        old_request = protocol.build_assignment_request(case.read_records(), case.artifacts, legacy)
        assignment = case.assign('slate', 'second')
        packet = json.loads(case.artifacts.read(assignment['packet']))
        self.assertNotIn(artifact, [row['artifact'] for row in packet['evidence']])
        self.assertLess(len(json.dumps(protocol.build_assignment_request(case.read_records(), case.artifacts, assignment))), 1300000)
        carried = protocol.collect_carried_records(case.read_records(), case.artifacts, None, packets=[packet])
        self.assertIsNotNone(protocol.find_carried_record(case.read_records(), carried, reference, frozen=True))
        output = case.build_slate_output(['candidate-1'])
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='text', kind='artifact', sha256=prompt['sha256'])]
        self.invoke(case, assignment, output)
        child = case.assign('slate', 'second', identifier='slate-next', prior_assignment_id=assignment['id'])
        delivered = json.loads(case.artifacts.read(child['packet']))
        self.assertIn(prompt, [row['artifact'] for row in delivered['evidence']])
        final = case.build_slate_output(['candidate-1'])
        final['value']['evidence'] = [reference, prompt]
        self.invoke(case, child, final)
        state = protocol.assignment_state(case.read_records(), case.artifacts, child['id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['pending_source_requests'], [])
        self.assertEqual(case.artifacts.read(legacy['packet']), old_packet_bytes)
        self.assertEqual(protocol.build_assignment_request(case.read_records(), case.artifacts, legacy), old_request)
        self.assertTrue(protocol._prompt_matches(legacy, packet=old_packet))
        self.assertEqual(digest({key: value for key, value in old_packet.items() if key != 'digest'}),
                         legacy['packet_digest'])

    def test_a_slate_without_forming_records_and_stored_v2_sessions_keep_their_bytes(self):
        case = self.make_destination()
        case.record_candidate()
        case.observe_bars()
        records = case.read_records()
        packet = protocol.build_packet(records, case.artifacts, None, 'slate', 'first')
        unchanged = copy.deepcopy(packet)
        problem_forming_import.deliver_slate_imports(records, case.artifacts, unchanged)
        self.assertEqual(unchanged, packet)
        # Freeze a pre-v3 slate containing a native forming call; a continuation must not upgrade it.
        case.mutate(problem_forming.record_forming, build_forming())
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            assignment = case.assign('slate', 'first')
        old = json.loads(case.artifacts.read(assignment['packet']))
        self.assertNotIn('forming_records', old)
        output = case.build_slate_output(['candidate-1'])
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        self.invoke(case, assignment, output)
        before = protocol.build_assignment_request(case.read_records(), case.artifacts, assignment)
        child = case.assign('slate', 'first', identifier='old-next', prior_assignment_id=assignment['id'])
        self.assertEqual(protocol.build_assignment_request(case.read_records(), case.artifacts, assignment), before)
        self.assertNotIn('forming_records', json.loads(case.artifacts.read(child['packet'])))
