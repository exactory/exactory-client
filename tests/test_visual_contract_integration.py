"""Visual contract integration preserves text routes and credits only actual images."""

import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from research_harness import review_input_projection as projection
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_visual_inputs as visual
from research_harness import research_decisions as decisions
from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError


class VisualContractIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.data = visual.control_image('native page control')
        self.image = describe_artifact(self.data, 'image/png')
        self.pdf = describe_artifact(b'%PDF-1.4\nAn untransmitted original.\n', 'application/pdf')
        self.packet = {'protocol': 'exactory-problem-review-v2', 'role': 'result', 'reviewer_id': 'first',
                       'dossier_id': None, 'subject': {'kind': 'problem', 'statement_id': 'statement-1'},
                       'digest': '1' * 64, 'evidence': [
                           {'artifact': self.image, 'encoding': 'base64', 'delivery': 'attached-exact-image-bytes'},
                           {'artifact': self.pdf, 'encoding': 'base64', 'content': 'not actual PDF bytes'}],
                       'visual_source_delivery': {'protocol': 'pdf-page-images-v1', 'pages': [{'image': self.image}]}}

    def message(self, packet):
        return {'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(packet)}

    def request(self, messages):
        return {'input': messages, 'exactory_image_inputs': [{'artifact': self.image,
                'data_url': 'data:image/png;base64,' + base64.b64encode(self.data).decode()}]}

    def project(self, request):
        helper = getattr(projection, 'packets_from_visual_request', None)
        self.assertTrue(callable(helper), 'The visual route needs an exact actual-image projection.')
        return helper(request)

    def test_actual_native_png_is_credited_without_crediting_its_original_pdf(self):
        request = self.request([self.message(self.packet)])
        before = copy.deepcopy(request)
        packet = self.project(request)[0]
        self.assertEqual(packet['evidence'][0]['content'], base64.b64encode(self.data).decode())
        self.assertNotIn('content', packet['evidence'][1])
        self.assertEqual(request, before)

    def test_images_are_bound_to_exact_logical_message_indices(self):
        prior = copy.deepcopy(self.packet)
        prior.pop('visual_source_delivery')
        request = self.request([self.message(prior), {'role': 'assistant', 'content': 'Preserved output.'},
                                self.message(self.packet)])
        packets = self.project(request)
        self.assertNotIn('content', packets[0]['evidence'][0])
        self.assertIn('content', packets[1]['evidence'][0])
        request['input'][0] = self.message(self.packet)
        self.assertTrue(all(row['evidence'][0].get('content') for row in self.project(request)))

    def test_manifest_without_actual_image_bytes_is_refused(self):
        request = self.request([self.message(self.packet)])
        request['exactory_image_inputs'] = []
        with self.assertRaises(ResearchError):
            self.project(request)

    def probe_prompt(self, adapter):
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory))
            route = {'id': 'route', 'adapter': adapter}
            records = {'review_route': {'route': route}}
            def start_only(store, payload, operation, start, finish, **options):
                return start(records, payload)
            with patch.object(protocol, '_invoke_once', side_effect=start_only), \
                 patch.object(transport, 'request_body', return_value={'input': []}) as builder:
                protocol.probe_route(store, {'id': 'probe', 'route_id': 'route'},
                                     expected_revision=1, request_id='probe-test')
            return builder.call_args.args[1]

    def test_visual_probe_requests_controls_from_first_and_second_images(self):
        prompt = self.probe_prompt('codex_cli_visual_v1')
        self.assertIn('first image', prompt)
        self.assertIn('second image', prompt)
        for key in ('allowed_control', 'evidence_control', 'excluded_controls'):
            self.assertIn(key, prompt)
        self.assertIn('additional control codes', prompt)
        self.assertIn('exactly 32 lowercase hexadecimal characters', prompt)
        self.assertIn('padding text are not control codes', prompt)

    def test_original_probe_prompt_stays_byte_exact_on_original_routes(self):
        expected = ('Return JSON with allowed_control and evidence_control copied exactly from the packet, '
                    'and excluded_controls containing any other control strings you received, or an empty array. '
                    'Use only supplied evidence. This is an operational input-boundary probe.')
        for adapter in ('openai_responses_v1', 'codex_cli_v1', 'codex_native_v1'):
            with self.subTest(adapter=adapter):
                self.assertEqual(self.probe_prompt(adapter), expected)

    def contract_fixture(self, *, image):
        import test_research_problem_reviews as fixtures
        case = fixtures.ProblemReviewTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.observe_bar_reviews()
        assignment = case.assign('result', 'first')
        records = case.read_records()
        assignment = records['review_assignment'][assignment['id']]
        records['review_route'][assignment['route_id']]['adapter'] = 'codex_cli_visual_v1'
        packet = json.loads(case.artifacts.read(assignment['packet']))
        output = case.build_review_output('result')
        for cited in output['value']['evidence']:
            packet['evidence'].append({'artifact': cited, 'encoding': 'utf-8',
                                       'content': case.artifacts.read(cited).decode()})
        reference = (case.artifacts.put(self.data, 'image/png') if image else
                     case.artifacts.put(b'%PDF-1.4\nUntransmitted original.\n', 'application/pdf'))
        packet['evidence'].append({'artifact': reference, 'encoding': 'base64', 'delivery': 'attached-exact-image-bytes'})
        request = {'input': [], 'exactory_image_inputs': []}
        if image:
            packet['visual_source_delivery'] = {'protocol': 'pdf-page-images-v1', 'pages': [{'image': reference}]}
            request['exactory_image_inputs'].append({'artifact': reference,
                'data_url': 'data:image/png;base64,' + base64.b64encode(self.data).decode()})
        request['input'].append(self.message(packet))
        assignment['packet'] = case.artifacts.put(json.dumps(packet).encode(), 'application/json')
        output['value']['evidence'].append(reference)
        return case, records, assignment, request, output

    def test_visual_output_contract_refuses_an_omitted_whole_pdf_reference(self):
        case, records, assignment, request, output = self.contract_fixture(image=False)
        with patch.object(transport, 'request_body', return_value=request):
            with self.assertRaises(ResearchError) as failure:
                decisions.validate_review_contract(records, case.artifacts, assignment, output)
        self.assertEqual(failure.exception.code, 'review_evidence_undelivered')

    def test_visual_output_contract_accepts_exact_attached_png_reference(self):
        case, records, assignment, request, output = self.contract_fixture(image=True)
        with patch.object(transport, 'request_body', return_value=request):
            decisions.validate_review_contract(records, case.artifacts, assignment, output)


if __name__ == '__main__':
    unittest.main()
