"""Bind arXiv identifier enumerations and a path-bound citing-works example."""
import copy
import json
from pathlib import Path
from urllib.parse import urlencode

from test_search_receipts import SearchCaptureCase
from research_fixtures import atom, entry
from research_harness.acquisition import import_response
from research_harness.errors import ResearchError
from research_harness.openness_minimum import check_search_coverage


class ArxivIdListBindingTests(SearchCaptureCase):
    def capture_ids(self, requested, returned, *, start=0, total=None, query=None, **parameters):
        self.sequence += 1
        params = dict(id_list=requested, max_results=10, start=start, **parameters)
        if query is not None:
            params['search_query'] = query
        return import_response(self.store, 'arxiv', atom([entry(identifier) for identifier in returned],
            total=len(returned) if total is None else total, start=start),
            source_url='https://export.arxiv.org/api/query?' + urlencode(params),
            captured_at='2026-09-07T12:00:00Z', expected_revision=self.store.revision,
            request_id='ids-' + str(self.sequence))

    def test_three_identifiers_bind_and_establish_complete_coverage(self):
        ids = ['2601.00001v1', '2601.00002v1', '2601.00003v1']
        query = ','.join(ids)
        captured = self.capture_ids(query, ids)
        receipt, search = self.record_search_payload(self.build_search_payload([captured], [query]))
        self.assertEqual(search['coverage'][0]['kind'], 'complete')
        self.assertEqual(search['coverage'][0]['inspected_count'], 3)
        self.assertTrue(check_search_coverage(search))
        self.assertEqual(receipt['missing_identifiers'], [])

    def test_missing_entry_names_the_requested_identifier_despite_matching_response_total(self):
        ids = ['2601.00001v1', '2601.00002v1', '2601.00003v1']
        query = ','.join(ids)
        captured = self.capture_ids(query, ids[:2])
        receipt, search = self.record_search_payload(self.build_search_payload([captured], [query]))
        self.assertEqual(receipt['missing_identifier_count'], 1)
        self.assertEqual(receipt['missing_identifiers'][0]['identifier'], ids[2])
        self.assertEqual(receipt['missing_identifiers'][0]['query'], query)
        self.assertNotEqual(search['coverage'][0]['kind'], 'complete')
        self.assertFalse(check_search_coverage(search))

    def test_missing_ids_are_checked_across_pages_and_unversioned_ids_match_returned_versions(self):
        query = '2601.00001,hep-th/9901001v1,2601.00003v1'
        first = self.capture_ids(query, ['2601.00001v2', 'hep-th/9901001v1'], total=3)
        second = self.capture_ids(query, ['2601.00003v1'], start=2, total=3)
        receipt, search = self.record_search_payload(self.build_search_payload([first, second], [query, query]))
        self.assertEqual(search['coverage'][0]['kind'], 'complete')
        self.assertEqual(receipt['missing_identifiers'], [])

    def test_versioned_id_does_not_match_another_version(self):
        query = '2601.00001v1'
        captured = self.capture_ids(query, ['2601.00001v2'])
        receipt, search = self.record_search_payload(self.build_search_payload([captured], [query]))
        self.assertEqual(receipt['missing_identifiers'][0]['identifier'], query)
        self.assertFalse(check_search_coverage(search))

    def test_id_list_is_not_the_bound_query_when_search_query_is_present(self):
        captured = self.capture_ids('2601.00001v1', ['2601.00001v1'], query='all:sequence')
        with self.assertRaises(ResearchError) as caught:
            self.record_search_payload(self.build_search_payload([captured], ['2601.00001v1']))
        self.assertEqual(caught.exception.code, 'invalid_search')
        receipt, search = self.record_search_payload(self.build_search_payload([captured], ['all:sequence']))
        self.assertEqual(search['coverage'][0]['kind'], 'complete')
        captured = self.capture_ids('2601.00002v1', ['2601.00002v1'], query='')
        self.assertEqual(self.record_search_payload(self.build_search_payload(
            [captured], ['2601.00002v1']))[1]['coverage'][0]['kind'], 'complete')

    def test_cli_citing_works_example_executes_import_and_bound_search(self):
        examples = json.loads((Path(__file__).resolve().parents[1] / 'docs/research-cli-examples.json').read_text())
        imported = copy.deepcopy(examples['import-response'])
        captured = import_response(self.store, imported['provider'], json.dumps({'data': [dict(citingPaper=dict(
            url='https://example.org/citing-paper', title='A synthetic citing work'))]}).encode(),
            source_url=imported['source_url'], captured_at=imported['captured_at'], media_type=imported['media_type'],
            mappings=imported['mappings'], expected_revision=self.store.revision, request_id='citing-example')
        payload = copy.deepcopy(examples['search'])
        payload['responses'][0]['source_id'] = captured['source_ids'][0]
        earlier = copy.deepcopy(payload)
        completion = earlier.pop('completes')[0]
        earlier['id'] = completion['search_id']
        earlier['gaps'] = [gap['text'] for gap in completion['closed_gaps']]
        self.record_search_payload(earlier)
        receipt, search = self.record_search_payload(payload)
        self.assertEqual(search['queries'], ['arXiv:1208.4714'])
        self.assertEqual(receipt['found_work_ids'], captured['work_ids'])
        self.assertEqual(search['completes'][0]['search_id'], earlier['id'])
