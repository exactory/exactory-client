"""Springer Meta JSON documented shape, authored content, synthetic HTTP framing.

No complete original publisher response was available. Positive fixtures model
https://dev.springernature.com/docs/api-endpoints/meta-api/ and test that bounded
format only. They do not demonstrate live publisher retrieval or article scope.
"""

import copy
import json
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import acquisition, bar_sources, cli
from research_harness.errors import ResearchError
from research_harness.source_links import captured_source


DOI = 'doi:10.1007/s00454-005-1225-8'
SOURCE_URL = 'https://api.springernature.com/meta/v2/json?q=doi:10.1007/s00454-005-1225-8'
PARSER = {'format': 'springer-meta-json-v1', 'identifier': DOI}
ABSTRACT = 'An authored complete abstract with a final sentence.'


def document():
    return {'apiMessage': 'This JSON was provided by Springer Nature',
            'query': 'doi:10.1007/s00454-005-1225-8',
            'result': [{'total': '1', 'start': '1', 'pageLength': '1', 'recordsDisplayed': '1'}],
            'records': [{'identifier': DOI, 'doi': DOI[4:], 'title': 'Synthetic article',
                         'abstract': ABSTRACT}]}


def capture(data=None, *, status=200, extra_headers=(), length=None):
    body = json.dumps(document() if data is None else data, ensure_ascii=False).encode('utf-8')
    headers = ['HTTP/1.1 %d Captured' % status, 'Content-Type: application/json; charset=utf-8',
               'Content-Length: %d' % (len(body) if length is None else length)] + list(extra_headers)
    return ('\r\n'.join(headers) + '\r\n\r\n').encode('ascii') + body


