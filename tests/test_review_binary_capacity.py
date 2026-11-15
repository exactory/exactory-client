"""Binary admission uses provider rules, never observations of other documents."""
import base64
import copy
import struct
import tempfile
import unittest
from unittest.mock import patch

from research_harness import review_route_capacity as capacity, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.artifacts import ArtifactStore
from test_review_route_capacity import build_route
from capacity_evidence_fixtures import build_capacity_runtime, build_capacity_prior
from research_harness import review_capacity_evidence as capacity_evidence


def build_request(text='', data=b'%PDF-1.4\n', kind='input_file'):
    part = (dict(type=kind, filename='fixture.pdf', file_data='data:application/pdf;base64,')
            if kind == 'input_file' else dict(type=kind, image_url='data:image/png;base64,'))
    field = 'file_data' if kind == 'input_file' else 'image_url'
    part[field] += base64.b64encode(data).decode()
    return dict(instructions='JSON', input=[dict(role='user', content=[dict(type='input_text', text=text), part])])


def build_pdf(characters, image=False):
    content = b'BT /F1 12 Tf 20 700 Td (' + b'x'*characters + b') Tj ET'
    resources = b'/Font << /F1 5 0 R >>'
    if image:
        resources += b' /XObject << /Im0 6 0 R >>'
        content += b' q 10 0 0 10 20 20 cm /Im0 Do Q'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << '+resources+b' >> >>',
        b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    if image:
        objects.append(b'<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream')
    data, offsets = b'%PDF-1.4\n', []
    for index, body in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode()+b' 0 obj\n'+body+b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 '+str(len(objects)+1).encode()+b'\n0000000000 65535 f \n'
    data += b''.join(('%010d 00000 n \n' % offset).encode() for offset in offsets)
    return data+b'trailer\n<< /Size '+str(len(objects)+1).encode()+b' /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'


class BinaryCapacityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(directory.name)

    def test_each_binary_adapter_reports_rules_and_sources(self):
        for adapter in ('openai_responses_v1', 'codex_cli_visual_v1'):
            with self.subTest(adapter=adapter):
                result = capacity.describe_route_capacity(build_route(adapter, 'gpt-6-astra'))
                self.assertIsInstance(result['binary_inputs'], dict)
                self.assertIn('image', result['binary_inputs'])
                self.assertIn('token_rule', result['binary_inputs']['image'])
        pdf = capacity.describe_route_capacity(dict(build_route('openai_responses_v1', 'gpt-6-astra'),
            endpoint='https://api.openai.com/v1/responses'))['binary_inputs']['pdf']
        self.assertIsNone(pdf['token_rule'])
        self.assertEqual(pdf['input_limits']['bytes']['value'], 50000000)
        self.assertTrue(pdf['input_limits']['bytes']['exclusive'])
        self.assertIn('https://', pdf['request_limits']['bytes']['url'])

    def test_equal_page_pdfs_without_rule_keep_same_estimate_and_no_bound(self):
        route = build_route('openai_responses_v1', 'gpt-6-astra')
        measures = [capacity.measure_request(route, build_request(data=data))
                    for data in (build_pdf(100), build_pdf(10000, image=True))]
        for measure in measures:
            self.assertFalse(measure['fits'])
            self.assertEqual(measure['reason'], 'binary_input_without_bound')
            self.assertIsNone(measure['tokens_upper'])
        self.assertEqual(measures[0]['binary_inputs'][0]['estimate'], measures[1]['binary_inputs'][0]['estimate'])

    def test_an_explicit_fixed_binary_rule_bounds_the_declared_backend_only(self):
        route = dict(build_route('openai_responses_v1', 'synthetic-binary-reader', output=1000),
                     runtime=build_capacity_runtime('openai_responses_v1'))
        request = build_request(data=build_pdf(10000, image=True))
        self.assertIsNone(capacity.measure_request(route, request)['tokens_upper'])
        from visual_capacity_fixtures import install_bounded_binary_backend
        install_bounded_binary_backend(self, [route])
        with patch.object(capacity_evidence, 'CAPACITY_PRIORS', (build_capacity_prior(route, 100000),)):
            report = capacity.describe_route_capacity(route)
        with patch.object(capacity, 'describe_route_capacity', return_value=report):
            measured = capacity.measure_request(route, request)
        self.assertTrue(measured['fits'])
        self.assertEqual(measured['binary_tokens_upper'], 50000)
        self.assertEqual(measured['tokens_upper'], measured['text_tokens_upper']+50000)
        another = dict(route, endpoint='https://another.example/responses')
        self.assertIsNone(capacity.measure_request(another, request)['tokens_upper'])

    def test_individually_valid_attachments_pass_the_combined_capacity(self):
        from visual_capacity_fixtures import install_bounded_visual_backend
        for adapter in ('openai_responses_v1', 'codex_cli_visual_v1'):
            with self.subTest(adapter=adapter):
                route = dict(build_route(adapter, 'gpt-6-astra', output=128000),
                    endpoint='https://api.openai.com/v1/responses' if adapter == 'openai_responses_v1' else 'codex://local',
                    runtime=build_capacity_runtime(adapter, fixture='binary-input-limit'))
                if adapter == 'codex_cli_visual_v1':
                    install_bounded_visual_backend(self, [route])
                data = b'\x89PNG\r\n\x1a\n' + b'\x00'*8 + struct.pack('>II', 1024, 1024)
                text_size = 100000 if adapter == 'codex_cli_visual_v1' else 50000
                request = build_request('x' * text_size, data, 'input_image')
                request['input'][0]['content'][-1]['detail'] = 'high'
                if adapter == 'codex_cli_visual_v1':
                    request = dict(instructions='JSON', input=[dict(role='user', content='x'*text_size)],
                        exactory_image_inputs=[dict(data_url=request['input'][0]['content'][-1]['image_url'])])
                report = capacity.describe_route_capacity(route)
                report['input_tokens']['value'] = 55000
                assignment = dict(id='assignment', role='standalone', route_id='route', dossier_id=None,
                    author_id='author', reviewer_id='reviewer', context={})
                records = dict(review_route={'route': route})
                with patch.object(capacity, 'describe_route_capacity', return_value=report), \
                        patch.object(transport, 'runtime', return_value=route['runtime']), \
                        patch.object(transport, 'send_request') as send:
                    small = capacity.measure_request(route, request)
                    capacity.require_covered_assignment(records, self.artifacts, assignment, dict(evidence=[]), 'JSON', request)
                    if adapter == 'codex_cli_visual_v1':
                        request['exactory_image_inputs'] *= 6
                    else:
                        request['input'][0]['content'] += [copy.deepcopy(request['input'][0]['content'][-1]) for _ in range(5)]
                    large = capacity.measure_request(route, request)
                    with self.assertRaises(ResearchError) as caught:
                        capacity.require_covered_assignment(records, self.artifacts, assignment, dict(evidence=[]), 'JSON', request)
                    self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
                    self.assertEqual(caught.exception.details['reason'], 'binary_input_over_measure')
                    self.assertEqual(caught.exception.details['measure'], large)
                    send.assert_not_called()
                self.assertTrue(small['fits'])
                self.assertGreater(small['text_tokens_upper'], small['token_limit']/2)
                self.assertFalse(large['fits'])
                self.assertEqual(large['reason'], 'binary_input_over_measure')
                self.assertEqual(large['tokens_upper'], large['text_tokens_upper'] + large['binary_tokens_upper'])
                self.assertEqual(large['output_reservation_tokens'], 128000)

    def test_visual_transport_bound_precedes_png_validation(self):
        route = build_route('codex_cli_visual_v1', 'gpt-6-astra')
        for data in (b'x' * (33554432 + 1),
                     b'\x89PNG\r\n\x1a\n' + b'\x00'*8 + struct.pack('>II', 4097, 4097)):
            request = dict(instructions='JSON', input=[dict(role='user', content='{}')],
                           exactory_image_inputs=[dict(artifact=dict(size=len(data), media_type='image/png'),
                           data_url='data:image/png;base64,' + base64.b64encode(data).decode())])
            result = capacity.measure_request(route, request)
            self.assertFalse(result['fits'])
            self.assertEqual(result['reason'], 'binary_input_over_measure')

    def test_provider_count_and_per_file_limits_are_independent_of_tokens(self):
        route = dict(build_route('openai_responses_v1', 'gpt-6-astra'),
                     endpoint='https://api.openai.com/v1/responses')
        data = b'\x89PNG\r\n\x1a\n' + b'\x00'*8 + struct.pack('>II', 1, 1)
        request = build_request(data=data, kind='input_image')
        request['input'][0]['content'][-1]['detail'] = 'low'
        request['input'][0]['content'] *= 1501
        measure = capacity.measure_request(route, request)
        self.assertFalse(measure['fits'])
        self.assertEqual(measure['reason'], 'binary_input_over_measure')
        self.assertLessEqual(measure['tokens_upper'], measure['token_limit'])
        self.assertTrue(all(not row['over_limit'] for row in measure['binary_inputs']))
        report = capacity.describe_route_capacity(route)
        pdf = build_pdf(100)
        report['binary_inputs']['pdf']['input_limits']['bytes']['value'] = len(pdf)
        with patch.object(capacity, 'describe_route_capacity', return_value=report):
            measure = capacity.measure_request(route, build_request(data=pdf))
        self.assertEqual(measure['reason'], 'binary_input_over_measure')

    def test_serialized_image_bytes_and_combined_pdf_bytes_are_checked_independently(self):
        import json
        route = dict(build_route('openai_responses_v1', 'gpt-6-astra'), endpoint='https://api.openai.com/v1/responses')
        image = b'\x89PNG\r\n\x1a\n' + b'\x00'*8 + struct.pack('>II', 1, 1)
        request = build_request(data=image, kind='input_image')
        report = capacity.describe_route_capacity(route)
        limit = report['binary_inputs']['image']['request_limits']['bytes']
        limit['value'] = len(json.dumps(request, ensure_ascii=False).encode('utf-8'))
        with patch.object(capacity, 'describe_route_capacity', return_value=report):
            self.assertTrue(capacity.measure_request(route, request)['fits'])
            limit['value'] -= 1
            measured = capacity.measure_request(route, request)
        self.assertEqual(measured['reason'], 'binary_input_over_measure')
        self.assertLess(measured['tokens_upper'], measured['token_limit'])
        self.assertFalse(measured['binary_inputs'][0]['over_limit'])
        pdf = build_pdf(100)
        request = build_request(data=pdf)
        request['input'][0]['content'].append(copy.deepcopy(request['input'][0]['content'][-1]))
        report = capacity.describe_route_capacity(route)
        report['binary_inputs']['image']['request_limits']['bytes']['value'] = 1
        with patch.object(capacity, 'describe_route_capacity', return_value=report):
            self.assertEqual(capacity.measure_request(route, request)['reason'], 'binary_input_without_bound')
        report['binary_inputs']['pdf']['request_limits']['bytes']['value'] = len(pdf)*2-1
        with patch.object(capacity, 'describe_route_capacity', return_value=report):
            measured = capacity.measure_request(route, request)
        self.assertEqual(measured['reason'], 'binary_input_over_measure')
        self.assertTrue(all(not row['over_limit'] for row in measured['binary_inputs']))

    def test_visual_estimate_never_admits_a_small_png(self):
        from test_research_codex_visual_transport import png
        route = build_route('codex_cli_visual_v1', 'gpt-6-astra')
        data = png(90)
        request = dict(instructions='JSON', input=[dict(role='user', content='{}')],
                       exactory_image_inputs=[dict(artifact=dict(size=len(data), media_type='image/png'),
                       data_url='data:image/png;base64,' + base64.b64encode(data).decode())])
        measure = capacity.measure_request(route, request)
        self.assertFalse(measure['fits'])
        self.assertEqual(measure['reason'], 'binary_input_without_bound')
        estimate = measure['binary_inputs'][0]['estimate']
        self.assertEqual(estimate['largest_request_input_tokens'], 364458)
        self.assertEqual(estimate['tokens_per_image'], 364458 / 19)
        self.assertEqual(estimate['basis'], 'whole_request_usage_including_text')

    def test_another_endpoint_or_model_cannot_borrow_the_official_image_rule(self):
        for route in (build_route('openai_responses_v1', 'gpt-6-astra'),
                      dict(build_route('openai_responses_v1', 'unlisted'), endpoint='https://api.openai.com/v1/responses')):
            with self.subTest(route=route):
                rules = capacity.describe_route_capacity(route)['binary_inputs']
                self.assertIsNone(rules['image']['token_rule'])
                if route['endpoint'] != 'https://api.openai.com/v1/responses':
                    self.assertEqual(rules['image']['request_limits'], {})
                    self.assertEqual(rules['pdf']['input_limits'], {})

    def test_original_detail_rejects_a_png_above_the_documented_patch_ceiling(self):
        route = dict(build_route('openai_responses_v1', 'gpt-6-astra'), endpoint='https://api.openai.com/v1/responses')
        data = b'\x89PNG\r\n\x1a\n' + b'\x00'*8 + struct.pack('>II', 6400, 6400)
        result = capacity.measure_request(route, build_request(data=data, kind='input_image'))
        self.assertEqual(result['reason'], 'binary_input_over_measure')
        self.assertFalse(result['fits'])

    def test_a_short_output_reservation_does_not_raise_the_documented_input_maximum(self):
        for model in ('gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna'):
            with self.subTest(model=model):
                result = capacity.describe_route_capacity(build_route('openai_responses_v1', model, output=1000))
                self.assertEqual(result['input_tokens']['value'], 922000)
                self.assertIn('/models/' + model, result['input_tokens']['url'])

    def test_verified_visual_observations_stay_estimates_without_an_admission_rule(self):
        import json
        from pathlib import Path
        table = json.loads((Path(__file__).parent / 'fixtures' / 'binary_capacity_measurements.json').read_text())
        self.assertEqual(table['source'], capacity.MEASUREMENT_SOURCE)
        self.assertEqual(len(table['rows']), 6)
        report = capacity.describe_route_capacity(build_route('codex_cli_visual_v1', 'gpt-6-astra'))['binary_inputs']['image']
        self.assertIsNone(report['token_rule'])
        self.assertEqual(report['estimate']['largest_request_input_tokens'], max(row['input_tokens'] for row in table['rows']))
        self.assertEqual(report['estimate']['largest_bytes'], max(row['largest_bytes'] for row in table['rows']))
        self.assertEqual(report['estimate']['largest_pixels'], max(row['largest_pixels'] for row in table['rows']))
        for row in table['rows']:
            self.assertFalse(row['covered'])
            self.assertIsNone(row['tokens_upper'])
            self.assertEqual(row['images'], 19)

    def test_model_documentation_is_not_borrowed_by_another_adapter(self):
        for adapter, model in (('claude_cli_v1', 'gpt-6-astra'), ('openai_responses_v1', 'claude-opus-5-5')):
            with self.subTest(adapter=adapter):
                result = capacity.describe_route_capacity(build_route(adapter, model))
                self.assertFalse(result['listed'])
                self.assertEqual(result['request_measure'], 'bytes')
