"""PDF review coverage requires native page derivation and delivered image bytes."""

import base64
import copy

from literature_fixtures import LiteratureCase
from research_fixtures import client
from test_research_binary_pdf import authored_pdf
from research_harness import literature, reading
from research_harness.acquisition import acquire_fulltext
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.errors import ResearchError


class VisualSourceTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.version = self.metadata()
        http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, authored_pdf())])
        self.capture_value = acquire_fulltext(self.store, self.version, 'https://arxiv.org/pdf/' + self.version[6:],
            http=http, extractor=lambda _: {'status': 'extracted', 'text': 'Authored page. References: none.\f'},
            expected_revision=self.store.revision, request_id='valid-pdf')['capture']
        self.bundle_value = self.bundle(self.version, capture=self.capture_value)
        self.link_value = {'version_id': self.version, 'source_id': self.capture_value['source_id'],
            'artifact': self.capture_value['original'], 'locator': {'kind': 'pdf', 'page_index': 0,
            'printed_page': '1', 'region': [0, 0, 1, 1]}}
        for identifier in ('page-a', 'page-b'):
            self.bundle_value['units'].append({'id': identifier, 'kind': 'figure', 'required': True,
                                               'link': copy.deepcopy(self.link_value)})
        self.mutate(literature.import_bundle, self.bundle_value)
        self.mutate(reading.record_reading, self.full_note(self.bundle_value, note_id='visual-reading'))
        self.records = self.store.snapshot()['records']
        self.native_reading = self.records['reading']['visual-reading']
        self.packet = {'problem_evidence': {'readings': [{'id': self.native_reading['id'],
                                                        'digest': digest(self.native_reading)}]}, 'evidence': []}

    def prepared(self):
        from research_harness.review_visual_sources import prepare_packet
        return prepare_packet(self.records, self.artifacts, copy.deepcopy(self.packet))

    def test_native_full_page_deduplicates_units_and_requires_image_bytes(self):
        from research_harness.review_visual_sources import has_pdf_page, MARKER
        packet = self.prepared()
        manifest = packet[MARKER]
        self.assertEqual(len(manifest['pages']), 1)
        self.assertEqual(len(manifest['readings'][0]['units']), 2)
        image = manifest['pages'][0]['image']
        self.assertTrue(self.artifacts.read(image).startswith(b'\x89PNG\r\n\x1a\n'))
        evaluation = Evaluation(self.records, self.artifacts)
        self.assertTrue(has_pdf_page(evaluation, packet, self.link_value))
        packet['evidence'] = []
        self.assertFalse(has_pdf_page(evaluation, packet, self.link_value))

    def test_source_page_version_and_reading_binding_cannot_be_forged(self):
        from research_harness.review_visual_sources import has_pdf_page, MARKER, prepare_packet
        packet = self.prepared()
        evaluation = Evaluation(self.records, self.artifacts)
        for key, value in [('source_id', 'unrelated'), ('version_id', 'arxiv:2601.00002v1')]:
            link = dict(self.link_value, **{key: value})
            self.assertFalse(has_pdf_page(evaluation, packet, link))
        link = copy.deepcopy(self.link_value); link['locator']['page_index'] = 1
        self.assertFalse(has_pdf_page(evaluation, packet, link))
        bad = copy.deepcopy(packet); bad[MARKER]['readings'][0]['digest'] = '0' * 64
        self.assertFalse(has_pdf_page(evaluation, bad, self.link_value))
        bad = copy.deepcopy(self.packet); bad['problem_evidence']['readings'][0]['digest'] = '0' * 64
        with self.assertRaises(ResearchError):
            prepare_packet(self.records, self.artifacts, bad)

    def test_manifest_cannot_mutate_the_cached_renderer_profile(self):
        from research_harness.review_visual_sources import has_pdf_page, MARKER
        from research_harness.review_pdf_pages import profile
        packet = self.prepared()
        packet[MARKER]['render_profile']['dpi'] = 1
        self.assertEqual(profile()['dpi'], 150)
        self.assertFalse(has_pdf_page(Evaluation(self.records, self.artifacts), packet, self.link_value))

    def test_unrelated_valid_png_cannot_replace_native_page(self):
        from research_harness.review_visual_sources import has_pdf_page, MARKER
        from research_harness.review_visual_inputs import control_image
        packet = self.prepared()
        data = control_image('unrelated image')
        reference = self.artifacts.put(data, 'image/png')
        packet[MARKER]['pages'][0]['image'] = reference
        packet['evidence'] = [{'artifact': reference, 'encoding': 'base64',
                               'content': base64.b64encode(data).decode()}]
        self.assertFalse(has_pdf_page(Evaluation(self.records, self.artifacts), packet, self.link_value))

    def test_renderer_rejects_out_of_range_and_missing_original(self):
        from research_harness.review_pdf_pages import render
        with self.assertRaises(ResearchError):
            render(self.artifacts, self.capture_value['original'], 1)
        missing = dict(self.capture_value['original'], sha256='0' * 64,
                       path='research/sources/objects/' + '0' * 64)
        with self.assertRaises((ResearchError, FileNotFoundError)):
            render(self.artifacts, missing, 0)

    def test_rendered_page_does_not_supply_original_pdf_artifact(self):
        from research_harness.direction_sources import has_attachment, find_delivered_readings
        packet = self.prepared()
        text = self.capture_value['text']
        packet['evidence'].append({'artifact': text, 'encoding': 'utf-8',
                                   'content': self.artifacts.read(text).decode()})
        evaluation = Evaluation(self.records, self.artifacts)
        self.assertFalse(has_attachment(evaluation, packet, self.capture_value['original']))
        self.assertEqual(find_delivered_readings(self.records, self.artifacts, [self.native_reading], [packet],
                                                whole_text=False), {'visual-reading'})


class InlineSourceReceiptTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        from research_harness.direction_sources import build_inline_passages
        self.version = self.metadata()
        self.quote = 'The bounded HTML passage is supplied exactly.'
        self.capture_value = self.capture(self.version, body=self.quote + ' An unsupplied paragraph. References: none.')
        self.link_value = self.link(self.version, self.capture_value['source_id'],
                                   self.capture_value['original'], self.quote, kind='span')
        bundle = self.bundle(self.version, capture=self.capture_value)
        bundle['units'].append({'id': 'bounded-original', 'kind': 'text', 'required': True, 'link': self.link_value})
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id='inline-reading'))
        self.records = self.store.snapshot()['records']
        self.native_reading = self.records['reading']['inline-reading']
        passages = [passage for passage, _ in build_inline_passages(self.records, self.artifacts, [self.native_reading])
                    if passage.get('text') == self.quote]
        self.assertEqual(len(passages), 1)
        self.packet = {'problem_evidence': {'readings': [{'id': self.native_reading['id'],
                       'digest': digest(self.native_reading)}], 'passages': passages}, 'evidence': []}

    def received(self, link=None, packets=None, *, raw=False):
        from research_harness.review_supplied_evidence import is_received
        evidence = self.capture_value['original'] if raw else {'kind': 'source', 'link': link or self.link_value}
        return is_received(self.records, evidence, {}, [], artifacts=self.artifacts, frozen=True,
                           packets=[self.packet] if packets is None else packets)

    def test_exact_inline_html_span_and_its_subspan_are_received_without_original_bytes(self):
        self.assertTrue(self.received())
        shorter = self.link(self.version, self.capture_value['source_id'], self.capture_value['original'],
                            'bounded HTML passage', kind='span')
        self.assertTrue(self.received(shorter))
        self.assertEqual(self.packet['evidence'], [])

    def test_altered_inline_text_does_not_supply_native_source_content(self):
        broken = copy.deepcopy(self.packet)
        broken['problem_evidence']['passages'][0]['text'] += ' Fabricated words.'
        self.assertFalse(self.received(packets=[broken]))

    def test_inline_source_requires_exact_same_packet_native_reading_digest_and_inspection(self):
        for change in ('missing-reading', 'digest', 'reading-id', 'inspection', 'passage-version'):
            broken = copy.deepcopy(self.packet)
            block = broken['problem_evidence']
            if change == 'missing-reading': block['readings'] = []
            if change == 'digest': block['readings'][0]['digest'] = '0' * 64
            if change == 'reading-id': block['passages'][0]['reading_id'] = 'another-reading'
            if change == 'inspection': block['passages'][0]['inspection_index'] = 0
            if change == 'passage-version': block['passages'][0]['version_id'] = 'arxiv:2601.00002v1'
            with self.subTest(change=change):
                self.assertFalse(self.received(packets=[broken]))

    def test_different_source_version_or_artifact_cannot_borrow_the_inline_passage(self):
        for key, value in (('source_id', 'unrelated'), ('version_id', 'arxiv:2601.00002v1'),
                           ('artifact', self.capture_value['text'])):
            with self.subTest(key=key):
                self.assertFalse(self.received(dict(self.link_value, **{key: value})))

    def test_citation_cannot_expand_past_the_exact_delivered_inline_span(self):
        wider = self.link(self.version, self.capture_value['source_id'], self.capture_value['original'],
                          self.quote + ' An unsupplied paragraph.', kind='span')
        self.assertFalse(self.received(wider))
        broken = copy.deepcopy(self.packet)
        row = broken['problem_evidence']['passages'][0]
        row.update(start=wider['locator']['start'], end=wider['locator']['end'],
                   text=self.quote + ' An unsupplied paragraph.')
        self.assertFalse(self.received(wider, [broken]))

    def test_inline_span_never_supplies_whole_original_or_absent_actual_packet(self):
        self.assertFalse(self.received(raw=True))
        self.assertFalse(self.received(packets=[]))
        self.assertFalse(self.received(packets=[{'evidence': []}]))
