"""Search receipts retain gaps, excluded front matter and captured query provenance."""

import base64
import json
from pathlib import Path
from urllib.parse import urlencode

from literature_fixtures import LiteratureCase
from research_harness.acquisition import import_response, preview_import_response
from research_harness.evidence import digest
from research_harness.literature import (find_search_coverage, foundation_report, record_search)
from research_harness.openness_minimum import audit_minimum, check_search_coverage
from test_research_check_mode import CheckCommandMixin


FIXTURE = Path(__file__).parent / 'fixtures/research/search-paratext-0.85.3.json'


class SearchCaptureCase(CheckCommandMixin, LiteratureCase):
    def capture_page(self, data, *, query='bounded', page=1, captured_at='2026-09-07T12:00:00Z',
                     provider='openalex', **parameters):
        self.sequence += 1
        parameters = dict({'search': query, 'page': page, 'per_page': 2} if provider == 'openalex'
                          else {'query': query, 'rows': 2}, **parameters)
        return import_response(self.store, provider, json.dumps(data).encode(),
            source_url='https://api.' + provider + '.org/works?' + urlencode(parameters),
            captured_at=captured_at, expected_revision=self.store.revision, request_id='page-' + str(self.sequence))

    def build_search_payload(self, captures, queries=None):
        queries = queries or ['bounded'] * len(captures)
        found = sorted({work for capture in captures for work in capture['work_ids']})
        return {'id': 'search-' + str(self.sequence), 'profile': 'research', 'purpose': 'direct',
                'question': 'Every bounded configuration has the stated property.', 'queries': list(dict.fromkeys(queries)),
                'responses': [{'source_id': capture['source_ids'][0], 'query': query}
                              for capture, query in zip(captures, queries)],
                'captured_at': '2026-09-09T12:00:00Z', 'scope': 'The captured comparison query.',
                'found_work_ids': found, 'verdict': 'nothing-new', 'cited_work_ids': [],
                'impact': 'Only the saved results were inspected.', 'gaps': [],
                'dispositions': [{'work_id': work, 'disposition': 'out_of_scope', 'reason': 'Outside the comparison.'}
                                 for work in found]}

    def record_search_payload(self, payload):
        receipt = self.mutate(record_search, payload)['result']
        return receipt, self.store.snapshot()['records']['literature_search'][payload['id']]


