"""A PDF that its server reported as generic binary reaches a reviewer as a PDF, never as text."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from strategy_fixtures import StrategyCase
from research_fixtures import client
from research_harness import review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.acquisition import acquire_fulltext
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError

# A PDF carries a binary comment after its header, so its bytes are not valid UTF-8.
BINARY_PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n% Authored fixture for injected extraction.\n%%EOF\n"
ASCII_PDF = b"%PDF-1.4\n% An ASCII-only authored fixture.\n%%EOF\n"
RESPONSES = {'id': 'route-api', 'adapter': 'openai_responses_v1', 'model': 'gpt-6',
             'endpoint': 'https://api.example.test/v1/responses',
             'configuration': {'max_output_tokens': 20000, 'timeout_seconds': 600}}


class GenericBinaryEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ArtifactStore(Path(directory.name))

    def test_text_only_route_withholds_a_generic_binary_pdf_like_a_declared_pdf(self):
        for index, data in enumerate((BINARY_PDF, ASCII_PDF)):
            with self.subTest(index=index):
                reference = self.store.put(data, 'application/octet-stream')
                with transport.text_only_delivery(True):
                    entry, = protocol._evidence(self.store, {'cited': reference})
                self.assertEqual(entry['artifact'], reference)
                self.assertEqual(entry['encoding'], 'omitted')
                self.assertEqual(entry['delivery'], 'bytes-omitted-on-text-route')
                self.assertEqual(entry['content'], '')

    def test_responses_route_attaches_a_generic_binary_pdf_as_a_pdf_file(self):
        reference = self.store.put(BINARY_PDF, 'application/octet-stream')
        entry, = protocol._evidence(self.store, {'cited': reference})
        self.assertEqual(entry['encoding'], 'base64')
        self.assertEqual(base64.b64decode(entry['content']), BINARY_PDF)
        content = transport.request_body(RESPONSES, 'Review.', {'evidence': [entry]})['input'][-1]['content']
        self.assertEqual(len(content), 2)
        self.assertEqual(content[1]['type'], 'input_file')
        self.assertEqual(content[1]['filename'], reference['sha256'] + '.pdf')
        self.assertTrue(content[1]['file_data'].startswith('data:application/pdf;base64,'))
        delivered = json.loads(content[0]['text'][len(transport.PACKET_LABEL):])['evidence'][0]
        self.assertEqual(delivered['artifact'], reference)
        self.assertEqual(delivered['delivery'], 'attached-exact-bytes')

    def test_generic_binary_text_is_still_delivered_as_text(self):
        reference = self.store.put(b'{"result": 7}', 'application/octet-stream')
        for enabled in (False, True):
            with self.subTest(text_only=enabled), transport.text_only_delivery(enabled):
                entry, = protocol._evidence(self.store, {'cited': reference})
                self.assertEqual((entry['encoding'], entry['content']), ('utf-8', '{"result": 7}'))

    def test_generic_binary_without_a_pdf_signature_keeps_the_refusal(self):
        reference = self.store.put(b'\x7fELF\x02\x01\x01\x00\xff\xfe', 'application/octet-stream')
        for enabled in (False, True):
            with self.subTest(text_only=enabled), transport.text_only_delivery(enabled):
                with self.assertRaises(ResearchError) as raised:
                    protocol._evidence(self.store, {'cited': reference})
                self.assertEqual(raised.exception.code, 'review_evidence_unreadable')

    def test_binary_octet_stream_pdf_is_withheld_on_a_text_only_route(self):
        for data in (ASCII_PDF, BINARY_PDF):
            with self.subTest(data=data):
                reference = self.store.put(data, 'binary/octet-stream')
                try:
                    with transport.text_only_delivery(True):
                        entry, = protocol._evidence(self.store, {'cited': reference})
                except ResearchError as error:
                    self.fail(f"A generic binary PDF should be withheld without refusing delivery: {error.code}")
                self.assertEqual(entry['artifact'], reference)
                self.assertEqual(entry['encoding'], 'omitted')
                self.assertEqual(entry['delivery'], 'bytes-omitted-on-text-route')
                self.assertEqual(entry['content'], '')

    def test_binary_octet_stream_pdf_is_attached_as_pdf_without_relabeling_its_descriptor(self):
        for data in (ASCII_PDF, BINARY_PDF):
            with self.subTest(data=data):
                reference = self.store.put(data, 'binary/octet-stream')
                try:
                    entry, = protocol._evidence(self.store, {'cited': reference})
                except ResearchError as error:
                    self.fail(f"A generic binary PDF should be attached as PDF: {error.code}")
                self.assertEqual(entry['encoding'], 'base64')
                request = transport.request_body(RESPONSES, 'Review.', {'evidence': [entry]})
                content = request['input'][-1]['content']
                self.assertEqual(content[1]['file_data'],
                                 'data:application/pdf;base64,' + base64.b64encode(data).decode('ascii'))
                delivered = json.loads(content[0]['text'][len(transport.PACKET_LABEL):])['evidence'][0]
                self.assertEqual(delivered['artifact'], reference)


class GenericBinarySourceDeliveryTests(StrategyCase):
    metadata = LiteratureCase.metadata

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()

    def assign(self, identifier, **context):
        return self.mutate(protocol.record_assignment, {'id': identifier, 'route_id': 'route',
            'dossier_id': 'dossier-1', 'role': 'bar', 'reviewer_id': 'reviewer-a',
            'author_id': 'author', 'context': context})['result']

    def packet(self, identifier):
        assignment = self.store.snapshot()['records']['review_assignment'][identifier]
        return json.loads(self.artifacts.read(assignment['packet']))

    def invoke(self, response, assignment, sent=None):
        def send(route, request, credential):
            if sent is not None:
                sent.append(request)
            return self.provider_response(response)

        with patch('research_harness.review_transport.send_request', side_effect=send):
            return protocol.invoke_assignment(self.store, {'id': 'attempt-' + assignment, 'assignment_id': assignment},
                expected_revision=self.store.revision, request_id='invoke-' + assignment, credential='fixture-only')

    def request_fulltext(self, body):
        source = self.metadata(1)
        capture = capture_generic_binary(self, source, body)
        self.assign('a')
        response = self.review_response('bar', status='unresolved')
        response['value']['evidence'] = []
        response['source_requests'] = [{'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}]
        self.invoke(response, 'a')
        return capture

    def check_text_only_continuation(self, body):
        capture = self.request_fulltext(body)
        with transport.text_only_delivery(True):
            self.assign('b', prior_assignment_id='a')
        check_text_only_delivery(self, self.packet('b'), capture)

    def check_responses_continuation(self, body):
        capture = self.request_fulltext(body)
        self.assign('b', prior_assignment_id='a')
        packet = self.packet('b')
        delivery, = packet['source_deliveries']
        self.assertEqual(delivery['status'], 'delivered')
        self.assertEqual(delivery['evidence'], [capture['original'], capture['text']])
        original, = [entry for entry in packet['evidence'] if entry['artifact'] == capture['original']]
        self.assertEqual(original['encoding'], 'base64')
        sent = []
        self.invoke(self.review_response('bar'), 'b', sent)
        files = [part for part in sent[0]['input'][-1]['content'] if part['type'] != 'input_text']
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['type'], 'input_file')
        self.assertEqual(files[0]['filename'], capture['original']['sha256'] + '.pdf')
        self.assertEqual(files[0]['file_data'], 'data:application/pdf;base64,' + base64.b64encode(body).decode('ascii'))

    def test_text_only_continuation_withholds_a_binary_original(self):
        self.check_text_only_continuation(BINARY_PDF)

    def test_text_only_continuation_withholds_an_ascii_original(self):
        self.check_text_only_continuation(ASCII_PDF)

    def test_responses_continuation_attaches_a_binary_original_as_a_pdf_file(self):
        self.check_responses_continuation(BINARY_PDF)

    def test_responses_continuation_attaches_an_ascii_original_as_a_pdf_file(self):
        self.check_responses_continuation(ASCII_PDF)


class GenericBinaryProblemBarTests(LiteratureCase):
    """The failing production path: a problem-creation bar continuation for a requested full text. The packet is
    built for a text-only route, as record_assignment does for a Claude Code or Codex route."""

    def setUp(self):
        from test_research_problem_reviews import ProblemReviewTests
        super().setUp()
        self.reviews = ProblemReviewTests
        self.reviews.put(self, 'workspace', 'problem', {'version': 1, 'orchestration': 'problem_creation',
            'slug': 'bounded-sequences', 'area': 'math.CO', 'field': 'Extremal bounded sequences', 'authors': ['author']})
        self.supporting = self.artifacts.put(b'A captured field standard.', 'text/plain')
        self.reviews.prepare_review_route(self)

    def build_provider_response(self, output):
        return self.reviews.build_provider_response(self, output)

    def invoke(self, output, assignment):
        with patch('research_harness.review_transport.send_request', return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {'id': 'attempt-' + assignment, 'assignment_id': assignment},
                        credential='fixture-only')

    def test_text_only_bar_continuation_delivers_the_extraction(self):
        source = self.metadata(4242)
        capture = capture_generic_binary(self, source, BINARY_PDF)
        context = self.reviews.deliver_supporting(self, 'first')
        self.reviews.assign(self, 'bar', 'first', identifier='bar-first', **context)
        turn = self.reviews.build_review_output(self, 'bar')
        turn['value'].update(status='unresolved', evidence=[])
        turn['source_requests'] = [{'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}]
        self.invoke(turn, 'bar-first')
        with transport.text_only_delivery(True):
            second = self.reviews.assign(self, 'bar', 'first', identifier='bar-first-2', prior_assignment_id='bar-first')
        check_text_only_delivery(self, json.loads(self.artifacts.read(second['packet'])), capture)


def capture_generic_binary(case, identifier, body):
    """Acquire an authored PDF body that its server reports as generic binary."""
    http, _, _ = client([(200, {'Content-Type': 'application/octet-stream'}, body)], max_retries=0)
    result = acquire_fulltext(case.store, identifier, 'https://arxiv.org/pdf/' + identifier[6:], http=http,
        extractor=lambda data: {'status': 'extracted', 'text': 'THE GENERIC BINARY PDF BODY. References: none.'},
        expected_revision=case.store.revision, request_id='binary-body')
    case.assertEqual(result['capture']['extraction']['format_detection'], 'pdf_signature_from_generic_binary')
    return result['capture']


def check_text_only_delivery(case, packet, capture):
    """The delivery names the original, delivers its extraction alone, and the packet carries no original bytes."""
    delivery, = packet['source_deliveries']
    case.assertEqual(delivery['status'], 'delivered')
    case.assertEqual(delivery['evidence'], [capture['text']])
    provenance, = delivery['source_provenance']
    case.assertEqual(provenance['delivery_mode'], 'extracted-text-only')
    case.assertEqual(provenance['delivered_sha256'], [capture['text']['sha256']])
    case.assertEqual(provenance['original_descriptor']['media_type'], 'application/octet-stream')
    case.assertEqual(provenance['original_descriptor']['sha256'], capture['original']['sha256'])
    case.assertNotIn(capture['original']['sha256'], [entry['artifact']['sha256'] for entry in packet['evidence']])
    case.assertIn('THE GENERIC BINARY PDF BODY.', json.dumps(packet))


if __name__ == '__main__':
    unittest.main()
