"""Returned rows retain author judgments and provider evidence.

Synthetic incident shape: 20261114070000-openness-finding-unrecordable-and-unbindable-captures.
"""
import copy
import hashlib
import json

from test_search_receipts import SearchCaptureCase
import test_search_completes as completion_fixtures
from literature_fixtures import LiteratureCase
from openness_reading_fixtures import OpennessReadingCase
from research_fixtures import atom, entry
from research_harness.acquisition import import_response
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness import record_check
from research_harness.openness_minimum import check_search_coverage


class RowDispositionTests(SearchCaptureCase):
    def build_invalid_capture(self, **changes):
        row = dict(id='https://openalex.org/W9', title=None, type='conference-paper', **changes)
        return self.capture_page({'meta': {'count': 1}, 'results': [row]}), row

    def build_disposition(self, capture, index=0, reason='No identifiable article title is provided.'):
        return dict(source_id=capture['source_ids'][0], index=index, reason=reason)

    def test_record_retains_canonical_row_and_counts_it_as_inspected(self):
        capture, row = self.build_invalid_capture(description='é' * 21000)
        payload = self.build_search_payload([capture, capture])
        payload['row_dispositions'] = [self.build_disposition(capture)]
        receipt, search = self.record_search_payload(payload)
        saved = search['row_dispositions'][0]
        body = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.assertEqual(saved, dict(payload['row_dispositions'][0], provider='openalex',
            row_id=row['id'], record_type='conference-paper', row_digest=digest(row),
            row_byte_length=len(body.encode()), row_excerpt=body[:20000]))
        self.assertEqual(receipt['row_disposition_count'], 1)
        self.assertEqual(receipt['row_dispositions'], search['row_dispositions'])
        self.assertEqual(receipt['invalid_work_count'], 0)
        self.assertEqual(search['pending'], [])
        self.assertEqual(search['coverage'][0]['kind'], 'complete')
        self.assertEqual(search['coverage'][0]['inspected_count'], 1)
        self.assertTrue(check_search_coverage(search))
        self.assertEqual(record_check.check_authentication(self.store)['search_row_dispositions'], [search['id']])

    def test_refuses_imported_excluded_and_absent_rows_with_the_entry(self):
        capture = self.capture_page({'meta': {'count': 3}, 'results': [
            dict(id='https://openalex.org/W1', title='Synthetic article', type='article'),
            dict(id='https://openalex.org/W2', title=None, type='paratext'),
            dict(id='https://openalex.org/W3', title=None, type='article')]}, per_page=3)
        for index in (0, 1, 3, -1, True):
            disposition = self.build_disposition(capture, index)
            payload = dict(self.build_search_payload([capture]), row_dispositions=[disposition])
            before = self.store.snapshot()
            with self.subTest(index=index), self.assertRaises(ResearchError) as caught:
                self.record_search_payload(payload)
            self.assertEqual(caught.exception.code, 'invalid_search')
            self.assertEqual(caught.exception.details['entry'], disposition)
            self.assertEqual(self.store.snapshot(), before)

    def test_refuses_unbound_source_empty_reason_duplicates_and_malformed_entries(self):
        capture, _ = self.build_invalid_capture()
        other, _ = self.build_invalid_capture(note='Another capture')
        good = self.build_disposition(capture)
        for entries in (None, {}, 'rows', [None], [dict(good, reason=' ')],
                        [self.build_disposition(other)], [good, good], [dict(good, row_digest='a' * 64)]):
            with self.subTest(entries=entries), self.assertRaises(ResearchError) as caught:
                self.record_search_payload(dict(self.build_search_payload([capture]), row_dispositions=entries))
            self.assertEqual(caught.exception.code, 'invalid_search')
            self.assertIn('entry', caught.exception.details)

    def test_record_check_rejects_changed_row_digest_and_response_binding(self):
        capture, _ = self.build_invalid_capture()
        _, search = self.record_search_payload(dict(self.build_search_payload([capture]),
            row_dispositions=[self.build_disposition(capture)]))
        for changes in (dict(row_digest='a' * 64), dict(source_id='missing-response')):
            changed = copy.deepcopy(search)
            changed['row_dispositions'][0].update(changes)
            def put(transaction):
                transaction.put('literature_search', search['id'], changed)
            self.store.mutate('test.tamper', changes, put, expected_revision=self.store.revision,
                              request_id='tamper-' + next(iter(changes)))
            with self.assertRaises(ResearchError) as caught:
                record_check.check_authentication(self.store)
            self.assertEqual(caught.exception.code, 'invalid_search')
            self.assertEqual(caught.exception.details['entry'], changed['row_dispositions'][0])

    def test_refuses_an_imported_arxiv_row_with_a_category_failure(self):
        for number, category in enumerate((None, 'not a category'), 1):
            capture = import_response(self.store, 'arxiv', atom([entry('2601.%05dv1' % number,
                category=category)], total=1), source_url='https://export.arxiv.org/api/query?search_query=bounded',
                captured_at='2026-09-07T12:00:00Z', expected_revision=self.store.revision,
                request_id='category-row-%d' % number)
            self.assertEqual(len(capture['work_ids']), 1)
            disposition = self.build_disposition(capture)
            payload = dict(self.build_search_payload([capture]), row_dispositions=[disposition])
            with self.subTest(category=category), self.assertRaises(ResearchError) as caught:
                self.record_search_payload(payload)
            self.assertEqual(caught.exception.code, 'invalid_search')
            self.assertEqual(caught.exception.details['entry'], disposition)

    def test_atom_disposition_digest_uses_exact_entry_bytes(self):
        raw_entry = entry('2601.00009v1').replace('<title>', '<title>\n').replace(
            'Source 2601.00009v1', '')
        # Make an invalid entry without changing its formatting, namespace or whitespace.
        import re
        raw_entry = re.sub(r'<title>.*?</title>', '<title> </title>', raw_entry, flags=re.S)
        data = atom([raw_entry], total=1)
        capture = import_response(self.store, 'arxiv', data,
            source_url='https://export.arxiv.org/api/query?search_query=bounded',
            captured_at='2026-09-07T12:00:00Z', expected_revision=self.store.revision, request_id='atom-row')
        _, search = self.record_search_payload(dict(self.build_search_payload([capture]),
            row_dispositions=[self.build_disposition(capture)]))
        saved = search['row_dispositions'][0]
        self.assertEqual(saved['row_digest'], hashlib.sha256(raw_entry.encode()).hexdigest())
        self.assertEqual(saved['row_byte_length'], len(raw_entry.encode()))
        self.assertEqual(saved['row_excerpt'], raw_entry)
        self.assertTrue(check_search_coverage(search))