class SearchReceiptTests(SearchCaptureCase):
    def test_receipt_counts_invalid_items_and_incomplete_responses_with_query_and_provider_type(self):
        self.scope([self.metadata()])
        first = self.capture_page({'meta': {'count': 2}, 'results': [
            {'id': 'https://openalex.org/W123', 'title': None, 'type': 'article'}]})
        second = self.capture_page({'message': {'total-results': 2, 'items': [
            {'title': ['Missing DOI'], 'type': 'journal-article'}]}}, provider='crossref', query='adjacent')
        receipt, _ = self.record_search_payload(self.build_search_payload([first, second], ['bounded', 'adjacent']))
        self.assertEqual(receipt['invalid_work_count'], 2)
        self.assertEqual({(row['query'], row['record_type']) for row in receipt['invalid_works']},
                         {('bounded', 'article'), ('adjacent', 'journal-article')})
        self.assertEqual(receipt['incomplete_response_count'], 2)
        self.assertEqual({(row['query'], row['returned_count'], row['reported_count'])
                          for row in receipt['incomplete_responses']}, {('bounded', 1, 2), ('adjacent', 1, 2)})
        self.assertEqual(receipt['excluded_item_count'], 0)

    def test_new_search_excludes_null_and_titled_paratext_without_integrity_errors(self):
        self.scope([self.metadata()])
        rows = [{'id': 'https://openalex.org/W' + str(100 + i), 'doi': 'https://doi.org/10.1234/issue-' + str(i),
                 'title': title, 'type': 'paratext'} for i, title in enumerate((None, 'Issue front matter'))]
        capture = self.capture_page({'meta': {'count': 2}, 'results': rows})
        self.assertEqual(capture['work_ids'], [])
        self.assertEqual(capture['pending'], [])
        self.assertEqual(capture['excluded_item_count'], 2)
        receipt, search = self.record_search_payload(self.build_search_payload([capture, capture]))
        self.assertEqual(receipt['excluded_item_count'], 2)
        self.assertEqual([(item['id'], item['doi'], item['reason'], item['query']) for item in receipt['excluded_items']],
                         [(row['id'], row['doi'], 'openalex_paratext', 'bounded') for row in rows])
        self.assertEqual(search['page_groups'][0]['excluded_items'],
                         [{**item, 'source_id': capture['source_ids'][0]} for item in capture['excluded_items']])
        self.assertEqual(search['integrity_errors'], [])
        self.assertEqual(search['pending'], [])
        self.assertEqual(search['coverage'][0]['returned_count'], 2)
        self.assertEqual(search['coverage'][0]['inspected_count'], 0)
        self.assertTrue(check_search_coverage(search))
        self.assertFalse(any(row['code'] == 'search_pending' for row in foundation_report(self.store, 'research')['obligations']))
        identity = digest(search)
        finding = {'outcome': 'supported_open', 'searched_at': '2026-09-09', 'evidence': [identity], 'minimum': {
            'schema_version': 1, 'statement': search['question'], 'neighbours': [],
            'searches': [{'formulation': search['question'], 'search_digest': identity}],
            'reading_digests': [], 'settling_work_id': None}}
        audit_minimum(finding, search['question'], self.store.snapshot()['records'], self.artifacts, {identity})

    def test_stored_0853_search_retains_work_digest_projection_errors_and_audit(self):
        fixture = json.loads(FIXTURE.read_text())
        for item in fixture['artifacts']:
            path = self.root / item['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(item['base64']))
        for event in fixture['events']:
            def replay_event(transaction):
                for change in event['changes']:
                    transaction.put(change['kind'], change['key'], change['value'])
                return event['result']
            self.store.mutate(event['operation'], event['payload'], replay_event,
                              expected_revision=event['revision'] - 1, request_id=event['request_id'])
        before = self.store.snapshot()['records']
        self.assertEqual(before, fixture['records'])
        search = before['literature_search']['legacy-search']
        self.assertNotIn('native_parse_version', search)
        self.assertEqual(search['found_work_ids'], ['openalex:W100', 'openalex:W102'])
        self.assertEqual(digest(search), fixture['search_digest'])
        original = fixture['events'][-1]
        self.assertEqual(record_search(self.store, original['payload'], expected_revision=original['revision'] - 1,
                                       request_id=original['request_id']), fixture['receipt'])
        source = before['source'][search['responses'][0]['source_id']]
        self.assertEqual(import_response(self.store, 'openalex', self.artifacts.read(source['response']),
            source_url=source['url'], captured_at=source['captured_at'], expected_revision=0,
            request_id='legacy-openalex'), fixture['import_receipt'])
        self.assertEqual(foundation_report(self.store, 'research'), fixture['foundation_report'])
        legacy = {key: value for key, value in search.items() if key != 'coverage'}
        self.assertEqual(find_search_coverage(legacy, before, self.artifacts), fixture['coverage'])
        self.assertEqual(check_search_coverage(search), fixture['check_search_coverage'])
        self.assert_error(fixture['audit_code'], lambda: audit_minimum(fixture['finding'], search['question'],
                          before, self.artifacts, {fixture['search_digest']}))
        payload = {key: search[key] for key in ('profile', 'purpose', 'queries', 'question', 'responses', 'captured_at',
                   'scope', 'verdict', 'cited_work_ids', 'impact', 'gaps')}
        payload.update(id='new-search', found_work_ids=['openalex:W100'], dispositions=[search['dispositions'][0]])
        receipt, new_search = self.record_search_payload(payload)
        self.assertEqual(receipt['excluded_item_count'], 2)
        self.assertEqual(new_search['integrity_errors'], [])
        after = self.store.snapshot()['records']
        for kind in ('literature_search', 'source_import', 'work'):
            for key, value in before[kind].items():
                self.assertEqual(after[kind][key], value)
        self.assertEqual(digest(after['literature_search']['legacy-search']), fixture['search_digest'])
        self.assert_error(fixture['audit_code'], lambda: audit_minimum(fixture['finding'], search['question'],
                          after, self.artifacts, {fixture['search_digest']}))

    def test_check_reports_each_page_group_captured_more_than_fifteen_minutes_apart_without_writing(self):
        captures, queries = [], []
        for query in ('bounded', 'adjacent', 'originals', 'theory'):
            for page, time in ((1, '2026-09-07T12:00:00Z'), (2, '2026-09-08T12:00:00Z')):
                captures.append(self.capture_page({'meta': {'count': 2 + page}, 'results': [
                    {'id': 'https://openalex.org/W' + str(number), 'title': 'A mathematical article', 'type': 'article'}
                    for number in (page, page + 1)]}, query=query, page=page, captured_at=time))
                queries.append(query)
        payload = self.build_search_payload(captures, queries)
        before = self.read_workspace()
        response = self.assert_accepted_check(self.invoke_command('search', payload), self.store.revision)
        self.assertEqual(self.read_workspace(), before)
        warnings = response['warnings']
        self.assertEqual({row['query'] for row in warnings}, {'bounded', 'adjacent', 'originals', 'theory'})
        self.assertEqual(len(warnings), 4)
        for warning in warnings:
            self.assertEqual(warning['code'], 'search_page_capture_interval')
            self.assertEqual([page['page_number'] for page in warning['pages']], [1, 2])
            self.assertEqual([page['captured_at'] for page in warning['pages']],
                             ['2026-09-07T12:00:00Z', '2026-09-08T12:00:00Z'])
        receipt, _ = self.record_search_payload(payload)
        self.assertEqual(receipt['warnings'], warnings)
        self.assertTrue({'search_results_duplicate', 'search_totals_inconsistent'} <=
                        {row['code'] for row in receipt['pending']})

    def test_capture_interval_uses_a_strict_threshold_and_requires_distinct_pages(self):
        for seconds, expected in (('15:00', 0), ('15:01', 1)):
            first = self.capture_page({'meta': {'count': 0}, 'results': []})
            second = self.capture_page({'meta': {'count': 0}, 'results': []}, page=2,
                                       captured_at='2026-09-07T12:' + seconds + 'Z')
            receipt, _ = self.record_search_payload(self.build_search_payload([first, second]))
            self.assertEqual(len(receipt['warnings']), expected)
        repeated = self.capture_page({'meta': {'count': 0}, 'results': []}, page=1,
                                     captured_at='2026-09-08T12:00:00Z')
        receipt, _ = self.record_search_payload(self.build_search_payload([first, repeated]))
        self.assertEqual(receipt['warnings'], [])
        captures = [self.capture_page({'message': {'total-results': 4, 'next-cursor': 'second', 'items': [
                                         {'DOI': '10.1234/cursor-1', 'title': ['An article']},
                                         {'DOI': '10.1234/cursor-2', 'title': ['Another article']}]}}, provider='crossref',
                                     cursor=cursor, captured_at=time) for cursor, time in (
                                         ('*', '2026-09-07T12:00:00Z'), ('second', '2026-09-08T12:00:00Z'))]
        receipt, _ = self.record_search_payload(self.build_search_payload(captures))
        self.assertEqual([page['page_number'] for page in receipt['warnings'][0]['pages']], [1, 2])


class PathQueryTests(SearchCaptureCase):
    def setUp(self):
        super().setUp()
        self.url = 'https://api.semanticscholar.org/graph/v1/paper/arXiv:1208.4714/citations'
        self.data = {'data': [{'citingPaper': {'paperId': 'a' * 40, 'url': 'https://www.semanticscholar.org/paper/' + 'a' * 40, 'title': 'A citing paper'}}]}
        self.mappings = {'records': [{'id': '/data/0/citingPaper/url', 'title': '/data/0/citingPaper/title'}],
                         'query_path': {'template': '/graph/v1/paper/{paper_id}/citations', 'segment': 'paper_id'}}

    def import_citations(self, mappings=None, url=None, provider='web'):
        self.sequence += 1
        return import_response(self.store, provider, json.dumps(self.data).encode(),
            source_url=url or self.url, captured_at='2026-09-07T12:00:00Z', media_type='application/json',
            mappings=self.mappings if mappings is None else mappings,
            expected_revision=self.store.revision, request_id='citations-' + str(self.sequence))

    def build_citation_payload(self, capture, query='arXiv:1208.4714'):
        payload = self.build_search_payload([capture], [query])
        payload['responses'][0]['results_pointer'] = '/data'
        return payload

    def test_import_and_search_bind_the_declared_named_path_segment(self):
        captured = self.import_citations()
        payload = self.build_citation_payload(captured)
        receipt, search = self.record_search_payload(payload)
        self.assertEqual(receipt['found_work_ids'], ['url:https://www.semanticscholar.org/paper/' + 'a' * 40])
        records = self.store.snapshot()['records']
        imported = next(iter(records['source_import'].values()))
        self.assertEqual(imported['mappings'], self.mappings)
        self.assertEqual(imported['query_binding']['value'], 'arXiv:1208.4714')
        self.assertEqual(find_search_coverage({key: value for key, value in search.items() if key != 'coverage'},
                                             records, self.artifacts), search['coverage'])
        for query in ('arXiv:1208.471', 'citations', 'paper'):
            self.assert_error('invalid_search', lambda: self.record_search_payload(self.build_citation_payload(captured, query)))

    def test_path_binding_requires_the_import_declaration_and_keeps_parameter_and_saved_value_forms(self):
        capture = self.import_citations(self.mappings['records'])
        self.assert_error('invalid_search', lambda: self.record_search_payload(self.build_citation_payload(capture)))
        for suffix, query_locator in (('?query=arXiv%3A1208.4714', None), ('', {
                'kind': 'json', 'pointer': '/query', 'value': 'arXiv:1208.4714'})):
            self.data['query'] = 'arXiv:1208.4714'
            capture = self.import_citations(self.mappings['records'], self.url + suffix)
            payload = self.build_citation_payload(capture)
            if query_locator is not None:
                payload['responses'][0]['query_locator'] = query_locator
            receipt, _ = self.record_search_payload(payload)
            self.assertEqual(receipt['invalid_work_count'], 0)

    def test_invalid_path_declarations_refuse_before_recording_evidence(self):
        cases = [({'records': [], 'query_path': None}, self.url, 'web'),
                 ({**self.mappings, 'extra': True}, self.url, 'web'),
                 ({**self.mappings, 'query_path': {'template': '/graph/{other}', 'segment': 'paper_id'}}, self.url, 'web'),
                 ({**self.mappings, 'query_path': {'template': '/{paper_id}/{paper_id}', 'segment': 'paper_id'}}, self.url, 'web'),
                 (self.mappings, self.url.replace('/citations', '/references'), 'web'),
                 (self.mappings, self.url.replace('arXiv:1208.4714', ''), 'web'),
                 (self.mappings, self.url, 'mcp')]
        for mappings, url, provider in cases:
            with self.subTest(mappings=mappings, url=url, provider=provider):
                before = self.store.snapshot()
                self.assert_error('invalid_import', lambda: self.import_citations(mappings, url, provider))
                self.assertEqual(self.store.snapshot(), before)
                self.assertFalse((self.root / 'research/sources/objects').exists())
        self.assertEqual(len(self.import_citations()['work_ids']), 1)

    def test_example_declares_the_named_segment_in_the_import_mapping(self):
        from research_harness import cli
        example = cli.run(cli.build_parser().parse_args(['example', 'import-response']))
        self.assertEqual(example['source_url'], self.url)
        self.assertEqual(example['mappings'], self.mappings)

    def test_path_binding_decodes_one_url_segment_and_preserves_import_preview(self):
        url = self.url.replace('arXiv:1208.4714', 'arXiv%3A1208.4714')
        before = self.read_workspace()
        preview = preview_import_response(self.store, 'web', json.dumps(self.data).encode(),
            source_url=url, captured_at='2026-09-07T12:00:00Z', media_type='application/json', mappings=self.mappings)
        self.assertEqual(self.read_workspace(), before)
        captured = self.import_citations(url=url)
        self.assertEqual(captured['work_ids'], preview['work_ids'])
        receipt, _ = self.record_search_payload(self.build_citation_payload(captured))
        self.assertEqual(receipt['invalid_work_count'], 0)
