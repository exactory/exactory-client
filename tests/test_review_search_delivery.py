"""Exact whole-field sharing never changes an original search or grants delivery from storage."""

import copy
import importlib
import json
import unittest

from openness_search_fixtures import build_fifteen_searches
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_openness import serialize_record


class SearchDeliveryTests(unittest.TestCase):
    def setUp(self):
        from research_harness import openness_reading
        self.assertEqual(openness_reading.find_chain_protocol({'kind': 'result',
            'contract': {'orchestration': 'problem_creation'},
            'scientific_context': {'openness_minimum': {}}}), 'exactory-openness-reading-v5')
        self.delivery = importlib.import_module('research_harness.review_search_delivery')

    def plan(self, searches):
        bindings = [{'formulation': row['question'], 'search_digest': digest(row), 'search': row}
                    for row in searches]
        return self.delivery.plan_search_delivery(bindings)

    def delivered(self, plan):
        return {sha256: {'content': content, 'deliveries': [{'assignment_id': 'turn-1',
                'attempt_id': 'attempt-1', 'part': 1, 'parts': 1, 'start': 0,
                'end': len(content.decode()), 'sha256': sha256}]} for sha256, content in plan['bodies'].items()}

    def test_fifteen_searches_deliver_two_disposition_lists_and_rebuild_every_byte(self):
        bindings = build_fifteen_searches()
        before = copy.deepcopy(bindings)
        plan = self.delivery.plan_search_delivery(bindings)
        self.assertEqual(bindings, before)
        self.assertEqual(plan, self.delivery.plan_search_delivery(bindings))
        manifest = plan['manifest']
        self.assertEqual(len(manifest['searches']), 15)
        lists = [row for row in manifest['shared_fields'] if row['field'] == 'dispositions']
        self.assertEqual([len(row['owners']) for row in lists], [10, 5])
        self.assertEqual([len(json.loads(plan['bodies'][row['artifact']['sha256']]))
                          for row in lists], [339, 271])
        self.assertEqual(len([row for row in manifest['shared_fields'] if row['field'] == 'scope']), 3)
        originals = {row['search_digest']: serialize_record(row['search']) for row in bindings}
        for entry in manifest['searches']:
            shared = {field: plan['bodies'][sha256] for field, sha256 in entry['shared_fields'].items()}
            rebuilt = self.delivery.rebuild_search_record(entry, plan['bodies'][entry['residual']['sha256']],
                                                         shared, originals[entry['search_digest']])
            self.assertEqual(rebuilt, originals[entry['search_digest']])
        receipts = self.delivery.verify_search_deliveries(manifest, self.delivered(plan), originals)
        self.assertEqual([row['search_digest'] for row in receipts], list(originals))
        saved_manifest = json.loads(json.dumps(manifest, sort_keys=True))
        self.assertEqual(self.delivery.verify_search_deliveries(saved_manifest, self.delivered(plan), originals), receipts)
        self.assertLess(sum(map(len, plan['bodies'].values())), sum(map(len, originals.values())) // 4)

    def test_only_equal_canonical_values_share_and_missing_is_distinct_from_null(self):
        base = {'id': 'one', 'question': 'Exact question.', 'dispositions': [
            {'reason': 'λ and escaped "quotation" \\ newline\n', 'disposition': 'relevant'},
            {'reason': 'A second judgment.', 'disposition': 'out_of_scope'}],
            'scope': {'z': None, 'a': ['é', 'e\u0301']}, 'queries': ['q1', 'q2'],
            'responses': [{'source_id': 'one', 'query': 'q1'}, {'source_id': 'two', 'query': 'q2'}]}
        for field in ('dispositions', 'scope', 'queries', 'responses'):
            with self.subTest(field=field):
                changed = copy.deepcopy(base)
                changed['id'] = 'two'
                if field == 'dispositions':
                    changed[field][0]['disposition'] = 'contradictory'
                elif field == 'scope':
                    changed[field]['a'][0] = 'e\u0301'
                elif field == 'queries':
                    changed[field].reverse()
                else:
                    changed[field][0]['source_id'] = 'other'
                plan = self.plan([base, changed])
                self.assertNotIn(field, plan['manifest']['searches'][0]['shared_fields'])
        for field in ('dispositions', 'queries', 'responses'):
            changed = copy.deepcopy(base)
            changed.update(id='two')
            changed[field] = list(reversed(changed[field]))
            self.assertNotIn(field, self.plan([base, changed])['manifest']['searches'][0]['shared_fields'])
        searches = [{'id': str(index), 'question': 'Exact question.', **fields}
                    for index, fields in enumerate(({}, {'scope': None}, {'scope': None}, {'scope': ''}))]
        plan = self.plan(searches)
        self.assertIsNone(plan['manifest']['searches'][0]['residual'])
        self.assertEqual([row['shared_fields'] for row in plan['manifest']['searches']],
                         [{}, {'scope': digest(None)}, {'scope': digest(None)}, {}])
        # Object key order is canonicalized; array order, Unicode and explicit nulls are preserved.
        changed = copy.deepcopy(base)
        changed.update(id='two', scope={'a': ['é', 'e\u0301'], 'z': None})
        plan = self.plan([base, changed])
        self.assertEqual(set(plan['manifest']['searches'][0]['shared_fields']),
                         {'dispositions', 'scope', 'queries', 'responses'})
        originals = {digest(row): serialize_record(row) for row in (base, changed)}
        receipts = self.delivery.verify_search_deliveries(plan['manifest'], self.delivered(plan), originals)
        self.assertEqual({row['search_digest'] for row in receipts}, set(originals))

    def test_repeating_a_binding_does_not_make_one_originals_fields_shared(self):
        search = {'id': 'one', 'question': 'Exact question.', 'scope': 'One original search.'}
        binding = {'formulation': search['question'], 'search_digest': digest(search), 'search': search}
        plan = self.delivery.plan_search_delivery([binding, binding])
        self.assertEqual(plan['manifest']['shared_fields'], [])
        self.assertEqual(len(plan['manifest']['searches']), 1)
        self.assertIsNone(plan['manifest']['searches'][0]['residual'])
        self.assertEqual(plan['bodies'], {})

    def test_missing_truncated_altered_or_unreceipted_components_supply_no_credit(self):
        plan = self.delivery.plan_search_delivery(build_fifteen_searches())
        originals = {row['search_digest']: serialize_record(row['search']) for row in build_fifteen_searches()}
        component = plan['manifest']['shared_fields'][0]['artifact']['sha256']
        for change in ('missing', 'truncated', 'altered', 'unreceipted'):
            with self.subTest(change=change):
                delivered = self.delivered(plan)
                if change == 'missing':
                    del delivered[component]
                elif change == 'unreceipted':
                    delivered[component]['deliveries'] = []
                else:
                    delivered[component]['content'] = delivered[component]['content'][:-1] + (
                        b'!' if change == 'altered' else b'')
                receipts = self.delivery.verify_search_deliveries(plan['manifest'], delivered, originals)
                self.assertFalse({row['search_digest'] for row in plan['manifest']['searches'][:10]}
                                 & {row['search_digest'] for row in receipts})

    def test_swapped_references_and_overlapping_fields_cannot_rebuild_an_original(self):
        plan = self.delivery.plan_search_delivery(build_fifteen_searches())
        entry = copy.deepcopy(plan['manifest']['searches'][0])
        original = serialize_record(build_fifteen_searches()[0]['search'])
        shared = {field: plan['bodies'][sha256] for field, sha256 in entry['shared_fields'].items()}
        shared['dispositions'] = plan['bodies'][plan['manifest']['searches'][-1]['shared_fields']['dispositions']]
        with self.assertRaises(ResearchError):
            self.delivery.rebuild_search_record(entry, plan['bodies'][entry['residual']['sha256']], shared, original)
        residual = json.loads(plan['bodies'][entry['residual']['sha256']])
        residual['fields']['scope'] = 'Forged overlapping inline scope.'
        with self.assertRaises(ResearchError):
            self.delivery.rebuild_search_record(entry, serialize_record(residual), shared, original)