class PublisherCompletenessTests(LiteratureCase):
    def values(self):
        return dict(source_url=SOURCE_URL, captured_at='2026-11-01T00:00:00Z',
                    media_type='message/http', publisher_capture=PARSER)

    def import_capture(self, data=None, **changes):
        values = dict(self.values(), **changes)
        self.sequence += 1
        return acquisition.import_response(self.store, 'web', capture() if data is None else data,
            request_id='publisher-' + str(self.sequence), expected_revision=self.store.revision, **values)

    def reject_capture(self, data, **changes):
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.import_capture(data, **changes)
        self.assertEqual(caught.exception.code, 'invalid_publisher_capture')
        self.assertEqual(self.store.snapshot(), before)

    def test_complete_documented_response_delivers_only_the_exact_doi_abstract(self):
        result = self.import_capture()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['pending'], [])
        records = self.store.snapshot()['records']
        work = records['work'][DOI]
        self.assertEqual(work['abstract_status'], 'available')
        self.assertEqual(work['abstracts'][0]['completeness'], 'complete')
        self.assertEqual(work['abstracts'][0]['representation'], 'springer-meta-json-v1')
        self.assertEqual(self.artifacts.read(work['abstract']), ABSTRACT.encode())
        source = captured_source(records, self.artifacts, result['source_ids'][0])
        self.assertEqual(self.artifacts.read(source['response']), capture())
        self.assertFalse(source['origin_verified'])
        self.assertEqual(source['capture_method'], 'external_import')
        self.assertEqual(source['content_scope'], 'publisher_metadata')
        self.assertEqual(source['observed_identifier'], DOI)
        self.assertEqual(source['publisher_capture'], PARSER)
        delivery = bar_sources._source(records, self.artifacts,
            {'id': 'abstract', 'kind': 'source', 'version_id': DOI, 'depth': 'abstract'}, {})
        self.assertEqual(delivery['status'], 'delivered')
        self.assertEqual(work['fulltexts'], [])
        self.assertFalse(records.get('reading'))
        self.assertFalse(delivery['scientific_truth_certified'])

    def test_truncated_json_and_missing_abstract_boundary_are_rejected(self):
        raw = capture()
        self.reject_capture(raw[:-4])
        body = raw.split(b'\r\n\r\n', 1)[1][:-4]
        self.reject_capture(capture(length=len(body)).split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n' + body)

    def test_wrong_doi_and_conflicting_identifier_are_rejected(self):
        for field in ('doi', 'identifier'):
            data = document()
            data['records'][0][field] = 'doi:10.1007/wrong'
            self.reject_capture(capture(data))

    def test_partial_transfer_and_ambiguous_http_framing_are_rejected(self):
        for raw in (capture(status=206), capture(extra_headers=['Content-Range: bytes 0-99/200']),
                    capture(extra_headers=['Transfer-Encoding: chunked']), capture(length=1),
                    capture(extra_headers=['Content-Length: 1']),
                    capture(extra_headers=['Content-Encoding: gzip']), capture() + b'partial-tail'):
            with self.subTest(prefix=raw[:60]):
                self.reject_capture(raw)

    def test_duplicate_or_multiple_abstract_boundaries_are_rejected(self):
        body = json.dumps(document()).replace('"abstract":', '"abstract": "Competing boundary", "abstract":').encode()
        headers = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode()
        self.reject_capture(headers + b'\r\n\r\n' + body)
        data = document()
        data['records'].append(copy.deepcopy(data['records'][0]))
        self.reject_capture(capture(data))
        data = document()
        data['records'][0]['abstract'] = {'text': ABSTRACT, 'complete': True}
        self.reject_capture(capture(data))

    def test_generic_span_and_complete_true_do_not_upgrade_a_partial_excerpt(self):
        data = {'id': DOI, 'title': 'Synthetic article', 'abstract': ABSTRACT, 'complete': True}
        values = dict(source_url='https://link.springer.com/article/' + DOI[4:], captured_at='2026-11-01T00:00:00Z',
                      media_type='application/json', mappings=[{'id': '/id', 'title': '/title', 'abstract': '/abstract'}])
        result = acquisition.import_response(self.store, 'web', json.dumps(data).encode(), request_id='old',
            expected_revision=self.store.revision, **values)
        self.assertEqual(result['pending'], [{'code': 'partial_abstract', 'work_id': DOI}])
        self.assertNotIn('publisher_capture', self.store.snapshot()['records']['source_import']['old'])
        self.assertIsNone(self.store.snapshot()['records']['work'][DOI]['abstract'])
        text = DOI + '\nSynthetic article\n' + 'x' * 1115
        start = len(DOI) + len('\nSynthetic article\n')
        values.update(media_type='text/plain', mappings=[{'id': {'start': 0, 'end': len(DOI)},
            'title': {'start': len(DOI) + 1, 'end': start - 1}, 'abstract': {'start': start, 'end': len(text)}}])
        result = acquisition.import_response(self.store, 'web', text.encode(), request_id='old-span',
            expected_revision=self.store.revision, **values)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.store.snapshot()['records']['work'][DOI]['abstracts'][-1]['completeness'], 'partial')

    def test_complete_true_cannot_replace_transport_or_parser_evidence(self):
        self.reject_capture(json.dumps(document()).encode())
        self.reject_capture(capture(), publisher_capture=dict(PARSER, complete=True))
        self.reject_capture(capture(), publisher_capture={'format': 'unknown', 'identifier': DOI})
        self.reject_capture(capture(), mappings=[])
        self.reject_capture(capture(), source_url='https://example.org/meta/v2/json')

    def test_invalid_media_type_refuses_before_admission_and_in_preview(self):
        for media_type in ('message/http;', ' message/http', 'message/http;\ninvalid'):
            with self.subTest(media_type=media_type):
                self.reject_capture(capture(), media_type=media_type)
                before = self.store.snapshot()
                with self.assertRaises(ResearchError) as caught:
                    acquisition.preview_import_response(self.store, 'web', capture(),
                        **dict(self.values(), media_type=media_type))
                self.assertEqual(caught.exception.code, 'invalid_publisher_capture')
                self.assertEqual(self.store.snapshot(), before)

    def test_publisher_preview_reports_existing_alias_candidates_without_writes(self):
        raw = {'id': 'openalex:W123', 'title': 'Synthetic alias', 'aliases': [DOI]}
        acquisition.import_response(self.store, 'web', json.dumps(raw).encode(),
            source_url='https://example.org/mapping', captured_at='2026-11-01T00:00:00Z',
            media_type='application/json', mappings=[{'id': '/id', 'title': '/title', 'aliases': '/aliases'}],
            request_id='mapped-alias', expected_revision=self.store.revision)
        before = self.store.snapshot()
        preview = acquisition.preview_import_response(self.store, 'web', capture(), **self.values())
        self.assertEqual(preview['source_candidates'][0]['version_id'], DOI)
        candidates = preview['source_candidates'][0]['candidates']
        self.assertEqual([row['version_id'] for row in candidates], ['openalex:W123'])
        self.assertFalse(candidates[0]['reusable'])
        self.assertEqual(self.store.snapshot(), before)

    def test_preview_file_cli_and_replay_bind_the_parser_and_bytes(self):
        data = capture()
        before = self.store.snapshot()
        preview = acquisition.preview_import_response(self.store, 'web', data, **self.values())
        self.assertEqual(preview['work_ids'], [DOI])
        self.assertEqual(preview['pending'], [])
        self.assertEqual(self.store.snapshot(), before)
        response = self.root / 'publisher.http'
        response.write_bytes(data)
        payload = dict(provider='web', response_file=str(response), **self.values())
        saved = self.root / 'import.json'
        saved.write_text(json.dumps(payload))
        args = cli.build_parser().parse_args(['import-response', '--workspace', str(self.root),
            '--file', str(saved), '--request-id', 'publisher-cli', '--expected-revision', '0'])
        result = cli.run(args)
        self.assertEqual(result['status'], 'complete')
        with patch('research_harness.acquisition._import_page', side_effect=AssertionError('Replay parsed')):
            self.assertEqual(cli.run(args), result)
        changed = dict(PARSER, format='unknown')
        with self.assertRaises(ResearchError) as caught:
            acquisition.import_response_file(self.store, 'web', str(response), request_id='publisher-cli',
                expected_revision=0, **dict(self.values(), publisher_capture=changed))
        self.assertEqual(caught.exception.code, 'request_id_conflict')
