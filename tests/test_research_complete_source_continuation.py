"""Native result records authenticate complete visible inputs and preserve pending sources."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness.review_bodies import read_history
from research_harness import research_decisions, review_protocol as protocol, review_transport as transport
from research_harness import review_source_complete as complete
from research_harness import review_codex_invocation as invocation
from research_harness.errors import ResearchError
from research_harness import problem_candidates
from codex_route_fixtures import bind_codex_response, install_codex_runtime
import test_research_problem_reviews as reviewers


class NativeCompleteSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = reviewers.ProblemReviewTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.store, self.artifacts = fixture.store, fixture.artifacts
        install_codex_runtime(self)
        fixture.supporting = self.artifacts.put(b'Exact independently supplied field evidence.', 'text/plain')
        fixture.mutate(protocol.record_route, {'id': 'route', 'adapter': 'codex_cli_v1', 'model': 'fixture-model',
            'endpoint': 'codex://local', 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}})
        with patch.object(transport, 'send_request', side_effect=self.respond_to_probe):
            fixture.mutate(protocol.probe_route, {'id': 'probe', 'route_id': 'route'}, credential='test-only')
        for reviewer in ('first', 'second'):
            fixture.assign('bar', reviewer, **fixture.deliver_supporting(reviewer))
            output = fixture.build_review_output('bar')
            self.invoke('bar-' + reviewer, output)
            fixture.mutate(research_decisions.record_value_review,
                dict(output, id='review-bar-' + reviewer, dossier_id=None, assignment_id='bar-' + reviewer))
        self.version = fixture.metadata(1)
        self.second_version = fixture.metadata(2)
        self.html = fixture.capture(self.version, body='An exact complete HTML main and proof. ' * 240)
        self.pdf = fixture.capture(self.version, pdf_text='An exact complete PDF main and proof. ' * 240 + '\f')
        self.request = {'id': 'first-source', 'kind': 'source', 'version_id': self.version, 'depth': 'fulltext'}
        self.second_request = dict(self.request, id='second-source', version_id=self.second_version)
        fixture.assign('result', 'first')
        self.invoke('result-first', self.output([self.request]))

    def response(self, route, request, output):
        return bind_codex_response(route, request, self.fixture.build_provider_response(output))

    def respond_to_probe(self, route, request, credential):
        packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
        return self.response(route, request, {'allowed_control': packet['allowed_control'],
            'evidence_control': packet['evidence_control'], 'excluded_controls': []})

    def output(self, requests):
        output = self.fixture.build_review_output('result')
        output['value']['status'] = 'unresolved'
        output['source_requests'] = requests
        return output

    def invoke(self, assignment, output):
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential: self.response(route, request, output)):
            return self.fixture.mutate(protocol.invoke_assignment,
                {'id': 'attempt-' + assignment, 'assignment_id': assignment}, credential='test-only')

    def assign(self, identifier, prior='result-first', **context):
        return self.fixture.assign('result', 'first', identifier=identifier, prior_assignment_id=prior, **context)

    def packet(self, assignment):
        return json.loads(self.artifacts.read(assignment['packet']))

    def test_native_probe_and_source_attempt_require_retained_model_and_effort_capture(self):
        records = self.store.snapshot()['records']
        assignment = records['review_assignment']['result-first']
        attempt = records['review_attempt']['attempt-result-first']
        probe = records['review_route_probe']['probe']
        self.assertEqual(assignment.get(invocation.KEY), invocation.PROTOCOL)
        self.assertEqual(attempt.get(invocation.KEY), invocation.PROTOCOL)
        self.assertEqual(probe.get(invocation.KEY), invocation.PROTOCOL)
        route = records['review_route']['route']
        response = json.loads(self.artifacts.read(attempt['response']))
        request = json.loads(self.artifacts.read(attempt['request']))
        contexts = [event['payload'] for event in response[invocation.KEY]['native_rollout']
                    if event.get('type') == 'turn_context']
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]['model'], route['model'])
        self.assertEqual(contexts[0]['effort'], 'high')
        self.assertTrue(contexts[0]['cwd'].startswith('/'))
        self.assertTrue(invocation.verify_context(route, request, response, required=True))
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
        response.pop(invocation.KEY)
        self.assertFalse(invocation.verify_context(route, request, response, required=True))

    def test_native_observation_closure_inherited_mode_and_later_acquisition_stability(self):
        assignment = self.assign('complete', source_delivery=complete.MODE)
        self.assertEqual(assignment['slot_id'], 'bar-first')
        self.invoke('complete', self.output([self.second_request]))
        records = self.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-complete']
        before = json.loads(self.artifacts.read(attempt['request']))
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
        actual = protocol.source_delivery_packet(records, self.artifacts, assignment)
        self.assertEqual(actual['pending_source_requests'], [])
        self.assertEqual(actual[complete.MARKER]['source_requests'], [self.request])
        self.fixture.capture(self.version, body='A later complete HTML representation. ' * 240)
        records = self.store.snapshot()['records']
        self.assertEqual(transport.request_body(records['review_route']['route'], assignment['prompt']['text'],
            self.packet(assignment), read_history(self.artifacts, assignment)), before)
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
        successor = self.assign('next', prior='complete')
        self.assertEqual(successor['context']['source_delivery'], complete.MODE)
        packet = self.packet(successor)
        self.assertEqual(packet[complete.MARKER]['source_requests'], [self.request, self.second_request])
        self.assertEqual(packet['pending_source_requests'], [self.second_request])
        self.assertEqual((successor['route_id'], successor['reviewer_id'], successor['slot_id']),
                         (assignment['route_id'], assignment['reviewer_id'], assignment['slot_id']))

    def test_pending_fulltext_cannot_finalize_and_wrong_reviewer_cannot_continue(self):
        assignment = self.assign('complete', source_delivery=complete.MODE)
        self.invoke('complete', self.output([self.second_request]))
        self.fixture.assert_error('review_source_continuation_mismatch', lambda: self.fixture.assign(
            'result', 'second', identifier='wrong', prior_assignment_id='complete'))
        pending = self.assign('pending', prior='complete')
        output = self.fixture.build_review_output('result')
        self.invoke('pending', output)
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, pending['id'])
        self.assertTrue(state['ready'])
        self.assertEqual(state['pending_source_requests'], [self.second_request])
        self.fixture.assert_error('review_sources_pending', lambda: self.fixture.mutate(research_decisions.record_value_review,
            dict(output, id='premature-final', dossier_id=None, assignment_id='pending')))

    def test_forged_marker_and_selection_fail_native_invocation_and_authentication(self):
        assignment = self.assign('complete', source_delivery=complete.MODE)
        variants = []
        for variant in ('marker', 'selection', 'source-closure', 'historical-source'):
            packet = self.packet(assignment)
            if variant == 'marker':
                packet[complete.MARKER]['protocol'] = 'invented'
            elif variant == 'selection':
                packet[complete.MARKER]['sources'][0]['selected_text_sha256'] = self.html['text']['sha256']
            elif variant == 'source-closure':
                packet[complete.MARKER]['source_requests'] = []
            else:
                packet[complete.MARKER]['historical_source_bodies'].append({'source_id': 'invented'})
            variants.append((variant, packet))
            with self.subTest(variant=variant):
                original = protocol._read_json
                with patch.object(protocol, '_read_json', side_effect=lambda artifacts, reference:
                        packet if reference == assignment['packet'] else original(artifacts, reference)):
                    with self.assertRaises(ResearchError) as caught:
                        self.fixture.mutate(protocol.invoke_assignment,
                            {'id': 'forged-' + variant, 'assignment_id': 'complete'}, credential='test-only')
                    self.assertEqual(caught.exception.code, 'review_complete_source_mismatch')
        self.invoke('complete', self.output([]))
        records = self.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-complete']
        for variant, packet in variants:
            changed = copy.deepcopy(records)
            forged = dict(assignment, packet=self.artifacts.put(json.dumps(packet).encode(), 'application/json'))
            changed['review_assignment']['complete'] = forged
            with self.subTest(authentication=variant):
                self.assertFalse(protocol.authenticate_provenance(changed, self.artifacts, forged, attempt))


if __name__ == '__main__':
    unittest.main()