class RowDispositionCompletionTests(LiteratureCase):
    capture_page = completion_fixtures.SearchCompletionTests.capture_page
    build_work = completion_fixtures.SearchCompletionTests.build_work
    build_payload = completion_fixtures.SearchCompletionTests.build_payload
    save_search = completion_fixtures.SearchCompletionTests.save_search
    assert_completion_refused = completion_fixtures.SearchCompletionTests.assert_completion_refused

    def build_completion(self, earlier_row, later_row, *, complete=False):
        old_capture = self.capture_page([earlier_row], total=20)
        earlier = self.save_search(self.build_payload([old_capture], hits_per_query=1))
        capture = self.capture_page([later_row], total=1 if complete else 20, sort='relevance_score:desc')
        later = self.build_payload([capture], hits_per_query=1,
            row_dispositions=[dict(source_id=capture['source_ids'][0], index=0, reason='No usable title.')],
            completes=[dict(search_id=earlier['id'], closed_gaps=[])])
        return earlier, later

    def test_completion_accepts_a_disposition_for_the_same_identifier_with_changed_fields(self):
        old, later = self.build_completion(self.build_work(9, title=None), self.build_work(9, title=None, year=2026))
        self.assertEqual(self.save_search(later)['completes'][0]['not_returned'], [])

    def test_completion_accepts_unidentified_equal_digest_under_top_k(self):
        row = dict(title=None, type='article')
        old, later = self.build_completion(row, row)
        saved = self.save_search(later)
        self.assertEqual(saved['coverage'][0]['kind'], 'top_k')
        self.assertEqual(saved['completes'][0]['not_returned'], [])

    def test_completion_refuses_unidentified_different_digest_under_top_k_and_names_earlier_row(self):
        row = dict(title=None, type='article', note='earlier')
        old, later = self.build_completion(row, dict(row, note='later'))
        self.assert_completion_refused(later, 'rows', query='bounded', row_id=None, row_digest=digest(row))
        later['id'] += '-complete'
        source = self.capture_page([dict(row, note='later')], sort='relevance_score:desc')
        later['responses'][0]['source_id'] = source['source_ids'][0]
        later['row_dispositions'][0]['source_id'] = source['source_ids'][0]
        self.assertEqual(self.save_search(later)['coverage'][0]['kind'], 'complete')

    def test_completion_refuses_a_disposition_for_another_identifier(self):
        row = self.build_work(9, title=None)
        old, later = self.build_completion(row, self.build_work(8, title=None))
        self.assert_completion_refused(later, 'rows', row_id=row['id'])

    def test_completion_matches_a_disposition_only_within_its_captured_query_identity(self):
        row = self.build_work(9, title=None)
        first_a = self.capture_page([row], filter='type:article')
        first_b = self.capture_page([], filter='type:book')
        earlier = self.save_search(self.build_payload([first_a, first_b]))
        later_a = self.capture_page([], filter='type:article')
        later_b = self.capture_page([row], filter='type:book')
        payload = self.build_payload([later_a, later_b], row_dispositions=[dict(
            source_id=later_b['source_ids'][0], index=0, reason='The other query returns this untitled row.')],
            completes=[dict(search_id=earlier['id'], closed_gaps=[])])
        saved = self.save_search(payload)
        self.assertEqual(saved['completes'][0]['not_returned'], [dict(query='bounded', row_id=row['id'])])

    def test_completion_never_matches_an_identified_and_an_unidentified_row(self):
        for old_row, new_row in ((dict(title=None), self.build_work(9, title=None)),
                                 (self.build_work(9, title=None), dict(title=None))):
            old, later = self.build_completion(old_row, new_row)
            self.assert_completion_refused(later, 'rows', row_id=old_row.get('id'))

    def test_two_completions_require_a_fresh_disposition_or_complete_absence(self):
        row = self.build_work(9, title=None)
        first, second_payload = self.build_completion(row, row)
        second = self.save_search(second_payload)
        for rows, total, dispose, accepted in (([row], 20, True, True), ([], 0, False, True),
                                               ([self.build_work(8, title=None)], 20, True, False)):
            captured = self.capture_page(rows, total=total, sort='relevance_score:desc')
            payload = self.build_payload([captured], hits_per_query=1,
                completes=[dict(search_id=second['id'], closed_gaps=[])])
            if dispose:
                payload['row_dispositions'] = [dict(source_id=captured['source_ids'][0], index=0, reason='Still untitled.')]
            if accepted:
                third = self.save_search(payload)
                from research_harness.openness_minimum import list_minimum_gaps
                minimum = dict(schema_version=1, statement=third['question'], neighbours=[],
                    searches=[dict(formulation=third['question'], search_digest=digest(third))],
                    reading_digests=[], settling_work_id=None)
                gaps = list_minimum_gaps(minimum, third['question'], self.store.snapshot()['records'], self.artifacts)
                self.assertFalse(any(gap['condition'] == 'search_integrity' for gap in gaps))
            else:
                self.assert_completion_refused(payload, 'rows', row_id=row['id'])


class RowDispositionExportTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (121000,)
    capture_page = SearchCaptureCase.capture_page
    build_search_payload = SearchCaptureCase.build_search_payload
    record_search_payload = SearchCaptureCase.record_search_payload

    def test_two_pages_53_rows_export_requires_a_disposition_and_preserves_the_stored_search(self):
        # Source: 20261114070000-openness-finding-unrecordable-and-unbindable-captures T2.
        self.limit_input(1300000)
        rows = [dict(id='https://openalex.org/W%d' % (100 + index),
                     title='Synthetic result %d' % index if index != 2 else None,
                     type='conference-paper') for index in range(53)]
        pages = [self.capture_page(dict(meta=dict(count=53), results=rows[:50]), per_page=50),
                 self.capture_page(dict(meta=dict(count=53), results=rows[50:]), per_page=50, page=2)]
        payload = self.build_search_payload(pages)
        payload.update(question=self.selected['statement'], captured_at='2026-10-17T12:00:00Z', hits_per_query=53)
        receipt, earlier = self.record_search_payload(payload)
        old_digest = digest(earlier)
        old_bytes = json.dumps(earlier, sort_keys=True)
        self.assertEqual(receipt['invalid_work_count'], 1)
        self.assertFalse(check_search_coverage(earlier))
        from research_harness.problem_openness import export_openness
        from research_harness.openness_minimum import list_minimum_gaps
        def build_spec(search):
            return dict(assignment_id='turn-1', reviewer_id='openness-reviewer', minimum=dict(
                searches=[dict(formulation=self.selected['statement'], search_id=row['id'])
                          for row in (self.search, search)], settling_work_id=None, duplicate_resolutions=[]))
        with self.assertRaises(ResearchError) as caught:
            export_openness(self.store, build_spec(earlier), self.root / 'refused-export')
        self.assertEqual(caught.exception.code, 'openness_minimum_gaps')
        self.assertFalse((self.root / 'refused-export').exists())
        payload.update(id=payload['id'] + '-disposed', row_dispositions=[dict(
            source_id=pages[0]['source_ids'][0], index=2, reason='The provider returned no title to identify an article.')],
            completes=[dict(search_id=earlier['id'], closed_gaps=[])])
        receipt, later = self.record_search_payload(payload)
        self.assertEqual(later['coverage'][0]['kind'], 'complete')
        self.assertEqual(later['coverage'][0]['returned_count'], 53)
        self.assertEqual(later['coverage'][0]['inspected_count'], 53)
        self.assertEqual(receipt['row_disposition_count'], 1)
        self.assertTrue(check_search_coverage(later))
        exported = export_openness(self.store, build_spec(later), self.root / 'accepted-export')
        packet = json.loads(self.artifacts.read(exported['native_packet']))
        minimum = packet['scientific_context']['openness_minimum']
        gaps = list_minimum_gaps(minimum, self.selected['statement'], self.store.snapshot()['records'], self.artifacts)
        self.assertFalse(any(row['condition'] == 'search_integrity' for row in gaps))
        self.assertEqual(exported['audit_forecast']['outcomes']['supported_open'], 'possible')
        saved = self.store.snapshot()['records']['literature_search'][earlier['id']]
        self.assertEqual(digest(saved), old_digest)
        self.assertEqual(json.dumps(saved, sort_keys=True), old_bytes)
        self.assertNotIn('row_dispositions', saved)
