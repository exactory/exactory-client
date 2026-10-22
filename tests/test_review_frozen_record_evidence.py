"""Frozen optional record citations resolve only exact, unambiguous delivered versions."""

import copy
import unittest

from research_harness.evidence import digest
from research_harness.review_protocol import find_carried_record
from research_harness.review_supplied_evidence import undelivered_references


class FrozenRecordEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.record = {'id': 'value-1', 'motivation': ['An exact delivered claim.']}
        self.identity = digest(self.record)
        self.reference = {'kind': 'record', 'record_kind': 'problem_value', 'id': 'value-1'}
        self.projection = {'id': 'value-1', 'digest': self.identity, 'motivation': self.record['motivation']}
        self.row = ('problem_value', 'value-1', self.identity, self.projection, [])

    def find(self, rows, reference=None, records=None):
        return find_carried_record(records or {}, rows, reference or self.reference, frozen=True)

    def test_omitted_digest_resolves_exact_delivered_version_without_current_records(self):
        self.assertEqual(self.find([self.row]), (self.projection, []))

    def test_repeated_projections_of_one_version_are_not_ambiguous(self):
        another = ('problem_value', 'value-1', self.identity, {'id': 'value-1', 'record': self.record}, [])
        self.assertEqual(self.find([self.row, another, self.row]), (self.projection, []))

    def test_distinct_delivered_versions_are_ambiguous_without_an_explicit_digest(self):
        older = ('problem_value', 'value-1', '1' * 64, {'id': 'value-1', 'older': True}, [])
        for rows in ([self.row, older], [older, self.row]):
            self.assertIsNone(self.find(rows))
        reference = dict(self.reference, digest='1' * 64)
        self.assertEqual(self.find([self.row, older], reference), ({'id': 'value-1', 'older': True}, []))

    def test_wrong_explicit_digest_does_not_match_a_known_delivered_version(self):
        self.assertIsNone(self.find([self.row], dict(self.reference, digest='0' * 64)))

    def test_unknown_legacy_version_cannot_resolve_an_omitted_digest(self):
        legacy = ('problem_value', 'value-1', None, {'id': 'value-1'}, [])
        self.assertIsNone(self.find([legacy]))
        self.assertIsNone(self.find([self.row, legacy]))
        # Existing explicit legacy references retain separate record-time qualification.
        self.assertEqual(self.find([legacy], dict(self.reference, digest=self.identity)), ({'id': 'value-1'}, []))

    def test_full_search_and_source_rows_bind_their_own_id_instead_of_any_cited_id(self):
        for kind in ('literature_search', 'source'):
            record = {'id': kind + '-1', 'content': 'Complete immutable native row.'}
            row = (kind, None, digest(record), record, [])
            reference = {'kind': 'record', 'record_kind': kind, 'id': record['id']}
            with self.subTest(kind=kind):
                self.assertEqual(self.find([row], reference), (record, []))
                self.assertIsNone(self.find([row], dict(reference, id='not-delivered')))
                self.assertIsNone(self.find([row], dict(reference, id='not-delivered', digest=digest(record))))
                self.assertIsNone(self.find([row], dict(reference, digest='0' * 64)))

    def test_frozen_aliases_depend_on_delivered_kinds_not_future_current_namespaces(self):
        hyphen = dict(self.reference, record_kind='problem-value')
        states = [{}, {'problem_value': {}}, {'problem-value': {}},
                  {'problem-value': {'value-1': {'id': 'value-1', 'future': True}}, 'problem_value': {}},
                  {'problem_value': {'value-1': self.record}}]
        for records in states:
            with self.subTest(records=records):
                self.assertEqual(self.find([self.row], hyphen, records), (self.projection, []))
                self.assertIsNone(self.find([], hyphen, records))

    def test_two_delivered_kind_spellings_retain_distinct_exact_namespaces(self):
        hyphenated = ('problem-value', 'value-1', '2' * 64, {'id': 'value-1', 'different-kind': True}, [])
        rows = [self.row, hyphenated]
        self.assertEqual(self.find(rows), (self.projection, []))
        self.assertEqual(self.find(rows, dict(self.reference, record_kind='problem-value')),
                         ({'id': 'value-1', 'different-kind': True}, []))

    def test_missing_or_ambiguous_optional_reference_produces_details_without_fabricated_digest(self):
        other = ('problem_value', 'value-1', '3' * 64, {'id': 'value-1', 'other-version': True}, [])
        for rows in ([], [self.row, other]):
            reference = copy.deepcopy(self.reference)
            missing = undelivered_references({}, None, [reference], rows, {}, [], frozen=True)
            self.assertEqual(missing, [{'record_kind': 'problem_value', 'id': 'value-1'}])
            self.assertEqual(reference, self.reference)
        explicit = dict(self.reference, digest='0' * 64)
        self.assertEqual(undelivered_references({}, None, [explicit], [self.row], {}, [], frozen=True),
                         [{key: explicit[key] for key in ('record_kind', 'id', 'digest')}])

    def test_nonfrozen_current_record_qualification_is_unchanged(self):
        older = ('problem_value', 'value-1', '1' * 64, {'id': 'value-1', 'older': True}, [])
        records = {'problem_value': {'value-1': self.record}}
        self.assertEqual(find_carried_record(records, [older, self.row], self.reference), (self.projection, []))


if __name__ == '__main__':
    unittest.main()
