"""Recorded delivery keeps its original MIME decisions; parsing accepts base type spellings."""

import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path

from research_harness import bar_sources, problem_openness_sources, review_source_complete
from research_harness import problem_openness_reviews, review_protocol, review_transport
from research_harness.acquisition import import_response
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.html_visuals import validate_visual
from research_harness.imports import parse_mapped
from research_harness.review_input_projection import packets_from_request
from research_harness.source_links import read_locator, span_locator
from research_harness.storage import Store


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2'
                       'FzhVAAAAAElFTkSuQmCC')
PDF = b'%PDF-1.4\nAn authored ASCII PDF.\n%%EOF\n'


class MediaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.artifacts = ArtifactStore(self.root)

    def packet(self, entry):
        return {'protocol': 'exactory-problem-review-v2', 'role': 'result', 'reviewer_id': 'first',
                'dossier_id': None, 'subject': {'kind': 'problem', 'statement_id': 'statement-1'},
                'digest': '1' * 64, 'evidence': [entry]}

    def test_recorded_text_uses_the_reported_type_and_actual_bytes(self):
        for media in ('Binary/Octet-Stream', 'binary/octet-stream; charset=utf-8',
                      'APPLICATION/OCTET-STREAM', 'Application/PDF'):
            reference = self.artifacts.put(PDF, media)
            entry = {'artifact': reference, 'encoding': 'utf-8', 'content': PDF.decode()}
            packet = self.packet(entry)
            request = {'input': [{'role': 'user',
                                 'content': review_transport.PACKET_LABEL + json.dumps(packet)}]}
            before = copy.deepcopy(request)
            for native_attachments in (False, True):
                with self.subTest(media=media, native_attachments=native_attachments):
                    projected, = packets_from_request(request, native_attachments=native_attachments)
                    self.assertEqual(projected['evidence'], [entry])
                    self.assertEqual(request, before)

    def test_responses_rebuild_keeps_the_recorded_image_data_url_type(self):
        reference = self.artifacts.put(PNG, 'image/PNG')
        entry, = review_protocol._evidence(self.artifacts, {'image': reference})
        route = {'adapter': 'openai_responses_v1', 'model': 'fixture-model',
                 'endpoint': 'https://api.example.test/v1/responses',
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 600}}
        request = review_transport.request_body(route, 'Review.', self.packet(entry))
        parts = request['input'][-1]['content']
        self.assertEqual(parts[1]['image_url'], 'data:image/PNG;base64,' + entry['content'])
        projected, = packets_from_request(request, native_attachments=True)
        self.assertEqual(projected['evidence'][0]['content'], entry['content'])
        self.assertEqual(projected['evidence'][0]['artifact'], reference)

    def test_attachment_type_must_equal_the_recorded_construction_type(self):
        reference = self.artifacts.put(PNG, 'image/PNG')
        entry = {'artifact': reference, 'encoding': 'base64', 'delivery': 'attached-exact-bytes'}
        for media in ('image/png', ' Image/PNG ', 'image/PNG; profile=fixture'):
            request = {'input': [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': review_transport.PACKET_LABEL + json.dumps(self.packet(entry))},
                {'type': 'input_image', 'image_url': 'data:' + media + ';base64,'
                 + base64.b64encode(PNG).decode('ascii')}]}]}
            with self.subTest(media=media):
                projected, = packets_from_request(request, native_attachments=True)
                self.assertNotIn('content', projected['evidence'][0])

    def test_complete_text_readers_use_the_construction_decision(self):
        for media, binary in (('application/pdf', True), ('binary/octet-stream', True),
                              ('application/octet-stream', True), ('image/PNG', True),
                              ('Binary/Octet-Stream', False), ('binary/octet-stream; charset=utf-8', False),
                              ('APPLICATION/OCTET-STREAM', False), ('Application/PDF', False), ('Image/PNG', False)):
            reference = self.artifacts.put(PDF, media)
            entry = {'artifact': reference, 'encoding': 'utf-8', 'content': PDF.decode()}
            for reader in (review_source_complete._complete, problem_openness_sources._complete):
                with self.subTest(media=media, reader=reader.__module__):
                    if binary:
                        with self.assertRaises(ResearchError):
                            reader(entry)
                    else:
                        result = reader(entry)
                        self.assertEqual(result.encode() if isinstance(result, str) else result, PDF)

    def test_openness_actual_input_does_not_promote_binary_bytes_declared_as_text(self):
        for media, binary in (('application/pdf', True), ('binary/octet-stream', True),
                              ('Application/PDF', False), ('Binary/Octet-Stream', False)):
            reference = self.artifacts.put(PDF, media)
            packet = dict.fromkeys(('root', 'objective_id', 'contract', 'subject', 'subject_digest',
                                    'claim_digest', 'reviewer'), 'fixture')
            packet.update(scientific_context={}, evidence=[{'digest': reference['sha256'], 'artifact': reference}])
            supplied = {'native': packet, 'evidence': [
                {'artifact': reference, 'encoding': 'utf-8', 'content': PDF.decode()}]}
            alternate = self.artifacts.put(b'%PDF-1.4\nAn alternate source.\n%%EOF\n', 'application/pdf')
            supplied['evidence'].append({'artifact': alternate, 'encoding': 'omitted', 'content': '',
                                        'delivery': 'alternate-main-representation'})
            request = {'input': [{'role': 'user',
                                 'content': review_transport.PACKET_LABEL + json.dumps(supplied)}]}
            saved = self.artifacts.put(json.dumps(request).encode(), 'application/json')
            records = {'review_attempt': {'attempt': {'request': saved}}}
            with self.subTest(media=media):
                args = (records, self.artifacts, {'attempt_id': 'attempt'}, packet, {reference['sha256']})
                if binary:
                    with self.assertRaises(ResearchError) as raised:
                        problem_openness_reviews.actual_delivery(*args)
                    self.assertEqual(raised.exception.code, problem_openness_reviews.UNDELIVERED)
                else:
                    self.assertEqual(problem_openness_reviews.actual_delivery(*args), supplied)

    def test_visual_validation_accepts_base_type_spellings_and_checks_the_container(self):
        for media in ('Image/PNG', 'image/png; profile=fixture', ' image/PNG '):
            with self.subTest(media=media):
                validate_visual(PNG, media)
                with self.assertRaises(ResearchError) as raised:
                    validate_visual(PNG[:24], media)
                self.assertEqual(raised.exception.code, 'malformed_visual_asset')

    def test_html_locator_accepts_base_type_spellings_without_relabeling(self):
        data = b'<figure><p>An authored caption.</p></figure>'
        for media in ('Text/HTML; charset=utf-8', 'Application/XHTML+XML'):
            reference = self.artifacts.put(data, media)
            locator = {'kind': 'html', 'anchor': span_locator(data.decode(), 0, len(data))}
            with self.subTest(media=media):
                self.assertEqual(read_locator(self.artifacts, reference, locator), data.decode())
                self.assertEqual(reference['media_type'], media)

    def test_bar_source_html_inventory_accepts_base_type_spellings(self):
        data = b'<article class="article-body"><figure><img src="/figure.png"></figure></article>'
        for media in ('Text/HTML; charset=utf-8', 'Application/XHTML+XML'):
            original = self.artifacts.put(data, media)
            capture = {'source_id': 'source', 'original': original, 'text': None, 'availability': 'available'}
            work = {'id': 'arxiv:2601.00001v1', 'fulltexts': [capture], 'abstracts': [], 'source_ids': ['source']}
            source = {'id': 'source', 'status': 'captured', 'response_complete': True, 'response': original,
                      'url': 'https://example.org/article'}
            records = {'work': {work['id']: work}, 'source': {'source': source}}
            with self.subTest(media=media):
                delivered, pending = bar_sources._html_assets(records, self.artifacts, work, capture)
                self.assertEqual(delivered, [])
                self.assertEqual(pending, [{'code': 'visual_asset_missing', 'url': 'https://example.org/figure.png'}])
                self.assertEqual(original['media_type'], media)

    def test_json_mapping_dispatch_accepts_base_type_spellings(self):
        data = b'{"id":"https://example.org/p","title":"An authored title"}'
        for media in ('Application/JSON; charset=utf-8', ' application/json '):
            with self.subTest(media=media):
                page = parse_mapped(data, media, [{'id': '/id', 'title': '/title'}])
                self.assertEqual(page.works[0]['id'], 'url:https://example.org/p')
                self.assertEqual(page.works[0]['title'], 'An authored title')
                with self.assertRaises(ResearchError) as raised:
                    parse_mapped(b'not JSON', media, [{'id': '/id', 'title': '/title'}])
                self.assertEqual(raised.exception.code, 'invalid_import')

    def test_import_admission_and_capture_keep_the_reported_media_type(self):
        store = Store(self.root, create=True)
        data = b'{"id":"https://example.org/p","title":"An authored title"}'
        text = 'https://example.org/p An authored title'
        for provider in ('web', 'mcp'):
            for media in ('Application/JSON; charset=utf-8', 'Text/Plain; charset=utf-8', 'Text/HTML; charset=utf-8'):
                is_json = media.startswith('Application/')
                response = data if is_json else text.encode()
                mappings = [{'id': '/id', 'title': '/title'}] if is_json else [
                    {'id': {'start': 0, 'end': 21}, 'title': {'start': 22, 'end': len(text)}}]
                with self.subTest(provider=provider, media=media):
                    request_id = provider + '-' + media.split(';')[0].split('/')[1].lower()
                    import_response(store, provider, response, source_url='https://example.org/search',
                                    captured_at='2026-10-25T00:00:00+00:00', request_id=request_id,
                                    expected_revision=store.revision, media_type=media, mappings=mappings)
                    records = store.snapshot()['records']
                    imported = records['source_import'][request_id]
                    self.assertEqual(imported['media_type'], media)
                    source = records['source'][imported['source_id']]
                    self.assertEqual(source['response']['media_type'], media)
                    self.assertEqual(self.artifacts.read(source['response']), response)


if __name__ == '__main__':
    unittest.main()
