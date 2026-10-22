"""Keep representation diagnostics without confusing them with required units."""

import copy
import json

from literature_fixtures import LiteratureCase
from research_harness import bar_sources, review_protocol, review_transport
from research_harness.errors import ResearchError
from research_harness.literature import import_bundle


class SourceAlternativeTests(LiteratureCase):
    def noisy_html(self, identifier):
        return self.capture(identifier, body=(
            '<style>p { color: black; }</style><script>void 0;</script>'
            '<figure><img src="decorative-logo.png"><figcaption>Authored figure.</figcaption></figure>'
            'The authored body has a finite example. References: none.'))

    def pdf(self, identifier):
        return self.capture(identifier, pdf_text='Authored complete main text.\fReferences: none.\f')

    def source_request(self, identifier, depth='fulltext'):
        return {'id': 'source-request', 'kind': 'source', 'version_id': identifier, 'depth': depth}

    def delivery(self, identifier, *, records=None, text_only=False, depth='fulltext'):
        records = self.store.snapshot()['records'] if records is None else records
        with review_transport.text_only_delivery(text_only):
            return bar_sources._source(records, self.artifacts, self.source_request(identifier, depth), {})

    def continuation(self, identifier):
        records = self.store.snapshot()['records']
        previous = {'source_inventory': bar_sources.inventory(records, self.artifacts),
                    'pending_source_requests': [self.source_request(identifier)]}
        return bar_sources.continuation(records, self.artifacts, previous, {})

    def test_same_version_pdf_resolves_request_without_erasing_html_diagnostics(self):
        identifier = self.metadata()
        html = self.noisy_html(identifier)
        pdf = self.pdf(identifier)
        result = self.delivery(identifier)

        self.assertEqual(result['status'], 'delivered')
        self.assertEqual(result['source_scope_pending'], [])
        alternatives = {row['source_id']: row for row in result['source_alternatives']}
        self.assertEqual(set(alternatives), {html['source_id'], pdf['source_id']})
        html_alternative = alternatives[html['source_id']]
        self.assertEqual(html_alternative['version_id'], identifier)
        self.assertEqual(html_alternative['original_descriptor']['sha256'], html['original']['sha256'])
        self.assertEqual(html_alternative['extraction_descriptor']['sha256'], html['text']['sha256'])
        self.assertTrue(html_alternative['main_representation'])
        pending = {item['code'] for item in html_alternative['source_scope_pending']}
        self.assertTrue({'visual_document_style_unsupported', 'dynamic_visual_unsupported',
                         'visual_asset_missing'} <= pending)
        self.assertEqual(alternatives[pdf['source_id']]['source_scope_pending'], [])
        self.assertTrue(alternatives[pdf['source_id']]['main_representation'])
        delivered = {ref['sha256'] for ref in result['evidence']}
        self.assertTrue({html['original']['sha256'], html['text']['sha256'],
                         pdf['original']['sha256'], pdf['text']['sha256']} <= delivered)
        self.assertFalse(result['reading_credit'])
        self.assertFalse(result['scientific_truth_certified'])
        self.assertEqual(self.continuation(identifier)['pending_source_requests'], [])

    def test_noisy_html_without_ready_main_keeps_request_pending(self):
        identifier = self.metadata()
        html = self.noisy_html(identifier)
        result = self.delivery(identifier)

        self.assertEqual(result['status'], 'partial')
        self.assertIn('visual_document_style_unsupported',
                      [item['code'] for item in result['source_scope_pending']])
        self.assertIn('source_alternatives', result)
        self.assertEqual(result['source_alternatives'][0]['source_id'], html['source_id'])
        self.assertEqual(result['source_alternatives'][0]['source_scope_pending'], result['source_scope_pending'])
        self.assertEqual(self.continuation(identifier)['pending_source_requests'], [self.source_request(identifier)])

    def test_other_version_pdf_is_not_an_alternative(self):
        identifier = self.metadata(version=1)
        self.noisy_html(identifier)
        other_version = self.metadata(version=2)
        other_pdf = self.pdf(other_version)
        result = self.delivery(identifier)

        self.assertEqual(result['status'], 'partial')
        self.assertIn('source_alternatives', result)
        self.assertNotIn(other_pdf['source_id'], [row['source_id'] for row in result['source_alternatives']])
        self.assertEqual({row['version_id'] for row in result['source_alternatives']}, {identifier})

    def test_ready_pdf_cannot_waive_required_supplement(self):
        identifier = self.metadata()
        html = self.noisy_html(identifier)
        self.pdf(identifier)
        bundle = self.bundle(identifier, html)
        bundle['units'].append({'id': 'required-si', 'kind': 'supplement', 'required': True, 'link': None,
                                'reason': 'The authored derivation is required.', 'url': 'https://example.org/si.pdf'})
        self.mutate(import_bundle, bundle)
        result = self.delivery(identifier)

        self.assertEqual(result['status'], 'partial')
        self.assertIn('required_unit_missing', [item['code'] for item in result['source_scope_pending']])
        self.assertEqual(self.continuation(identifier)['pending_source_requests'], [self.source_request(identifier)])

    def test_ready_pdf_cannot_waive_required_html_visual(self):
        identifier = self.metadata()
        html = self.noisy_html(identifier)
        self.pdf(identifier)
        bundle = self.bundle(identifier, html)
        bundle['units'].append({'id': 'required-figure', 'kind': 'figure', 'required': True, 'link': {
            'version_id': identifier, 'source_id': html['source_id'], 'artifact': html['original'],
            'locator': {'kind': 'html', 'anchor': self.span(html['original'], '<img src="decorative-logo.png">')}}})
        self.mutate(import_bundle, bundle)
        result = self.delivery(identifier)

        self.assertEqual(result['status'], 'partial')
        self.assertTrue(any(item['code'] == 'visual_asset_missing' and item.get('unit_id') == 'required-figure'
                            for item in result['source_scope_pending']))

    def test_ready_pdf_cannot_waive_partial_declared_article_bundle(self):
        identifier = self.metadata()
        html = self.noisy_html(identifier)
        self.pdf(identifier)
        self.mutate(import_bundle, self.bundle(identifier, html, complete=False))
        result = self.delivery(identifier)

        self.assertEqual(result['status'], 'partial')
        self.assertIn('source_bundle_incomplete', [item['code'] for item in result['source_scope_pending']])

    def test_pdf_declared_as_supplement_cannot_satisfy_main_request(self):
        identifier = self.metadata()
        html = self.noisy_html(identifier)
        pdf = self.pdf(identifier)
        bundle = self.bundle(identifier, html)
        bundle['units'].append({'id': 'optional-si', 'kind': 'supplement', 'required': False,
                                'link': self.link(identifier, pdf['source_id'], pdf['text'])})
        self.mutate(import_bundle, bundle)
        result = self.delivery(identifier)

        self.assertEqual(result['status'], 'partial')
        self.assertIn('source_alternatives', result)
        alternative = next(row for row in result['source_alternatives'] if row['source_id'] == pdf['source_id'])
        self.assertFalse(alternative['main_representation'])
        self.assertEqual(alternative['source_scope_pending'], [])
        self.assertEqual(self.continuation(identifier)['pending_source_requests'], [self.source_request(identifier)])

    def test_good_main_does_not_bypass_invalid_second_capture(self):
        identifier = self.metadata()
        self.pdf(identifier)
        html = self.noisy_html(identifier)
        original_records = self.store.snapshot()['records']
        cases = ('requested-version', 'observed-version', 'unverified-origin', 'missing-extraction',
                 'extraction-measures', 'incomplete-response', 'corrupt-artifact')
        for mode in cases:
            with self.subTest(mode=mode):
                records = copy.deepcopy(original_records)
                capture = next(item for item in records['work'][identifier]['fulltexts']
                               if item['source_id'] == html['source_id'])
                source = records['source'][html['source_id']]
                expected = 'review_source_binding_mismatch'
                if mode == 'requested-version':
                    capture['requested_version_id'] = 'arxiv:2601.00001v2'
                elif mode == 'observed-version':
                    source['observed_identifier'] = 'arxiv:2601.00001v2'
                elif mode == 'unverified-origin':
                    source['origin_verified'] = False
                elif mode == 'missing-extraction':
                    capture['text'] = None
                elif mode == 'extraction-measures':
                    capture['extraction']['text_bytes'] += 1
                elif mode == 'incomplete-response':
                    source['response_complete'] = False
                    expected = 'source_pending'
                elif mode == 'corrupt-artifact':
                    capture['text']['size'] += 1
                    expected = 'artifact_corrupt'
                with self.assertRaises(ResearchError) as caught:
                    self.delivery(identifier, records=records)
                self.assertEqual(caught.exception.code, expected)

    def test_text_route_retains_pdf_identity_and_exact_extraction_without_original_bytes(self):
        identifier = self.metadata()
        self.noisy_html(identifier)
        pdf = self.pdf(identifier)
        result = self.delivery(identifier, text_only=True)

        self.assertEqual(result['status'], 'delivered')
        self.assertNotIn(pdf['original']['sha256'], [ref['sha256'] for ref in result['evidence']])
        self.assertIn(pdf['text']['sha256'], [ref['sha256'] for ref in result['evidence']])
        provenance = next(row for row in result['source_provenance'] if row['source_id'] == pdf['source_id'])
        self.assertEqual(provenance['original_descriptor']['sha256'], pdf['original']['sha256'])
        self.assertEqual(provenance['delivery_mode'], 'extracted-text-only')
        self.assertNotIn(pdf['original']['sha256'], provenance['delivered_sha256'])
        with review_transport.text_only_delivery(True):
            evidence = review_protocol._evidence(self.artifacts, {'source_deliveries': [result]})
        extracted = next(row for row in evidence if row['artifact']['sha256'] == pdf['text']['sha256'])
        self.assertEqual(extracted['content'], self.artifacts.read(pdf['text']).decode('utf-8'))
        self.assertNotIn('%PDF-', json.dumps(evidence))

    def test_abstract_request_keeps_existing_delivery_semantics(self):
        identifier = self.metadata()
        self.noisy_html(identifier)
        result = self.delivery(identifier, depth='abstract')

        self.assertEqual(result['status'], 'delivered')
        self.assertEqual(result['source_scope_pending'], [])
        self.assertNotIn('source_alternatives', result)
        self.assertFalse(result['reading_credit'])
