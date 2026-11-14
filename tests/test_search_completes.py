"""G6: completions account for every saved row before retiring a search.

Synthetic shape from 20261112143000-openness-chain-ends-unresolved-on-search-integrity-gaps.
"""
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from literature_fixtures import LiteratureCase
from openness_reading_fixtures import OpennessReadingCase
from research_harness.acquisition import import_response
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.literature import record_search
from research_harness import openness_minimum as minimums


class SearchCompletionTests(LiteratureCase):
    def capture_page(self, rows, *, query='bounded', total=None, date='2026-10-15T12:00:00Z', **parameters):
        self.sequence += 1
        return import_response(self.store, 'openalex', json.dumps({
            'meta': {'count': len(rows) if total is None else total}, 'results': rows}).encode(),
            source_url='https://api.openalex.org/works?' + urlencode(dict(search=query, per_page=10, **parameters)),
            captured_at=date, expected_revision=self.store.revision, request_id='page-%d' % self.sequence)

    def build_work(self, number=1, **changes):
        return dict({'id': 'https://openalex.org/W%d' % number, 'title': 'Synthetic result %d' % number,
                     'type': 'article'}, **changes)

    def build_payload(self, pages, *, queries=None, **changes):
        queries = queries or ['bounded'] * len(pages)
        self.sequence += 1
        found = sorted({work for page in pages for work in page['work_ids']})
        value = dict(id='search-%d' % self.sequence, profile='research', purpose='direct',
            question='Bound every finite sequence.', scope='The specified saved query.', queries=list(dict.fromkeys(queries)),
            responses=[dict(source_id=page['source_ids'][0], query=query) for page, query in zip(pages, queries)],
            captured_at='2026-10-15T12:00:00Z', found_work_ids=found, verdict='nothing-new', cited_work_ids=[],
            dispositions=[dict(work_id=work, disposition='relevant', reason='The result addresses the formulation.')
                          for work in found], impact='The saved query informs the formulation.', gaps=[])
        value.update(changes)
        return value

    def save_search(self, payload):
        self.mutate(record_search, payload)
        return self.store.snapshot()['records']['literature_search'][payload['id']]

    def build_pair(self, earlier_rows=None, later_rows=None, *, earlier_options=None, later_options=None, **earlier_changes):
        old = self.save_search(self.build_payload([self.capture_page(
            earlier_rows if earlier_rows is not None else [self.build_work()], **(earlier_options or {}))], **earlier_changes))
        new = self.build_payload([self.capture_page(
            later_rows if later_rows is not None else [self.build_work()], **(later_options or {}))],
            completes=[dict(search_id=old['id'], closed_gaps=[])])
        return old, new

    def assert_completion_refused(self, payload, condition, **details):
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.save_search(payload)
        error = caught.exception
        self.assertEqual(error.code, 'search_completion_invalid')
        self.assertEqual(error.details['condition'], condition)
        self.assertEqual(error.details['search_id'], payload['completes'][0]['search_id'])
        for key, value in details.items():
            self.assertEqual(error.details[key], value)
        self.assertEqual(self.store.snapshot(), before)

    def test_formulation_requires_same_profile_and_question(self):
        for changes in (dict(question='Another formulation.'), dict(profile='verification')):
            old, new = self.build_pair()
            new.update(changes)
            self.assert_completion_refused(new, 'formulation')

    def test_time_refuses_an_earlier_capture(self):
        old, new = self.build_pair(later_options=dict(date='2026-10-14T12:00:00Z'))
        new['captured_at'] = '2026-10-14T12:00:00Z'
        self.assert_completion_refused(new, 'time')

    def test_queries_refuses_changed_matching_filter_and_missing_query(self):
        old, new = self.build_pair(later_options=dict(filter='is_paratext:false'))
        self.assert_completion_refused(new, 'queries', query='bounded')
        old, new = self.build_pair(later_options=dict(query='different'))
        new['queries'] = ['different']
        new['responses'][0]['query'] = 'different'
        self.assert_completion_refused(new, 'queries', query='bounded')

    def test_clean_requires_integrity_no_gaps_and_nonweaker_coverage(self):
        cases = [dict(later_rows=[self.build_work(), self.build_work(9, title=None)]),
                 dict(later_options=dict(total=20)), {}]
        for changes in cases:
            old, new = self.build_pair(**changes)
            if not changes:
                new['gaps'] = ['An unclosed gap.']
            self.assert_completion_refused(new, 'clean')

    def test_pages_requires_a_whole_or_prefix_after_disconnected_pages(self):
        old, new = self.build_pair(earlier_options=dict(total=20, page=2), later_options=dict(total=20))
        self.assert_completion_refused(new, 'pages', query='bounded')

    def test_pages_do_not_treat_a_disconnected_empty_page_as_a_verified_prefix(self):
        first = self.capture_page([self.build_work()], total=20)
        detached = self.capture_page([], total=20, page=3)
        old = self.save_search(self.build_payload([first, detached]))
        new = self.build_payload([self.capture_page([self.build_work()], total=20)],
            completes=[dict(search_id=old['id'], closed_gaps=[])])
        self.assert_completion_refused(new, 'pages', query='bounded')

    def test_rows_refuses_invalid_row_lost_to_a_smaller_bound_or_changed_order(self):
        for options in ({}, dict(sort='publication_date:desc')):
            old, new = self.build_pair(earlier_rows=[self.build_work(), self.build_work(9, title=None)],
                earlier_options=dict(total=20), later_options=dict(total=20, **options))
            new['hits_per_query'] = 1
            self.assert_completion_refused(new, 'rows', query='bounded', row_id='https://openalex.org/W9')

    def test_works_refuses_a_missing_previously_found_work(self):
        old, new = self.build_pair(earlier_rows=[self.build_work(), self.build_work(2)])
        self.assert_completion_refused(new, 'works', work_id='openalex:W2')

    def test_judgments_requires_a_reason_for_each_changed_material_judgment(self):
        old, new = self.build_pair()
        new['dispositions'][0]['disposition'] = 'out_of_scope'
        self.assert_completion_refused(new, 'judgments', work_id='openalex:W1')
        new['resolved'] = [dict(work_id='openalex:W1', reason='The full text uses different assumptions.')]
        self.assertEqual(self.save_search(new)['completes'][0]['search_digest'], digest(old))

    def test_gaps_requires_an_author_statement_for_each_exact_gap(self):
        old, new = self.build_pair(gaps=['A missing page.', 'An uncertain item.'])
        self.assert_completion_refused(new, 'gaps', text='A missing page.')
        new['completes'][0]['closed_gaps'] = [dict(text=gap, how='The new complete capture closes this gap.') for gap in old['gaps']]
        self.assertEqual(self.save_search(new)['completes'][0]['closed_gaps'], new['completes'][0]['closed_gaps'])

    def test_complete_enumeration_records_missing_and_unidentified_rows(self):
        old, new = self.build_pair(earlier_rows=[self.build_work(), self.build_work(9, title=None), {'title': None}],
                                  earlier_options=dict(total=20))
        saved = self.save_search(new)
        self.assertEqual(saved['completes'], [dict(search_id=old['id'], search_digest=digest(old), closed_gaps=[],
            not_returned=[dict(query='bounded', row_id='https://openalex.org/W9'), dict(query='bounded', row_id=None)])])

    def test_unidentified_row_requires_complete_enumeration(self):
        old, new = self.build_pair(earlier_rows=[self.build_work(), {'title': None}],
                                  earlier_options=dict(total=20), later_options=dict(total=20))
        self.assert_completion_refused(new, 'rows', row_id=None)

    def test_recorded_paratext_exclusion_accounts_for_a_row(self):
        rows = [self.build_work(), self.build_work(9, title=None, type='paratext')]
        old, new = self.build_pair(earlier_rows=rows, later_rows=rows,
                                  earlier_options=dict(total=20), later_options=dict(total=20))
        self.assertEqual(self.save_search(new)['completes'][0]['not_returned'], [])

    def test_payload_refuses_missing_duplicate_self_targets_and_invalid_closed_gaps(self):
        old, new = self.build_pair()
        cases = [None, {}, [dict(search_id='absent', closed_gaps=[])],
                 [dict(search_id=new['id'], closed_gaps=[])], new['completes'] * 2,
                 [dict(search_id=old['id'], closed_gaps=[dict(text='gap', how='')])],
                 [dict(search_id=old['id'], closed_gaps=[], not_returned=[])]]
        for value in cases:
            payload = dict(new, completes=value)
            with self.subTest(value=value), self.assertRaises(ResearchError) as caught:
                self.save_search(payload)
            self.assertEqual(caught.exception.code, 'search_completion_invalid')
            self.assertEqual(caught.exception.details['condition'], 'payload')

    def test_one_search_can_complete_two_distinct_earlier_searches(self):
        old, new = self.build_pair()
        other = self.save_search(self.build_payload([self.capture_page([self.build_work()])]))
        new['completes'].append(dict(search_id=other['id'], closed_gaps=[]))
        saved = self.save_search(new)
        self.assertEqual([row['search_digest'] for row in saved['completes']], [digest(old), digest(other)])

    def test_only_bound_completers_remove_transitive_ancestors_and_explicit_binding_is_allowed(self):
        old, new = self.build_pair()
        middle = self.save_search(new)
        last = self.save_search(self.build_payload([self.capture_page([self.build_work()])],
            completes=[dict(search_id=middle['id'], closed_gaps=[])]))
        records = self.store.snapshot()['records']
        formulation = old['question']
        def build_minimum(searches):
            return dict(schema_version=1, statement=formulation, neighbours=[], reading_digests=[], settling_work_id=None,
                        searches=[dict(formulation=formulation, search_digest=digest(row)) for row in searches])
        minimums.validate_minimum_bindings(build_minimum([last]), formulation, records)
        minimums.validate_minimum_bindings(build_minimum([old, middle, last]), formulation, records)
        with self.assertRaises(ResearchError) as caught:
            minimums.validate_minimum_bindings(build_minimum([old]), formulation, records)
        self.assertEqual(caught.exception.code, 'openness_search_stale')
        self.assertIn(last['id'], caught.exception.details['search_ids'])

    def test_without_completes_material_set_matches_frozen_reader_and_old_digest_stays(self):
        old, new = self.build_pair()
        before = json.dumps(old, sort_keys=True)
        legacy = {'__name__': 'research_harness.legacy_minimum', '__package__': 'research_harness'}
        path = Path(__file__).with_name('fixtures') / 'openness_minimum_0_85_3.py.txt'
        exec(compile(path.read_text(), str(path), 'exec'), legacy)
        records = self.store.snapshot()['records']
        self.assertEqual(minimums.find_material_searches(records, {old['question']}),
                         legacy['find_material_searches'](records, {old['question']}))
        self.save_search(new)
        stored = self.store.snapshot()['records']['literature_search'][old['id']]
        self.assertEqual(json.dumps(stored, sort_keys=True), before)
        self.assertEqual(digest(stored), digest(old))
        self.assertNotIn('completes', stored)

    def test_legacy_prompt_hashes_stay_frozen_and_v6_explains_completes(self):
        from research_harness import openness_reading as reading
        frozen = json.loads((Path(__file__).with_name('fixtures') / 'search-completes-legacy-prompts.json').read_text())
        for protocol, prompts in frozen.items():
            for kind, expected in prompts.items():
                self.assertEqual(hashlib.sha256(reading.PROMPTS[protocol][kind].encode()).hexdigest(), expected)
        for prompt in reading.PROMPTS[reading.PROTOCOL_V6].values():
            self.assertIn(reading.SEARCH_COMPLETION_SENTENCE, prompt)

    def test_invalid_search_ids_with_completes_retain_the_normal_id_refusal(self):
        old, new = self.build_pair()
        for identifier in ([], {}, None, ''):
            with self.subTest(identifier=identifier), self.assertRaises(ResearchError) as caught:
                self.save_search(dict(new, id=identifier, completes=[]))
            self.assertEqual(caught.exception.code, 'invalid_input')

    def test_transitive_notices_retain_missing_rows_and_attribute_the_completion_edge(self):
        old, new = self.build_pair(earlier_rows=[self.build_work(), self.build_work(9, title=None)],
                                  earlier_options=dict(total=20), gaps=['A missing page.'])
        new['completes'][0]['closed_gaps'] = [dict(text=old['gaps'][0], how='The complete capture closes the page gap.')]
        middle = self.save_search(new)
        last = self.save_search(self.build_payload([self.capture_page([self.build_work()])],
            completes=[dict(search_id=middle['id'], closed_gaps=[])]))
        notice = minimums.list_minimum_notices(dict(searches=[dict(formulation=old['question'], search_digest=digest(last))]),
            self.store.snapshot()['records'], self.artifacts)[0]
        self.assertEqual([(row['completing_search_id'], row['search_id']) for row in notice['completes']],
                         [(last['id'], middle['id']), (middle['id'], old['id'])])
        self.assertEqual(notice['completes'][1]['not_returned'],
                         [dict(query='bounded', row_id='https://openalex.org/W9')])
        self.assertEqual(notice['completes'][1]['closed_gaps'], new['completes'][0]['closed_gaps'])

    def test_authentication_checks_the_exact_search_pin(self):
        from research_harness.record_check import check_authentication
        old, new = self.build_pair()
        saved = self.save_search(new)
        self.assertEqual(check_authentication(self.store)['search_completions'], [saved['id']])
        for target, pin in (('absent', digest(old)), (old['id'], '0' * 64)):
            def alter(transaction):
                row = copy.deepcopy(saved)
                row['completes'][0].update(search_id=target, search_digest=pin)
                transaction.put('literature_search', row['id'], row)
            self.sequence += 1
            self.store.mutate('test.damage', {}, alter, expected_revision=self.store.revision, request_id='damage-%d' % self.sequence)
            with self.assertRaises(ResearchError) as caught:
                check_authentication(self.store)
            self.assertEqual(caught.exception.code, 'search_completion_invalid')
            self.assertEqual(caught.exception.details['condition'], 'search_digest')

    def test_authentication_refuses_missing_or_malformed_completion_pins(self):
        from research_harness.record_check import check_authentication
        old, new = self.build_pair()
        saved = self.save_search(new)
        for entries in (None, [None], [dict(search_id=old['id'])], [dict(search_id=[], search_digest=digest(old))]):
            def alter(transaction):
                transaction.put('literature_search', saved['id'], dict(saved, completes=entries))
            self.sequence += 1
            self.store.mutate('test.damage', {}, alter, expected_revision=self.store.revision, request_id='pins-%d' % self.sequence)
            with self.subTest(entries=entries), self.assertRaises(ResearchError) as caught:
                check_authentication(self.store)
            self.assertEqual(caught.exception.code, 'search_completion_invalid')
            self.assertEqual(caught.exception.details['condition'], 'search_digest')

    def test_stop_hands_off_to_search_and_example_exposes_completes(self):
        from research_harness.stop_classes import describe_terminal
        stop = describe_terminal('search_completion_invalid')
        self.assertEqual(stop['state'], 'handoff')
        self.assertEqual(stop['command'], 'search')
        examples = json.loads((Path(__file__).resolve().parents[1] / 'docs/research-cli-examples.json').read_text())
        self.assertEqual(set(examples['search']['completes'][0]), {'search_id', 'closed_gaps'})
        self.assertEqual(set(examples['search']['completes'][0]['closed_gaps'][0]), {'text', 'how'})

    def test_malformed_judgment_stays_a_payload_refusal(self):
        old, new = self.build_pair()
        self.save_search(new)
        for invalid in (None, [None], [dict(work_id='openalex:W1', disposition='relevant')]):
            with self.subTest(invalid=invalid), self.assertRaises(ResearchError) as caught:
                self.save_search(dict(new, dispositions=invalid))
            self.assertEqual(caught.exception.code, 'invalid_search')

    def test_rows_can_be_imported_through_an_alias(self):
        old, new = self.build_pair(earlier_rows=[self.build_work(), self.build_work(9, title=None)],
                                  later_rows=[self.build_work(), self.build_work(2)])
        # A provider assertion on the imported later work names the earlier invalid row.
        def assert_alias(transaction):
            work = transaction.get('work', 'openalex:W2')
            work['aliases'].append('openalex:W9')
            transaction.put('work', work['id'], work)
        self.store.mutate('test.alias', {}, assert_alias, expected_revision=self.store.revision, request_id='assert-alias')
        new['dispositions'][1]['disposition'] = 'out_of_scope'
        self.assertEqual(self.save_search(new)['completes'][0]['not_returned'], [])

    def test_web_and_mcp_identity_keeps_provider_address_and_bound_query(self):
        for provider in ('web', 'mcp'):
            def build_mapped(url, query='bounded'):
                self.sequence += 1
                captured = import_response(self.store, provider, json.dumps(dict(query=query, results=[])).encode(),
                    source_url=url, captured_at='2026-10-15T12:00:00Z', media_type='application/json', mappings=[],
                    expected_revision=self.store.revision, request_id='mapped-%d' % self.sequence)
                return self.build_payload([captured], queries=[query], responses=[dict(
                    source_id=captured['source_ids'][0], query=query, results_pointer='/results',
                    query_locator=dict(kind='json', pointer='/query', value=query))])
            old = self.save_search(build_mapped('https://example.org/search?a=1'))
            later = build_mapped('https://example.org/search?a=2')
            later['completes'] = [dict(search_id=old['id'], closed_gaps=[])]
            self.save_search(later)
            different = build_mapped('https://example.org/another')
            different['completes'] = later['completes']
            self.assert_completion_refused(different, 'queries')

    def test_mapped_rows_account_for_the_original_imported_work_identifiers(self):
        work = self.metadata()
        old = self.capture_search(found=[work])
        old['question'] = 'Bound every finite sequence.'
        earlier = self.save_search(old)
        later = dict(old, id='later-mapped-search', completes=[dict(search_id=earlier['id'], closed_gaps=[])])
        self.assertEqual(self.save_search(later)['completes'][0]['not_returned'], [])

    def test_mapped_url_rows_use_the_importers_canonical_work_identifier(self):
        captured = import_response(self.store, 'web', json.dumps(dict(query='bounded', results=[
            dict(id='https://EXAMPLE.org', title='A synthetic web result')])).encode(),
            source_url='https://example.org/search?q=bounded', captured_at='2026-10-15T12:00:00Z',
            media_type='application/json', mappings=[dict(id='/results/0/id', title='/results/0/title')],
            expected_revision=self.store.revision, request_id='url-row')
        old = self.build_payload([captured], responses=[dict(source_id=captured['source_ids'][0],
            query='bounded', results_pointer='/results')])
        earlier = self.save_search(old)
        self.assertEqual(earlier['found_work_ids'], ['url:https://example.org/'])
        later = dict(old, id='canonical-url-search', completes=[dict(search_id=earlier['id'], closed_gaps=[])])
        self.assertEqual(self.save_search(later)['completes'][0]['not_returned'], [])

    def test_scoped_searches_can_complete_with_both_questions_absent(self):
        self.scope([self.metadata()])
        old = self.build_payload([self.capture_page([self.build_work()])])
        old.pop('question')
        earlier = self.save_search(old)
        later = self.build_payload([self.capture_page([self.build_work()])],
            completes=[dict(search_id=earlier['id'], closed_gaps=[])])
        later.pop('question')
        self.assertEqual(self.save_search(later)['completes'][0]['search_digest'], digest(earlier))

    def test_row_ids_include_invalid_crossref_arxiv_results(self):
        from research_fixtures import atom, entry
        from research_harness.search_pages import read_search_row_ids
        for provider, data, url, expected in (
            ('crossref', json.dumps({'message': {'total-results': 2, 'items': [
                {'DOI': '10.1234/synthetic', 'title': ['A synthetic result']}, {'DOI': '10.1234/untitled'}]}}).encode(),
             'https://api.crossref.org/works?query=bounded&rows=10', ['10.1234/synthetic', '10.1234/untitled']),
            ('arxiv', atom([entry('2601.00001v1'), entry('2601.00002v1')], total=2),
             'https://export.arxiv.org/api/query?search_query=bounded&max_results=10',
             ['http://arxiv.org/abs/2601.00001v1', 'http://arxiv.org/abs/2601.00002v1'])):
            self.sequence += 1
            captured = import_response(self.store, provider, data, source_url=url, captured_at='2026-10-15T12:00:00Z',
                expected_revision=self.store.revision, request_id='registry-%d' % self.sequence)
            records = self.store.snapshot()['records']
            source = records['source'][captured['source_ids'][0]]
            self.assertEqual(read_search_row_ids(records, self.artifacts, source, dict(query='bounded')), expected)


class SearchCompletionExportTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (80000, 80000)
    capture_page = SearchCompletionTests.capture_page
    save_search = SearchCompletionTests.save_search

    def record_searches(self):
        old = self.capture_search(found=self.versions)
        old['id'] = 'incomplete-search'
        old['captured_at'] = '2026-10-15T12:00:00Z'
        old['question'] = self.selected['statement']
        page = self.capture_page([{'id': 'https://openalex.org/W9', 'title': None, 'type': 'article'}], total=2)
        old['queries'].append('bounded')
        old['responses'].append(dict(source_id=page['source_ids'][0], query='bounded'))
        self.earlier = self.save_search(old)
        later = copy.deepcopy(old)
        later.update(id='complete-search', completes=[dict(search_id=old['id'], closed_gaps=[])])
        page = self.capture_page([])
        later['responses'][-1]['source_id'] = page['source_ids'][0]
        return [self.save_search(later)]

    def test_two_query_repair_binds_only_later_search_and_delivers_notice_to_v6(self):
        from research_harness.problem_openness import export_openness
        from research_harness import openness_reading as reading
        self.limit_input(1300000)
        result = export_openness(self.store, dict(assignment_id='turn-1', reviewer_id='openness-reviewer',
            minimum=dict(searches=[dict(formulation=self.selected['statement'], search_id=self.search['id'])],
                         settling_work_id=None, duplicate_resolutions=[])), self.root / 'completed-export')
        native = json.loads(self.artifacts.read(result['native_packet']))
        notice = result['audit_forecast']['notices'][0]
        self.assertEqual(notice['completes'], [dict(row, completing_search_id=self.search['id'])
                                              for row in self.search['completes']])
        self.assertEqual(notice['completes'][0]['not_returned'], [dict(query='bounded', row_id='https://openalex.org/W9')])
        self.assertEqual(native['scientific_context']['audit_forecast'], result['audit_forecast'])
        self.assertEqual([row['search_digest'] for row in native['scientific_context']['openness_minimum']['searches']],
                         [digest(self.search)])
        assigned = self.assign('turn-1', result['native_packet'])
        packet = self.read_packet('turn-1')
        record = reading._find_record(packet)
        self.assertEqual(record['protocol'], reading.PROTOCOL_V6)
        units = reading.list_reading_units(self.artifacts, reading.list_record_rows(self.artifacts, native))
        ids = {unit.get('record_id') for unit in units}
        self.assertIn(self.search['id'], ids)
        self.assertNotIn(self.earlier['id'], ids)
        plan = packet[reading.TURN_KEY]['reading_plan']
        chain_units = [unit for key in ('delivered_before', 'delivered_now', 'remaining') for unit in plan[key]]
        self.assertNotIn(self.earlier['id'], {unit.get('record_id') for unit in chain_units})
        for prompt in reading.PROMPTS[reading.PROTOCOL_V6].values():
            self.assertIn(reading.SEARCH_COMPLETION_SENTENCE, prompt)
        self.assertIn('closed_gaps', reading.SEARCH_COMPLETION_SENTENCE)
