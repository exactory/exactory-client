"""A cited PDF page is checked against the page map of the delivered extraction when the output is classified."""

import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from strategy_fixtures import StrategyCase
from research_fixtures import client
from research_harness import research_decisions as decisions
from research_harness import review_protocol as protocol
from research_harness.acquisition import acquire_fulltext
from research_harness.errors import ResearchError
from research_harness.review_refusal import classify_observed_output, validate_evidence_contract

BINARY_PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n% Fixture for an injected extraction.\n%%EOF\n"


class FrozenPdfCaptureTests(StrategyCase):
    metadata = LiteratureCase.metadata

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()

    def invoke(self, response, assignment, attempt=None):
        with patch('research_harness.review_transport.send_request', return_value=self.provider_response(response)):
            return protocol.invoke_assignment(self.store, {'id': attempt or 'attempt-' + assignment, 'assignment_id': assignment},
                expected_revision=self.store.revision, request_id='invoke-' + (attempt or assignment), credential='fixture-only')

    def assign(self, identifier, **context):
        return self.mutate(protocol.record_assignment, {'id': identifier, 'route_id': 'route', 'dossier_id': 'dossier-1',
            'role': 'bar', 'reviewer_id': 'reviewer-a', 'author_id': 'author', 'context': context})['result']

    def test_a_page_outside_the_delivered_pdf_is_a_recorded_refusal(self):
        source = self.metadata(1)
        http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, BINARY_PDF)], max_retries=0)
        capture = acquire_fulltext(self.store, source, 'https://arxiv.org/pdf/' + source[6:], http=http,
            extractor=lambda data: {'status': 'extracted', 'text': 'THE FIXTURE PDF BODY. References: none.'},
            expected_revision=self.store.revision, request_id='fixture-body')['capture']
        # The acquisition stores the extraction with its charset parameter.
        self.assertEqual(capture['text']['media_type'], 'text/plain; charset=utf-8')
        self.assign('a')
        request_turn = self.review_response('bar', status='unresolved')
        request_turn['value']['evidence'] = []
        request_turn['source_requests'] = [{'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}]
        self.invoke(request_turn, 'a')
        self.assign('b', prior_assignment_id='a')
        final = self.review_response('bar')
        final['value']['evidence'] = [self.evidence, {'kind': 'source', 'link': {
            'version_id': source, 'source_id': capture['source_id'], 'artifact': capture['original'],
            'locator': {'kind': 'pdf', 'page_index': 99, 'printed_page': None, 'region': [0, 0, 1, 1]}}}]
        self.invoke(final, 'b')
        failure = classify_observed_output(self.store.snapshot()['records'], self.artifacts, 'attempt-b')
        self.assertEqual((failure['code'], failure['check']), ('invalid_locator', 'evidence.locator'))
        with self.assertRaises(ResearchError):
            self.mutate(decisions.record_value_review, dict(final, id='bar-review-b', dossier_id='dossier-1', assignment_id='b'))
        self.assertIn('attempt-b', self.store.snapshot()['records']['review_output_refusal'])
        # The refused output frees the assignment for one more run.
        self.invoke(final, 'b', attempt='attempt-b2')

    def test_stored_generic_binary_pdf_without_frozen_inference_is_not_a_locator_refusal(self):
        source = self.metadata(1)
        original = self.artifacts.put(BINARY_PDF, 'binary/octet-stream')
        text = self.artifacts.put(b'An extracted page.\f', 'text/plain; charset=utf-8')
        link = {'version_id': source, 'source_id': 'binary-source', 'artifact': original,
                'locator': {'kind': 'pdf', 'page_index': 99, 'printed_page': None, 'region': [0, 0, 1, 1]}}
        packet = {'source_deliveries': [{'request': {'version_id': source}, 'evidence': [original, text],
                  'source_provenance': [{'source_id': 'binary-source', 'original_descriptor': original,
                                         'delivered_sha256': [original['sha256'], text['sha256']]}]}]}
        for packets in ((), (packet,)):
            with self.subTest(frozen_delivery=bool(packets)):
                try:
                    validate_evidence_contract({}, self.artifacts, [{'kind': 'source', 'link': link}], packets=packets)
                except ResearchError as error:
                    self.fail(f"A generic binary descriptor lacks frozen PDF inference: {error.code}")


if __name__ == '__main__':
    unittest.main()
