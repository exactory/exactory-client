"""Public result recording checks exact visual requests and scoped citations.

Transport responses below are synthetic test fixtures with inspected rollout
records. Separate observed model probes establish real image capability.
"""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import literature, reading, research_decisions as decisions
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_codex_visual_transport as visual, review_visual_inputs
from research_harness import review_source_complete as complete
from research_harness.acquisition import acquire_fulltext
from research_harness.direction_sources import build_inline_passages
from research_harness.evidence import digest
from research_harness.errors import ResearchError


class VisualResultDeliveryTests(unittest.TestCase):
    def setUp(self):
        import test_research_complete_source_continuation as fixtures
        from research_fixtures import client
        from test_research_binary_pdf import authored_pdf
        original = protocol.canonical_prompt
        def old_prompt(role, **options):
            if role == 'result' and options.get('orchestration') == 'problem_creation' and 'version' not in options:
                options['version'] = 2
            return original(role, **options)
        previous_contract = patch.object(protocol, 'canonical_prompt', side_effect=old_prompt)
        previous_contract.start()
        self.addCleanup(previous_contract.stop)
        self.native = fixtures.NativeCompleteSourceTests()
        self.addCleanup(self.native.doCleanups)
        self.native.setUp()
        self.case = case = self.native.fixture
        self.store, self.artifacts = case.store, case.artifacts
        active = patch.object(visual, 'runtime', return_value={'adapter': visual.ADAPTER, 'fixture': 'visual-result-test'})
        active.start()
        self.addCleanup(active.stop)
        version = self.native.second_version
        http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, authored_pdf())])
        self.pdf = acquire_fulltext(self.store, version, 'https://arxiv.org/pdf/' + version[6:], http=http,
            extractor=lambda _: {'status': 'extracted', 'text': 'Authored page. References: none.\f'},
            expected_revision=self.store.revision, request_id='native-visual-pdf')['capture']
        bundle = case.bundle(version, capture=self.pdf, bundle_id='visual-pdf-bundle')
        self.pdf_link = {'version_id': version, 'source_id': self.pdf['source_id'], 'artifact': self.pdf['original'],
                         'locator': {'kind': 'pdf', 'page_index': 0, 'printed_page': '1', 'region': [0, 0, 1, 1]}}
        bundle['units'].append({'id': 'figure', 'kind': 'figure', 'required': True, 'link': self.pdf_link})
        case.mutate(literature.import_bundle, bundle)
        case.mutate(reading.record_reading, case.full_note(bundle, note_id='visual-pdf-reading'))
        self.html = case.capture(self.native.version,
            body='The bounded HTML passage is supplied exactly. ' * 20 + 'References: none.')
        html_bundle = case.bundle(self.native.version, capture=self.html, bundle_id='bounded-html-bundle')
        self.html_link = case.link(self.native.version, self.html['source_id'], self.html['original'],
                                   'The bounded HTML passage is supplied exactly.', kind='span')
        html_bundle['units'].append({'id': 'bounded-original', 'kind': 'text', 'required': True, 'link': self.html_link})
        case.mutate(literature.import_bundle, html_bundle)
        case.mutate(reading.record_reading, case.full_note(html_bundle, note_id='bounded-html-reading'))
        records = case.read_records()
        notes = [records['reading'][identifier] for identifier in ('visual-pdf-reading', 'bounded-html-reading')]
        case.expected['readings'] = [{'id': note['id'], 'version_id': note['version_id'], 'digest': digest(note),
                                     'depth': 'fulltext', 'reading': note} for note in notes]
        passages = []
        for passage, data in build_inline_passages(records, self.artifacts, notes):
            if data is not None:
                self.assertEqual(self.artifacts.put(data, passage['excerpt']['media_type']), passage['excerpt'])
            passages.append(passage)
        case.expected['passages'] = passages
        first = self.native.assign('complete-first', source_delivery=complete.MODE)
        self.native.invoke(first['id'], self.native.output([self.native.second_request]))
        prior = self.native.assign('legacy-complete-result', prior=first['id'])
        output = case.build_review_output('result')
        output['value']['assurances'][-1]['status'] = 'not_assessed'
        self.native.invoke(prior['id'], output)
        case.mutate(decisions.record_value_review,
            dict(output, id='review-legacy', dossier_id=None, assignment_id=prior['id']))
        previous_contract.stop()
        case.mutate(protocol.record_route, {'id': 'visual-route', 'adapter': visual.ADAPTER,
            'model': 'fixture-model', 'endpoint': 'codex://local',
            'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}})
        def respond_probe(route, request, credential):
            positive = case.read_records()['review_probe_invocation']['visual-probe']['positive_controls']
            return self.response(route, request, {key: positive[key] for key in ('allowed_control', 'evidence_control')}
                                 | {'excluded_controls': []})
        with patch.object(transport, 'send_request', side_effect=respond_probe):
            case.mutate(protocol.probe_route, {'id': 'visual-probe', 'route_id': 'visual-route'}, credential='test-only')
        self.assignment = case.mutate(protocol.record_assignment, {'id': 'visual-result', 'route_id': 'visual-route',
            'dossier_id': None, 'role': 'result', 'reviewer_id': 'first', 'author_id': 'author',
            'context': {'prior_assignment_id': prior['id']}})['result']

    def response(self, route, request, output):
        response = self.case.build_provider_response(output)
        directory = '/fixture-visual-review'
        events = [
            {'type': 'session_meta', 'payload': {'id': response['id'], 'source': 'exec',
                'base_instructions': {'text': request['instructions']}}},
            {'type': 'turn_context', 'payload': {'model': route['model'], 'effort': 'high', 'cwd': directory}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': visual.native_user_parts(request, directory)}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': json.dumps(output)}]}},
        ]
        excluded = review_visual_inputs.control_image('0' * 32)
        evidence = visual.inspect_rollout(events, request, response['id'], directory, [], excluded_images=[excluded])
        self.assertTrue(evidence['input_verified'], evidence['issues'])
        evidence.update(inspected=True, route_digest=digest(route), runtime=visual.runtime(), event_digest=digest(events))
        response['context_evidence'] = evidence
        return response

    def observe(self, evidence):
        output = self.case.build_review_output('result')
        output['value']['evidence'].extend(evidence)
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          self.response(route, request, output)):
            self.case.mutate(protocol.invoke_assignment, {'id': 'actual-visual-attempt',
                'assignment_id': self.assignment['id']}, credential='test-only')
        records = self.case.read_records()
        attempt = records['review_attempt']['actual-visual-attempt']
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, self.assignment, attempt))
        return output

    def record(self, output):
        return self.case.mutate(decisions.record_value_review,
            dict(output, id='recorded-visual-result', dossier_id=None, assignment_id=self.assignment['id']))['result']

    def test_native_page_and_bounded_html_links_record_and_pass_actual_delivery_gates(self):
        evidence = [{'kind': 'source', 'link': self.pdf_link}, {'kind': 'source', 'link': self.html_link}]
        output = self.observe(evidence)
        review = self.record(output)
        records = self.case.read_records()
        self.assertEqual(decisions.find_undelivered_review_references(records, self.artifacts, review), [])
        self.assertEqual(decisions._assess_problem_coverage(records, self.artifacts, [review],
                         self.case.expected, digest(self.case.expected)), [])
        from research_harness.source_links import span_locator
        wider = copy.deepcopy(review)
        link = copy.deepcopy(self.html_link)
        original = self.artifacts.read(link['artifact']).decode()
        link['locator'] = span_locator(original, 0, len(original))
        wider['payload']['value']['evidence'].append({'kind': 'source', 'link': link})
        self.assertTrue(decisions.find_undelivered_review_references(records, self.artifacts, wider))

    def test_omitted_whole_pdf_reference_is_refused_by_public_recording_and_final_delivery_gate(self):
        output = self.observe([self.pdf['original']])
        with self.assertRaises(ResearchError) as failure:
            self.record(output)
        self.assertEqual(failure.exception.code, 'review_evidence_undelivered')
        review = {'subject': self.assignment['subject'], 'assignment_id': self.assignment['id'], 'payload': output}
        missing = decisions.find_undelivered_review_references(self.case.read_records(), self.artifacts, review)
        self.assertTrue(any(row.get('sha256') == self.pdf['original']['sha256'] for row in missing))

    def test_omitted_whole_html_reference_is_refused_even_when_its_exact_span_was_supplied(self):
        output = self.observe([self.html['original']])
        with self.assertRaises(ResearchError) as failure:
            self.record(output)
        self.assertEqual(failure.exception.code, 'review_evidence_undelivered')
        review = {'subject': self.assignment['subject'], 'assignment_id': self.assignment['id'], 'payload': output}
        missing = decisions.find_undelivered_review_references(self.case.read_records(), self.artifacts, review)
        self.assertTrue(any(row.get('sha256') == self.html['original']['sha256'] for row in missing))


if __name__ == '__main__':
    unittest.main()
