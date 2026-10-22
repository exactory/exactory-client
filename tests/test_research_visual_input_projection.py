"""Visual source projection consumes only exact images in the actual request."""

import base64
import copy
import json
import unittest

from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError
from research_harness.review_transport import PACKET_LABEL


class VisualInputProjectionTests(unittest.TestCase):
    def request(self):
        from research_harness.review_visual_inputs import control_image
        images = [control_image(code) for code in ('01234567', '89abcdef')]
        refs = [describe_artifact(data, 'image/png') for data in images]
        packet = {'visual_source_delivery': {'protocol': 'pdf-page-images-v1',
                  'pages': [{'image': ref} for ref in refs]}, 'evidence': []}
        return {'input': [{'role': 'user', 'content': PACKET_LABEL + json.dumps(packet)}],
                'exactory_image_inputs': [{'artifact': ref, 'data_url': 'data:image/png;base64,' + base64.b64encode(data).decode()}
                                         for ref, data in zip(refs, images)]}

    def test_actual_ordered_images_project_and_descriptor_claims_do_not(self):
        from research_harness.review_visual_inputs import project_request
        request = self.request()
        projected = project_request(request)
        self.assertEqual(projected['messages'], request['input'])
        self.assertEqual(projected['packet_images'][0], [row['artifact']['sha256'] for row in request['exactory_image_inputs']])
        for row, supplied in zip(projected['images'], request['exactory_image_inputs']):
            self.assertEqual(base64.b64decode(row['content']), base64.b64decode(supplied['data_url'].split(',', 1)[1]))
        for change in ('missing', 'extra', 'order', 'data', 'descriptor'):
            broken = copy.deepcopy(request)
            if change == 'missing': broken['exactory_image_inputs'].pop()
            if change == 'extra': broken['exactory_image_inputs'].append(broken['exactory_image_inputs'][0])
            if change == 'order': broken['exactory_image_inputs'].reverse()
            if change == 'data': broken['exactory_image_inputs'][0]['data_url'] = broken['exactory_image_inputs'][1]['data_url']
            if change == 'descriptor': broken['exactory_image_inputs'][0].pop('data_url')
            with self.subTest(change=change), self.assertRaises(ResearchError):
                project_request(broken)

    def test_historical_images_need_explicit_reattachment(self):
        from research_harness.review_visual_inputs import project_request
        request = self.request()
        current = {'visual_source_delivery': {'protocol': 'pdf-page-images-v1', 'pages': []}, 'evidence': []}
        request['input'].extend([{'role': 'assistant', 'content': '{"result":"prior"}'},
                                {'role': 'user', 'content': PACKET_LABEL + json.dumps(current)}])
        self.assertEqual(len(project_request(request)['packet_images'][0]), 2)
        request['exactory_image_inputs'] = []
        with self.assertRaises(ResearchError):
            project_request(request)

    def test_actual_packets_do_not_promote_scoped_passages_to_whole_artifacts(self):
        from research_harness.review_supplied_evidence import is_received
        reference = describe_artifact(b'An exact original body.', 'text/html')
        mappings = [{'origin': {'artifact_descriptor': {'sha256': reference['sha256']}}}]
        self.assertFalse(is_received({}, reference, {}, mappings, artifacts=None, packets=[{'evidence': []}]))
        self.assertTrue(is_received({}, reference, {}, mappings, artifacts=None))

    def test_probe_codes_are_image_only_and_all_nineteen_images_are_bound(self):
        from research_harness.review_visual_inputs import probe_request
        controls = {'allowed_control': '0123456789abcdef0123456789abcdef',
                    'evidence_control': 'fedcba9876543210fedcba9876543210', 'evidence': 'Permitted primary evidence.'}
        route = {'model': 'gpt-6-astra', 'configuration': {'max_output_tokens': 128}}
        request = probe_request(route, 'Read images 1 and 2.', controls)
        self.assertEqual(len(request['exactory_image_inputs']), 19)
        visible = json.loads(request['input'][0]['content'][len(PACKET_LABEL):])
        self.assertNotIn('allowed_control', visible)
        self.assertNotIn('evidence_control', visible)
        self.assertEqual(visible['code_image_indices'], {'allowed_control': 1, 'evidence_control': 2})
        text = json.dumps(request['input']) + request['instructions']
        for field in ('allowed_control', 'evidence_control'):
            self.assertNotIn(controls[field], text)
        self.assertEqual(request, probe_request(route, 'Read images 1 and 2.', controls))
