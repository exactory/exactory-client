"""UB9/G5: every recorded search key has a line in the v6 field guide."""

import json

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading
from research_harness.literature import record_search
from openness_legacy_fixtures import load_0_87_0_openness_reading
import test_research_search_pages as page_fixtures


class SearchFieldGuideTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (121000,)
    page = page_fixtures.SearchPageTests.page
    search_pages = page_fixtures.SearchPageTests.search

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)

    def test_g5_table_matches_keys_recorded_by_search_including_optional_fields(self):
        # Exercise provisional, scoped, bound and native parsed records through the real search command.
        provisional = self.store.snapshot()['records']['literature_search'][self.search['id']]
        payload = self.capture_search()
        payload.update(id='unscoped', question='Which finite examples are known?', resolved=[], depth={'hits_per_query': 10})
        self.mutate(record_search, payload)
        self.scope([self.versions[0]])
        self.mutate(record_search, dict(payload, id='scoped', binding=dict(
            provisional_search_id='unscoped', reason='The same receipts address these roots.')))
        page = self.page('crossref', {'message': {'total-results': 0, 'items': []}},
                         {'query': 'bounded', 'rows': '10'})
        self.search_pages([page])
        records = self.store.snapshot()['records']['literature_search']
        keys = {key for search in records.values() for key in search}
        self.assertEqual(set(reading.SEARCH_FIELD_GUIDE), keys)
        for key in keys:
            row = reading.SEARCH_FIELD_GUIDE[key]
            self.assertEqual(set(row), {'class', 'line'})
            self.assertTrue(row['class'])
            self.assertTrue(row['line'])
        self.assertEqual(reading.SEARCH_FIELD_GUIDE['provisional']['class'], 'scope_binding')
        self.assertEqual(reading.SEARCH_FIELD_GUIDE['coverage']['class'], 'coverage')
        self.assertEqual(reading.SEARCH_FIELD_GUIDE['impact']['class'], 'author_judgment')
        self.assertEqual(provisional['scope_digest'], None)
        self.assertTrue(provisional['provisional'])

    def test_all_provisional_v6_packet_carries_the_guide_in_reading_and_final_prompts(self):
        # Sources: 20261112135015-openness-reading-turn-stopped-by-provider-safeguards T10;
        # 20261114000100-openness-review-provisional-flag-and-web-sources (second occurrence).
        reference = self.export()
        native = json.loads(self.artifacts.read(reference))
        before = self.store.snapshot()['records']
        bound = {row['search_digest'] for row in native['scientific_context']['openness_minimum']['searches']}
        from research_harness.evidence import digest
        self.assertTrue(all(row['provisional'] and row['scope_digest'] is None
                            for row in before['literature_search'].values() if digest(row) in bound))
        turns = self.run_reading_turns(reference, turn_count=8)
        records = self.store.snapshot()['records']
        for identifier in (turns[0], turns[-1]):
            prompt = records['review_assignment'][identifier]['prompt']
            self.assertTrue(prompt['template_id'].startswith(reading.PROTOCOL_V6))
            for key, row in reading.SEARCH_FIELD_GUIDE.items():
                line = key + ': ' + row['line']
                self.assertEqual(prompt['text'].count(line), 1, key)
            self.assertIn('provisional: [X2 R42: scope_binding; no literature scope', prompt['text'])
            self.assertIn('says nothing about coverage or outstanding readings', prompt['text'])
            self.assertIn(reading.SUPPORTING_READING_SENTENCE, prompt['text'])
        for protocol, prompts in load_0_87_0_openness_reading().PROMPTS.items():
            self.assertEqual(reading.PROMPTS[protocol], prompts)
        self.assertEqual(self.store.snapshot()['records']['literature_search'], before['literature_search'])
