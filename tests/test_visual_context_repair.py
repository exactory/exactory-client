"""Native visual repair preserves failed observations and requires fresh independent input."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness import research_decisions as decisions
from research_harness import review_context_repair as context
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_source_complete as complete
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.artifacts import ArtifactStore
from research_harness import review_visual_context_repair as visual_repair


class VisualContextRepairTests(unittest.TestCase):
    def setUp(self):
        from test_visual_result_delivery import VisualResultDeliveryTests
        self.fixture = VisualResultDeliveryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.case, self.artifacts = self.fixture.case, self.fixture.artifacts
        self.prior = self.fixture.assignment
        self.output = self.case.build_review_output('result')
        self.output['value']['evidence'].extend([
            {'kind': 'source', 'link': self.fixture.pdf_link},
            {'kind': 'source', 'link': self.fixture.html_link}])
        def failed_input(route, request, credential):
            response = self.fixture.response(route, request, self.output)
            response['context_evidence']['input_verified'] = False
            response['context_evidence']['issues'] = ['session_identity_or_instructions']
            return response
        with patch.object(transport, 'send_request', side_effect=failed_input):
            self.case.mutate(protocol.invoke_assignment, {'id': 'failed-visual-attempt',
                'assignment_id': self.prior['id']}, credential='test-only')
        self.event_evidence = self.artifacts.put(b'An exact terminal instruction-space mismatch was observed.', 'text/plain')
        self.case.mutate(protocol.record_context_event, {'id': 'input-defect', 'assignment_id': self.prior['id'],
            'kind': 'contamination', 'source': 'runtime_instruction_transformation',
            'reason': 'The observed instruction bytes differ; no foreign author context was observed.',
            'evidence': [self.event_evidence]})
        self.case.mutate(decisions.record_value_review, dict(self.output, id='failed-visual-final',
            dossier_id=None, assignment_id=self.prior['id']))
        self.probe('repair-probe')
        self.value = {'id': 'repaired-visual-result', 'route_id': self.prior['route_id'], 'dossier_id': None,
            'role': 'result', 'reviewer_id': self.prior['reviewer_id'], 'author_id': self.prior['author_id'],
            'context': {'context_repair': {'assignment_id': self.prior['id'], 'event_ids': ['input-defect'],
                'probe_id': 'repair-probe', 'reason': 'Reassess the unchanged findings using exact fresh visual input.',
                'evidence': [self.event_evidence]}}}

    def probe(self, identifier):
        def respond(route, request, credential):
            controls = self.case.read_records()['review_probe_invocation'][identifier]['positive_controls']
            return self.fixture.response(route, request, {key: controls[key] for key in
                ('allowed_control', 'evidence_control')} | {'excluded_controls': []})
        with patch.object(transport, 'send_request', side_effect=respond):
            self.case.mutate(protocol.probe_route, {'id': identifier, 'route_id': self.prior['route_id']},
                             credential='test-only')

    def assign(self):
        return self.case.mutate(protocol.record_assignment, self.value)['result']

    def test_public_repair_keeps_actual_history_and_records_a_new_independent_final(self):
        repaired = self.assign()
        records = self.case.read_records()
        packet = json.loads(self.artifacts.read(repaired['packet']))
        old_attempt = records['review_attempt']['failed-visual-attempt']
        self.assertTrue(protocol.authenticate_provenance(records, self.artifacts, self.prior, old_attempt))
        self.assertFalse(protocol.authenticate_attempt(records, self.artifacts, self.prior, old_attempt))
        self.assertEqual(repaired['history'], [])
        self.assertEqual(repaired['session_id'], repaired['id'])
        self.assertEqual(context.slot_id(records, repaired), context.slot_id(records, self.prior))
        self.assertEqual(packet['visual_context_repair']['attempt_id'], old_attempt['id'])
        self.assertEqual(packet['scientific_history'][-1]['observed_output'], self.output)
        self.assertEqual(packet['scientific_history'][-1]['assignment_id'], self.prior['id'])
        self.assertEqual(packet['historical_reviews'][-1]['id'], 'failed-visual-final')
        complete.validate(records, self.artifacts, repaired, packet)
        response = copy.deepcopy(self.output)
        response['reassessment'] = {'assignment_id': self.prior['id'], 'findings': [
            {'review_id': row['id'], 'disposition': 'revised', 'reason': 'The exact fresh supplied source evidence supports this assessment.',
             'evidence': [{'kind': 'source', 'link': self.fixture.pdf_link}]} for row in packet['historical_reviews']]}
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          self.fixture.response(route, request, response)):
            self.case.mutate(protocol.invoke_assignment, {'id': 'repair-attempt',
                'assignment_id': repaired['id']}, credential='test-only')
        review = self.case.mutate(decisions.record_value_review, dict(response, id='repair-final',
            dossier_id=None, assignment_id=repaired['id']))['result']
        records = self.case.read_records()
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, repaired, records['review_attempt']['repair-attempt']))
        self.assertEqual(decisions.find_undelivered_review_references(records, self.artifacts, review), [])
        self.probe('later-probe')
        self.assertTrue(protocol.authenticate_provenance(self.case.read_records(), self.artifacts,
            repaired, records['review_attempt']['repair-attempt']))

    def test_repair_binding_rejects_changed_observations_history_and_required_sources(self):
        repaired = self.assign()
        records = self.case.read_records()
        packet = json.loads(self.artifacts.read(repaired['packet']))
        for defect in ('output', 'history', 'order', 'encoding', 'required-source', 'route', 'reviewer'):
            changed, assignment = copy.deepcopy(packet), copy.deepcopy(repaired)
            if defect == 'output':
                changed['visual_context_repair']['output'] = self.event_evidence
            elif defect == 'history':
                changed['scientific_history'][-1]['observed_output']['value']['reason'] = 'Invented replacement.'
            elif defect == 'order':
                changed['scientific_history'].reverse()
            elif defect == 'encoding':
                changed['visual_context_repair']['encoding']['implementation_sha256'] = '0' * 64
            elif defect == 'required-source':
                selected = changed[complete.MARKER]['sources'][0]['selected_text_sha256']
                for entry in changed['evidence']:
                    if entry['artifact']['sha256'] == selected:
                        entry.update(content='', encoding='omitted')
            else:
                assignment[defect + '_id'] = 'different'
            with self.subTest(defect=defect), self.assertRaises(ResearchError):
                complete.validate(records, self.artifacts, assignment, changed)

    def test_existing_native_event_probe_and_no_fork_guards_still_apply(self):
        for change in ({'probe_id': 'visual-probe'}, {'event_ids': []}, {'evidence': []}):
            value = copy.deepcopy(self.value)
            value['context']['context_repair'].update(change)
            with self.subTest(change=change), self.assertRaises(ResearchError):
                self.case.mutate(protocol.record_assignment, value)
        self.assign()
        value = dict(self.value, id='forked-repair')
        with self.assertRaises(ResearchError) as failure:
            self.case.mutate(protocol.record_assignment, value)
        self.assertEqual(failure.exception.code, 'review_resampling_forbidden')

    def test_source_continuation_after_repair_retains_complete_sources_and_fixed_slot(self):
        repaired = self.assign()
        response = copy.deepcopy(self.output)
        response['value']['status'] = 'unresolved'
        response['source_requests'] = [{'id': 'next-inventory', 'kind': 'inventory_page', 'page': 0}]
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          self.fixture.response(route, request, response)):
            self.case.mutate(protocol.invoke_assignment, {'id': 'repair-source-attempt',
                'assignment_id': repaired['id']}, credential='test-only')
        value = dict(self.value, id='repair-source-continuation', context={'prior_assignment_id': repaired['id']})
        continued = self.case.mutate(protocol.record_assignment, value)['result']
        records = self.case.read_records()
        packet = json.loads(self.artifacts.read(continued['packet']))
        complete.validate(records, self.artifacts, continued, packet)
        self.assertEqual(continued['context']['source_delivery'], complete.MODE)
        self.assertEqual(continued['session_id'], repaired['session_id'])
        self.assertEqual(continued['history_assignment_id'], repaired['id'])
        self.assertEqual(context.slot_id(records, continued), context.slot_id(records, self.prior))
        first = json.loads(self.artifacts.read(repaired['packet']))
        self.assertEqual(packet[complete.MARKER]['sources'], first[complete.MARKER]['sources'])
        self.assertEqual(packet[complete.MARKER]['historical_source_bodies'], first[complete.MARKER]['historical_source_bodies'])
        self.assertEqual(packet['context_repair'], first['context_repair'])

    def test_later_unverified_attempt_does_not_replace_the_native_history_selection(self):
        records = copy.deepcopy(self.case.read_records())
        prior_attempt = records['review_attempt']['failed-visual-attempt']
        history = visual_repair._history(self.artifacts, prior_attempt)
        ancestor = context._exposure_lineage(records, self.prior)[0]
        original = min((row for row in records['review_attempt'].values()
            if row['assignment_id'] == ancestor['id']), key=lambda row: row['recorded_revision'])
        later = copy.deepcopy(original)
        later.update(id='later-unverified-history', recorded_revision=original['recorded_revision'] + 1, origin='imported')
        response = json.loads(self.artifacts.read(original['response']))
        response['context_evidence'] = {'inspected': True, 'input_verified': False}
        later['response'] = self.artifacts.put(json.dumps(response).encode(), 'application/json')
        records['review_attempt'][later['id']] = later
        selected = protocol.assignment_state(records, self.artifacts, ancestor['id'])
        self.assertEqual(selected['attempt_id'], original['id'])
        retained = visual_repair._science(records, self.artifacts, self.prior, prior_attempt, history)
        self.assertEqual(retained[0]['observed_output'], selected['output'])


class FrozenVisualAttemptSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.records = {'review_attempt': {}, 'review_output_refusal': {}}
        for identifier, revision, verified in [('first', 2, True), ('unverified', 4, False), ('last', 6, True)]:
            response = self.artifacts.put(json.dumps({'context_evidence':
                {'inspected': True, 'input_verified': verified}}).encode(), 'application/json')
            self.records['review_attempt'][identifier] = {'id': identifier, 'assignment_id': 'assignment',
                'status': 'completed', 'recorded_revision': revision, 'response': response}

    def select(self, revision):
        selector = getattr(protocol, '_selected_attempt', None)
        origin = getattr(visual_repair, '_origin_records', None)
        self.assertTrue(callable(selector) and callable(origin), 'Reuse the native selector on frozen origin records.')
        return selector(origin(self.records, revision), self.artifacts, 'assignment')[:2]

    def test_later_attempts_and_refusals_do_not_change_the_frozen_selection(self):
        self.assertEqual(self.select(3)[0]['id'], 'first')
        self.assertEqual(self.select(7)[0]['id'], 'first')
        self.records['review_output_refusal']['first'] = {'recorded_revision': 8}
        # Certificate classification has its own native contract suite; this test
        # isolates whether an existing certificate is visible at the origin cutoff.
        with patch('research_harness.review_refusal.classify_observed_output', return_value={
                'code': 'review_contract_failure', 'check': 'fixture-contract', 'details': {}}):
            self.assertEqual(self.select(8), (self.records['review_attempt']['first'], []))
            selected, refused = self.select(9)
        self.assertEqual(selected['id'], 'last')
        self.assertEqual(refused, [{'attempt_id': 'first', 'code': 'review_contract_failure'}])


if __name__ == '__main__':
    unittest.main()
